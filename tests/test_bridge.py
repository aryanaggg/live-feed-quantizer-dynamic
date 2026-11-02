import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import bridge
from frame_gateway import FrameGateway, decode_packet
from security import pairing_store

BASE_URL = "http://127.0.0.1:8765"
ALLOWED_ORIGIN = "http://127.0.0.1:5500"
# TestClient does not derive the WebSocket Host header from base_url.
WS_HEADERS = {"Origin": ALLOWED_ORIGIN, "Host": "127.0.0.1:8765"}


def _gradient_jpeg(w=320, h=180, seed=0):
    x = np.linspace(0, 255, w, dtype=np.uint8)
    y = np.linspace(0, 255, h, dtype=np.uint8)
    r = np.tile(x, (h, 1))
    g = np.tile(y[:, None], (1, w))
    b = np.full((h, w), (seed * 37) % 255, dtype=np.uint8)
    img = np.dstack([b, g, r])
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert ok
    return buf.tobytes()


@pytest.fixture(autouse=True)
def clean_bridge_state():
    pairing_store.clear()
    for limiter in (bridge.pair_limiter, bridge.bridge_http_limiter, bridge.bridge_ws_limiter):
        limiter._events.clear()
    yield


def _client(gateway=None):
    return TestClient(bridge.create_app(gateway), base_url=BASE_URL)


def _pair(client):
    resp = client.post("/pair", headers=WS_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_health_requires_loopback_host_and_allowed_origin():
    with _client() as client:
        resp = client.get("/health", headers=WS_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"] is True
        assert data["service"] == "live-feed-quantizer-bridge"

        # DNS-rebinding defence: an attacker-controlled domain is rejected even
        # though it may resolve to 127.0.0.1.
        resp = client.get("/health", headers={"Host": "evil.example", "Origin": ALLOWED_ORIGIN})
        assert resp.status_code == 403
        assert resp.json()["error"] == "host_not_allowed"

        resp = client.get("/health", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "origin_not_allowed"


def test_pair_issues_one_stable_token_per_origin():
    with _client() as client:
        first = client.post("/pair", headers=WS_HEADERS)
        assert first.status_code == 200
        token = first.json()["token"]
        assert token

        second = client.post("/pair", headers=WS_HEADERS)
        assert second.json()["token"] == token

        missing = client.post("/pair")
        assert missing.status_code == 403
        assert missing.json()["error"] == "origin_required"

        evil = client.post("/pair", headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        assert evil.json()["error"] == "origin_not_allowed"


def test_ws_requires_pairing_token_and_allowed_origin():
    with _client() as client:
        with client.websocket_connect("/ws?token=wrong", headers=WS_HEADERS) as ws:
            assert json.loads(ws.receive_text())["detail"] == "pairing_required"

        with client.websocket_connect("/ws", headers=WS_HEADERS) as ws:
            assert json.loads(ws.receive_text())["detail"] == "pairing_required"

        with client.websocket_connect(
            "/ws?token=x",
            headers={"Origin": "https://evil.example", "Host": "127.0.0.1:8765"},
        ) as ws:
            assert json.loads(ws.receive_text())["detail"] == "origin_not_allowed"

        with client.websocket_connect(
            "/ws?token=x",
            headers={"Origin": ALLOWED_ORIGIN, "Host": "evil.example"},
        ) as ws:
            assert json.loads(ws.receive_text())["detail"] == "host_not_allowed"


def test_ws_subscribe_forwards_binary_epoch_packet():
    jpeg = _gradient_jpeg()
    gateway = FrameGateway(fetch_fn=lambda _url: jpeg)
    with _client(gateway) as client:
        token = _pair(client)
        with client.websocket_connect(
            f"/ws?token={token}", headers=WS_HEADERS
        ) as ws:
            ready = json.loads(ws.receive_text())
            assert ready["type"] == "ready"
            assert ready["version"] == bridge.BRIDGE_VERSION

            ws.send_text(
                json.dumps(
                    {
                        "type": "subscribe",
                        "kind": "http",
                        "url": "http://127.0.0.1:9/snap.jpg",
                        "maxDim": 320,
                        "quality": 70,
                        "fps": 10,
                    }
                )
            )
            subscribed = json.loads(ws.receive_text())
            assert subscribed["type"] == "subscribed"
            assert subscribed["maxDim"] == 320
            assert subscribed["quality"] == 70
            assert subscribed["epoch"] >= 1

            packet = ws.receive_bytes()
            epoch, timestamp, body = decode_packet(packet)
            assert epoch == subscribed["epoch"]
            assert timestamp > 0
            assert body[:2] == b"\xff\xd8"

            ws.send_text(json.dumps({"type": "unsubscribe"}))
            # A frame may already be in flight; drain until the ack arrives.
            for _ in range(20):
                message = ws.receive()
                if message.get("text"):
                    ack = json.loads(message["text"])
                    if ack["type"] == "unsubscribed":
                        break
            else:
                pytest.fail("unsubscribe was not acknowledged")


def test_ws_reports_stream_errors_and_keeps_socket_open():
    gateway = FrameGateway(fetch_fn=lambda _url: (_ for _ in ()).throw(RuntimeError("camera offline")))
    with _client(gateway) as client:
        token = _pair(client)
        with client.websocket_connect(
            f"/ws?token={token}", headers=WS_HEADERS
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "ready"
            ws.send_text(
                json.dumps(
                    {"type": "subscribe", "kind": "http", "url": "http://127.0.0.1:9/snap.jpg"}
                )
            )
            assert json.loads(ws.receive_text())["type"] == "subscribed"
            error = json.loads(ws.receive_text())
            assert error["type"] == "stream_error"
            assert "camera offline" in error["detail"]

            ws.send_text(json.dumps({"type": "ping"}))
            for _ in range(20):
                message = ws.receive()
                if message.get("text"):
                    data = json.loads(message["text"])
                    if data["type"] == "pong":
                        break
            else:
                pytest.fail("socket did not answer ping after a stream error")


def test_ws_rejects_malformed_and_ssrf_sources():
    with _client(FrameGateway(fetch_fn=lambda _url: _gradient_jpeg())) as client:
        token = _pair(client)
        with client.websocket_connect(
            f"/ws?token={token}", headers=WS_HEADERS
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "ready"

            ws.send_text("not json")
            assert json.loads(ws.receive_text())["detail"] == "bad_json"

            ws.send_text(json.dumps({"type": "subscribe", "kind": "http",
                                     "url": "http://169.254.169.254/latest/meta-data/"}))
            assert json.loads(ws.receive_text())["detail"] == "blocked_address"

            ws.send_text(json.dumps({"type": "subscribe", "kind": "http",
                                     "url": "file:///etc/passwd"}))
            assert json.loads(ws.receive_text())["detail"] == "scheme_not_allowed"

            ws.send_text(json.dumps({"type": "subscribe", "kind": "gopher",
                                     "url": "gopher://camera.local/frame"}))
            assert json.loads(ws.receive_text())["detail"] == "unsupported_kind"

            ws.send_text(json.dumps({"type": "dance"}))
            assert json.loads(ws.receive_text())["detail"] == "unsupported_type"
