"""WebSocket integration for zone configuration, frames, and scenes."""

import json

from fastapi.testclient import TestClient

import bridge
from frame_gateway import LightDriver
from light_zones import LightZoneConfig

BASE_URL = "http://127.0.0.1:8765"


class FakeDriver(LightDriver):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def set_state(self, state: dict) -> None:
        self.calls.append((state.get("device_id"), dict(state)))

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "fast",
                "max_rate_hz": 100.0}

    def status(self) -> dict:
        return {"online": True, "devices": []}


def _payload():
    return {
        "groups": [
            {"id": "living", "devices": ["lamp1", "lamp2"], "brightness_cap": 60},
        ],
        "zones": [
            {"id": "bass_zone", "band": "bass", "devices": ["group:living"],
             "smoothing_ms": 0},
            {"id": "mid_zone", "band": "mid", "devices": ["strip"],
             "brightness_cap": 40, "smoothing_ms": 0},
        ],
        "scenes": [
            {"id": "evening", "targets": ["group:living"],
             "state": {"r": 1, "g": 2, "b": 3, "brightness": 100}},
        ],
        "device_caps": {"lamp1": 50, "lamp2": 100, "strip": 100},
        "device_on": {"lamp1": True, "lamp2": True, "strip": True},
        "device_kinds": {"lamp1": "fake", "lamp2": "fake", "strip": "fake"},
        "global_smoothing_ms": 0,
    }


def _connect(app):
    return TestClient(app, base_url=BASE_URL)


def test_ws_configure_frame_and_caps():
    driver = FakeDriver()
    config = LightZoneConfig()
    app = bridge.create_app(light_drivers={"fake": driver}, zone_config=config)

    with _connect(app) as client:
        with client.websocket_connect("/lights/ws") as ws:
            ws.send_text(json.dumps({"type": "zones", "action": "configure",
                                     "config": _payload()}))
            configured = json.loads(ws.receive_text())
            assert configured["type"] == "zones"
            assert configured["action"] == "configure"
            assert len(configured["config"]["zones"]) == 2

            ws.send_text(json.dumps({
                "type": "zones", "action": "frame",
                "levels": {"bass": 1.0, "mid": 1.0},
                "colors": {"bass": {"r": 255, "g": 0, "b": 0}},
            }))
            frame = json.loads(ws.receive_text())
            assert frame["action"] == "frame"
            assert set(frame["applied"]) == {"lamp1", "lamp2", "strip"}

    by_device = {device: state for device, state in driver.calls}
    assert by_device["lamp1"]["brightness"] == 50  # device cap
    assert by_device["lamp1"]["r"] == 255  # browser-owned colour
    assert by_device["lamp2"]["brightness"] == 60  # group cap
    assert by_device["strip"]["brightness"] == 40  # zone cap


def test_ws_scene_and_device_cap_update():
    driver = FakeDriver()
    config = LightZoneConfig()
    app = bridge.create_app(light_drivers={"fake": driver}, zone_config=config)

    with _connect(app) as client:
        with client.websocket_connect("/lights/ws") as ws:
            ws.send_text(json.dumps({"type": "zones", "action": "configure",
                                     "config": _payload()}))
            assert json.loads(ws.receive_text())["action"] == "configure"

            ws.send_text(json.dumps({
                "type": "zones", "action": "device",
                "device_id": "lamp1", "brightness_cap": 20,
            }))
            updated = json.loads(ws.receive_text())
            assert updated["config"]["device_caps"]["lamp1"] == 20

            ws.send_text(json.dumps({"type": "zones", "action": "scene",
                                     "scene_id": "evening"}))
            scene = json.loads(ws.receive_text())
            assert scene["action"] == "scene"
            assert set(scene["applied"]) == {"lamp1", "lamp2"}

    by_device = {device: state for device, state in driver.calls}
    assert by_device["lamp1"]["brightness"] == 20
    assert by_device["lamp2"]["brightness"] == 60


def test_ws_slider_and_invalid_requests():
    driver = FakeDriver()
    config = LightZoneConfig()
    app = bridge.create_app(light_drivers={"fake": driver}, zone_config=config)

    with _connect(app) as client:
        with client.websocket_connect("/lights/ws") as ws:
            ws.send_text(json.dumps({"type": "zones", "action": "configure",
                                     "config": _payload()}))
            json.loads(ws.receive_text())

            ws.send_text(json.dumps({
                "type": "zones", "action": "slider", "zone_id": "bass_zone",
                "smoothing_ms": 500, "reactivity": 0.5,
            }))
            slider = json.loads(ws.receive_text())
            zone = next(item for item in slider["config"]["zones"]
                        if item["id"] == "bass_zone")
            assert zone["smoothing_ms"] == 500
            assert zone["reactivity"] == 0.5

            ws.send_text(json.dumps({"type": "zones", "action": "slider",
                                     "zone_id": "missing"}))
            error = json.loads(ws.receive_text())
            assert error["type"] == "error"
            assert error["detail"] == "unknown_zone"

            ws.send_text(json.dumps({"type": "zones", "action": "nope"}))
            error = json.loads(ws.receive_text())
            assert error["detail"] == "unknown_zone_action"

            ws.send_text(json.dumps({"type": "zones", "action": "get"}))
            current = json.loads(ws.receive_text())
            assert current["action"] == "get"
            assert current["config"]["global_smoothing_ms"] == 0
