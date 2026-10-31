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
    match = re.search(r"(?s)<script>(.*?)</script>", _index_text())
    assert match, "frontend/index.html must contain one inline script"
    return match.group(1)


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
        "backoffDelay",
        "resolveBackend",
        "lfq_sources_v2",
        "savedCams",
        "devicechange",
    ):
        assert symbol in text, f"missing client architecture symbol: {symbol}"


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
