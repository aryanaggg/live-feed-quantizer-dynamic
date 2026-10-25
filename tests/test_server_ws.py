import base64
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)


def _jpeg_data_url():
    frame = np.random.randint(0, 255, (48, 48, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _tone(sr=8000, seconds=0.5, freq=220, amp=0.4):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32).tobytes()


def _connect():
    sock = client.websocket_connect("/ws")
    sock.__enter__()
    return sock


def test_full_frame_flow_returns_quantized_jpeg():
    ws = _connect()
    try:
        ws.send_text(json.dumps({"type": "audio_info", "sampleRate": 8000}))
        ws.send_bytes(_tone())
        ws.send_text(_jpeg_data_url())
        response = ws.receive_text()
        assert response.startswith("data:image/jpeg;base64,")
        img_bytes = base64.b64decode(response.split(",", 1)[1])
        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape == (48, 48, 3)
    finally:
        ws.__exit__(None, None, None)


def test_missing_audio_returns_json_waiting_status():
    ws = _connect()
    try:
        ws.send_text(json.dumps({"type": "audio_info", "sampleRate": 8000}))
        ws.send_text(_jpeg_data_url())
        response = ws.receive_text()
        obj = json.loads(response)
        assert obj["type"] == "status"
        assert obj["detail"] == "waiting_for_mic"
    finally:
        ws.__exit__(None, None, None)


def test_invalid_json_returns_no_crash():
    ws = _connect()
    try:
        ws.send_bytes(b"\x00\x00")  # not a valid float32 multiple? actually is; invalid audio ok
        ws.send_text("{not json")
        ws.send_text(_jpeg_data_url())
        # Should get either a processed frame or a waiting status, never a crash.
        response = ws.receive_text()
        assert response.startswith("data:image/jpeg;base64,") or response.startswith("{")
    finally:
        ws.__exit__(None, None, None)


def test_ping_pong():
    ws = _connect()
    try:
        ws.send_text(json.dumps({"type": "ping"}))
        assert json.loads(ws.receive_text()) == {"type": "pong"}
    finally:
        ws.__exit__(None, None, None)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_serves_frontend():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Live Feed Quantizer" in resp.text
