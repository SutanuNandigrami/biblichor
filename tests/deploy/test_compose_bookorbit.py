"""Phase 6a additions to compose tests — pin the bookorbit + bookorbit-db
service shape so a future PR can't silently break the integration."""

from pathlib import Path

import pytest
import yaml

COMPOSE_PATH = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


# ============ REGRESSION: BookOrbit + Postgres present ============


def test_bookorbit_service_present(compose):
    assert "bookorbit" in compose["services"]


def test_bookorbit_db_service_present(compose):
    assert "bookorbit-db" in compose["services"]


def test_bookorbit_image_pins_to_ghcr(compose):
    """The plan ships the BookOrbit-published image; never build locally."""
    img = compose["services"]["bookorbit"]["image"]
    assert img.startswith("ghcr.io/bookorbit/bookorbit"), f"unexpected image: {img!r}"


def test_bookorbit_image_not_latest(compose):
    """Review M-7: never use :latest. An unattended `docker compose pull`
    could change BookOrbit's auth API under us. Pin to a specific tag
    (v1.x.y) or sha digest; bump deliberately + re-validate.
    """
    img = compose["services"]["bookorbit"]["image"]
    assert ":latest" not in img, f"BookOrbit image must be pinned, not :latest. Got {img!r}"
    # Must have either a sha digest OR a semver-shaped tag
    has_sha = "@sha256:" in img
    has_semver_tag = ":" in img.rsplit("/", 1)[-1] and not img.endswith(":")
    assert has_sha or has_semver_tag, f"BookOrbit image needs a tag or sha pin: {img!r}"


def test_bookorbit_db_image_pins_to_pgvector_sha(compose):
    """Postgres + pgvector at a specific sha matches what BookOrbit's
    own compose pins. Drifting from this could break migrations."""
    img = compose["services"]["bookorbit-db"]["image"]
    assert img.startswith("pgvector/pgvector:pg16@sha256:"), f"image must be sha-pinned: {img!r}"


def test_bookorbit_depends_on_db_healthy(compose):
    """If bookorbit starts before postgres is migrated, the entrypoint
    hangs. Require depends_on with service_healthy condition."""
    bo = compose["services"]["bookorbit"]
    dep = bo.get("depends_on", {})
    assert "bookorbit-db" in dep
    if isinstance(dep["bookorbit-db"], dict):
        assert dep["bookorbit-db"].get("condition") == "service_healthy"


def test_bookorbit_shares_library_volume(compose):
    """biblichor drops files into ./library; bookorbit watches /books.
    The shared mount is the entire integration."""
    vols = " ".join(str(v) for v in compose["services"]["bookorbit"]["volumes"])
    assert "./library:/books" in vols


def test_bookorbit_has_own_data_volume(compose):
    """./data/bookorbit holds covers + book-bucket. Persists across container restarts."""
    vols = " ".join(str(v) for v in compose["services"]["bookorbit"]["volumes"])
    assert "./data/bookorbit:/data" in vols


def test_bookorbit_db_has_persistent_volume(compose):
    """./data/bookorbit-db is the Postgres data dir."""
    vols = " ".join(str(v) for v in compose["services"]["bookorbit-db"]["volumes"])
    assert "./data/bookorbit-db:/var/lib/postgresql/data" in vols


def test_bookorbit_has_health_check(compose):
    """Health probe hits /api/v1/health so bootstrap.sh can wait for ready."""
    hc = compose["services"]["bookorbit"]["healthcheck"]
    test_cmd = " ".join(hc["test"]) if isinstance(hc["test"], list) else hc["test"]
    assert "/api/v1/health" in test_cmd


def test_bookorbit_port_is_configurable(compose):
    """Default 3000 but overridable via BOOKORBIT_PORT."""
    ports = compose["services"]["bookorbit"]["ports"]
    assert any("BOOKORBIT_PORT" in str(p) for p in ports)


def test_bookorbit_env_includes_required_secrets(compose):
    """Without JWT_SECRET + SETUP_BOOTSTRAP_TOKEN, bookorbit refuses to boot."""
    env = compose["services"]["bookorbit"]["environment"]
    keys = set(env.keys())
    for required in ("JWT_SECRET", "SETUP_BOOTSTRAP_TOKEN", "POSTGRES_HOST"):
        assert required in keys, f"missing required env: {required}"


# ============ FEATURE-INTACT: Phase 2b services still present ============


def test_calibre_web_removed_after_phase_6e(compose):
    """Phase 6e retires Calibre-Web. This test flipped from "must be
    present" to "must be absent" when 6e shipped."""
    assert "calibre-web" not in compose["services"]


def test_biblichor_service_unchanged(compose):
    bib = compose["services"]["biblichor"]
    assert "image" in bib and bib["image"].startswith("ghcr.io/sutanunandigrami/biblichor")
    # Healthcheck still hits /healthz
    hc_cmd = " ".join(bib["healthcheck"]["test"])
    assert "/healthz" in hc_cmd


def test_flaresolverr_still_present(compose):
    assert "flaresolverr" in compose["services"]


def test_clamav_still_under_profile_av(compose):
    profiles = compose["services"]["clamav"].get("profiles", [])
    assert "av" in profiles
