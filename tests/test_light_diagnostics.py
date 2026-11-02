"""Per-driver latency diagnostics: measured value or 'unmeasurable'."""

import json

from fastapi.testclient import TestClient

import bridge
from frame_gateway import LightDriver

BASE_URL = "http://127.0.0.1:8765"


class FakeLightDriver(LightDriver):
    async def set_state(self, state: dict) -> None:
        return None

    def capabilities(self) -> dict:
        return {
            "supports_rgb": True,
            "supports_brightness": True,
            "supports_transition": True,
            "max_brightness": 100,
            "max_transition": 300,
            "rate_mode": "slow",
            "max_rate_hz": 1.0,
        }

    def status(self) -> dict:
        return {"online": True, "devices": [{"id": "lamp"}]}


def test_latency_diagnostics_defaults_to_unmeasurable():
    driver = FakeLightDriver()
    assert driver.latency_ms() == 0.0
    assert driver.latency_diagnostics() == "unmeasurable"


def test_latency_diagnostics_rounds_measured_value():
    driver = FakeLightDriver()
    driver.record_latency(12.345)
    assert driver.latency_diagnostics() == 12.3


def test_ws_diagnostics_reports_every_driver_latency():
    driver = FakeLightDriver()
    app = bridge.create_app(light_drivers={"fake": driver})
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            ws.send_text(json.dumps({"type": "diagnostics"}))
            message = json.loads(ws.receive_text())
            assert message["type"] == "diagnostics"
            entry = message["drivers"]["fake"]
            assert entry["latencyMs"] == "unmeasurable"
            assert entry["capabilities"]["max_rate_hz"] == 1.0
            assert entry["status"]["online"] is True

            ws.send_text(json.dumps({
                "type": "latency", "kind": "fake", "latencyMs": 7.5,
            }))
            assert json.loads(ws.receive_text())["type"] == "latency_ack"

            ws.send_text(json.dumps({"type": "diagnostics"}))
            updated = json.loads(ws.receive_text())
            assert updated["drivers"]["fake"]["latencyMs"] == 7.5
