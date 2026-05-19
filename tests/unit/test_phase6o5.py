"""Phase 6o.5 tests for container hardening + reviewer Importants."""

from __future__ import annotations

import inspect
from pathlib import Path

# ============ R-I-1: BookOrbitCfg docstring no longer references config/bookorbit.json ============


def test_bookorbit_cfg_docstring_no_longer_says_writes_bookorbit_json():
    from endless_library.config import BookOrbitCfg

    doc = BookOrbitCfg.__doc__ or ""
    # The stale text was: "writes config/bookorbit.json with the library_id"
    # That file was deleted in Phase 6m.ii.
    assert "writes config/bookorbit.json" not in doc, (
        "BookOrbitCfg docstring still tells users about a file that doesn't exist"
    )
    # But the historical context (where the field used to live) is fine
    # — checking that the doc IS accurate, not blanking it.
    assert "config.yaml" in doc, "docstring should point users at config.yaml as source of truth"


# ============ R-I-7: probe timeout bumped from 0.5s to 2.5s ============


def test_probe_bookorbit_health_default_timeout_handles_cold_start():
    from endless_library.app import _probe_bookorbit_health

    sig = inspect.signature(_probe_bookorbit_health)
    timeout_default = sig.parameters["timeout"].default
    assert timeout_default >= 2.0, (
        f"timeout default ({timeout_default}s) too low to handle BookOrbit cold start"
    )


# ============ R-I-6: Dockerfile USER directive present ============


def test_dockerfile_runs_as_non_root():
    dockerfile = (Path(__file__).parent.parent.parent / "deploy" / "Dockerfile").read_text()
    assert "USER 1000:1000" in dockerfile, "Dockerfile must run as UID 1000 (not root)"
    # And the user/group must be created
    assert "useradd" in dockerfile or "adduser" in dockerfile
    assert "chown" in dockerfile, "/app must be chowned to the runtime user"


# ============ R-I-5: bootstrap.sh has docker compose exec fallback ============


def test_bootstrap_sh_has_docker_compose_exec_fallback():
    bs = (Path(__file__).parent.parent.parent / "deploy" / "bootstrap.sh").read_text()
    # The fallback chain: biblichor on PATH -> .venv/bin/python -> docker compose exec
    assert "docker compose" in bs
    assert "exec -T biblichor" in bs
    # The library-root arg picks container-internal /library when docker path is used
    assert 'LIBRARY_ROOT_ARG="/library"' in bs


# ============ R-M-7: SETUP_BOOTSTRAP_TOKEN cleared after first-use ============


def test_bootstrap_sh_clears_setup_token_post_bootstrap():
    bs = (Path(__file__).parent.parent.parent / "deploy" / "bootstrap.sh").read_text()
    # After successful bookorbit-setup, the token line is sed'd to blank
    assert "sed -i" in bs
    assert "BOOKORBIT_SETUP_TOKEN=" in bs
    assert "cleared post-bootstrap" in bs


# ============ R-I-2: README explains the two .env files ============


def test_readme_documents_two_env_files():
    readme = (Path(__file__).parent.parent.parent / "README.md").read_text()
    assert "Two `.env` files" in readme
    # The table should distinguish their roles
    assert "docker compose" in readme
    assert "biblichor application" in readme
