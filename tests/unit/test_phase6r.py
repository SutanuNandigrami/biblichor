"""Phase 6r: regression coverage for container-vs-native bugs.

The pattern: a config value (URL, path) that works in a native
install but breaks inside a docker container because the same
string means something different there.

Already-fixed examples this suite codifies:
  - cfg.bookorbit.url  / BOOKORBIT_URL (Phase 6o.10)
  - cfg.scrapers.flaresolverr_url / FLARESOLVERR_URL (Phase 6r)
  - cfg.bookorbit.library_root (Phase 6o.2)

Plus a pipeline bug that surfaced in the cutover error log:
ConvertResult was being treated as a Path.
"""

from __future__ import annotations

from pathlib import Path

# ============ FLARESOLVERR_URL env override ============


def test_flaresolverr_url_env_override(monkeypatch, tmp_path):
    """FLARESOLVERR_URL in env should override config.yaml — same
    pattern as BOOKORBIT_URL. Compose deployments set this so the
    yaml can never carry a stale localhost value."""
    from endless_library.config import load_config

    yaml = tmp_path / "config.yaml"
    yaml.write_text(
        "general:\n  books_dir: /tmp\nscrapers:\n  flaresolverr_url: http://127.0.0.1:8191/v1\n"
    )
    monkeypatch.setenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
    cfg = load_config(yaml)
    assert cfg.scrapers.flaresolverr_url == "http://flaresolverr:8191/v1"


def test_flaresolverr_url_default_is_compose_service_name():
    """The default in code MUST be the compose service hostname, not
    127.0.0.1 — fresh installs that bypass the bootstrap rewrite still
    need to work inside the container."""
    from endless_library.config import Config

    cfg = Config()
    assert cfg.scrapers.flaresolverr_url == "http://flaresolverr:8191/v1"


def test_probes_flaresolverr_fallback_uses_service_hostname():
    """probes.py has a hardcoded fallback for when FLARESOLVERR_URL
    env isn't set. That fallback must also be the compose service
    hostname, not 127.0.0.1 (which inside a container points at the
    container itself)."""
    import inspect

    from endless_library import probes

    src = inspect.getsource(probes)
    # Should NOT have 127.0.0.1:8191 anywhere
    assert "127.0.0.1:8191" not in src
    # Should have the compose service hostname
    assert "flaresolverr:8191" in src


# ============ Compose passes env-overrides for biblichor's URLs ============


def test_compose_passes_flaresolverr_url_to_biblichor():
    """deploy/compose.yml must pass FLARESOLVERR_URL to the biblichor
    container so the env-override path is self-healing."""
    import yaml as pyyaml

    compose_path = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"
    compose = pyyaml.safe_load(compose_path.read_text())
    env = compose["services"]["biblichor"]["environment"]
    assert "FLARESOLVERR_URL" in env
    # And it should resolve to the compose service hostname
    assert "flaresolverr" in env["FLARESOLVERR_URL"]


def test_compose_passes_bookorbit_url_to_biblichor():
    """Same self-healing rule for BOOKORBIT_URL — passing it from
    compose env makes the value correct regardless of what's in
    config/.env."""
    import yaml as pyyaml

    compose_path = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"
    compose = pyyaml.safe_load(compose_path.read_text())
    env = compose["services"]["biblichor"]["environment"]
    assert "BOOKORBIT_URL" in env


# ============ Doctor surfaces flaresolverr connectivity ============


def test_doctor_includes_flaresolverr_check_when_url_supplied():
    """A new check name `flaresolverr.reachable` is emitted whenever
    flaresolverr_url is passed to run_doctor."""
    import httpx
    import respx

    from endless_library.bookorbit.doctor import run_doctor

    with respx.mock(base_url="http://bookorbit.test", assert_all_called=False) as r:
        r.get("/api/v1/health").mock(
            return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
        )
        r.get("/api/v1/auth/setup-status").mock(
            return_value=httpx.Response(200, json={"needsSetup": False})
        )
        r.get("/api/v1/opds").mock(return_value=httpx.Response(401))
        # The flaresolverr URL won't actually resolve in the test, but
        # the doctor must include a check entry either way.
        with respx.mock(base_url="http://fake-flaresolverr:8191", assert_all_called=False) as r2:
            r2.get("/").mock(return_value=httpx.Response(200))
            report = run_doctor(
                bookorbit_url="http://bookorbit.test",
                library_root=Path("/tmp"),
                library_id=None,
                flaresolverr_url="http://fake-flaresolverr:8191/v1",
            )
    names = [c.name for c in report.checks]
    assert "flaresolverr.reachable" in names


def test_doctor_skips_flaresolverr_when_url_none():
    """Backward compat: if flaresolverr_url isn't supplied, the check
    is silently skipped (so existing CLI invocations don't blow up)."""
    import httpx
    import respx

    from endless_library.bookorbit.doctor import run_doctor

    with respx.mock(base_url="http://bookorbit.test", assert_all_called=False) as r:
        r.get("/api/v1/health").mock(side_effect=httpx.ConnectError("x"))
        r.get("/api/v1/auth/setup-status").mock(side_effect=httpx.ConnectError("x"))
        r.get("/api/v1/opds").mock(side_effect=httpx.ConnectError("x"))
        report = run_doctor(
            bookorbit_url="http://bookorbit.test",
            library_root=None,
            library_id=None,
            # flaresolverr_url omitted
        )
    names = [c.name for c in report.checks]
    assert "flaresolverr.reachable" not in names


# ============ ConvertResult bug (PDF->EPUB rescue) ============


def test_convert_to_epub_returns_convertresult_not_path():
    """Phase 6r regression. The PDF->EPUB rescue path assumed
    convert_to_epub returns a Path, but it returns a ConvertResult.
    Touching .stat() on the result raised AttributeError on every
    oversize PDF. This pins the actual API shape."""
    import inspect

    from endless_library.convert import ConvertResult, convert_to_epub

    sig = inspect.signature(convert_to_epub)
    assert sig.return_annotation is ConvertResult or sig.return_annotation == "ConvertResult"


def test_pipeline_unwraps_convertresult_before_stat():
    """The exact bug: pipeline.py used to do `convert_to_epub(...).stat()`.
    Pin the source so it now goes through `.path` first."""
    import inspect

    from endless_library import pipeline

    src = inspect.getsource(pipeline)
    # The rescue block must access .path on the result before calling .stat()
    assert "convert_result.path" in src or "result.path" in src
    # And the BUG signature must be gone — no direct .stat() call on
    # the convert_to_epub return value.
    assert "convert_to_epub(file_path).stat()" not in src
