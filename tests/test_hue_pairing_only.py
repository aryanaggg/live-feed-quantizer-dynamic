"""Hue is pairing/discovery only until DTLS Entertainment streaming exists."""

import json

from fastapi.testclient import TestClient

import bridge
from frame_gateway import HueDriver

BASE_URL = "http://127.0.0.1:8765"


def test_hue_capabilities_are_pairing_only():
    caps = HueDriver(None).capabilities()

    assert caps["streaming"] is False
    assert caps["rate_mode"] == "unavailable"
    assert caps["max_rate_hz"] == 0.0


def test_hue_capabilities_over_the_light_websocket():
    app = bridge.create_app()
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            ws.send_text(json.dumps({"type": "capabilities", "kind": "hue"}))
            message = json.loads(ws.receive_text())

    assert message["type"] == "capabilities"
    caps = message["caps"]
    assert caps["streaming"] is False
    assert caps["rate_mode"] == "unavailable"
    assert caps["max_rate_hz"] == 0.0
