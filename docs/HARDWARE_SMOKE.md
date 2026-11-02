# Hardware smoke test

`tools/hw_smoke.py` exercises real WiZ/Tuya bulbs. It is **not** part of CI and
is never imported by the application at startup.

## Run

```powershell
# WiZ (UDP 38899, no credentials)
.\.venv\Scripts\python.exe tools\hw_smoke.py --driver wiz
.\.venv\Scripts\python.exe tools\hw_smoke.py --driver wiz --host 192.168.1.50

# Tuya (local key required; prefer the env var over the CLI flag)
$env:TUYA_LOCAL_KEY = "<key>"
.\.venv\Scripts\python.exe tools\hw_smoke.py --driver tuya `
  --host 192.168.1.60 --device-id <gateway-id>
```

`--json` prints the same report in machine-readable form. `--wiz-broadcast`
overrides WiZ discovery when the default broadcast is blocked.

## What it reports

| Field | Meaning |
|---|---|
| `discovered` / `provided` | Devices found by discovery, or a device synthesized from `--host` when discovery is unavailable. |
| `paired` | Driver pairing completed. For Tuya this stores the local key Bridge-side. |
| `colors` | Each requested RGB plus `sent`, the real `ack_ms`, or the failure. |
| `probe_latency_ms` | WiZ `getPilot` / Tuya `status` round-trip, or `"unmeasurable"`. |
| `ack_latency_ms` | Average real `set_state` acknowledgement round-trip, or `"unmeasurable"`. |
| `status` | Driver `status()` snapshot; never contains credentials. |

Exit code is `0` only when the device was reached, all colours were sent, and a
real ack was measured.

## Credentials

The Tuya local key is accepted via `--local-key` or `TUYA_LOCAL_KEY`, is stored
only in the driver instance, is passed only to `tinytuya.BulbDevice`, and is
never printed in the text or JSON report. The key is not sent to the browser.

## Protocol notes

- WiZ uses JSON over UDP 38899: `getPilot` for discovery/state and `setPilot`
  for commands. The report latency is measured from `sendto` to the matching
  `recvfrom`, not estimated.
- Tuya uses `tinytuya.deviceScan` (the public `tinytuya.scan` only prints and
  returns `None`) and `BulbDevice` with protocol 3.3/3.4/3.5.
- `pyproject.toml` pins `tinytuya>=1.20.0`; there is no 5.x release.
