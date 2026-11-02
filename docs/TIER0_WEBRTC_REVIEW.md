# Tier 0 WebRTC review (`feature/tier0-webrtc`)

Review of `d476e25` (`phase 3: tier-0 WebRTC signaling, rooms/QR, remote
sources, mixer UI`) against the Tier 0 plan. This branch is **open for review
only**; it is not merged into `main`.

## Plan compliance

| Requirement | Implementation | Evidence |
|---|---|---|
| Rooms expire | `RoomRegistry` prunes on every access and each open socket is closed at `Room.expires_at` (`LFQ_ROOM_TTL`, default 600 s). | `test_registry_expires_rooms`, `test_room_ttl_is_enforced_through_http`, `test_room_expiry_closes_host_and_guest_sockets` |
| Unguessable room IDs | `secrets.token_urlsafe(24)` room/host/peer tokens; the 6-char code is only a human convenience. | `test_room_and_token_ids_are_unguessable_and_unique` |
| One-time join | Each join returns a fresh peer token; only its SHA-256 hash is stored. Reconnect with the same token is allowed inside `LFQ_ROOM_GRACE`. | `test_peer_token_is_one_time_but_reconnect_grace_works` |
| Slot only after a valid offer | A join creates a pending peer; the guest slot is consumed by `activate_peer` when its first `offer` arrives. Pending peers are released after `LFQ_ROOM_JOIN_TIMEOUT` (default 30 s). | `test_pending_guest_does_not_consume_a_slot_until_offer`, `test_pending_guest_times_out_and_frees_capacity`, `test_guest_without_offer_times_out_and_frees_slot`, `test_second_active_offer_is_rejected_when_room_full` |
| Room / IP caps | `LFQ_ROOM_MAX_PER_IP` per client IP; `LFQ_ROOM_MAX_GUESTS` active guests; pending joins have a separate cap. | `test_registry_enforces_max_rooms_per_ip`, `test_http_room_limit_returns_429`, `test_http_room_full_returns_429` |
| Rate limits | Room creation 8/min, joins 30/min, WS relay 240/min per IP. | `test_room_creation_rate_limit`, `test_join_attempts_are_rate_limited`, `test_signaling_messages_are_rate_limited` |
| Message limits | 64 KiB max, JSON object, allowlisted `offer`/`answer`/`ice`/`bye`. | `test_ws_rejects_oversized_message`, `test_ws_rejects_bad_role_and_bad_json`, `test_ws_rejects_unsupported_message_types` |
| STUN/TURN from env | `LFQ_STUN_URLS`, `LFQ_TURN_URLS`, `LFQ_TURN_USERNAME`, `LFQ_TURN_PASSWORD`; `LFQ_TURN_SECRET` enables coturn-style ephemeral credentials. | `test_stun_and_turn_are_configured_from_environment`, `test_static_turn_credentials_are_returned_when_configured`, `test_ice_config_contains_stun_and_ephemeral_turn` |
| Remote tracks in the same pipeline | Host `pc.ontrack` stores one `MediaStream`; `RemoteDeviceSource` feeds the existing `FrameSource`, `RemoteAudioSource` feeds the existing `AudioSource`/FFT analyser. Guest adds every camera and microphone track with `pc.addTrack`. | `test_remote_tracks_use_the_same_video_and_audio_pipeline` |
| No Bridge dependency | Signaling uses `resolveBackend()`/`wsURL()` (hosted server); the remote section never references `bridgeURL`, `BRIDGE_PORTS`, or `127.0.0.1`. | `test_remote_signaling_has_no_bridge_dependency` |
| Origin allowlist | HTTP and WS signaling paths reject origins outside `LFQ_ALLOWED_ORIGINS`. | `test_room_creation_rejects_unknown_origin`, `test_ice_and_join_reject_unknown_origin`, `test_ws_rejects_unknown_origin_and_bad_token` |

## Tests added for review

`tests/test_signaling.py` gained coverage for room/token entropy, per-IP and
per-room caps, all three rate limiters, HTTP room expiry, HTTP `room_full`,
static and ephemeral TURN, env-driven ICE config, origin checks on `ice`/`join`/
`status`, lowercase join codes, bad role/JSON/oversized messages, and host
broadcast to every guest.

`tests/test_frontend_static.py` now asserts the remote video/audio pipeline, the
AudioContext close on peer leave, and that remote signaling never touches the
loopback Bridge.

```powershell
# inside the worktree, after rebasing onto main (f9b5725)
F:\code\projects\live-feed-quantizer\.venv\Scripts\python.exe -m pytest -q
# 176 passed
```

## Known limitations (not blockers for Tier 0)

- **Single instance only.** The registry is process-local. Run exactly one
  signaling worker, or add sticky routing plus a shared registry. This is also
  documented at the top of `signaling.py`; two workers will not see each
  other's rooms.
- Pending joins are held for `LFQ_ROOM_JOIN_TIMEOUT` (default 30 s); a client
  that joins and never sends an offer briefly occupies a pending token, not an
  active guest slot.
- Browsers require HTTPS (or localhost) for camera/microphone capture; the
  hosted deployment must terminate TLS.

## Recommendation

Keep the branch unmerged and review the four frontend behaviours manually with
two browsers on the same room (host video, host audio, guest join, and peer
leave). The signaling contract and the static pipeline checks are covered by
automated tests.
