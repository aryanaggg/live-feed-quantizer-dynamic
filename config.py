"""Central runtime configuration.

Everything that used to be duplicated across ``package.json``, the README and
``server.py`` (ports, origins) lives here.  Values can be overridden with
environment variables so the same code runs for local dev, the hosted service
and the local Bridge.
"""

from __future__ import annotations

import os

DEFAULT_PORT = 8001
DEFAULT_BRIDGE_PORT = 8765
DEFAULT_BRIDGE_PORTS = tuple(range(DEFAULT_BRIDGE_PORT, DEFAULT_BRIDGE_PORT + 3))

_LOCAL_ORIGINS = (
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default):
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """Process-wide settings, read once at import time."""

    def __init__(self) -> None:
        self.port = _env_int("PORT", DEFAULT_PORT)
        self.bridge_port = _env_int("LFQ_BRIDGE_PORT", DEFAULT_BRIDGE_PORT)

        # Hosted server: only these browser origins may call the API / WS.
        self.allowed_origins = _env_list("LFQ_ALLOWED_ORIGINS", _LOCAL_ORIGINS)
        # The hosted deployment sets this to its public origin(s).
        self.public_origin = os.environ.get("LFQ_PUBLIC_ORIGIN", "").strip()

        # The hosted server must never fetch camera URLs (section 3 of the
        # architecture).  Developers can opt in explicitly; the Bridge always
        # enables it locally.
        self.allow_server_camera = _env_bool("LFQ_ALLOW_SERVER_CAMERA", False)
        # Allow private/LAN address ranges for camera fetches.  Off by default
        # for the hosted service, always on for the local Bridge.
        self.allow_private_networks = _env_bool("LFQ_ALLOW_PRIVATE_NETWORKS", False)

        # Host header allowlist for the Bridge (DNS-rebinding defence).
        self.bridge_allowed_hosts = _env_list(
            "LFQ_BRIDGE_ALLOWED_HOSTS",
            (f"127.0.0.1:{self.bridge_port}", f"localhost:{self.bridge_port}"),
        )
        # Browser origins allowed to talk to the Bridge.  A hosted page must be
        # added explicitly by the deployment.
        self.bridge_allowed_origins = _env_list(
            "LFQ_BRIDGE_ALLOWED_ORIGINS",
            (
                "http://localhost:5500",
                "http://127.0.0.1:5500",
                f"http://localhost:{self.bridge_port}",
                f"http://127.0.0.1:{self.bridge_port}",
                self.public_origin,
            ),
        )

        # Rate limiting (requests or WS control messages per minute).
        self.http_rate_limit = _env_int("LFQ_HTTP_RATE_LIMIT", 60)
        self.ws_message_rate_limit = _env_int("LFQ_WS_RATE_LIMIT", 240)
        self.max_camera_sources_per_client = _env_int("LFQ_MAX_CAMERA_SOURCES", 2)

        # WebRTC signalling.
        self.signaling_room_ttl = _env_int("LFQ_ROOM_TTL", 600)
        self.signaling_room_grace = _env_int("LFQ_ROOM_GRACE", 60)
        self.signaling_max_guests = _env_int("LFQ_ROOM_MAX_GUESTS", 6)
        self.signaling_max_rooms_per_ip = _env_int("LFQ_ROOM_MAX_PER_IP", 10)

        # ICE servers (STUN always; TURN when configured).
        self.stun_urls = _env_list(
            "LFQ_STUN_URLS", ("stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302")
        )
        self.turn_urls = _env_list("LFQ_TURN_URLS", ())
        self.turn_username = os.environ.get("LFQ_TURN_USERNAME", "")
        self.turn_password = os.environ.get("LFQ_TURN_PASSWORD", "")
        self.turn_secret = os.environ.get("LFQ_TURN_SECRET", "")
        self.turn_ttl = _env_int("LFQ_TURN_TTL", 3600)

        # Bridge credential storage (local key material is never sent to the
        # browser).  Defaults to a file beside the Bridge data cache.
        self.bridge_state_dir = os.environ.get(
            "LFQ_BRIDGE_STATE_DIR",
            os.path.join(os.path.expanduser("~"), ".live-feed-quantizer"),
        )

    @property
    def is_https_deployment(self) -> bool:
        return self.public_origin.startswith("https://")


settings = Settings()
