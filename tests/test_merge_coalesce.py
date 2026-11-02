"""Merge and coalescing tests for discovery and frame gateway."""

import asyncio
import hashlib
import time

import pytest

from discovery import DeviceState, merge_devices


# ============================================================
# Merge behavior tests
# ==================================================================


def test_same_observation_remerge_keeps_seen_count_and_ttl():
    """(a) Re-merging the exact same observation changes neither count nor TTL."""
    d1 = DeviceState(
        "test-id-1", "onvif", "Camera 1", observed_at=1000.0,
        protocol="onvif", url="http://192.168.1.50:554/stream",
    )
    d1.seen_count = 4
    original_last_seen = time.time() - 100
    d1.last_seen = original_last_seen

    merged = merge_devices({d1.device_id: d1}, [d1])

    assert merged[d1.device_id] is d1
    assert merged[d1.device_id].seen_count == 4
    assert merged[d1.device_id].last_seen == original_last_seen


def test_new_observation_increments_seen_count_and_resets_ttl():
    """(b) A genuinely new observation increments seen_count and resets TTL."""
    url1 = "http://192.168.1.50:554/stream"
    id1 = "url:" + hashlib.sha256(url1.encode()).hexdigest()[:16]

    first = DeviceState(id1, "onvif", "Camera 1", observed_at=1000.0,
                        protocol="onvif", url=url1)
    merged = merge_devices({}, [first])
    assert merged[id1].seen_count == 1
    assert merged[id1].last_seen > time.time() - 1

    old_last_seen = time.time() - 100
    merged[id1].last_seen = old_last_seen

    # Same device, same fields, but a different observation timestamp.
    second = DeviceState(id1, "onvif", "Camera 1", observed_at=1001.0,
                         protocol="onvif", url=url1)
    merged = merge_devices(merged, [second])

    assert merged[id1].seen_count == 2
    assert merged[id1].last_seen > old_last_seen
    assert merged[id1].last_seen > time.time() - 1


def test_genuine_field_update_resets_ttl_without_incrementing_seen_count():
    """(c) A changed field resets TTL; it is still the same observation."""
    url1 = "http://192.168.1.50:554/stream"
    url2 = "http://192.168.1.51:554/stream"
    id1 = "url:" + hashlib.sha256(url1.encode()).hexdigest()[:16]

    first = DeviceState(id1, "onvif", "Camera 1", observed_at=1000.0,
                        protocol="onvif", url=url1)
    merged = merge_devices({}, [first])
    assert merged[id1].seen_count == 1

    old_last_seen = time.time() - 100
    merged[id1].last_seen = old_last_seen

    # Same observation (same observed_at) but the URL field changed.
    updated = DeviceState(id1, "onvif", "Camera 1", observed_at=1000.0,
                          protocol="onvif", url=url2)
    merged = merge_devices(merged, [updated])

    assert merged[id1].seen_count == 1
    assert merged[id1].last_seen > old_last_seen
    assert merged[id1].last_seen > time.time() - 1
    assert merged[id1].kwargs["url"] == url2


# ============================================================
# Coalescing tests for frame gateway
# ==================================================================


def test_frame_coalescing_keeps_latest():
    """Coalescing: only the latest state per device_id is retained."""
    from frame_gateway import FrameGateway, FetchOptions
    import cv2
    import numpy as np

    def _gradient_jpeg(w=320, h=180, seed=0):
        x = np.linspace(0, 255, w, dtype=np.uint8)
        y = np.linspace(0, 255, h, dtype=np.uint8)
        r = np.tile(x, (h, 1))
        g = np.tile(y[:, None], (1, w))
        b = np.full((h, w), (seed * 37) % 255, dtype=np.uint8)
        img = np.dstack([b, g, r])
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        assert ok
        return buf.tobytes()

    async def scenario():
        gw = FrameGateway()
        sub = await gw.subscribe(
            FetchOptions(kind="http", url="http://camera.local/snap.jpg", fps=1),
            fetch_fn=lambda _url: _gradient_jpeg(),
        )
        # Push three frames with the same device_id but different epochs
        sub.push(1, 1.0, b"first")
        sub.push(1, 2.0, b"second")
        sub.push(1, 3.0, b"third")
        # The mailbox should deliver the latest (third)
        packet = await sub.next(timeout=1)
        assert packet[2] == b"third"
        # Advance epoch to clear the mailbox
        await sub.next(timeout=1)
        # Push another frame - should be "fourth"
        sub.push(1, 4.0, b"fourth")
        packet = await sub.next(timeout=1)
        assert packet[2] == b"fourth"
        await gw.unsubscribe(sub)
        await gw.close()

    asyncio.run(scenario())