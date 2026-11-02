"""Hardware-path tests that do not need a real bulb.

These cover the WiZ JSON ack path, the Tuya tinytuya wiring (with a fake
module), and the ``tools/hw_smoke.py`` report shape.
"""

import asyncio
import json
import socket
import sys
import threading
import types

import pytest

import frame_gateway
from frame_gateway import (
    LightDriverError,
    TuyaDriver,
    WizDriver,
    _tuya_scan,
    _wiz_request,
)
from tools import hw_smoke


def test_wiz_request_measures_real_udp_ack():
    captured: list[dict] = []

    def serve(server: socket.socket) -> None:
        data, addr = server.recvfrom(4096)
        captured.append(json.loads(data.decode("utf-8")))
        server.sendto(json.dumps({"method": "setPilot",
                                  "result": {"success": True}}).encode(), addr)
        server.close()

    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    thread = threading.Thread(target=serve, args=(server,))
    thread.start()

    response, elapsed_ms, peer = _wiz_request(
        "127.0.0.1", port, "setPilot",
        {"r": 1, "g": 2, "b": 3, "dimming": 40}, timeout=2.0,
    )
    thread.join(timeout=2.0)

    assert response["result"]["success"] is True
    assert elapsed_ms >= 0.0
    assert peer[0] == "127.0.0.1"
    assert captured[0]["method"] == "setPilot"
    assert captured[0]["params"]["dimming"] == 40


def test_wiz_request_timeout_raises_driver_error():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    try:
        with pytest.raises(LightDriverError, match="wiz_io_error"):
            _wiz_request("127.0.0.1", port, "getPilot", {}, timeout=0.05)
    finally:
        server.close()


def test_wiz_set_state_records_ack_latency(monkeypatch):
    driver = WizDriver(None)
    driver._devices["wiz:abc"] = {
        "id": "wiz:abc",
        "address": "wiz://127.0.0.1:38899",
        "name": "Test",
        "kind": "wiz",
    }
    calls: list[tuple] = []

    def fake_request(host, port, method, params, timeout):
        calls.append((host, port, method, params))
        return {"result": {"success": True}}, 8.26, (host, port)

    monkeypatch.setattr(frame_gateway, "_wiz_request", fake_request)
    asyncio.run(driver.set_state({
        "device_id": "wiz:abc", "r": 255, "g": 0, "b": 0,
        "brightness": 40, "transition": 0,
    }))

    assert calls[0][2] == "setPilot"
    assert calls[0][3] == {"r": 255, "g": 0, "b": 0, "dimming": 40, "state": True}
    assert driver.latency_diagnostics() == 8.3


def test_wiz_set_state_rejects_failed_ack(monkeypatch):
    driver = WizDriver(None)
    driver._devices["wiz:abc"] = {
        "id": "wiz:abc", "address": "wiz://127.0.0.1:38899", "name": "Test",
    }
    monkeypatch.setattr(
        frame_gateway, "_wiz_request",
        lambda *args, **kwargs: ({"result": {"success": False}}, 1.0, ("x", 1)),
    )
    with pytest.raises(LightDriverError, match="wiz_ack_failed"):
        asyncio.run(driver.set_state({"device_id": "wiz:abc", "r": 1}))


def test_tuya_scan_uses_device_scan_and_gateway_id(monkeypatch):
    fake = types.ModuleType("tinytuya")
    seen: dict = {}

    def device_scan(**kwargs):
        seen.update(kwargs)
        return {
            "192.168.1.9": {
                "gwId": "gw-123", "version": 3.4, "productKey": "p1",
                "name": "Desk lamp",
            },
        }

    fake.deviceScan = device_scan
    monkeypatch.setitem(sys.modules, "tinytuya", fake)

    fixtures = asyncio.run(_tuya_scan(timeout=2))

    assert seen["poll"] is False
    assert fixtures == [{
        "id": "tuya:gw-123",
        "device_id": "gw-123",
        "address": "tuya://192.168.1.9",
        "ip": "192.168.1.9",
        "name": "Desk lamp",
        "model": "p1",
        "version": 3.4,
        "kind": "tuya",
    }]


class _FakeBulb:
    last_instance = None

    def __init__(self, device_id, host, local_key):
        self.device_id = device_id
        self.host = host
        self.local_key = local_key
        self.calls: list[tuple] = []
        self.closed = False
        _FakeBulb.last_instance = self

    def set_version(self, version):
        self.calls.append(("version", version))

    def set_socketTimeout(self, timeout):
        self.calls.append(("timeout", timeout))

    def set_socketPersistent(self, persistent):
        self.calls.append(("persistent", persistent))

    def set_colour(self, r, g, b):
        self.calls.append(("colour", r, g, b))
        return {"dps": {"24": "ff0000"}}

    def set_brightness_percentage(self, brightness):
        self.calls.append(("brightness", brightness))
        return {"dps": {"22": brightness}}

    def close(self):
        self.closed = True


def _install_fake_tinytuya(monkeypatch):
    fake = types.ModuleType("tinytuya")
    fake.BulbDevice = _FakeBulb
    monkeypatch.setitem(sys.modules, "tinytuya", fake)


def test_tuya_set_state_sends_key_only_to_device(monkeypatch):
    _install_fake_tinytuya(monkeypatch)
    driver = TuyaDriver(None)
    driver._devices["tuya:gw-1"] = {
        "id": "tuya:gw-1", "device_id": "gw-1", "ip": "192.168.1.9",
        "version": 3.4, "name": "Lamp", "kind": "tuya",
    }
    asyncio.run(driver.pair({"local_key": "top-secret-key"}))
    asyncio.run(driver.set_state({
        "device_id": "tuya:gw-1", "r": 10, "g": 20, "b": 30,
        "brightness": 40, "transition": 0,
    }))

    bulb = _FakeBulb.last_instance
    assert bulb.device_id == "gw-1"
    assert bulb.host == "192.168.1.9"
    assert bulb.local_key == "top-secret-key"
    assert ("colour", 10, 20, 30) in bulb.calls
    assert ("brightness", 40) in bulb.calls
    assert bulb.closed is True
    assert driver.latency_diagnostics() != "unmeasurable"
    assert "top-secret-key" not in json.dumps(driver.status())


def test_tuya_set_state_requires_key(monkeypatch):
    _install_fake_tinytuya(monkeypatch)
    driver = TuyaDriver(None)
    driver._devices["tuya:gw-1"] = {
        "id": "tuya:gw-1", "device_id": "gw-1", "ip": "192.168.1.9",
    }
    with pytest.raises(LightDriverError, match="needs_credentials"):
        asyncio.run(driver.set_state({"device_id": "tuya:gw-1", "r": 1}))


class _SmokeDriver(frame_gateway.LightDriver):
    def __init__(self, bridge=None):
        super().__init__()
        self.calls: list[dict] = []
        self.credentials = None

    async def discover(self):
        return [{
            "id": "tuya:gw-1", "device_id": "gw-1",
            "address": "tuya://192.168.1.9", "name": "Lamp", "kind": "tuya",
        }]

    async def pair(self, credentials=None):
        self.credentials = credentials
        return "token"

    async def set_state(self, state):
        self.calls.append(dict(state))
        self.record_latency(4.0 + len(self.calls) / 10)

    def capabilities(self):
        return {"max_rate_hz": 1.0, "rate_mode": "slow"}

    def status(self):
        return {"online": True, "devices": [{"id": "tuya:gw-1"}]}


def test_hw_smoke_reports_per_driver_ack_and_never_prints_key(monkeypatch, capsys):
    monkeypatch.setitem(hw_smoke.DRIVER_FACTORIES, "tuya", _SmokeDriver)

    async def no_probe(*args, **kwargs):
        return None

    monkeypatch.setattr(hw_smoke, "_async_probe", no_probe)

    exit_code = hw_smoke.main([
        "--driver", "tuya", "--local-key", "TOPSECRET", "--json",
    ])
    output = capsys.readouterr().out
    reports = json.loads(output)

    assert exit_code == 0
    assert "TOPSECRET" not in output
    report = reports[0]
    assert report["driver"] == "tuya"
    assert report["discovered"] == 1
    assert report["paired"] is True
    assert report["color_ok"] is True
    assert len(report["colors"]) == 3
    assert report["colors"][0]["ack_ms"] == 4.1
    assert report["ack_latency_ms"] == 4.2
    assert report["probe_latency_ms"] == "unmeasurable"
    assert report["status"]["online"] is True
