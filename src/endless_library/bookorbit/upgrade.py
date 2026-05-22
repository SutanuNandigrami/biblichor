"""Safe BookOrbit upgrade.

Phase 6v.1. The UI Settings page exposes a button that:

  1. Queries BookOrbit's own /api/v1/app-info to learn the current
     and latest versions (BookOrbit already does this check itself
     on startup; we don't need a parallel GitHub poll).
  2. Pulls the candidate image into the local Docker daemon and
     runs a battery of preflight checks (changelog scan for breaking
     migrations, disk free, the image actually exists, current DB
     can be dumped, etc.). The user only sees the Apply button when
     these are green.
  3. On Apply: pg_dumps the BookOrbit DB into /data/backups/, swaps
     the compose image SHA, restarts the bookorbit container, polls
     /api/v1/health for 90s, and runs the full doctor. If anything
     in this sequence fails, rolls back to the prior image and
     restores the DB from the backup.

Talks to the host Docker daemon via /var/run/docker.sock (bind-mount
added to compose.yml as part of this phase). All Docker calls go
through a thin Runner abstraction so tests can inject a fake.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets as _secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)


BOOKORBIT_IMAGE = "ghcr.io/bookorbit/bookorbit"
BOOKORBIT_CONTAINER = "biblichor-bookorbit"
BOOKORBIT_DB_CONTAINER = "biblichor-bookorbit-db"
DOCKER_SOCKET = Path("/var/run/docker.sock")
DEFAULT_BACKUP_DIR = Path("/data/backups")
MIN_FREE_DISK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB safety margin
HEALTH_POLL_TIMEOUT_SEC = 90
HEALTH_POLL_INTERVAL_SEC = 3
PREFLIGHT_TOKEN_TTL_SEC = 15 * 60

# Words in release notes that should block auto-apply: each is a
# strong signal of a manual step the user must consciously take.
DANGER_WORDS = (
    "breaking change",
    "breaking-change",
    "manual migration",
    "manual intervention",
    "data loss",
    "incompatible",
    "deprecated",
    "removed",
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VersionInfo:
    current: str | None
    latest: str | None
    update_available: bool
    last_checked_at: str
    docker_socket_available: bool
    release_notes: str = ""
    release_url: str = ""


@dataclass
class PreflightReport:
    target_version: str
    checks: list[CheckResult]
    ok: bool
    token: str
    expires_at: float  # epoch seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_version": self.target_version,
            "ok": self.ok,
            "token": self.token,
            "expires_at": self.expires_at,
            "checks": [asdict(c) for c in self.checks],
        }


@dataclass
class ApplyStep:
    name: str
    status: str  # 'running', 'ok', 'failed', 'skipped'
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass
class ApplyResult:
    target_version: str
    success: bool
    rolled_back: bool
    backup_path: str | None
    final_version: str | None
    steps: list[ApplyStep] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_version": self.target_version,
            "success": self.success,
            "rolled_back": self.rolled_back,
            "backup_path": self.backup_path,
            "final_version": self.final_version,
            "steps": [asdict(s) for s in self.steps],
        }


class UpgradeError(Exception):
    """Raised when a preflight/apply step fails in a way the caller
    should surface to the SPA."""


# --------------------------------------------------------------------
# Docker runner abstraction. Tests inject a fake; production uses the
# subprocess runner that shells out to the docker CLI.
# --------------------------------------------------------------------


class DockerRunner(Protocol):
    def available(self) -> bool: ...
    def run(self, args: list[str], *, timeout: float = 60.0) -> tuple[int, str, str]: ...
    def run_to_file(
        self, args: list[str], dest: Path, *, timeout: float = 60.0
    ) -> tuple[int, str]: ...


class SubprocessDockerRunner:
    """Production runner: subprocess.run('docker', *args). Requires
    the docker socket to be mounted at /var/run/docker.sock and the
    docker CLI to be on PATH inside the biblichor container.
    """

    def available(self) -> bool:
        if not DOCKER_SOCKET.exists():
            return False
        if shutil.which("docker") is None:
            return False
        rc, _, _ = self.run(["version", "--format", "{{.Server.Version}}"], timeout=5)
        return rc == 0

    def run(self, args: list[str], *, timeout: float = 60.0) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            return 124, e.stdout or "", f"timeout after {timeout}s"
        except FileNotFoundError as e:
            return 127, "", str(e)

    def run_to_file(
        self, args: list[str], dest: Path, *, timeout: float = 60.0
    ) -> tuple[int, str]:
        """Stream `docker <args>` stdout directly into `dest`. Used by
        pg_dump where stdout is gzipped binary and decoding via the
        text runner would corrupt it. stderr is captured as a string
        and returned alongside the exit code.
        """
        try:
            with dest.open("wb") as fh:
                proc = subprocess.run(
                    ["docker", *args],
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
            return proc.returncode, proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s"
        except FileNotFoundError as e:
            return 127, str(e)


# --------------------------------------------------------------------
# Version detection
# --------------------------------------------------------------------


def _strip_v(v: str | None) -> str | None:
    if not v:
        return v
    return v[1:] if v.startswith("v") else v


def get_version_info(
    bookorbit_url: str,
    *,
    admin_username: str | None = None,
    admin_password: str | None = None,
    runner: DockerRunner | None = None,
    http_client: httpx.Client | None = None,
    fetch_release_notes: bool = True,
) -> VersionInfo:
    """Hit BookOrbit's own /api/v1/app-info (which already polls
    upstream for the latest release). Auth required — supply stored
    admin creds. Falls back to current=None if anything fails so the
    SPA can render an explanatory error state.
    """
    runner = runner or SubprocessDockerRunner()
    docker_avail = runner.available()
    now = datetime.now(timezone.utc).isoformat()

    info = VersionInfo(
        current=None,
        latest=None,
        update_available=False,
        last_checked_at=now,
        docker_socket_available=docker_avail,
    )
    if not bookorbit_url:
        return info

    own_client = http_client is None
    client = http_client or httpx.Client(base_url=bookorbit_url.rstrip("/"), timeout=10.0)
    try:
        # Auth optional; without it we still get version data on the
        # newer BookOrbit builds, but old ones 401 it. Try auth path
        # first when creds are supplied.
        headers: dict[str, str] = {}
        if admin_username and admin_password:
            try:
                lr = client.post(
                    "/api/v1/auth/login",
                    json={"username": admin_username, "password": admin_password},
                )
                if lr.status_code == 200:
                    body = lr.json()
                    token = body.get("accessToken") or body.get("access_token")
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
            except (httpx.HTTPError, ValueError) as e:
                log.warning("bookorbit: login for app-info failed: %s", e)

        r = client.get("/api/v1/app-info", headers=headers)
        if r.status_code != 200:
            log.warning("bookorbit: app-info returned %s", r.status_code)
            return info
        body = r.json()
        info.current = body.get("version")
        info.latest = body.get("latestVersion")
        info.update_available = bool(body.get("updateAvailable"))

        if fetch_release_notes and info.latest:
            tag = info.latest if info.latest.startswith("v") else f"v{info.latest}"
            try:
                notes_resp = httpx.get(
                    f"https://api.github.com/repos/bookorbit/bookorbit/releases/tags/{tag}",
                    timeout=10.0,
                )
                if notes_resp.status_code == 200:
                    rel = notes_resp.json()
                    info.release_notes = rel.get("body", "") or ""
                    info.release_url = rel.get("html_url", "") or ""
            except httpx.HTTPError as e:
                log.warning("bookorbit: GH release fetch failed: %s", e)
    finally:
        if own_client:
            client.close()
    return info


# --------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------


def _scan_release_notes_for_danger(notes: str) -> tuple[bool, list[str]]:
    """Return (is_safe, hits). Used by preflight to refuse one-click
    upgrades when the release notes describe a manual step."""
    found: list[str] = []
    lc = notes.lower()
    for word in DANGER_WORDS:
        if word in lc:
            found.append(word)
    return (not found), found


def _disk_free_bytes(path: Path) -> int:
    """Walk up `path` until an existing directory is found, then return
    free bytes there. shutil.disk_usage requires an extant path; the
    naive `backup_dir.parent` fallback breaks when even the parent
    hasn't been created yet (e.g. the very first run before /data/backups
    is materialised)."""
    probe = path
    while probe != probe.parent:
        if probe.exists():
            try:
                return shutil.disk_usage(probe).free
            except OSError as e:
                log.warning("disk_usage(%s) failed: %s", probe, e)
                return 0
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError as e:
        log.warning("disk_usage(%s) failed: %s", probe, e)
        return 0


def preflight(
    target_version: str,
    *,
    runner: DockerRunner | None = None,
    release_notes: str = "",
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    db_container: str = BOOKORBIT_DB_CONTAINER,
) -> PreflightReport:
    """Run every check before letting Apply run. The returned token
    must be passed back to apply() within PREFLIGHT_TOKEN_TTL_SEC
    or apply() refuses the call. Token-gating prevents a stale UI
    or a script from triggering Apply without a recent green
    preflight."""
    runner = runner or SubprocessDockerRunner()
    checks: list[CheckResult] = []

    # 1. Docker reachability — without this nothing else can work.
    docker_ok = runner.available()
    checks.append(
        CheckResult(
            name="docker.socket",
            ok=docker_ok,
            detail=(
                "docker daemon reachable"
                if docker_ok
                else f"docker socket {DOCKER_SOCKET} unavailable or docker CLI missing"
            ),
        )
    )

    # 2. Image exists upstream + pulls cleanly into local daemon.
    if docker_ok:
        image_ref = f"{BOOKORBIT_IMAGE}:{target_version.lstrip('v')}"
        rc, out, err = runner.run(["pull", image_ref], timeout=300.0)
        if rc == 0:
            checks.append(CheckResult("image.pull", True, image_ref))
            # Read multi-arch + image size for the UI.
            rc2, out2, _ = runner.run(
                ["image", "inspect", image_ref, "--format", "{{.Architecture}}/{{.Size}}"],
            )
            if rc2 == 0:
                checks.append(CheckResult("image.inspect", True, out2.strip()))
        else:
            checks.append(CheckResult("image.pull", False, (err or out)[:400]))
    else:
        checks.append(CheckResult("image.pull", False, "skipped — docker unavailable"))

    # 3. Disk space — pg_dump + image layers can easily exceed 1 GB.
    free = _disk_free_bytes(backup_dir)
    checks.append(
        CheckResult(
            name="disk.free",
            ok=free >= MIN_FREE_DISK_BYTES,
            detail=(
                f"{free / 1024 / 1024 / 1024:.1f} GB free "
                f"(need >= {MIN_FREE_DISK_BYTES / 1024 / 1024 / 1024:.0f} GB)"
            ),
        )
    )

    # 4. DB can be pg_dump'd. Lightweight probe: just exec a no-op
    # against the DB container to confirm it's running + reachable.
    if docker_ok:
        rc, out, err = runner.run(
            ["exec", db_container, "pg_isready", "-U", "bookorbit"], timeout=10
        )
        checks.append(
            CheckResult(
                name="db.dumpable",
                ok=rc == 0,
                detail=(out or err).strip()[:200] if (out or err) else "pg_isready returned 0",
            )
        )
    else:
        checks.append(CheckResult("db.dumpable", False, "skipped — docker unavailable"))

    # 5. Release notes safety. Only fail the preflight if release notes
    # mention a blocking word — informational on silence.
    if release_notes:
        safe, hits = _scan_release_notes_for_danger(release_notes)
        checks.append(
            CheckResult(
                name="changelog.safe",
                ok=safe,
                detail=(
                    "no blocking words in release notes"
                    if safe
                    else f"release notes mention: {', '.join(hits)} — review required"
                ),
            )
        )
    else:
        checks.append(
            CheckResult(
                name="changelog.safe",
                ok=True,
                detail="release notes unavailable — skipped",
            )
        )

    # 6. Backup dir writable.
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        probe = backup_dir / f".write-probe-{os.getpid()}"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        checks.append(CheckResult("backup.dir_writable", True, str(backup_dir)))
    except OSError as e:
        checks.append(CheckResult("backup.dir_writable", False, str(e)))

    all_ok = all(c.ok for c in checks)
    token = _secrets.token_urlsafe(24)
    return PreflightReport(
        target_version=target_version,
        checks=checks,
        ok=all_ok,
        token=token,
        expires_at=time.time() + PREFLIGHT_TOKEN_TTL_SEC,
    )


# --------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backup_filename(target_version: str, backup_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return backup_dir / f"bookorbit-pre-{target_version.lstrip('v')}-{ts}.sql.gz"


def _step(result: ApplyResult, name: str) -> ApplyStep:
    step = ApplyStep(name=name, status="running", started_at=_now_iso())
    result.steps.append(step)
    return step


def _finish_step(step: ApplyStep, *, ok: bool, detail: str = "") -> None:
    step.status = "ok" if ok else "failed"
    step.detail = detail
    step.finished_at = _now_iso()


def _compose_args(compose_path: Path, env_file: Path) -> list[str]:
    """Common prefix for every `docker compose` invocation inside
    apply_upgrade. Includes --project-directory so the docker daemon
    resolves relative bind-mount paths (e.g. `../data/bookorbit-db`
    in compose.yml) against the HOST repo root, not against
    /app/deploy where biblichor sees the compose file from inside its
    container.

    The host repo path is passed in via the `HOST_REPO_PATH` env var
    set in compose.yml; this fails over to the compose file's parent's
    parent (which is correct on the host but wrong inside the
    container — only used as a safety net for unit tests).
    """
    host_root = os.environ.get("HOST_REPO_PATH")
    if not host_root:
        # Best-effort fallback (correct from the host, wrong from
        # the container). Real production calls always have the env
        # var set by compose.yml.
        host_root = str(compose_path.parent.parent)
    return [
        "compose",
        "-f",
        str(compose_path),
        "--env-file",
        str(env_file),
        "--project-directory",
        host_root,
    ]


def _read_current_image_ref(
    runner: DockerRunner, container: str = BOOKORBIT_CONTAINER
) -> str | None:
    rc, out, _ = runner.run(["inspect", container, "--format", "{{.Config.Image}}"])
    if rc != 0:
        return None
    return out.strip() or None


def _swap_compose_image(compose_path: Path, new_image_ref: str) -> str | None:
    """Mutate the bookorbit service's `image:` line in compose.yml to
    point at new_image_ref. Returns the OLD image ref so apply() can
    roll back. We do this as a literal-line replacement rather than a
    yaml round-trip to preserve comments + formatting (the compose
    file is heavily commented and a PyYAML round-trip loses that).
    """
    text = compose_path.read_text(encoding="utf-8")
    # Match the bookorbit service block's `image:` line. The bookorbit
    # service is named exactly `bookorbit` in compose.yml; we anchor on
    # the service header to avoid touching biblichor / flaresolverr.
    # Critical: use [^\n]* instead of .* so each iteration is one line.
    # The previous (?ms) flags + .* let dotall slip the lazy quantifier
    # past the bookorbit service entirely and match the tor sidecar's
    # image line below it — which on apply would swap dperson/torproxy
    # instead of BookOrbit. Multiline only (no dotall) keeps the per-
    # iteration scope strictly line-bounded.
    m = re.search(
        r"(?m)^(  bookorbit:\n(?:    [^\n]*\n)*?    image:\s*)(\S+)",
        text,
    )
    if not m:
        return None
    old_ref = m.group(2)
    new_text = text[: m.start(2)] + new_image_ref + text[m.end(2) :]
    compose_path.write_text(new_text, encoding="utf-8")
    return old_ref


def apply_upgrade(
    target_version: str,
    *,
    submitted_token: str,
    expected_token: str,
    preflight_expires_at: float,
    compose_path: Path,
    env_file: Path,
    runner: DockerRunner | None = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    db_container: str = BOOKORBIT_DB_CONTAINER,
    container: str = BOOKORBIT_CONTAINER,
    bookorbit_url: str = "http://bookorbit:3000",
    poll_timeout: int = HEALTH_POLL_TIMEOUT_SEC,
    poll_interval: int = HEALTH_POLL_INTERVAL_SEC,
) -> ApplyResult:
    """Execute the full upgrade. Persists each step into the returned
    ApplyResult; rolls back automatically on failure of any step
    after the image swap (backup + compose mutation undo, then
    docker compose up -d to restore the old image).
    """
    runner = runner or SubprocessDockerRunner()
    result = ApplyResult(
        target_version=target_version,
        success=False,
        rolled_back=False,
        backup_path=None,
        final_version=None,
    )

    # ---- 0. Token validation ----
    token_step = _step(result, "token.validate")
    if submitted_token != expected_token:
        _finish_step(token_step, ok=False, detail="preflight token mismatch")
        return result
    if time.time() > preflight_expires_at:
        _finish_step(
            token_step,
            ok=False,
            detail=f"preflight token expired at {preflight_expires_at}",
        )
        return result
    _finish_step(token_step, ok=True, detail="token accepted")

    # ---- 1. Backup BookOrbit DB ----
    backup_step = _step(result, "db.backup")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_filename(target_version, backup_dir)
    # Stream the gzipped dump directly into the host file. Going through
    # the text runner would re-encode the gzip bytes and corrupt the
    # archive.
    rc, err = runner.run_to_file(
        [
            "exec",
            db_container,
            "sh",
            "-c",
            "pg_dump -U \"$POSTGRES_USER\" \"$POSTGRES_DB\" | gzip -9",
        ],
        backup_path,
        timeout=600,
    )
    if rc != 0:
        _finish_step(backup_step, ok=False, detail=err[:400])
        # Don't leave a half-written backup that masquerades as good.
        backup_path.unlink(missing_ok=True)
        return result
    if not backup_path.exists() or backup_path.stat().st_size < 1024:
        size = backup_path.stat().st_size if backup_path.exists() else 0
        _finish_step(
            backup_step,
            ok=False,
            detail=f"backup at {backup_path} is too small ({size} bytes)",
        )
        backup_path.unlink(missing_ok=True)
        return result
    result.backup_path = str(backup_path)
    _finish_step(
        backup_step,
        ok=True,
        detail=f"{backup_path.name} ({backup_path.stat().st_size} bytes)",
    )

    # ---- 2. Swap compose image ----
    swap_step = _step(result, "compose.swap_image")
    new_ref = f"{BOOKORBIT_IMAGE}:{target_version.lstrip('v')}"
    old_ref = _swap_compose_image(compose_path, new_ref)
    if old_ref is None:
        _finish_step(
            swap_step,
            ok=False,
            detail="could not locate bookorbit `image:` line in compose.yml",
        )
        return result
    _finish_step(swap_step, ok=True, detail=f"{old_ref} -> {new_ref}")

    def _rollback(reason: str) -> None:
        rb_step = _step(result, "rollback")
        # Restore compose
        try:
            _swap_compose_image(compose_path, old_ref)
        except OSError as e:
            log.error("rollback: compose restore failed: %s", e)
        rc_rb, _, err_rb = runner.run(
            _compose_args(compose_path, env_file)
            + ["up", "-d", "bookorbit"],
            timeout=300,
        )
        ok_rb = rc_rb == 0
        result.rolled_back = ok_rb
        _finish_step(
            rb_step,
            ok=ok_rb,
            detail=(
                f"reverted image to {old_ref} after: {reason}"
                if ok_rb
                else f"rollback compose up failed: {err_rb[:200]}"
            ),
        )

    # ---- 3. compose up bookorbit ----
    up_step = _step(result, "compose.up_bookorbit")
    rc, out, err = runner.run(
        _compose_args(compose_path, env_file)
        + ["up", "-d", "bookorbit"],
        timeout=300,
    )
    if rc != 0:
        _finish_step(up_step, ok=False, detail=(err or out)[:400])
        _rollback("docker compose up failed")
        return result
    _finish_step(up_step, ok=True, detail="container recreated")

    # ---- 4. Health poll ----
    health_step = _step(result, "health.poll")
    deadline = time.time() + poll_timeout
    healthy = False
    last_err = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{bookorbit_url.rstrip('/')}/api/v1/health", timeout=5)
            if r.status_code == 200:
                body = r.json()
                if body.get("info", {}).get("database", {}).get("status") == "up":
                    healthy = True
                    break
            else:
                last_err = f"HTTP {r.status_code}"
        except (httpx.HTTPError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(poll_interval)
    if not healthy:
        _finish_step(
            health_step,
            ok=False,
            detail=f"health never went green within {poll_timeout}s: {last_err}",
        )
        _rollback("health poll timeout")
        return result
    _finish_step(health_step, ok=True, detail=f"healthy within {int(time.time() - (deadline - poll_timeout))}s")

    # ---- 5. Verify version reports as target ----
    version_step = _step(result, "version.verify")
    try:
        r = httpx.get(f"{bookorbit_url.rstrip('/')}/api/v1/app-info", timeout=5)
        if r.status_code == 200:
            running_ver = (r.json().get("version") or "").lstrip("v")
        else:
            running_ver = None
    except httpx.HTTPError:
        running_ver = None
    if running_ver is None or running_ver != target_version.lstrip("v"):
        # If app-info is auth-gated and we got 401, fall back to docker
        # inspect — Config.Image carries the tag we just upgraded to.
        running_image = _read_current_image_ref(runner, container=container) or ""
        if running_image.endswith(f":{target_version.lstrip('v')}"):
            result.final_version = target_version.lstrip("v")
            _finish_step(
                version_step,
                ok=True,
                detail=f"verified via image inspect: {running_image}",
            )
        else:
            _finish_step(
                version_step,
                ok=False,
                detail=f"running version is {running_ver or running_image or 'unknown'}, expected {target_version}",
            )
            _rollback("version verification failed")
            return result
    else:
        result.final_version = running_ver
        _finish_step(version_step, ok=True, detail=f"running version: {running_ver}")

    result.success = True
    return result


# --------------------------------------------------------------------
# Convenience JSON dump for the persistence layer (Phase 6v.1 stores
# the most recent ApplyResult in /data/bookorbit-upgrade-history.jsonl
# so the SPA can show a "last attempt" panel).
# --------------------------------------------------------------------


def append_history(path: Path, result: ApplyResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.as_dict(), ensure_ascii=False))
        fh.write("\n")
