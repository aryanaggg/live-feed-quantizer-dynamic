import json

import pytest
from fastapi.testclient import TestClient

import security
from config import settings
from server import app

client = TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/video",
        "data:image/jpeg;base64,AAAA",
        "gopher://example.com",
        "javascript:alert(1)",
    ],
)
def test_fetch_url_rejects_disallowed_schemes(url):
    with pytest.raises(security.URLValidationError) as exc:
        security.validate_fetch_url(url, allow_private=False)
    assert exc.value.reason == "scheme_not_allowed"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/snapshot.jpg",
        "http://[::1]:8080/snapshot.jpg",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/snapshot.jpg",
        "http://192.168.1.20/snapshot.jpg",
        "rtsp://172.16.0.9:554/stream",
        "http://0.0.0.0/",
    ],
)
def test_fetch_url_blocks_internal_ranges_by_default(url):
    with pytest.raises(security.URLValidationError) as exc:
        security.validate_fetch_url(url, allow_private=False)
    assert exc.value.reason in {"blocked_address", "blocked_metadata"}


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.20/snapshot.jpg",
        "http://10.0.0.5/snapshot.jpg",
        "rtsp://172.16.0.9:554/stream",
        "http://127.0.0.1:8080/snapshot.jpg",
    ],
)
def test_private_lan_flag_allows_private_cameras(url):
    assert security.validate_fetch_url(url, allow_private=True) == url


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/", "http://100.100.100.200/", "http://metadata.google.internal/"],
)
def test_metadata_is_never_allowed(url):
    with pytest.raises(security.URLValidationError):
        security.validate_fetch_url(url, allow_private=True)


def test_public_hostname_is_allowed():
    assert security.validate_fetch_url("https://example.com/cam.jpg", allow_private=False)


def test_proxy_url_rejects_rtsp():
    with pytest.raises(security.URLValidationError) as exc:
        security.validate_proxy_url("rtsp://example.com/stream", allow_private=True)
    assert exc.value.reason == "scheme_not_allowed"


def test_credentials_are_redacted_in_logs():
    url = "rtsp://admin:hunter2@192.168.1.50:554/stream?password=hunter2&token=abc"
    redacted = security.redact_url(url)
    assert "hunter2" not in redacted
    assert "abc" not in redacted
    assert "192.168.1.50" in redacted


def test_origin_allowlist_exact_match():
    allowed = ["https://app.example.com", "http://localhost:5500"]
    assert security.origin_allowed("https://app.example.com", allowed)
    assert security.origin_allowed("http://localhost:5500", allowed)
    assert not security.origin_allowed("https://evil.example", allowed)
    assert not security.origin_allowed("https://app.example.com.evil.com", allowed)
    # Non-browser clients do not send Origin.
    assert security.origin_allowed(None, allowed)


def test_bridge_host_check_rejects_rebinding_domains():
    allowed = ["127.0.0.1:8765", "localhost:8765"]
    assert security.bridge_host_allowed("127.0.0.1:8765", allowed)
    assert security.bridge_host_allowed("localhost:8765", allowed)
    assert not security.bridge_host_allowed("evil.example:8765", allowed)
    assert not security.bridge_host_allowed("127.0.0.1.evil.example:8765", allowed)
    assert not security.bridge_host_allowed(None, allowed)


def test_pairing_tokens_are_per_origin_and_constant_time():
    store = security.PairingStore()
    token = store.issue("https://app.example.com")
    assert store.verify("https://app.example.com", token)
    assert not store.verify("https://evil.example", token)
    assert store.issue("https://app.example.com") == token
    assert store.issue("https://app.example.com", force=True) != token


def test_cors_rejects_unknown_origin():
    allowed = settings.allowed_origins[0]
    resp = client.options(
        "/health",
        headers={"Origin": allowed, "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") == allowed

    resp = client.options(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"


def test_camera_proxy_disabled_on_hosted_server():
    resp = client.get("/camera_proxy", params={"url": "http://192.168.1.20/snap.jpg"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "camera_fetch_disabled"


def test_camera_proxy_rejects_unknown_origin_before_fetching():
    resp = client.get(
        "/camera_proxy",
        params={"url": "http://192.168.1.20/snap.jpg"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "origin_not_allowed"


def test_camera_source_requires_bridge_by_default():
    ws = client.websocket_connect("/ws")
    ws.__enter__()
    try:
        ws.send_text(json.dumps({"type": "camera_source", "source": "rtsp", "url": "rtsp://192.168.1.5/s"}))
        obj = json.loads(ws.receive_text())
        assert obj == {"type": "error", "detail": "bridge_required"}
    finally:
        ws.__exit__(None, None, None)


def test_ws_rejects_disallowed_origin():
    ws = client.websocket_connect("/ws", headers={"origin": "https://evil.example"})
    ws.__enter__()
    try:
        obj = json.loads(ws.receive_text())
        assert obj["detail"] == "origin_not_allowed"
    finally:
        ws.__exit__(None, None, None)
