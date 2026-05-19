"""High-level "make BookOrbit ready for biblichor to use" routine.

Idempotent. Calls /auth/setup-status to decide whether to:
  - bootstrap admin from scratch (first run), OR
  - just log in (subsequent runs)
then ensures a watched library exists at the expected mount point,
and writes both the URL and the library_id directly into biblichor's
config.yaml so the pipeline + future CLI calls have a single source
of truth.

Phase 6m.ii consolidated this from a dual-file model (bookorbit.json
+ config.yaml) down to config.yaml only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from endless_library.bookorbit.client import (
    BookOrbitClient,
)

log = logging.getLogger(__name__)

DEFAULT_LIBRARY_NAME = "biblichor"
DEFAULT_LIBRARY_ICON = "📚"
DEFAULT_LIBRARY_MOUNT = "/books"  # what BookOrbit sees inside its container


@dataclass
class SetupResult:
    library_id: str
    just_created: bool  # admin created right now (False if existing)
    config_yaml_updated: bool = False  # config.yaml fields changed this run
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
    biblichor_config_yaml_path: Path,
    library_mount: str = DEFAULT_LIBRARY_MOUNT,
) -> SetupResult:
    """First-run bootstrap or no-op resume.

    `library_root_on_host` is the host directory mounted at
    `library_mount` inside the bookorbit container. Persisted in
    config.yaml so the pipeline knows where to drop files on the host.
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
            (lib for lib in libs if lib.get("name") == DEFAULT_LIBRARY_NAME),
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

    # Always update config.yaml — this is the single source of truth.
    # Only writes if any field differs (keeps mtime stable on no-op reruns).
    from endless_library.config import load_config, save_config

    cfg = load_config(biblichor_config_yaml_path)
    library_id_str = str(library_id)
    fields_changed = (
        not cfg.bookorbit.enabled
        or cfg.bookorbit.library_root_on_host != library_root_on_host
        or cfg.bookorbit.organization_mode != "book_per_folder"
        or cfg.bookorbit.library_id != library_id_str
        or (url and cfg.bookorbit.url != url)
    )
    if fields_changed:
        cfg.bookorbit.enabled = True
        cfg.bookorbit.url = url
        cfg.bookorbit.library_root_on_host = library_root_on_host
        cfg.bookorbit.organization_mode = "book_per_folder"
        cfg.bookorbit.library_id = library_id_str
        save_config(cfg, biblichor_config_yaml_path)
        log.info("biblichor config.yaml: bookorbit integration updated")

    return SetupResult(
        library_id=library_id_str,
        just_created=just_created,
        config_yaml_updated=fields_changed,
        config_yaml_path=biblichor_config_yaml_path,
    )
