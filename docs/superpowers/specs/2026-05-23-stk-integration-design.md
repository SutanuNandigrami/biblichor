# Send-to-Kindle Browser-Upload Integration — Design

**Date:** 2026-05-23
**Status:** Approved design — ready for implementation plan

## Goal

Add an alternative delivery path to biblichor that uses Amazon's "Send to Kindle" browser-upload API (the OAuth + RSA-signed Fiona/ADP flow), replacing the current SMTP-only model. Books go to the user's Kindle Personal Documents library without hitting Gmail's ~80/day SMTP cap or its 24 MB attachment limit. SMTP stays as a fallback.

## Why

- SMTP rate-limit hit in production (`sent_24h: 80, exhausted: true`) — the current path is a chronic bottleneck.
- Amazon is tightening SMTP: April 2025 enforcement requiring full email addresses; Feb 2025 Instapaper STK integration paywalled. Trend is away from SMTP.
- STK supports up to ~200 MB per file vs SMTP's 50 MB; biblichor's typical ebook is <10 MB but Bengali / academic PDFs sometimes exceed SMTP's cap.
- The OAuth flow + Distributor Terms of Use mean this is legally cleaner than the alternative ("scrape `amazon.com/sendtokindle` with username/password" — which violates Amazon's AUP).

## Scope

In scope:
- Vendor `maxdjohnson/stkclient` (~500 LOC, MIT) as `src/endless_library/kindle_stk/_vendored/`.
- biblichor wrapper module `kindle_stk/` exposing `KindleStkService`.
- High-level `kindle_router.deliver(...)` that the pipeline calls; encapsulates the STK-primary + SMTP-fallback decision.
- Setup wizard SPA modal + 7 new FastAPI endpoints.
- STK rate-limit gate (`stk_rate.py`) mirroring `smtp_rate.py`.
- `/healthz` extension with an `stk` block.
- Per-book `sent_method` column + audit events with `kind ∈ {send-stk, send-stk-failed}`.
- ~45 new tests across 6 layers; no live Amazon calls in CI.

Out of scope:
- Registering biblichor's own OAuth `client_id` at Amazon Developer Console (deferred; documented risk).
- Multi-user / multi-account support — biblichor remains effectively single-user.
- "Send to a friend's Kindle" — only the configured account's own devices.
- Audiobook / Kindle Unlimited integration.
- Server-side conversion (e.g. EPUB → KFX) — Amazon does this for us post-upload.

## Architectural decisions (locked during brainstorming)

| Decision | Choice |
|---|---|
| Vendor strategy | In-tree copy at `src/endless_library/kindle_stk/_vendored/`, not git submodule. |
| OAuth client_id | Use stkclient's existing hardcoded client_id. Document the rotation risk; defer self-registration. |
| Routing default | STK primary; on failure, fall back to SMTP. |
| Device targeting | Single default device. Setup wizard pre-selects "Kindle for Web" (cloud-only target) when present. |
| Setup wizard surface | SPA modal on Settings page. Copy-paste redirect URL flow (mandated by stkclient's fixed `redirect_uri = https://www.amazon.com/ap/maplanding`). |
| STK retry policy | 3 attempts with exponential backoff (5s → 15s → 45s); honors `Retry-After` on 429; `AuthExpired` skips retries. |
| Rate-limit gate | Mirror SMTP pattern. Default daily cap 500 (vs SMTP 80). Counts `events` rows with `kind='send-stk'` in rolling 24h. |
| Per-book audit | New optional `sent_method` column on `books`. New event kinds `send-stk` and `send-stk-failed`. |
| Live verification | Manual one-time per deployment. CI uses fake vendored client. |

## Architecture

### Module layout

```
src/endless_library/
├── kindle.py                        ← existing SMTP send (untouched; called via router)
├── kindle_router.py                 ← NEW: deliver() entry-point used by pipeline
├── kindle_stk/                      ← NEW: vendored stkclient + biblichor wrapper
│   ├── __init__.py                  ← biblichor public surface
│   ├── service.py                   ← KindleStkService façade
│   ├── exceptions.py                ← biblichor exception hierarchy
│   ├── README.md                    ← vendoring notes, attribution, sync instructions
│   ├── LICENSE.stkclient            ← MIT license copy
│   └── _vendored/                   ← stkclient v0.1.1, files prefixed with underscore
│       ├── __init__.py
│       ├── _client.py
│       ├── _signer.py
│       ├── _oauth.py
│       ├── _models.py
│       └── _http.py
├── stk_rate.py                      ← NEW: quota_status() mirroring smtp_rate.py
├── web/api.py                       ← extends with 7 new endpoints
├── pipeline.py                      ← swaps kindle.send_to_kindle for kindle_router.deliver
└── db/schema.py                     ← adds `sent_method TEXT NULL` to books via _migrate()

tests/unit/
├── test_kindle_stk_service.py       ← Layer B: KindleStkService unit tests
├── test_kindle_router.py            ← Layer C: router decision tree tests
├── test_kindle_stk_endpoints.py     ← Layer D: FastAPI endpoint tests
└── test_stk_rate.py                 ← Layer E: rate-limit gate tests

tests/integration/
└── test_kindle_stk_smoke.py         ← Layer F: respx-mocked end-to-end

tests/_stkclient_stub.py             ← Layer A: FakeVendoredClient for B/C tests

webapp/src/pages/SettingsPage.vue    ← extends with Kindle Browser Upload Card
```

### Credentials shape

Stored in the existing encrypted `secrets` table via `BookOrbitService.set_secret_values` (atomic since Phase 6x I13):

| Key | Purpose |
|---|---|
| `kindle_stk.oauth_state.code_verifier` | Ephemeral PKCE code verifier between `start_oauth` and `complete_oauth`. Deleted after exchange. |
| `kindle_stk.device_cert.pem` | RSA private key from `registerDeviceWithToken`. Long-lived. |
| `kindle_stk.adp_token` | Amazon Device Provisioning token. Pairs with the RSA key for signing. |
| `kindle_stk.adp_did` | Device ID Amazon assigned. |
| `kindle_stk.registered_at` | ISO timestamp shown in Settings as "Connected since". |
| `kindle_stk.amazon_customer_id` | For display only. |
| `kindle_stk.default_destination_sn` | Selected device serial number for sends. |
| `kindle_stk.default_destination_name` | Friendly name (e.g. "Kindle for Web"). |

No new database tables required.

## Components

### 1. Vendored stkclient

Source: https://github.com/maxdjohnson/stkclient @ tag v0.1.1
License: MIT
Vendored on: 2026-05-23

Files are copied verbatim into `_vendored/` with the only change being the underscore-prefix on filenames (so it's obvious which code is upstream vs ours). The `_vendored/__init__.py` re-exports just the symbols biblichor needs:

```python
from ._client import Client
from ._oauth import OAuth2
from ._models import OwnedDevice
```

A small `tools/sync_stkclient.py` script automates the upstream sync if/when we want to bump versions. Not run in CI; manual maintenance only.

### 2. Exception hierarchy (`kindle_stk/exceptions.py`)

```python
class KindleStkError(Exception):
    """Base for all STK errors."""

class KindleStkNotConfigured(KindleStkError):
    """No device cert in secrets store — OAuth flow not completed."""

class KindleStkAuthExpired(KindleStkError):
    """ADP signing failed; device cert revoked/expired. User must re-OAuth."""

class KindleStkRateLimited(KindleStkError):
    """HTTP 429 from stkservice. Honors Retry-After header.
    Carries retry_after_sec: int as an attribute."""

class KindleStkUploadFailed(KindleStkError):
    """Transient or unknown failure during the 4-step send flow."""
```

Raw stkclient exceptions (`requests.HTTPError`, `ValueError`, etc.) are caught in `service.py` and re-raised as the appropriate biblichor type. The router pattern-matches on these.

### 3. KindleStkService (`kindle_stk/service.py`)

Façade for all OAuth + send operations. Loads/saves credentials via `BookOrbitService`. Owns the 5-minute device list cache.

```python
class KindleStkService:
    DEVICES_CACHE_TTL_SEC = 300

    def __init__(self, bookorbit_svc: BookOrbitService):
        self._svc = bookorbit_svc
        self._devices_cache: tuple[list[OwnedDevice], float] | None = None

    def is_configured(self) -> bool: ...
    def start_oauth(self) -> tuple[str, str]: ...
    def complete_oauth(self, redirect_url: str) -> dict: ...
    def list_devices(self) -> list[OwnedDevice]: ...
    def set_default_destination(self, device_sn: str) -> None: ...
    def send_file(self, file_path: Path, *, format: str, title: str,
                  author: str) -> dict: ...
    def deregister(self) -> None: ...
```

The constructed `stkclient.Client` is rebuilt on every call from the stored cert + ADP token; we don't cache the Client object itself (it's cheap to instantiate and avoids stale-state bugs).

### 4. kindle_router (`kindle_router.py`)

Single entry-point used by the pipeline:

```python
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
    book: BookRow,
    cfg: Config,
    db_path: Path,
    svc: BookOrbitService,
) -> DeliveryResult: ...
```

Decision tree:

1. If `KindleStkService(svc).is_configured()` is False → call existing SMTP path directly.
2. Else check `stk_rate.quota_status(db_path, daily_cap=cfg.stk.daily_cap)`. If exhausted → SMTP path, log event `send-stk-failed` with `meta_json: {reason: "stk-cap-reached"}`.
3. Else loop up to `cfg.stk.max_attempts` (default 3):
   - Call `KindleStkService.send_file(...)`.
   - On `KindleStkAuthExpired`: break loop, fall to SMTP, mark user "needs re-OAuth" via an event.
   - On `KindleStkRateLimited`: sleep for `e.retry_after_sec` or `backoff`, retry.
   - On `KindleStkUploadFailed`: sleep for backoff (`5s → 15s → 45s`), retry.
   - On success: log `send-stk` event with `meta_json: {attempts, duration_ms, device_sn}`, return.
4. After loop exhaustion: log `send-stk-failed` with full attempt history, fall to SMTP.

The SMTP path is the existing `kindle.send_to_kindle(...)`. The router catches `KindleSendError` / `KindleRateLimited` exactly as the pipeline does today.

### 5. stk_rate (`stk_rate.py`)

```python
@dataclass(frozen=True, slots=True)
class StkQuotaStatus:
    sent_24h: int
    cap: int
    remaining: int
    exhausted: bool


def quota_status(db_path: Path, *, daily_cap: int) -> StkQuotaStatus:
    """Counts events with kind='send-stk' in the rolling 24h window."""
```

Identical shape to `smtp_rate.quota_status`. Caller passes the cap from `cfg.stk.daily_cap`.

### 6. Config (`config.py`)

```python
class StkCfg(BaseModel):
    daily_cap: int = 500
    max_attempts: int = 3
    backoff_initial_sec: float = 5.0
    backoff_factor: float = 3.0
```

Wired under `Config.stk`.

### 7. New FastAPI endpoints (`web/api.py`)

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/api/kindle-stk/status` | `{configured, customer_id, registered_at, default_destination, devices_count}` |
| `POST`   | `/api/kindle-stk/oauth/start` | Returns `{authorize_url}`; stashes code verifier in secrets |
| `POST`   | `/api/kindle-stk/oauth/complete` | Body `{redirect_url}`; extracts code, registers device, persists cert |
| `GET`    | `/api/kindle-stk/devices` | List of registered devices (5-min server cache) |
| `PUT`    | `/api/kindle-stk/default-destination` | Body `{device_sn}`; validates against current devices |
| `POST`   | `/api/kindle-stk/test-send` | Sends a bundled 1-page test PDF; returns success/error |
| `DELETE` | `/api/kindle-stk/connection` | Calls deregister + wipes secrets |

All routed through `KindleStkService`. Exception → HTTP status mapping:
- `KindleStkNotConfigured` → 400 ("not configured")
- `KindleStkAuthExpired` → 401 ("re-authorize required")
- `KindleStkRateLimited` → 429 (include Retry-After header)
- `KindleStkUploadFailed` → 502 (upstream error)

### 8. /healthz extension

The existing healthz handler's body gains:

```json
"stk": {
  "configured": true,
  "sent_24h": 12,
  "cap": 500,
  "remaining": 488,
  "exhausted": false
}
```

When STK is not configured, the block is `{"configured": false}` with no quota fields.

### 9. SPA changes (`webapp/src/pages/SettingsPage.vue`)

A new "Kindle Browser Upload" Card sibling to the SMTP card.

**Not-configured state:**
- Card body explains why STK is preferred (no SMTP cap, larger files, Gmail-independent).
- Single CTA: "Set up Amazon ↗"
- Clicking opens the setup modal.

**Setup modal (two-step):**
1. Step 1: "Authorize biblichor on Amazon" — button opens `authorize_url` in a new tab (`target="_blank"`).
2. Step 2: "Paste redirect URL" — text input + Connect button. Calls `POST /oauth/complete` with the pasted URL.

**Device picker (after successful connect):**
- Radio list of all registered devices.
- "Kindle for Web" pre-selected when present (detected via the OwnedDevice's `device_type`).
- Brief explainer about cloud library vs per-device push.
- Save button → `PUT /default-destination`, closes modal.

**Configured state:**
- Shows "Connected as <customer_name> • since <registered_at>".
- Shows "Default destination: <device_name>".
- Shows "Sent today: 12 / 500" (computed from `/healthz` STK block).
- Three buttons: `Change device`, `Send test`, `Disconnect`.
- When `exhausted == true` the quota line turns red and adds "Daily cap reached — biblichor will fall back to SMTP."

### 10. Pipeline integration (`pipeline.py`)

Find the existing `kindle.send_to_kindle(...)` call site(s). Replace with:

```python
from endless_library.kindle_router import deliver

result = deliver(
    file_path=file_path, book=book, cfg=cfg,
    db_path=db_path, svc=bookorbit_svc,
)
if not result.ok:
    book_repo.mark_failed(book.id, last_error=result.error)
else:
    book_repo.mark_kindled(book.id, method=result.method.value)
```

The router internally handles all the retry, fallback, rate-gate, and event-logging. The pipeline doesn't need to know about STK vs SMTP.

### 11. Books schema migration

Add an optional `sent_method TEXT NULL` column to `books`. Migration in `db/schema.py` `_migrate()`:

```python
if "sent_method" not in {r["name"] for r in conn.execute("PRAGMA table_info(books)")}:
    conn.execute("ALTER TABLE books ADD COLUMN sent_method TEXT NULL")
```

`books_repo.mark_kindled(book_id, method=...)` writes this value. Old rows show `NULL`. SPA's Logs / Library pages get a small "via STK" / "via SMTP" badge based on this column when not null.

## Data flow

### OAuth setup flow

```
User clicks "Set up Amazon" in Settings
  → SPA POST /api/kindle-stk/oauth/start
  → KindleStkService.start_oauth()
    → vendored OAuth2.create_oauth_url() → (authorize_url, code_verifier)
    → secrets.set_secret_value("kindle_stk.oauth_state.code_verifier", verifier)
  → SPA opens authorize_url in new tab
User authenticates on Amazon, clicks Allow
  → Amazon redirects to https://www.amazon.com/ap/maplanding?openid...
  → User copies that URL, pastes back into biblichor modal
  → SPA POST /api/kindle-stk/oauth/complete { redirect_url: "..." }
  → KindleStkService.complete_oauth(redirect_url)
    → secrets.get_secret_value("kindle_stk.oauth_state.code_verifier")
    → vendored OAuth2.exchange_code_for_token(redirect_url, verifier)
    → vendored Client.register_device(access_token)
    → secrets.set_secret_values({
        "kindle_stk.device_cert.pem": ...,
        "kindle_stk.adp_token": ...,
        "kindle_stk.adp_did": ...,
        "kindle_stk.registered_at": ...,
        "kindle_stk.amazon_customer_id": ...,
      })
    → secrets.delete_secret("kindle_stk.oauth_state.code_verifier")
    → return device list to SPA
  → SPA shows device picker
User selects "Kindle for Web", clicks Save
  → SPA PUT /api/kindle-stk/default-destination { device_sn }
  → KindleStkService.set_default_destination(device_sn)
    → validates against list_devices()
    → secrets.set_secret_values({
        "kindle_stk.default_destination_sn": device_sn,
        "kindle_stk.default_destination_name": device_name,
      })
```

### Delivery flow

```
Pipeline calls kindle_router.deliver(file_path, book, cfg, db_path, svc)
  → service = KindleStkService(svc)
  → if not service.is_configured(): smtp_path() ; return
  → if stk_rate.quota_status(...).exhausted: smtp_path() ; return
  → for attempt in range(cfg.stk.max_attempts):
      → try service.send_file(file_path, format, title, author)
      → on success: events.log(kind='send-stk', meta=...); return ok
      → on AuthExpired: events.log(kind='send-stk-failed', meta={reason:'auth-expired'}); break loop
      → on RateLimited: sleep(retry_after); continue
      → on UploadFailed: sleep(backoff(attempt)); continue
  → smtp_path() ; events.log(kind='send-stk-failed', meta={attempts, fellback_to:'smtp'})
```

### Healthz extension

```
GET /healthz
  → existing body (db, scrapers, scheduler, queue_size, smtp)
  → + StkQuotaStatus(db_path, daily_cap=cfg.stk.daily_cap)
  → + KindleStkService(svc).is_configured()
  → JSON merged: body["stk"] = {configured, sent_24h, cap, remaining, exhausted}
```

## Error handling

| Failure | Detection | Response |
|---|---|---|
| OAuth code expired (user took >15min between start and complete) | `vendored.exchange_code_for_token` raises | 400 "OAuth code expired — restart setup" |
| Invalid redirect URL | URL doesn't match `https://www.amazon.com/ap/maplanding?...` shape | 400 "Paste the FULL URL from amazon.com/ap/maplanding" |
| Network error during register-device | Raw `requests.ConnectionError` | 502 "Could not reach Amazon" + log warning |
| Amazon returns 4xx during send | stkclient `requests.HTTPError` with status | Mapped to `KindleStkAuthExpired` (401/403) or `KindleStkRateLimited` (429) or `KindleStkUploadFailed` (others) |
| Cert revoked by user via amazon.com/mycontent | Send returns 401 with specific Amazon error code | `KindleStkAuthExpired` → flag user via toast "Reconnect Amazon"; pipeline falls to SMTP |
| Quota exhausted | `stk_rate.quota_status().exhausted` | Pipeline falls to SMTP; logs `send-stk-failed` with reason `stk-cap-reached` |
| File >200MB | (Future) — defer check until upload PUT actually fails | If we see this in practice, add a pre-check |
| SMTP also fails after STK fallback | Existing SMTP error handling | Book marked failed; manual retry via SPA |

## Testing

See Architecture Section 6 above for the 6-layer plan. ~45 new tests bringing total to ~1085.

No live Amazon calls in CI. Live verification is a manual one-time step per deployment (run setup wizard, send test PDF, confirm in Kindle library).

## Risks and open questions

1. **OAuth client_id rotation fragility** — stkclient hardcodes one. If Amazon ever revokes it, every stkclient deployment dies simultaneously. Mitigation: design `client_id` as a `Config.stk.client_id` setting with stkclient's value as the default, so a future swap is one line. Documented; deferred.
2. **Endpoint stability** — `stkservice.amazon.com` URLs have been stable since ~2018. The integration-smoke test (Layer F) is the canary.
3. **Per-device "auto-download" semantics** — biblichor doesn't control these (`amazon.com/mycontent` does). Documented in the device-picker explainer. User-visible.
4. **OAuth flow timeout** — Amazon's OAuth code expires after ~15 minutes. The code_verifier stashed in secrets has no TTL today; we delete it after a successful complete_oauth, but a never-completed flow leaves it sitting. Cosmetic concern; cleanup is one-line if it ever matters.
5. **stkclient's `requests` dependency** — biblichor uses `httpx` and `curl-cffi` elsewhere; stkclient brings `requests`. Acceptable: it's a small dep, stable, and the vendored module is isolated.
6. **`Send to Kindle` daily cap unofficial** — Amazon doesn't publish a STK limit. 500/day is an educated guess. If users hit it, raise via Settings.
7. **Vue 3 reactivity in the modal** — the device-picker radio list with dynamic data after OAuth completes is straightforward; SchedulePage.vue's pattern is the reference.

## Acceptance criteria

- All 6 test layers landed and green; total ~1085 passed.
- Setup wizard works end-to-end on a real Amazon account (manual verification).
- A test PDF send produces a file in the user's Kindle Personal Documents library.
- Quota gate prevents accidental overuse; the `/healthz` block shows live counts.
- SMTP fallback fires when STK is exhausted or fails; verified by ratcheting the daily cap to 0 in cfg.
- `Books` rows ship with the right `sent_method` after a delivery.
- Wiki page added at `docs/wiki/Kindle-Browser-Upload.md` documenting the setup flow + the per-device-prefs explainer.

## Out of scope (rejected / deferred)

- Multi-account / multi-tenant support.
- Own OAuth client_id registration at Amazon Developer Console.
- Real-time delivery status polling (Amazon's STK is async; we don't get a definitive "delivered to device" signal cheaply).
- Pre-conversion of EPUB → KFX (Amazon handles this server-side post-upload).
- File-size pre-check before STK call (defer until we see a real failure).
- Removing the SMTP path entirely (kept as fallback).

## Wiki updates

After implementation, add `docs/wiki/Kindle-Browser-Upload.md` covering:

- What this is + why prefer it over SMTP.
- Setup walkthrough with screenshots.
- The "Kindle for Web" recommendation + the per-device-auto-download tradeoffs.
- Troubleshooting common errors (OAuth code expired, redirect URL malformed, "needs reconnect" toast).
- The unofficial daily cap + how to tune.
- The OAuth client_id rotation note (single point of failure) — link to the `Config.stk.client_id` setting.

Sync via `scripts/sync-wiki.sh`.
