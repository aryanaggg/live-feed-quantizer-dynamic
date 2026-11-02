"""Tuya local-key onboarding: guided cloud flow, Bridge-side storage, no leaks."""

import logging

import pytest
from fastapi.testclient import TestClient

import bridge
from frame_gateway import TuyaDriver
from security import RateLimiter
from tuya_onboarding import TuyaCloudClient, TuyaCloudError, TuyaCredentialStore

BASE_URL = "http://127.0.0.1:8765"
ORIGIN = "http://localhost:5500"
DEVICE_ID = "gw-1"
SECRET_KEY = "local-key-that-must-never-leak"


class FakeCloud:
    def __init__(self, client_id, client_secret, region):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region

    def fetch_local_key(self, device_id):
        assert device_id == DEVICE_ID
        return ("Desk lamp", SECRET_KEY)


class FailingCloud(FakeCloud):
    def fetch_local_key(self, device_id):
        raise TuyaCloudError("cloud_code_1106")


def _make_app(tmp_path, cloud_factory=FakeCloud):
    store = TuyaCredentialStore(tmp_path / "state" / "tuya_credentials.json")
    driver = TuyaDriver(None, credential_store=store)
    driver._devices[f"tuya:{DEVICE_ID}"] = {
        "id": f"tuya:{DEVICE_ID}",
        "device_id": DEVICE_ID,
        "address": "tuya://192.168.1.9",
        "ip": "192.168.1.9",
        "name": "Desk lamp",
        "version": 3.4,
        "kind": "tuya",
    }
    app = bridge.create_app(
        light_drivers={"tuya": driver},
        tuya_store=store,
        tuya_cloud_factory=cloud_factory,
    )
    bridge.tuya_onboard_limiter = RateLimiter(limit=100, window=60)
    return app, store, driver


def _pair(client: TestClient) -> dict:
    response = client.post("/pair", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-Bridge-Token": response.json()["token"]}


def test_onboarding_requires_pairing_token(tmp_path):
    app, _, _ = _make_app(tmp_path)
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.get("/lights/tuya/onboarding", headers={"Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["error"] == "pairing_required"


def test_guided_flow_needs_credentials_then_ready(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    app, store, driver = _make_app(tmp_path)

    with TestClient(app, base_url=BASE_URL) as client:
        headers = _pair(client)

        before = client.get("/lights/tuya/onboarding", headers=headers)
        assert before.status_code == 200
        body = before.json()
        assert body["state"] == "needs_credentials"
        assert body["configured"] == 0
        assert body["devices"][0]["state"] == "needs_credentials"
        assert body["devices"][0]["needs_credentials"] is True
        assert body["regions"] == ["cn", "eu", "in", "us"]
        assert len(body["steps"]) == 5

        submitted = client.post(
            "/lights/tuya/onboarding",
            headers=headers,
            json={
                "client_id": "project-id",
                "client_secret": "project-secret",
                "region": "eu",
                "device_id": DEVICE_ID,
            },
        )
        assert submitted.status_code == 200
        result = submitted.json()
        assert result["state"] == "ready"
        assert result["configured"] == 1
        assert result["device"]["ready"] is True
        assert SECRET_KEY not in submitted.text
        assert "project-secret" not in submitted.text

        after = client.get("/lights/tuya/onboarding", headers=headers)
        assert after.status_code == 200
        assert after.json()["state"] == "ready"
        assert after.json()["devices"][0]["ready"] is True
        assert SECRET_KEY not in after.text

    assert store.get(DEVICE_ID) == SECRET_KEY
    assert driver._key_for(DEVICE_ID) == SECRET_KEY
    assert driver.status()["devices"][0]["state"] == "ready"
    assert SECRET_KEY not in caplog.text
    assert "project-secret" not in caplog.text
    assert SECRET_KEY in store.path.read_text(encoding="utf-8")


def test_cloud_error_is_generic_and_stores_nothing(tmp_path):
    app, store, _ = _make_app(tmp_path, cloud_factory=FailingCloud)
    with TestClient(app, base_url=BASE_URL) as client:
        headers = _pair(client)
        response = client.post(
            "/lights/tuya/onboarding",
            headers=headers,
            json={
                "client_id": "project-id",
                "client_secret": "project-secret",
                "region": "eu",
                "device_id": DEVICE_ID,
            },
        )
    assert response.status_code == 400
    assert response.json()["error"] == "cloud_code_1106"
    assert response.json()["state"] == "needs_credentials"
    assert SECRET_KEY not in response.text
    assert store.get(DEVICE_ID) is None


def test_invalid_region_and_missing_fields_do_not_call_cloud(tmp_path):
    calls: list[tuple] = []

    class RecordingCloud(FakeCloud):
        def __init__(self, client_id, client_secret, region):
            calls.append((client_id, client_secret, region))
            super().__init__(client_id, client_secret, region)

    app, _, _ = _make_app(tmp_path, cloud_factory=RecordingCloud)
    with TestClient(app, base_url=BASE_URL) as client:
        headers = _pair(client)
        bad_region = client.post(
            "/lights/tuya/onboarding",
            headers=headers,
            json={"client_id": "id", "client_secret": "secret",
                  "region": "mars", "device_id": DEVICE_ID},
        )
        missing = client.post(
            "/lights/tuya/onboarding",
            headers=headers,
            json={"client_id": "id", "region": "eu", "device_id": DEVICE_ID},
        )
    assert bad_region.status_code == 400
    assert bad_region.json()["error"] == "invalid_region"
    assert missing.status_code == 400
    assert missing.json()["error"] == "missing_fields"
    assert calls == []


def test_clear_returns_to_needs_credentials(tmp_path):
    app, store, driver = _make_app(tmp_path)
    with TestClient(app, base_url=BASE_URL) as client:
        headers = _pair(client)
        client.post(
            "/lights/tuya/onboarding",
            headers=headers,
            json={"client_id": "id", "client_secret": "secret",
                  "region": "eu", "device_id": DEVICE_ID},
        )
        cleared = client.delete(
            f"/lights/tuya/onboarding?device_id={DEVICE_ID}", headers=headers,
        )
    assert cleared.status_code == 200
    assert cleared.json()["state"] == "needs_credentials"
    assert store.get(DEVICE_ID) is None
    assert driver._key_for(DEVICE_ID) is None


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_cloud_client_signs_and_reads_local_key():
    requests = []
    payloads = [
        {"success": True, "result": {"access_token": "token-1",
                                     "expire_time": 3600}},
        {"success": True, "result": {"name": "Desk lamp",
                                     "local_key": SECRET_KEY}},
    ]

    def opener(request, timeout):
        requests.append(request)
        return _FakeResponse(payloads.pop(0))

    client = TuyaCloudClient("project-id", "project-secret", "eu", opener=opener)
    name, key = client.fetch_local_key(DEVICE_ID)

    assert (name, key) == ("Desk lamp", SECRET_KEY)
    assert "project-secret" not in repr(client)
    assert requests[0].full_url.endswith("/v1.0/token?grant_type=1")
    assert requests[1].full_url.endswith(f"/v1.0/devices/{DEVICE_ID}")
    token_headers = requests[0].headers
    device_headers = requests[1].headers
    assert token_headers["Sign_method"] == "HMAC-SHA256"
    assert len(token_headers["Sign"]) == 64
    assert device_headers["Access_token"] == "token-1"
