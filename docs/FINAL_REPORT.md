# Final corrective report

- Branch: `main`
- HEAD before this report commit: `6d07794`
- Baseline before the corrective phase: `354060b` (phase 2)
- Final test command: `.\.venv\Scripts\python.exe -m pytest -q` from the repo root, no path arguments
- Final result: `110 passed, 13 warnings in 11.39s`
- Collection result: `110 tests collected in 1.28s`

## Result table

| Item | Status | Evidence |
|---|---|---|
| 1. Contract-test discovery | done | `pyproject.toml` uses `testpaths`/`python_files`; collection shows `tests/test_driver_contracts.py: 25`; all 25 IDs listed below; total 110 |
| 2. TTL / `last_seen` reset semantics | done | `observation_key = (device_id, observed_at)`; 3 merge tests pass and assert the exact three cases |
| 3. Flash cap through light WS path | done | 30 Hz black/white for 2 s -> default cap 3/s; override 10 Hz -> >3/s and <=10/s; input/output shown below |
| 4. Driver rate limit / coalescing | done | 30 Hz for 2 s -> fake 2 Hz driver gets 4 sends; max 1 s window <=2; final state delivered; every response is `ack`/`flushed` |
| 5. Hue pairing-only capabilities | done | `capabilities()` returns `streaming=False`, `rate_mode="unavailable"`, `max_rate_hz=0.0`; frontend renders `pairing only`; stale 30 Hz notes corrected |
| 6. Matter note | done | `docs/MATTER_FEASIBILITY.md`; only sourced projects; `pymatter` and `chip-fabric` removed |
| 7. Test-name diff / "90" claim | done | baseline 43 functions -> current 95 functions; 52 added, 0 deleted, 0 renamed; "90" retracted |
| 8. WebRTC isolation | done | `git ls-files` on `main` has no `test_signaling`; `feature/tier0-webrtc` has `tests/test_signaling.py`; branch/status output below |

## Item 1 - contract-test discovery

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test"
python_functions = "test_"
```

`pytest --collect-only -q` per-file counts (root run, no path arguments):

```text
tests/test_bridge.py: 6
tests/test_driver_contracts.py: 25
tests/test_frame_gateway.py: 10
tests/test_frontend_static.py: 6
tests/test_hue_pairing_only.py: 2
tests/test_light_coalescing.py: 1
tests/test_light_flash_cap.py: 2
tests/test_merge_coalesce.py: 4
tests/test_music_visualizer.py: 6
tests/test_quantizer.py: 4
tests/test_security.py: 30
tests/test_server_ws.py: 6
tests/test_sound_device_processor.py: 8
```

All 25 contract tests, grouped by driver:

- Wiz (4): `test_wiz_driver_init`, `test_wiz_status_default`, `test_wiz_add_device`, `test_wiz_status_transitions`
- Tuya (4): `test_tuya_driver_init`, `test_tuya_status_default`, `test_tuya_add_device`, `test_tuya_mark_lost`
- LIFX (3): `test_lifx_driver_init`, `test_lifx_status_default`, `test_lifx_add_device`
- Govee (3): `test_govee_driver_init`, `test_govee_status_default`, `test_govee_add_device`
- Matter (2): `test_matter_driver_init`, `test_matter_status`
- Hue (3): `test_hue_driver_init`, `test_hue_status_default`, `test_hue_add_device`
- Home Assistant (3): `test_ha_driver_init`, `test_ha_status_default`, `test_ha_add_device`
- Cross-driver (3): `test_all_drivers_have_latency`, `test_all_drivers_have_status`, `test_credentials_bridge_side_only`

Final line of the full root run:

```text
110 passed, 13 warnings in 11.39s
```

## Item 2 - TTL and `last_seen`

Semantics implemented in `discovery/__init__.py`:

- Observation key: `(device_id, observed_at)`.
- `observed_at` defaults to the `DeviceState` creation time, so every freshly created discovery result is a new observation.
- Observed fields: `kind`, `name`, and every key in `kwargs`. `observed_at` is deliberately excluded from the field comparison.
- Same observation re-merged with identical fields: `seen_count` unchanged, `last_seen` unchanged.
- Same device with a different `observed_at`: `seen_count += 1`, `last_seen` reset.
- Same observation with a changed field: `last_seen` reset, `seen_count` unchanged.

Relevant code:

```python
@property
def observation_key(self) -> tuple[str, float]:
    """Identity of this discovery observation: (device_id, observed_at)."""
    return (self.device_id, self.observed_at)

def _observation_fields(device: DeviceState) -> dict:
    fields = {"kind": device.kind, "name": device.name}
    fields.update(device.kwargs)
    return fields

same_observation = key in current._observation_keys
changed = fields != current._merged_fields

if same_observation:
    if not changed:
        # Exact re-merge of the same observation: TTL stays untouched.
        continue
    current.last_seen = time.time()
else:
    current.seen_count += 1
    current.last_seen = time.time()
    current._observation_keys.add(key)
```

Test run:

```text
tests/test_merge_coalesce.py::test_same_observation_remerge_keeps_seen_count_and_ttl PASSED
tests/test_merge_coalesce.py::test_new_observation_increments_seen_count_and_resets_ttl PASSED
tests/test_merge_coalesce.py::test_genuine_field_update_resets_ttl_without_incrementing_seen_count PASSED
3 passed, 107 deselected
```

Exact assertions:

- (a) `seen_count == 4` and `last_seen == original_last_seen` after re-merging the identical observation.
- (b) `seen_count == 2` and `last_seen > old_last_seen` after a new `observed_at`.
- (c) `seen_count == 1` and `last_seen > old_last_seen` after a changed `url` with the same `observed_at`.

## Item 3 - flash cap through the light WebSocket path

Path exercised by `tests/test_light_flash_cap.py`:

`/lights/ws` -> `LightStreamController` -> `normalize_light_state` (luminance -> grey RGB) -> fake `LightDriver.set_state`.

Default run (no `flash_cap_hz` in the state):

```text
flash-cap default input: BWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBW
flash-cap default output: [(0.0, 'B'), (0.063, 'W'), (0.109, 'B'), (0.172, 'W'), (0.25, 'W'), (0.313, 'W'), (0.375, 'W'), (0.453, 'W'), (0.516, 'W'), (0.578, 'W'), (0.641, 'W'), (0.719, 'W'), (0.766, 'W'), (0.844, 'W'), (0.906, 'W'), (0.969, 'W'), (1.047, 'W'), (1.094, 'B'), (1.141, 'W'), (1.203, 'B'), (1.281, 'B'), (1.344, 'B'), (1.406, 'B'), (1.469, 'B'), (1.547, 'B'), (1.609, 'B'), (1.688, 'B'), (1.734, 'B'), (1.813, 'B'), (1.859, 'B'), (1.938, 'B'), (2.094, 'W')]
```

Override run (`flash_cap_hz=10.0`):

```text
flash-cap override input: BWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBW
flash-cap override output: [(0.0, 'B'), (0.047, 'W'), (0.094, 'B'), (0.14, 'W'), (0.187, 'W'), (0.234, 'B'), (0.281, 'W'), (0.328, 'B'), (0.39, 'W'), (0.437, 'B'), (0.484, 'W'), (0.531, 'B'), (0.609, 'B'), (0.672, 'B'), (0.75, 'B'), (0.812, 'B'), (0.875, 'B'), (0.937, 'B'), (1.0, 'B'), (1.047, 'W'), (1.094, 'B'), (1.14, 'W'), (1.234, 'B'), (1.281, 'W'), (1.344, 'B'), (1.39, 'W'), (1.437, 'B'), (1.515, 'W'), (1.562, 'B'), (1.609, 'B'), (1.687, 'B'), (1.75, 'B'), (1.797, 'B'), (1.875, 'B'), (1.937, 'B'), (2.047, 'W')]
```

Assertions:

- `DEFAULT_FLASH_CAP_HZ == 3.0`, so the cap is on by default.
- Default: maximum large transitions in any 1 s window `<= 3`.
- Override 10 Hz: maximum large transitions in any 1 s window `> 3` and `<= 10`.
- A large transition is a luminance delta `>= 0.5` or RGB distance `>= 128`.

Test result: `2 passed, 108 deselected`.

## Item 4 - driver rate limit and coalescing

`tests/test_light_coalescing.py` sends 60 alternating black/white states at 30 Hz for 2 s to `/lights/ws` with a fake driver whose `max_rate_hz=2.0`.

```text
coalescing input: BWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBWBW
coalescing output: [(0.0, 'B'), (0.5, 'W'), (1.016, 'B'), (1.516, 'W')]
1 passed, 109 deselected
```

Assertions:

- `len(before_flush) < len(inputs)` - the burst was coalesced.
- `_max_sends_in_one_second(...) <= 2` - the driver never receives more than 2 sends in a 1 s window.
- `len(before_flush) <= 5` - 2 s at 2 Hz plus one boundary send.
- `after_flush[-1][1]["luminance"] == inputs[-1]` - the final state is delivered.
- Every input receives `{"type": "ack"}`; the `flush` control receives `{"type": "flushed"}`. No error response is accepted.

The existing frame-gateway coalescing test (`tests/test_frame_gateway.py::test_frame_coalescing_keeps_latest`) remains in the suite.

## Item 5 - Hue is pairing only

`HueDriver(None).capabilities()`:

```text
{'supports_rgb': True, 'supports_brightness': True, 'supports_transition': True, 'max_brightness': 100, 'max_transition': 300, 'rate_mode': 'unavailable', 'max_rate_hz': 0.0, 'streaming': False}
```

Frontend (`frontend/index.html`):

```javascript
function lightDriverModeLabel(caps) {
  if (!caps) return 'unknown';
  if (caps.streaming === false || caps.rate_mode === 'unavailable' || !(caps.max_rate_hz > 0)) return 'pairing only';
  return `${caps.rate_mode || 'slow'} - ${caps.max_rate_hz} Hz`;
}
function renderLightDriverModes(capsByDriver) {
  ...
  `<div class="hintline">${escapeHtml(name)}: <span class="badge">${escapeHtml(lightDriverModeLabel(caps))}</span></div>`
}
```

Tests:

```text
tests/test_hue_pairing_only.py::test_hue_capabilities_are_pairing_only PASSED
tests/test_hue_pairing_only.py::test_hue_capabilities_over_the_light_websocket PASSED
tests/test_frontend_static.py::test_frontend_renders_light_driver_modes_from_capabilities PASSED
tests/test_frontend_static.py::test_hue_capabilities_render_as_pairing_only PASSED
6 passed, 104 deselected
```

The stale status notes were corrected in `docs/DRIVER_STATUS.md` and `docs/MATTER_HUE_STATUS.md`: they now say `streaming=False`, `rate_mode="unavailable"`, `max_rate_hz=0.0`, and explicitly state that no realtime 30 Hz claim is supported.

## Item 6 - Matter feasibility note

Final note text is in `docs/MATTER_FEASIBILITY.md`. It says, in substance:

> Status: feasibility note only. The Matter driver is a stub: it advertises the interface, returns no devices from discovery, and logs `set_state()` intent. It performs no commissioning, no DTLS session setup, and no LAN control. A real implementation needs a controller that commissions over Bluetooth LE and drives devices over operational IP (IPv6, mDNS/SRP, CASE). The practical shape for this project is a sidecar Matter server with a WebSocket API, with the Python bridge as a client.

Sourced library table:

| Library / project | Project URL | Maintainer | Last release | Source |
|---|---|---|---|---|
| `python-matter-server` | https://github.com/matter-js/python-matter-server (PyPI: https://pypi.org/project/python-matter-server/) | Open Home Foundation (matter-js org); funded by Nabu Casa | 8.1.2, 2025-12-15 | GitHub repo metadata + PyPI JSON API |
| `matterjs-server` (`matter-server` on npm) | https://github.com/matter-js/matterjs-server (npm: https://www.npmjs.com/package/matter-server) | Open Home Foundation; npm maintainers `balloob` and `apollon77` | 1.5.5, 2026-10-24 | GitHub Releases API + npm registry JSON |

Evidence:

```text
pypi python-matter-server latest=8.1.2 released=2025-12-15T14:56:46.695703Z
github archived=True pushed=2026-06-23T07:49:36Z url=https://github.com/matter-js/python-matter-server
npm matter-server latest=1.5.5 time=2026-10-24T13:09:06.417Z author=Open Home Foundation
maintainers=balloob, apollon77
github latest tag=v1.5.5 published=2026-10-24T13:14:50Z
```

Removed names:

- `pymatter`: PyPI summary is "Python bindings for R/Bioconductor 'matter' package"; author email `"Kylie A. Bemis" <k.bemis@northeastern.edu>`; last release 2024-12-24. It is unrelated to Matter smart-home work.
- `chip-fabric`: `https://pypi.org/pypi/chip-fabric/json` returns HTTP 404; no project or package was found.

The web-search tool returned `{"error":{"type":"unknown","message":"Web search cancelled"}}` on every attempt. The dates and maintainers above come from direct fetches of the official GitHub API, PyPI JSON API, and npm registry JSON, not from a search-engine snippet.

## Item 7 - test-name diff and the "90" claim

Baseline commit used for the 43-function diff: **`354060b`** (phase 2, immediately before the corrective phase). Baseline vs current `main`:

| Measure | Baseline | Current |
|---|---:|---:|
| Test functions | 43 | 95 |
| Pytest items | not re-run at baseline | 110 |
| Test files | 6 | 13 |

Diff:

- Added: 52 test functions (51 unique names; `test_frame_coalescing_keeps_latest` exists in both `tests/test_frame_gateway.py` and `tests/test_merge_coalesce.py`).
- Deleted: 0 test functions.
- Renamed: 0 test functions.

Added by file:

```text
tests/test_bridge.py: 6
tests/test_driver_contracts.py: 25
tests/test_frame_gateway.py: 10
tests/test_frontend_static.py: 2
tests/test_hue_pairing_only.py: 2
tests/test_light_coalescing.py: 1
tests/test_light_flash_cap.py: 2
tests/test_merge_coalesce.py: 4
```

New test files: `test_bridge.py`, `test_driver_contracts.py`, `test_frame_gateway.py`, `test_hue_pairing_only.py`, `test_light_coalescing.py`, `test_light_flash_cap.py`, `test_merge_coalesce.py`.

The earlier "90" figure is retracted. No report file in the repository contains a supported count of 90. A repository-wide search for standalone `90` found only JPEG quality `90` in test helpers (plus `node_modules` and an unrelated architecture note). The supported counts are 43 baseline test functions, 95 current test functions, and 110 collected/passed pytest items. Earlier claims of "88 + 25 = 113" and "22 signaling tests" were also wrong; `tests/test_signaling.py` on `feature/tier0-webrtc` contains 10 test functions.

## Item 8 - WebRTC isolation

```text
$ git branch --show-current
main

$ git ls-files | Select-String 'test_signaling'
(no matches)

$ git ls-tree -r --name-only feature/tier0-webrtc | Select-String 'test_signaling'

tests/test_signaling.py

$ git branch -a
  backup-before-fix
  feature/tier0-webrtc
* main
  remotes/origin/HEAD -> origin/main
  remotes/origin/main
```

`git status --short` before this report commit showed only untracked scratch files (`all_tests.txt`, `pytest_*.txt`, `summary*.txt`, `security_*.py`, `discovery/__main__.py`, etc.). None of those scratch files were committed. No code commit was needed for item 8.

## Tests added and not-done items

- Test functions added against baseline: 52.
- Final suite: 110 passed, 13 warnings.
- Not done: real Matter commissioning/LAN control; the driver remains a feasibility stub.
- Not done: real-hardware verification for Hue, Matter, or the other light drivers.
- Not done: the WebRTC/signaling work is intentionally not on `main`; it remains on `feature/tier0-webrtc`.
- Caveat: the web-search tool was unavailable; official project metadata was fetched directly instead.
