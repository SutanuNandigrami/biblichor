"""Phase 6v.1: safe BookOrbit upgrade.

Three layers under test:

  - get_version_info: BookOrbit /app-info -> VersionInfo. Mocks the
    HTTPX client; covers happy path, no-auth fallback, and the case
    where docker isn't reachable from inside biblichor.
  - preflight: every check is exercised against a FakeDockerRunner.
    The DANGER_WORDS scan is the only check that can fail purely on
    inputs, so it gets its own test.
  - apply_upgrade: full sequence with FakeDockerRunner. Token gate,
    backup file write, compose mutation, rollback path on health
    failure.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from endless_library.bookorbit.upgrade import (
    DANGER_WORDS,
    SubprocessDockerRunner,
    _scan_release_notes_for_danger,
    _swap_compose_image,
    apply_upgrade,
    get_version_info,
    preflight,
)

# ============ FakeDockerRunner ============


class FakeDockerRunner:
    """Stand-in for SubprocessDockerRunner. Each call appends to
    `calls` so tests can assert on the sequence. Callers configure
    behaviour by registering return values per arg-prefix:

        runner.respond(['pull', ...], (0, 'ok', ''))
        runner.respond(['exec', 'biblichor-bookorbit-db', 'pg_isready'], (0, 'ok', ''))
    """

    def __init__(self, *, available: bool = True):
        self._available = available
        self._responses: list[tuple[list[str], tuple]] = []
        self._file_responses: list[tuple[list[str], tuple, bytes]] = []
        self.calls: list[list[str]] = []

    def available(self) -> bool:
        return self._available

    def respond(self, prefix: list[str], result: tuple[int, str, str]) -> None:
        self._responses.append((prefix, result))

    def respond_to_file(
        self, prefix: list[str], result: tuple[int, str], file_bytes: bytes
    ) -> None:
        self._file_responses.append((prefix, result, file_bytes))

    def _match(self, args: list[str]) -> tuple[int, str, str]:
        for prefix, result in self._responses:
            if args[: len(prefix)] == prefix:
                return result
        return 1, "", f"FakeDockerRunner: no response registered for {args[:3]}"

    def run(self, args: list[str], *, timeout: float = 60.0) -> tuple[int, str, str]:
        self.calls.append(args)
        return self._match(args)

    def run_to_file(
        self, args: list[str], dest: Path, *, timeout: float = 60.0
    ) -> tuple[int, str]:
        self.calls.append(args)
        for prefix, result, payload in self._file_responses:
            if args[: len(prefix)] == prefix:
                if result[0] == 0:
                    dest.write_bytes(payload)
                return result
        return 1, f"no run_to_file response registered for {args[:3]}"


# ============ get_version_info ============


def test_get_version_info_returns_current_and_latest_from_app_info():
    """Happy path: BookOrbit returns version + latestVersion."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={"version": "v1.2.0", "latestVersion": "v1.3.0", "updateAvailable": True},
        )
        if req.url.path == "/api/v1/app-info"
        else httpx.Response(200, json={"accessToken": "tok"})
    )
    client = httpx.Client(base_url="http://fake", transport=transport)
    info = get_version_info(
        "http://fake",
        admin_username="admin",
        admin_password="pw",
        runner=FakeDockerRunner(available=True),
        http_client=client,
        fetch_release_notes=False,
    )
    assert info.current == "v1.2.0"
    assert info.latest == "v1.3.0"
    assert info.update_available is True
    assert info.docker_socket_available is True


def test_get_version_info_handles_empty_url_gracefully():
    """If cfg.bookorbit.url isn't set, return a stub with all-None
    fields rather than raising."""
    info = get_version_info("", runner=FakeDockerRunner(available=False))
    assert info.current is None
    assert info.latest is None
    assert info.update_available is False
    assert info.docker_socket_available is False


def test_get_version_info_handles_app_info_unreachable():
    """BookOrbit down or app-info returns 5xx: return a stub
    VersionInfo (current=None) instead of bubbling the error."""
    transport = httpx.MockTransport(lambda req: httpx.Response(503))
    client = httpx.Client(base_url="http://fake", transport=transport)
    info = get_version_info("http://fake", runner=FakeDockerRunner(), http_client=client)
    assert info.current is None


# ============ release notes danger scan ============


def test_release_notes_scan_flags_breaking_change():
    safe, hits = _scan_release_notes_for_danger("This release contains a breaking change in /libraries")
    assert safe is False
    assert "breaking change" in hits


def test_release_notes_scan_flags_data_loss():
    safe, hits = _scan_release_notes_for_danger("Warning: data loss possible without backup")
    assert safe is False
    assert "data loss" in hits


def test_release_notes_scan_allows_clean_notes():
    notes = """### Features\n* hardcover sync\n### Bug Fixes\n* scanner inode clamp"""
    safe, hits = _scan_release_notes_for_danger(notes)
    assert safe is True
    assert hits == []


def test_release_notes_scan_case_insensitive():
    safe, _ = _scan_release_notes_for_danger("BREAKING CHANGE")
    assert safe is False


def test_danger_words_includes_essentials():
    """Guard against accidentally trimming the danger word list — the
    SPA's safety guarantee depends on these tripping."""
    for required in ("breaking change", "data loss", "manual migration"):
        assert required in DANGER_WORDS


# ============ preflight ============


def test_preflight_reports_all_checks_green_on_happy_path(tmp_path: Path):
    runner = FakeDockerRunner(available=True)
    runner.respond(["pull"], (0, "Status: Downloaded", ""))
    runner.respond(["image", "inspect"], (0, "amd64/180000000", ""))
    runner.respond(["exec"], (0, "accepting connections", ""))
    report = preflight(
        "1.3.0",
        runner=runner,
        release_notes="### Features\n* hardcover sync",
        backup_dir=tmp_path / "backups",
    )
    by_name = {c.name: c for c in report.checks}
    assert report.ok is True
    assert by_name["docker.socket"].ok is True
    assert by_name["image.pull"].ok is True
    assert by_name["image.inspect"].ok is True
    assert by_name["db.dumpable"].ok is True
    assert by_name["changelog.safe"].ok is True
    assert by_name["backup.dir_writable"].ok is True
    # Token is fresh and not expired.
    assert report.token
    assert report.expires_at > time.time()


def test_preflight_fails_when_docker_unavailable(tmp_path: Path):
    runner = FakeDockerRunner(available=False)
    report = preflight("1.3.0", runner=runner, release_notes="", backup_dir=tmp_path)
    assert report.ok is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["docker.socket"].ok is False
    # Downstream docker-dependent checks should also be marked skipped/false.
    assert by_name["image.pull"].ok is False


def test_preflight_fails_when_release_notes_have_breaking_change(tmp_path: Path):
    runner = FakeDockerRunner(available=True)
    runner.respond(["pull"], (0, "", ""))
    runner.respond(["image", "inspect"], (0, "amd64/1", ""))
    runner.respond(["exec"], (0, "ok", ""))
    report = preflight(
        "2.0.0",
        runner=runner,
        release_notes="This is a major release with breaking changes.",
        backup_dir=tmp_path,
    )
    assert report.ok is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["changelog.safe"].ok is False


def test_preflight_fails_when_image_pull_fails(tmp_path: Path):
    runner = FakeDockerRunner(available=True)
    runner.respond(["pull"], (1, "", "manifest unknown"))
    runner.respond(["exec"], (0, "ok", ""))
    report = preflight("99.99.99", runner=runner, backup_dir=tmp_path)
    assert report.ok is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["image.pull"].ok is False
    assert "manifest unknown" in by_name["image.pull"].detail


# ============ compose mutation ============


def test_swap_compose_image_replaces_bookorbit_image_only(tmp_path: Path):
    """Surgical: only the bookorbit service's image line is rewritten;
    biblichor + flaresolverr + bookorbit-db are untouched."""
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """name: biblichor
services:
  biblichor:
    image: ghcr.io/sutanunandigrami/biblichor:latest
    container_name: biblichor
  bookorbit-db:
    image: pgvector/pgvector:pg16
  bookorbit:
    image: ghcr.io/bookorbit/bookorbit@sha256:olddigest
    container_name: biblichor-bookorbit
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
""",
        encoding="utf-8",
    )
    old = _swap_compose_image(compose, "ghcr.io/bookorbit/bookorbit:1.3.0")
    assert old == "ghcr.io/bookorbit/bookorbit@sha256:olddigest"
    new = compose.read_text(encoding="utf-8")
    assert "ghcr.io/bookorbit/bookorbit:1.3.0" in new
    assert "ghcr.io/sutanunandigrami/biblichor:latest" in new  # untouched
    assert "pgvector/pgvector:pg16" in new  # untouched


def test_apply_upgrade_passes_project_directory_to_compose(tmp_path: Path, monkeypatch):
    """Regression: live apply on 2026-05-22 ran `docker compose -f
    /app/deploy/compose.yml` from inside biblichor without
    --project-directory, so the docker daemon resolved relative bind-
    mount paths (../data/bookorbit-db) against /app/deploy/ and ended
    up writing to a brand-new empty /app/data/bookorbit-db on the host
    instead of the real persistent data at /home/ubuntu/<repo>/data/
    bookorbit-db. Every `docker compose` invocation in apply_upgrade
    must now include --project-directory <host-repo-root>.
    """
    monkeypatch.setenv("HOST_REPO_PATH", "/home/me/repo")
    compose, env_file = _seed_compose(tmp_path)
    runner = FakeDockerRunner(available=True)
    import os as _os

    runner.respond_to_file(
        ["exec", "biblichor-bookorbit-db"], (0, ""), gzip.compress(_os.urandom(4096))
    )
    runner.respond(["compose"], (0, "Started", ""))

    with patch("endless_library.bookorbit.upgrade.httpx.get") as fake_get:
        fake_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"info": {"database": {"status": "up"}}, "version": "v1.3.0"},
        )
        apply_upgrade(
            "1.3.0",
            submitted_token="ok",
            expected_token="ok",
            preflight_expires_at=time.time() + 600,
            compose_path=compose,
            env_file=env_file,
            runner=runner,
            backup_dir=tmp_path / "backups",
            poll_timeout=5,
            poll_interval=1,
        )

    # At least one docker compose call must carry --project-directory
    # and the host-side deploy/ subdir (NOT compose.parent which would
    # be /app/deploy from inside the container, NOR HOST_REPO_PATH
    # itself which would let `..` climb above the repo). The compose
    # file's relative bind-mounts assume the file lives in <repo>/
    # deploy/ — that's the layout bootstrap.sh enforces and the only
    # one we ship.
    compose_calls = [args for args in runner.calls if args and args[0] == "compose"]
    assert compose_calls, "no docker compose calls were made"
    for args in compose_calls:
        assert "--project-directory" in args, f"compose call missing flag: {args}"
        idx = args.index("--project-directory")
        assert args[idx + 1] == "/home/me/repo/deploy", (
            f"--project-directory should be HOST_REPO_PATH/deploy, got: {args[idx + 1]}"
        )


def test_swap_compose_image_picks_bookorbit_not_tor_sidecar(tmp_path: Path):
    """Regression: live apply on 2026-05-22 reported
       'dperson/torproxy:latest -> ghcr.io/bookorbit/bookorbit:1.3.0'
    because the original (?ms) regex + lazy quantifier slipped past the
    bookorbit service and hit the tor sidecar's image line further
    down the file. Multiline-only (no dotall) plus [^\\n]* per
    iteration keeps the scope line-bounded — this test pins that.
    """
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """name: biblichor
services:
  bookorbit-db:
    image: pgvector/pgvector:pg16
  bookorbit:
    # Comments + blank lines between header and image: must not
    # confuse the matcher.
    image: ghcr.io/bookorbit/bookorbit@sha256:oldbookorbit
    container_name: biblichor-bookorbit
    restart: unless-stopped
  clamav:
    image: clamav/clamav:latest
    profiles:
      - av
  tor:
    image: dperson/torproxy:latest
    profiles:
      - tor
""",
        encoding="utf-8",
    )
    old = _swap_compose_image(compose, "ghcr.io/bookorbit/bookorbit:1.3.0")
    assert old == "ghcr.io/bookorbit/bookorbit@sha256:oldbookorbit"
    text = compose.read_text(encoding="utf-8")
    assert "ghcr.io/bookorbit/bookorbit:1.3.0" in text
    # Tor sidecar image must not have been touched.
    assert "dperson/torproxy:latest" in text


def test_swap_compose_image_returns_none_when_block_missing(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  biblichor:\n    image: foo:bar\n", encoding="utf-8")
    assert _swap_compose_image(compose, "anything") is None


# ============ apply_upgrade ============


def _seed_compose(tmp_path: Path) -> tuple[Path, Path]:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """services:
  bookorbit:
    image: ghcr.io/bookorbit/bookorbit@sha256:old
    container_name: biblichor-bookorbit
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("BOOKORBIT_DB_PASSWORD=secret\n", encoding="utf-8")
    return compose, env_file


def test_apply_upgrade_rejects_mismatched_token(tmp_path: Path):
    compose, env_file = _seed_compose(tmp_path)
    runner = FakeDockerRunner(available=True)
    result = apply_upgrade(
        "1.3.0",
        submitted_token="wrong",
        expected_token="correct",
        preflight_expires_at=time.time() + 600,
        compose_path=compose,
        env_file=env_file,
        runner=runner,
        backup_dir=tmp_path / "backups",
    )
    assert result.success is False
    assert result.rolled_back is False
    # Image line should not have been touched.
    assert "@sha256:old" in compose.read_text(encoding="utf-8")
    # Should fail at the token step.
    assert result.steps[0].name == "token.validate"
    assert result.steps[0].status == "failed"


def test_apply_upgrade_rejects_expired_token(tmp_path: Path):
    compose, env_file = _seed_compose(tmp_path)
    runner = FakeDockerRunner(available=True)
    result = apply_upgrade(
        "1.3.0",
        submitted_token="t",
        expected_token="t",
        preflight_expires_at=time.time() - 1,
        compose_path=compose,
        env_file=env_file,
        runner=runner,
        backup_dir=tmp_path / "backups",
    )
    assert result.success is False
    assert result.steps[0].name == "token.validate"
    assert "expired" in result.steps[0].detail


def test_apply_upgrade_happy_path_persists_backup_and_swaps_image(tmp_path: Path):
    compose, env_file = _seed_compose(tmp_path)
    runner = FakeDockerRunner(available=True)
    # Fake a valid gzipped dump that exceeds the 1024-byte sanity
    # threshold even after gzip compression. Random bytes compress
    # poorly, so a 4 KB random input survives gzip with room to spare.
    import os as _os

    fake_dump = gzip.compress(_os.urandom(4096))
    assert len(fake_dump) >= 1024  # sanity-check the test fixture itself
    runner.respond_to_file(["exec", "biblichor-bookorbit-db"], (0, ""), fake_dump)
    runner.respond(["compose"], (0, "Container biblichor-bookorbit Started", ""))
    runner.respond(["inspect"], (0, "ghcr.io/bookorbit/bookorbit:1.3.0", ""))

    with patch("endless_library.bookorbit.upgrade.httpx.get") as fake_get:
        fake_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "info": {"database": {"status": "up"}},
                "version": "v1.3.0",
            },
        )
        result = apply_upgrade(
            "1.3.0",
            submitted_token="ok",
            expected_token="ok",
            preflight_expires_at=time.time() + 600,
            compose_path=compose,
            env_file=env_file,
            runner=runner,
            backup_dir=tmp_path / "backups",
            poll_timeout=5,
            poll_interval=1,
        )

    assert result.success is True, result.steps
    assert result.rolled_back is False
    assert result.backup_path is not None
    backup = Path(result.backup_path)
    assert backup.exists()
    assert backup.stat().st_size >= 1024
    # Compose was swapped to the new tag and not reverted.
    assert "ghcr.io/bookorbit/bookorbit:1.3.0" in compose.read_text(encoding="utf-8")
    assert "@sha256:old" not in compose.read_text(encoding="utf-8")
    # Final version recorded.
    assert result.final_version == "1.3.0"


def test_apply_upgrade_rolls_back_when_health_never_goes_green(tmp_path: Path):
    """If the new container starts but /health stays unhealthy past
    the poll timeout, we revert compose + bring back the old image."""
    compose, env_file = _seed_compose(tmp_path)
    runner = FakeDockerRunner(available=True)
    import os as _os

    runner.respond_to_file(
        ["exec", "biblichor-bookorbit-db"], (0, ""), gzip.compress(_os.urandom(4096))
    )
    runner.respond(["compose"], (0, "Started", ""))

    with patch("endless_library.bookorbit.upgrade.httpx.get") as fake_get:
        # Always returns 503 -> health never goes green.
        fake_get.return_value = MagicMock(status_code=503)
        result = apply_upgrade(
            "1.3.0",
            submitted_token="ok",
            expected_token="ok",
            preflight_expires_at=time.time() + 600,
            compose_path=compose,
            env_file=env_file,
            runner=runner,
            backup_dir=tmp_path / "backups",
            poll_timeout=2,
            poll_interval=1,
        )

    assert result.success is False
    assert result.rolled_back is True
    # Compose must be restored to the original digest after rollback.
    final = compose.read_text(encoding="utf-8")
    assert "@sha256:old" in final
    assert "ghcr.io/bookorbit/bookorbit:1.3.0" not in final
    # Steps must include both the failed health poll AND the rollback.
    step_names = [s.name for s in result.steps]
    assert "health.poll" in step_names
    assert "rollback" in step_names


def test_apply_upgrade_aborts_when_compose_block_missing(tmp_path: Path):
    """A compose.yml that doesn't carry a bookorbit image line should
    fail at the swap step BEFORE we mess with anything. The backup
    should already have been taken, but the failure surface is
    intentional + clear."""
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  unrelated:\n    image: foo\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("X=y\n", encoding="utf-8")

    runner = FakeDockerRunner(available=True)
    import os as _os

    runner.respond_to_file(
        ["exec", "biblichor-bookorbit-db"], (0, ""), gzip.compress(_os.urandom(4096))
    )
    result = apply_upgrade(
        "1.3.0",
        submitted_token="ok",
        expected_token="ok",
        preflight_expires_at=time.time() + 600,
        compose_path=compose,
        env_file=env_file,
        runner=runner,
        backup_dir=tmp_path / "backups",
    )
    assert result.success is False
    step_names = [s.name for s in result.steps]
    assert "compose.swap_image" in step_names
    assert result.rolled_back is False  # nothing to revert


# ============ SubprocessDockerRunner ============


def test_subprocess_runner_available_false_when_socket_missing(monkeypatch, tmp_path: Path):
    """Without /var/run/docker.sock, available() must return False
    BEFORE attempting to invoke docker (which would error noisily)."""
    monkeypatch.setattr("endless_library.bookorbit.upgrade.DOCKER_SOCKET", tmp_path / "missing")
    runner = SubprocessDockerRunner()
    assert runner.available() is False


def test_subprocess_runner_handles_missing_docker_cli(monkeypatch, tmp_path: Path):
    """If the socket exists but the docker CLI isn't on PATH (the
    pre-Phase-6v.1 Dockerfile), don't crash — report unavailable."""
    sock = tmp_path / "docker.sock"
    sock.touch()
    monkeypatch.setattr("endless_library.bookorbit.upgrade.DOCKER_SOCKET", sock)
    monkeypatch.setattr("endless_library.bookorbit.upgrade.shutil.which", lambda _: None)
    runner = SubprocessDockerRunner()
    assert runner.available() is False

def test_validate_target_version_accepts_canonical():
    from endless_library.bookorbit.upgrade import _validate_target_version
    assert _validate_target_version("v1.3.0") == "v1.3.0"
    assert _validate_target_version("1.3.0") == "1.3.0"
    assert _validate_target_version("v2.0.0-beta.1") == "v2.0.0-beta.1"


def test_validate_target_version_rejects_injection():
    from endless_library.bookorbit.upgrade import _validate_target_version
    for bad in ("", "x", "v1.3", "1.3.0; rm -rf", "v1.3.0\nimage:evil", "v" * 70):
        with pytest.raises(ValueError):
            _validate_target_version(bad)


# ============ bookorbit_upgrade_apply lock (TOCTOU fix) ============


def test_bookorbit_upgrade_apply_409_when_lock_held(tmp_path: Path):
    """Phase 6w I12: a second concurrent POST to /bookorbit/upgrade/apply
    must receive 409 while the first is still running, preventing two
    simultaneous docker compose ops.
    """
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from endless_library.web.api import register

    app = FastAPI()
    # Attach the lock exactly as create_app does
    app.state.bookorbit_upgrade_lock = asyncio.Lock()
    register(app)

    # Pre-acquire the lock to simulate an in-progress upgrade
    loop = asyncio.new_event_loop()
    lock = app.state.bookorbit_upgrade_lock

    async def acquire_and_hold():
        await lock.acquire()

    loop.run_until_complete(acquire_and_hold())
    assert lock.locked()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/bookorbit/upgrade/apply",
        json={"target_version": "1.3.0", "token": "tok"},
    )
    assert resp.status_code == 409, f"expected 409, got {resp.status_code}: {resp.text}"
    assert "in progress" in resp.json().get("detail", "").lower()

    lock.release()
    loop.close()
