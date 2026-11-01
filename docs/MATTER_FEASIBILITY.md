# Matter feasibility note

Status: **feasibility note only**. The Matter driver in this repository is a
stub: it advertises the driver interface, returns no devices from discovery,
and logs `set_state()` intent. It performs no commissioning, no DTLS session
setup, and no LAN control.

## What would be required

A real Matter implementation needs a controller that can commission devices
over Bluetooth LE and then drive them over operational IP (IPv6, mDNS/SRP,
CASE). That is a substantial protocol stack, not a small driver adapter.

For this project the practical integration shape is a **sidecar Matter
server** exposing a WebSocket API, with the Python bridge acting as a client.
That keeps the protocol stack out of the bridge process and matches the
existing bridge/WebSocket architecture.

## Sourced library options

| Library / project | Project URL | Maintainer | Last release | Source |
|---|---|---|---|---|
| `python-matter-server` | https://github.com/matter-js/python-matter-server (PyPI: https://pypi.org/project/python-matter-server/) | Open Home Foundation (matter-js org); funded by Nabu Casa | **8.1.2, 2025-12-15** | GitHub repo metadata + PyPI JSON API |
| `matterjs-server` (`matter-server` on npm) | https://github.com/matter-js/matterjs-server (npm: https://www.npmjs.com/package/matter-server) | Open Home Foundation; npm maintainers Paulus Schoutsen (`balloob`) and Ingo Fischer (`apollon77`) | **1.5.5, 2026-10-24** (latest alpha 1.5.6-alpha.0-20261031, 2026-10-31) | GitHub Releases API + npm registry JSON |

Notes:

- `python-matter-server` is the Python server historically used by Home
  Assistant. Its GitHub repository is **archived** (archived flag `true`,
  last push 2026-06-23). The final PyPI release is 8.1.2 from 2025-12-15.
- `matterjs-server` is the successor, built on Matter.js. Its npm package is
  `matter-server`; the stable line is 1.5.x.
- Both projects are Open Home Foundation projects. The `python-matter-server`
  README states it is funded by Nabu Casa and donated to The Open Home
  Foundation.

## Removed names (not sourced, not usable here)

The earlier status notes listed two additional names. They are removed:

- **`pymatter`** - PyPI package `pymatter` is "Python bindings for
  R/Bioconductor 'matter' package", authored by Kylie A. Bemis
  (`k.bemis@northeastern.edu`), last release 2024-12-24. It is unrelated to
  Matter smart-home protocol work and must not be cited as a Matter library.
  Source: https://pypi.org/pypi/pymatter/json
- **`chip-fabric`** - `https://pypi.org/pypi/chip-fabric/json` returns HTTP
  404. No project, package, or source was found. It must not be cited.

## Decision

Do not claim Matter realtime lighting support. Keep the driver labeled as a
feasibility stub until a sidecar server integration and commissioning flow are
implemented and tested against real hardware.
