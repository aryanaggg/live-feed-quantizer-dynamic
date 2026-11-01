"""Local Bridge service for network cameras.

The Bridge is an *optional* companion app.  It listens on loopback only, fetches
RTSP/HTTP camera frames, and forwards unquantized JPEG frames to the browser over
a WebSocket.  The browser still owns the palette and every WebGL mode: the
Bridge never quantizes and never sees audio.

Security model (see ``docs/ARCHITECTURE_REVIEW.md``):

* every HTTP request and WebSocket upgrade must carry a loopback ``Host`` header
  (DNS-rebinding defence),
* every browser ``Origin`` must be on the Bridge allowlist,
* once paired, the browser receives a per-origin token that every WebSocket
  connection must present,
* control messages are size/type/rate limited, camera URLs are validated with
  the same SSRF rules as the hosted server, and private LAN addresses are
  allowed on purpose (that is the Bridge's job).

Run it with ``python -m uvicorn bridge:app --host 127.0.0.1 --port 8765``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from frame_gateway import (
    DEFAULT_FPS,
    DEFAULT_MAX_DIM,
    DEFAULT_QUALITY,
    MAX_DIM_LIMIT,
    MIN_DIM_LIMIT,
    FrameGateway,
    Subscription,
    encode_packet,
    validate_source,
)
from frame_gateway import register_light_drivers, LIGHT_METHODS, LightDriverError
from security import (
    RateLimiter,
    URLValidationError,
    bridge_host_allowed,
    normalize_origin,
    origin_allowed,
    pairing_store,
)

logger = logging.getLogger("live_feed_quantizer.bridge")

BRIDGE_VERSION = "2.0"
MAX_CONTROL_BYTES = 4096
STREAM_ERROR_INTERVAL = 1.0   # at most one stream_error per second
STREAM_ERROR_BACKOFF = 0.1

pair_limiter = RateLimiter(limit=12, window=60)
bridge_http_limiter = RateLimiter(limit=120, window=60)
bridge_ws_limiter = RateLimiter(limit=settings.ws_message_rate_limit, window=60)


def _origin_ok(headers) -> bool:
    return origin_allowed(headers.get("origin"), settings.bridge_allowed_origins)


def _host_ok(headers) -> bool:
    return bridge_host_allowed(headers.get("host"), settings.bridge_allowed_hosts)


def _json(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})


async def _send_json(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def _stream_frames(ws: WebSocket, subscription: Subscription) -> None:
    """Forward frames until the socket closes or the subscription ends.

    ``Subscription`` is already latest-frame-wins, so slow clients naturally
    skip old frames instead of building a backlog.  Repeated fetch failures are
    throttled to one message per second.
    """
    last_error_at = 0.0
    last_reason = None
    while True:
        try:
            packet = await subscription.next()
        except RuntimeError as exc:
            reason = (str(exc) or exc.__class__.__name__)[:200]
            now = time.monotonic()
            if reason != last_reason or now - last_error_at >= STREAM_ERROR_INTERVAL:
                if not await _send_json(ws, {"type": "stream_error", "detail": reason}):
                    return
                last_error_at = now
                last_reason = reason
            await asyncio.sleep(STREAM_ERROR_BACKOFF)
            continue

        if packet is None:
            continue

        epoch, timestamp, jpeg = packet
        try:
            await ws.send_bytes(encode_packet(epoch, timestamp, jpeg))
        except Exception:
            return


def create_app(
    gateway: FrameGateway | None = None,
    light_drivers: dict | None = None,
) -> FastAPI:
    """Build the Bridge app.  Tests may inject a gateway and/or light drivers."""

    shared_gateway = gateway or FrameGateway()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await shared_gateway.close()
            logger.info("[BRIDGE] Gateway closed")

    app = FastAPI(title="Live Feed Quantizer Bridge", version=BRIDGE_VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in dict.fromkeys(settings.bridge_allowed_origins) if o],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )

    register_light_drivers(app, shared_gateway, drivers=light_drivers)

    @app.middleware("http")
    async def enforce_loopback_host(request: Request, call_next):
        # A malicious domain that resolves to 127.0.0.1 still sends its own
        # Host header, so this blocks DNS rebinding.
        if not _host_ok(request.headers):
            return _json({"error": "host_not_allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/health")
    async def health(request: Request):
        if not _origin_ok(request.headers):
            return _json({"error": "origin_not_allowed"}, status_code=403)
        ip = request.client.host if request.client else "unknown"
        if not bridge_http_limiter.allow(ip):
            return _json({"error": "rate_limited"}, status_code=429)
        return _json(
            {
                "status": "ok",
                "service": "live-feed-quantizer-bridge",
                "bridge": True,
                "version": BRIDGE_VERSION,
                "frameFormat": "jpeg-epoch-v1",
            }
        )

    @app.post("/pair")
    async def pair(request: Request):
        origin = request.headers.get("origin")
        if not origin or normalize_origin(origin) is None:
            return _json({"error": "origin_required"}, status_code=403)
        if not _origin_ok(request.headers):
            return _json({"error": "origin_not_allowed"}, status_code=403)

        ip = request.client.host if request.client else "unknown"
        if not pair_limiter.allow(ip) or not bridge_http_limiter.allow(ip):
            return _json({"error": "rate_limited"}, status_code=429)

        token = pairing_store.issue(origin)
        logger.info("[BRIDGE] Paired origin %s", normalize_origin(origin))
        return _json(
            {
                "token": token,
                "version": BRIDGE_VERSION,
                "origin": normalize_origin(origin),
                "limits": {
                    "minDim": MIN_DIM_LIMIT,
                    "maxDim": MAX_DIM_LIMIT,
                    "defaultDim": DEFAULT_MAX_DIM,
                    "defaultQuality": DEFAULT_QUALITY,
                    "defaultFps": DEFAULT_FPS,
                },
            }
        )

    @app.websocket("/ws")
    async def bridge_socket(websocket: WebSocket):
        # Accept first so protocol errors can be reported as JSON before close.
        await websocket.accept()

        if not _host_ok(websocket.headers):
            await _send_json(websocket, {"type": "error", "detail": "host_not_allowed"})
            await websocket.close(code=1008)
            return
        if not _origin_ok(websocket.headers):
            await _send_json(websocket, {"type": "error", "detail": "origin_not_allowed"})
            await websocket.close(code=1008)
            return

        origin = websocket.headers.get("origin")
        token = websocket.query_params.get("token")
        if not pairing_store.verify(origin, token):
            await _send_json(websocket, {"type": "error", "detail": "pairing_required"})
            await websocket.close(code=1008)
            return

        client = websocket.client.host if websocket.client else "unknown"
        logger.info("[BRIDGE] Client connected (%s, %s)", client, normalize_origin(origin))

        subscription: Subscription | None = None
        sender: asyncio.Task | None = None

        async def stop_subscription() -> None:
            nonlocal subscription, sender
            if sender is not None:
                sender.cancel()
                try:
                    await sender
                except (asyncio.CancelledError, Exception):
                    pass
                sender = None
            if subscription is not None:
                sub, subscription = subscription, None
                await shared_gateway.unsubscribe(sub)

        if not await _send_json(
            websocket,
            {
                "type": "ready",
                "version": BRIDGE_VERSION,
                "maxDim": MAX_DIM_LIMIT,
            },
        ):
            await websocket.close(code=1011)
            return

        try:
            while True:
                text = await websocket.receive_text()
                if len(text.encode("utf-8")) > MAX_CONTROL_BYTES:
                    await _send_json(websocket, {"type": "error", "detail": "message_too_large"})
                    continue
                if not bridge_ws_limiter.allow(client):
                    await _send_json(websocket, {"type": "error", "detail": "rate_limited"})
                    break

                try:
                    obj = json.loads(text)
                except ValueError:
                    await _send_json(websocket, {"type": "error", "detail": "bad_json"})
                    continue
                if not isinstance(obj, dict):
                    await _send_json(websocket, {"type": "error", "detail": "bad_message"})
                    continue

                mtype = obj.get("type")
                if mtype == "ping":
                    await _send_json(websocket, {"type": "pong"})
                    continue

                if mtype == "unsubscribe":
                    await stop_subscription()
                    await _send_json(websocket, {"type": "unsubscribed"})
                    continue

                if mtype != "subscribe":
                    await _send_json(websocket, {"type": "error", "detail": "unsupported_type"})
                    continue

                try:
                    options = await asyncio.to_thread(
                        validate_source,
                        str(obj.get("kind") or ""),
                        obj.get("url"),
                        allow_private=True,
                        max_dim=obj.get("maxDim", DEFAULT_MAX_DIM),
                        quality=obj.get("quality", DEFAULT_QUALITY),
                        fps=obj.get("fps", DEFAULT_FPS),
                    )
                except URLValidationError as exc:
                    await _send_json(websocket, {"type": "error", "detail": exc.reason})
                    continue
                except (TypeError, ValueError):
                    await _send_json(websocket, {"type": "error", "detail": "bad_source"})
                    continue

                await stop_subscription()
                try:
                    subscription = await shared_gateway.subscribe(options)
                except RuntimeError as exc:
                    await _send_json(websocket, {"type": "error", "detail": str(exc)})
                    continue

                sender = asyncio.create_task(_stream_frames(websocket, subscription))
                await _send_json(
                    websocket,
                    {
                        "type": "subscribed",
                        "epoch": subscription.fetcher.epoch,
                        "kind": options.kind,
                        "maxDim": options.max_dim,
                        "quality": options.quality,
                        "fps": options.fps,
                    },
                )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("[BRIDGE] Unexpected socket error")
        finally:
            await stop_subscription()
            logger.info("[BRIDGE] Client disconnected (%s)", client)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("bridge:app", host="127.0.0.1", port=settings.bridge_port)
