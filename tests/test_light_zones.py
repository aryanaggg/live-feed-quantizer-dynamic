"""Pure mapping tests for groups, band zones, scenes, presets, and caps."""

import pytest

from light_zones import LightZoneConfig, LightZoneError


def _config() -> LightZoneConfig:
    config = LightZoneConfig()
    config.replace({
        "groups": [
            {"id": "living", "name": "Living room",
             "devices": ["lamp1", "lamp2"], "brightness_cap": 60},
        ],
        "zones": [
            {"id": "bass_zone", "band": "bass", "devices": ["group:living"],
             "brightness_cap": 100, "smoothing_ms": 120},
            {"id": "mid_zone", "band": "mid", "devices": ["strip"],
             "reactivity": 2.0, "brightness_cap": 80},
            {"id": "high_zone", "band": "high", "devices": ["spot"], "on": False},
        ],
        "scenes": [
            {"id": "evening", "targets": ["group:living"],
             "state": {"r": 10, "g": 20, "b": 30, "brightness": 100}},
            {"id": "strip_red", "targets": ["zone:mid_zone"],
             "state": {"r": 255, "g": 0, "b": 0, "brightness": 90}},
        ],
        "presets": [
            {"id": "stereo", "name": "Stereo",
             "zones": {"bass_zone": {"position": "left", "devices": ["lamp1"],
                                     "brightness_cap": 40}},
             "groups": {"living": {"on": False}}},
        ],
        "device_caps": {"lamp1": 50, "lamp2": 100, "strip": 70},
        "device_on": {"lamp1": True, "lamp2": False, "strip": True, "spot": True},
        "device_kinds": {"lamp1": "fake", "lamp2": "fake", "strip": "fake",
                         "spot": "fake"},
        "global_smoothing_ms": 100,
        "global_reactivity": 1.0,
    })
    return config


def test_band_mapping_and_caps():
    outputs = _config().frame_outputs({"bass": 0.8, "mid": 0.5, "high": 1.0})

    # bass: group cap 60 and device cap 50 win; lamp2 is off; high zone is off.
    assert set(outputs) == {"lamp1", "strip"}
    assert outputs["lamp1"]["brightness"] == 50
    assert outputs["lamp1"]["r"] == outputs["lamp1"]["g"] == outputs["lamp1"]["b"] == 204
    assert outputs["lamp1"]["smoothing_ms"] == 120
    assert outputs["lamp1"]["transition"] == 0.12
    # mid: reactivity 2.0 turns 0.5 into full level, then the device cap wins.
    assert outputs["strip"]["brightness"] == 70
    assert outputs["strip"]["r"] == 255


def test_client_owned_colours_pass_through():
    outputs = _config().frame_outputs(
        {"bass": 0.5, "mid": 0.25},
        {"bass": {"r": 1, "g": 2, "b": 3}},
    )
    assert outputs["lamp1"]["r"] == 1
    assert outputs["lamp1"]["g"] == 2
    assert outputs["lamp1"]["b"] == 3
    # No colour supplied for mid: grey from the level, not a Bridge palette.
    assert outputs["strip"]["r"] == outputs["strip"]["g"] == outputs["strip"]["b"] == 128


def test_highest_brightness_wins_when_zones_overlap():
    config = _config()
    config.replace({
        "zones": [
            {"id": "a", "band": "bass", "devices": ["lamp1"], "brightness_cap": 100},
            {"id": "b", "band": "bass", "devices": ["lamp1"], "brightness_cap": 30},
        ],
        "device_caps": {"lamp1": 100},
    })
    outputs = config.frame_outputs({"bass": 0.9})
    assert outputs["lamp1"]["brightness"] == 90


def test_group_and_zone_on_off_and_device_cap():
    config = _config()
    config.groups["living"].on = False
    assert "lamp1" not in config.frame_outputs({"bass": 1.0})

    config.groups["living"].on = True
    config.set_zone_sliders("bass_zone", on=False)
    assert "lamp1" not in config.frame_outputs({"bass": 1.0})

    config.set_zone_sliders("bass_zone", on=True)
    config.set_device("lamp1", brightness_cap=5)
    assert config.frame_outputs({"bass": 1.0})["lamp1"]["brightness"] == 5

    config.set_device("lamp1", on=False)
    assert "lamp1" not in config.frame_outputs({"bass": 1.0})


def test_scene_targets_groups_zones_and_caps():
    config = _config()
    outputs = config.scene_outputs("evening")
    assert set(outputs) == {"lamp1"}  # lamp2 is off
    assert outputs["lamp1"]["brightness"] == 50  # device cap
    assert (outputs["lamp1"]["r"], outputs["lamp1"]["g"], outputs["lamp1"]["b"]) == (10, 20, 30)

    strip = config.scene_outputs("strip_red")["strip"]
    assert strip["brightness"] == 70  # device cap
    assert (strip["r"], strip["g"], strip["b"]) == (255, 0, 0)

    with pytest.raises(LightZoneError, match="unknown_scene"):
        config.scene_outputs("missing")


def test_scene_off_and_global_smoothing():
    config = _config()
    config.scenes["evening"].state["on"] = False
    assert config.scene_outputs("evening")["lamp1"]["brightness"] == 0
    config.set_global_sliders(smoothing_ms=400)
    assert config.scene_outputs("evening")["lamp1"]["smoothing_ms"] == 400


def test_spatial_preset_and_sliders():
    config = _config()
    config.apply_preset("stereo")
    zone = config.zones["bass_zone"]
    assert zone.position == "left"
    assert zone.devices == ["lamp1"]
    assert zone.brightness_cap == 40
    assert config.groups["living"].on is False

    config.set_zone_sliders("bass_zone", smoothing_ms=500, reactivity=0.5,
                            brightness_cap=20, on=True)
    config.set_global_sliders(smoothing_ms=0, reactivity=2.0)
    config.groups["living"].on = True
    output = config.frame_outputs({"bass": 0.5})["lamp1"]
    assert output["brightness"] == 20  # zone cap
    assert output["smoothing_ms"] == 500
    assert output["transition"] == 0.5


def test_group_refs_resolve_without_cycles():
    config = LightZoneConfig()
    config.replace({
        "groups": [
            {"id": "a", "devices": ["group:b", "lamp"]},
            {"id": "b", "devices": ["group:a"]},
        ],
    })
    assert config.resolve_devices(["group:a"]) == ["lamp"]


def test_invalid_config_is_rejected():
    config = LightZoneConfig()
    with pytest.raises(LightZoneError, match="invalid_band"):
        config.replace({"zones": [{"id": "z", "band": "sub"}]})
    with pytest.raises(LightZoneError, match="invalid_position"):
        config.replace({"zones": [{"id": "z", "band": "bass", "position": "up"}]})
    with pytest.raises(LightZoneError, match="unknown_group"):
        config.replace({"zones": [{"id": "z", "band": "bass", "devices": ["group:missing"]}]})
    with pytest.raises(LightZoneError, match="unknown_zone"):
        config.set_zone_sliders("missing", reactivity=1.0)


def test_mapping_style_config_is_accepted():
    config = LightZoneConfig()
    config.replace({
        "groups": {"living": {"devices": ["lamp"]}},
        "zones": {"bass_zone": {"band": "bass", "devices": ["group:living"]}},
    })
    assert config.zones["bass_zone"].id == "bass_zone"
    assert config.frame_outputs({"bass": 1.0})["lamp"]["brightness"] == 100
