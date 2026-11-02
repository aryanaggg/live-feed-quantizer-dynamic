# Tuya local-key onboarding

Tuya LAN control needs the device's `local_key`. The Bridge fetches it **once**
from a Tuya IoT Cloud project and stores it on the Bridge machine. The browser
never receives the key.

## Guided flow

1. Create a project at <https://iot.tuya.com/> (Cloud → Development → Create
   Cloud Project).
2. Link the Smart Life app account under **Devices → Link Tuya App Account**.
3. Copy the project Access ID and Access Secret.
4. Discover the bulb on the LAN (`{"type":"discover","kinds":["tuya"]}` on
   `/lights/ws`) so its gateway id is known.
5. Submit the project id/secret, region, and device id **once**.

`GET /lights/tuya/onboarding` returns the same five steps plus the current
state and discovered devices.

## HTTP API

All endpoints require the Bridge pairing token:

```http
Origin: http://localhost:5500
X-Bridge-Token: <token from POST /pair>
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/lights/tuya/onboarding` | `{state, configured, devices, steps, regions}` — no keys. |
| `POST` | `/lights/tuya/onboarding` | `{client_id, client_secret, region, device_id}` → fetch and store the key. |
| `DELETE` | `/lights/tuya/onboarding[?device_id=...]` | Remove one or all stored keys. |

States are `needs_credentials` → `ready`. Device entries expose
`ready`/`needs_credentials` only.

## Storage and secrecy

- Keys are written to
  `$LFQ_BRIDGE_STATE_DIR/tuya_credentials.json` (default
  `~/.live-feed-quantizer/`), atomically, with owner-only permissions.
- The project Access Secret is used in memory for the fetch and is **not**
  persisted.
- Keys and the Access Secret are never included in HTTP responses, driver
  status, or logs. Tests assert both properties.
- The cloud host is selected from a fixed region map (`eu`, `us`, `cn`, `in`);
  there is no user-supplied base URL, so the endpoint cannot be used as an
  SSRF primitive.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_tuya_onboarding.py -q
```

The tests inject a fake cloud client and assert the full
`needs_credentials` → `ready` → `needs_credentials` cycle, the credential file
contents, and that neither the local key nor the cloud secret appears in
responses or logs.
