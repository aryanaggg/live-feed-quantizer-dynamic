"""Tier 0 WebRTC signalling.

The signalling service only exchanges SDP/ICE envelopes.  Media is peer to
peer (or relayed by TURN when configured); nothing here sees or logs media.

Rooms are deliberately small and short lived:

* the room id is an unguessable 24-byte token, the short code is only a
  convenience for humans and is rate limited,
* each join returns a one-time peer token; reconnecting with the same token is
  allowed for a short grace period after a drop,
* the room expires after ``settings.signaling_room_ttl`` seconds,
* every message is size/type/rate limited.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from config import settings
from security import RateLimiter, normalize_origin, origin_allowed

logger = logging.getLogger("live_feed_quantizer.signaling")

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 6
TOKEN_BYTES = 24
MAX_MESSAGE_BYTES = 64 * 1024
ALLOWED_RELAY_TYPES = {"offer", "answer", "ice", "bye"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_matches(token: str | None, expected_hash: str | None) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(_hash(token), expected_hash)


def ice_servers() -> list[dict[str, Any]]:
    """STUN always; TURN credentials are short lived when a secret is set."""
    servers: list[dict[str, Any]] = []
    if settings.stun_urls:
        servers.append({"urls": list(settings.stun_urls)})

    if settings.turn_urls:
        username = settings.turn_username
        credential = settings.turn_password
        if settings.turn_secret:
            expiry = int(time.time()) + max(60, settings.turn_ttl)
            username = f"{expiry}:{secrets.token_urlsafe(12)}"
            digest = hmac.new(
                settings.turn_secret.encode("utf-8"),
                username.encode("utf-8"),
                hashlib.sha1,
            ).digest()
            credential = base64.b64encode(digest).decode("ascii")
        servers.append(
            {
                "urls": list(settings.turn_urls),
                "username": username,
                "credential": credential,
            }
        )
    return servers


@dataclass
class Peer:
    id: str
    token_hash: str
    connected: bool = False
    ws: Any = None
    joined_at: float = field(default_factory=time.time)
    disconnected_at: float = 0.0


@dataclass
class Room:
    id: str
    code: str
    host_token_hash: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    host_ws: Any = None
    peers: dict[str, Peer] = field(default_factory=dict)

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at


class RoomError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RoomRegistry:
    """In-memory room registry (single process; documented in README)."""

    def __init__(self, ttl: int | None = None, grace: int | None = None,
                 max_guests: int | None = None, max_rooms_per_ip: int | None = None,
                 clock=time.time) -> None:
        self.ttl = settings.signaling_room_ttl if ttl is None else ttl
        self.grace = settings.signaling_room_grace if grace is None else grace
        self.max_guests = settings.signaling_max_guests if max_guests is None else max_guests
        self.max_rooms_per_ip = settings.signaling_max_rooms_per_ip if max_rooms_per_ip is None else max_rooms_per_ip
        self.clock = clock
        self._rooms: dict[str, Room] = {}
        self._by_code: dict[str, str] = {}
        self._rooms_by_ip: dict[str, list[str]] = {}

    # -- helpers ---------------------------------------------------------
    def prune(self) -> None:
        now = self.clock()
        for room_id, room in list(self._rooms.items()):
            if room.expired(now):
                self._drop(room_id)

    def _drop(self, room_id: str) -> None:
        room = self._rooms.pop(room_id, None)
        if not room:
            return
        self._by_code.pop(room.code, None)
        for ip, ids in list(self._rooms_by_ip.items()):
            ids = [r for r in ids if r != room_id]
            if ids:
                self._rooms_by_ip[ip] = ids
            else:
                self._rooms_by_ip.pop(ip, None)

    def _new_code(self) -> str:
        for _ in range(50):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._by_code:
                return code
        raise RoomError("code_space_exhausted")

    def _ip_count(self, ip: str) -> int:
        return len(self._rooms_by_ip.get(ip, []))

    # -- public API ------------------------------------------------------
    def create(self, ip: str) -> tuple[Room, str]:
        self.prune()
        if self._ip_count(ip) >= self.max_rooms_per_ip:
            raise RoomError("room_limit")
        room_id = secrets.token_urlsafe(TOKEN_BYTES)
        host_token = secrets.token_urlsafe(TOKEN_BYTES)
        room = Room(
            id=room_id,
            code=self._new_code(),
            host_token_hash=_hash(host_token),
            expires_at=self.clock() + self.ttl,
        )
        self._rooms[room.id] = room
        self._by_code[room.code] = room.id
        self._rooms_by_ip.setdefault(ip, []).append(room.id)
        return room, host_token

    def get(self, room_id: str | None) -> Room | None:
        self.prune()
        return self._rooms.get(room_id or "")

    def get_by_code(self, code: str | None) -> Room | None:
        self.prune()
        if not code:
            return None
        return self._rooms.get(self._by_code.get(code.upper().strip(), ""))

    def join(self, code: str, ip: str) -> tuple[Room, Peer, str]:
        self.prune()
        room = self.get_by_code(code)
        if room is None:
            raise RoomError("room_not_found")
        if len(room.peers) >= self.max_guests:
            raise RoomError("room_full")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        peer = Peer(id=secrets.token_urlsafe(9), token_hash=_hash(token))
        room.peers[peer.id] = peer
        return room, peer, token

    def authenticate_host(self, room_id: str, token: str | None) -> Room:
        room = self.get(room_id)
        if room is None:
            raise RoomError("room_not_found")
        if not _token_matches(token, room.host_token_hash):
            raise RoomError("bad_token")
        return room

    def authenticate_peer(self, room_id: str, token: str | None) -> tuple[Room, Peer]:
        room = self.get(room_id)
        if room is None:
            raise RoomError("room_not_found")
        for peer in room.peers.values():
            if _token_matches(token, peer.token_hash):
                if peer.connected:
                    raise RoomError("peer_already_connected")
                if peer.disconnected_at and self.clock() - peer.disconnected_at > self.grace:
                    raise RoomError("reconnect_window_expired")
                return room, peer
        raise RoomError("bad_token")

    def mark_connected(self, room: Room, peer: Peer, ws) -> None:
        peer.connected = True
        peer.ws = ws

    def mark_disconnected(self, room: Room, peer: Peer) -> None:
        peer.connected = False
        peer.ws = None
        peer.disconnected_at = self.clock()

    def remove_peer(self, room: Room, peer: Peer) -> None:
        room.peers.pop(peer.id, None)

    def room_info(self, room: Room) -> dict[str, Any]:
        return {
            "id": room.id,
            "code": room.code,
            "expiresAt": int(room.expires_at * 1000),
            "guests": len(room.peers),
            "hostConnected": room.host_ws is not None,
        }


registry = RoomRegistry()
room_limiter = RateLimiter(limit=8, window=60)       # room creations per IP/min
join_limiter = RateLimiter(limit=30, window=60)      # join attempts per IP/min
signal_limiter = RateLimiter(limit=240, window=60)   # WS relay messages per IP/min

router = APIRouter(prefix="/signal", tags=["signaling"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _origin_ok(headers) -> bool:
    return origin_allowed(headers.get("origin"), settings.allowed_origins)


@router.get("/ice")
async def ice_config(request: Request):
    if not _origin_ok(request.headers):
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
    return {"iceServers": ice_servers()}


@router.post("/rooms")
async def create_room(request: Request):
    if not _origin_ok(request.headers):
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
    ip = _client_ip(request)
    if not room_limiter.allow(ip):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    try:
        room, host_token = registry.create(ip)
    except RoomError as exc:
        return JSONResponse({"error": exc.reason}, status_code=429)
    info = registry.room_info(room)
    return {
        "room": info,
        "hostToken": host_token,
        "joinPath": f"/?join={room.code}",
        "iceServers": ice_servers(),
    }


@router.post("/rooms/{code}/join")
async def join_room(code: str, request: Request):
    if not _origin_ok(request.headers):
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
    ip = _client_ip(request)
    if not join_limiter.allow(ip):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    try:
        room, peer, peer_token = registry.join(code, ip)
    except RoomError as exc:
        status = 429 if exc.reason == "room_full" else 404
        return JSONResponse({"error": exc.reason}, status_code=status)
    return {
        "room": registry.room_info(room),
        "peerId": peer.id,
        "peerToken": peer_token,
        "iceServers": ice_servers(),
    }


@router.get("/rooms/{code}")
async def room_status(code: str, request: Request):
    if not _origin_ok(request.headers):
        return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
    room = registry.get_by_code(code)
    if room is None:
        return JSONResponse({"error": "room_not_found"}, status_code=404)
    return {"room": registry.room_info(room)}


async def _send(ws, payload: dict) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


@router.websocket("/ws")
async def signal_socket(websocket: WebSocket):
    await websocket.accept()
    origin = websocket.headers.get("origin")
    if not origin_allowed(origin, settings.allowed_origins):
        await _send(websocket, {"type": "error", "detail": "origin_not_allowed"})
        await websocket.close(code=1008)
        return

    params = websocket.query_params
    room_id = params.get("room", "")
    role = params.get("role", "")
    token = params.get("token", "")

    if role not in {"host", "guest"}:
        await _send(websocket, {"type": "error", "detail": "bad_role"})
        await websocket.close(code=1008)
        return

    try:
        if role == "host":
            room = registry.authenticate_host(room_id, token)
            if room.host_ws is not None and room.host_ws is not websocket:
                # Host reconnect replaces the stale socket.
                try:
                    await room.host_ws.close(code=4001)
                except Exception:
                    pass
            room.host_ws = websocket
            await _send(websocket, {"type": "ready", "role": "host", "room": registry.room_info(room)})
        else:
            room, peer = registry.authenticate_peer(room_id, token)
            registry.mark_connected(room, peer, websocket)
            await _send(websocket, {"type": "ready", "role": "guest", "peerId": peer.id, "room": registry.room_info(room)})
            if room.host_ws is not None:
                await _send(room.host_ws, {"type": "peer_joined", "peerId": peer.id})
    except RoomError as exc:
        await _send(websocket, {"type": "error", "detail": exc.reason})
        await websocket.close(code=1008)
        return

    client = websocket.client.host if websocket.client else "unknown"
    peer = None
    if role == "guest":
        # authenticate_peer already located it; re-find for cleanup.
        peer = next((p for p in room.peers.values() if p.ws is websocket), None)

    try:
        while True:
            message = await websocket.receive_text()
            if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await _send(websocket, {"type": "error", "detail": "message_too_large"})
                continue
            if not signal_limiter.allow(client):
                await _send(websocket, {"type": "error", "detail": "rate_limited"})
                break
            try:
                obj = json.loads(message)
            except ValueError:
                await _send(websocket, {"type": "error", "detail": "bad_json"})
                continue
            if not isinstance(obj, dict):
                continue
            mtype = obj.get("type")
            if mtype not in ALLOWED_RELAY_TYPES:
                await _send(websocket, {"type": "error", "detail": "unsupported_type"})
                continue

            if role == "host":
                target = obj.get("to")
                out = {"type": mtype, "from": "host", "payload": obj.get("payload")}
                if target:
                    target_peer = room.peers.get(str(target))
                    if target_peer and target_peer.ws is not None:
                        await _send(target_peer.ws, out)
                else:
                    for p in list(room.peers.values()):
                        if p.ws is not None:
                            await _send(p.ws, out)
            else:
                if peer is None:
                    continue
                out = {"type": mtype, "from": peer.id, "payload": obj.get("payload")}
                if room.host_ws is not None:
                    await _send(room.host_ws, out)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("[SIGNAL] Unexpected socket error")
    finally:
        if role == "host":
            if room.host_ws is websocket:
                room.host_ws = None
        elif peer is not None:
            registry.mark_disconnected(room, peer)
            if room.host_ws is not None:
                await _send(room.host_ws, {"type": "peer_left", "peerId": peer.id})
        logger.info("[SIGNAL] %s disconnected (room %s)", role, room.id[:8] if room else "?")
