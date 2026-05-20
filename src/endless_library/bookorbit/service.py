"""BookOrbit service layer — shared by the FastAPI handlers and the
CLI. Wraps ensure_bookorbit_ready / run_doctor / scanner trigger +
the encrypted secrets store so callers don't need to thread the
recovery key through every call site.

Phase 6p.2.
"""

from __future__ import annotations

import logging
import secrets as stdlib_secrets
from dataclasses import dataclass
from pathlib import Path

from endless_library.bookorbit.client import BookOrbitClient
from endless_library.bookorbit.doctor import DoctorReport, run_doctor
from endless_library.bookorbit.setup import (
    SetupResult,
    ensure_bookorbit_ready,
)
from endless_library.config import Config
from endless_library.db.schema import connect
from endless_library.secrets_store import (
    delete_secret,
    derive_secrets_key,
    get_secret,
    init_secrets_table,
    list_secret_names,
    set_secret,
)

log = logging.getLogger(__name__)


SECRET_ADMIN_USER = "bookorbit.admin_user"
SECRET_ADMIN_PASSWORD = "bookorbit.admin_password"
SECRET_SETUP_TOKEN = "bookorbit.setup_token"


@dataclass
class BookOrbitStatus:
    """Payload for GET /api/bookorbit/status — drives the SPA's
    setup-wizard / settings UI."""

    enabled: bool
    setup_needed: bool
    has_creds: bool
    library_id: str | None
    library_root: str
    library_root_exists: bool
    url: str
    health_ok: bool
    last_check_error: str | None = None


class BookOrbitServiceError(Exception):
    pass


class BookOrbitService:
    """All BookOrbit ops the SPA + CLI share.

    Construct one per request — it's cheap (a few path lookups +
    a key derivation cached on first secret-touch)."""

    def __init__(self, cfg: Config, db_path: Path, restore_key_path: Path):
        self._cfg = cfg
        self._db_path = db_path
        self._restore_key_path = restore_key_path
        self._secrets_key_cache: bytes | None = None

    # ---------- secrets ----------

    @property
    def _secrets_key(self) -> bytes:
        if self._secrets_key_cache is None:
            # Phase 6p.5: prefer to derive from the existing age recovery
            # key if it's already there (so backups + secrets share a
            # trust root). Otherwise, generate a dedicated symmetric
            # secrets key file — independent of age, so we don't require
            # age-keygen to be installed for the SPA to work out of the
            # box. The user can later run `biblichor backup-key` to set
            # up the age key for backup encryption; that doesn't affect
            # secrets (different concerns, different keys).
            secrets_dir = self._restore_key_path.parent
            secrets_key_file = secrets_dir / "secrets.key"

            if self._restore_key_path.exists():
                self._secrets_key_cache = derive_secrets_key(self._restore_key_path)
            elif secrets_key_file.exists():
                self._secrets_key_cache = derive_secrets_key(secrets_key_file)
            else:
                # Generate a dedicated 32-byte symmetric key.
                import os as _os

                secrets_dir.mkdir(parents=True, exist_ok=True)
                secrets_key_file.write_bytes(_os.urandom(64))
                _os.chmod(secrets_key_file, 0o600)
                self._secrets_key_cache = derive_secrets_key(secrets_key_file)
        return self._secrets_key_cache

    def has_admin_creds(self) -> bool:
        with connect(self._db_path) as conn:
            init_secrets_table(conn)
            names = list_secret_names(conn)
        return SECRET_ADMIN_PASSWORD in names

    def get_admin_creds(self) -> tuple[str, str] | None:
        with connect(self._db_path) as conn:
            init_secrets_table(conn)
            user = get_secret(conn, self._secrets_key, SECRET_ADMIN_USER)
            pw = get_secret(conn, self._secrets_key, SECRET_ADMIN_PASSWORD)
        if user and pw:
            return user, pw
        return None

    def store_admin_creds(self, username: str, password: str) -> None:
        with connect(self._db_path) as conn:
            init_secrets_table(conn)
            set_secret(conn, self._secrets_key, SECRET_ADMIN_USER, username)
            set_secret(conn, self._secrets_key, SECRET_ADMIN_PASSWORD, password)

    def clear_admin_creds(self) -> None:
        with connect(self._db_path) as conn:
            init_secrets_table(conn)
            delete_secret(conn, SECRET_ADMIN_USER)
            delete_secret(conn, SECRET_ADMIN_PASSWORD)
            delete_secret(conn, SECRET_SETUP_TOKEN)

    # ---------- status ----------

    def status(self) -> BookOrbitStatus:
        cfg = self._cfg
        bo = cfg.bookorbit
        library_root_exists = bool(bo.library_root) and Path(bo.library_root).exists()
        setup_needed = True
        health_ok = False
        err: str | None = None

        if bo.enabled and bo.url:
            try:
                with BookOrbitClient(bo.url) as client:
                    health_ok = client.health()
                    status_payload = client.setup_status()
                    setup_needed = bool(status_payload.get("needsSetup", True))
            except Exception as e:
                err = f"{type(e).__name__}: {e}"

        return BookOrbitStatus(
            enabled=bo.enabled,
            setup_needed=setup_needed,
            has_creds=self.has_admin_creds(),
            library_id=bo.library_id or None,
            library_root=bo.library_root or "",
            library_root_exists=library_root_exists,
            url=bo.url or "",
            health_ok=health_ok,
            last_check_error=err,
        )

    # ---------- setup ----------

    def run_setup(
        self,
        *,
        admin_username: str,
        admin_email: str,
        admin_name: str,
        admin_password: str,
        setup_token: str,
        library_root: str | None = None,
        biblichor_config_yaml_path: Path,
    ) -> SetupResult:
        """Drive BookOrbit's first-run /auth/setup + create the
        biblichor library + store credentials encrypted.

        ONLY for the first-run case (BookOrbit reports needsSetup=true).
        If BookOrbit is already set up, raises a BookOrbitServiceError
        directing the caller to Change-password / Stored-creds /
        recreate_watched_library instead — otherwise we'd fail with an
        opaque 401 on the login step.
        """
        if not self._cfg.bookorbit.enabled or not self._cfg.bookorbit.url:
            raise BookOrbitServiceError("BookOrbit is not enabled in config.yaml")

        # Pre-check: refuse to run setup if BookOrbit is already
        # bootstrapped. Otherwise ensure_bookorbit_ready will try to
        # login with the supplied (likely-wrong) password and 401.
        try:
            with BookOrbitClient(self._cfg.bookorbit.url) as client:
                status_payload = client.setup_status()
                if not status_payload.get("needsSetup", True):
                    raise BookOrbitServiceError(
                        "BookOrbit is already set up. Use 'Change password' "
                        "to rotate the admin password, 'Stored creds' to "
                        "save existing credentials biblichor can authenticate "
                        "with, or 'Recreate watched library' if the library "
                        "is missing in BookOrbit. The setup wizard is only "
                        "for the first-run case."
                    )
        except BookOrbitServiceError:
            raise
        except Exception as e:
            raise BookOrbitServiceError(
                f"could not contact BookOrbit at {self._cfg.bookorbit.url}: {type(e).__name__}: {e}"
            ) from e

        eff_library_root = library_root or self._cfg.bookorbit.library_root or "/library"
        result = ensure_bookorbit_ready(
            url=self._cfg.bookorbit.url,
            setup_token=setup_token,
            admin_username=admin_username,
            admin_name=admin_name,
            admin_email=admin_email,
            admin_password=admin_password,
            library_root=eff_library_root,
            biblichor_config_yaml_path=biblichor_config_yaml_path,
        )
        self.store_admin_creds(admin_username, admin_password)
        return result

    # ---------- doctor ----------

    def doctor(self) -> DoctorReport:
        creds = self.get_admin_creds()
        return run_doctor(
            bookorbit_url=self._cfg.bookorbit.url or "",
            library_root=Path(self._cfg.bookorbit.library_root)
            if self._cfg.bookorbit.library_root
            else None,
            library_id=self._cfg.bookorbit.library_id or None,
            admin_username=creds[0] if creds else None,
            admin_password=creds[1] if creds else None,
        )

    # ---------- admin password change ----------

    def _resolve_current_credentials(self) -> tuple[str, str] | None:
        """Best-effort lookup of the password biblichor should use to
        authenticate as admin. Order:
          1. Encrypted stored creds (the normal case once bootstrap
             has run or the user has saved them).
          2. Env-var fallback (BOOKORBIT_ADMIN_USER / _PASSWORD), for
             the first container start before stored creds exist.
        Returns (username, password) or None if neither path yields
        credentials biblichor can try."""
        import os as _os

        stored = self.get_admin_creds()
        if stored:
            return stored
        env_user = _os.environ.get("BOOKORBIT_ADMIN_USER") or "admin"
        env_pw = _os.environ.get("BOOKORBIT_ADMIN_PASSWORD")
        if env_pw:
            return (env_user, env_pw)
        return None

    def change_admin_password(
        self,
        *,
        new_password: str,
        current_password: str | None = None,
    ) -> dict:
        """Rotate the BookOrbit admin password without requiring the
        user to know or type the current one.

        Mechanism: biblichor authenticates with its stored credentials
        (or env-var fallback), mints a one-time reset token via
        POST /users/{id}/reset-password, then applies the new password
        via POST /auth/reset-password. After success, the new password
        is saved into the encrypted store so Scan / Doctor keep
        working.

        current_password is optional: if supplied (e.g. when the user
        knows it and biblichor doesn't), it's used directly for the
        login step instead of stored/env credentials.
        """
        if current_password:
            creds: tuple[str, str] | None = ("admin", current_password)
            stored = self.get_admin_creds()
            if stored:
                creds = (stored[0], current_password)
        else:
            creds = self._resolve_current_credentials()

        if not creds:
            raise BookOrbitServiceError(
                "biblichor cannot find any credentials to authenticate "
                "with BookOrbit. The container env var "
                "BOOKORBIT_ADMIN_PASSWORD is not set and no credentials "
                "are stored. Restart with the password in the compose "
                "env, OR use 'Stored creds' to save the current "
                "BookOrbit admin password first."
            )
        username, current_pw = creds

        with BookOrbitClient(self._cfg.bookorbit.url) as client:
            try:
                client.login(username=username, password=current_pw)
            except Exception as e:
                raise BookOrbitServiceError(
                    "biblichor could not authenticate with BookOrbit using its "
                    "stored credentials. Either save the current admin password "
                    "via 'Stored creds' and retry, or rotate the password "
                    "out-of-band first."
                ) from e
            user_id = client.current_user_id()
            reset_url = client.mint_reset_url(user_id)
            # token=XXX query param
            from urllib.parse import parse_qs, urlparse

            token = parse_qs(urlparse(reset_url).query).get("token", [""])[0]
            if not token:
                raise BookOrbitServiceError(
                    f"BookOrbit returned a reset URL with no token: {reset_url}"
                )
            client.apply_password_reset(token, new_password)

        self.store_admin_creds(username, new_password)
        return {"ok": True, "username": username}

    # ---------- scan ----------

    def trigger_scan(self) -> dict:
        """Tell BookOrbit to rescan the library. Auth required."""
        creds = self.get_admin_creds()
        if not creds:
            raise BookOrbitServiceError(
                "No admin credentials stored — run setup or POST /api/bookorbit/creds first"
            )
        library_id = self._cfg.bookorbit.library_id
        if not library_id:
            raise BookOrbitServiceError(
                "No library_id in config — run setup first to create the biblichor library"
            )
        with BookOrbitClient(self._cfg.bookorbit.url) as client:
            client.login(username=creds[0], password=creds[1])
            client.trigger_scan(library_id=library_id)
        return {"ok": True, "library_id": library_id}

    # ---------- token rotation (setup_token) ----------

    def recreate_watched_library(self) -> dict:
        """Use stored credentials to login and ensure the biblichor
        library exists in BookOrbit. The recovery path when the
        library was deleted in BookOrbit's own UI, or when the
        library_id in config.yaml has drifted from what BookOrbit
        actually has.

        Does NOT touch admin credentials. Requires stored creds.
        """
        creds = self.get_admin_creds()
        if not creds:
            raise BookOrbitServiceError(
                "No stored credentials. Open 'Stored creds' first, "
                "save your BookOrbit admin username + password, then "
                "retry."
            )
        username, password = creds
        from endless_library.bookorbit.setup import (
            DEFAULT_LIBRARY_ICON,
            DEFAULT_LIBRARY_NAME,
        )

        with BookOrbitClient(self._cfg.bookorbit.url) as client:
            client.login(username=username, password=password)
            libs = client.list_libraries()
            existing = next(
                (lib for lib in libs if lib.get("name") == DEFAULT_LIBRARY_NAME),
                None,
            )
            if existing:
                return {
                    "ok": True,
                    "library_id": existing["id"],
                    "created": False,
                }
            new_lib = client.create_library(
                name=DEFAULT_LIBRARY_NAME,
                icon=DEFAULT_LIBRARY_ICON,
                folders=[self._cfg.bookorbit.library_root or "/library"],
                watch=True,
                organization_mode=self._cfg.bookorbit.organization_mode or "book_per_folder",
                auto_scan_cron_expression="0 * * * *",
            )
            return {"ok": True, "library_id": new_lib["id"], "created": True}

    @staticmethod
    def generate_setup_token() -> str:
        """48-char URL-safe token for /auth/setup. Returned to the SPA
        so the user can show it in the wizard if BookOrbit asks (some
        setups require it as a bootstrap token)."""
        return stdlib_secrets.token_urlsafe(48)
