# server.py
"""FastAPI backend for the Live Feed Quantizer web app.

Protocol over a single WebSocket (/ws):
- client -> server binary payload: JPEG frame (starts with ``FF D8``) or raw
  Float32 microphone PCM. Both directions use raw JPEG bytes for binary
  clients (no base64 overhead, no data-URL parse/serialize).
- client -> server text payload:   base64 / data-URL JPEG frame (legacy) or
  JSON control messages (`audio_info`, `jpeg_quality`, `audio_gain`, `ping`,
  `integration_start`, `integration_stop`, `integration_device_action`)
- server -> client: raw JPEG bytes for frames that arrived binary; a
  data-URL text frame for frames that arrived as text (legacy clients keep
  working unchanged); JSON control replies otherwise (`status`, `error`,
  `pong`, integration status).

Binary classification needs no protocol negotiation: a payload that starts
with the JPEG SOI marker (``FF D8``) is a frame, otherwise it is treated as
audio only when its length is a multiple of 4 and its reinterpretation as
Float32 is entirely finite with small magnitude (real PCM samples).

Every connection gets its own :class:`AudioPaletteQuantizer`, so palettes are
never shared between clients, and ALL heavy media work (JPEG decode ->
quantize -> JPEG encode) runs in a single worker thread per frame so the
event loop only does socket I/O.
"""

import asyncio
import base64
import json
import logging
import os
import urllib.request
import urllib.error

try:
    import tinytuya
    TINYTUA_AVAILABLE = True
except ImportError:
    TINYTUA_AVAILABLE = False

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|"
    "max_delay;0|"
    "stimeout;3000000|"
    "buffer_size;1048576|"
    "fflags;nobuffer|"
    "flags;low_delay|"
    "probesize;32|"
    "analyzeduration;0"
)

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from sound_device_processor import AudioPaletteQuantizer

logger = logging.getLogger("live_feed_quantizer")

MAX_AUDIO_BYTES = 1 << 20           # 1 MiB cap on raw PCM payloads
MAX_FRAME_BYTES = 8 << 20           # 8 MiB cap on an incoming JPEG frame
JPEG_SOI = b"\xff\xd8"              # JPEG start-of-image marker
# Float32 PCM sanity window: real mic samples are small finite numbers.
MAX_AUDIO_ABS = 8.0
MIN_SAMPLE_RATE = 1000
MAX_SAMPLE_RATE = 192000
JPEG_QUALITY_MIN, JPEG_QUALITY_MAX, JPEG_QUALITY_DEFAULT = 10, 100, 60
GAIN_MIN, GAIN_MAX = 0.1, 5.0
# Send the "waiting for mic" status at most every N frames (~1s at 30 fps).
WAITING_STATUS_EVERY = 30
# Quantize at most this many pixels on the longest edge; frames are
# nearest-neighbor upscaled back to original size after quantization. Keeps
# the blocky posterized look while cutting CPU cost roughly quadratically.
PROCESS_MAX_DIM = 480
# IP camera fetch interval (seconds) between snapshot grabs.
IP_CAMERA_INTERVAL = 0.033   # ~30 fps
IP_CAMERA_TIMEOUT = 5.0      # seconds per HTTP request


def _fetch_frame(url: str) -> bytes | None:
    """Fetch a single JPEG frame from an IP camera snapshot URL."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "LiveFeedQuantizer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=IP_CAMERA_TIMEOUT) as resp:
        return resp.read()


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


@app.get("/camera_proxy")
async def camera_proxy(url: str = Query(..., description="IP camera snapshot URL to proxy")):
    """Proxy a single frame from an IP camera, avoiding browser CORS restrictions."""
    try:
        jpeg_bytes = await asyncio.to_thread(_fetch_frame, url)
    except Exception as exc:
        logger.warning("[CAMERA] Proxy fetch failed for %s: %s", url, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)
    if not jpeg_bytes or jpeg_bytes[:2] != JPEG_SOI:
        return JSONResponse({"error": "Not a JPEG image"}, status_code=400)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


def _looks_like_audio(data):
    """Heuristic: is this binary payload Float32 PCM rather than a JPEG?

    JPEGs are recognized by their SOI marker before this is ever called.
    Real microphone samples are small finite floats, so a payload whose
    length is a multiple of 4 and whose float32 reinterpretation is entirely
    finite with |x| <= 8 is audio.
    """
    if len(data) == 0 or len(data) % 4 != 0:
        return False
    audio = np.frombuffer(data, dtype=np.float32)
    if not audio.size:
        return False
    return bool(np.all(np.isfinite(audio)) and np.abs(audio).max() <= MAX_AUDIO_ABS)


def _decode_jpeg_bytes(img_bytes):
    """Decode raw JPEG bytes into a BGR image, or None."""
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


def _render_frame(jpeg_bytes, quantizer, audio, jpeg_quality):
    """Full frame pipeline, executed OFF the event loop via to_thread.

    Decodes the JPEG, quantizes it against the audio-driven palette and
    re-encodes it. Returns ``(jpeg_bytes, None)`` on success, or
    ``(None, reason)`` with reason in {"invalid_frame", "waiting_for_mic",
    "processing_failed", "encode_failed"}.
    """
    img = _decode_jpeg_bytes(jpeg_bytes)
    if img is None:
        return None, "invalid_frame"

    flip = getattr(quantizer, "_flip", None)
    if flip == "horizontal":
        img = cv2.flip(img, 1)
    elif flip == "vertical":
        img = cv2.flip(img, 0)
    elif flip == "both":
        img = cv2.flip(img, -1)

    h, w = img.shape[:2]
    scale = min(PROCESS_MAX_DIM / max(h, w), 1.0)
    if scale < 1.0:
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    else:
        small = img

    try:
        processed = quantizer.process(small, audio)
    except Exception:
        logger.exception("[PROCESS] Unexpected processing error")
        return None, "processing_failed"

    if processed is None:
        # No mic audio yet -> no palette yet. Video resumes automatically
        # once audio arrives; the last known palette is used meanwhile.
        return None, "waiting_for_mic"

    if scale < 1.0:
        processed = cv2.resize(processed, (w, h), interpolation=cv2.INTER_NEAREST)

    try:
        ok, buf = cv2.imencode(
            ".jpg", processed, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
        )
    except Exception as exc:
        logger.warning("[RESPONSE] JPEG encoding failed: %s", exc)
        return None, "encode_failed"
    if not ok:
        return None, "encode_failed"
    return buf.tobytes(), None


def decode_jpeg_payload(data):
    """Decode a base64 or data-URL JPEG payload into raw JPEG bytes, or None."""
    if data.startswith("data:") and "," in data:
        _, b64 = data.split(",", 1)
    else:
        b64 = data
    try:
        img_bytes = base64.b64decode(b64)
    except Exception as exc:
        logger.warning("[IMAGE] base64 decode failed: %s", exc)
        return None
    return img_bytes or None


async def send_json(websocket, payload):
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as exc:
        logger.warning("[WS] Failed to send control message: %s", exc)


async def _handle_frame(websocket, jpeg_bytes, *, binary_out,
                        quantizer, audio, server_jpeg_quality):
    """Decode -> quantize -> encode in the worker thread, then reply.

    The reply format matches what the client sent: raw JPEG bytes for binary
    senders, a data-URL for legacy text senders. Returns (sent, reason):
    ``(True, None)`` on success, otherwise ``(False, reason)`` where reason
    is "invalid_frame" / "process_failed" / "encode_failed" /
    "waiting_for_mic" (callers throttle that last one themselves).
    """
    if len(jpeg_bytes) > MAX_FRAME_BYTES:
        logger.warning("[IMAGE] Oversized frame ignored (%d bytes)", len(jpeg_bytes))
        await send_json(websocket, {"type": "error", "detail": "invalid_frame"})
        return False, "invalid_frame"

    try:
        out, reason = await asyncio.to_thread(
            _render_frame, jpeg_bytes, quantizer, audio, server_jpeg_quality
        )
    except Exception:
        logger.exception("[PROCESS] Worker failed unexpectedly")
        await send_json(websocket, {"type": "error", "detail": "processing_failed"})
        return False, "process_failed"

    if out is None:
        if reason not in ("waiting_for_mic",):
            await send_json(websocket, {"type": "error", "detail": reason})
        return False, reason

    try:
        if binary_out:
            await websocket.send_bytes(out)
        else:
            await websocket.send_text("data:image/jpeg;base64," + base64.b64encode(out).decode("ascii"))
    except Exception as exc:
        logger.warning("[RESPONSE] Failed to send frame: %s", exc)
        return False, "send_failed"
    return True, None


async def _ip_camera_loop(
    websocket: WebSocket,
    quantizer: AudioPaletteQuantizer,
    camera_url: str,
    jpeg_quality: int,
    audio_gain: float,
    stop_event: asyncio.Event,
    get_audio,
):
    """Background task: fetch frames from an IP camera, quantize, push over WS."""
    logger.info("[CAMERA] IP camera loop started for %s", camera_url)
    while not stop_event.is_set():
        try:
            jpeg_bytes = await asyncio.to_thread(_fetch_frame, camera_url)
            if not jpeg_bytes or jpeg_bytes[:2] != JPEG_SOI:
                await asyncio.sleep(IP_CAMERA_INTERVAL)
                continue

            audio = get_audio()
            if audio is not None and audio_gain != 1.0:
                audio = audio * audio_gain
            sent, reason = await _handle_frame(
                websocket, jpeg_bytes, binary_out=True,
                quantizer=quantizer, audio=audio,
                server_jpeg_quality=jpeg_quality,
            )
            if not sent and reason not in ("waiting_for_mic",):
                logger.debug("[CAMERA] Frame not sent (%s)", reason)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("[CAMERA] IP camera loop error: %s", exc)
            await asyncio.sleep(0.5)
            continue

        await asyncio.sleep(IP_CAMERA_INTERVAL)
    logger.info("[CAMERA] IP camera loop stopped for %s", camera_url)


async def _rtsp_stream_loop(
    websocket: WebSocket,
    quantizer: AudioPaletteQuantizer,
    rtsp_url: str,
    jpeg_quality: int,
    stop_event: asyncio.Event,
    get_audio,
    audio_gain: float,
):
    """Background task: open an RTSP stream via OpenCV, quantize frames, push over WS."""
    logger.info("[CAMERA] RTSP stream loop started for %s", rtsp_url)
    cap = None
    try:
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            logger.warning("[CAMERA] Failed to open RTSP stream: %s", rtsp_url)
            await send_json(websocket, {"type": "error", "detail": "rtsp_open_failed"})
            return
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FPS, 25)
        except Exception:
            pass

        while not stop_event.is_set():
            try:
                for _ in range(3):
                    if not cap.grab():
                        break
                ret, frame = await asyncio.to_thread(cap.retrieve)
                if not ret or frame is None:
                    await asyncio.sleep(0.01)
                    continue

                # Encode frame to JPEG for processing pipeline
                ok, buf = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
                )
                if not ok:
                    logger.warning("[CAMERA] RTSP frame encode failed")
                    continue
                jpeg_bytes = buf.tobytes()

                audio = get_audio()
                if audio is not None and audio_gain != 1.0:
                    audio = audio * audio_gain
                sent, reason = await _handle_frame(
                    websocket, jpeg_bytes, binary_out=True,
                    quantizer=quantizer, audio=audio,
                    server_jpeg_quality=jpeg_quality,
                )
                if not sent and reason not in ("waiting_for_mic",):
                    logger.debug("[CAMERA] RTSP frame not sent (%s)", reason)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[CAMERA] RTSP stream loop error: %s", exc)
                await asyncio.sleep(0.5)
                continue
    finally:
        if cap is not None:
            cap.release()
        logger.info("[CAMERA] RTSP stream loop stopped for %s", rtsp_url)


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
    # Camera source: None = local client-sent frames, or dict {"type": "ip|onvif|rtsp", "url": "..."}
    camera_source = None
    ip_camera_task = None
    ip_camera_stop = None
    rtsp_task = None
    rtsp_stop = None

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            # --- binary: JPEG frame (FF D8) or microphone PCM (Float32) ---
            data = msg.get("bytes")
            if data is not None:
                if data[:2] == JPEG_SOI:
                    # In IP camera mode the backend generates frames; ignore
                    # client-sent frames to avoid conflicts.
                    if camera_source and camera_source.get("type") in ("ip", "onvif", "rtsp"):
                        continue
                    frame_count += 1
                    if frame_count % 30 == 0:
                        logger.info("[IMAGE] Received %d frames, last %d bytes", frame_count, len(data))

                    # Apply client-side audio gain before processing.
                    audio = latest_audio
                    if audio is not None and audio_gain != 1.0:
                        audio = audio * audio_gain

                    sent, reason = await _handle_frame(
                        websocket, data, binary_out=True,
                        quantizer=quantizer, audio=audio,
                        server_jpeg_quality=server_jpeg_quality,
                    )
                    if sent:
                        waiting_status_count = 0
                    elif reason == "waiting_for_mic":
                        waiting_status_count += 1
                        if (waiting_status_count == 1
                                or waiting_status_count % WAITING_STATUS_EVERY == 0):
                            await send_json(websocket, {"type": "status", "detail": "waiting_for_mic"})
                    continue

                if _looks_like_audio(data):
                    if len(data) > MAX_AUDIO_BYTES:
                        logger.warning("[AUDIO] Oversized payload ignored (%d bytes)", len(data))
                        continue
                    latest_audio = np.frombuffer(data, dtype=np.float32)
                    continue

                logger.debug("[WS] Ignoring %d-byte binary payload (not JPEG/PCM)", len(data))
                continue

            # --- text: JSON control message or base64 JPEG frame ---
            text = msg.get("text")
            if text is None:
                continue
            text = text.strip()

            if text.startswith("{"):
                try:
                    obj = json.loads(text)
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
                if mtype == "flip" and "mode" in obj:
                    mode = obj.get("mode")
                    if mode in ("none", "horizontal", "vertical", "both"):
                        quantizer._flip = None if mode == "none" else mode
                        logger.info("[CONFIG] Flip mode: %s", mode)
                    continue
                if mtype == "ping":
                    await send_json(websocket, {"type": "pong"})
                    continue
                if mtype == "integration_start":
                    # Start an integration
                    integration_type = obj.get("type")
                    device_id = obj.get("device_id")
                    if integration_type == "tuya" and device_id:
                        # Start Tuya integration
                        try:
                            client = tinytuya.Device(
                                dev_id=device_id,
                                ip=None,
                                local_key=None,
                                version=3.3,
                                overwrite=True
                            )
                            # Scan for device
                            logger.info("[INTEGRATION] Started Tuya integration for device %s", device_id)
                            await send_json(websocket, {"type": "status", "detail": "integration_started", "integration": "tuya"})
                        except Exception as e:
                            logger.warning("[INTEGRATION] Tuya start failed: %s", e)
                            await send_json(websocket, {"type": "error", "detail": "integration_failed"})
                    elif integration_type == "google_home":
                        logger.info("[INTEGRATION] Started Google Home integration")
                        await send_json(websocket, {"type": "status", "detail": "integration_started", "integration": "google_home"})
                    elif integration_type == "wifi":
                        logger.info("[INTEGRATION] Started WiFi integration")
                        await send_json(websocket, {"type": "status", "detail": "integration_started", "integration": "wifi"})
                    continue
                if mtype == "integration_stop":
                    # Stop an integration
                    integration_type = obj.get("type")
                    logger.info("[INTEGRATION] Stopped %s integration", integration_type)
                    await send_json(websocket, {"type": "status", "detail": "integration_stopped", "integration": integration_type})
                    continue
                if mtype == "integration_device_action":
                    # Device-specific action
                    integration_type = obj.get("integration_type")
                    device_id = obj.get("device_id")
                    action = obj.get("action")
                    payload = obj.get("payload", {})
                    logger.info("[INTEGRATION] Action %s on %s: %s", action, device_id, payload)
                    await send_json(websocket, {"type": "status", "detail": "action_ack", "integration": integration_type, "device": device_id, "action": action})
                    continue
                if mtype == "camera_source":
                    source_type = obj.get("source")
                    camera_url = obj.get("url")
                    # Stop any existing camera loop.
                    if ip_camera_stop is not None:
                        ip_camera_stop.set()
                    if ip_camera_task is not None:
                        ip_camera_task.cancel()
                        ip_camera_task = None
                    ip_camera_stop = None
                    if rtsp_stop is not None:
                        rtsp_stop.set()
                    if rtsp_task is not None:
                        rtsp_task.cancel()
                        rtsp_task = None
                    rtsp_stop = None
                    camera_source = None
                    if source_type == "ip" and camera_url:
                        ip_camera_stop = asyncio.Event()
                        camera_source = {"type": "ip", "url": camera_url}
                        ip_camera_task = asyncio.create_task(
                            _ip_camera_loop(
                                websocket, quantizer, camera_url,
                                server_jpeg_quality, audio_gain, ip_camera_stop,
                                lambda: latest_audio,
                            )
                        )
                        logger.info("[CAMERA] Switched to IP camera: %s", camera_url)
                        await send_json(websocket, {"type": "status", "detail": "camera_connected"})
                    elif source_type == "rtsp" and camera_url:
                        rtsp_stop = asyncio.Event()
                        camera_source = {"type": "rtsp", "url": camera_url}
                        rtsp_task = asyncio.create_task(
                            _rtsp_stream_loop(
                                websocket, quantizer, camera_url,
                                server_jpeg_quality, rtsp_stop,
                                lambda: latest_audio, audio_gain,
                            )
                        )
                        logger.info("[CAMERA] Switched to RTSP stream: %s", camera_url)
                        await send_json(websocket, {"type": "status", "detail": "camera_connected"})
                    elif source_type == "onvif" and camera_url:
                        rtsp_stop = asyncio.Event()
                        camera_source = {"type": "onvif", "url": camera_url}
                        rtsp_task = asyncio.create_task(
                            _rtsp_stream_loop(
                                websocket, quantizer, camera_url,
                                server_jpeg_quality, rtsp_stop,
                                lambda: latest_audio, audio_gain,
                            )
                        )
                        logger.info("[CAMERA] Switched to ONVIF stream: %s", camera_url)
                        await send_json(websocket, {"type": "status", "detail": "camera_connected"})
                    elif source_type == "local":
                        camera_source = None
                        logger.info("[CAMERA] Switched to local camera")
                        await send_json(websocket, {"type": "status", "detail": "camera_connected"})
                    continue
                logger.debug("[WS] Unknown control message type: %s", mtype)
                continue

            if not text:
                continue

            # In IP/RTSP/ONVIF camera mode the backend generates frames; ignore
            # client-sent base64 frames to avoid conflicts.
            if camera_source and camera_source.get("type") in ("ip", "onvif", "rtsp"):
                continue

            jpeg_bytes = decode_jpeg_payload(text)
            if jpeg_bytes is None:
                await send_json(websocket, {"type": "error", "detail": "invalid_frame"})
                continue

            frame_count += 1
            if frame_count % 30 == 0:
                logger.info("[IMAGE] Received %d frames, last %d bytes", frame_count, len(jpeg_bytes))

            # Apply client-side audio gain before processing.
            audio = latest_audio
            if audio is not None and audio_gain != 1.0:
                audio = audio * audio_gain

            sent, reason = await _handle_frame(
                websocket, jpeg_bytes, binary_out=False,
                quantizer=quantizer, audio=audio,
                server_jpeg_quality=server_jpeg_quality,
            )
            if sent:
                waiting_status_count = 0
            elif reason == "waiting_for_mic":
                waiting_status_count += 1
                if (waiting_status_count == 1
                        or waiting_status_count % WAITING_STATUS_EVERY == 0):
                    await send_json(websocket, {"type": "status", "detail": "waiting_for_mic"})

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected (%s)", client)
    except Exception:
        logger.exception("[WS] Unexpected error")
    finally:
        if ip_camera_stop is not None:
            ip_camera_stop.set()
        if ip_camera_task is not None:
            ip_camera_task.cancel()
        if rtsp_stop is not None:
            rtsp_stop.set()
        if rtsp_task is not None:
            rtsp_task.cancel()
        logger.info("[WS] Session ended (%s) frames=%d", client, frame_count)


if TINYTUA_AVAILABLE:
    # Tuya device scanner helper
    def scan_tuya_devices():
        """Scan for Tuya devices on the local network.
        
        Returns a list of discovered Tuya devices with their info.
        Each device dict contains: id, address, name, etc.
        """
        devices = []
        try:
            # Use tinytuya's scan function to discover devices
            scan_results = tinytuya.scan(timeout=30)
            for device_id, device_info in scan_results.items():
                device = {
                    'id': device_id,
                    'address': device_info.get('ip', 'unknown'),
                    'version': device_info.get('version', 'unknown'),
                    'name': device_info.get('name', 'Unknown Device'),
                    'model': device_info.get('model', 'unknown'),
                }
                devices.append(device)
        except Exception as e:
            logger.warning("[TUYA] Scan error: %s", e)
        return devices


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000)
