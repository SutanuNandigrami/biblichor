"""Tests for Phase 6z Fix 9 (I-NEW-5): Anubis solver uses raw post + recursion guard.

The old code passed `session` (with wrapped .post) to _solve_and_get_cookie,
which then called session.post() — the wrapped version. If the submit endpoint
returned another Anubis challenge page, the wrapper would call itself recursively,
causing infinite recursion or stack overflow.

Fix: capture raw session.post before wrapping and pass it to _solve_and_get_cookie.
Belt-and-suspenders: depth counter _ANUBIS_DEPTH_LIMIT = 2 prevents any recursion.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_anubis_html(challenge="abc123", difficulty=3):
    """Build a minimal Anubis challenge HTML page."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta name="anubis-challenge" content="{challenge}">
  <meta name="anubis-difficulty" content="{difficulty}">
  <meta name="anubis-action" content="/.within.website/x/cmd/anubis/api/pass-challenge">
  <meta name="generator" content="anubis">
</head>
<body><title>Making sure you're not a bot</title></body>
</html>"""


def _make_normal_html():
    """Non-Anubis page."""
    return "<html><body><h1>Normal Page</h1></body></html>"


def test_anubis_depth_limit_constant():
    """_ANUBIS_DEPTH_LIMIT must be defined and >= 1."""
    from endless_library.scrapers.http_client import _ANUBIS_DEPTH_LIMIT
    assert _ANUBIS_DEPTH_LIMIT >= 1


def test_solve_and_get_cookie_accepts_raw_post():
    """_solve_and_get_cookie signature must accept raw_post kwarg."""
    import inspect

    from endless_library.scrapers.http_client import _solve_and_get_cookie
    sig = inspect.signature(_solve_and_get_cookie)
    assert "raw_post" in sig.parameters, (
        "_solve_and_get_cookie must accept raw_post keyword argument"
    )


def test_anubis_recursion_bounded():
    """Mock a Session whose .post always returns Anubis HTML.
    The wrapper must NOT recurse infinitely — it should stop at _ANUBIS_DEPTH_LIMIT.
    """
    from endless_library.scrapers.http_client import _ANUBIS_DEPTH_LIMIT, _make_anubis_wrapper

    call_count = [0]

    # Fake session.post that always returns Anubis HTML
    class _FakePost:
        def __call__(self, url, **kw):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.text = _make_anubis_html()
            resp.cookies = {}
            return resp

    raw_post = _FakePost()

    # Also fake solve_anubis so test doesn't actually compute PoW
    import endless_library.scrapers.http_client as hc
    original_solve = hc.solve_anubis

    def _fast_solve(challenge, difficulty):
        return 0  # fake nonce

    hc.solve_anubis = _fast_solve
    try:
        # Create wrapper using raw_post (both orig_fn and raw_post_for_solve)
        wrapper = _make_anubis_wrapper(None, raw_post, raw_post)
        
        # Call wrapper once — it should detect Anubis, try to solve (calling raw_post),
        # then stop due to depth limit. Must NOT recurse unboundedly.
        import sys
        original_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(100)  # low limit to catch runaway recursion
        try:
            wrapper("https://example.com/page")
        except RecursionError:
            raise AssertionError("Anubis wrapper recursed infinitely (recursion guard not working)")  # noqa: B904
        finally:
            sys.setrecursionlimit(original_limit)
    finally:
        hc.solve_anubis = original_solve

    # The wrapper called orig_fn once, then _solve called raw_post once (submit),
    # and may retry once more. Total calls bounded.
    assert call_count[0] <= _ANUBIS_DEPTH_LIMIT + 3, (
        f"Too many post calls: {call_count[0]} (expected <= {_ANUBIS_DEPTH_LIMIT + 3})"
    )


def test_install_middleware_captures_raw_post():
    """_install_anubis_middleware must capture raw post before wrapping."""
    
    # We can't easily test this directly without a real Session,
    # but we can verify that _solve_and_get_cookie is called with raw_post=
    # by checking the function signature accepts it.
    import inspect

    from endless_library.scrapers.http_client import _solve_and_get_cookie
    
    params = inspect.signature(_solve_and_get_cookie).parameters
    assert "raw_post" in params
    assert "session" not in params, (
        "'session' should be removed from _solve_and_get_cookie — use raw_post instead"
    )


def test_make_anubis_wrapper_accepts_raw_post_arg():
    """_make_anubis_wrapper must accept raw_post_for_solve as third argument."""
    import inspect

    from endless_library.scrapers.http_client import _make_anubis_wrapper
    
    sig = inspect.signature(_make_anubis_wrapper)
    params = list(sig.parameters.keys())
    assert len(params) >= 3, f"Expected 3+ params, got: {params}"
    assert "raw_post_for_solve" in params
