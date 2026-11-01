"""Latest-wins coalescing through the real Bridge light WebSocket path."""

import json
import time

from fastapi.testclient import TestClient

import bridge
from frame_gateway import LightDriver

BASE_URL = "http://127.0.0.1:8765"


class FakeLightDriver(LightDriver):
    def __init__(self, max_rate_hz: float = 2.0):
        super().__init__()
        self.max_rate_hz = max_rate_hz
        self.calls: list[tuple[float, dict]] = []

    async def set_state(self, state: dict) -> None:
        self.calls.append((time.monotonic(), dict(state)))

    def capabilities(self) -> dict:
        return {
            "supports_rgb": True,
            "supports_brightness": True,
            "supports_transition": True,
            "max_brightness": 100,
            "max_transition": 300,
            "rate_mode": "slow",
            "max_rate_hz": self.max_rate_hz,
            "streaming": True,
        }


def _max_sends_in_one_second(times):
    best = 0
    for index, start in enumerate(times):
        end = index
        while end < len(times) and times[end] < start + 1.0:
            end += 1
        best = max(best, end - index)
    return best


def _sequence(calls, origin):
    return [
        (round(timestamp - origin, 3), "W" if state.get("luminance") else "B")
        for timestamp, state in calls
    ]


def test_light_ws_coalesces_30hz_burst_to_2hz_driver(capsys):
    driver = FakeLightDriver(max_rate_hz=2.0)
    app = bridge.create_app(light_drivers={"fake": driver})
    inputs: list[float] = []
    interval = 1.0 / 30.0

    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            start = time.monotonic()
            # Odd length: the final input is black while the last 2 Hz send is
            # white, so the flush has a distinct final state to deliver.
            for index in range(59):  # 30 Hz for ~2 s
                luminance = 1.0 if index % 2 else 0.0
                inputs.append(luminance)
                ws.send_text(json.dumps({
                    "type": "set_state",
                    "device_id": "lamp",
                    "state": {"kind": "fake", "luminance": luminance},
                }))
                response = json.loads(ws.receive_text())
                assert response["type"] == "ack", response
                delay = start + (index + 1) * interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

            before_flush = list(driver.calls)
            ws.send_text(json.dumps({"type": "flush"}))
            flushed = json.loads(ws.receive_text())
            assert flushed["type"] == "flushed"
            time.sleep(0.1)
            after_flush = list(driver.calls)

    assert before_flush, "fake driver received no states"
    assert len(before_flush) < len(inputs), "burst was not coalesced"
    assert _max_sends_in_one_second([t for t, _ in before_flush]) <= 2
    assert len(before_flush) <= (2 * 2) + 1  # 2 s at 2 Hz, plus one boundary send
    assert inputs[-1] != before_flush[-1][1]["luminance"], (
        "odd burst must end on a state that was not already sent mid-stream"
    )

    assert after_flush, "fake driver received no states"
    assert after_flush[-1][1]["luminance"] == inputs[-1], "final state was not delivered"
    assert after_flush[-1][1]["luminance"] != before_flush[-1][1]["luminance"]
    assert all(call[1].get("luminance") in (0.0, 1.0) for call in after_flush)

    with capsys.disabled():
        print("\ncoalescing input:", "".join("W" if value else "B" for value in inputs))
        print("coalescing output:", _sequence(after_flush, after_flush[0][0]))


def test_light_ws_timer_delivers_final_odd_state_without_flush(capsys):
    """The trailing timer, not an explicit flush, delivers the odd final state."""
    driver = FakeLightDriver(max_rate_hz=2.0)
    app = bridge.create_app(light_drivers={"fake": driver})
    inputs: list[float] = []
    interval = 1.0 / 30.0

    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            start = time.monotonic()
            for index in range(59):  # odd length, ~2 s at 30 Hz
                luminance = 1.0 if index % 2 else 0.0
                inputs.append(luminance)
                ws.send_text(json.dumps({
                    "type": "set_state",
                    "device_id": "lamp",
                    "state": {"kind": "fake", "luminance": luminance},
                }))
                assert json.loads(ws.receive_text())["type"] == "ack"
                delay = start + (index + 1) * interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

            before_timer = list(driver.calls)
            assert before_timer, "fake driver received no states"
            assert inputs[-1] != before_timer[-1][1]["luminance"]

            # No flush message: the controller's trailing timer must deliver it.
            time.sleep(0.8)
            after_timer = list(driver.calls)

    assert after_timer[-1][1]["luminance"] == inputs[-1], "timer did not deliver the final state"
    assert after_timer[-1][1]["luminance"] != before_timer[-1][1]["luminance"]

    with capsys.disabled():
        print("\ntimer input:", "".join("W" if value else "B" for value in inputs))
        print("timer output:", _sequence(after_timer, after_timer[0][0]))
