"""Parse the cookie string a user copies out of devtools / a browser
extension and convert it into the cookie-jar shape FlareSolverr and
Playwright expect.

Two input shapes are accepted:
  * Browser Cookie header value: 'name1=val1; name2=val2; name3=val3'
  * Newline-separated:           'name1=val1\nname2=val2\n...'
"""

from __future__ import annotations

DEFAULT_DOMAIN = "welib.org"


def parse_cookie_string(raw: str | None, *, domain: str = DEFAULT_DOMAIN) -> list[dict]:
    """Return a list of {name, value, domain, path} cookie dicts.

    Empty / None input returns []. Whitespace-only values are dropped.
    Cookies without an '=' are skipped.
    """
    if not raw:
        return []
    out: list[dict] = []
    # Split on semicolons AND newlines so both shapes are accepted.
    tokens = raw.replace("\n", ";").split(";")
    for tok in tokens:
        tok = tok.strip()
        if not tok or "=" not in tok:
            continue
        name, _, value = tok.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        out.append({"name": name, "value": value, "domain": domain, "path": "/"})
    return out


def to_playwright(cookies: list[dict]) -> list[dict]:
    """Same dict shape works for Playwright with one tweak — Playwright
    insists the domain field starts with a leading dot for subdomains
    (we just leave it as-is; bare `welib.org` matches the apex)."""
    return list(cookies)


def to_header_value(cookies: list[dict]) -> str:
    """Render back to the 'Cookie:' header form, useful when injecting into
    plain httpx requests."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
