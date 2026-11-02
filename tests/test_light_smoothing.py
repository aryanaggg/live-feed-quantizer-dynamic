"""Min-spacing smoothing after the flash cap, through the light WS path."""

import json
import time

from fastapi.testclient import TestClient

import bridge
import frame_gateway
from frame_gateway import LightDriver

BASE_URL = "http://127.0.0.1:8765"


class FakeLightDriver(LightDriver):
    def __init__(self, max_rate_hz: float = 30.0):
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
            "rate_mode": "realtime",
            "max_rate_hz": self.max_rate_hz,
            "streaming": True,
        }


def _run_burst(driver, count, *, smoothing_ms, flash_cap_hz, hz=30.0):
    app = bridge.create_app(light_drivers={"fake": driver})
    inputs: list[float] = []
    interval = 1.0 / hz
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            start = time.monotonic()
            for index in range(count):
                luminance = 1.0 if index % 2 else 0.0
                inputs.append(luminance)
                ws.send_text(json.dumps({
                    "type": "set_state",
                    "device_id": "lamp",
                    "state": {
                        "kind": "fake",
                        "luminance": luminance,
                        "smoothing_ms": smoothing_ms,
                        "flash_cap_hz": flash_cap_hz,
                    },
                }))
                assert json.loads(ws.receive_text())["type"] == "ack"
                delay = start + (index + 1) * interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

            before_flush = list(driver.calls)
            ws.send_text(json.dumps({"type": "flush"}))
            assert json.loads(ws.receive_text())["type"] == "flushed"
            time.sleep(0.1)
            after_flush = list(driver.calls)
    return inputs, before_flush, after_flush


def _max_sends_in_one_second(times):
    best = 0
    for index, start in enumerate(times):
        end = index
        while end < len(times) and times[end] < start + 1.0:
            end += 1
        best = max(best, end - index)
    return best


def _large_transition_times(calls):
    times = []
    previous = None
    for timestamp, state in calls:
        if previous is not None and frame_gateway.is_large_transition(previous, state):
            times.append(timestamp)
        previous = state
    return times


def _sequence(calls, origin):
    return [
        (round(timestamp - origin, 3), "W" if state.get("luminance") else "B")
        for timestamp, state in calls
    ]


def test_smoothing_spreads_30hz_input_to_10hz(capsys):
    driver = FakeLightDriver(max_rate_hz=30.0)
    inputs, before_flush, after_flush = _run_burst(
        driver, 31, smoothing_ms=100.0, flash_cap_hz=0.0
    )

    times = [timestamp for timestamp, _ in before_flush]
    assert len(times) < len(inputs)
    assert len(times) <= 11  # ~1 s at 10 Hz, plus one boundary send
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps and min(gaps) >= 0.08, gaps
    assert _max_sends_in_one_second(times) <= 11
    assert after_flush[-1][1]["luminance"] == inputs[-1]

    with capsys.disabled():
        print("\nsmoothing input:", "".join("W" if value else "B" for value in inputs))
        print("smoothing output:", _sequence(after_flush, after_flush[0][0]))


def test_smoothing_never_raises_flash_cap_ceiling(capsys):
    driver = FakeLightDriver(max_rate_hz=30.0)
    inputs, _before_flush, after_flush = _run_burst(
        driver, 61, smoothing_ms=100.0, flash_cap_hz=3.0
    )

    large = _large_transition_times(after_flush)
    assert large, "no large transitions reached the driver"
    assert _max_sends_in_one_second(large) <= 3, "flash cap was exceeded"
    assert after_flush[-1][1]["luminance"] == inputs[-1]

    with capsys.disabled():
        print("\ncap+smoothing input:", "".join("W" if value else "B" for value in inputs))
        print("cap+smoothing output:", _sequence(after_flush, after_flush[0][0]))
