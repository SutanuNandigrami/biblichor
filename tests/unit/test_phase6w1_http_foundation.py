# tests/unit/test_phase6w1_http_foundation.py
def test_curl_cffi_imports():
    from curl_cffi import requests as cffi_requests
    assert cffi_requests.Session is not None
