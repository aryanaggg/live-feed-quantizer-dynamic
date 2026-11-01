"""Flash-cap behaviour through the real Bridge light WebSocket path.

The browser owns the audio -> light mapping.  Here we drive the same mapping
contract the browser uses (``luminance`` 0..1) into ``/lights/ws`` and record
what a fake driver actually receives.
"""

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


def _run_strobe(driver: FakeLightDriver, flash_cap_hz: float | None = None,
                hz: float = 30.0, duration: float = 2.0):
    app = bridge.create_app(light_drivers={"fake": driver})
    inputs: list[float] = []
    interval = 1.0 / hz
    with TestClient(app, base_url=BASE_URL) as client:
        with client.websocket_connect("/lights/ws") as ws:
            start = time.monotonic()
            for index in range(int(round(hz * duration))):
                luminance = 1.0 if index % 2 else 0.0
                inputs.append(luminance)
                state = {"kind": "fake", "luminance": luminance}
                if flash_cap_hz is not None:
                    state["flash_cap_hz"] = flash_cap_hz
                ws.send_text(json.dumps({
                    "type": "set_state",
                    "device_id": "lamp",
                    "state": state,
                }))
                response = json.loads(ws.receive_text())
                assert response["type"] == "ack", response
                delay = start + (index + 1) * interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            # Let the trailing latest-state delivery run through the cap.
            time.sleep(1.2)
    return inputs, driver.calls


def _large_transition_times(calls):
    times = []
    previous = None
    for timestamp, state in calls:
        if previous is not None and frame_gateway.is_large_transition(previous, state):
            times.append(timestamp)
        previous = state
    return times


def _max_transitions_in_one_second(times):
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


def test_default_flash_cap_limits_strobe_to_3hz(capsys):
    assert frame_gateway.DEFAULT_FLASH_CAP_HZ == 3.0
    driver = FakeLightDriver(max_rate_hz=30.0)

    inputs, calls = _run_strobe(driver)

    assert calls, "fake driver received no states"
    large = _large_transition_times(calls)
    assert _max_transitions_in_one_second(large) <= 3

    with capsys.disabled():
        print("\nflash-cap default input:", "".join("W" if value else "B" for value in inputs))
        print("flash-cap default output:", _sequence(calls, calls[0][0]))


def test_flash_cap_override_raises_the_limit(capsys):
    driver = FakeLightDriver(max_rate_hz=30.0)

    inputs, calls = _run_strobe(driver, flash_cap_hz=10.0)

    assert calls, "fake driver received no states"
    large = _large_transition_times(calls)
    max_per_second = _max_transitions_in_one_second(large)
    assert max_per_second > 3, "override did not raise the default 3 Hz cap"
    assert max_per_second <= 10

    with capsys.disabled():
        print("\nflash-cap override input:", "".join("W" if value else "B" for value in inputs))
        print("flash-cap override output:", _sequence(calls, calls[0][0]))
