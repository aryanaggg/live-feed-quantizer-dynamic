"""Hardware smoke test for real light drivers (not part of CI).

Run this on the machine that can reach the bulb:

    python tools/hw_smoke.py --driver wiz
    python tools/hw_smoke.py --driver wiz --host 192.168.1.50
    python tools/hw_smoke.py --driver tuya --host 192.168.1.60 \
        --device-id <tuya-gateway-id> --local-key <key>

The local key can also come from ``TUYA_LOCAL_KEY`` so it does not land in
shell history.  The tool discovers, pairs when credentials are supplied, sets
red/green/blue, measures the real device ack round-trip, and prints one report
per driver.  It never prints or logs the Tuya local key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frame_gateway import (  # noqa: E402
    GoveeDriver,
    HomeAssistantDriver,
    HueDriver,
    LifxDriver,
    MatterDriver,
    TuyaDriver,
    WizDriver,
    _parse_device_address,
)

DRIVER_FACTORIES = {
    "wiz": WizDriver,
    "tuya": TuyaDriver,
    "lifx": LifxDriver,
    "govee": GoveeDriver,
    "matter": MatterDriver,
    "hue": HueDriver,
    "home_assistant": HomeAssistantDriver,
}

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


def _wiz_probe(host: str, port: int = 38899, timeout: float = 2.0) -> float | None:
    """Send a WiZ getPilot request and return the real ack round-trip in ms."""
    payload = json.dumps({"method": "getPilot", "params": {}}).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        start = time.perf_counter()
        sock.sendto(payload, (host, port))
        data, _ = sock.recvfrom(4096)
        elapsed = (time.perf_counter() - start) * 1000.0
        return elapsed if data else None
    except OSError:
        return None
    finally:
        sock.close()


def _tuya_probe(device_id: str, host: str, local_key: str,
                version: float = 3.3, timeout: float = 2.0) -> float | None:
    """Read Tuya status and return the real ack round-trip in ms."""
    try:
        import tinytuya
    except ImportError:
        return None
    bulb = tinytuya.BulbDevice(device_id, host, local_key)
    try:
        bulb.set_version(version)
        bulb.set_socketTimeout(timeout)
        bulb.set_socketPersistent(False)
        start = time.perf_counter()
        status = bulb.status()
        elapsed = (time.perf_counter() - start) * 1000.0
        return elapsed if isinstance(status, dict) and "Error" not in status else None
    except Exception:
        return None
    finally:
        bulb.close()


async def _async_probe(kind: str, args, host: str | None,
                       device_id: str | None) -> float | None:
    if kind == "wiz" and host:
        return await asyncio.to_thread(_wiz_probe, host, args.port, args.timeout)
    if kind == "tuya" and host and device_id and args.local_key:
        return await asyncio.to_thread(
            _tuya_probe, device_id, host, args.local_key,
            args.tuya_version, args.timeout,
        )
    return None


def _host_from_address(address: str) -> str | None:
    if not address:
        return None
    return _parse_device_address(address, 0)[0] or None


def _find_device(devices: list[dict], kind: str, wanted: str | None) -> dict | None:
    if not wanted:
        return devices[0] if devices else None
    wanted_ids = {wanted, f"{kind}:{wanted}"}
    return next(
        (d for d in devices
         if d.get("id") in wanted_ids or d.get("device_id") == wanted),
        None,
    )


def _synthesize_device(kind: str, args, device_id: str | None) -> dict | None:
    """Build a device entry from --host when discovery is unavailable."""
    if not args.host:
        return None
    if kind == "wiz":
        return {
            "id": f"wiz:{args.host}",
            "address": f"wiz://{args.host}:{args.port}",
            "name": f"WiZ light ({args.host})",
            "kind": "wiz",
            "provided": True,
        }
    if kind == "tuya":
        if not device_id:
            return None
        return {
            "id": f"tuya:{device_id}",
            "device_id": device_id,
            "address": f"tuya://{args.host}",
            "ip": args.host,
            "name": f"Tuya device ({args.host})",
            "version": args.tuya_version,
            "kind": "tuya",
            "provided": True,
        }
    return None


async def smoke_driver(kind: str, args) -> dict:
    args.local_key = args.local_key or os.environ.get("TUYA_LOCAL_KEY")
    requested_id = args.device_id or os.environ.get("TUYA_DEVICE_ID")

    driver = DRIVER_FACTORIES[kind](None)
    report = {
        "driver": kind,
        "discovered": 0,
        "provided": False,
        "paired": False,
        "color_ok": False,
        "colors": [],
        "probe_latency_ms": "unmeasurable",
        "ack_latency_ms": "unmeasurable",
        "capabilities": driver.capabilities(),
    }

    try:
        devices = await driver.discover()
    except Exception as exc:
        report["error"] = f"discover: {type(exc).__name__}: {exc}"
        return report

    report["discovered"] = len(devices)
    device = _find_device(devices, kind, requested_id)
    if device is None:
        device = _synthesize_device(kind, args, requested_id)
        if device is not None:
            # The driver's public discover() has no host override; register the
            # explicitly supplied device so set_state can address it.
            driver._devices[device["id"]] = device
            report["provided"] = True

    host = args.host or (_host_from_address(device.get("address", "")) if device else None)
    device_id = (device or {}).get("device_id") or requested_id
    report["host"] = host
    report["device_id"] = device_id or (device or {}).get("id")

    if device is None:
        report["error"] = "no_device_found"
        return report

    if kind == "tuya":
        if not args.local_key:
            report["error"] = "needs_credentials"
            return report
        try:
            await driver.pair({"local_key": args.local_key})
            report["paired"] = True
        except Exception as exc:
            report["error"] = f"pair: {type(exc).__name__}: {exc}"
            return report
    else:
        try:
            await driver.pair()
            report["paired"] = True
        except Exception as exc:
            report["error"] = f"pair: {type(exc).__name__}: {exc}"

    probe = await _async_probe(kind, args, host, device_id)
    if probe is not None:
        report["probe_latency_ms"] = round(probe, 1)

    color_results = []
    for r, g, b in COLORS:
        try:
            await driver.set_state({
                "device_id": device.get("id"),
                "r": r, "g": g, "b": b,
                "brightness": 40,
                "transition": 0,
            })
            color_results.append({
                "rgb": [r, g, b],
                "sent": True,
                "ack_ms": driver.latency_diagnostics(),
            })
        except Exception as exc:
            color_results.append({
                "rgb": [r, g, b],
                "sent": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    report["colors"] = color_results
    report["color_ok"] = all(item["sent"] for item in color_results)

    measured_acks = [
        item["ack_ms"] for item in color_results
        if isinstance(item.get("ack_ms"), (int, float))
    ]
    if measured_acks:
        report["ack_latency_ms"] = round(sum(measured_acks) / len(measured_acks), 1)

    report["status"] = driver.status()
    return report


def _print_report(reports: list[dict]) -> None:
    print("\nHardware smoke report")
    print("=" * 78)
    for report in reports:
        print(f"\n[{report['driver']}]")
        print(f"  discovered : {report['discovered']}")
        print(f"  provided   : {report['provided']}")
        print(f"  paired     : {report['paired']}")
        print(f"  colors ok  : {report['color_ok']}")
        print(f"  probe ack  : {report['probe_latency_ms']} ms (getPilot/status)")
        print(f"  set ack    : {report['ack_latency_ms']} ms (set_state)")
        for item in report.get("colors", []):
            rgb = ",".join(str(value) for value in item["rgb"])
            if item["sent"]:
                print(f"    rgb({rgb}) -> ack {item['ack_ms']} ms")
            else:
                print(f"    rgb({rgb}) -> FAILED {item.get('error', '')}")
        print(f"  host       : {report.get('host') or 'unknown'}")
        print(f"  device id  : {report.get('device_id') or 'none'}")
        print(f"  caps       : {json.dumps(report['capabilities'], sort_keys=True)}")
        if "error" in report:
            print(f"  error      : {report['error']}")
    print("\nLatency is a real device acknowledgement for WiZ/Tuya when reachable;")
    print("otherwise 'unmeasurable'.  No credentials are printed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-hardware light smoke test (not CI).")
    parser.add_argument("--driver", action="append", choices=sorted(DRIVER_FACTORIES),
                        help="driver to test; repeatable (default: wiz, tuya)")
    parser.add_argument("--host", help="override device host/IP")
    parser.add_argument("--port", type=int, default=38899, help="WiZ UDP port")
    parser.add_argument("--device-id", help="Tuya gateway id (or TUYA_DEVICE_ID)")
    parser.add_argument("--local-key", help="Tuya local key (or TUYA_LOCAL_KEY; never printed)")
    parser.add_argument("--tuya-version", type=float, default=3.3)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--wiz-broadcast", help="WiZ broadcast address override")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.wiz_broadcast:
        os.environ["WIZ_BROADCAST"] = args.wiz_broadcast

    kinds = args.driver or ["wiz", "tuya"]
    reports = [asyncio.run(smoke_driver(kind, args)) for kind in kinds]
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
    else:
        _print_report(reports)

    return 0 if all(
        (report["discovered"] or report["provided"])
        and report["color_ok"]
        and report["ack_latency_ms"] != "unmeasurable"
        for report in reports
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
