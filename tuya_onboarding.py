"""Tuya local-key onboarding helpers.

The Bridge is the only component that ever sees a Tuya local key.  This module
provides:

* :class:`TuyaCloudClient` — fetches a device's ``local_key`` once from the
  Tuya IoT Cloud project API (the same project links the user's Smart Life
  account).
* :class:`TuyaCredentialStore` — persists keys in the Bridge state directory
  with owner-only file permissions.  Values are never returned to the browser
  and never logged.

The HTTP flow lives in ``bridge.py``; this module deliberately has no FastAPI
dependency so it can be unit-tested with a fake cloud client.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import settings

logger = logging.getLogger("live_feed_quantizer.tuya")


class TuyaCloudError(RuntimeError):
    """A cloud failure with a safe, non-secret error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TuyaCloudClient:
    """Minimal Tuya IoT Cloud client for the ``local_key`` field.

    Only the documented project-credential flow is used: create an IoT Cloud
    project, link the Smart Life app account, then read
    ``GET /v1.0/devices/{device_id}``.  The client secret is held in memory for
    one fetch and never persisted.
    """

    REGIONS = {
        "eu": "https://openapi.tuyaeu.com",
        "us": "https://openapi.tuyaus.com",
        "cn": "https://openapi.tuyacn.com",
        "in": "https://openapi.tuyain.com",
    }

    def __init__(self, client_id: str, client_secret: str, region: str,
                 timeout: float = 10.0, opener=None) -> None:
        region = (region or "eu").strip().lower()
        if region not in self.REGIONS:
            raise ValueError("invalid_region")
        if not client_id or not client_secret:
            raise ValueError("missing_credentials")
        self.client_id = client_id
        self._client_secret = client_secret
        self.region = region
        self.base_url = self.REGIONS[region]
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def __repr__(self) -> str:  # never expose the secret
        return f"TuyaCloudClient(region={self.region!r})"

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        string_to_sign = (
            f"{self.client_id}{timestamp}\n{method}\n{content_hash}\n\n{path}"
        )
        return hmac.new(
            self._client_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest().upper()

    def _request(self, method: str, path: str, body: dict | None = None,
                 access_token: str | None = None) -> dict:
        body_text = json.dumps(body) if body is not None else ""
        timestamp = str(int(time.time() * 1000))
        headers = {
            "client_id": self.client_id,
            "t": timestamp,
            "sign_method": "HMAC-SHA256",
            "sign": self._sign(timestamp, method, path, body_text),
            "Content-Type": "application/json",
        }
        if access_token:
            headers["access_token"] = access_token
        request = urllib.request.Request(
            self.base_url + path,
            data=body_text.encode("utf-8") if body_text else None,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise TuyaCloudError(f"cloud_http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TuyaCloudError("cloud_unreachable") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TuyaCloudError("cloud_bad_response") from exc
        if not isinstance(payload, dict):
            raise TuyaCloudError("cloud_bad_response")
        if not payload.get("success"):
            # The provider's message can contain account details; only the
            # numeric code is safe to surface.
            raise TuyaCloudError(f"cloud_code_{payload.get('code', 'unknown')}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def _token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        result = self._request("GET", "/v1.0/token?grant_type=1")
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise TuyaCloudError("cloud_token_missing")
        try:
            expires_in = int(result.get("expire_time") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        self._access_token = token
        self._token_expires_at = now + max(60, expires_in)
        return token

    def fetch_local_key(self, device_id: str) -> tuple[str, str]:
        """Return ``(display_name, local_key)`` for one device.

        The caller must store the key Bridge-side and must not return it.
        """
        device_id = str(device_id or "").strip()
        if not device_id:
            raise TuyaCloudError("missing_device_id")
        safe_id = urllib.parse.quote(device_id, safe="")
        result = self._request(
            "GET", f"/v1.0/devices/{safe_id}", access_token=self._token(),
        )
        local_key = result.get("local_key")
        if not isinstance(local_key, str) or not local_key:
            raise TuyaCloudError("local_key_unavailable")
        name = result.get("name")
        return (name if isinstance(name, str) and name else device_id, local_key)


class TuyaCredentialStore:
    """Bridge-side local-key store with owner-only permissions.

    The file is JSON keyed by Tuya gateway id.  It never leaves the Bridge: the
    HTTP layer exposes only ready/needs-credentials state.
    """

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        if path is None:
            path = Path(settings.bridge_state_dir) / "tuya_credentials.json"
        self.path = Path(path)
        self._keys: dict[str, str] = {}
        self._load()

    def __repr__(self) -> str:
        return f"TuyaCredentialStore(path={str(self.path)!r}, count={len(self._keys)})"

    def __len__(self) -> int:
        return len(self._keys)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[TUYA] Credential file unreadable (%s)", type(exc).__name__)
            return
        if not isinstance(data, dict):
            logger.warning("[TUYA] Credential file has an unexpected shape")
            return
        self._keys = {
            str(device_id): str(local_key)
            for device_id, local_key in data.items()
            if isinstance(device_id, str) and isinstance(local_key, str) and local_key
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self._keys, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, device_id: str) -> str | None:
        return self._keys.get(str(device_id))

    def has(self, device_id: str) -> bool:
        return bool(self.get(device_id))

    def set(self, device_id: str, local_key: str) -> None:
        device_id = str(device_id).strip()
        local_key = str(local_key).strip()
        if not device_id or not local_key:
            raise ValueError("missing_device_or_key")
        self._keys[device_id] = local_key
        self._save()

    def delete(self, device_id: str) -> None:
        self._keys.pop(str(device_id), None)
        self._save()

    def clear(self) -> None:
        self._keys.clear()
        self._save()

    def device_ids(self) -> list[str]:
        return sorted(self._keys)
