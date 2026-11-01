import asyncio
import time

import cv2
import numpy as np
import pytest

from frame_gateway import (
    FetchOptions,
    FrameGateway,
    decode_packet,
    encode_packet,
    process_frame,
    validate_source,
)
from security import URLValidationError


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


def test_packet_header_roundtrip():
    jpeg = _gradient_jpeg()
    packet = encode_packet(7, 123.5, jpeg)
    epoch, timestamp, body = decode_packet(packet)
    assert epoch == 7
    assert timestamp == 123.5
    assert body == jpeg
    with pytest.raises(ValueError):
        decode_packet(b"short")


def test_quantize_false_keeps_unquantized_colors():
    options = FetchOptions(kind="http", url="http://camera.local/snap.jpg", max_dim=960, quality=60)
    jpeg, w, h = process_frame(_gradient_jpeg(320, 180), options)
    assert (w, h) == (320, 180)
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    unique = len(np.unique(img.reshape(-1, 3), axis=0))
    # A palette-collapsed (7 colour) frame would have <= 7 unique colours.
    assert unique > 8


def test_max_dim_downscales_without_changing_aspect():
    options = FetchOptions(kind="http", url="http://camera.local/snap.jpg", max_dim=640, quality=60)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :, 1] = np.linspace(0, 255, 1280, dtype=np.uint8)
    _jpeg, w, h = process_frame(frame, options)
    assert w == 640
    assert h == 360


def test_source_url_validation_allows_private_lan_but_blocks_metadata():
    assert validate_source("rtsp", "rtsp://192.168.1.50:554/stream", allow_private=True)
    assert validate_source("http", "http://192.168.1.50/snap.jpg", allow_private=True)
    with pytest.raises(URLValidationError):
        validate_source("http", "http://169.254.169.254/latest/meta-data/", allow_private=True)
    with pytest.raises(URLValidationError):
        validate_source("http", "file:///etc/passwd", allow_private=True)
    with pytest.raises(URLValidationError):
        validate_source("rtsp", "not a url", allow_private=True)


def test_malformed_url_errors_without_crashing_gateway():
    async def scenario():
        gw = FrameGateway()
        with pytest.raises(URLValidationError):
            validate_source("http", "gopher://example.com/frame", allow_private=True)
        await gw.close()

    asyncio.run(scenario())


def test_fanout_shares_one_fetcher():
    calls = {"n": 0}
    jpeg = _gradient_jpeg()

    def fetch(_url):
        calls["n"] += 1
        return jpeg

    async def scenario():
        gw = FrameGateway()
        options = FetchOptions(kind="http", url="http://camera.local/snap.jpg", fps=30)
        a = await gw.subscribe(options, fetch_fn=fetch)
        b = await gw.subscribe(options, fetch_fn=fetch)
        assert a.fetcher is b.fetcher
        assert len(gw.stats()) == 1
        assert len(a.fetcher.subscribers) == 2
        pa = await a.next(timeout=2)
        pb = await b.next(timeout=2)
        assert pa and pb
        assert pa[2] == pb[2]
        assert calls["n"] >= 1
        await gw.unsubscribe(a)
        assert len(gw.stats()) == 1  # b keeps the shared fetcher alive
        await gw.unsubscribe(b)
        assert gw.stats() == []
        await gw.close()

    asyncio.run(scenario())


def test_source_switch_stops_previous_fetcher_and_increments_epoch():
    jpeg = _gradient_jpeg()

    async def scenario():
        gw = FrameGateway()
        a = await gw.subscribe(
            FetchOptions(kind="http", url="http://camera-a.local/snap.jpg", fps=30),
            fetch_fn=lambda _url: jpeg,
        )
        first = await a.next(timeout=2)
        assert first is not None
        epoch_a = a.fetcher.epoch
        await gw.unsubscribe(a)
        assert gw.stats() == []

        b = await gw.subscribe(
            FetchOptions(kind="http", url="http://camera-b.local/snap.jpg", fps=30),
            fetch_fn=lambda _url: jpeg,
        )
        assert b.fetcher.epoch > epoch_a
        assert b.fetcher.epoch != epoch_a
        await gw.unsubscribe(b)
        await gw.close()

    asyncio.run(scenario())


def test_latest_frame_wins_mailbox():
    async def scenario():
        gw = FrameGateway()
        sub = await gw.subscribe(
            FetchOptions(kind="http", url="http://camera.local/snap.jpg", fps=1),
            fetch_fn=lambda _url: _gradient_jpeg(),
        )
        sub.push(1, 1.0, b"first")
        sub.push(1, 2.0, b"second")
        sub.push(1, 3.0, b"third")
        packet = await sub.next(timeout=1)
        assert packet[2] == b"third"
        await gw.unsubscribe(sub)
        await gw.close()

    asyncio.run(scenario())


def test_frame_coalescing_keeps_latest():
    """Coalescing: only the latest state per device_id is retained."""
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


def test_fetch_error_is_reported_to_subscriber():
    async def scenario():
        gw = FrameGateway()

        def broken(_url):
            raise RuntimeError("camera offline")

        sub = await gw.subscribe(
            FetchOptions(kind="http", url="http://camera.local/snap.jpg", fps=30),
            fetch_fn=broken,
        )
        with pytest.raises(RuntimeError, match="camera offline"):
            await sub.next(timeout=2)
        await gw.unsubscribe(sub)
        await gw.close()

    asyncio.run(scenario())
