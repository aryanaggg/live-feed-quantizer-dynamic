# server.py
"""FastAPI backend for the Live Feed Quantizer web app.

Protocol over a single WebSocket (/ws):
- client -> server binary payload: raw Float32 microphone PCM
- client -> server text payload:   base64 / data-URL JPEG frame
- client -> server text JSON:      control messages (`audio_info`,
  `jpeg_quality`, `audio_gain`, `ping`)
- server -> client text payload:   base64 / data-URL JPEG of the quantized
  frame, or JSON control replies (`status`, `error`, `pong`).

Every connection gets its own :class:`AudioPaletteQuantizer`, so palettes are
never shared between clients, and heavy processing runs in a worker thread to
keep the event loop responsive.
"""

import asyncio
import base64
import json
import logging

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sound_device_processor import AudioPaletteQuantizer

logger = logging.getLogger("live_feed_quantizer")

MAX_AUDIO_BYTES = 1 << 20           # 1 MiB cap on raw PCM payloads
MIN_SAMPLE_RATE = 1000
MAX_SAMPLE_RATE = 192000
JPEG_QUALITY_MIN, JPEG_QUALITY_MAX, JPEG_QUALITY_DEFAULT = 10, 100, 60
GAIN_MIN, GAIN_MAX = 0.1, 5.0
# Send the "waiting for mic" status at most every N frames (~1s at 30 fps).
WAITING_STATUS_EVERY = 30


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


app = FastAPI(title="Live Feed Quantizer", version="2.0")

# CORS: the browser client talks only to its own origin; this stays open so
# separately-hosted frontends can still connect, but credentials are disabled
# (the browser rejects wildcard origins combined with credentials anyway).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.get("/health")
async def health():
    """Liveness probe for load balancers / Railway health checks."""
    return JSONResponse({"status": "ok"})


def decode_jpeg_payload(data):
    """Decode a base64 or data-URL JPEG payload into a BGR image, or None."""
    if data.startswith("data:") and "," in data:
        _, b64 = data.split(",", 1)
    else:
        b64 = data
    try:
        img_bytes = base64.b64decode(b64)
    except Exception as exc:
        logger.warning("[IMAGE] base64 decode failed: %s", exc)
        return None
    if not img_bytes:
        return None
    try:
        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        logger.warning("[IMAGE] cv2.imdecode failed: %s", exc)
        return None
    if img is None:
        logger.warning("[IMAGE] cv2.imdecode returned None")
    return img


async def send_json(websocket, payload):
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as exc:
        logger.warning("[WS] Failed to send control message: %s", exc)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client = websocket.client.host if websocket.client else "unknown"
    logger.info("[WS] Client connected (%s)", client)

    # Per-connection state: isolated quantizer + connection settings.
    quantizer = AudioPaletteQuantizer()
    server_jpeg_quality = JPEG_QUALITY_DEFAULT
    audio_gain = 1.0
    latest_audio = None
    frame_count = 0
    waiting_status_count = 0

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            # --- binary: microphone PCM (Float32Array) ---
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if len(data) > MAX_AUDIO_BYTES:
                    logger.warning("[AUDIO] Oversized payload ignored (%d bytes)", len(data))
                    continue
                try:
                    audio = np.frombuffer(data, dtype=np.float32)
                    if audio.size:
                        latest_audio = audio
                except ValueError as exc:
                    logger.warning("[AUDIO] Failed to decode binary: %s", exc)
                    latest_audio = None
                continue

            # --- text: JSON control message or JPEG frame ---
            text = msg.get("text")
            if text is None:
                continue
            data = text.strip()

            if data.startswith("{"):
                try:
                    obj = json.loads(data)
                except ValueError as exc:
                    logger.warning("[JSON] Failed to parse control message: %s", exc)
                    continue
                if not isinstance(obj, dict):
                    continue
                mtype = obj.get("type")
                if mtype == "audio_info" and "sampleRate" in obj:
                    # AudioPaletteQuantizer clamps/sanitizes the value.
                    quantizer.set_sample_rate(obj["sampleRate"])
                    logger.info("[AUDIO] Client sample rate set")
                    continue
                if mtype == "jpeg_quality" and "value" in obj:
                    try:
                        server_jpeg_quality = int(_clamp(int(obj["value"]), JPEG_QUALITY_MIN, JPEG_QUALITY_MAX))
                        logger.info("[CONFIG] Server JPEG quality: %d", server_jpeg_quality)
                    except (TypeError, ValueError):
                        logger.warning("[CONFIG] Invalid jpeg_quality value")
                    continue
                if mtype == "audio_gain" and "value" in obj:
                    try:
                        audio_gain = _clamp(float(obj["value"]), GAIN_MIN, GAIN_MAX)
                        logger.info("[CONFIG] Audio gain: %.2f", audio_gain)
                    except (TypeError, ValueError):
                        logger.warning("[CONFIG] Invalid audio_gain value")
                    continue
                if mtype == "ping":
                    await send_json(websocket, {"type": "pong"})
                    continue
                logger.debug("[WS] Unknown control message type: %s", mtype)
                continue

            if not data:
                continue

            img = decode_jpeg_payload(data)
            if img is None:
                await send_json(websocket, {"type": "error", "detail": "invalid_frame"})
                continue

            frame_count += 1
            if frame_count % 30 == 0:
                logger.info("[IMAGE] Received %d frames, shape: %s", frame_count, img.shape)

            # Apply client-side audio gain before processing.
            audio = latest_audio
            if audio is not None and audio_gain != 1.0:
                audio = audio * audio_gain

            # Heavy CPU work in a worker thread so the event loop (and other
            # clients) stay responsive even during slow frames.
            try:
                processed = await asyncio.to_thread(quantizer.process, img, audio)
            except Exception:
                logger.exception("[PROCESS] Unexpected processing error")
                await send_json(websocket, {"type": "error", "detail": "processing_failed"})
                continue

            if processed is None:
                # No mic audio yet -> no palette yet. Video resumes automatically
                # once audio arrives; the last known palette is used meanwhile.
                waiting_status_count += 1
                if waiting_status_count == 1 or waiting_status_count % WAITING_STATUS_EVERY == 0:
                    await send_json(websocket, {"type": "status", "detail": "waiting_for_mic"})
                continue
            waiting_status_count = 0

            try:
                ok, buf = cv2.imencode(
                    ".jpg", processed, [int(cv2.IMWRITE_JPEG_QUALITY), server_jpeg_quality]
                )
                if not ok:
                    logger.warning("[RESPONSE] JPEG encoding failed")
                    continue
                out_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                await websocket.send_text("data:image/jpeg;base64," + out_b64)
            except Exception as exc:
                logger.warning("[RESPONSE] Failed to encode/send: %s", exc)
                break

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected (%s)", client)
    except Exception:
        logger.exception("[WS] Unexpected error")
    finally:
        logger.info("[WS] Session ended (%s) frames=%d", client, frame_count)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000)
