"""Static checks for the single-file browser client.

Browser-only behaviour (permission prompts, WebGL, WebRTC) is covered by the
manual checklist in README.md.  These checks catch the regressions that are
cheap to detect without a browser: broken JavaScript syntax, accidental
splitting of the single file, and removal of the source abstraction.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"


def _index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


def _inline_script() -> str:
    scripts = re.findall(r"(?s)<script>(.*?)</script>", _index_text())
    assert scripts, "frontend/index.html must contain inline scripts"
    return "\n;\n".join(scripts)


def test_frontend_is_still_a_single_file():
    assert INDEX.exists()
    assert INDEX.as_posix().endswith("frontend/index.html")


def test_source_abstraction_is_present():
    text = _index_text()
    for symbol in (
        "class FrameSource",
        "class LocalCameraSource",
        "class AudioSource",
        "class LocalMicSource",
        "class SilentAudioSource",
        "class RemoteDeviceSource",
        "class RemoteAudioSource",
        "RTCPeerConnection",
        "createRemoteRoom",
        "joinAsGuest",
        "backoffDelay",
        "resolveBackend",
        "lfq_sources_v2",
        "savedCams",
        "devicechange",
    ):
        assert symbol in text, f"missing client architecture symbol: {symbol}"


def _remote_section() -> str:
    text = _index_text()
    start = text.index("/* ---------- Tier 0: remote devices over WebRTC ---------- */")
    end = text.index("/* ---------- WebGL:")
    return text[start:end]


def test_remote_tracks_use_the_same_video_and_audio_pipeline():
    section = _remote_section()

    # Host: an incoming peer track becomes one MediaStream that feeds the same
    # FrameSource / AudioSource abstractions as local devices.
    assert "pc.ontrack = event =>" in section
    assert "entry.stream = stream;" in section
    assert "new RemoteDeviceSource(src.id, src.peerId, entry.stream" in section
    assert "new RemoteAudioSource('remote-audio-' + peerId, entry.stream)" in section
    assert "videoSource = source;" in section
    assert "audioSource = source;" in section
    assert "this.ctx.createMediaStreamSource(this.stream).connect(this.analyser);" in section

    # Guest: both camera and microphone tracks are offered to the host.
    assert "guest.stream.getTracks().forEach(track => pc.addTrack(track, guest.stream));" in section
    assert "audio: true" in section

    # A remote peer leaving must close its AudioContext, not just the video.
    assert "remoteAudioPeerId === peerId && audioSource instanceof RemoteAudioSource" in section
    assert "audioSource.stop();" in section

    # ICE configuration comes from the signaling server, so STUN/TURN are set
    # by the deployment (env) rather than hardcoded in the browser.
    assert "remote.iceServers = data.iceServers || [];" in section
    assert "new RTCPeerConnection({ iceServers: remote.iceServers })" in section


def test_remote_signaling_has_no_bridge_dependency():
    section = _remote_section()
    assert "bridgeURL" not in section
    assert "BRIDGE_PORTS" not in section
    assert "127.0.0.1" not in section
    # Signaling is resolved from the hosted backend (query override, https
    # origin, or the local dev server), never through the loopback Bridge.
    assert "fetch(`${resolveBackend()}${path}`" in section
    assert "async function signalRequest(path, options = {})" in section


def test_no_server_palette_or_wildcard_cors_in_client():
    text = _index_text()
    # The client must never send frames to the server for quantization.
    assert "uP[8]" in text  # WebGL palette uniform still owns the look
    assert "allow_origins" not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_inline_javascript_parses():
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(_inline_script())
        path = handle.name
    try:
        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    finally:
        Path(path).unlink(missing_ok=True)


def test_frontend_renders_light_driver_modes_from_capabilities():
    text = _index_text()
    assert "lightDriverModeLabel" in text
    assert "renderLightDriverModes" in text
    assert "pairing only" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_hue_capabilities_render_as_pairing_only():
    script = _inline_script()
    match = re.search(r"(function lightDriverModeLabel\(caps\) \{.*?\n\})", script, re.S)
    assert match, "frontend must define lightDriverModeLabel(caps)"

    probe = match.group(1) + """
const hue = {streaming: false, rate_mode: 'unavailable', max_rate_hz: 0};
const wiz = {streaming: true, rate_mode: 'slow', max_rate_hz: 1};
console.log(lightDriverModeLabel(hue));
console.log(lightDriverModeLabel(wiz));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(probe)
        path = handle.name
    try:
        result = subprocess.run(
            ["node", path], capture_output=True, text=True, encoding="utf-8"
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().splitlines()
        assert lines[0] == "pairing only"
        assert lines[1] == "slow · 1 Hz"
    finally:
        Path(path).unlink(missing_ok=True)
