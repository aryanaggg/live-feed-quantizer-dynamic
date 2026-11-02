# Camera & Microphone Architecture Review

**For:** Claude (integration reviewer)
**Repo:** `F:\code\projects\live-feed-quantizer`
**Date:** 2026-10-31
**Status:** Decision document — no code changes pending approval
**Related commit:** `c3f48ea` (2026-10-26) “replace UI with client-side WebGL quantization redesign”

---

## 0. TL;DR

1. The camera/mic source controls were **not** removed by recent bug-fix work. They were removed by `c3f48ea`, which replaced `frontend/index.html` wholesale (480 insertions, **964 deletions**) instead of porting the existing panel.
2. The lost feature was **manual source configuration + a saved camera list** (Local / IP / ONVIF / RTSP), not automatic network discovery. No `enumerateDevices`, `audioinput`, or `videoinput` code ever existed in this repo’s frontend history. The device picker added on 2026-10-31 is **new**, not a restoration.
3. Restoring network cameras forces a choice between two quantization architectures:
   - **Server-authoritative** (legacy): server decodes, quantizes, JPEG-encodes and streams frames.
   - **Client-authoritative** (current): browser uploads frames to WebGL and quantizes in a fragment shader.
4. The two paths **clash on palette ownership, quantization locus, transport bandwidth, and effect parity** (the five visual modes exist only in the shader).
5. **Recommendation:** use the backend as a *frame gateway* only (Option B), keep palette computation and WebGL quantization in the browser for every source. Optionally add a *palette bridge* (Option D) later if bandwidth becomes a problem. Do **not** restore the legacy server-quantizing path for local cameras, and do not run both quantizers on the same frames.

---

## 1. Evidence and timeline

| Fact | Source |
|---|---|
| Camera panel, saved cameras, WS client, AudioWorklet mic existed before `c3f48ea` | `git show c3f48ea~1:frontend/index.html` |
| `c3f48ea` removed them | `git show --stat c3f48ea` → 1444 changed lines, 480 insertions / 964 deletions |
| Backend still supports network cameras | `server.py:282` `_ip_camera_loop`, `server.py:321` `_rtsp_stream_loop`, `server.py:539` `camera_source` |
| Current frontend has no WebSocket or `fetch` at all | `grep -n "WebSocket\|/ws\|fetch(" frontend/index.html` → no matches |
| No device enumeration ever existed before 2026-10-31 | `git log -S "enumerateDevices" -- frontend/index.html` → no commits |
| Project plan says RTSP/ONVIF must not be removed | `.kilo/plans/project-improvement-plan.md:28` |

The stale artifacts `server.py.rej`, `rtsp-fix.patch`, `frontend/index.html.bak/.tmp/.tmp2` are unrelated leftovers from earlier patch attempts.

---

## 2. Architecture A — current: client-side WebGL

```
getUserMedia(video + audio)
      │
      ├── <video id="vid"> ──────────────► WebGL texture ──► fragment shader ──► <canvas id="cv">
      │                                                          ▲
      └── AudioContext ──► AnalyserNode ──► analyse() ──► updatePalette() ──► uP[8] palette
```

Code map:

| Concern | Location |
|---|---|
| Stream acquisition | `frontend/index.html:133-172` (`start`) |
| Device enumeration/selection | `frontend/index.html:427-497` |
| Palette from analyser | `frontend/index.html:292-333` |
| Fragment shader + 5 modes | `frontend/index.html:207-248` |
| Render loop | `frontend/index.html:370-398` |
| Presets (localStorage) | `frontend/index.html:505+` |

**Strengths**
- Zero network latency and zero server cost for the local camera.
- Five visual modes (Palette, Dither, Halftone, Gradient, Ripple) are shader branches (`uM==1..4`); 60 fps verified.
- Works as a static page (`live-server` on :5500); backend optional.
- Mic mute and device switching are local (`track.enabled`, `deviceId: {exact}`), verified.

**Limits**
- Cannot read RTSP/ONVIF or arbitrary IP cameras (browser sandbox/CORS).
- No saved sources, no reconnect/session state, no server-side audio pipeline.
- Palette algorithm (`hsl()` over 3 bands) differs from the server’s `extract_colors_from_audio` (librosa FFT bands), so local vs server output would never match.

---

## 3. Architecture B — legacy: server-side WebSocket pipeline

```
camera (local or network)
      │
      ├── local: <video> → canvas.toBlob(JPEG) ──┐
      └── network: server fetches snapshot / RTSP ┘
                                                  ▼
                                        WebSocket /ws
                                                  ▼
                        AudioPaletteQuantizer.process(frame, pcm)
                                                  ▼
                          quantized JPEG → WS → client <img>/canvas
```

Code map (old file, `c3f48ea~1:frontend/index.html`):

| Concern | Location in old file |
|---|---|
| Camera source tabs (Local/IP/ONVIF/RTSP) | lines 166-198 |
| Saved cameras (`savedCams`, `activeCamId`) | lines 215-245 |
| Frame capture + backpressure (`MAX_PENDING = 2`) | lines 643-690 |
| Mic AudioWorklet → Float32 PCM | lines 553-612 |
| `camera_source` messages | lines 797-880 |
| Backend resolution / reconnect | lines 284-300, 440-545 |

Server side (still present):

| Concern | Location |
|---|---|
| Snapshot URL proxy | `server.py:125` `GET /camera_proxy` |
| IP camera loop | `server.py:282` |
| RTSP/ONVIF loop (OpenCV) | `server.py:321` |
| Per-connection quantizer | `server.py:386-393` |
| `camera_source` switching | `server.py:539-596` |
| Server palette/quantize | `sound_device_processor.py:128`, `image_quantizer/quantizer.py:95-122` |
| Downscale before quantize (`PROCESS_MAX_DIM = 480`) | `server.py:77, 189` |

**Strengths**
- Network cameras work through the backend (browser can’t fetch RTSP itself).
- Server-side quantized JPEG compresses very well (flat colors), low downstream bandwidth.
- Saved sources and reconnect logic already existed.

**Limits**
- Round trip for local cameras (canvas → JPEG → WS → decode → quantize → re-encode → WS → display).
- Only nearest-palette quantization server-side; the five WebGL modes do not exist there.
- Different palette algorithm and responsiveness than the current client.
- No tests for `camera_source` loops (the 24 tests only cover frame flow, `waiting_for_mic`, health, malformed input).
- Security debt: `/camera_proxy` and `camera_source` fetch arbitrary URLs (SSRF), `CORS allow_origins=["*"]` (`server.py:104`), credentials embedded in ONVIF URLs.

---

## 4. What “discovery” actually meant

| Capability | Before `c3f48ea` | Today |
|---|---|---|
| Local camera | default `getUserMedia` (no picker) | `enumerateDevices` picker (new) |
| Local microphone | default `getUserMedia` audio (no picker) | `enumerateDevices` picker + mute (new) |
| IP camera | manual snapshot URL + saved entry | gone |
| ONVIF camera | manual host/port/user/pass → constructed `/onvif/snapshot` URL | gone |
| RTSP camera | manual `rtsp://` URL → OpenCV | gone |
| Saved cameras | `localStorage.savedCams` + auto-reconnect | gone |
| Automatic network discovery (mDNS/SSDP/WS-Discovery) | **never implemented** | not implemented |
| Tuya/Google discovery | `tinytuya.scan()` server helper + WS stubs; UI buttons were undefined | UI stubs say “not available yet” |

So if “discovery system” means *scan the network and list cameras/mics*, that never existed. If it means *configure and persist camera/mic sources*, that is what was lost and can be restored.

---

## 5. Clash analysis (the hard parts)

### 5.1 Palette ownership — **primary clash**
- Client owns a 7-color RGB palette derived from a 3-band analyser (`analyse` → `updatePalette`).
- Server owns a different 7-color palette derived from `extract_colors_from_audio` (librosa FFT) and blends it per connection (`sound_device_processor.py:98-126`).
- If both run, the same audio produces different colors on local vs network sources, and state can drift.
- **Resolution:** exactly one palette owner. Recommended: browser. Server never computes palettes in the restored design.

### 5.2 Quantization locus and visual parity
- Client quantizes in GLSL: `blk = uPix * (1 + bass)`, per-cell sampling, five modes.
- Server quantizes with a NumPy LUT (`quantize_bgr`) at max 480px, then nearest-neighbour upscales.
- Network frames quantized server-side would look different (smoother, no dither/halftone/ripple) and cannot use the mode buttons.
- **Resolution:** server sends unquantized (optionally downscaled) JPEG; client quantizes everything. Accept higher bandwidth; optionally cap resolution server-side.

### 5.3 Frame transport, bandwidth, latency
- Current local path: zero bytes over the network.
- Option B (raw network JPEG at 1280×720, q60): roughly 80–200 KB/frame → ~19–48 Mbps at 30 fps on LAN/localhost.
- Option C (server-quantized JPEG): roughly 15–50 KB/frame → ~4–12 Mbps, much cheaper, but loses mode parity and duplicates palette logic.
- Browser decode of 30 JPEGs/s via `createImageBitmap` is feasible; RTSP decode/encode stays on the server.
- **Resolution:** Option B with adaptive downscale (e.g. 640–960 px long edge) and client upscale; measure real bandwidth before optimizing.

### 5.4 Audio duplication and mute
- Restoring server quantization would require sending Float32 PCM over WS again (old `AudioWorklet` path) *in addition to* the local analyser.
- Mic mute (`track.enabled = false`) must also zero outgoing PCM, or the server palette keeps reacting.
- **Resolution (Option B):** no PCM over WS for visualization. If integrations later need audio, send a separate opt-in stream.

### 5.5 Effects and controls
- `uM` modes, `uPix`, `uMir`, cover/aspect (`uCov`), and palette swatches are shader-only.
- Server has `_flip` and fixed quantizer parameters; no equivalent for modes or block size.
- **Resolution:** keep all effects client-side; network frames enter the same texture path.

### 5.6 Color space, aspect, timing
- JPEG is YCbCr; both paths eventually reach RGB, but server uses OpenCV BGR, client uses sRGB texture. Add a single conversion convention.
- Client uses `uCov` cover-fit; server resizes to 480 then nearest-upscales (stretch). Pick one.
- No timestamp sync in either path; old code relied on frame ordering and `MAX_PENDING = 2`.
- **Resolution:** normalize to “upload decoded bitmap → same shader”; reject frame bursts with a 2-frame in-flight cap.

### 5.7 State machine, backpressure, reconnect
- Old client: exponential backoff (`500ms * 2^n`, capped 5s), 2 outstanding frames, `waiting_for_mic` status.
- Current client: synchronous local loop, no connection state.
- **Resolution:** add an explicit source state machine: `idle → connecting → live → reconnecting → error`, per source kind, with tests.

### 5.8 Device identity and persistence schema
- Old schema: `{id, name, source: 'local'|'ip'|'onvif'|'rtsp', url}`.
- New local identity: `deviceId` (opaque, can change when unplugged; stable per origin).
- **Resolution:** versioned schema, e.g.
  ```json
  { "v": 2, "sources": [
    { "id": "…", "kind": "local", "name": "FaceTime HD", "deviceId": "…", "audioDeviceId": "…" },
    { "id": "…", "kind": "network", "protocol": "ip|onvif|rtsp", "name": "Front door", "url": "…" }
  ], "activeId": "…" }
  ```
  Migrate old `savedCams` on first load; never store plaintext passwords in localStorage without stating the risk.

### 5.9 Security
- `/camera_proxy` (`server.py:125-135`) fetches any URL; `camera_source` (`server.py:556-591`) does the same for IP/RTSP. SSRF risk on a public deployment.
- ONVIF credentials are embedded in URLs in the old design; they’d end up in localStorage and server logs.
- CORS is wildcard with credentials disabled (`server.py:102-108`).
- **Resolution:** scheme allowlist (http/https/rtsp), block loopback/link-local unless explicitly allowed, per-client rate limit, never log credentials, prefer per-session secrets over localStorage.

### 5.10 Deployment and topology
- Dev: frontend on `:5500` (`npm run dev`), backend on `:8001` (`package.json:8`), README says `:8000`, `npm start` uses `:8000` (`package.json:10`).
- Old client resolved the backend via `/health` probes and `?backend=` override; production is same-origin.
- Network sources require a reachable backend; static-only hosting can no longer be fully functional.
- HTTPS pages cannot open `ws://`/`http://` backends (mixed content); Railway terminates TLS, so `wss` behind the proxy.
- **Resolution:** one `resolveBackend()` helper, explicit `?backend=` override, document that network sources are unavailable in static-only mode.

### 5.11 Integrations coupling
- Tuya/Google/WiFi handlers live on the same `/ws` (`server.py:497-538`); only `tinytuya.scan` exists and only when the optional import succeeds.
- If a WS client returns for cameras, integrations can share it — but per-connection state and error handling must be designed together.
- **Resolution:** treat integrations as a separate phase; don’t block camera restoration on them.

---

## 6. Options

### Option A — Restore the legacy server-authoritative pipeline wholesale
Bring back old `frontend/index.html` behavior: send local frames as JPEG, server quantizes, display returned JPEG.

- ✅ Least new design work; saved cameras and reconnect code can be lifted.
- ✅ Lowest downstream bandwidth.
- ❌ Loses the five WebGL modes for all sources (or forces maintaining two renderers).
- ❌ Round trip for local camera; 480px processing cap.
- ❌ Reintroduces MPEG/JPEG encode on the client and duplicate palette state.
- **Verdict:** not recommended as the target architecture; useful only as a fallback.

### Option B — Backend as frame gateway; client owns palette + WebGL *(recommended)*
Server only decodes network sources and forwards JPEG frames (optionally downscaled). Client decodes → texture → same shader for every source.

- ✅ One palette, one quantizer, one visual language, all five modes everywhere.
- ✅ Local camera stays zero-network and 60 fps.
- ✅ Reuses `_ip_camera_loop` / `_rtsp_stream_loop` with a `quantize: false` branch.
- ❌ Higher bandwidth than option C.
- ❌ Client CPU cost for decode + WebGL (already proven at 60 fps for local).
- ❌ Still needs WS client, reconnect, backpressure, source state machine.

### Option C — Server quantizes network frames; local stays WebGL
Keep the legacy path only for network sources; accept two visual pipelines.

- ✅ Low bandwidth; server CPU does the work.
- ❌ Two palettes, two looks, no modes on network sources.
- ❌ Same audio would need to be sent to the server and analyzed locally.
- **Verdict:** only acceptable if visual parity is explicitly not required.

### Option D — Palette bridge (client → server)
Client computes palette/modes parameters and sends them (small JSON) to the server; server quantizes network frames with the client palette.

- ✅ Low bandwidth, one palette *source* of truth.
- ❌ Still no shader modes server-side; requires porting effects to CPU or accepting reduced effects.
- ❌ New protocol + server API (`quantize_with_palette`) + validation.
- **Verdict:** a bandwidth optimization for Option B, not a standalone choice; skip unless needed.

### Option E — Local-only restoration
Keep the current picker, add saved local device pairs and auto-reconnect; no network cameras.

- ✅ Smallest change, no new security surface.
- ❌ Does not restore the IP/ONVIF/RTSP feature the user asked about.
- **Verdict:** fallback if network cameras are out of scope.

### Comparison

| Criterion | A legacy | B gateway | C server-quantize | D palette bridge | E local-only |
|---|---|---|---|---|---|
| Visual parity across sources | ❌ | ✅ | ❌ | ⚠️ (no modes) | ✅ (local only) |
| All 5 modes on network cams | ❌ | ✅ | ❌ | ❌ | n/a |
| Local-camera latency | ❌ round trip | ✅ zero | ✅ zero | ✅ zero | ✅ |
| Downstream bandwidth | ✅ low | ❌ high | ✅ low | ✅ low | n/a |
| Server CPU | high | low | high | high | none |
| Client CPU | low | medium | low | medium | medium |
| New backend work | none | small | none | medium | none |
| Security surface | high | medium | high | high | none |
| Implementation risk | medium | medium | low | high | low |

---

## 7. Recommendation

**Primary: Option B.** It preserves the current product’s identity (WebGL palette/modes), gives one consistent look for every source, and only requires a small backend branch plus a WS client.

**Then, if bandwidth is unacceptable:** add Option D in front of B (server may quantize *only* when the client sends a palette, otherwise it forwards raw frames). Do not enable C and B simultaneously on the same frame.

**Do not:** port the five shader modes to NumPy, run both palettes, or send local frames through the server as JPEG.

---

## 8. Proposed target design

### 8.1 Client source abstraction

```js
// one renderer, two feed types
const source = {
  kind: 'local' | 'network',
  start(), stop(),
  // local: video element already bound to #vid
  // network: WS pushes Blob/ArrayBuffer JPEG frames
};
```

Render loop remains `frontend/index.html:370-398`; only the texture upload changes:

- local: `gl.texImage2D(..., vid)`
- network: `createImageBitmap(blob)` → `gl.texImage2D(..., bitmap)` (reuse a single texture, close bitmap after upload)

### 8.2 UI restoration

- One camera panel with tabs: **Local / IP / ONVIF / RTSP**.
  - Local: existing `#camSelect` + `#micSelect` (keep).
  - IP: snapshot URL.
  - ONVIF: host, port, user, pass (build snapshot URL as before, but do not persist the password in plaintext without a warning).
  - RTSP: `rtsp://` URL.
- Saved sources list with Connect/Remove, `localStorage` schema v2, restore last active source.
- Status line for `connecting / live / reconnecting / error`; reuse `waiting_for_mic` only if audio ever returns to the server.

### 8.3 Data flow (target)

```
LOCAL:   getUserMedia → <video> → texture → shader (client palette) → canvas
NETWORK: backend fetcher → raw/downscaled JPEG → WS → createImageBitmap → texture → shader → canvas
AUDIO:   getUserMedia → AnalyserNode → client palette (only; never sent for visualization)
```

### 8.4 Backend changes (minimal, backward compatible)

- `camera_source` accepts `"quantize": false` (default `true` for old clients).
- In `_handle_frame`/`_render_frame`, when `quantize` is false: optional downscale only, skip `quantizer.process`, return the JPEG unchanged (or re-encode at the requested quality).
- Add optional `"maxDim"` and `"quality"` fields to `camera_source`.
- Keep `/camera_proxy` for connectivity tests; harden it (scheme allowlist, private-IP block, rate limit).
- No audio message required for Option B.

Example control messages:

```json
{ "type": "camera_source", "source": "rtsp",
  "url": "rtsp://192.168.1.50:554/stream",
  "quantize": false, "maxDim": 960, "quality": 60 }
```

```json
{ "type": "palette", "colors": [[12,4,40],[30,10,90], "..."] }   // only if Option D is adopted
```

### 8.5 Backpressure and reconnect

- Max 2 in-flight network frames; drop the oldest frame on overflow (old `MAX_PENDING` behavior).
- Exponential backoff (0.5s → 5s) with status text; reset on open.
- On source switch: cancel server loop → close/reuse socket → start new source; guarantee stale frames from the old source are discarded (generation counter).

---

## 9. Phased migration plan

| Phase | Work | Acceptance |
|---|---|---|
| 0 | Baseline: `npm run dev`, run 24 tests, confirm current app | 24/24 pass; local camera 60 fps |
| 1 | Restore camera panel UI + saved sources (schema v2) with local-only connect; no server | panel opens, list persists, local source restores |
| 2 | Add WS client + backend `quantize:false` frame forwarding for IP/ONVIF | snapshot URL renders through shader, all modes work |
| 3 | Add RTSP path (server OpenCV) + source switch cancellation + backpressure | switch RTSP↔local, no stale frames |
| 4 | Reconnect/backoff, status states, error surfaces, mic/device switching integration | kill backend → auto-reconnect; device hot-swap |
| 5 | Security (SSRF, CORS, credential handling), docs, deployment notes | `/camera_proxy` rejects private IPs; README updated |
| 6 (optional) | Palette bridge (Option D) and integrations wiring | bandwidth target met; integrations still isolated |

Keep commits small and run tests after each phase, per `.kilo/plans/project-improvement-plan.md:18`.

---

## 10. Test plan

Existing suite: 24 tests in `tests/` (quantizer, visualizer, processor, WS protocol). Verified during this review: `.venv\Scripts\python.exe -m pytest tests -q` → **24 passed** in 2.08s. They must keep passing.

New backend tests:
- `camera_source` with `quantize:false` returns a frame whose bytes are not palette-collapsed (e.g. > 1 unique color / compare against quantized path).
- Source switch cancels the previous task (no frames after switch; use a fake fetcher).
- Malformed URL → error message, no crash.
- `/camera_proxy` rejects `file://`, loopback, and non-http(s) schemes.
- Private-IP block behavior is configurable for LAN use.

New frontend tests / manual checks:
- Local: device pickers, mute, restart, mode switching.
- Network: IP snapshot URL, RTSP (if a stream is available), saved list restore, reconnect after server restart.
- Visual parity: same palette/effects on local and network frames.
- Backpressure: cap in-flight frames; no memory growth.
- Bandwidth sample: record bytes/s at 720p and at `maxDim: 640`.

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Network bandwidth on remote deploys | high | `maxDim` 640–960, q50–60, adaptive drop; document LAN-first |
| SSRF via camera URLs | high | scheme allowlist, block private IPs by default, opt-in LAN flag, rate limit |
| Credentials in localStorage/logs | high | warn, offer session-only storage, redact URLs in logs |
| Double palette / visual mismatch | medium | single palette owner (client); remove server palette from network path |
| Stale frames after source switch | medium | generation counter per source |
| Mixed content (`ws://` from https) | medium | backend URL resolution + `wss` behind proxy; document |
| Port confusion (:8000 vs :8001) | low | unify `package.json`, README, and `resolveBackend()` |
| Old `savedCams` schema | low | versioned migration on load |

---

## 12. Open questions for Claude

1. Is **visual parity** between local and network sources a hard requirement? If not, Option C becomes viable.
2. Is the product **LAN/local-first**, or must it work on a remote Railway deployment? This decides whether raw-JPEG bandwidth is acceptable.
3. Are the five visual modes required for network cameras? (Option B yes; C/D no.)
4. Should the palette algorithms be unified (one implementation) across all sources, or is client-only sufficient?
5. Do we need **true network discovery** (SSDP/WS-Discovery/mDNS), or is manual URL entry + saved list enough?
6. What is the credential policy for ONVIF/RTSP (session-only vs persisted)?
7. Should the app remain usable as a static page without the backend? (If yes, the UI must clearly mark network sources unavailable.)

---

## 13. Appendix — key references

Current frontend:
- `frontend/index.html:133` `start()` — `getUserMedia`, analyser, run state
- `frontend/index.html:207` fragment shader, `frontend/index.html:292` `analyse()`
- `frontend/index.html:313` `updatePalette()`, `frontend/index.html:370` `frame()`
- `frontend/index.html:427` device panel, `frontend/index.html:430` `refreshDevices()`
- `frontend/index.html:455` `toggleDevices()`, `frontend/index.html:469` `switchStream()`
- No `WebSocket`/`fetch` anywhere in the file

Backend:
- `server.py:77` `PROCESS_MAX_DIM = 480`, `server.py:79` `IP_CAMERA_INTERVAL`
- `server.py:102` CORS, `server.py:125` `/camera_proxy`, `server.py:386` `/ws`
- `server.py:282` `_ip_camera_loop`, `server.py:321` `_rtsp_stream_loop`
- `server.py:539` `camera_source`, `server.py:497` integrations
- `sound_device_processor.py:128` `process()`, `image_quantizer/quantizer.py:107` `quantize_bgr()`

Legacy frontend (`git show c3f48ea~1:frontend/index.html`):
- lines 166-198 camera panel; 215-245 saved cameras
- lines 553-612 AudioWorklet; 643-690 frame capture; 797-880 source connect
- lines 284-300 backend resolution; 440-545 reconnect

Useful commands:
```bash
git show c3f48ea~1:frontend/index.html
git show --stat c3f48ea
grep -n "WebSocket\|/ws\|fetch(" frontend/index.html
.venv\Scripts\python.exe -m pytest tests -q
```
Windows syntax check for the inline script (PowerShell):
```powershell
$c = Get-Content -Raw frontend\index.html
$m = [regex]::Match($c, '(?s)<script>(.*?)</script>')
Set-Content -Path "$env:TEMP\app.js" -Value $m.Groups[1].Value
node --check "$env:TEMP\app.js"
```
