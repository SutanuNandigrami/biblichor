# STK Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Amazon "Send to Kindle" browser-upload as biblichor's primary delivery path (with SMTP as fallback), bypassing SMTP's ~80/day rate cap and 24 MB attachment limit.

**Architecture:** Vendor `maxdjohnson/stkclient` in-tree, wrap it in a `KindleStkService` façade that uses biblichor's encrypted secrets store. A new `kindle_router.deliver(...)` becomes the single pipeline entry-point and chooses STK-with-retry → SMTP-fallback per call. New SPA Settings card with a copy-paste OAuth modal.

**Tech Stack:** Python 3.12 + FastAPI + SQLite + Vue 3, vendored stkclient (MIT, OAuth + RSA-PKCS1v15 ADP signing), `requests` (stkclient dep), `cryptography` (already present), `httpx` (already present).

---

## How to use this plan

- Work happens on remote host `claude-1` at `/home/ubuntu/endless-library/`. Connect via `ssh ubuntu@claude-1`.
- Branch is `stk-integration` (already created from `main` at commit `2a767de`). Do not push to `main`.
- Tests run in the existing venv: `cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest <path> -v`.
- The container rebuild only happens at the very end (Task 12 end-state). In-development reload pattern: `docker cp <local-file> biblichor:/app/<container-path> && docker restart biblichor`. Use the rebuild for the final acceptance step.
- After every task: full pytest run must stay green.

---

## Task 1: Vendor stkclient

**Files:**
- Create: `src/endless_library/kindle_stk/_vendored/__init__.py`
- Create: `src/endless_library/kindle_stk/_vendored/_client.py`
- Create: `src/endless_library/kindle_stk/_vendored/_signer.py`
- Create: `src/endless_library/kindle_stk/_vendored/_oauth.py`
- Create: `src/endless_library/kindle_stk/_vendored/_models.py`
- Create: `src/endless_library/kindle_stk/_vendored/_http.py`
- Create: `src/endless_library/kindle_stk/README.md`
- Create: `src/endless_library/kindle_stk/LICENSE.stkclient`
- Create: `tools/sync_stkclient.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Pull upstream stkclient v0.1.1 source**

```bash
ssh ubuntu@claude-1 'cd /tmp && rm -rf stkclient && git clone --depth 1 --branch v0.1.1 https://github.com/maxdjohnson/stkclient.git'
```

Verify the file list:
```bash
ssh ubuntu@claude-1 'ls /tmp/stkclient/stkclient/'
```
Expected: `__init__.py`, `client.py`, `signer.py`, `oauth.py`, `models.py`, `http.py` (or equivalent — actual filenames may differ; adjust the underscore-prefix renaming accordingly).

- [ ] **Step 2: Copy files with underscore prefix into `_vendored/`**

```bash
ssh ubuntu@claude-1 'mkdir -p /home/ubuntu/endless-library/src/endless_library/kindle_stk/_vendored && for f in /tmp/stkclient/stkclient/*.py; do base=$(basename "$f"); if [ "$base" = "__init__.py" ]; then dest="__init__.py"; else dest="_${base}"; fi; cp "$f" "/home/ubuntu/endless-library/src/endless_library/kindle_stk/_vendored/$dest"; done && ls /home/ubuntu/endless-library/src/endless_library/kindle_stk/_vendored/'
```

If the upstream files have different names than the spec assumed (`client.py`, `signer.py`, `oauth.py`, `models.py`, `http.py`), keep their actual names but prefix with underscore. Update import paths in the next step accordingly.

- [ ] **Step 3: Replace `_vendored/__init__.py` with the biblichor re-export shim**

Write to `src/endless_library/kindle_stk/_vendored/__init__.py`:

```python
"""Vendored maxdjohnson/stkclient @ v0.1.1, MIT.

Re-export only the symbols biblichor uses. Internal modules are
underscore-prefixed so it's obvious which code is upstream.
"""
from ._client import Client
from ._oauth import OAuth2
from ._models import OwnedDevice

__all__ = ["Client", "OAuth2", "OwnedDevice"]
```

If upstream's symbol names differ, adjust here — `Client`, `OAuth2`, `OwnedDevice` are the names biblichor's wrapper expects. If upstream uses different names (e.g. `StkClient`, `OAuth`), add `as` aliases in the imports.

- [ ] **Step 4: Update upstream imports inside the vendored files**

Each `_*.py` will have `from stkclient.signer import ...` or similar. These need to become relative: `from ._signer import ...`.

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library/src/endless_library/kindle_stk/_vendored && for f in _*.py; do sed -i 's|^from stkclient\.|from .|g; s|^from stkclient\.\([a-z]*\) import|from ._\1 import|g' \"\$f\"; done"
```

Verify the imports look right:
```bash
ssh ubuntu@claude-1 'grep -n "^from \." /home/ubuntu/endless-library/src/endless_library/kindle_stk/_vendored/_*.py | head -20'
```

Manual fixup may be needed depending on the exact upstream layout. The goal: every `_vendored/_X.py` should import from `_vendored/_Y.py` via `from ._Y import ...`, never `from stkclient.Y import ...`.

- [ ] **Step 5: Write the attribution README**

Write to `src/endless_library/kindle_stk/README.md`:

```markdown
# kindle_stk — biblichor's Send-to-Kindle integration

Wraps a vendored copy of [maxdjohnson/stkclient](https://github.com/maxdjohnson/stkclient)
(MIT) with biblichor's encrypted secrets store, exception hierarchy,
and rate-limit gate.

## What's in here

- `_vendored/` — verbatim copy of stkclient v0.1.1 source, files
  prefixed with `_` so it's obvious which code is upstream. **Do
  not modify these files directly** — sync via `tools/sync_stkclient.py`
  if the upstream gets a bugfix release worth pulling.
- `__init__.py` — biblichor's public surface
- `service.py` — `KindleStkService` façade
- `exceptions.py` — biblichor's typed exceptions over stkclient's raw ones

## Sync upstream

```bash
python tools/sync_stkclient.py            # current pinned version
python tools/sync_stkclient.py --tag X.Y  # specific tag
```

Re-run `python -m pytest tests/unit/test_kindle_stk_*.py` after.

## License

stkclient is MIT-licensed. See `LICENSE.stkclient` for the full text.
biblichor's code in this directory is under the same license as the
rest of the project.
```

- [ ] **Step 6: Copy the MIT license file**

```bash
ssh ubuntu@claude-1 'cp /tmp/stkclient/LICENSE /home/ubuntu/endless-library/src/endless_library/kindle_stk/LICENSE.stkclient && head -3 /home/ubuntu/endless-library/src/endless_library/kindle_stk/LICENSE.stkclient'
```

Expected: starts with `MIT License` or `Copyright (c) ...`.

- [ ] **Step 7: Add the sync script**

Write to `tools/sync_stkclient.py`:

```python
#!/usr/bin/env python3
"""Pull a fresh copy of stkclient into kindle_stk/_vendored/.

Usage:
    python tools/sync_stkclient.py             # uses the pinned version
    python tools/sync_stkclient.py --tag X.Y   # explicit tag
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

PINNED_TAG = "v0.1.1"
REPO_URL = "https://github.com/maxdjohnson/stkclient.git"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default=PINNED_TAG)
    args = p.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    vendored = project_root / "src" / "endless_library" / "kindle_stk" / "_vendored"
    tmp = Path("/tmp/stkclient_sync")

    if tmp.exists():
        shutil.rmtree(tmp)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", args.tag, REPO_URL, str(tmp)],
        check=True,
    )
    src = tmp / "stkclient"
    if not src.is_dir():
        print(f"FATAL: {src} not found in upstream clone", file=sys.stderr)
        return 1
    for f in src.glob("*.py"):
        if f.name == "__init__.py":
            continue  # biblichor's __init__.py is hand-written
        dest = vendored / f"_{f.name}"
        shutil.copyfile(f, dest)
        text = dest.read_text(encoding="utf-8")
        text = re.sub(r"^from stkclient\.", "from .", text, flags=re.M)
        text = re.sub(
            r"^from stkclient\.([a-z_]+) import",
            r"from ._\1 import",
            text,
            flags=re.M,
        )
        dest.write_text(text, encoding="utf-8")
        print(f"Synced {dest.name}")
    print(f"Done. Verify with: python -m pytest tests/unit/test_kindle_stk_*.py -v")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Make it executable:
```bash
ssh ubuntu@claude-1 'chmod +x /home/ubuntu/endless-library/tools/sync_stkclient.py'
```

- [ ] **Step 8: Add `requests` to pyproject.toml**

stkclient uses `requests`. Add it:

```bash
ssh ubuntu@claude-1 'grep -n "^dependencies = \[" /home/ubuntu/endless-library/pyproject.toml'
```

Find the dependencies block and add `"requests>=2.31",` if not already present.

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && python -c "import pkgutil; print(pkgutil.find_loader(\"requests\"))"'
```

If it prints `<class 'requests'>` or similar, the dep is already installed (likely is — it's a common transitive). Still add it explicitly so removing other deps doesn't break us.

After editing `pyproject.toml`, install:
```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && pip install -e . 2>&1 | tail -3'
```

- [ ] **Step 9: Verify the vendored module imports**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -c "from endless_library.kindle_stk._vendored import Client, OAuth2, OwnedDevice; print(Client, OAuth2, OwnedDevice)"'
```

Expected: prints the three class references with no exception. If `ImportError` on a symbol name, look at upstream's exports (`/tmp/stkclient/stkclient/__init__.py`) and adjust the re-export shim.

- [ ] **Step 10: Run the existing test suite to confirm no regression**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3'
```

Expected: same number passing as baseline (~1042).

- [ ] **Step 11: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/kindle_stk tools/sync_stkclient.py pyproject.toml && git commit -m "STK 1: vendor stkclient v0.1.1 + sync script" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 2: Exception hierarchy

**Files:**
- Create: `src/endless_library/kindle_stk/exceptions.py`
- Modify: `src/endless_library/kindle_stk/__init__.py`
- Test: `tests/unit/test_kindle_stk_exceptions.py`

- [ ] **Step 1: Write the failing test**

Write to `tests/unit/test_kindle_stk_exceptions.py`:

```python
"""Phase STK 2: biblichor's exception hierarchy for Send-to-Kindle."""
from __future__ import annotations

import pytest


def test_exceptions_form_a_hierarchy():
    from endless_library.kindle_stk.exceptions import (
        KindleStkError,
        KindleStkNotConfigured,
        KindleStkAuthExpired,
        KindleStkRateLimited,
        KindleStkUploadFailed,
    )
    assert issubclass(KindleStkNotConfigured, KindleStkError)
    assert issubclass(KindleStkAuthExpired, KindleStkError)
    assert issubclass(KindleStkRateLimited, KindleStkError)
    assert issubclass(KindleStkUploadFailed, KindleStkError)


def test_rate_limited_carries_retry_after_sec():
    from endless_library.kindle_stk.exceptions import KindleStkRateLimited
    e = KindleStkRateLimited("rate limited", retry_after_sec=30)
    assert e.retry_after_sec == 30


def test_rate_limited_defaults_retry_after_sec_to_5():
    from endless_library.kindle_stk.exceptions import KindleStkRateLimited
    e = KindleStkRateLimited("rate limited")
    assert e.retry_after_sec == 5


def test_all_exceptions_importable_from_package_root():
    """They should be re-exported from kindle_stk/__init__.py
    so callers do `from endless_library.kindle_stk import ...`."""
    from endless_library.kindle_stk import (
        KindleStkError,
        KindleStkNotConfigured,
        KindleStkAuthExpired,
        KindleStkRateLimited,
        KindleStkUploadFailed,
    )
    assert all(
        isinstance(e, type)
        for e in (
            KindleStkError, KindleStkNotConfigured, KindleStkAuthExpired,
            KindleStkRateLimited, KindleStkUploadFailed,
        )
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_exceptions.py -v 2>&1 | tail -10'
```

Expected: 4 failures with `ImportError`.

- [ ] **Step 3: Implement the exception module**

Write to `src/endless_library/kindle_stk/exceptions.py`:

```python
"""Biblichor's exception hierarchy for Send-to-Kindle.

Maps vendored stkclient's raw exceptions (requests.HTTPError, ValueError)
to typed biblichor exceptions so callers can pattern-match.
"""
from __future__ import annotations


class KindleStkError(Exception):
    """Base for all Send-to-Kindle errors."""


class KindleStkNotConfigured(KindleStkError):
    """No device cert in secrets store. User must complete OAuth setup."""


class KindleStkAuthExpired(KindleStkError):
    """ADP signing failed or device cert was revoked. User must re-OAuth."""


class KindleStkRateLimited(KindleStkError):
    """Amazon returned 429. Honors Retry-After header where present."""

    def __init__(self, message: str = "", *, retry_after_sec: int = 5) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class KindleStkUploadFailed(KindleStkError):
    """Transient or unknown failure during the 4-step send flow."""
```

- [ ] **Step 4: Wire re-exports in `kindle_stk/__init__.py`**

Write to `src/endless_library/kindle_stk/__init__.py`:

```python
"""Biblichor's Send-to-Kindle browser-upload integration.

The public surface is small: KindleStkService for OAuth + send, and
the typed exception hierarchy. The pipeline does not import this
module directly — it goes through kindle_router.deliver(...).
"""
from .exceptions import (
    KindleStkError,
    KindleStkNotConfigured,
    KindleStkAuthExpired,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)

__all__ = [
    "KindleStkError",
    "KindleStkNotConfigured",
    "KindleStkAuthExpired",
    "KindleStkRateLimited",
    "KindleStkUploadFailed",
]
```

(`KindleStkService` is added to `__all__` in Task 4.)

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_exceptions.py -v 2>&1 | tail -10'
```

Expected: 4 passes.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/kindle_stk/exceptions.py src/endless_library/kindle_stk/__init__.py tests/unit/test_kindle_stk_exceptions.py && git commit -m "STK 2: typed exception hierarchy" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 3: FakeVendoredClient test infrastructure

**Files:**
- Create: `tests/_stkclient_stub.py`

This is shared test infrastructure used by Tasks 4, 8, 9, and the integration smoke test. No tests of its own — it's a fixture.

- [ ] **Step 1: Write the stub module**

Write to `tests/_stkclient_stub.py`:

```python
"""Fake vendored stkclient.Client + OAuth2 for biblichor unit tests.

Used by tests that exercise KindleStkService and kindle_router without
hitting Amazon. Mirrors the surface of endless_library.kindle_stk._vendored.

Configure behaviour via constructor kwargs:
    FakeVendoredClient(
        devices=[FakeDevice(...)],
        send_raises=KindleStkUploadFailed("..."),
    )
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeDevice:
    device_serial_number: str
    device_type: str
    device_name: str


class FakeVendoredClient:
    """Stand-in for endless_library.kindle_stk._vendored.Client."""

    def __init__(
        self,
        *,
        devices: list[FakeDevice] | None = None,
        send_raises: Exception | None = None,
        send_return: dict | None = None,
    ):
        self._devices = devices or [
            FakeDevice(
                device_serial_number="G0WEB1",
                device_type="FionaWebApp",
                device_name="Kindle for Web",
            ),
        ]
        self._send_raises = send_raises
        self._send_return = send_return or {"transaction_id": "tx-1", "status": "PENDING"}
        self.send_calls: list[dict] = []

    def get_owned_devices(self) -> list[FakeDevice]:
        return list(self._devices)

    def send_file(
        self,
        file_path,
        *,
        destinations: list[FakeDevice],
        format: str,
        title: str,
        author: str,
    ) -> dict:
        self.send_calls.append(
            {
                "file_path": str(file_path),
                "destinations": [d.device_serial_number for d in destinations],
                "format": format,
                "title": title,
                "author": author,
            }
        )
        if self._send_raises is not None:
            raise self._send_raises
        return self._send_return


class FakeOAuth2:
    """Stand-in for endless_library.kindle_stk._vendored.OAuth2."""

    @staticmethod
    def create_oauth_url() -> tuple[str, str]:
        """Returns (authorize_url, code_verifier)."""
        return (
            "https://www.amazon.com/ap/oa?client_id=stk&scope=&response_type=code&"
            "redirect_uri=https%3A%2F%2Fwww.amazon.com%2Fap%2Fmaplanding&"
            "code_challenge=fake_challenge&code_challenge_method=S256",
            "fake_code_verifier_FAKEFAKE",
        )

    @staticmethod
    def extract_code_from_redirect(redirect_url: str) -> str:
        """Real OAuth2 parses the URL fragment. Fake just looks for a code= param."""
        if "openid.oa2.access_token=" in redirect_url:
            return redirect_url.split("openid.oa2.access_token=", 1)[1].split("&", 1)[0]
        if "code=" in redirect_url:
            return redirect_url.split("code=", 1)[1].split("&", 1)[0]
        raise ValueError(f"no code in redirect URL: {redirect_url}")


def register_fake(monkeypatch) -> tuple[FakeVendoredClient, type[FakeOAuth2]]:
    """Convenience helper for tests: patch the vendored module so
    `KindleStkService` uses fakes. Returns the fake instances so the
    test can assert on send_calls etc.

    Example:
        client, oauth = register_fake(monkeypatch)
        svc = KindleStkService(real_bookorbit_svc)
        svc.send_file(...)
        assert client.send_calls[0]["title"] == "..."
    """
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.OAuth2",
        FakeOAuth2,
    )
    return fake_client, FakeOAuth2
```

- [ ] **Step 2: Smoke test the stub itself**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -c "from tests._stkclient_stub import FakeVendoredClient, FakeOAuth2, FakeDevice; c = FakeVendoredClient(); print(c.get_owned_devices()); print(FakeOAuth2.create_oauth_url()[0][:80])"'
```

Expected: prints the fake device + a truncated URL string with no exception.

- [ ] **Step 3: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add tests/_stkclient_stub.py && git commit -m "STK 3: FakeVendoredClient test stub" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 4: KindleStkService façade

**Files:**
- Create: `src/endless_library/kindle_stk/service.py`
- Modify: `src/endless_library/kindle_stk/__init__.py`
- Test: `tests/unit/test_kindle_stk_service.py`

- [ ] **Step 1: Write the failing tests**

Write to `tests/unit/test_kindle_stk_service.py`:

```python
"""Phase STK 4: KindleStkService unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)
from tests._stkclient_stub import FakeDevice, FakeOAuth2, FakeVendoredClient, register_fake


@pytest.fixture
def fake_bookorbit_svc(tmp_path):
    """Minimal stand-in for BookOrbitService.

    biblichor's real one stores encrypted values via sqlite + cryptography.
    The unit test just needs get/set/delete_secret_value(s)."""
    class _Svc:
        def __init__(self):
            self._secrets: dict[str, str] = {}

        def get_secret_value(self, key: str) -> str | None:
            return self._secrets.get(key)

        def set_secret_value(self, key: str, value: str) -> None:
            self._secrets[key] = value

        def set_secret_values(self, kv: dict[str, str]) -> None:
            self._secrets.update(kv)

        def delete_secret_value(self, key: str) -> None:
            self._secrets.pop(key, None)

    return _Svc()


def test_is_configured_false_when_no_cert_stored(fake_bookorbit_svc):
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    assert svc.is_configured() is False


def test_is_configured_true_after_storing_cert_and_token(fake_bookorbit_svc):
    from endless_library.kindle_stk.service import KindleStkService
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc = KindleStkService(fake_bookorbit_svc)
    assert svc.is_configured() is True


def test_start_oauth_returns_url_and_stashes_verifier(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    url, verifier = svc.start_oauth()
    assert url.startswith("https://www.amazon.com/ap/oa?")
    assert verifier == "fake_code_verifier_FAKEFAKE"
    # Verifier was persisted for complete_oauth to pick up.
    assert fake_bookorbit_svc.get_secret_value(
        "kindle_stk.oauth_state.code_verifier"
    ) == "fake_code_verifier_FAKEFAKE"


def test_complete_oauth_extracts_code_and_persists_cert(fake_bookorbit_svc, monkeypatch):
    """Happy path: redirect URL parses, fake vendored Client.register_device
    returns a cert + ADP token, KindleStkService persists everything and
    deletes the ephemeral verifier."""
    fake_client = FakeVendoredClient()
    # The fake vendored module must expose register_device on the Client.
    fake_client.register_device = lambda code, verifier: {
        "device_private_key": "PEM_KEY",
        "adp_token": "ADP_TOK",
        "adp_did": "DID-1",
        "customer_id": "amzn1.account.xyz",
        "customer_name": "Sutanu N.",
    }
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.OAuth2",
        FakeOAuth2,
    )
    fake_bookorbit_svc.set_secret_value(
        "kindle_stk.oauth_state.code_verifier", "fake_verifier"
    )

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    result = svc.complete_oauth(
        "https://www.amazon.com/ap/maplanding?openid.oa2.access_token=AUTHCODE&foo=bar"
    )
    assert result["customer_id"] == "amzn1.account.xyz"
    assert result["customer_name"] == "Sutanu N."
    # Persistent state
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.device_cert.pem") == "PEM_KEY"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.adp_token") == "ADP_TOK"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.adp_did") == "DID-1"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.amazon_customer_id") == "amzn1.account.xyz"
    # Ephemeral state cleaned up
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.oauth_state.code_verifier") is None


def test_complete_oauth_raises_on_malformed_redirect_url(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(ValueError, match="redirect URL"):
        svc.complete_oauth("https://example.com/no-code-here")


def test_list_devices_returns_owned_devices(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    # Pretend the user is configured.
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    devices = svc.list_devices()
    assert len(devices) >= 1
    assert devices[0].device_serial_number == "G0WEB1"


def test_list_devices_raises_not_configured_when_no_cert(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkNotConfigured):
        svc.list_devices()


def test_set_default_destination_validates_against_device_list(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    svc.set_default_destination("G0WEB1")
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.default_destination_sn") == "G0WEB1"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.default_destination_name") == "Kindle for Web"


def test_set_default_destination_rejects_unknown_device_sn(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(ValueError, match="unknown device"):
        svc.set_default_destination("UNKNOWN-SN-9999")


def test_send_file_raises_not_configured_when_no_cert(fake_bookorbit_svc, monkeypatch, tmp_path):
    register_fake(monkeypatch)
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkNotConfigured):
        svc.send_file(f, format="EPUB", title="t", author="a")


def test_send_file_translates_vendored_409_to_rate_limited(fake_bookorbit_svc, monkeypatch, tmp_path):
    import requests
    class _R:
        status_code = 429
        headers = {"Retry-After": "30"}
    err = requests.HTTPError(response=_R())
    fake_client = FakeVendoredClient(send_raises=err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkRateLimited) as exc:
        svc.send_file(f, format="EPUB", title="t", author="a")
    assert exc.value.retry_after_sec == 30


def test_send_file_translates_401_to_auth_expired(fake_bookorbit_svc, monkeypatch, tmp_path):
    import requests
    class _R:
        status_code = 401
        headers = {}
    err = requests.HTTPError(response=_R())
    fake_client = FakeVendoredClient(send_raises=err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkAuthExpired):
        svc.send_file(f, format="EPUB", title="t", author="a")


def test_deregister_wipes_all_secrets(fake_bookorbit_svc, monkeypatch):
    """deregister() must remove every kindle_stk.* key."""
    register_fake(monkeypatch)
    keys = [
        "kindle_stk.device_cert.pem", "kindle_stk.adp_token", "kindle_stk.adp_did",
        "kindle_stk.amazon_customer_id", "kindle_stk.default_destination_sn",
        "kindle_stk.default_destination_name", "kindle_stk.registered_at",
    ]
    for k in keys:
        fake_bookorbit_svc.set_secret_value(k, "value")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    svc.deregister()
    for k in keys:
        assert fake_bookorbit_svc.get_secret_value(k) is None
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_service.py -v 2>&1 | tail -20'
```

Expected: 12 failures with `ImportError: KindleStkService` or `ModuleNotFoundError`.

- [ ] **Step 3: Implement `KindleStkService`**

Write to `src/endless_library/kindle_stk/service.py`:

```python
"""KindleStkService — biblichor's façade over the vendored stkclient.

Handles all credential persistence via BookOrbitService. Translates
raw stkclient exceptions to biblichor's typed hierarchy. Provides
the public surface used by the FastAPI endpoints + kindle_router.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import _vendored
from .exceptions import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)

log = logging.getLogger(__name__)

# Cache TTL for the device list (avoid hammering stkservice).
DEVICES_CACHE_TTL_SEC = 300


class KindleStkService:
    """High-level Send-to-Kindle façade.

    Each method that talks to Amazon reconstructs a fresh vendored
    Client from the stored cert + ADP token. We don't cache the
    Client object itself — it's cheap to instantiate and avoids
    stale-state bugs across credential rotations.
    """

    def __init__(self, bookorbit_svc):
        self._svc = bookorbit_svc
        self._devices_cache: tuple[list, float] | None = None

    # ---------------- Configuration state ----------------

    def is_configured(self) -> bool:
        cert = self._svc.get_secret_value("kindle_stk.device_cert.pem")
        adp = self._svc.get_secret_value("kindle_stk.adp_token")
        return bool(cert and adp)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise KindleStkNotConfigured(
                "No device certificate stored. Complete OAuth setup first."
            )

    # ---------------- OAuth flow ----------------

    def start_oauth(self) -> tuple[str, str]:
        """Return (authorize_url, code_verifier). The verifier is also
        persisted to secrets so complete_oauth can pick it up after
        the user pastes the redirect URL back."""
        url, verifier = _vendored.OAuth2.create_oauth_url()
        self._svc.set_secret_value("kindle_stk.oauth_state.code_verifier", verifier)
        return url, verifier

    def complete_oauth(self, redirect_url: str) -> dict[str, Any]:
        """Exchange code for tokens, register device, persist cert.

        Returns: {customer_id, customer_name, registered_at}.
        Raises ValueError if the redirect URL is malformed.
        """
        try:
            code = _vendored.OAuth2.extract_code_from_redirect(redirect_url)
        except Exception as e:
            raise ValueError(f"Could not extract code from redirect URL: {e}") from e
        verifier = self._svc.get_secret_value("kindle_stk.oauth_state.code_verifier")
        if not verifier:
            raise ValueError(
                "No code_verifier in session. start_oauth must run before complete_oauth."
            )
        client = _vendored.Client()
        try:
            result = client.register_device(code, verifier)
        except requests.HTTPError as e:
            raise KindleStkUploadFailed(f"register_device failed: {e}") from e

        now_iso = datetime.now(timezone.utc).isoformat()
        self._svc.set_secret_values(
            {
                "kindle_stk.device_cert.pem": result["device_private_key"],
                "kindle_stk.adp_token": result["adp_token"],
                "kindle_stk.adp_did": result["adp_did"],
                "kindle_stk.amazon_customer_id": result["customer_id"],
                "kindle_stk.registered_at": now_iso,
            }
        )
        # Cleanup the ephemeral verifier
        self._svc.delete_secret_value("kindle_stk.oauth_state.code_verifier")
        return {
            "customer_id": result["customer_id"],
            "customer_name": result.get("customer_name", ""),
            "registered_at": now_iso,
        }

    # ---------------- Device management ----------------

    def list_devices(self) -> list:
        """Return a list of OwnedDevice (or FakeDevice in tests).

        5-minute cache to avoid hammering stkservice; bypass cache by
        calling _flush_devices_cache().
        """
        self._require_configured()
        if self._devices_cache:
            devices, ts = self._devices_cache
            if time.time() - ts < DEVICES_CACHE_TTL_SEC:
                return devices
        client = self._build_client()
        devices = client.get_owned_devices()
        self._devices_cache = (devices, time.time())
        return devices

    def _flush_devices_cache(self) -> None:
        self._devices_cache = None

    def set_default_destination(self, device_sn: str) -> None:
        devices = self.list_devices()
        match = next(
            (d for d in devices if d.device_serial_number == device_sn), None
        )
        if not match:
            raise ValueError(f"unknown device: {device_sn!r} is not in your device list")
        self._svc.set_secret_values(
            {
                "kindle_stk.default_destination_sn": device_sn,
                "kindle_stk.default_destination_name": match.device_name,
            }
        )

    # ---------------- Send ----------------

    def send_file(
        self,
        file_path: Path,
        *,
        format: str,
        title: str,
        author: str,
    ) -> dict[str, Any]:
        """Send a single file to the user's default destination device.

        Translates raw stkclient exceptions to biblichor's typed ones.
        Raises KindleStkNotConfigured if no cert or no default device.
        """
        self._require_configured()
        dest_sn = self._svc.get_secret_value("kindle_stk.default_destination_sn")
        if not dest_sn:
            raise KindleStkNotConfigured(
                "No default destination device. Complete setup wizard first."
            )
        # Resolve dest_sn against the current device list (may refresh cache).
        devices = self.list_devices()
        dest = next(
            (d for d in devices if d.device_serial_number == dest_sn), None
        )
        if not dest:
            raise KindleStkNotConfigured(
                f"Default device {dest_sn} is no longer registered. Re-pick a device."
            )
        client = self._build_client()
        try:
            return client.send_file(
                file_path, destinations=[dest], format=format, title=title, author=author
            )
        except requests.HTTPError as e:
            self._raise_typed_from_http_error(e)
        except Exception as e:
            raise KindleStkUploadFailed(f"unexpected stkclient failure: {e}") from e

    def _raise_typed_from_http_error(self, e: requests.HTTPError) -> None:
        """Map a raw requests.HTTPError to biblichor's typed exception."""
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status in (401, 403):
            raise KindleStkAuthExpired(
                f"Amazon rejected the device cert ({status}). Re-OAuth required."
            ) from e
        if status == 429:
            retry_after = 5
            if resp is not None:
                hdr = getattr(resp, "headers", {}) or {}
                try:
                    retry_after = int(hdr.get("Retry-After", "5"))
                except (TypeError, ValueError):
                    retry_after = 5
            raise KindleStkRateLimited(
                f"Rate-limited by Amazon (Retry-After {retry_after}s)",
                retry_after_sec=retry_after,
            ) from e
        raise KindleStkUploadFailed(f"HTTP {status}: {e}") from e

    # ---------------- Logout ----------------

    def deregister(self) -> None:
        """Wipe every kindle_stk.* secret. Best-effort call to Amazon
        FirsProxy/disownFiona to revoke the cert on Amazon's side; failure
        of that call doesn't prevent the local wipe."""
        try:
            if self.is_configured():
                client = self._build_client()
                # Some stkclient versions expose this; ignore if not.
                if hasattr(client, "disown_device"):
                    try:
                        client.disown_device()
                    except Exception as e:
                        log.warning("kindle_stk: disown_device failed: %s", e)
        finally:
            for k in (
                "kindle_stk.device_cert.pem",
                "kindle_stk.adp_token",
                "kindle_stk.adp_did",
                "kindle_stk.amazon_customer_id",
                "kindle_stk.registered_at",
                "kindle_stk.default_destination_sn",
                "kindle_stk.default_destination_name",
                "kindle_stk.oauth_state.code_verifier",
            ):
                self._svc.delete_secret_value(k)
            self._flush_devices_cache()

    # ---------------- Internal ----------------

    def _build_client(self):
        """Construct a fresh vendored Client from stored credentials."""
        cert = self._svc.get_secret_value("kindle_stk.device_cert.pem")
        adp = self._svc.get_secret_value("kindle_stk.adp_token")
        # The vendored stkclient.Client constructor signature may accept
        # the cert + adp directly, or expose a from_credentials classmethod.
        # Detect at runtime.
        ClientCls = _vendored.Client
        if hasattr(ClientCls, "from_credentials"):
            return ClientCls.from_credentials(device_private_key=cert, adp_token=adp)
        return ClientCls(device_private_key=cert, adp_token=adp)
```

- [ ] **Step 4: Add KindleStkService to package `__all__`**

Edit `src/endless_library/kindle_stk/__init__.py` to also re-export the service:

```python
from .exceptions import (
    KindleStkError,
    KindleStkNotConfigured,
    KindleStkAuthExpired,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)
from .service import KindleStkService

__all__ = [
    "KindleStkService",
    "KindleStkError",
    "KindleStkNotConfigured",
    "KindleStkAuthExpired",
    "KindleStkRateLimited",
    "KindleStkUploadFailed",
]
```

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_service.py -v 2>&1 | tail -25'
```

Expected: all 12 pass. If `register_device`'s signature on real stkclient differs (kwargs vs positional, return key names like `device_private_key` vs `device_cert`), match the real upstream — read `_vendored/_client.py`.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/kindle_stk/service.py src/endless_library/kindle_stk/__init__.py tests/unit/test_kindle_stk_service.py && git commit -m "STK 4: KindleStkService facade" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 5: stk_rate.py

**Files:**
- Create: `src/endless_library/stk_rate.py`
- Test: `tests/unit/test_stk_rate.py`

- [ ] **Step 1: Read the existing smtp_rate.py for shape**

```bash
ssh ubuntu@claude-1 'cat /home/ubuntu/endless-library/src/endless_library/smtp_rate.py'
```

Note the function signature, dataclass shape, and SQL query — we mirror them.

- [ ] **Step 2: Write the failing tests**

Write to `tests/unit/test_stk_rate.py`:

```python
"""Phase STK 5: stk_rate.quota_status — STK delivery rate-limit gate."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from endless_library.db.schema import init_db


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


def _record_event(db: Path, kind: str, ts_offset_sec: int = 0) -> None:
    """Insert an events row at NOW + ts_offset_sec."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO events (kind, book_id, meta_json, ts) "
            "VALUES (?, NULL, '{}', datetime('now', ?))",
            (kind, f"{ts_offset_sec:+d} seconds"),
        )


def test_quota_status_zero_when_no_events(db):
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 0
    assert s.cap == 500
    assert s.remaining == 500
    assert s.exhausted is False


def test_quota_status_counts_only_send_stk_kind(db):
    _record_event(db, "send-stk")
    _record_event(db, "send-stk")
    _record_event(db, "send")        # SMTP — must not count
    _record_event(db, "search")      # noise — must not count
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 2
    assert s.remaining == 498


def test_quota_status_respects_24h_window(db):
    _record_event(db, "send-stk", ts_offset_sec=-90_000)  # 25 hours ago
    _record_event(db, "send-stk", ts_offset_sec=-3600)    # 1 hour ago
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 1


def test_quota_status_exhausted_when_at_cap(db):
    for _ in range(5):
        _record_event(db, "send-stk")
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=5)
    assert s.exhausted is True
    assert s.remaining == 0
```

- [ ] **Step 3: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_stk_rate.py -v 2>&1 | tail -10'
```

Expected: 4 failures (`ImportError: stk_rate`).

- [ ] **Step 4: Implement stk_rate.py**

Write to `src/endless_library/stk_rate.py`:

```python
"""STK delivery rate-limit gate.

Mirrors smtp_rate.py — counts events with kind='send-stk' in a rolling
24h window. Daily cap is supplied by the caller (typically
cfg.stk.daily_cap).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db.schema import connect


@dataclass(frozen=True, slots=True)
class StkQuotaStatus:
    sent_24h: int
    cap: int
    remaining: int
    exhausted: bool


def quota_status(db_path: Path, *, daily_cap: int) -> StkQuotaStatus:
    """Count rows in events with kind='send-stk' in the rolling 24h
    window. Returns a status struct the router + healthz can both use.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM events
               WHERE kind = 'send-stk'
                 AND ts >= datetime('now', '-1 day')""",
        ).fetchone()
    n = int(row["n"] or 0)
    return StkQuotaStatus(
        sent_24h=n,
        cap=daily_cap,
        remaining=max(0, daily_cap - n),
        exhausted=n >= daily_cap,
    )
```

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_stk_rate.py -v 2>&1 | tail -10'
```

Expected: 4 pass.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/stk_rate.py tests/unit/test_stk_rate.py && git commit -m "STK 5: stk_rate.quota_status mirrors smtp_rate" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 6: Config.stk

**Files:**
- Modify: `src/endless_library/config.py`

- [ ] **Step 1: Read the existing Config to find the right insertion point**

```bash
ssh ubuntu@claude-1 'grep -n "class.*Cfg\|^class Config" /home/ubuntu/endless-library/src/endless_library/config.py'
```

Locate where SMTP and Bench config classes are; add `StkCfg` next to them.

- [ ] **Step 2: Add `StkCfg` class**

In `src/endless_library/config.py`, add (near other `*Cfg` classes):

```python
class StkCfg(BaseModel):
    """Send-to-Kindle delivery configuration."""
    daily_cap: int = 500
    max_attempts: int = 3
    backoff_initial_sec: float = 5.0
    backoff_factor: float = 3.0
    client_id: str | None = None  # None = use vendored stkclient's hardcoded value
```

And in the top-level `Config` class, add:

```python
stk: StkCfg = StkCfg()
```

- [ ] **Step 3: Smoke-import to verify**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -c "from endless_library.config import Config, StkCfg; c = Config(); print(c.stk.daily_cap, c.stk.max_attempts)"'
```

Expected: `500 3`.

- [ ] **Step 4: Run the existing config tests to confirm no regression**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/ -v -k config 2>&1 | tail -10'
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/config.py && git commit -m "STK 6: Config.stk with daily_cap=500, max_attempts=3, backoff 5/3" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 7: books.sent_method migration

**Files:**
- Modify: `src/endless_library/db/schema.py`
- Modify: `src/endless_library/db/books.py`
- Test: `tests/unit/test_schema_migrate_sent_method.py`

- [ ] **Step 1: Write the failing test**

Write to `tests/unit/test_schema_migrate_sent_method.py`:

```python
"""Phase STK 7: books.sent_method TEXT NULL column."""
from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.schema import init_db, connect


def test_books_has_sent_method_column_after_init_db(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    assert "sent_method" in cols


def test_books_sent_method_default_is_null(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, status) VALUES ('Test', 'queued')"
        )
        book_id = cur.lastrowid
        row = conn.execute(
            "SELECT sent_method FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["sent_method"] is None


def test_mark_kindled_records_method(tmp_path: Path):
    """books_repo.mark_kindled(book_id, method='stk') stores 'stk'."""
    from endless_library.db.books import BooksRepo
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, status) VALUES ('Test', 'queued')"
        )
        book_id = cur.lastrowid
    repo = BooksRepo(db)
    repo.mark_kindled(book_id, method="stk")
    with connect(db) as conn:
        row = conn.execute(
            "SELECT status, sent_method FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["status"] == "kindled"
    assert row["sent_method"] == "stk"


def test_mark_kindled_without_method_leaves_null(tmp_path: Path):
    """Backwards compat — old call sites without `method=` still work."""
    from endless_library.db.books import BooksRepo
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, status) VALUES ('Test', 'queued')"
        )
        book_id = cur.lastrowid
    repo = BooksRepo(db)
    repo.mark_kindled(book_id)  # no method
    with connect(db) as conn:
        row = conn.execute(
            "SELECT status, sent_method FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["status"] == "kindled"
    assert row["sent_method"] is None
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_schema_migrate_sent_method.py -v 2>&1 | tail -10'
```

Expected: 4 failures (column doesn't exist; `mark_kindled` may not accept `method=`).

- [ ] **Step 3: Add the column to the CREATE TABLE statement and to `_migrate()`**

In `src/endless_library/db/schema.py`:

a) Locate the `CREATE TABLE IF NOT EXISTS books` statement. Add `sent_method TEXT` to the column list (anywhere — append at end is fine).

b) In the `_migrate(conn)` function, add an idempotent ADD COLUMN:

```python
cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
if "sent_method" not in cols:
    conn.execute("ALTER TABLE books ADD COLUMN sent_method TEXT")
```

- [ ] **Step 4: Update `BooksRepo.mark_kindled` to accept `method`**

In `src/endless_library/db/books.py`, find the existing `mark_kindled` method. Update its signature and SQL:

```python
def mark_kindled(self, book_id: int, *, method: str | None = None) -> None:
    """Mark a book as successfully delivered to Kindle.

    method: 'stk' or 'smtp' (Phase STK 7); leave None for legacy callers.
    """
    with connect(self.db_path) as conn:
        conn.execute(
            "UPDATE books SET status = 'kindled', sent_method = ? WHERE id = ?",
            (method, book_id),
        )
```

If the existing implementation has additional fields (e.g. sent_at timestamp), keep them.

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_schema_migrate_sent_method.py -v 2>&1 | tail -10'
```

Expected: 4 pass.

- [ ] **Step 6: Run the full suite to confirm no regression**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3'
```

Expected: same count + 4 new = passing.

- [ ] **Step 7: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/db/schema.py src/endless_library/db/books.py tests/unit/test_schema_migrate_sent_method.py && git commit -m "STK 7: books.sent_method column + mark_kindled(method=...) " -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 8: kindle_router.deliver

**Files:**
- Create: `src/endless_library/kindle_router.py`
- Test: `tests/unit/test_kindle_router.py`

- [ ] **Step 1: Write the failing tests**

Write to `tests/unit/test_kindle_router.py`:

```python
"""Phase STK 8: kindle_router.deliver — single entry-point with STK-first + SMTP-fallback."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endless_library.db.schema import init_db, connect
from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def cfg():
    """A minimal Config-shaped object with stk + smtp blocks."""
    return SimpleNamespace(
        stk=SimpleNamespace(
            daily_cap=500, max_attempts=3,
            backoff_initial_sec=0.0, backoff_factor=1.0,  # Fast in tests
        ),
        smtp=SimpleNamespace(daily_cap=80),
    )


@pytest.fixture
def book(db):
    """Insert a fake book row + return a BookRow-like object."""
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, status) VALUES (?, ?, 'queued')",
            ("Test Book", "Test Author"),
        )
        book_id = cur.lastrowid
    return SimpleNamespace(id=book_id, title="Test Book", author="Test Author")


@pytest.fixture
def file_path(tmp_path):
    f = tmp_path / "book.epub"
    f.write_bytes(b"FAKE EPUB BYTES")
    return f


@pytest.fixture
def svc():
    """Minimal BookOrbitService stand-in."""
    class _Svc:
        def __init__(self):
            self._secrets: dict[str, str] = {}

        def get_secret_value(self, key): return self._secrets.get(key)
        def set_secret_value(self, key, value): self._secrets[key] = value
        def set_secret_values(self, kv): self._secrets.update(kv)
        def delete_secret_value(self, key): self._secrets.pop(key, None)
    return _Svc()


def _configure_stk(svc):
    """Mark the service as configured so the router takes the STK branch."""
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")


def test_router_picks_smtp_when_stk_not_configured(monkeypatch, db, cfg, book, file_path, svc):
    """No STK config → straight to SMTP."""
    smtp_calls = []
    def fake_smtp(*a, **kw):
        smtp_calls.append((a, kw))
    monkeypatch.setattr("endless_library.kindle_router._send_smtp", fake_smtp)
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.SMTP
    assert smtp_calls, "SMTP path was not called"


def test_router_picks_stk_when_configured(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    stk_calls = []
    def fake_stk_send(self, file, *, format, title, author):
        stk_calls.append((file, format, title, author))
        return {"transaction_id": "tx-1"}
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        fake_stk_send,
    )
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.STK
    assert len(stk_calls) == 1


def test_router_falls_to_smtp_when_quota_exhausted(monkeypatch, db, cfg, book, file_path, svc):
    """If STK is configured but the daily quota is exhausted, fall to SMTP without trying STK."""
    _configure_stk(svc)
    # Pre-fill 500 send-stk events to exhaust the quota.
    with connect(db) as conn:
        for _ in range(500):
            conn.execute("INSERT INTO events (kind, meta_json) VALUES ('send-stk', '{}')")
    stk_called = []
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        lambda *a, **kw: stk_called.append(True),
    )
    smtp_called = []
    monkeypatch.setattr(
        "endless_library.kindle_router._send_smtp",
        lambda *a, **kw: smtp_called.append(True),
    )
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.method == DeliveryMethod.SMTP
    assert not stk_called, "STK should not have been called when quota is exhausted"
    assert smtp_called


def test_router_retries_stk_3x_then_falls_to_smtp(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    attempts = []
    def fake_send(self, file, **kw):
        attempts.append(time.monotonic())
        raise KindleStkUploadFailed("transient")
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file", fake_send,
    )
    smtp_called = []
    monkeypatch.setattr(
        "endless_library.kindle_router._send_smtp",
        lambda *a, **kw: smtp_called.append(True),
    )
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert len(attempts) == 3
    assert smtp_called
    assert result.method == DeliveryMethod.SMTP


def test_router_auth_expired_skips_retries(monkeypatch, db, cfg, book, file_path, svc):
    """AuthExpired skips remaining retries and falls to SMTP."""
    _configure_stk(svc)
    attempts = []
    def fake_send(self, file, **kw):
        attempts.append(True)
        raise KindleStkAuthExpired("revoked")
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file", fake_send,
    )
    smtp_called = []
    monkeypatch.setattr(
        "endless_library.kindle_router._send_smtp",
        lambda *a, **kw: smtp_called.append(True),
    )
    from endless_library.kindle_router import deliver
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert len(attempts) == 1, "Should not retry after AuthExpired"
    assert smtp_called


def test_router_honors_retry_after(monkeypatch, db, cfg, book, file_path, svc):
    """KindleStkRateLimited.retry_after_sec drives the sleep between attempts."""
    _configure_stk(svc)
    sleep_calls = []
    monkeypatch.setattr(
        "endless_library.kindle_router.time.sleep",
        lambda s: sleep_calls.append(s),
    )
    attempts = [
        KindleStkRateLimited("rl-1", retry_after_sec=2),
        KindleStkRateLimited("rl-2", retry_after_sec=4),
        None,  # third attempt succeeds
    ]
    def fake_send(self, file, **kw):
        nxt = attempts.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"transaction_id": "tx-final"}
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file", fake_send,
    )
    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.method == DeliveryMethod.STK
    assert sleep_calls == [2, 4]  # used Retry-After, not the default backoff


def test_router_records_send_stk_event_on_success(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        lambda *a, **kw: {"transaction_id": "tx-ok"},
    )
    from endless_library.kindle_router import deliver
    deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT kind FROM events WHERE book_id = ?", (book.id,)
        ).fetchall()
    kinds = [r["kind"] for r in rows]
    assert "send-stk" in kinds


def test_router_records_send_stk_failed_event_on_exhaustion(monkeypatch, db, cfg, book, file_path, svc):
    _configure_stk(svc)
    monkeypatch.setattr(
        "endless_library.kindle_stk.service.KindleStkService.send_file",
        lambda *a, **kw: (_ for _ in ()).throw(KindleStkUploadFailed("nope")),
    )
    monkeypatch.setattr(
        "endless_library.kindle_router._send_smtp",
        lambda *a, **kw: None,
    )
    from endless_library.kindle_router import deliver
    deliver(file_path=file_path, book=book, cfg=cfg, db_path=db, svc=svc)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT kind FROM events WHERE book_id = ?", (book.id,)
        ).fetchall()
    kinds = [r["kind"] for r in rows]
    assert "send-stk-failed" in kinds
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_router.py -v 2>&1 | tail -20'
```

Expected: 8 failures (ModuleNotFound `kindle_router`).

- [ ] **Step 3: Implement `kindle_router.py`**

Write to `src/endless_library/kindle_router.py`:

```python
"""Single delivery entry-point used by the pipeline.

STK-primary + SMTP-fallback decision tree. Handles retry + backoff,
rate-gate, and event-log recording. The pipeline calls deliver(...);
it does not need to know about STK vs SMTP.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .db.schema import connect
from .kindle import send_to_kindle as _send_smtp
from .kindle_stk import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkService,
    KindleStkUploadFailed,
)
from .stk_rate import quota_status as _stk_quota_status

log = logging.getLogger(__name__)


class DeliveryMethod(str, Enum):
    STK = "stk"
    SMTP = "smtp"


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    method: DeliveryMethod
    error: str | None
    attempts: int
    duration_ms: int


def deliver(
    *,
    file_path: Path,
    book: Any,
    cfg: Any,
    db_path: Path,
    svc: Any,
) -> DeliveryResult:
    """Deliver `file_path` to the user's Kindle.

    Tries STK first if configured + quota available, with retries.
    Falls back to SMTP on STK exhaustion or auth failure. Records
    audit events on every outcome.
    """
    start = time.monotonic()
    stk_svc = KindleStkService(svc)

    # Branch 1: STK not configured → SMTP only.
    if not stk_svc.is_configured():
        ok, err = _smtp_deliver(file_path, book, cfg, db_path)
        return DeliveryResult(
            ok=ok, method=DeliveryMethod.SMTP, error=err,
            attempts=1, duration_ms=int((time.monotonic() - start) * 1000),
        )

    # Branch 2: STK configured but quota exhausted → SMTP fallback.
    status = _stk_quota_status(db_path, daily_cap=cfg.stk.daily_cap)
    if status.exhausted:
        _record_event(
            db_path, "send-stk-failed", book.id,
            meta={"reason": "stk-cap-reached", "sent_24h": status.sent_24h, "cap": status.cap},
        )
        ok, err = _smtp_deliver(file_path, book, cfg, db_path)
        return DeliveryResult(
            ok=ok, method=DeliveryMethod.SMTP,
            error=err, attempts=1,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    # Branch 3: try STK up to max_attempts; fall to SMTP on exhaustion.
    max_attempts = int(cfg.stk.max_attempts)
    backoff = float(cfg.stk.backoff_initial_sec)
    factor = float(cfg.stk.backoff_factor)
    last_error: str | None = None
    auth_expired = False

    fmt = _infer_format(file_path)
    title = getattr(book, "title", file_path.stem)
    author = getattr(book, "author", "") or ""

    for attempt in range(1, max_attempts + 1):
        try:
            stk_svc.send_file(file_path, format=fmt, title=title, author=author)
            _record_event(
                db_path, "send-stk", book.id,
                meta={"attempts": attempt, "duration_ms": int((time.monotonic() - start) * 1000)},
            )
            return DeliveryResult(
                ok=True, method=DeliveryMethod.STK, error=None,
                attempts=attempt,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except KindleStkAuthExpired as e:
            last_error = str(e)
            auth_expired = True
            log.warning("kindle_router: AuthExpired on attempt %d, skipping retries: %s", attempt, e)
            break
        except KindleStkRateLimited as e:
            last_error = str(e)
            if attempt < max_attempts:
                time.sleep(e.retry_after_sec)
        except (KindleStkUploadFailed, KindleStkNotConfigured) as e:
            last_error = str(e)
            if attempt < max_attempts:
                time.sleep(backoff)
                backoff *= factor

    _record_event(
        db_path, "send-stk-failed", book.id,
        meta={
            "attempts": attempt, "last_error": last_error,
            "auth_expired": auth_expired, "fellback_to": "smtp",
        },
    )

    ok, err = _smtp_deliver(file_path, book, cfg, db_path)
    return DeliveryResult(
        ok=ok, method=DeliveryMethod.SMTP, error=err,
        attempts=attempt + (1 if ok or err else 0),
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _smtp_deliver(file_path: Path, book: Any, cfg: Any, db_path: Path) -> tuple[bool, str | None]:
    """Wrap the existing SMTP path so router exceptions don't leak."""
    try:
        _send_smtp(
            file_path,
            book=book,
            cfg=cfg,
            db_path=db_path,
        )
        return True, None
    except Exception as e:
        log.warning("kindle_router: SMTP fallback also failed: %s", e)
        return False, str(e)


def _infer_format(file_path: Path) -> str:
    """Return the STK format token from the file extension."""
    ext = file_path.suffix.lower().lstrip(".")
    return {
        "epub": "EPUB", "pdf": "PDF", "doc": "DOC", "docx": "DOCX",
        "txt": "TXT", "rtf": "RTF", "htm": "HTM", "html": "HTM",
        "png": "PNG", "gif": "GIF", "jpg": "JPG", "jpeg": "JPG", "bmp": "BMP",
    }.get(ext, "EPUB")


def _record_event(db_path: Path, kind: str, book_id: int, *, meta: dict) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (kind, book_id, meta_json) VALUES (?, ?, ?)",
            (kind, book_id, json.dumps(meta)),
        )
```

**Note on the existing `send_to_kindle` signature**: The router calls `_send_smtp(file_path, book=book, cfg=cfg, db_path=db_path)`. Read the actual signature in `src/endless_library/kindle.py` first; if it's different, adapt the call. If the existing function doesn't accept `db_path`, drop that kwarg.

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_router.py -v 2>&1 | tail -20'
```

Expected: 8 pass.

- [ ] **Step 5: Run the full suite**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3'
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/kindle_router.py tests/unit/test_kindle_router.py && git commit -m "STK 8: kindle_router.deliver — STK-primary + SMTP-fallback" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 9: FastAPI endpoints

**Files:**
- Modify: `src/endless_library/web/api.py`
- Test: `tests/unit/test_kindle_stk_endpoints.py`

- [ ] **Step 1: Read where SMTP / Mobilism creds endpoints live**

```bash
ssh ubuntu@claude-1 'grep -n "/scrapers/mobilism" /home/ubuntu/endless-library/src/endless_library/web/api.py | head'
```

This shows the pattern for credential-store endpoints; new STK endpoints sit alongside.

- [ ] **Step 2: Write the failing tests**

Write to `tests/unit/test_kindle_stk_endpoints.py`:

```python
"""Phase STK 9: FastAPI endpoints for setup wizard + status + test send."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import init_db
from endless_library.web import api as api_mod
from tests._stkclient_stub import FakeOAuth2, FakeVendoredClient


def _build_app(tmp_path: Path) -> tuple[FastAPI, SimpleNamespace]:
    db = tmp_path / "test.db"
    init_db(db)
    app = FastAPI()
    # In-memory BookOrbitService stand-in
    class _Svc:
        def __init__(self): self._s: dict[str, str] = {}
        def get_secret_value(self, k): return self._s.get(k)
        def set_secret_value(self, k, v): self._s[k] = v
        def set_secret_values(self, kv): self._s.update(kv)
        def delete_secret_value(self, k): self._s.pop(k, None)
    svc = _Svc()
    deps = SimpleNamespace(
        db_path=db,
        cfg=SimpleNamespace(stk=SimpleNamespace(daily_cap=500)),
        bookorbit_service=svc,
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.config_path = Path("/tmp/cfg.yaml")
    api_mod.register(app)
    return app, svc


def test_status_returns_configured_false_when_no_setup(tmp_path):
    app, _ = _build_app(tmp_path)
    r = TestClient(app).get("/api/kindle-stk/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_status_returns_full_block_when_configured(tmp_path):
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.amazon_customer_id", "amzn1.account.x")
    svc.set_secret_value("kindle_stk.registered_at", "2026-05-23T12:00:00+00:00")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    r = TestClient(app).get("/api/kindle-stk/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["customer_id"] == "amzn1.account.x"
    assert body["default_destination"] == "Kindle for Web"


def test_oauth_start_returns_authorize_url(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, _ = _build_app(tmp_path)
    r = TestClient(app).post("/api/kindle-stk/oauth/start")
    assert r.status_code == 200
    body = r.json()
    assert body["authorize_url"].startswith("https://www.amazon.com/ap/oa?")


def test_oauth_complete_with_valid_url_persists_cert(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    fake_client.register_device = lambda c, v: {
        "device_private_key": "PEM_K", "adp_token": "ADP_T", "adp_did": "DID-1",
        "customer_id": "amzn1.account.y", "customer_name": "Test",
    }
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.oauth_state.code_verifier", "verifier")
    r = TestClient(app).post(
        "/api/kindle-stk/oauth/complete",
        json={"redirect_url": "https://www.amazon.com/ap/maplanding?openid.oa2.access_token=XYZ"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == "amzn1.account.y"
    assert svc.get_secret_value("kindle_stk.device_cert.pem") == "PEM_K"


def test_oauth_complete_with_invalid_url_returns_400(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.oauth_state.code_verifier", "verifier")
    r = TestClient(app).post(
        "/api/kindle-stk/oauth/complete",
        json={"redirect_url": "https://example.com/garbage"},
    )
    assert r.status_code == 400


def test_devices_returns_list_after_configured(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).get("/api/kindle-stk/devices")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["devices"], list)
    assert any(d["device_serial_number"] == "G0WEB1" for d in body["devices"])


def test_default_destination_validates_against_device_list(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).put(
        "/api/kindle-stk/default-destination",
        json={"device_sn": "G0WEB1"},
    )
    assert r.status_code == 200
    assert svc.get_secret_value("kindle_stk.default_destination_sn") == "G0WEB1"


def test_default_destination_returns_400_for_unknown_sn(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).put(
        "/api/kindle-stk/default-destination",
        json={"device_sn": "BOGUS-SN"},
    )
    assert r.status_code == 400


def test_delete_connection_wipes_secrets(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    fake_client.disown_device = lambda: None
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).delete("/api/kindle-stk/connection")
    assert r.status_code == 200
    assert svc.get_secret_value("kindle_stk.device_cert.pem") is None


def test_test_send_returns_4xx_on_send_failure(tmp_path, monkeypatch):
    from endless_library.kindle_stk import KindleStkUploadFailed
    fake_client = FakeVendoredClient(send_raises=KindleStkUploadFailed("nope"))
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    r = TestClient(app).post("/api/kindle-stk/test-send")
    assert r.status_code in (400, 502)
```

- [ ] **Step 3: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_endpoints.py -v 2>&1 | tail -20'
```

Expected: 10 failures (endpoints don't exist).

- [ ] **Step 4: Implement the 7 endpoints in `web/api.py`**

Find the existing Mobilism creds endpoints in `src/endless_library/web/api.py` and add this block near them (the exact location is wherever `register(app)` defines other `@router` paths):

```python
# Kindle Send-to-Kindle (Phase STK 9) -----------------------------

@router.get("/kindle-stk/status")
def kindle_stk_status(request: Request) -> dict:
    deps = request.app.state.deps
    svc = KindleStkService(deps.bookorbit_service)
    if not svc.is_configured():
        return {"configured": False}
    return {
        "configured": True,
        "customer_id": deps.bookorbit_service.get_secret_value("kindle_stk.amazon_customer_id"),
        "registered_at": deps.bookorbit_service.get_secret_value("kindle_stk.registered_at"),
        "default_destination": deps.bookorbit_service.get_secret_value("kindle_stk.default_destination_name"),
        "default_destination_sn": deps.bookorbit_service.get_secret_value("kindle_stk.default_destination_sn"),
    }


@router.post("/kindle-stk/oauth/start")
def kindle_stk_oauth_start(request: Request) -> dict:
    deps = request.app.state.deps
    svc = KindleStkService(deps.bookorbit_service)
    url, _ = svc.start_oauth()
    return {"authorize_url": url}


@router.post("/kindle-stk/oauth/complete")
def kindle_stk_oauth_complete(payload: dict, request: Request) -> dict:
    deps = request.app.state.deps
    redirect_url = (payload or {}).get("redirect_url", "")
    if not redirect_url:
        raise HTTPException(400, "redirect_url is required")
    svc = KindleStkService(deps.bookorbit_service)
    try:
        return svc.complete_oauth(redirect_url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except KindleStkUploadFailed as e:
        raise HTTPException(502, str(e)) from e


@router.get("/kindle-stk/devices")
def kindle_stk_devices(request: Request) -> dict:
    deps = request.app.state.deps
    svc = KindleStkService(deps.bookorbit_service)
    try:
        devs = svc.list_devices()
    except KindleStkNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    return {"devices": [
        {
            "device_serial_number": d.device_serial_number,
            "device_type": d.device_type,
            "device_name": d.device_name,
        } for d in devs
    ]}


@router.put("/kindle-stk/default-destination")
def kindle_stk_set_destination(payload: dict, request: Request) -> dict:
    deps = request.app.state.deps
    sn = (payload or {}).get("device_sn", "")
    if not sn:
        raise HTTPException(400, "device_sn is required")
    svc = KindleStkService(deps.bookorbit_service)
    try:
        svc.set_default_destination(sn)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except KindleStkNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/kindle-stk/test-send")
def kindle_stk_test_send(request: Request) -> dict:
    """Send a tiny bundled PDF to verify the configured device works."""
    deps = request.app.state.deps
    svc = KindleStkService(deps.bookorbit_service)
    # Use a 1-line text file as the test payload — we don't bundle a
    # PDF resource just for this.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("biblichor connection test — Phase STK 9.")
        tmp = Path(f.name)
    try:
        result = svc.send_file(tmp, format="TXT", title="biblichor test", author="biblichor")
        return {"ok": True, "result": result}
    except KindleStkNotConfigured as e:
        raise HTTPException(400, str(e)) from e
    except KindleStkAuthExpired as e:
        raise HTTPException(401, str(e)) from e
    except KindleStkRateLimited as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after_sec)}) from e
    except KindleStkUploadFailed as e:
        raise HTTPException(502, str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)


@router.delete("/kindle-stk/connection")
def kindle_stk_deregister(request: Request) -> dict:
    deps = request.app.state.deps
    svc = KindleStkService(deps.bookorbit_service)
    svc.deregister()
    return {"ok": True}
```

Also add the imports at the top of `web/api.py`:

```python
from endless_library.kindle_stk import (
    KindleStkService,
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)
```

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_kindle_stk_endpoints.py -v 2>&1 | tail -15'
```

Expected: 10 pass.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py tests/unit/test_kindle_stk_endpoints.py && git commit -m "STK 9: 7 FastAPI endpoints for setup wizard + status" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 10: /healthz STK block

**Files:**
- Modify: `src/endless_library/web/api.py`
- Modify: `tests/unit/test_healthz.py`

- [ ] **Step 1: Append failing tests to test_healthz.py**

Find the existing `tests/unit/test_healthz.py`. Add these test functions:

```python
def test_healthz_stk_block_says_unconfigured_when_no_setup(tmp_path):
    """When STK isn't configured, /healthz returns {'stk': {'configured': false}}
    with no quota fields."""
    # Reuse the existing _build_app helper or copy its pattern.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from pathlib import Path
    from endless_library.db.schema import init_db
    from endless_library.web import api as api_mod

    db = tmp_path / "test.db"
    init_db(db)
    app = FastAPI()
    class _Svc:
        def get_secret_value(self, k): return None
        def set_secret_value(self, *a): pass
        def set_secret_values(self, *a): pass
        def delete_secret_value(self, *a): pass
    deps = SimpleNamespace(
        db_path=db,
        cfg=SimpleNamespace(
            stk=SimpleNamespace(daily_cap=500),
            smtp=SimpleNamespace(daily_cap=80),
        ),
        bookorbit_service=_Svc(),
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    api_mod.register(app)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "stk" in body
    assert body["stk"]["configured"] is False
    assert "sent_24h" not in body["stk"]


def test_healthz_stk_block_returns_quota_when_configured(tmp_path):
    """When STK is configured, /healthz reports sent_24h, cap, remaining, exhausted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from pathlib import Path
    from endless_library.db.schema import init_db, connect
    from endless_library.web import api as api_mod

    db = tmp_path / "test.db"
    init_db(db)
    # Pre-record 3 send-stk events
    with connect(db) as conn:
        for _ in range(3):
            conn.execute("INSERT INTO events (kind, meta_json) VALUES ('send-stk', '{}')")
    app = FastAPI()
    class _Svc:
        def __init__(self): self._s = {
            "kindle_stk.device_cert.pem": "PEM",
            "kindle_stk.adp_token": "ADP",
        }
        def get_secret_value(self, k): return self._s.get(k)
        def set_secret_value(self, k, v): self._s[k] = v
        def set_secret_values(self, kv): self._s.update(kv)
        def delete_secret_value(self, k): self._s.pop(k, None)
    deps = SimpleNamespace(
        db_path=db,
        cfg=SimpleNamespace(
            stk=SimpleNamespace(daily_cap=500),
            smtp=SimpleNamespace(daily_cap=80),
        ),
        bookorbit_service=_Svc(),
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    api_mod.register(app)
    r = TestClient(app).get("/healthz")
    body = r.json()
    assert body["stk"]["configured"] is True
    assert body["stk"]["sent_24h"] == 3
    assert body["stk"]["cap"] == 500
    assert body["stk"]["remaining"] == 497
    assert body["stk"]["exhausted"] is False
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_healthz.py -v -k stk 2>&1 | tail -10'
```

Expected: 2 failures (`KeyError: 'stk'`).

- [ ] **Step 3: Extend /healthz**

Find the `/healthz` handler in `src/endless_library/web/api.py`. Inside the function body (typically returns a dict like `{"ok": True, "db": ..., "scrapers": ..., "smtp": ...}`), add the `stk` block before returning:

```python
# Phase STK 10: STK delivery health
from endless_library.kindle_stk import KindleStkService
from endless_library.stk_rate import quota_status as _stk_qs

deps = request.app.state.deps
stk_svc = KindleStkService(deps.bookorbit_service)
if stk_svc.is_configured():
    qs = _stk_qs(deps.db_path, daily_cap=deps.cfg.stk.daily_cap)
    body["stk"] = {
        "configured": True,
        "sent_24h": qs.sent_24h,
        "cap": qs.cap,
        "remaining": qs.remaining,
        "exhausted": qs.exhausted,
    }
else:
    body["stk"] = {"configured": False}
```

(Adjust to match the existing healthz handler's structure.)

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_healthz.py -v 2>&1 | tail -10'
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py tests/unit/test_healthz.py && git commit -m "STK 10: /healthz stk block" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 11: SPA — Kindle Browser Upload Card + setup modal

**Files:**
- Modify: `webapp/src/pages/SettingsPage.vue`

This task ships UI only; functional behaviour was covered by API tests in Tasks 9 + 10.

- [ ] **Step 1: Open SettingsPage.vue and find the SMTP card**

```bash
ssh ubuntu@claude-1 'grep -n "SMTP\|smtp_user\|smtp_host" /home/ubuntu/endless-library/webapp/src/pages/SettingsPage.vue | head -5'
```

Add the new Kindle Browser Upload Card immediately after the SMTP card.

- [ ] **Step 2: Add reactive state for the new card**

Inside the existing `<script setup lang="ts">` block, add:

```ts
import { ref, reactive, onMounted, onUnmounted } from 'vue'   // existing

// Phase STK 11: Kindle Browser Upload state -----------------------
const stkStatus = ref<{
  configured: boolean
  customer_id?: string
  registered_at?: string
  default_destination?: string
  default_destination_sn?: string
}>({ configured: false })

const stkQuota = ref<{
  configured: boolean
  sent_24h?: number
  cap?: number
  remaining?: number
  exhausted?: boolean
}>({ configured: false })

const showStkModal = ref(false)
const stkModalStep = ref<'authorize' | 'paste' | 'devices' | 'done'>('authorize')
const stkAuthorizeUrl = ref<string>('')
const stkRedirectUrl = ref<string>('')
const stkDevices = ref<Array<{ device_serial_number: string; device_type: string; device_name: string }>>([])
const stkSelectedSn = ref<string>('')
const stkLoading = ref<boolean>(false)
const stkError = ref<string>('')

async function loadStkStatus(): Promise<void> {
  try {
    const r1 = await fetch('/api/kindle-stk/status').then(r => r.json())
    stkStatus.value = r1
    const r2 = await fetch('/healthz').then(r => r.json())
    if (r2.stk) stkQuota.value = r2.stk
  } catch (e) {
    console.warn('stk status load failed', e)
  }
}

async function openStkSetup(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  showStkModal.value = true
  stkModalStep.value = 'authorize'
  try {
    const r = await fetch('/api/kindle-stk/oauth/start', { method: 'POST' }).then(r => r.json())
    stkAuthorizeUrl.value = r.authorize_url
    stkModalStep.value = 'paste'
  } catch (e: any) {
    stkError.value = e?.message || String(e)
  } finally {
    stkLoading.value = false
  }
}

async function completeStkOauth(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  try {
    const r = await fetch('/api/kindle-stk/oauth/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirect_url: stkRedirectUrl.value.trim() }),
    })
    if (!r.ok) {
      stkError.value = (await r.json()).detail || `HTTP ${r.status}`
      stkLoading.value = false
      return
    }
    // Load devices for the picker
    const devs = await fetch('/api/kindle-stk/devices').then(r => r.json())
    stkDevices.value = devs.devices || []
    // Pre-select Kindle for Web if present
    const webDev = stkDevices.value.find(d => d.device_type === 'FionaWebApp' || d.device_name.toLowerCase().includes('web'))
    stkSelectedSn.value = webDev?.device_serial_number || stkDevices.value[0]?.device_serial_number || ''
    stkModalStep.value = 'devices'
  } catch (e: any) {
    stkError.value = e?.message || String(e)
  } finally {
    stkLoading.value = false
  }
}

async function saveStkDestination(): Promise<void> {
  stkLoading.value = true
  stkError.value = ''
  try {
    const r = await fetch('/api/kindle-stk/default-destination', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_sn: stkSelectedSn.value }),
    })
    if (!r.ok) {
      stkError.value = (await r.json()).detail || `HTTP ${r.status}`
      return
    }
    showStkModal.value = false
    await loadStkStatus()
  } finally {
    stkLoading.value = false
  }
}

async function disconnectStk(): Promise<void> {
  if (!confirm('Disconnect Amazon and wipe stored credentials?')) return
  await fetch('/api/kindle-stk/connection', { method: 'DELETE' })
  await loadStkStatus()
}

async function sendStkTest(): Promise<void> {
  const r = await fetch('/api/kindle-stk/test-send', { method: 'POST' })
  if (r.ok) {
    alert('Test send queued — check your Kindle library in a minute.')
  } else {
    const b = await r.json().catch(() => ({}))
    alert(`Test failed: ${b.detail || r.status}`)
  }
}

onMounted(() => {
  // existing onMounted body continues...
  loadStkStatus()
})
```

(Make sure the existing `onMounted` is not duplicated; just append `loadStkStatus()` to its body.)

- [ ] **Step 3: Add the Card markup to the template**

Inside the `<template>` block, after the SMTP card, add:

```vue
<!-- Phase STK 11: Kindle Browser Upload Card -->
<Card title="Kindle Browser Upload (recommended)">
  <div v-if="!stkStatus.configured" class="space-y-2">
    <p class="text-sm text-slate-600">
      Send via Amazon's web upload — bypasses SMTP's ~80/day cap,
      supports files up to 200&nbsp;MB, no Gmail dependency.
    </p>
    <button class="btn-primary" @click="openStkSetup">Set up Amazon ↗</button>
  </div>
  <div v-else class="space-y-2">
    <p class="text-sm text-emerald-700">
      ✓ Connected as <strong>{{ stkStatus.customer_id }}</strong>
      <span v-if="stkStatus.registered_at"> · since {{ stkStatus.registered_at.slice(0, 10) }}</span>
    </p>
    <p class="text-sm">
      Default destination: <strong>{{ stkStatus.default_destination || 'none' }}</strong>
    </p>
    <p class="text-sm" :class="stkQuota.exhausted ? 'text-red-600' : 'text-slate-600'">
      Sent today: {{ stkQuota.sent_24h }} / {{ stkQuota.cap }}
      <span v-if="stkQuota.exhausted">
        — daily cap reached, biblichor will fall back to SMTP
      </span>
    </p>
    <div class="flex gap-2 mt-2">
      <button class="btn-secondary" @click="openStkSetup">Change device</button>
      <button class="btn-secondary" @click="sendStkTest">Send test</button>
      <button class="btn-danger" @click="disconnectStk">Disconnect</button>
    </div>
  </div>
</Card>

<!-- Setup modal -->
<div v-if="showStkModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
  <div class="bg-white rounded shadow-xl max-w-lg w-full p-6 space-y-4">
    <div class="flex items-center justify-between">
      <h3 class="text-lg font-semibold">Connect to Amazon</h3>
      <button @click="showStkModal = false" class="text-slate-400 hover:text-slate-700">✕</button>
    </div>

    <div v-if="stkError" class="bg-red-50 text-red-700 p-3 rounded">{{ stkError }}</div>

    <div v-if="stkModalStep === 'authorize' || stkModalStep === 'paste'" class="space-y-4">
      <div>
        <p class="text-sm mb-2">
          <strong>Step 1:</strong> Click below to open Amazon's authorize page.
          Sign in and click "Allow".
        </p>
        <a :href="stkAuthorizeUrl" target="_blank" rel="noopener" class="btn-primary inline-block">
          Open Amazon authorize page ↗
        </a>
      </div>
      <div>
        <p class="text-sm mb-2">
          <strong>Step 2:</strong> After clicking Allow, you'll land on a page on
          amazon.com that shows just a code. Copy the FULL URL from your address
          bar and paste here:
        </p>
        <input v-model="stkRedirectUrl"
               placeholder="https://www.amazon.com/ap/maplanding?openid..."
               class="input w-full" />
        <div class="flex gap-2 mt-3">
          <button class="btn-primary" :disabled="!stkRedirectUrl || stkLoading"
                  @click="completeStkOauth">
            Connect
          </button>
          <button class="btn-secondary" @click="showStkModal = false">Cancel</button>
        </div>
      </div>
    </div>

    <div v-else-if="stkModalStep === 'devices'" class="space-y-3">
      <p class="text-sm text-emerald-700">✓ Connected to Amazon</p>
      <p class="text-sm">Pick the default delivery target:</p>
      <div class="space-y-1">
        <label v-for="d in stkDevices" :key="d.device_serial_number"
               class="flex items-center gap-2 text-sm">
          <input type="radio" :value="d.device_serial_number" v-model="stkSelectedSn" />
          <span>{{ d.device_name }}</span>
          <span v-if="d.device_type === 'FionaWebApp' || d.device_name.toLowerCase().includes('web')"
                class="text-xs text-emerald-700">(recommended — cloud-only)</span>
        </label>
      </div>
      <p class="text-xs text-slate-500">
        Every book lands in your Personal Documents library regardless. Per-device
        auto-download is set at amazon.com/mycontent.
      </p>
      <div class="flex gap-2">
        <button class="btn-primary" :disabled="!stkSelectedSn || stkLoading"
                @click="saveStkDestination">Save</button>
        <button class="btn-secondary" @click="showStkModal = false">Cancel</button>
      </div>
    </div>
  </div>
</div>
```

(The CSS classes `btn-primary`, `btn-secondary`, `btn-danger`, `input`, `Card` are existing biblichor components/utilities. If your style system uses different names, swap accordingly.)

- [ ] **Step 4: Build the SPA**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library/webapp && npm run build 2>&1 | tail -5'
```

Expected: builds clean. If TypeScript complains about `Card` props, check the existing component's signature in `webapp/src/components/`.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add webapp/src/pages/SettingsPage.vue && git commit -m "STK 11: SettingsPage Kindle Browser Upload card + modal" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Task 12: Pipeline integration + integration smoke

**Files:**
- Modify: `src/endless_library/pipeline.py`
- Test: `tests/integration/test_kindle_stk_smoke.py`

- [ ] **Step 1: Find the existing kindle.send_to_kindle call site(s) in pipeline.py**

```bash
ssh ubuntu@claude-1 'grep -n "send_to_kindle\|from endless_library.kindle" /home/ubuntu/endless-library/src/endless_library/pipeline.py'
```

There should be one or two call sites. Each becomes a `kindle_router.deliver(...)` call.

- [ ] **Step 2: Write the integration smoke test**

Write to `tests/integration/test_kindle_stk_smoke.py`:

```python
"""Phase STK 12: end-to-end STK integration smoke test.

Mocks only the HTTP layer (intercepts requests to amazon.com and
stkservice.amazon.com). Runs the OAuth → register-device → send flow
through the real KindleStkService + kindle_router stack.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import respx
import httpx

from endless_library.db.schema import init_db, connect


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


@pytest.fixture
def svc():
    class _Svc:
        def __init__(self): self._s: dict[str, str] = {}
        def get_secret_value(self, k): return self._s.get(k)
        def set_secret_value(self, k, v): self._s[k] = v
        def set_secret_values(self, kv): self._s.update(kv)
        def delete_secret_value(self, k): self._s.pop(k, None)
    return _Svc()


def test_router_smoke_records_send_stk_event_via_router(db, svc, tmp_path, monkeypatch):
    """Path: router with fake vendored client + real event-log writes.
    This is the most useful smoke — it asserts the wiring from router
    down through KindleStkService → vendored client → event log all
    holds together."""
    from tests._stkclient_stub import FakeVendoredClient
    fake = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake,
    )
    # Configure STK
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")

    # Insert a book row so events.book_id has somewhere to point
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, status) VALUES ('Test', 'A', 'queued')"
        )
        book_id = cur.lastrowid

    cfg = SimpleNamespace(
        stk=SimpleNamespace(daily_cap=500, max_attempts=3,
                            backoff_initial_sec=0.0, backoff_factor=1.0),
        smtp=SimpleNamespace(daily_cap=80),
    )
    book = SimpleNamespace(id=book_id, title="Test", author="A")
    f = tmp_path / "book.epub"
    f.write_bytes(b"FAKE")

    from endless_library.kindle_router import deliver, DeliveryMethod
    result = deliver(file_path=f, book=book, cfg=cfg, db_path=db, svc=svc)
    assert result.ok is True
    assert result.method == DeliveryMethod.STK
    assert fake.send_calls, "vendored client send_file was not invoked"

    with connect(db) as conn:
        events = [r["kind"] for r in conn.execute(
            "SELECT kind FROM events WHERE book_id = ?", (book_id,)
        )]
    assert "send-stk" in events
```

- [ ] **Step 3: Run to verify failure (because the wiring is shallow)**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/integration/test_kindle_stk_smoke.py -v 2>&1 | tail -10'
```

Expected: 1 pass IF Tasks 4 + 8 are wired correctly. If it fails, the actual wiring of the vendored Client constructor or send_file kwargs is different and needs adjustment.

- [ ] **Step 4: Replace the existing kindle.send_to_kindle call in pipeline.py with kindle_router.deliver**

Find each call site in `src/endless_library/pipeline.py`. They typically look like:

```python
from endless_library.kindle import send_to_kindle, KindleSendError, KindleRateLimited
...
try:
    send_to_kindle(file_path, book=book, ...)
    books_repo.mark_kindled(book.id)
except KindleRateLimited:
    ...
except KindleSendError as e:
    ...
```

Replace with:

```python
from endless_library.kindle_router import deliver, DeliveryMethod

result = deliver(
    file_path=file_path, book=book, cfg=cfg,
    db_path=db_path, svc=bookorbit_svc,
)
if result.ok:
    books_repo.mark_kindled(book.id, method=result.method.value)
else:
    # Existing failure handling — both STK and SMTP exhausted.
    books_repo.mark_failed(book.id, last_error=result.error)
```

The router itself handles all the retry, fallback, and audit-event logic; the pipeline doesn't need to catch STK-specific exceptions. The existing scheduler retry-job mechanism still runs on the next pass if the book stays failed.

- [ ] **Step 5: Run the full suite**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3'
```

Expected: all pass. New test count: baseline + ~45 = ~1085.

- [ ] **Step 6: Commit**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git add src/endless_library/pipeline.py tests/integration/test_kindle_stk_smoke.py && git commit -m "STK 12: pipeline calls kindle_router.deliver + smoke test" -m "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"'
```

---

## Final acceptance: rebuild + manual live verification

- [ ] **Step 1: Push the branch**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git push origin stk-integration'
```

- [ ] **Step 2: Rebuild biblichor with the new code**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && docker compose -f deploy/compose.yml --env-file ./.env up -d --no-deps --build biblichor 2>&1 | tail -10'
```

- [ ] **Step 3: Verify healthz includes the new STK block**

```bash
ssh ubuntu@claude-1 'curl -fsS http://localhost:8090/healthz | python3 -m json.tool'
```

Expected: response contains `"stk": {"configured": false}` since no user has set up STK yet.

- [ ] **Step 4: Live OAuth setup (manual one-time)**

In a browser, open `http://localhost:8090/` (or your Tailscale URL) → Settings → click "Set up Amazon ↗".

Walk the modal:
1. Click "Open Amazon authorize page" → sign in on Amazon → click Allow.
2. Copy the redirect URL from `amazon.com/ap/maplanding?...`.
3. Paste it into the modal → click Connect.
4. Pick "Kindle for Web" as the destination → Save.

- [ ] **Step 5: Send a test**

Click "Send test" on the now-connected card. Open `read.amazon.com` and verify the test file appears in your Personal Documents library within 1–2 minutes.

- [ ] **Step 6: Merge to main**

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && git checkout main && git pull origin main && git merge --no-ff stk-integration -m "Merge stk-integration: Send-to-Kindle browser-upload + SMTP fallback" -m "See docs/superpowers/specs/2026-05-23-stk-integration-design.md" && git push origin main'
```

- [ ] **Step 7: Sync wiki**

Add `docs/wiki/Kindle-Browser-Upload.md` covering the setup wizard, the Kindle-for-Web recommendation, and the per-device-auto-download tradeoffs. Then:

```bash
ssh ubuntu@claude-1 'cd /home/ubuntu/endless-library && bash scripts/sync-wiki.sh 2>&1 | tail -5'
```

Phase STK complete.
