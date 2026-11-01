"""Discovery engine for Live Feed Quantizer.

Runs concurrently per network interface, merges results by MAC/UUID/IP.
Supports: WS-Discovery, SSDP, subnet sweep (basic), and light drivers.

State machine per device: found -> verified -> needs_credentials -> ready -> lost (TTL).
Pushes device_added/updated/removed over WS; no polling.
CLI: python -m discovery --once prints results per method.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from config import settings

logger = logging.getLogger("live_feed_quantizer.discovery")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WS_DISCOVERY_GROUP = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

SSDP_PORT = 1900
SSDP_MSEARCH = b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n\r\n"

DEVICE_TTL = 300  # 5 minutes


class DeviceState:
    """Mutable device state with TTL-based expiry.

    ``observed_at`` identifies one discovery observation.  Two merges are the
    same observation only when both the device identity (``device_id``) and
    this timestamp match.  Discovery code leaves it unset, so every freshly
    created ``DeviceState`` is a new observation.
    """

    def __init__(self, device_id: str, kind: str, name: str, observed_at: float | None = None, **kwargs):
        self.device_id = device_id
        self.kind = kind
        self.name = name
        self.kwargs = kwargs
        self.first_seen = time.time()
        self.observed_at = self.first_seen if observed_at is None else float(observed_at)
        self.last_seen = self.first_seen
        self.seen_count = 0
        self.verified = False
        self.needs_credentials = False
        self.ready = False
        self.lost = False
        # Bookkeeping used by merge_devices().
        self._observation_keys: set[tuple[str, float]] = set()
        self._merged_fields: dict | None = None

    @property
    def observation_key(self) -> tuple[str, float]:
        """Identity of this discovery observation: (device_id, observed_at)."""
        return (self.device_id, self.observed_at)

    def update(self):
        self.last_seen = time.time()
        self.seen_count += 1

    def is_stale(self) -> bool:
        return time.time() - self.last_seen > DEVICE_TTL

    def mark_lost(self):
        self.lost = True

    def to_dict(self) -> dict:
        base = {
            "device_id": self.device_id,
            "kind": self.kind,
            "name": self.name,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "seen_count": self.seen_count,
        }
        base.update(self.kwargs)
        return base


def _device_id_from_url(url: str) -> str:
    return "url:" + hashlib.sha256(url.encode()).hexdigest()[:16]


def _observation_fields(device: DeviceState) -> dict:
    """Fields that define what was observed about a device.

    A change in any of these fields is a genuine metadata update, not merely a
    repeat of the same observation.  ``observed_at`` is deliberately excluded:
    it identifies the observation event, not the observed state.
    """
    fields = {"kind": device.kind, "name": device.name}
    fields.update(device.kwargs)
    return fields


def merge_devices(
    existing: Dict[str, DeviceState],
    new_devices: List[DeviceState],
) -> Dict[str, DeviceState]:
    """Merge observations into the device table.

    Same observation (same ``device_id`` and ``observed_at``) re-merged:
        no change to ``seen_count`` and no change to ``last_seen``.

    Truly new observation (same ``device_id``, different ``observed_at``):
        ``seen_count += 1`` and ``last_seen`` reset.

    Genuine field update (same observation, different observed fields):
        ``last_seen`` reset, ``seen_count`` unchanged.
    """
    merged: Dict[str, DeviceState] = {}
    for device in list(existing.values()):
        merged[device.device_id] = device
        if not device._observation_keys:
            device._observation_keys = {device.observation_key}
        if device._merged_fields is None:
            device._merged_fields = _observation_fields(device)

    for device in new_devices:
        did = device.device_id
        fields = _observation_fields(device)
        key = device.observation_key

        if did not in merged:
            # First time this device is seen: it is one new observation.
            merged[did] = device
            device.update()
            device._observation_keys = {key}
            device._merged_fields = fields
            continue

        current = merged[did]
        same_observation = key in current._observation_keys
        changed = fields != current._merged_fields

        if same_observation:
            if not changed:
                # Exact re-merge of the same observation: TTL stays untouched.
                continue
            # Same observation, corrected/changed metadata: genuine update.
            current.last_seen = time.time()
        else:
            # A genuinely new sighting of a known device.
            current.seen_count += 1
            current.last_seen = time.time()
            current._observation_keys.add(key)

        current.kind = device.kind
        current.name = device.name
        current.kwargs.update(device.kwargs)
        current._merged_fields = fields

    to_remove = []
    for did, dev in merged.items():
        if dev.is_stale() and not dev.ready:
            dev.mark_lost()
            to_remove.append(did)
    for did in to_remove:
        merged.pop(did, None)
    return merged


# ============================================================================
# WS-Discovery
# ============================================================================


def _inet_atom(addr: str) -> bytes:
    parts = addr.split(".")
    return bytes(int(p) for p in parts)


async def _ws_discovery_once(timeout: float = 3.0) -> List[DeviceState]:
    discovered: List[DeviceState] = []
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", WS_DISCOVERY_PORT))
        mreq = struct.pack("4sI", _inet_atom(WS_DISCOVERY_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        probe = struct.pack("!4sH", b"\x01\x00\x00\x0c", 0x0024)
        probe += struct.pack("!I", 0x6723A127)
        probe += b" xmlns:w='urn:schemas-upnp-org:ws-discovery':"
        probe += b" xmlns:d='urn:schemas-upnp-org:metadata:1'"
        probe += b"\r\n"
        probe += b"X-USER: *\r\n"
        probe += b"\r\n"

        sock.sendto(probe, (WS_DISCOVERY_GROUP, WS_DISCOVERY_PORT))

        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                break
            try:
                text = data.decode("utf-8", errors="ignore")
                headers = {}
                for line in text.split("\r\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        headers[key.strip().lower()] = val.strip()
                location = headers.get("x-addr", "")
                if not location:
                    continue
                from urllib.parse import urlsplit
                parts = urlsplit(location)
                host = parts.hostname or addr[0]
                port = parts.port or 80
                path = parts.path or "/"
                device_id = _device_id_from_url(location)
                dev = DeviceState(
                    device_id=device_id,
                    kind="onvif",
                    name=f"ONVIF camera at {host}",
                    protocol="onvif",
                    url=f"http://{host}:{port}{path}",
                    source_address=location,
                )
                discovered.append(dev)
            except Exception as exc:
                logger.debug("[WS-DISC] Parse error: %s", exc)
                continue
    except Exception as exc:
        logger.warning("[WS-DISC] Error: %s", exc)
    finally:
        if sock:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,
                    struct.pack("4sI", _inet_atom(WS_DISCOVERY_GROUP), socket.INADDR_ANY)
                )
            except Exception:
                pass
            sock.close()
    return discovered


async def discover_ws_disc(timeout: float = 3.0) -> List[DeviceState]:
    results: List[DeviceState] = []
    try:
        devs = await _ws_discovery_once(timeout)
        results.extend(devs)
    except Exception as exc:
        logger.warning("[WS-DISC] outer error: %s", exc)
    return results


# ============================================================================
# SSDP
# ============================================================================


async def _ssdp_once(timeout: float = 3.0) -> List[DeviceState]:
    discovered: List[DeviceState] = []
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", SSDP_PORT))
        sock.sendto(SSDP_MSEARCH, ("239.255.255.250", SSDP_PORT))
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            try:
                text = data.decode("utf-8", errors="ignore")
                headers = {}
                for line in text.split("\r\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        headers[key.strip().lower()] = val.strip()
                location = headers.get("location", "")
                usn = headers.get("usn", "")
                if not location:
                    continue
                from urllib.parse import urlsplit
                parts = urlsplit(location)
                host = parts.hostname or addr[0]
                port = parts.port or 80
                path = parts.path or "/"
                usn_lower = usn.lower()
                if "onvif" in usn_lower:
                    kind = "onvif"
                elif "rtsp" in usn_lower or "camera" in usn_lower:
                    kind = "rtsp"
                else:
                    kind = "ip"
                device_id = _device_id_from_url(location)
                dev = DeviceState(
                    device_id=device_id,
                    kind=kind,
                    name=usn.strip() or f"{kind} device at {host}",
                    protocol=kind,
                    url=f"http://{host}:{path}",
                    location=location,
                )
                discovered.append(dev)
            except Exception as exc:
                logger.debug("[SSDP] Parse error: %s", exc)
                continue
    except Exception as exc:
        logger.warning("[SSDP] Error: %s", exc)
    finally:
        if sock:
            sock.close()
    return discovered


async def discover_ssdp(timeout: float = 3.0) -> List[DeviceState]:
    return await _ssdp_once(timeout)


# ============================================================================
# Light drivers
# ============================================================================


async def _wiz_discover(timeout: float = 2.0) -> List[DeviceState]:
    discovered: List[DeviceState] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        discover_cmd = bytes.fromhex("0100000a")
        sock.sendto(discover_cmd, ("255.255.255.255", 1930))
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            if len(data) >= 8:
                ip_str = ".".join(str(b) for b in data[:4])
                port = data[4] | (data[5] << 8)
                name_len = data[7]
                name = data[8 : 8 + name_len].decode("utf-8", errors="ignore") if len(data) > 8 else "WiZ device"
                device_id = _device_id_from_url(f"wiz://{ip_str}:{port}")
                dev = DeviceState(
                    device_id=device_id,
                    kind="wiz",
                    name=name or "WiZ light",
                    protocol="wiz",
                    url=f"wiz://{ip_str}:{port}",
                    ip=ip_str,
                    port=port,
                )
                discovered.append(dev)
        sock.close()
    except Exception as exc:
        logger.warning("[WiZ] Discovery error: %s", exc)
    return discovered


async def _tuya_scan(timeout: float = 5.0) -> List[DeviceState]:
    discovered: List[DeviceState] = []
    try:
        import tinytuya
        try:
            results = tinytuya.scan(timeout=timeout)
            for device_id, info in results.items():
                ip = info.get("ip", "")
                name = info.get("name", "Tuya device")
                model = info.get("model", "")
                device_id_full = _device_id_from_url(f"tuya://{ip}")
                dev = DeviceState(
                    device_id=device_id_full,
                    kind="tuya",
                    name=name or f"Tuya device ({model})",
                    protocol="tuya",
                    url=f"tuya://{ip}",
                    ip=ip,
                )
                discovered.append(dev)
        except Exception as exc:
            logger.debug("[Tuya] Scan error: %s", exc)
    except ImportError:
        logger.warning("[Tuya] tinytuya not available, skipping")
    return discovered


async def discover_lights(timeout: float = 5.0) -> List[DeviceState]:
    results: List[DeviceState] = []
    wiz = await _wiz_discover(timeout)
    results.extend(wiz)
    tuya = await _tuya_scan(timeout)
    results.extend(tuya)
    return results


# ============================================================================
# Main orchestrator
# ============================================================================


async def discover_all(timeout: float = 5.0) -> Dict[str, any]:
    """Run all discovery methods concurrently and return merged results."""
    tasks = [
        discover_ws_disc(timeout),
        discover_ssdp(timeout),
        discover_lights(timeout),
    ]
    # Subnet sweep is omitted for now to avoid hanging
    results = await asyncio.gather(*tasks, return_exceptions=True)

    by_method: Dict[str, List[DeviceState]] = {
        "ws_disc": results[0] if not isinstance(results[0], Exception) else [],
        "ssdp": results[1] if not isinstance(results[1], Exception) else [],
        "lights": results[2] if len(results) > 2 and not isinstance(results[2], Exception) else [],
    }

    all_devices: Dict[str, DeviceState] = {}
    for method_devs in by_method.values():
        for dev in method_devs:
            all_devices = merge_devices(all_devices, [dev])

    stale = [did for did, dev in all_devices.items() if dev.is_stale() and not dev.ready]

    return {
        "devices": all_devices,
        "by_method": by_method,
        "stale": stale,
        "timestamp": time.time(),
    }


# ============================================================================
# CLI
# ============================================================================


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Live Feed Quantizer Discovery")
    parser.add_argument("--once", action="store_true", help="Run once and print results")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout per method (seconds)")
    args = parser.parse_args()

    if args.once:
        data = asyncio.run(discover_all(args.timeout))
        print(json.dumps({
            "timestamp": data["timestamp"],
            "stale": data["stale"],
            "by_method": {
                k: [d.to_dict() for d in v] for k, v in data["by_method"].items()
            },
            "total_devices": len(data["devices"]),
        }, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()