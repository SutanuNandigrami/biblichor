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
    library_root: str,
    biblichor_config_yaml_path: Path,
    library_mount: str = DEFAULT_LIBRARY_MOUNT,
) -> SetupResult:
    """First-run bootstrap or no-op resume.

    `library_root` is the path biblichor SEES the BookOrbit-watched
    library at. In a docker compose deployment this is the
    container-internal mount point (default: /library); the host path
    is irrelevant to the pipeline. In a native deployment this is the
    host path biblichor was launched from. Caller picks the right
    value.
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
            # Phase 6o.6 (D-2): hourly auto-scan cron in addition to the
            # @parcel/watcher. The watcher catches all real-time drops, but
            # the cron is belt-and-braces for cases where the watcher misses
            # an event (network shares, container restarts mid-write, etc).
            created = client.create_library(
                name=DEFAULT_LIBRARY_NAME,
                icon=DEFAULT_LIBRARY_ICON,
                folders=[library_mount],
                watch=True,
                organization_mode="book_per_folder",
                auto_scan_cron_expression="0 * * * *",  # hourly
            )
            library_id = created["id"]

    # Always update config.yaml — this is the single source of truth.
    # Only writes if any field differs (keeps mtime stable on no-op reruns).
    # Phase 6o.2 (I-3): handle missing yaml gracefully — create a default
    # Config() instead of raising on a fresh install.
    from endless_library.config import Config, load_config, save_config

    if biblichor_config_yaml_path.exists():
        cfg = load_config(biblichor_config_yaml_path)
    else:
        log.info(
            "biblichor config.yaml not found at %s; creating fresh",
            biblichor_config_yaml_path,
        )
        biblichor_config_yaml_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = Config()
    library_id_str = str(library_id)
    fields_changed = (
        not cfg.bookorbit.enabled
        or cfg.bookorbit.library_root != library_root
        or cfg.bookorbit.organization_mode != "book_per_folder"
        or cfg.bookorbit.library_id != library_id_str
        or (url and cfg.bookorbit.url != url)
    )
    if fields_changed:
        cfg.bookorbit.enabled = True
        cfg.bookorbit.url = url
        cfg.bookorbit.library_root = library_root
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
