"""Security primitives shared by the hosted server and the local Bridge.

The functions here are deliberately dependency-free so they can be unit-tested
without a network.  They cover the three attack surfaces that matter most for
this app:

* SSRF through camera / proxy URLs (scheme + address allowlists),
* DNS rebinding against the Bridge (Host + Origin checks),
* unauthenticated local access (per-origin pairing tokens).
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from config import settings

CAMERA_SCHEMES = frozenset({"http", "https", "rtsp"})
PROXY_SCHEMES = frozenset({"http", "https"})

# Ranges that are never acceptable, even with the private-LAN flag enabled:
# cloud metadata services are the classic SSRF target.
METADATA_ADDRESSES = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "fd00:ec2::254",
    }
)
METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

_SECRET_QUERY_KEYS = (
    "password",
    "passwd",
    "pass",
    "token",
    "key",
    "secret",
    "auth",
    "apikey",
    "api_key",
    "local_key",
)


class URLValidationError(ValueError):
    """Raised when a user-supplied URL is not safe to fetch."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


def redact_url(url: str) -> str:
    """Return a log-safe URL with credentials and secret query values removed."""
    try:
        parts = urlsplit(url)
    except Exception:
        return "<invalid-url>"

    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"***:***@{host}"

    query = parts.query
    if query:
        pieces = []
        for piece in query.split("&"):
            key = piece.split("=", 1)[0]
            if key.lower() in _SECRET_QUERY_KEYS:
                pieces.append(f"{key}=***")
            else:
                pieces.append(piece)
        query = "&".join(pieces)

    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _unmap(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_metadata(ip: ipaddress._BaseAddress) -> bool:
    ip = _unmap(ip)
    return str(ip) in METADATA_ADDRESSES


def address_is_blocked(ip: str | ipaddress._BaseAddress, *, allow_private: bool) -> bool:
    """True when an address must not be fetched.

    Metadata and link-local addresses are always blocked.  Loopback and
    RFC1918/ULA ranges are blocked unless ``allow_private`` is set (the Bridge
    opts in for LAN cameras; the hosted service never does).
    """
    try:
        addr = _unmap(ipaddress.ip_address(ip) if isinstance(ip, str) else ip)
    except ValueError:
        return True

    if _is_metadata(addr):
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    if addr.is_link_local:
        return True
    if addr.is_loopback:
        return not allow_private
    if addr.is_private:
        return not allow_private
    return False


def _check_hostname(hostname: str, *, allow_private: bool, resolve: bool) -> None:
    host = hostname.strip().strip("[]").rstrip(".").lower()
    if not host:
        raise URLValidationError("missing_host")
    if host in METADATA_HOSTNAMES:
        raise URLValidationError("blocked_metadata")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if address_is_blocked(literal, allow_private=allow_private):
            raise URLValidationError("blocked_address", f"{host} is not allowed")
        return

    if not resolve:
        return

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise URLValidationError("unresolvable_host", str(exc)) from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise URLValidationError("unresolvable_host")
    for address in addresses:
        if address_is_blocked(address, allow_private=allow_private):
            raise URLValidationError("blocked_address", f"{host} resolves to a blocked address")


def validate_fetch_url(
    url: str,
    *,
    allow_private: bool | None = None,
    allowed_schemes: Iterable[str] = CAMERA_SCHEMES,
    resolve: bool = False,
) -> str:
    """Validate a camera/proxy URL and return it unchanged on success.

    ``resolve=False`` avoids DNS lookups in unit tests; production callers
    should pass ``resolve=True`` so a hostname cannot resolve into a blocked
    range (DNS rebinding / internal service exposure).
    """
    if not isinstance(url, str) or not url.strip():
        raise URLValidationError("empty_url")
    url = url.strip()
    if any(ord(ch) < 0x20 for ch in url):
        raise URLValidationError("control_characters")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise URLValidationError("malformed_url", str(exc)) from exc

    scheme = parts.scheme.lower()
    if scheme not in {s.lower() for s in allowed_schemes}:
        raise URLValidationError("scheme_not_allowed", scheme or "missing scheme")
    if not parts.hostname:
        raise URLValidationError("missing_host")
    if parts.port is not None and not (1 <= parts.port <= 65535):
        raise URLValidationError("invalid_port")

    if allow_private is None:
        allow_private = settings.allow_private_networks

    _check_hostname(parts.hostname, allow_private=allow_private, resolve=resolve)
    return url


def validate_proxy_url(url: str, *, allow_private: bool | None = None, resolve: bool = False) -> str:
    return validate_fetch_url(
        url,
        allow_private=allow_private,
        allowed_schemes=PROXY_SCHEMES,
        resolve=resolve,
    )


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def origin_allowed(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    """Validate a browser Origin header against an explicit allowlist.

    A missing Origin is accepted for non-browser clients (CLI, tests); browsers
    always send one, and the Bridge additionally requires the pairing token.
    """
    if origin is None:
        return True
    normalized = normalize_origin(origin)
    if normalized is None:
        return False
    allowed = {o.lower().rstrip("/") for o in allowed_origins if o}
    return normalized.rstrip("/") in allowed


def bridge_host_allowed(host_header: str | None, allowed_hosts: Iterable[str]) -> bool:
    """DNS-rebinding defence for the loopback Bridge.

    Only ``127.0.0.1`` / ``localhost`` (with the configured port) are valid.
    An attacker-controlled domain that resolves to 127.0.0.1 still sends its
    own Host header and is rejected.
    """
    if not host_header:
        return False
    host = host_header.strip().lower().rstrip(".")
    allowed = {h.lower().rstrip(".") for h in allowed_hosts if h}
    if host in allowed:
        return True
    # Allow the same host without the port when it is one of the loopback names.
    bare = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if bare in {"127.0.0.1", "localhost", "[::1]", "::1"}:
        return True
    return False


@dataclass
class RateLimiter:
    """Thread-safe sliding-window rate limiter keyed by client/IP."""

    limit: int
    window: float = 60.0
    _events: dict[str, deque] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str, cost: int = 1) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] > self.window:
                events.popleft()
            if len(events) + cost > self.limit:
                return False
            events.extend([now] * cost)
            return True


class PairingStore:
    """Per-origin pairing tokens for the local Bridge.

    The first allowed browser origin that calls ``/pair`` receives a token.
    Later HTTP requests and WS upgrades must present it.  Tokens live in
    Bridge memory (and optionally on disk) and are never sent to any other
    origin.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()

    def issue(self, origin: str, *, force: bool = False) -> str:
        import secrets

        key = (normalize_origin(origin) or origin).lower()
        with self._lock:
            if not force and key in self._tokens:
                return self._tokens[key]
            token = secrets.token_urlsafe(24)
            self._tokens[key] = token
            return token

    def verify(self, origin: str | None, token: str | None) -> bool:
        if not origin or not token:
            return False
        key = (normalize_origin(origin) or origin).lower()
        with self._lock:
            expected = self._tokens.get(key)
        if not expected:
            return False
        import hmac

        return hmac.compare_digest(expected, token)

    def forget(self, origin: str) -> None:
        key = (normalize_origin(origin) or origin).lower()
        with self._lock:
            self._tokens.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


pairing_store = PairingStore()
http_limiter = RateLimiter(settings.http_rate_limit)
ws_limiter = RateLimiter(settings.ws_message_rate_limit)
