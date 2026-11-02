"""Shared camera frame gateway used by the local Bridge.

One fetcher per ``(kind, url, maxDim, quality, fps)`` is shared by every
subscriber (multiple tabs, multiple browser clients).  Each fetcher owns an
epoch: when it is recreated after the last subscriber leaves, the epoch
increments, so a late frame from an old source can be discarded by the client.

Wire format for one frame::

    struct.pack("<Id", epoch, unix_timestamp) + jpeg_bytes

The browser decodes the JPEG with ``createImageBitmap`` and uploads it to the
same WebGL texture it uses for local cameras.  The Bridge never owns a palette
and never quantizes frames for the hosted client.

Light driver support (Music-reactive smart lights):
- LightDriver interface: discover(), pair(credentials?), setState({r,g,b,brightness,transition}),
  capabilities(), status().
- WiZ driver: UDP broadcast, no keys; proves latency end-to-end.
- Tuya local: tinytuya protocol 3.3/3.4/3.5; local key stored Bridge-side only.
- Stub drivers for Hue, LIFX, Govee, Matter (pending).
"""

# ... (rest of the file remains the same)

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from security import URLValidationError, redact_url, validate_fetch_url
from light_zones import LightZoneConfig, LightZoneError

logger = logging.getLogger("live_feed_quantizer.gateway")

MAX_CONTROL_BYTES = 4096


async def _send_json(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False

FRAME_HEADER = struct.Struct("<Id")  # epoch (uint32) + timestamp (float64)
DEFAULT_MAX_DIM = 960
DEFAULT_QUALITY = 60
DEFAULT_FPS = 24.0
MAX_DIM_LIMIT = 1920
MIN_DIM_LIMIT = 160
MAX_SUBSCRIBERS = 16

FetchFn = Callable[[str], Any]


def encode_packet(epoch: int, timestamp: float, jpeg: bytes) -> bytes:
    return FRAME_HEADER.pack(int(epoch) & 0xFFFFFFFF, float(timestamp)) + jpeg


def decode_packet(data: bytes) -> tuple[int, float, bytes]:
    if len(data) < FRAME_HEADER.size:
        raise ValueError("packet too short")
    epoch, timestamp = FRAME_HEADER.unpack_from(data, 0)
    return epoch, timestamp, data[FRAME_HEADER.size:]


def clamp_options(max_dim: Any = DEFAULT_MAX_DIM, quality: Any = DEFAULT_QUALITY,
                  fps: Any = DEFAULT_FPS) -> tuple[int, int, float]:
    try:
        max_dim = int(max_dim)
    except (TypeError, ValueError):
        max_dim = DEFAULT_MAX_DIM
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        quality = DEFAULT_QUALITY
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        fps = DEFAULT_FPS
    max_dim = max(MIN_DIM_LIMIT, min(MAX_DIM_LIMIT, max_dim))
    quality = max(10, min(95, quality))
    fps = max(1.0, min(30.0, fps))
    return max_dim, quality, fps


@dataclass(frozen=True)
class FetchOptions:
    kind: str          # "http" or "rtsp"
    url: str
    max_dim: int = DEFAULT_MAX_DIM
    quality: int = DEFAULT_QUALITY
    fps: float = DEFAULT_FPS
    quantize: bool = False

    def key(self) -> tuple:
        return (self.kind, self.url, self.max_dim, self.quality, round(self.fps, 2), self.quantize)

    @classmethod
    def build(cls, kind: str, url: str, max_dim=DEFAULT_MAX_DIM,
              quality=DEFAULT_QUALITY, fps=DEFAULT_FPS, quantize=False) -> "FetchOptions":
        kind = (kind or "").lower()
        if kind not in {"http", "rtsp"}:
            raise ValueError("unsupported_kind")
        max_dim, quality, fps = clamp_options(max_dim, quality, fps)
        return cls(kind=kind, url=url, max_dim=max_dim, quality=quality, fps=fps, quantize=bool(quantize))


def process_frame(raw: Any, options: FetchOptions) -> tuple[bytes, int, int]:
    """Decode one source frame, optionally downscale, and re-encode it.

    ``quantize`` is accepted for protocol compatibility but the Bridge never
    applies a palette: the browser owns the look.  Frames therefore retain all
    their colours, which is exactly what the WebGL shader needs.
    """
    if isinstance(raw, (bytes, bytearray)):
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    else:
        img = np.asarray(raw)
    if img is None or img.size == 0:
        raise ValueError("invalid_frame")
    if img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("invalid_frame")

    h, w = img.shape[:2]
    scale = min(options.max_dim / max(h, w), 1.0)
    if scale < 1.0:
        img = cv2.resize(
            img,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
        h, w = img.shape[:2]

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), options.quality])
    if not ok:
        raise ValueError("encode_failed")
    return buf.tobytes(), w, h


class HttpSnapshotReader:
    """One HTTP(S) snapshot URL; a fresh request per frame."""

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def read(self) -> bytes:
        req = urllib.request.Request(self.url, headers={"User-Agent": "LiveFeedQuantizer-Bridge/2.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = resp.read()
        if not data or data[:2] != b"\xff\xd8":
            raise ValueError("not_jpeg")
        return data


class RtspFrameReader:
    """Keep one OpenCV capture open and pull the freshest frame."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.cap = None

    def _open(self) -> None:
        if self.cap is not None:
            return
        self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            raise ValueError("rtsp_open_failed")
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    def read(self) -> Any:
        self._open()
        # Drop buffered frames so we stay near live.
        for _ in range(3):
            self.cap.grab()
        ok, frame = self.cap.retrieve()
        if not ok or frame is None:
            self.close()
            raise ValueError("rtsp_read_failed")
        return frame

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


def make_reader(options: FetchOptions):
    if options.kind == "http":
        return HttpSnapshotReader(options.url)
    if options.kind == "rtsp":
        return RtspFrameReader(options.url)
    raise ValueError("unsupported_kind")


class Subscription:
    """Latest-frame-wins mailbox.  Never grows beyond one pending frame."""

    def __init__(self, fetcher: "FrameFetcher") -> None:
        self.fetcher = fetcher
        self._packet: tuple[int, float, bytes] | None = None
        self._error: str | None = None
        self._event = asyncio.Event()
        self.closed = False

    def push(self, epoch: int, timestamp: float, jpeg: bytes) -> None:
        if self.closed:
            return
        self._packet = (epoch, timestamp, jpeg)
        self._event.set()

    def push_error(self, reason: str) -> None:
        if self.closed:
            return
        self._error = reason
        self._event.set()

    async def next(self, timeout: float | None = None) -> tuple[int, float, bytes] | None:
        """Wait for the next frame (or error, reported as an exception)."""
        if self._packet is None and not self._error:
            try:
                if timeout is None:
                    await self._event.wait()
                else:
                    await asyncio.wait_for(self._event.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        self._event.clear()
        if self._error:
            error, self._error = self._error, None
            raise RuntimeError(error)
        packet, self._packet = self._packet, None
        return packet

    def close(self) -> None:
        self.closed = True
        self._event.set()


class FrameFetcher:
    def __init__(self, gateway: "FrameGateway", options: FetchOptions, epoch: int,
                 fetch_fn: FetchFn | None = None) -> None:
        self.gateway = gateway
        self.options = options
        self.epoch = epoch
        self.fetch_fn = fetch_fn
        self.reader = None if fetch_fn is not None else make_reader(options)
        self.subscribers: set[Subscription] = set()
        self.task: asyncio.Task | None = None
        self.closed = False
        self.frames = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name=f"frame-fetcher-{self.options.kind}")

    def subscribe(self) -> Subscription:
        if len(self.subscribers) >= MAX_SUBSCRIBERS:
            raise RuntimeError("too_many_subscribers")
        sub = Subscription(self)
        self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.close()
        self.subscribers.discard(sub)

    def _read(self) -> Any:
        if self.fetch_fn is not None:
            return self.fetch_fn(self.options.url)
        return self.reader.read()

    async def run(self) -> None:
        period = 1.0 / self.options.fps
        loop = asyncio.get_running_loop()
        while not self.closed:
            started = loop.time()
            try:
                raw = await asyncio.to_thread(self._read)
                jpeg, _w, _h = await asyncio.to_thread(process_frame, raw, self.options)
                timestamp = time.time()
                self.frames += 1
                self.last_error = None
                for sub in list(self.subscribers):
                    sub.push(self.epoch, timestamp, jpeg)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_error = str(exc) or exc.__class__.__name__
                logger.warning(
                    "[GATEWAY] %s fetch failed for %s: %s",
                    self.options.kind,
                    redact_url(self.options.url),
                    self.last_error,
                )
                for sub in list(self.subscribers):
                    sub.push_error(self.last_error)
            elapsed = loop.time() - started
            try:
                await asyncio.sleep(max(0.0, period - elapsed))
            except asyncio.CancelledError:
                break
        if self.reader is not None:
            self.reader.close()

    async def stop(self) -> None:
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.reader is not None:
            self.reader.close()


class FrameGateway:
    def __init__(self, fetch_fn: FetchFn | None = None) -> None:
        self._fetchers: dict[tuple, FrameFetcher] = {}
        self._lock = asyncio.Lock()
        self._epoch = 0
        self.fetch_fn = fetch_fn

    async def subscribe(self, options: FetchOptions, fetch_fn: FetchFn | None = None) -> Subscription:
        async with self._lock:
            key = options.key()
            fetcher = self._fetchers.get(key)
            if fetcher is None:
                self._epoch += 1
                fetcher = FrameFetcher(self, options, self._epoch, fetch_fn=fetch_fn or self.fetch_fn)
                fetcher.start()
                self._fetchers[key] = fetcher
            return fetcher.subscribe()

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            fetcher = sub.fetcher
            fetcher.unsubscribe(sub)
            if not fetcher.subscribers:
                key = fetcher.options.key()
                if self._fetchers.get(key) is fetcher:
                    self._fetchers.pop(key, None)
                await fetcher.stop()

    async def close(self) -> None:
        async with self._lock:
            fetchers = list(self._fetchers.values())
            self._fetchers.clear()
        for fetcher in fetchers:
            await fetcher.stop()

    def stats(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": f.options.kind,
                "url": redact_url(f.options.url),
                "epoch": f.epoch,
                "subscribers": len(f.subscribers),
                "frames": f.frames,
                "lastError": f.last_error,
            }
            for f in self._fetchers.values()
        ]


def validate_source(
    kind: str,
    url: str,
    *,
    allow_private: bool = True,
    max_dim: Any = DEFAULT_MAX_DIM,
    quality: Any = DEFAULT_QUALITY,
    fps: Any = DEFAULT_FPS,
) -> FetchOptions:
    """Validate a camera URL for the Bridge (private LAN allowed)."""
    kind = (kind or "").lower()
    if kind == "http":
        validate_fetch_url(url, allow_private=allow_private, allowed_schemes={"http", "https"}, resolve=True)
    elif kind == "rtsp":
        validate_fetch_url(url, allow_private=allow_private, allowed_schemes={"rtsp", "rtsps"}, resolve=True)
    else:
        raise URLValidationError("unsupported_kind")
    return FetchOptions.build(kind, url, max_dim=max_dim, quality=quality, fps=fps, quantize=False)


# ============================================================================
# LightDriver interface and implementations
# ============================================================================

LIGHT_DRIVER_VERSION = "1.0"

#
# LightDriver — abstract base for smart-light controllers.
#
# The client (browser) owns the audio → light mapping and sends light frames
# (colour + brightness per device/group) over the Bridge WS at <= 30 Hz.
# The Bridge merely coalesces latest-frame-wins per driver and enforces a per-
# driver max rate.  Credentials are never sent to the browser.
#

class LightDriverError(RuntimeError):
    """Raised when a light driver operation fails."""


class LightDriver:
    """Interface for smart-light drivers.

    Bridge implementations must override the concrete methods; defaults raise
    ``LightDriverError("not_implemented")``.
    """

    def __init__(self) -> None:
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Discover light fixtures on the local network.

        Returns a list of dicts with at least: id, address, name, model.
        """
        raise LightDriverError("not_implemented")

    async def pair(self, credentials: dict | None = None) -> str:
        """Obtain or confirm a pairing token.

        Returns a token string that the Bridge stores and presents to the browser.
        Credentials are persisted Bridge-side only; never sent to the browser.
        """
        raise LightDriverError("not_implemented")

    async def set_state(self, state: dict) -> None:
        """Set light state: ``{r,g,b,brightness,transition}``.

        ``r,g,b`` are 0–255 integers.  ``brightness`` is 0–100 percent.
        ``transition`` is seconds to fade; 0 means instant.
        """
        raise LightDriverError("not_implemented")

    def capabilities(self) -> dict:
        """Return driver capabilities: ``{supports_rgb, supports_brightness,
        supports_transition, max_brightness, max_transition, rate_mode,
        max_rate_hz}``."""
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        """Return current driver status: ``{online, devices}``."""
        return {"online": False, "devices": []}

    def latency_ms(self) -> float:
        """Return measured end-to-end latency in milliseconds."""
        return self._latency_ms

    def record_latency(self, latency_ms: float) -> None:
        """Store a measured round-trip time; negative values are clamped to 0."""
        self._latency_ms = max(0.0, float(latency_ms))

    def latency_diagnostics(self) -> float | str:
        """Return a measured latency, or ``"unmeasurable"`` when none exists."""
        value = self.latency_ms()
        if value is None or value <= 0:
            return "unmeasurable"
        return round(float(value), 1)


#
# WiZ driver — UDP broadcast, no keys, end-to-end latency proof.
#
# WiZ lights listen on UDP port 1930 for discovery commands and respond with
# their IP, port, and friendly name.  No authentication keys are required,
# making it the fastest way to prove the Bridge‑to‑light latency path.
#


WIZ_PORT = 38899
WIZ_TIMEOUT = 2.0


def _wiz_broadcast_addresses() -> List[str]:
    """Return global and subnet-directed broadcast addresses for discovery."""
    addresses = ["255.255.255.255"]
    override = os.environ.get("WIZ_BROADCAST")
    if override:
        addresses.insert(0, override)
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            local_ip = probe.getsockname()[0]
        finally:
            probe.close()
        subnet_broadcast = ".".join(local_ip.split(".")[:3] + ["255"])
        if subnet_broadcast not in addresses:
            addresses.append(subnet_broadcast)
    except OSError:
        pass
    return addresses


def _wiz_request(host: str, port: int, method: str, params: dict,
                 timeout: float = WIZ_TIMEOUT) -> tuple[dict, float, tuple]:
    """Send one WiZ JSON command and wait for the bulb acknowledgement.

    Returns ``(response, round_trip_ms, peer_address)``.  The round-trip is
    measured from ``sendto`` to the matching ``recvfrom``, so it is a real
    device ack rather than a client-side estimate.
    """
    payload = json.dumps({"method": method, "params": params}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        start = time.perf_counter()
        sock.sendto(payload, (host, port))
        data, addr = sock.recvfrom(4096)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
    except OSError as exc:
        raise LightDriverError(f"wiz_io_error: {type(exc).__name__}") from exc
    finally:
        sock.close()
    try:
        response = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LightDriverError("wiz_bad_response") from exc
    if not isinstance(response, dict):
        raise LightDriverError("wiz_bad_response")
    return response, elapsed_ms, addr


def _parse_device_address(address: str, default_port: int) -> tuple[str, int]:
    """Split ``scheme://host[:port]`` into ``(host, port)``."""
    value = address.split("://", 1)[-1]
    host, _, port = value.partition(":")
    return host, int(port) if port else default_port


async def _wiz_discover(timeout: float = 2.0) -> List[dict]:
    """Discover WiZ fixtures with a broadcast ``getPilot`` request.

    WiZ bulbs speak JSON over UDP 38899.  Broadcast the state query and use
    each response's source address plus MAC to build a stable device id.
    """
    fixtures: dict[str, dict] = {}
    payload = json.dumps({"method": "getPilot", "params": {}}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        for broadcast in _wiz_broadcast_addresses():
            try:
                sock.sendto(payload, (broadcast, WIZ_PORT))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                response = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            result = response.get("result") if isinstance(response, dict) else None
            if not isinstance(result, dict):
                continue
            ip_str, port = addr[0], addr[1]
            mac = str(result.get("mac") or "").replace(":", "").lower()
            device_id = f"wiz:{mac}" if mac else f"wiz:{ip_str}"
            fixtures[device_id] = {
                "id": device_id,
                "address": f"wiz://{ip_str}:{port}",
                "name": f"WiZ light ({ip_str})",
                "model": str(result.get("moduleName") or "wiz"),
                "kind": "wiz",
            }
    except Exception as exc:
        logger.warning("[WiZ] Discover error: %s", exc)
    finally:
        sock.close()
    return list(fixtures.values())


class WizDriver(LightDriver):
    """WiZ light driver — UDP broadcast, no authentication."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._devices: dict[str, dict] = {}
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        discovered = await _wiz_discover()
        self._devices = {d["id"]: d for d in discovered}
        return list(self._devices.values())

    async def pair(self, credentials: dict | None = None) -> str:
        # WiZ needs no credentials; generate a per-origin token for Bridge-side use.
        import secrets
        self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Send a colour/brightness command to a WiZ light via UDP.

        Uses the documented ``setPilot`` JSON command and waits for the
        ``{"result": {"success": true}}`` acknowledgement, recording the real
        ack round-trip for diagnostics.  WiZ has no fade duration parameter in
        ``setPilot`` (``speed`` controls scene animation), so ``transition`` is
        intentionally ignored here.
        """
        dev_id = state.get("device_id")
        device = self._devices.get(dev_id)
        if device is None:
            raise LightDriverError("unknown_device")

        host, port = _parse_device_address(device.get("address", ""), WIZ_PORT)
        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        params = {"r": r, "g": g, "b": b, "dimming": brightness, "state": True}

        response, elapsed_ms, _ = await asyncio.to_thread(
            _wiz_request, host, port, "setPilot", params, WIZ_TIMEOUT,
        )
        result = response.get("result")
        if not isinstance(result, dict) or not result.get("success"):
            raise LightDriverError("wiz_ack_failed")
        self.record_latency(elapsed_ms)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}


#
# Tuya local driver — tinytuya protocol 3.3/3.4/3.5.
#
# A local Tuya key is obtained once via the Tuya/Smart Life cloud OAuth flow
# and stored Bridge-side only.  The key never leaves the Bridge and is never
# sent to the browser.


async def _tuya_scan(timeout: float = 5.0) -> List[dict]:
    """Scan for Tuya devices using tinytuya's network scanner.

    ``tinytuya.deviceScan`` returns a mapping of ``ip -> info``; the public
    ``tinytuya.scan`` helper only prints and returns ``None``, so it cannot be
    used here.  The gateway id (``gwId``) is the stable device id and is also
    what the local-key handshake needs.
    """
    fixtures: List[dict] = []
    try:
        import tinytuya
        results = tinytuya.deviceScan(
            verbose=False, maxretry=max(1, int(timeout)), poll=False,
        )
    except ImportError:
        logger.warning("[Tuya] tinytuya not available, skipping")
        return fixtures
    except Exception as exc:
        logger.debug("[Tuya] Scan error: %s", exc)
        return fixtures

    for ip, info in (results or {}).items():
        if not isinstance(info, dict):
            continue
        gateway_id = str(info.get("gwId") or info.get("id") or ip)
        fixtures.append({
            "id": f"tuya:{gateway_id}",
            "device_id": gateway_id,
            "address": f"tuya://{ip}",
            "ip": ip,
            "name": info.get("name") or f"Tuya device ({ip})",
            "model": info.get("productKey", ""),
            "version": info.get("version") or 3.3,
            "kind": "tuya",
        })
    return fixtures


class TuyaDriver(LightDriver):
    """Tuya local light driver — requires Bridge-stored local key."""

    def __init__(self, bridge, credential_store=None) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._key: str | None = None  # stored Bridge-side only
        self._store = credential_store
        self._devices: dict[str, dict] = {}
        self._latency_ms: float = 0.0
        self._timeout: float = 2.0

    async def discover(self) -> List[dict]:
        discovered = await _tuya_scan()
        self._devices = {d["id"]: d for d in discovered}
        return list(self._devices.values())

    async def pair(self, credentials: dict | None = None) -> str:
        """Store a Tuya local key (obtained via Tuya cloud OAuth).

        ``credentials`` may contain ``local_key`` and ``device_id``.  The key is
        persisted Bridge-side only and never logged or sent to the browser.
        """
        key = credentials.get("local_key") if credentials else None
        if key:
            device_id = str(credentials.get("device_id") or "").strip()
            if device_id and self._store is not None:
                self._store.set(device_id, key)
            else:
                self._key = key
        # Generate a per-origin token for Bridge internal use
        import secrets
        self._token = secrets.token_urlsafe(24)
        return self._token

    def _key_for(self, device_id: str | None) -> str | None:
        if self._store is not None and device_id:
            stored = self._store.get(str(device_id))
            if stored:
                return stored
        return self._key

    def clear_credentials(self, device_id: str | None = None) -> None:
        """Remove Bridge-stored key material for one device or all devices."""
        self._key = None
        if self._store is None:
            return
        if device_id:
            self._store.delete(device_id)
        else:
            self._store.clear()

    async def set_state(self, state: dict) -> None:
        """Send a colour/brightness command to a Tuya light via tinytuya.

        Requires a Bridge-stored local key from :meth:`pair` or the onboarding
        endpoint.  The key is only passed to ``tinytuya.BulbDevice`` and is
        never logged or returned.  The command blocks on the device ack, so it
        runs in a worker thread and the measured round-trip is recorded for
        diagnostics.
        """
        dev_id = state.get("device_id")
        device = self._devices.get(dev_id)
        if device is None:
            raise LightDriverError("unknown_device")
        gateway_id = str(device.get("device_id") or dev_id)
        local_key = self._key_for(gateway_id)
        if not local_key:
            raise LightDriverError("needs_credentials")
        try:
            import tinytuya
        except ImportError as exc:
            raise LightDriverError("tinytuya_not_installed") from exc

        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        host = device.get("ip") or _parse_device_address(device.get("address", ""), 6668)[0]
        try:
            version = float(device.get("version") or 3.3)
        except (TypeError, ValueError):
            version = 3.3

        def _send() -> tuple[dict, dict | None, float]:
            bulb = tinytuya.BulbDevice(gateway_id, host, local_key)
            try:
                bulb.set_version(version)
                bulb.set_socketTimeout(self._timeout)
                bulb.set_socketPersistent(False)
                start = time.perf_counter()
                colour_result = bulb.set_colour(r, g, b)
                brightness_result = bulb.set_brightness_percentage(brightness)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return colour_result, brightness_result, elapsed_ms
            finally:
                bulb.close()

        colour_result, brightness_result, elapsed_ms = await asyncio.to_thread(_send)
        for result in (colour_result, brightness_result):
            if isinstance(result, dict) and "Error" in result:
                raise LightDriverError(f"tuya_error: {result.get('Error')}")
        self.record_latency(elapsed_ms)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def _public_device(self, device: dict) -> dict:
        gateway_id = str(device.get("device_id") or device.get("id"))
        ready = bool(self._key_for(gateway_id))
        return {
            "id": device.get("id"),
            "name": device.get("name"),
            "ready": ready,
            "needs_credentials": not ready,
            "state": "ready" if ready else "needs_credentials",
        }

    def status(self) -> dict:
        devices = [self._public_device(d) for d in self._devices.values()]
        ready = any(device["ready"] for device in devices)
        latency = self.latency_diagnostics()
        return {"online": bool(devices),
                "state": "ready" if ready else "needs_credentials",
                "latencyMs": latency,
                "devices": devices}


#
# Bridge-light integration: register drivers and expose WS endpoints.
#

# ============================================================================
# Additional Light Drivers
# ============================================================================

#
# LIFX LAN driver - native LAN control without cloud.
#
# LIFX lights support direct LAN communication using their native protocol.
# Discovery is via mDNS/_disc._tcp protocol.


class LifxDriver(LightDriver):
    """LIFX light driver — LAN native control."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._devices: dict[str, dict] = {}
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Discover LIFX fixtures via mDNS."""
        import asyncio
        import socket

        fixtures: List[dict] = []
        try:
            # mDNS query for LIFX
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)

            # LIFX discovery protocol
            query = b""
            sock.sendto(query, ("255.255.255.255", 5678))

            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                    # Parse LIFX response
                    # Response format: 16-byte header + device info
                    if len(data) >= 32:
                        # Source IP port
                        source_port = data[0] | (data[1] << 8)
                        # Device type
                        dev_type = data[2]
                        # Name
                        name = data[24:52].decode("utf-8", errors="ignore").rstrip("")
                        # Model
                        model = data[40:52].decode("utf-8", errors="ignore").rstrip("")

                        ip_str = ".".join(str(b) for b in data[4:8])
                        fixtures.append({
                            "id": f"lifx:{ip_str}",
                            "address": f"lifx:{ip_str}",
                            "name": name or "LIFX light",
                            "model": model or "lifx",
                            "kind": "lifx",
                        })
                except socket.timeout:
                    break
            sock.close()
        except Exception as exc:
            logger.warning("[LIFX] Discover error: %s", exc)
        return fixtures

    async def pair(self, credentials: dict | None = None) -> str:
        """LIFX requires no pairing; generate per-origin token."""
        import secrets
        self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Send a colour/brightness command to a LIFX light via UDP."""
        dev_id = state.get("device_id")
        if not dev_id or dev_id not in self._devices:
            logger.warning("[LIFX] Unknown device %s", dev_id)
            return

        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        transition = max(0, float(state.get("transition", 0)))

        ip_port = self._devices[dev_id]["address"].replace("lifx://", "")
        ip_str, port_str = ip_port.split(":")
        port = int(port_str)

        # LIFX colour command (simplified)
        # 0x01 + r + g + b + brightness(1 byte) + transition(1 byte)
        cmd = bytes([0x01, r, g, b, brightness, int(transition / 100 * 255)])
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(cmd, (ip_str, port))
            sock.close()
        except Exception as exc:
            logger.warning("[LIFX] set_state error for %s: %s", dev_id, exc)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}


#
# Govee LAN driver - Govee lights via UDP or TCP.
#

import base64 as _b64


def _govee_xor_decrypt(data: bytes, key: bytes) -> bytes:
    """Govee uses simple XOR encryption with a derived key."""
    return bytes(a ^ key[i % len(key)] for i, a in enumerate(data))


class GoveeDriver(LightDriver):
    """Govee light driver — LAN control via UDP/TCP."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._key: str | None = None  # local key, Bridge-side only
        self._devices: dict[str, dict] = {}
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Discover Govee fixtures via UDP broadcast."""
        fixtures: List[dict] = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(2.0)

            # Govee discovery command
            sock.sendto(b"Z", ("255.255.255.255", 4321))

            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                    if len(data) >= 16:
                        # Govee response format varies; parse basic info
                        ip_str = ".".join(str(b) for b in data[4:8])
                        name = data[8:16].decode("utf-8", errors="ignore").rstrip("")
                        fixtures.append({
                            "id": f"govee:{ip_str}",
                            "address": f"govee:{ip_str}",
                            "name": name or "Govee light",
                            "model": "govee",
                            "kind": "govee",
                        })
                except socket.timeout:
                    break
            sock.close()
        except Exception as exc:
            logger.warning("[Govee] Discover error: %s", exc)
        return fixtures

    async def pair(self, credentials: dict | None = None) -> str:
        """Govee pairing may provide a local key."""
        key = credentials.get("local_key") if credentials else None
        if key:
            self._key = key
        import secrets
        self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Send a colour/brightness command to a Govee light."""
        dev_id = state.get("device_id")
        if not dev_id or dev_id not in self._devices:
            logger.warning("[Govee] Unknown device %s", dev_id)
            return

        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        transition = max(0, float(state.get("transition", 0)))

        ip_port = self._devices[dev_id]["address"].replace("govee://", "")
        ip_str, port_str = ip_port.split(":")
        port = int(port_str)

        # Govee colour command (XOR-encrypted)
        raw = _govee_xor_decrypt(
            bytes([0x01, r, g, b, brightness, int(transition / 100 * 255)]),
            self._key.encode() if self._key else b""
        )
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(raw, (ip_str, port))
            sock.close()
        except Exception as exc:
            logger.warning("[Govee] set_state error for %s: %s", dev_id, exc)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}


#
# Matter over LAN driver - abstract base; actual implementation depends
# on the device's Matter fabric setup.  For now, a stub that logs intent.
#


class MatterDriver(LightDriver):
    """Matter light driver — abstract base ( Bridge-side fabric required )."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._devices: dict[str, dict] = {}
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Matter discovery is Bridge-fabric-dependent; return empty for now."""
        logger.info("[Matter] Discovery not implemented without Bridge fabric.")
        return []

    async def pair(self, credentials: dict | None = None) -> str:
        """Matter pairing is Bridge-fabric-dependent."""
        import secrets
        self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Matter state update is Bridge-fabric-dependent."""
        dev_id = state.get("device_id")
        if dev_id:
            logger.info("[Matter] Would update state for %s: %s", dev_id, state)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}


#
# Hue Entertainment driver - Hue Entertainment (DTLS streaming).
#
# Hue Entertainment uses the Hue Bridge with the Entertainment API.
# Pairing is done via the link button on the Hue Bridge.
# Entertainment areas map physical zones to audio-driven color streams.

import asyncio
import json
import socket
import ssl

class HueDriver(LightDriver):
    """Hue Entertainment light driver - LAN via TLS/DTLS."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._devices: dict[str, dict] = {}
        self._last_press: float = 0.0
        self._link_button_timeout = 120  # seconds to wait after link button press
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Discover Hue Bridges via mDNS.

        Hue bridges announce themselves on the local network via mDNS
        with the name _hue._tcp.local.
        """
        fixtures: List[dict] = []
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(3.0)

            # mDNS query for Hue Bridges
            sock.sendto(b"\x01\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00\x00\x03\x00\x00\x00\x00\x20\x00\x00\x00", ("224.0.0.251", 5353))
            sock.settimeout(3.0)
            deadline = asyncio.get_event_loop().time() + 3.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                    # Parse mDNS response
                    if b"HUE" in data or b"_hue._tcp" in data:
                        # Extract hostname from response
                        fixtures.append({
                            "id": "hue_entertainment",
                            "address": "hue://bridge.local",
                            "name": "Hue Entertainment Bridge",
                            "model": "hue",
                            "kind": "hue",
                        })
                except socket.timeout:
                    break
            sock.close()
        except Exception as exc:
            logger.warning("[Hue] Discover error: %s", exc)
        return fixtures

    async def pair(self, credentials: dict | None = None) -> str:
        """Pair with Hue Bridge via link button.

        The user must press the link button on the Hue Bridge within
        _link_button_timeout seconds.  Returns a per-origin token.
        """
        import secrets
        # In a full implementation, this would wait for the Bridge to
        # confirm the link button press and return a session ticket.
        # For now, generate a token.
        self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Send state to Hue Entertainment bridge.

        The state should contain: device_id (entertainment area ID),
        r, g, b, brightness, transition.
        """
        dev_id = state.get("device_id")
        if not dev_id or dev_id not in self._devices:
            logger.warning("[Hue] Unknown device %s", dev_id)
            return

        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        transition = max(0, float(state.get("transition", 0)))

        # In a full implementation, this would send DTLS-wrapped commands
        # to the Hue Entertainment API.  For now, log the intent.
        logger.info("[Hue] Would set state %s on entertainment area %s: rgb=(%d,%d,%d) bri=%d%% trans=%.1fs",
                     dev_id, r, g, b, brightness, transition)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "unavailable", "max_rate_hz": 0.0,
                "streaming": False}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}

#
# Home Assistant adapter - Home Assistant LAN integration.
#
# Home Assistant uses a long-lived access token for authentication.
# The token is entered by the user in the Bridge configuration
# and stored Bridge-side only.  Never sent to the browser.
# Only light entities are supported.

import asyncio
import json
import socket

class HomeAssistantDriver(LightDriver):
    """Home Assistant light driver - LAN via REST API."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self._token: str | None = None
        self._devices: dict[str, dict] = {}
        self._base_url: str | None = None
        self._rate_mode = "slow"
        self._max_rate_hz = 1.0
        self._latency_ms: float = 0.0

    async def discover(self) -> List[dict]:
        """Discover light entities from Home Assistant.

        Connects to the Home Assistant API to find light entities.
        Requires a valid access token.
        """
        fixtures: List[dict] = []
        try:
            if not self._token or not self._base_url:
                logger.warning("[HA] No token or base URL configured")
                return fixtures

            # Query Home Assistant for light entities
            url = f"{self._base_url}/api/states.light"
            # In a full implementation, this would use HTTPS with auth
            logger.info("[HA] Would discover lights from %s", url)
            fixtures.append({
                "id": "home_assistant",
                "address": "homeassistant://local",
                "name": "Home Assistant Light",
                "model": "homeassistant",
                "kind": "home_assistant",
            })
        except Exception as exc:
            logger.warning("[HA] Discover error: %s", exc)
        return fixtures

    async def pair(self, credentials: dict | None = None) -> str:
        """Store Home Assistant long-lived access token.

        The token is entered by the user in the Bridge configuration
        and stored Bridge-side only.  Never sent to the browser.
        """
        key = credentials.get("access_token") if credentials else None
        if key:
            self._token = key
        # Also set base_url if provided
        if credentials and "base_url" in credentials:
            self._base_url = credentials["base_url"]
        import secrets
        # Generate a per-origin token for Bridge internal use
        # (only if no HA token was provided)
        if not self._token:
            self._token = secrets.token_urlsafe(24)
        return self._token

    async def set_state(self, state: dict) -> None:
        """Send state to a Home Assistant light entity.

        The state should contain: device_id (entity ID),
        r, g, b, brightness, transition.
        """
        dev_id = state.get("device_id")
        if not dev_id or dev_id not in self._devices:
            logger.warning("[HA] Unknown device %s", dev_id)
            return

        r = min(255, max(0, int(state.get("r", 0))))
        g = min(255, max(0, int(state.get("g", 0))))
        b = min(255, max(0, int(state.get("b", 0))))
        brightness = min(100, max(0, int(state.get("brightness", 100))))
        transition = max(0, float(state.get("transition", 0)))

        # In a full implementation, this would call the HA API:
        # PUT /api/states/{entity_id}
        # with payload: {"state": {"rgb_color": [r/255, g/255, b/255], "brightness": brightness, "transition": transition}}
        logger.info("[HA] Would set state %s on entity %s: rgb=(%d,%d,%d) bri=%d%% trans=%.1fs",
                     dev_id, r, g, b, brightness, transition)

    def capabilities(self) -> dict:
        return {"supports_rgb": True, "supports_brightness": True,
                "supports_transition": True, "max_brightness": 100,
                "max_transition": 300, "rate_mode": "slow", "max_rate_hz": 1.0}

    def status(self) -> dict:
        devices = list(self._devices.values())
        latency = self.latency_diagnostics()
        return {"online": bool(self._devices),
                "latencyMs": latency,
                "devices": [{"id": d["id"], "name": d["name"], "ready": d.get("ready", False), "needs_credentials": d.get("needs_credentials", False)} for d in devices]}

DEFAULT_FLASH_CAP_HZ = 3.0
DEFAULT_SMOOTHING_MS = 50.0
MAX_SMOOTHING_MS = 1000.0
LARGE_TRANSITION_RGB_DISTANCE = 128.0
LARGE_TRANSITION_LUMA_DELTA = 0.5


def normalize_light_state(state: dict) -> dict:
    """Normalize a browser light frame.

    The browser owns the audio -> light mapping.  For luminance-driven
    mappings it may send ``{"luminance": 0.0..1.0}`` instead of explicit RGB;
    the Bridge expands that to a grey RGB value so every driver receives the
    same ``{r,g,b,brightness,transition}`` contract.
    """
    normalized = dict(state)
    if "luminance" in normalized:
        luma = max(0.0, min(1.0, float(normalized["luminance"])))
        normalized["luminance"] = luma
        value = int(round(luma * 255))
        normalized.setdefault("r", value)
        normalized.setdefault("g", value)
        normalized.setdefault("b", value)
        normalized.setdefault("brightness", int(round(luma * 100)))
    for key in ("r", "g", "b"):
        if key in normalized:
            normalized[key] = max(0, min(255, int(normalized[key])))
    if "brightness" in normalized:
        normalized["brightness"] = max(0, min(100, int(normalized["brightness"])))
    if "transition" in normalized:
        normalized["transition"] = max(0.0, float(normalized["transition"]))
    return normalized


def is_large_transition(previous: dict | None, current: dict) -> bool:
    """Whether two light states differ enough to count as a flash."""
    if previous is None:
        return False
    if "luminance" in previous and "luminance" in current:
        return abs(float(current["luminance"]) - float(previous["luminance"])) >= LARGE_TRANSITION_LUMA_DELTA
    previous_rgb = tuple(int(previous.get(channel, 0)) for channel in ("r", "g", "b"))
    current_rgb = tuple(int(current.get(channel, 0)) for channel in ("r", "g", "b"))
    distance = sum((a - b) ** 2 for a, b in zip(previous_rgb, current_rgb)) ** 0.5
    return distance >= LARGE_TRANSITION_RGB_DISTANCE


class LightStreamController:
    """Per-connection latest-wins light output with rate and flash caps.

    The controller coalesces frames per device, dispatches at the driver's
    ``max_rate_hz``, limits large luminance/colour transitions to
    ``flash_cap_hz`` (default ``DEFAULT_FLASH_CAP_HZ``, 3/s), then applies a
    minimum spacing between any two sends (``smoothing_ms``, default
    ``DEFAULT_SMOOTHING_MS``).  The flash cap is a hard ceiling: smoothing can
    delay a send but can never raise the number of large transitions.  A
    suppressed frame is kept as the latest state and delivered when both
    stages allow it.
    """

    def __init__(self, drivers: dict[str, "LightDriver"], clock=time.monotonic, sleep=asyncio.sleep):
        self.drivers = drivers
        self.clock = clock
        self.sleep = sleep
        self.latest: dict[str, dict] = {}
        self.last_sent_at: dict[tuple[str, str], float] = {}
        self.last_sent_state: dict[str, dict] = {}
        self.large_transitions: dict[str, collections.deque[float]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.closed = False

    def _capabilities(self, kind: str) -> dict:
        return self.drivers[kind].capabilities()

    def _rate_hz(self, kind: str) -> float:
        try:
            return float(self._capabilities(kind).get("max_rate_hz", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _flash_cap_hz(self, state: dict) -> float:
        try:
            cap = float(state.get("flash_cap_hz", DEFAULT_FLASH_CAP_HZ))
        except (TypeError, ValueError):
            cap = DEFAULT_FLASH_CAP_HZ
        return max(0.0, cap)

    def _smoothing_ms(self, state: dict) -> float:
        try:
            value = float(state.get("smoothing_ms", DEFAULT_SMOOTHING_MS))
        except (TypeError, ValueError):
            value = DEFAULT_SMOOTHING_MS
        return max(0.0, min(MAX_SMOOTHING_MS, value))

    def _next_delay(self, device_id: str, kind: str, state: dict) -> float:
        now = self.clock()
        rate_hz = self._rate_hz(kind)
        if rate_hz <= 0:
            return 1.0
        last_sent = self.last_sent_at.get((kind, device_id), 0.0)
        delay = max(0.0, (1.0 / rate_hz) - (now - last_sent))
        cap = self._flash_cap_hz(state)
        if cap > 0 and is_large_transition(self.last_sent_state.get(device_id), state):
            times = self.large_transitions.get(device_id)
            if times and len(times) >= cap:
                delay = max(delay, times[0] + 1.0 - now)
        # Smoothing runs after the flash cap: it only adds spacing.
        spacing = self._smoothing_ms(state) / 1000.0
        if spacing > 0:
            delay = max(delay, spacing - (now - last_sent))
        return max(0.0, delay)

    def _flash_allows(self, device_id: str, state: dict, now: float) -> bool:
        cap = self._flash_cap_hz(state)
        if cap <= 0:
            return True
        if not is_large_transition(self.last_sent_state.get(device_id), state):
            return True
        times = self.large_transitions.setdefault(device_id, collections.deque())
        cutoff = now - 1.0
        while times and times[0] <= cutoff:
            times.popleft()
        if len(times) >= cap:
            return False
        times.append(now)
        return True

    async def _send(self, device_id: str, kind: str, state: dict) -> None:
        await self.drivers[kind].set_state(state)
        self.last_sent_at[(kind, device_id)] = self.clock()
        self.last_sent_state[device_id] = state

    async def _try_send(self, device_id: str, kind: str, force: bool = False) -> bool:
        state = self.latest.get(device_id)
        if state is None:
            return False
        if not force:
            rate_hz = self._rate_hz(kind)
            if rate_hz <= 0:
                return False
            now = self.clock()
            last_sent = self.last_sent_at.get((kind, device_id), 0.0)
            if now - last_sent < (1.0 / rate_hz) - 1e-9:
                return False
            spacing = self._smoothing_ms(state) / 1000.0
            if spacing > 0 and now - last_sent < spacing - 1e-9:
                return False
            if not self._flash_allows(device_id, state, now):
                return False
        await self._send(device_id, kind, state)
        return True

    def _schedule(self, device_id: str, kind: str) -> None:
        if self.closed or self._rate_hz(kind) <= 0:
            return
        task = self.tasks.get(device_id)
        if task is not None and not task.done():
            return
        self.tasks[device_id] = asyncio.create_task(self._deliver_latest(device_id, kind))

    async def _deliver_latest(self, device_id: str, kind: str) -> None:
        try:
            while not self.closed:
                state = self.latest.get(device_id)
                if state is None or self.last_sent_state.get(device_id) == state:
                    return
                delay = self._next_delay(device_id, kind, state)
                if delay > 0:
                    await self.sleep(min(delay, 0.05))
                    continue
                if await self._try_send(device_id, kind):
                    return
                await self.sleep(0.005)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A failed device ack must not take down the background delivery
            # task or leave an unretrieved task exception behind.
            logger.warning("[LIGHT] delivery failed for %s/%s", kind, device_id,
                           exc_info=True)
        finally:
            self.tasks.pop(device_id, None)

    async def submit(self, device_id: str, state: dict) -> None:
        kind = state.get("kind", "wiz")
        if kind not in self.drivers:
            raise LightDriverError("unknown_driver")
        normalized = normalize_light_state(state)
        self.latest[device_id] = normalized
        await self._try_send(device_id, kind)
        self._schedule(device_id, kind)

    async def flush(self) -> None:
        """Deliver each device's latest state now, even if rate-limited."""
        for device_id, state in list(self.latest.items()):
            kind = state.get("kind", "wiz")
            if kind not in self.drivers or self._rate_hz(kind) <= 0:
                continue
            if self.last_sent_state.get(device_id) == state:
                continue
            await self._try_send(device_id, kind, force=True)

    async def close(self) -> None:
        self.closed = True
        for task in list(self.tasks.values()):
            task.cancel()
        for task in list(self.tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()


LIGHT_METHODS = ["wiz", "tuya", "lifx", "govee", "matter", "hue", "home_assistant"]


def register_light_drivers(app: FastAPI, bridge, drivers: dict[str, LightDriver] | None = None,
                           tuya_store=None, zone_config: LightZoneConfig | None = None) -> None:
    """Register light-control WS endpoint and diagnostics.

    ``drivers`` is injectable so tests can exercise the real WS path with a
    fake driver; production callers get the built-in driver set.
    ``tuya_store`` is the Bridge-side credential store shared with the Tuya
    onboarding HTTP endpoints.  ``zone_config`` holds user groups/zones/scenes.
    """

    driver_instances: dict[str, LightDriver] = dict(drivers) if drivers is not None else {
        "wiz": WizDriver(bridge),
        "tuya": TuyaDriver(bridge, credential_store=tuya_store),
        "lifx": LifxDriver(bridge),
        "govee": GoveeDriver(bridge),
        "matter": MatterDriver(bridge),
        "hue": HueDriver(bridge),
        "home_assistant": HomeAssistantDriver(bridge),
    }
    zones = zone_config if zone_config is not None else LightZoneConfig()
    app.state.light_drivers = driver_instances
    app.state.tuya_store = tuya_store
    app.state.light_zones = zones

    @app.websocket("/lights/ws")
    async def lights_socket(websocket: WebSocket):
        await websocket.accept()
        # Latest-wins coalescing, driver rate limiting, and flash capping.
        controller = LightStreamController(driver_instances)
        driver_capabilities: dict[str, dict] = {
            did: drv.capabilities() for did, drv in driver_instances.items()
        }
        driver_latencies: dict[str, float] = {
            did: 0.0 for did in driver_instances
        }

        async def submit_zone_outputs(outputs: dict[str, dict]) -> list[str]:
            applied: list[str] = []
            for device_id, state in outputs.items():
                kind = zones.device_kinds.get(device_id, "wiz")
                if kind not in driver_instances:
                    continue
                forwarded = dict(state)
                forwarded["device_id"] = device_id
                forwarded["kind"] = kind
                await controller.submit(device_id, forwarded)
                applied.append(device_id)
            return applied

        try:
            while True:
                text = await websocket.receive_text()
                if len(text.encode("utf-8")) > MAX_CONTROL_BYTES:
                    await _send_json(websocket, {"type": "error", "detail": "message_too_large"})
                    continue
                try:
                    obj = json.loads(text)
                except ValueError:
                    await _send_json(websocket, {"type": "error", "detail": "bad_json"})
                    continue
                if not isinstance(obj, dict):
                    continue
                mtype = obj.get("type")

                if mtype == "ping":
                    await _send_json(websocket, {"type": "pong"})
                    continue

                if mtype == "set_state":
                    device_id = obj.get("device_id")
                    state = obj.get("state", {})
                    if not device_id or not state:
                        await _send_json(websocket, {"type": "error", "detail": "missing_fields"})
                        continue
                    kind = state.get("kind", "wiz")
                    if kind not in driver_instances:
                        await _send_json(websocket, {"type": "error", "detail": "unknown_driver"})
                        continue
                    try:
                        await controller.submit(device_id, state)
                    except LightDriverError as exc:
                        await _send_json(websocket, {"type": "error", "detail": str(exc)})
                        continue
                    except Exception:
                        logger.exception("[LIGHT] set_state failed")
                        await _send_json(websocket, {"type": "error", "detail": "driver_error"})
                        continue
                    await _send_json(websocket, {"type": "ack", "device_id": device_id,
                                                  "is_group": bool(obj.get("is_group", False))})

                elif mtype == "flush":
                    await controller.flush()
                    await _send_json(websocket, {"type": "flushed"})

                elif mtype == "discover":
                    # Run discovery for the requested kind(s)
                    kinds = obj.get("kinds", ["wiz", "tuya"])
                    results: dict[str, List[dict]] = {}
                    for kind in kinds:
                        if kind in driver_instances:
                            try:
                                res = await driver_instances[kind].discover()
                                results[kind] = res
                            except Exception as exc:
                                logger.warning("[LIGHT] Discover error for %s: %s", kind, exc)
                                results[kind] = []
                    await _send_json(websocket, {"type": "discovery_results", "results": results})

                elif mtype == "capabilities":
                    kind = obj.get("kind", "wiz")
                    driver = driver_instances.get(kind)
                    if driver:
                        await _send_json(websocket, {"type": "capabilities", "caps": driver.capabilities()})
                    else:
                        await _send_json(websocket, {"type": "error", "detail": "unknown_driver"})

                elif mtype == "status":
                    kind = obj.get("kind", "wiz")
                    driver = driver_instances.get(kind)
                    if driver:
                        info = driver.status()
                        info["latencyMs"] = driver.latency_diagnostics()
                        await _send_json(websocket, {"type": "status", "info": info})
                    else:
                        await _send_json(websocket, {"type": "error", "detail": "unknown_driver"})

                elif mtype == "zones":
                    action = obj.get("action", "get")
                    try:
                        if action == "get":
                            pass
                        elif action == "configure":
                            zones.replace(obj.get("config"))
                        elif action == "preset":
                            zones.apply_preset(obj.get("preset_id"))
                        elif action == "frame":
                            outputs = zones.frame_outputs(
                                obj.get("levels") or {}, obj.get("colors") or {},
                            )
                            applied = await submit_zone_outputs(outputs)
                            await _send_json(websocket, {
                                "type": "zones", "action": "frame",
                                "applied": applied, "config": zones.to_dict(),
                            })
                            continue
                        elif action == "scene":
                            outputs = zones.scene_outputs(obj.get("scene_id"))
                            applied = await submit_zone_outputs(outputs)
                            await _send_json(websocket, {
                                "type": "zones", "action": "scene",
                                "applied": applied, "config": zones.to_dict(),
                            })
                            continue
                        elif action == "device":
                            zones.set_device(
                                obj.get("device_id"),
                                brightness_cap=obj.get("brightness_cap"),
                                on=obj.get("on"),
                                kind=obj.get("kind"),
                            )
                        elif action == "slider":
                            zones.set_zone_sliders(
                                obj.get("zone_id"),
                                smoothing_ms=obj.get("smoothing_ms"),
                                reactivity=obj.get("reactivity"),
                                brightness_cap=obj.get("brightness_cap"),
                                on=obj.get("on"),
                            )
                        elif action == "global":
                            zones.set_global_sliders(
                                smoothing_ms=obj.get("smoothing_ms"),
                                reactivity=obj.get("reactivity"),
                            )
                        else:
                            await _send_json(websocket, {"type": "error",
                                                         "detail": "unknown_zone_action"})
                            continue
                    except LightZoneError as exc:
                        await _send_json(websocket, {"type": "error", "detail": str(exc)})
                        continue
                    except LightDriverError as exc:
                        await _send_json(websocket, {"type": "error", "detail": str(exc)})
                        continue
                    await _send_json(websocket, {
                        "type": "zones", "action": action, "config": zones.to_dict(),
                    })

                elif mtype == "diagnostics":
                    report = {}
                    for kind, driver in driver_instances.items():
                        try:
                            status = driver.status()
                        except Exception as exc:
                            status = {"online": False, "error": type(exc).__name__}
                        report[kind] = {
                            "capabilities": driver.capabilities(),
                            "status": status,
                            "latencyMs": driver.latency_diagnostics(),
                        }
                    await _send_json(websocket, {"type": "diagnostics", "drivers": report})

                elif mtype == "latency":
                    kind = obj.get("kind")
                    if kind and kind in driver_instances:
                        # Report measured latency from the driver
                        driver_latencies[kind] = obj.get("latencyMs", 0.0)
                        driver_instances[kind].record_latency(obj.get("latencyMs", 0.0))
                        await _send_json(websocket, {"type": "latency_ack", "kind": kind})
                    else:
                        await _send_json(websocket, {"type": "error", "detail": "unknown_driver"})

                else:
                    await _send_json(websocket, {"type": "error", "detail": "unsupported_type"})

        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("[LIGHT] WS socket error")
        finally:
            await controller.close()
            logger.info("[LIGHT] Client disconnected")
