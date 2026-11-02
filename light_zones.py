"""User-defined light groups, frequency-band zones, scenes, and presets.

Everything here is a pure mapping layer: the browser still owns the palette and
sends the colours/levels it wants.  The Bridge only maps a band level to the
configured devices, applies per-device/per-group brightness caps and on/off
state, and forwards a normal light state to the existing coalescing controller.

No network or FastAPI imports, so the mapping rules are unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BANDS = ("bass", "mid", "high")
POSITIONS = ("left", "center", "right", "front", "back")
DEFAULT_SMOOTHING_MS = 50.0
MAX_SMOOTHING_MS = 1000.0
MAX_REACTIVITY = 4.0


class LightZoneError(ValueError):
    """Invalid zone/group/scene configuration."""


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes"}
    return default


def _as_str(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _items(raw: Any) -> list[dict]:
    """Accept either a list of records or a mapping of id -> record."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        records = []
        for key, value in raw.items():
            if isinstance(value, dict):
                record = dict(value)
                record.setdefault("id", str(key))
                records.append(record)
        return records
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []


@dataclass
class Group:
    id: str
    name: str = ""
    devices: list[str] = field(default_factory=list)
    on: bool = True
    brightness_cap: int = 100

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        group_id = _as_str(data.get("id"))
        if not group_id:
            raise LightZoneError("group_id_required")
        return cls(
            id=group_id,
            name=_as_str(data.get("name"), group_id),
            devices=[_as_str(item) for item in _list(data.get("devices")) if _as_str(item)],
            on=_as_bool(data.get("on"), True),
            brightness_cap=_clamp_int(data.get("brightness_cap"), 0, 100, 100),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "devices": list(self.devices),
            "on": self.on,
            "brightness_cap": self.brightness_cap,
        }


@dataclass
class Zone:
    id: str
    name: str = ""
    band: str = "bass"
    devices: list[str] = field(default_factory=list)
    on: bool = True
    reactivity: float = 1.0
    smoothing_ms: float = DEFAULT_SMOOTHING_MS
    brightness_cap: int = 100
    position: str = "center"

    @classmethod
    def from_dict(cls, data: dict) -> "Zone":
        zone_id = _as_str(data.get("id"))
        if not zone_id:
            raise LightZoneError("zone_id_required")
        band = _as_str(data.get("band"), "bass").lower()
        if band not in BANDS:
            raise LightZoneError("invalid_band")
        position = _as_str(data.get("position"), "center").lower()
        if position not in POSITIONS:
            raise LightZoneError("invalid_position")
        return cls(
            id=zone_id,
            name=_as_str(data.get("name"), zone_id),
            band=band,
            devices=[_as_str(item) for item in _list(data.get("devices")) if _as_str(item)],
            on=_as_bool(data.get("on"), True),
            reactivity=_clamp_float(data.get("reactivity"), 0.0, MAX_REACTIVITY, 1.0),
            smoothing_ms=_clamp_float(
                data.get("smoothing_ms"), 0.0, MAX_SMOOTHING_MS, DEFAULT_SMOOTHING_MS,
            ),
            brightness_cap=_clamp_int(data.get("brightness_cap"), 0, 100, 100),
            position=position,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "band": self.band,
            "devices": list(self.devices),
            "on": self.on,
            "reactivity": self.reactivity,
            "smoothing_ms": self.smoothing_ms,
            "brightness_cap": self.brightness_cap,
            "position": self.position,
        }


@dataclass
class Scene:
    id: str
    name: str = ""
    state: dict = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        scene_id = _as_str(data.get("id"))
        if not scene_id:
            raise LightZoneError("scene_id_required")
        raw_state = data.get("state") if isinstance(data.get("state"), dict) else {}
        state = {
            "r": _clamp_int(raw_state.get("r"), 0, 255, 0),
            "g": _clamp_int(raw_state.get("g"), 0, 255, 0),
            "b": _clamp_int(raw_state.get("b"), 0, 255, 0),
            "brightness": _clamp_int(raw_state.get("brightness"), 0, 100, 100),
            "on": _as_bool(raw_state.get("on"), True),
        }
        if "transition" in raw_state:
            state["transition"] = _clamp_float(raw_state.get("transition"), 0.0, 300.0, 0.0)
        if "smoothing_ms" in raw_state:
            state["smoothing_ms"] = _clamp_float(
                raw_state.get("smoothing_ms"), 0.0, MAX_SMOOTHING_MS,
                DEFAULT_SMOOTHING_MS,
            )
        return cls(
            id=scene_id,
            name=_as_str(data.get("name"), scene_id),
            state=state,
            targets=[_as_str(item) for item in _list(data.get("targets")) if _as_str(item)],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": dict(self.state),
            "targets": list(self.targets),
        }


@dataclass
class SpatialPreset:
    id: str
    name: str = ""
    zones: dict[str, dict] = field(default_factory=dict)
    groups: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "SpatialPreset":
        preset_id = _as_str(data.get("id"))
        if not preset_id:
            raise LightZoneError("preset_id_required")
        zones = data.get("zones") if isinstance(data.get("zones"), dict) else {}
        groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
        return cls(
            id=preset_id,
            name=_as_str(data.get("name"), preset_id),
            zones={str(key): dict(value) for key, value in zones.items()
                   if isinstance(value, dict)},
            groups={str(key): dict(value) for key, value in groups.items()
                    if isinstance(value, dict)},
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "zones": {key: dict(value) for key, value in self.zones.items()},
            "groups": {key: dict(value) for key, value in self.groups.items()},
        }


class LightZoneConfig:
    """Validated, mutable zone/scene configuration."""

    def __init__(self) -> None:
        self.groups: dict[str, Group] = {}
        self.zones: dict[str, Zone] = {}
        self.scenes: dict[str, Scene] = {}
        self.presets: dict[str, SpatialPreset] = {}
        self.device_caps: dict[str, int] = {}
        self.device_on: dict[str, bool] = {}
        self.device_kinds: dict[str, str] = {}
        self.global_smoothing_ms = DEFAULT_SMOOTHING_MS
        self.global_reactivity = 1.0

    # ------------------------------------------------------------------
    # serialization / replacement
    # ------------------------------------------------------------------
    def replace(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise LightZoneError("bad_config")
        groups = {group.id: group for group in (Group.from_dict(item) for item in _items(payload.get("groups")))}
        zones = {zone.id: zone for zone in (Zone.from_dict(item) for item in _items(payload.get("zones")))}
        for zone in zones.values():
            self._validate_refs(zone.devices, groups, zones)
        scenes = {scene.id: scene for scene in (Scene.from_dict(item) for item in _items(payload.get("scenes")))}
        presets = {preset.id: preset for preset in (SpatialPreset.from_dict(item) for item in _items(payload.get("presets")))}
        caps = payload.get("device_caps") if isinstance(payload.get("device_caps"), dict) else {}
        device_on = payload.get("device_on") if isinstance(payload.get("device_on"), dict) else {}
        kinds = payload.get("device_kinds") if isinstance(payload.get("device_kinds"), dict) else {}
        self.groups = groups
        self.zones = zones
        self.scenes = scenes
        self.presets = presets
        self.device_caps = {
            str(device): _clamp_int(cap, 0, 100, 100) for device, cap in caps.items()
        }
        self.device_on = {str(device): _as_bool(on, True) for device, on in device_on.items()}
        self.device_kinds = {str(device): _as_str(kind, "wiz") for device, kind in kinds.items()}
        self.global_smoothing_ms = _clamp_float(
            payload.get("global_smoothing_ms"), 0.0, MAX_SMOOTHING_MS,
            DEFAULT_SMOOTHING_MS,
        )
        self.global_reactivity = _clamp_float(
            payload.get("global_reactivity"), 0.0, MAX_REACTIVITY, 1.0,
        )

    def _validate_refs(self, refs: list[str], groups: dict[str, Group],
                       zones: dict[str, Zone] | None = None) -> None:
        for ref in refs:
            if ref.startswith("group:") and ref[6:] not in groups:
                raise LightZoneError("unknown_group")
            if ref.startswith("zone:") and zones is not None and ref[5:] not in zones:
                raise LightZoneError("unknown_zone")

    def to_dict(self) -> dict:
        return {
            "groups": [group.to_dict() for group in self.groups.values()],
            "zones": [zone.to_dict() for zone in self.zones.values()],
            "scenes": [scene.to_dict() for scene in self.scenes.values()],
            "presets": [preset.to_dict() for preset in self.presets.values()],
            "device_caps": dict(self.device_caps),
            "device_on": dict(self.device_on),
            "device_kinds": dict(self.device_kinds),
            "global_smoothing_ms": self.global_smoothing_ms,
            "global_reactivity": self.global_reactivity,
        }

    # ------------------------------------------------------------------
    # refs / membership
    # ------------------------------------------------------------------
    def resolve_devices(self, refs: list[str], _seen: set[str] | None = None) -> list[str]:
        """Expand ``group:<id>`` and ``zone:<id>`` refs, preserving order."""
        seen = _seen if _seen is not None else set()
        result: list[str] = []
        for ref in refs:
            ref = str(ref)
            if ref.startswith("group:"):
                group_id = ref[6:]
                if group_id in seen:
                    continue
                group = self.groups.get(group_id)
                if group is None:
                    continue
                seen.add(group_id)
                for device in self.resolve_devices(group.devices, seen):
                    if device not in result:
                        result.append(device)
            elif ref.startswith("zone:"):
                zone_id = ref[5:]
                if zone_id in seen:
                    continue
                zone = self.zones.get(zone_id)
                if zone is None:
                    continue
                seen.add(zone_id)
                for device in self.resolve_devices(zone.devices, seen):
                    if device not in result:
                        result.append(device)
            elif ref and ref not in result:
                result.append(ref)
        return result

    def _membership(self) -> dict[str, list[Group]]:
        members: dict[str, list[Group]] = {}
        for group in self.groups.values():
            for device in self.resolve_devices(group.devices):
                members.setdefault(device, []).append(group)
        return members

    def _device_limits(self, device: str, zone_cap: int,
                       members: dict[str, list[Group]]) -> tuple[int, bool]:
        groups = members.get(device, [])
        caps = [zone_cap, self.device_caps.get(device, 100)]
        caps.extend(group.brightness_cap for group in groups)
        return min(100, max(0, min(caps))), all(group.on for group in groups)

    # ------------------------------------------------------------------
    # mapping
    # ------------------------------------------------------------------
    def frame_outputs(self, levels: dict, colors: dict | None = None) -> dict[str, dict]:
        """Map ``{bass,mid,high}`` levels to per-device light states.

        ``colors`` is the browser-owned palette, e.g.
        ``{"bass": {"r": 255, "g": 0, "b": 0}, ...}``.  If a band has no
        colour, a grey value derived from the level is used so the mapping still
        works without a palette.
        """
        levels = levels if isinstance(levels, dict) else {}
        colors = colors if isinstance(colors, dict) else {}
        members = self._membership()
        outputs: dict[str, dict] = {}

        for zone in self.zones.values():
            if not zone.on:
                continue
            raw_level = _clamp_float(levels.get(zone.band, 0.0), 0.0, 1.0, 0.0)
            level = max(0.0, min(1.0, raw_level * zone.reactivity * self.global_reactivity))
            colour = colors.get(zone.band) if isinstance(colors.get(zone.band), dict) else {}
            if colour:
                r = _clamp_int(colour.get("r"), 0, 255, 0)
                g = _clamp_int(colour.get("g"), 0, 255, 0)
                b = _clamp_int(colour.get("b"), 0, 255, 0)
            else:
                grey = int(round(level * 255))
                r = g = b = grey
            base_brightness = int(round(level * 100))
            smoothing = max(zone.smoothing_ms, self.global_smoothing_ms)

            for device in self.resolve_devices(zone.devices):
                if not self.device_on.get(device, True):
                    continue
                cap, groups_on = self._device_limits(device, zone.brightness_cap, members)
                if not groups_on:
                    continue
                brightness = min(base_brightness, cap)
                state = {
                    "r": r, "g": g, "b": b,
                    "brightness": brightness,
                    "on": brightness > 0,
                    "transition": smoothing / 1000.0,
                    "smoothing_ms": smoothing,
                }
                previous = outputs.get(device)
                if previous is None or state["brightness"] > previous["brightness"]:
                    outputs[device] = state
        return outputs

    def scene_outputs(self, scene_id: str) -> dict[str, dict]:
        """Return the static per-device state for one scene."""
        scene = self.scenes.get(str(scene_id))
        if scene is None:
            raise LightZoneError("unknown_scene")
        targets = scene.targets or list(self.device_kinds) or list(self.device_caps)
        if not targets:
            # No explicit targets and no known devices: address every device
            # referenced by groups/zones so a configured scene still works.
            for zone in self.zones.values():
                targets.extend(zone.devices)
            for group in self.groups.values():
                targets.extend(group.devices)
        members = self._membership()
        outputs: dict[str, dict] = {}
        for device in self.resolve_devices(targets):
            if not self.device_on.get(device, True):
                continue
            cap, groups_on = self._device_limits(device, 100, members)
            if not groups_on:
                continue
            smoothing = max(
                _clamp_float(scene.state.get("smoothing_ms"), 0.0, MAX_SMOOTHING_MS,
                             self.global_smoothing_ms),
                self.global_smoothing_ms,
            )
            brightness = min(_clamp_int(scene.state.get("brightness"), 0, 100, 100), cap)
            if not scene.state.get("on", True):
                brightness = 0
            state = {
                "r": _clamp_int(scene.state.get("r"), 0, 255, 0),
                "g": _clamp_int(scene.state.get("g"), 0, 255, 0),
                "b": _clamp_int(scene.state.get("b"), 0, 255, 0),
                "brightness": brightness,
                "on": brightness > 0,
                "transition": scene.state.get("transition", smoothing / 1000.0),
                "smoothing_ms": smoothing,
            }
            if brightness > 0 or device not in outputs:
                outputs[device] = state
        return outputs

    # ------------------------------------------------------------------
    # mutations used by the UI sliders / on-off controls
    # ------------------------------------------------------------------
    def apply_preset(self, preset_id: str) -> None:
        preset = self.presets.get(str(preset_id))
        if preset is None:
            raise LightZoneError("unknown_preset")
        for zone_id, override in preset.zones.items():
            zone = self.zones.get(zone_id)
            if zone is None:
                continue
            if "band" in override:
                band = _as_str(override["band"]).lower()
                if band not in BANDS:
                    raise LightZoneError("invalid_band")
                zone.band = band
            if "devices" in override:
                zone.devices = [_as_str(item) for item in _list(override["devices"]) if _as_str(item)]
                self._validate_refs(zone.devices, self.groups, self.zones)
            if "position" in override:
                position = _as_str(override["position"]).lower()
                if position not in POSITIONS:
                    raise LightZoneError("invalid_position")
                zone.position = position
            if "on" in override:
                zone.on = _as_bool(override["on"], True)
            if "reactivity" in override:
                zone.reactivity = _clamp_float(override["reactivity"], 0.0, MAX_REACTIVITY, 1.0)
            if "smoothing_ms" in override:
                zone.smoothing_ms = _clamp_float(
                    override["smoothing_ms"], 0.0, MAX_SMOOTHING_MS, DEFAULT_SMOOTHING_MS,
                )
            if "brightness_cap" in override:
                zone.brightness_cap = _clamp_int(override["brightness_cap"], 0, 100, 100)
        for group_id, override in preset.groups.items():
            group = self.groups.get(group_id)
            if group is None:
                continue
            if "on" in override:
                group.on = _as_bool(override["on"], True)
            if "brightness_cap" in override:
                group.brightness_cap = _clamp_int(override["brightness_cap"], 0, 100, 100)
            if "devices" in override:
                group.devices = [_as_str(item) for item in _list(override["devices"]) if _as_str(item)]

    def set_device(self, device_id: str, brightness_cap: int | None = None,
                   on: bool | None = None, kind: str | None = None) -> None:
        device_id = _as_str(device_id)
        if not device_id:
            raise LightZoneError("device_id_required")
        if brightness_cap is not None:
            self.device_caps[device_id] = _clamp_int(brightness_cap, 0, 100, 100)
        if on is not None:
            self.device_on[device_id] = _as_bool(on, True)
        if kind is not None:
            self.device_kinds[device_id] = _as_str(kind, "wiz")

    def set_zone_sliders(self, zone_id: str, smoothing_ms: float | None = None,
                         reactivity: float | None = None,
                         brightness_cap: int | None = None,
                         on: bool | None = None) -> None:
        zone = self.zones.get(str(zone_id))
        if zone is None:
            raise LightZoneError("unknown_zone")
        if smoothing_ms is not None:
            zone.smoothing_ms = _clamp_float(
                smoothing_ms, 0.0, MAX_SMOOTHING_MS, DEFAULT_SMOOTHING_MS,
            )
        if reactivity is not None:
            zone.reactivity = _clamp_float(reactivity, 0.0, MAX_REACTIVITY, 1.0)
        if brightness_cap is not None:
            zone.brightness_cap = _clamp_int(brightness_cap, 0, 100, 100)
        if on is not None:
            zone.on = _as_bool(on, True)

    def set_global_sliders(self, smoothing_ms: float | None = None,
                           reactivity: float | None = None) -> None:
        if smoothing_ms is not None:
            self.global_smoothing_ms = _clamp_float(
                smoothing_ms, 0.0, MAX_SMOOTHING_MS, DEFAULT_SMOOTHING_MS,
            )
        if reactivity is not None:
            self.global_reactivity = _clamp_float(
                reactivity, 0.0, MAX_REACTIVITY, 1.0,
            )
