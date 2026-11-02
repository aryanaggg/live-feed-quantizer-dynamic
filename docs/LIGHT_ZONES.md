# Groups, band zones, scenes, and presets

`light_zones.py` is the Bridge-side mapping layer. The browser still owns the
palette: it sends `levels` for bass/mid/high and, optionally, the colours it
already computed. The Bridge only maps those values to devices and applies
caps/on-off/smoothing.

## WebSocket messages (`/lights/ws`)

All messages use `"type": "zones"`.

| Action | Payload | Result |
|---|---|---|
| `get` | — | current sanitized config |
| `configure` | `config` object | replace groups/zones/scenes/presets |
| `frame` | `levels: {bass,mid,high}`, `colors: {band:{r,g,b}}` | map to devices; returns `applied` |
| `scene` | `scene_id` | apply a static scene; returns `applied` |
| `preset` | `preset_id` | apply a spatial preset to the config |
| `device` | `device_id`, `brightness_cap?`, `on?`, `kind?` | per-device cap / on-off / driver kind |
| `slider` | `zone_id`, `smoothing_ms?`, `reactivity?`, `brightness_cap?`, `on?` | per-zone sliders |
| `global` | `smoothing_ms?`, `reactivity?` | global sliders |

Example:

```json
{
  "type": "zones",
  "action": "configure",
  "config": {
    "groups": [
      {"id": "living", "devices": ["lamp1", "lamp2"], "brightness_cap": 60}
    ],
    "zones": [
      {"id": "low", "band": "bass", "devices": ["group:living"],
       "reactivity": 1.2, "smoothing_ms": 120, "brightness_cap": 80}
    ],
    "scenes": [
      {"id": "evening", "targets": ["group:living"],
       "state": {"r": 10, "g": 20, "b": 30, "brightness": 100}}
    ],
    "presets": [
      {"id": "stereo", "zones": {"low": {"position": "left"}}}
    ],
    "device_caps": {"lamp1": 50},
    "device_on": {"lamp1": true},
    "device_kinds": {"lamp1": "wiz"},
    "global_smoothing_ms": 50
  }
}
```

```json
{
  "type": "zones",
  "action": "frame",
  "levels": {"bass": 0.8, "mid": 0.3, "high": 0.1},
  "colors": {"bass": {"r": 255, "g": 0, "b": 0}}
}
```

## Rules

- A zone selects one band (`bass`, `mid`, `high`) and targets raw device ids or
  `group:<id>` / `zone:<id>` references.
- `reactivity` (0–4) multiplies the incoming level; `smoothing_ms` is forwarded
  to the existing coalescing/smoothing controller.
- Effective brightness is
  `min(zone cap, group cap, per-device cap, level * 100)`.
- A zone, group, or device can be switched off; off groups/zones/devices produce
  no state.
- When two zones target the same device, the state with the higher brightness
  wins.
- Spatial presets are named overrides for zone position/band/devices and group
  on/off/caps; `preset` applies them to the current config.
- Scenes are static states for raw/group/zone targets and are still capped.
- Colours come from the browser. If a band has no supplied colour, the Bridge
  falls back to grey from the level so the mapping still works; it does not
  compute a palette.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_light_zones.py tests\test_light_zones_ws.py -q
```
