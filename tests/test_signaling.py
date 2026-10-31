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
