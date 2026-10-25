# Live Feed Quantizer

Real-time camera feed quantized to a color palette generated from live audio.
Louder/bassier audio → brighter, more saturated colors via FFT band energies
mapped to hues around the color wheel.

## Architecture

```
audio (mic PCM) ──► FFT band energies ──► HSV palette ──┐
                                                        ├──► color quantize ──► output frame
camera frame ───────────────────────────────────────────┘
```

| File | Role |
|---|---|
| `sound_device_processor.py` | Core pipeline: `AudioPaletteQuantizer` (per-connection state, audio→palette blending, frame quantization) |
| `audio_to_palette/music_visualizer.py` | `extract_colors_from_audio`: FFT bands → L1 normalize → hues |
| `image_quantizer/quantizer.py` | `quantize_array` / `quantize_image`: fast chunked nearest-palette-color mapping |
| `server.py` | FastAPI backend: WebSocket frame/audio exchange, control messages, `/health` |
| `frontend/index.html` | Browser client: getUserMedia, AudioWorklet capture, JPEG frame loop, meter, reconnect |
| `capture_audio.py` / `capture_camera.py` | Local capture (PyAudio thread, fullscreen OpenCV window) |
| `main.py` | Local window mode entry point |
| `check_devices.py`, `capture_mic_and_playback.py`, `test_audio.py` | Audio device utilities |

## Run locally (web)

```bash
npm run dev
# BACKEND  : uvicorn with auto-reload on http://localhost:8000
# FRONTEND : live-reload static server on http://localhost:5500
```

`npm run dev` starts both processes together under `concurrently` (killing one
stops the other). The frontend served on :5500 probes `/health` and talks to
the backend on :8000 automatically; in production (backend serves the page)
everything is same-origin and no extra config is needed. Override the backend
endpoint manually with `index.html?backend=localhost:9000` if needed.

Production-style single process (backend serves the frontend too):

```bash
npm start                                  # http://localhost:8000
.venv\Scripts\python.exe -m uvicorn server:app --port 8000   # equivalent
```

Local window mode:

```bash
.venv\Scripts\python.exe main.py
```

## Run locally (local window mode)

```bash
.venv\Scripts\python.exe main.py
```

## WebSocket protocol (`/ws`)

Client → server:
- binary: raw Float32 microphone PCM
- text JSON controls: `{"type": "audio_info", "sampleRate": 48000}`,
  `{"type": "jpeg_quality", "value": 10-100}`, `{"type": "audio_gain", "value": 0.1-5}`,
  `{"type": "ping"}`
- text: base64 or data-URL JPEG frame

Server → client:
- text: `data:image/jpeg;base64,...` quantized frame
- text JSON: `{"type": "status", "detail": "waiting_for_mic"}`,
  `{"type": "error", "detail": "invalid_frame" | "processing_failed"}`,
  `{"type": "pong"}`

Notes:
- Each connection has an isolated palette state (no leaking between clients).
- Silent/absent audio reuses the last palette; only when no palette exists yet
  does the server report `waiting_for_mic` and skip processing.
- The client applies backpressure: it stops sending frames while two responses
  are outstanding, and the server keeps video flowing with cached palettes when
  the mic goes quiet.

## Tests

```bash
.venv\Scripts\python.exe -m pytest tests -q        # install dev deps first: pip install -r requirements-dev.txt
```

## Deploy (Railway)

See `RAILWAY_DEPLOY.md`. The Docker image builds from `requirements.txt`;
the dev/test extras live in `requirements-dev.txt`.
