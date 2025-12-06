
import base64
import json
import traceback
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from sound_device_processor import process_frame


app = FastAPI()


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[WS] Client connected")

    latest_audio: Optional[np.ndarray] = None
    client_sample_rate: Optional[float] = None
    server_jpeg_quality = 100
    audio_gain = 1.0
    frame_count = 0

    try:
        while True:
            # receive next message
            try:
                msg = await websocket.receive()
            except Exception as exc:
                print(f"[WS] Receive error: {exc}")
                break

            # binary = audio PCM (Float32Array)
            if "bytes" in msg:
                try:
                    data_bytes = msg["bytes"]
                    latest_audio = np.frombuffer(data_bytes, dtype=np.float32).copy()
                except Exception as exc:
                    print("[AUDIO] Failed to decode binary:", exc)
                continue

            # text = either JSON metadata or data URL / base64 image
            if "text" in msg:
                data = msg["text"]

                # metadata / control message
                if data and data.startswith("{"):
                    try:
                        obj = json.loads(data)
                        # audio info from client
                        if obj.get("type") == "audio_info" and "sampleRate" in obj:
                            client_sample_rate = float(obj["sampleRate"])
                            print(f"[AUDIO] Client sample rate: {client_sample_rate}")
                            continue
                        # client requests server-side JPEG encode quality
                        if obj.get("type") == "jpeg_quality" and "value" in obj:
                            try:
                                q = int(obj["value"])
                                q = max(10, min(100, q))
                                server_jpeg_quality = q
                                print(f"[CONFIG] Server JPEG quality set to: {server_jpeg_quality}")
                            except Exception:
                                print("[CONFIG] Invalid jpeg_quality value")
                            continue
                        # client requests audio gain adjustment
                        if obj.get("type") == "audio_gain" and "value" in obj:
                            try:
                                gain = float(obj["value"])
                                gain = max(0.1, min(5.0, gain))
                                audio_gain = gain
                                print(f"[CONFIG] Audio gain set to: {audio_gain}")
                            except Exception:
                                print("[CONFIG] Invalid audio_gain value")
                            continue
                    except Exception as exc:
                        print("[JSON] Failed to parse:", exc)
                        # fall through to treat as plain text if needed

                if not data:
                    # nothing to do
                    continue

                # handle data URL or plain base64
                if data.startswith("data:") and "," in data:
                    _, b64 = data.split(",", 1)
                else:
                    b64 = data

                try:
                    img_bytes = base64.b64decode(b64)
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is None:
                        print(f"[IMAGE] Frame {frame_count}: cv2.imdecode returned None")
                        await websocket.send_text("")
                        continue
                    frame_count += 1
                    if frame_count % 30 == 0:
                        print(f"[IMAGE] Received {frame_count} frames, shape: {img.shape}")
                except Exception as exc:
                    print(f"[IMAGE] Failed to decode: {exc}")
                    traceback.print_exc()
                    await websocket.send_text("")
                    continue

                # performance: skip audio processing on alternating frames
                skip_audio = (frame_count % 2 == 0)

                # apply audio gain before processing
                audio_to_process = latest_audio
                if audio_to_process is not None and audio_gain != 1.0:
                    audio_to_process = audio_to_process * audio_gain

                # process
                try:
                    processed = process_frame(img, audio_chunk=audio_to_process, skip_audio=skip_audio)
                    if processed is None:
                        print("[PROCESS] process_frame returned None, sending original frame")
                        processed = img
                except Exception as exc:
                    print(f"[PROCESS] Error: {exc}")
                    traceback.print_exc()
                    processed = img

                # encode and send using server_jpeg_quality
                try:
                    _, buf = cv2.imencode(".jpg", processed, [cv2.IMWRITE_JPEG_QUALITY, server_jpeg_quality])
                    out_b64 = base64.b64encode(buf.tobytes()).decode("ascii")
                    response = "data:image/jpeg;base64," + out_b64
                    await websocket.send_text(response)
                except Exception as exc:
                    print(f"[RESPONSE] Failed to encode/send: {exc}")
                    traceback.print_exc()

    except WebSocketDisconnect:
        print("[WS] Client disconnected")
    except Exception as exc:
        print(f"[WS] Error: {exc}")
        traceback.print_exc()
