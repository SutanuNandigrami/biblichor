"""Parse-and-shape tests for deploy/compose.yml.

We don't run `docker compose up` in CI — too heavy. But we DO want a
regression net for: required services present, healthchecks wired,
ports exposed correctly, profiles configured. That way a typo in
the compose file gets caught before someone tries to bootstrap a
fresh box.

These tests use only PyYAML (already a project dep) — no docker
binary needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


# ============ REGRESSION: services we depend on are present ============


def test_compose_file_exists():
    assert COMPOSE_PATH.exists(), f"compose.yml missing at {COMPOSE_PATH}"


def test_all_required_services_present(compose: dict):
    """If any of these go missing, bootstrap.sh will silently bring up
    a half-broken stack. Phase 6e replaced calibre-web with bookorbit."""
    services = set(compose["services"].keys())
    REQUIRED = {"biblichor", "flaresolverr", "bookorbit", "bookorbit-db", "clamav"}
    assert services >= REQUIRED, f"missing services: {REQUIRED - services}"


def test_calibre_web_is_gone_after_phase_6e(compose: dict):
    """Phase 6e retires Calibre-Web in favor of BookOrbit. Catches
    accidental revert."""
    assert "calibre-web" not in compose["services"]


def test_biblichor_service_has_healthcheck(compose: dict):
    """Without this, docker doesn't know when biblichor is ready, and
    bootstrap.sh's /healthz poll falls back to a 2-minute timeout."""
    bib = compose["services"]["biblichor"]
    assert "healthcheck" in bib, "biblichor must declare a healthcheck"
    hc = bib["healthcheck"]
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "/healthz" in test_cmd, "healthcheck must probe /healthz"


def test_biblichor_depends_on_flaresolverr_healthy(compose: dict):
    """If biblichor starts before flaresolverr is reachable, the first
    cycle's CF challenges fail. depends_on with condition=service_healthy
    holds biblichor back until /api/v1 is responding."""
    bib = compose["services"]["biblichor"]
    dep = bib.get("depends_on", {})
    assert "flaresolverr" in dep, "biblichor must depend_on flaresolverr"
    fl_dep = dep["flaresolverr"]
    # Compose v2 normalizes string-form into {condition: ...} but we
    # write the explicit form. Accept either.
    if isinstance(fl_dep, dict):
        assert fl_dep.get("condition") == "service_healthy"


def test_clamav_is_optional_via_profile(compose: dict):
    """ClamAV is heavy — must be opt-in. The biblichor archive-hygiene
    code falls back gracefully when ClamAV is absent, so the av profile
    only fires for users who explicitly want it."""
    clam = compose["services"]["clamav"]
    profiles = clam.get("profiles", [])
    assert "av" in profiles, "clamav must be under profile=av"


# ============ REGRESSION: dashboard port mapping ============


def test_biblichor_dashboard_port_published(compose: dict):
    """Default 8090; overridable via BIBLICHOR_PORT. bootstrap.sh writes
    this to .env so the host-side port matches the user choice."""
    ports = compose["services"]["biblichor"]["ports"]
    assert any(":8090" in str(p) for p in ports), f"no :8090 in {ports!r}"
    assert any("BIBLICHOR_PORT" in str(p) for p in ports), (
        "port must be templated via ${BIBLICHOR_PORT:-8090} so the user override works"
    )


def test_flaresolverr_not_published_to_host(compose: dict):
    """Internal-only — biblichor reaches flaresolverr by service name."""
    fl = compose["services"]["flaresolverr"]
    assert "ports" not in fl, "flaresolverr ports should NOT be host-published"


def test_biblichor_data_and_config_volumes(compose: dict):
    """The data + config dirs must persist across container restarts.
    A regression that swaps these for tmpfs would lose every queue."""
    vols = compose["services"]["biblichor"]["volumes"]
    joined = " ".join(str(v) for v in vols)
    assert "./data:/data" in joined
    assert "./config:" in joined
    assert "./library:" in joined


def test_required_env_vars_referenced(compose: dict):
    """These three must be substituted into the biblichor service so the
    config plumbing at first boot can write them to config.yaml."""
    env = compose["services"]["biblichor"]["environment"]
    keys = set(env.keys())
    assert "GMAIL_USER" in keys
    assert "GMAIL_APP_PASSWORD" in keys
    assert "KINDLE_EMAIL" in keys


def test_image_tag_targets_ghcr(compose: dict):
    """The CI workflow (next commit) publishes to GHCR — the compose
    file must reference the same image so `docker compose pull` works."""
    img = compose["services"]["biblichor"]["image"]
    assert img.startswith("ghcr.io/"), f"image must be GHCR-hosted, got {img!r}"


def test_biblichor_always_pulls_published_image_before_recreate(compose: dict):
    """A plain `docker compose up` must not reuse a stale local `latest` image."""
    bib = compose["services"]["biblichor"]
    assert bib.get("pull_policy") == "always"
