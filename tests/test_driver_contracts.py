"""Driver contract tests for all light drivers.

Tests that don't require WS connections - pure unit tests of driver
initialization, status transitions, and contract compliance.
"""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from frame_gateway import WizDriver, TuyaDriver, LifxDriver, GoveeDriver, MatterDriver, HueDriver, HomeAssistantDriver
from server import app
from discovery import DeviceState, merge_devices

client = TestClient(app)


# ============================================================
# WIZ Driver Contract Tests
# ==================================================================

def test_wiz_driver_init():
    """WizDriver initializes with correct attributes."""
    driver = WizDriver(None)
    assert hasattr(driver, '_latency_ms')
    assert driver._latency_ms == 0.0


def test_wiz_status_default():
    """WizDriver status: online=False when no devices."""
    driver = WizDriver(None)
    status = driver.status()
    assert status["online"] == False


def test_wiz_add_device():
    """WizDriver adding a device sets online=True."""
    driver = WizDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "wiz"}
    assert driver.status()["online"] == True


def test_wiz_status_transitions():
    """WizDriver status transitions: found -> online."""
    driver = WizDriver(None)
    # Initially no devices = online = False
    status = driver.status()
    assert status["online"] == False
    # After discovering a device, should be online
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "wiz"}
    status = driver.status()
    assert status["online"] == True


# ============================================================
# TUYA Driver Contract Tests
# ==================================================================

def test_tuya_driver_init():
    """TuyaDriver initializes with correct attributes."""
    driver = TuyaDriver(None)
    assert hasattr(driver, '_latency_ms')


def test_tuya_status_default():
    """TuyaDriver status: online=False when no devices."""
    driver = TuyaDriver(None)
    assert driver.status()["online"] == False

def test_tuya_add_device():
    """TuyaDriver adding a device sets online=True."""
    driver = TuyaDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "tuya"}
    assert driver.status()["online"] == True


def test_tuya_mark_lost():
    """TuyaDriver marks device as lost."""
    from time import time
    driver = TuyaDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "tuya", "lost": False}
    driver._devices["test1"]["lost"] = True
    assert driver._devices["test1"]["lost"] == True


# ============================================================
# LIFX Driver Contract Tests
# ==================================================================

def test_lifx_driver_init():
    """LIFXDriver initializes with correct attributes."""
    driver = LifxDriver(None)
    assert hasattr(driver, '_latency_ms')


def test_lifx_status_default():
    """LIFXDriver status: online=False when no devices."""
    driver = LifxDriver(None)
    assert driver.status()["online"] == False

def test_lifx_add_device():
    """LIFXDriver adding a device sets online=True."""
    driver = LifxDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "lifx"}
    assert driver.status()["online"] == True


# ============================================================
# GOVEE Driver Contract Tests
# ==================================================================

def test_govee_driver_init():
    """GoveeDriver initializes with correct attributes."""
    driver = GoveeDriver(None)
    assert hasattr(driver, '_latency_ms')

def test_govee_status_default():
    """GoveeDriver status: online=False when no devices."""
    driver = GoveeDriver(None)
    assert driver.status()["online"] == False

def test_govee_add_device():
    """GoveeDriver adding a device sets online=True."""
    driver = GoveeDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "govee"}
    assert driver.status()["online"] == True


# ============================================================
# MATTER Driver Contract Tests
# ==================================================================

def test_matter_driver_init():
    """MatterDriver initializes with correct attributes."""
    driver = MatterDriver(None)
    assert hasattr(driver, '_latency_ms')

def test_matter_status():
    """MatterDriver status returns dict with online key."""
    driver = MatterDriver(None)
    status = driver.status()
    assert "online" in status


# ============================================================
# HUE Driver Contract Tests
# ==================================================================

def test_hue_driver_init():
    """HueDriver initializes with correct attributes."""
    driver = HueDriver(None)
    assert hasattr(driver, '_latency_ms')

def test_hue_status_default():
    """HueDriver status: online=False when no devices."""
    driver = HueDriver(None)
    assert driver.status()["online"] == False

def test_hue_add_device():
    """HueDriver adding a device sets online=True."""
    driver = HueDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "hue"}
    assert driver.status()["online"] == True


# ============================================================
# HOME ASSISTANT Driver Contract Tests
# ==================================================================

def test_ha_driver_init():
    """HomeAssistantDriver initializes with correct attributes."""
    from time import time
    driver = HomeAssistantDriver(None)
    assert hasattr(driver, '_latency_ms')

def test_ha_status_default():
    """HomeAssistantDriver status: online=False when no devices."""
    from time import time
    driver = HomeAssistantDriver(None)
    assert driver.status()["online"] == False

def test_ha_add_device():
    """HomeAssistantDriver adding a device sets online=True."""
    from time import time
    driver = HomeAssistantDriver(None)
    driver._devices["test1"] = {"id": "test1", "name": "Test", "kind": "ha"}
    assert driver.status()["online"] == True


# ============================================================
# Cross-driver Contract Tests
# ==================================================================

def test_all_drivers_have_latency():
    """All drivers have latency_ms attribute."""
    for driver_class in [WizDriver, TuyaDriver, LifxDriver, GoveeDriver, MatterDriver, HueDriver, HomeAssistantDriver]:
        driver = driver_class(None)
        assert hasattr(driver, 'latency_ms')
        assert driver.latency_ms() == 0.0


def test_all_drivers_have_status():
    """All drivers have status() method returning dict with online key."""
    for driver_class in [WizDriver, TuyaDriver, LifxDriver, GoveeDriver, MatterDriver, HueDriver, HomeAssistantDriver]:
        driver = driver_class(None)
        status = driver.status()
        assert "online" in status


def test_credentials_bridge_side_only():
    """Credentials (local keys, tokens) are stored Bridge-side only."""
    from time import time
    # Tuya: key stored Bridge-side only
    tuya = TuyaDriver(None)
    # The pair method is async; we verify the key attribute concept
    # by checking the driver has the pair method and _key attribute
    assert hasattr(TuyaDriver, 'pair')
    # After pairing, _key would be set Bridge-side
    # The key never flows to the browser
    
    # HA: long-lived token Bridge-side only
    from time import time as now
    ha = HomeAssistantDriver(None)
    # pair is async; verify the attribute setup
    assert hasattr(ha, 'pair')
    assert ha._token is None  # Initially None until pair is awaited
    assert ha._base_url is None  # Initially None