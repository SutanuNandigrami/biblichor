"""High-level "make BookOrbit ready for biblichor to use" routine.

Idempotent. Calls /auth/setup-status to decide whether to:
  - bootstrap admin from scratch (first run), OR
  - just log in (subsequent runs)
then ensures a watched library exists at the expected mount point.

Persisted state lands in config/bookorbit.json so the pipeline can
read library_id without re-authing on every boot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from endless_library.bookorbit.client import (
    BookOrbitClient,
    BookOrbitConfig,
    BookOrbitError,
)

log = logging.getLogger(__name__)

DEFAULT_LIBRARY_NAME = "biblichor"
DEFAULT_LIBRARY_ICON = "📚"
DEFAULT_LIBRARY_MOUNT = "/books"  # what BookOrbit sees inside its container


@dataclass
class SetupResult:
    config_path: Path
    library_id: str
    just_created: bool   # admin created right now (False if existing)
    config_yaml_updated: bool = False  # Phase 6i: bookorbit section enabled
    config_yaml_path: Path | None = None


def ensure_bookorbit_ready(
    *,
    url: str,
    setup_token: str,
    admin_username: str,
    admin_name: str,
    admin_email: str,
    admin_password: str,
    library_root_on_host: str,
    config_path: Path,
    library_mount: str = DEFAULT_LIBRARY_MOUNT,
    biblichor_config_yaml_path: Path | None = None,
) -> SetupResult:
    """First-run bootstrap or no-op resume.

    `library_root_on_host` is the host directory mounted at
    `library_mount` inside the bookorbit container. We persist it so
    the pipeline knows where to drop files on the host.
    """
    with BookOrbitClient(url) as client:
        status = client.setup_status()
        needs_setup = status.get("needsSetup", status.get("needs_setup", True))
        just_created = False
        if needs_setup:
            log.info("bookorbit: bootstrapping first admin (%s)", admin_email)
            client.setup_admin(
                token=setup_token,
                username=admin_username,
                name=admin_name,
                email=admin_email,
                password=admin_password,
            )
            just_created = True

        client.login(username=admin_username, password=admin_password)

        # Find or create the biblichor library
        libs = client.list_libraries()
        existing = next(
            (l for l in libs if l.get("name") == DEFAULT_LIBRARY_NAME),
            None,
        )
        if existing:
            library_id = existing["id"]
            log.info("bookorbit: reusing existing library id=%s", library_id)
        else:
            log.info("bookorbit: creating library %s -> %s", DEFAULT_LIBRARY_NAME, library_mount)
            created = client.create_library(
                name=DEFAULT_LIBRARY_NAME,
                icon=DEFAULT_LIBRARY_ICON,
                folders=[library_mount],
                watch=True,
                organization_mode="book_per_folder",
            )
            library_id = created["id"]

    config = BookOrbitConfig(
        url=url,
        library_id=library_id,
        library_root_on_host=library_root_on_host,
        organization_mode="book_per_folder",
    )
    config.save(config_path)

    # Phase 6i: flip cfg.bookorbit.enabled in the live config.yaml so the
    # pipeline drop actually fires after setup. Without this step, the
    # pipeline integration is silently disabled and the integration looks
    # complete but is wired to nothing.
    config_yaml_updated = False
    if biblichor_config_yaml_path is not None and biblichor_config_yaml_path.exists():
        from endless_library.config import load_config, save_config

        cfg = load_config(biblichor_config_yaml_path)
        # Only update if any field differs — keeps file mtime stable on idempotent runs
        if (not cfg.bookorbit.enabled
                or cfg.bookorbit.library_root_on_host != library_root_on_host
                or cfg.bookorbit.organization_mode != "book_per_folder"):
            cfg.bookorbit.enabled = True
            cfg.bookorbit.library_root_on_host = library_root_on_host
            cfg.bookorbit.organization_mode = "book_per_folder"
            save_config(cfg, biblichor_config_yaml_path)
            log.info("biblichor config.yaml: bookorbit integration enabled")
            config_yaml_updated = True

    return SetupResult(
        config_path=config_path,
        library_id=library_id,
        just_created=just_created,
        config_yaml_updated=config_yaml_updated,
        config_yaml_path=biblichor_config_yaml_path,
    )
