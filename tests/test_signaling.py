import json
import time

import pytest
from fastapi.testclient import TestClient

import signaling
from config import settings
from server import app

client = TestClient(app)

ALLOWED_ORIGIN = settings.allowed_origins[0]


@pytest.fixture(autouse=True)
def clean_registry():
    signaling.registry._rooms.clear()
    signaling.registry._by_code.clear()
    signaling.registry._rooms_by_ip.clear()
    for limiter in (signaling.room_limiter, signaling.join_limiter, signaling.signal_limiter):
        limiter._events.clear()
    yield


def _create(origin=ALLOWED_ORIGIN):
    resp = client.post("/signal/rooms", headers={"Origin": origin})
    return resp


def _ws(room_id, role, token, origin=ALLOWED_ORIGIN):
    headers = {"Origin": origin} if origin else {}
    return client.websocket_connect(
        f"/signal/ws?room={room_id}&role={role}&token={token}", headers=headers
    )


def test_create_join_and_status():
    resp = _create()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    code = data["room"]["code"]
    assert len(code) == 6
    assert data["hostToken"]
    assert data["joinPath"] == f"/?join={code}"

    joined = client.post(f"/signal/rooms/{code}/join", headers={"Origin": ALLOWED_ORIGIN})
    assert joined.status_code == 200, joined.text
    payload = joined.json()
    assert payload["peerToken"]
    assert payload["room"]["guests"] == 1

    status = client.get(f"/signal/rooms/{code}", headers={"Origin": ALLOWED_ORIGIN})
    assert status.status_code == 200
    assert status.json()["room"]["guests"] == 1


def test_join_rejects_unknown_code():
    resp = client.post("/signal/rooms/ZZZZZZ/join", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 404
    assert resp.json()["error"] == "room_not_found"


def test_room_creation_rejects_unknown_origin():
    resp = _create(origin="https://evil.example")
    assert resp.status_code == 403
    assert resp.json()["error"] == "origin_not_allowed"


def test_ice_config_contains_stun_and_ephemeral_turn(monkeypatch):
    monkeypatch.setattr(settings, "turn_urls", ["turn:turn.example.com:3478"])
    monkeypatch.setattr(settings, "turn_secret", "super-secret")
    resp = client.get("/signal/ice", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    servers = resp.json()["iceServers"]
    assert any("stun:" in url for server in servers for url in server["urls"])
    turn = next(s for s in servers if any("turn:" in u for u in s["urls"]))
    assert turn["username"] and turn["credential"]
    assert "super-secret" not in json.dumps(turn)


def test_registry_expires_rooms():
    now = [1000.0]
    reg = signaling.RoomRegistry(ttl=10, clock=lambda: now[0])
    room, _ = reg.create("1.2.3.4")
    assert reg.get(room.id) is not None
    now[0] += 11
    assert reg.get(room.id) is None
    assert reg.get_by_code(room.code) is None


def test_registry_rejects_room_when_full():
    reg = signaling.RoomRegistry(ttl=60, max_guests=1)
    room, _ = reg.create("1.2.3.4")
    reg.join(room.code, "1.2.3.5")
    with pytest.raises(signaling.RoomError) as exc:
        reg.join(room.code, "1.2.3.6")
    assert exc.value.reason == "room_full"


def test_peer_token_is_one_time_but_reconnect_grace_works():
    now = [1000.0]
    reg = signaling.RoomRegistry(ttl=60, grace=10, clock=lambda: now[0])
    room, _ = reg.create("1.2.3.4")
    _, peer, token = reg.join(room.code, "1.2.3.5")

    reg.mark_connected(room, peer, object())
    with pytest.raises(signaling.RoomError) as exc:
        reg.authenticate_peer(room.id, token)
    assert exc.value.reason == "peer_already_connected"

    reg.mark_disconnected(room, peer)
    assert reg.authenticate_peer(room.id, token)[1].id == peer.id

    now[0] += 11
    with pytest.raises(signaling.RoomError) as exc:
        reg.authenticate_peer(room.id, token)
    assert exc.value.reason == "reconnect_window_expired"


def test_host_and_guest_relay_and_reconnect():
    room, host_token = signaling.registry.create("testclient")
    joined = signaling.registry.join(room.code, "testclient")
    peer_id = joined[1].id
    peer_token = joined[2]

    host = _ws(room.id, "host", host_token)
    host.__enter__()
    try:
        ready = json.loads(host.receive_text())
        assert ready["type"] == "ready" and ready["role"] == "host"

        guest = _ws(room.id, "guest", peer_token)
        guest.__enter__()
        try:
            guest_ready = json.loads(guest.receive_text())
            assert guest_ready["type"] == "ready"
            joined_msg = json.loads(host.receive_text())
            assert joined_msg == {"type": "peer_joined", "peerId": peer_id}

            host.send_text(json.dumps({"type": "offer", "to": peer_id, "payload": {"sdp": "x"}}))
            offer = json.loads(guest.receive_text())
            assert offer["type"] == "offer" and offer["from"] == "host"

            guest.send_text(json.dumps({"type": "answer", "payload": {"sdp": "y"}}))
            answer = json.loads(host.receive_text())
            assert answer["type"] == "answer" and answer["from"] == peer_id
        finally:
            guest.__exit__(None, None, None)

        left = json.loads(host.receive_text())
        assert left == {"type": "peer_left", "peerId": peer_id}

        # A dropped guest may reconnect with the same one-time peer token
        # inside the grace window.
        guest2 = _ws(room.id, "guest", peer_token)
        guest2.__enter__()
        try:
            again = json.loads(guest2.receive_text())
            assert again["type"] == "ready"
            joined_again = json.loads(host.receive_text())
            assert joined_again["type"] == "peer_joined"
        finally:
            guest2.__exit__(None, None, None)
    finally:
        host.__exit__(None, None, None)


def test_ws_rejects_unknown_origin_and_bad_token():
    room, host_token = signaling.registry.create("testclient")
    ws = _ws(room.id, "host", host_token, origin="https://evil.example")
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["detail"] == "origin_not_allowed"
    finally:
        ws.__exit__(None, None, None)

    ws = _ws(room.id, "host", "not-the-token")
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["detail"] == "bad_token"
    finally:
        ws.__exit__(None, None, None)


def test_ws_rejects_unsupported_message_types():
    room, host_token = signaling.registry.create("testclient")
    ws = _ws(room.id, "host", host_token)
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "chat", "payload": "hello"}))
        assert json.loads(ws.receive_text())["detail"] == "unsupported_type"
    finally:
        ws.__exit__(None, None, None)


def test_room_and_token_ids_are_unguessable_and_unique():
    rooms = [signaling.RoomRegistry(ttl=60).create(f"10.0.0.{index}") for index in range(20)]
    room_ids = {room.id for room, _ in rooms}
    host_tokens = {token for _, token in rooms}

    assert len(room_ids) == 20
    assert len(host_tokens) == 20
    for room, token in rooms:
        # 24 random bytes -> 32 url-safe characters, far above the 6-char code.
        assert len(room.id) >= 32
        assert len(token) >= 32
        assert room.id != room.code
        assert len(room.code) == signaling.CODE_LENGTH
        assert set(room.code) <= set(signaling.CODE_ALPHABET)


def test_registry_enforces_max_rooms_per_ip():
    reg = signaling.RoomRegistry(ttl=60, max_rooms_per_ip=1)
    reg.create("1.2.3.4")
    with pytest.raises(signaling.RoomError) as exc:
        reg.create("1.2.3.4")
    assert exc.value.reason == "room_limit"

    # A different IP (and the same IP after expiry) may still create a room.
    reg.create("1.2.3.5")
    now = [1000.0]
    timed = signaling.RoomRegistry(ttl=5, max_rooms_per_ip=1, clock=lambda: now[0])
    timed.create("9.9.9.9")
    now[0] += 6
    timed.create("9.9.9.9")


def test_http_room_limit_returns_429(monkeypatch):
    monkeypatch.setattr(
        signaling, "registry", signaling.RoomRegistry(ttl=60, max_rooms_per_ip=1)
    )
    assert _create().status_code == 200
    limited = _create()
    assert limited.status_code == 429
    assert limited.json()["error"] == "room_limit"


def test_room_ttl_is_enforced_through_http(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(
        signaling,
        "registry",
        signaling.RoomRegistry(ttl=5, clock=lambda: now[0]),
    )
    data = _create().json()
    code = data["room"]["code"]

    assert client.get(f"/signal/rooms/{code}", headers={"Origin": ALLOWED_ORIGIN}).status_code == 200
    now[0] += 6
    expired = client.get(f"/signal/rooms/{code}", headers={"Origin": ALLOWED_ORIGIN})
    assert expired.status_code == 404
    assert expired.json()["error"] == "room_not_found"
    assert client.post(
        f"/signal/rooms/{code}/join", headers={"Origin": ALLOWED_ORIGIN}
    ).status_code == 404


def test_http_room_full_returns_429(monkeypatch):
    monkeypatch.setattr(
        signaling, "registry", signaling.RoomRegistry(ttl=60, max_guests=1)
    )
    code = _create().json()["room"]["code"]
    assert client.post(
        f"/signal/rooms/{code}/join", headers={"Origin": ALLOWED_ORIGIN}
    ).status_code == 200
    full = client.post(f"/signal/rooms/{code}/join", headers={"Origin": ALLOWED_ORIGIN})
    assert full.status_code == 429
    assert full.json()["error"] == "room_full"


def test_room_creation_rate_limit(monkeypatch):
    monkeypatch.setattr(
        signaling, "room_limiter", signaling.RateLimiter(limit=2, window=60)
    )
    assert _create().status_code == 200
    assert _create().status_code == 200
    limited = _create()
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"


def test_join_attempts_are_rate_limited(monkeypatch):
    monkeypatch.setattr(
        signaling, "join_limiter", signaling.RateLimiter(limit=2, window=60)
    )
    for _ in range(2):
        assert client.post(
            "/signal/rooms/ZZZZZZ/join", headers={"Origin": ALLOWED_ORIGIN}
        ).status_code == 404
    limited = client.post(
        "/signal/rooms/ZZZZZZ/join", headers={"Origin": ALLOWED_ORIGIN}
    )
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"


def test_signaling_messages_are_rate_limited(monkeypatch):
    monkeypatch.setattr(
        signaling, "signal_limiter", signaling.RateLimiter(limit=1, window=60)
    )
    room, host_token = signaling.registry.create("testclient")
    ws = _ws(room.id, "host", host_token)
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "offer", "payload": {"sdp": "one"}}))
        ws.send_text(json.dumps({"type": "offer", "payload": {"sdp": "two"}}))
        assert json.loads(ws.receive_text())["detail"] == "rate_limited"
    finally:
        ws.__exit__(None, None, None)


def test_static_turn_credentials_are_returned_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "turn_urls", ["turn:turn.example.com:3478"])
    monkeypatch.setattr(settings, "turn_username", "bridge-user")
    monkeypatch.setattr(settings, "turn_password", "bridge-pass")
    monkeypatch.setattr(settings, "turn_secret", "")

    servers = signaling.ice_servers()
    turn = next(server for server in servers if "turn:" in server["urls"][0])
    assert turn["username"] == "bridge-user"
    assert turn["credential"] == "bridge-pass"


def test_stun_and_turn_are_configured_from_environment(monkeypatch):
    import config

    monkeypatch.setenv("LFQ_STUN_URLS", "stun:a.example:3478, stun:b.example:3478")
    monkeypatch.setenv("LFQ_TURN_URLS", "turn:turn.example:3478?transport=udp")
    monkeypatch.setenv("LFQ_TURN_USERNAME", "env-user")
    monkeypatch.setenv("LFQ_TURN_PASSWORD", "env-pass")
    monkeypatch.setenv("LFQ_TURN_SECRET", "env-secret")
    monkeypatch.setenv("LFQ_TURN_TTL", "120")
    monkeypatch.setenv("LFQ_ROOM_TTL", "30")
    env_settings = config.Settings()

    assert env_settings.stun_urls == ["stun:a.example:3478", "stun:b.example:3478"]
    assert env_settings.turn_urls == ["turn:turn.example:3478?transport=udp"]
    assert env_settings.turn_username == "env-user"
    assert env_settings.turn_password == "env-pass"
    assert env_settings.turn_secret == "env-secret"
    assert env_settings.turn_ttl == 120
    assert env_settings.signaling_room_ttl == 30


def test_ice_servers_without_turn_only_has_stun():
    servers = signaling.ice_servers()
    assert servers
    assert all("turn:" not in url for server in servers for url in server["urls"])


def test_ice_and_join_reject_unknown_origin():
    room, _ = signaling.registry.create("testclient")
    evil = {"Origin": "https://evil.example"}
    assert client.get("/signal/ice", headers=evil).status_code == 403
    assert client.get(f"/signal/rooms/{room.code}", headers=evil).status_code == 403
    assert client.post(f"/signal/rooms/{room.code}/join", headers=evil).status_code == 403


def test_join_accepts_lowercase_code():
    code = _create().json()["room"]["code"]
    joined = client.post(
        f"/signal/rooms/{code.lower()}/join", headers={"Origin": ALLOWED_ORIGIN}
    )
    assert joined.status_code == 200


def test_ws_rejects_bad_role_and_bad_json():
    room, host_token = signaling.registry.create("testclient")

    bad_role = client.websocket_connect(
        f"/signal/ws?room={room.id}&role=spectator&token={host_token}",
        headers={"Origin": ALLOWED_ORIGIN},
    )
    bad_role.__enter__()
    try:
        assert json.loads(bad_role.receive_text())["detail"] == "bad_role"
    finally:
        bad_role.__exit__(None, None, None)

    ws = _ws(room.id, "host", host_token)
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text("{not json")
        assert json.loads(ws.receive_text())["detail"] == "bad_json"
    finally:
        ws.__exit__(None, None, None)


def test_ws_rejects_oversized_message():
    room, host_token = signaling.registry.create("testclient")
    ws = _ws(room.id, "host", host_token)
    ws.__enter__()
    try:
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "offer", "payload": "x" * signaling.MAX_MESSAGE_BYTES}))
        assert json.loads(ws.receive_text())["detail"] == "message_too_large"
    finally:
        ws.__exit__(None, None, None)


def test_host_broadcast_without_target_reaches_every_guest():
    room, host_token = signaling.registry.create("testclient")
    _, peer_a, token_a = signaling.registry.join(room.code, "testclient")
    _, peer_b, token_b = signaling.registry.join(room.code, "testclient")

    host = _ws(room.id, "host", host_token)
    host.__enter__()
    guests = []
    try:
        assert json.loads(host.receive_text())["type"] == "ready"
        for token in (token_a, token_b):
            guest = _ws(room.id, "guest", token)
            guest.__enter__()
            guests.append(guest)
            assert json.loads(guest.receive_text())["type"] == "ready"
            assert json.loads(host.receive_text())["type"] == "peer_joined"

        host.send_text(json.dumps({"type": "offer", "payload": {"sdp": "all"}}))
        for guest in guests:
            offer = json.loads(guest.receive_text())
            assert offer == {"type": "offer", "from": "host", "payload": {"sdp": "all"}}
    finally:
        for guest in guests:
            guest.__exit__(None, None, None)
        host.__exit__(None, None, None)
    assert peer_a.id != peer_b.id
