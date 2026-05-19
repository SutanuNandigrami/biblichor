"""Phase 6o.7 tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

# ============ R-M-1: bookorbit healthcheck uses wget --spider ============


def test_bookorbit_healthcheck_uses_wget_spider():
    import yaml

    compose_path = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"
    compose = yaml.safe_load(compose_path.read_text())
    hc = compose["services"]["bookorbit"]["healthcheck"]
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "wget" in test_cmd
    assert "--spider" in test_cmd, "healthcheck should use --spider (no body download)"


# ============ R-M-2: probe handles reverse-proxy path prefix ============


def test_probe_handles_reverse_proxy_prefix():
    """If BOOKORBIT_URL=http://proxy/books/, the probe should hit
    http://proxy/books/api/v1/health (not http://proxy/api/v1/health)."""
    from endless_library.app import _probe_bookorbit_health

    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    with patch("httpx.get", fake_get):
        _probe_bookorbit_health("http://reverse-proxy/books/")
    assert seen[0] == "http://reverse-proxy/books/api/v1/health"


def test_probe_normalizes_trailing_slash_with_path():
    from endless_library.app import _probe_bookorbit_health

    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))

    with patch("httpx.get", fake_get):
        _probe_bookorbit_health("http://x:3000/books")  # no trailing slash
        _probe_bookorbit_health("http://x:3000/books/")
    assert seen[0] == seen[1] == "http://x:3000/books/api/v1/health"


# ============ R-M-3: .gitignore has data/ ============


def test_gitignore_has_explicit_data_dir():
    gi = (Path(__file__).parent.parent.parent / ".gitignore").read_text()
    assert "\ndata/\n" in gi or gi.endswith("\ndata/\n"), (
        "data/ must be explicitly ignored in .gitignore"
    )


# ============ R-M-8: TLS CA bundle override ============


def test_client_accepts_verify_kwarg():
    """BookOrbitClient accepts verify= kwarg without crashing."""
    from endless_library.bookorbit.client import BookOrbitClient

    with BookOrbitClient("http://x:3000", verify=False):
        pass


def _spy_on_httpx_client(monkeypatch):
    """Patch httpx.Client.__init__ to capture the verify kwarg
    passed in. Returns the dict that will receive the value."""
    from unittest.mock import patch

    import endless_library.bookorbit.client as client_mod

    captured: dict = {}
    real_init = client_mod.httpx.Client.__init__

    def spy(self, *a, **kw):
        captured["verify"] = kw.get("verify", "<unset>")
        return real_init(self, *a, **kw)

    p_obj = patch.object(client_mod.httpx.Client, "__init__", spy)
    return captured, p_obj


def test_bookorbit_tls_ca_bundle_env_disable(monkeypatch):
    """BOOKORBIT_TLS_CA_BUNDLE=false routes through the verify=False path."""
    import endless_library.bookorbit.client as client_mod

    monkeypatch.setenv("BOOKORBIT_TLS_CA_BUNDLE", "false")
    captured, p_obj = _spy_on_httpx_client(monkeypatch)
    with p_obj, client_mod.BookOrbitClient("https://x"):
        pass
    assert captured["verify"] is False


def test_bookorbit_tls_ca_bundle_env_accepts_path(monkeypatch, tmp_path):
    """BOOKORBIT_TLS_CA_BUNDLE=/path/to/ca.pem routes through as a path string."""
    import endless_library.bookorbit.client as client_mod

    fake_ca = tmp_path / "ca.pem"
    fake_ca.write_text("x")
    monkeypatch.setenv("BOOKORBIT_TLS_CA_BUNDLE", str(fake_ca))
    captured, p_obj = _spy_on_httpx_client(monkeypatch)
    # httpx will try to load the (fake) CA bundle on construction.
    # The spy captures the verify value BEFORE that load, so we tolerate
    # the SSLError and still assert the captured value.
    import ssl

    try:
        with p_obj, client_mod.BookOrbitClient("https://x"):
            pass
    except ssl.SSLError:
        pass
    assert captured["verify"] == str(fake_ca)


# ============ R-V-3: restore-postgres command exists ============


def test_restore_postgres_subcommand_registered():
    """`biblichor restore-postgres --help` returns 0."""
    result = subprocess.run(
        [
            os.environ.get("PYTHON_EXE", "python3"),
            "-m",
            "endless_library",
            "restore-postgres",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # When invoked outside an interpreter that has biblichor installed,
    # this might fail with module-not-found. In the test env biblichor IS
    # installed, so this should succeed.
    if "No module named" in result.stderr:
        pytest.skip("biblichor not installed in test invocation env")
    assert result.returncode == 0, result.stderr
    assert "Postgres" in result.stdout or "postgres" in result.stdout
    assert "dump_path" in result.stdout


# ============ R-M-6: compose runner detection ============


def test_detect_compose_runner_prefers_docker_when_available(monkeypatch):
    """When docker is on PATH and `docker compose ls` succeeds, use it."""
    from types import SimpleNamespace

    from endless_library.__main__ import _detect_compose_runner

    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/docker" if x == "docker" else None)

    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "compose"] and cmd[2:] == ["ls"]:
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return real_run(cmd, **kw)

    monkeypatch.setattr("subprocess.run", fake_run)
    args = SimpleNamespace(compose_runner="")
    result = _detect_compose_runner(args)
    assert result == ["docker", "compose"]


def test_detect_compose_runner_falls_back_to_default_when_no_runner_works(monkeypatch):
    """When nothing's installed, fall back to docker compose so the call
    site gets a clear error rather than a NoneType crash."""
    from types import SimpleNamespace

    from endless_library.__main__ import _detect_compose_runner

    monkeypatch.setattr("shutil.which", lambda x: None)
    args = SimpleNamespace(compose_runner="")
    assert _detect_compose_runner(args) == ["docker", "compose"]


def test_detect_compose_runner_honors_explicit_override():
    from types import SimpleNamespace

    from endless_library.__main__ import _detect_compose_runner

    args = SimpleNamespace(compose_runner="podman compose")
    assert _detect_compose_runner(args) == ["podman", "compose"]
