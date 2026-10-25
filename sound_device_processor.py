# sound_device_processor.py
"""Real-time quantizer pipeline: microphone audio -> color palette-> frame.

Each :class:`AudioPaletteQuantizer` instance owns its own blending state, so
multiple consumers (WebSocket clients, a local window, tests) never interfere
with each other and no module-level mutable state is shared across sessions.

Behavior changes from the legacy module-level implementation:
- The client's sample rate can be supplied (web browsers usually run at
  48 kHz, not the legacy hardcoded 44.1 kHz).
- Silent audio / missing audio no longer raises: the last known palette is
  reused so video keeps flowing while the microphone is quiet. When no
  palette has been produced at all yet, ``process`` returns ``None``.
- ``skip_audio`` means "quantize with the current palette, don't update it"
  instead of raising.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

from audio_to_palette.music_visualizer import extract_colors_from_audio
from image_quantizer.quantizer import quantize_array

DEFAULT_SAMPLE_RATE = 44100
PALETTE_SIZE = 7
GAIN_SCALE = 15.0
BLEND_ALPHA = 0.8
MIN_RMS = 1e-6
MIN_PALETTE_BRIGHTNESS = 20
MIN_SAMPLE_RATE = 1000
MAX_SAMPLE_RATE = 192000


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


class AudioPaletteQuantizer:
    """Audio-driven palette quantizer with per-instance smooth-blend state."""

    def __init__(self, sample_rate=DEFAULT_SAMPLE_RATE, palette_size=PALETTE_SIZE,
                 gain_scale=GAIN_SCALE, blend_alpha=BLEND_ALPHA):
        self._lock = threading.Lock()
        self.sample_rate = self._sanitize_sample_rate(sample_rate)
        self.palette_size = max(1, int(palette_size))
        self.gain_scale = float(gain_scale)
        self.blend_alpha = float(_clamp(blend_alpha, 0.0, 1.0))
        self.prev_palette = None   # blended palette actually used for output
        self.last_palette = None   # last palette extracted from live audio

    @staticmethod
    def _sanitize_sample_rate(sample_rate):
        try:
            sr = float(sample_rate)
        except (TypeError, ValueError):
            return DEFAULT_SAMPLE_RATE
        if not np.isfinite(sr) or not (MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE):
            return DEFAULT_SAMPLE_RATE
        return sr

    def set_sample_rate(self, sample_rate):
        with self._lock:
            self.sample_rate = self._sanitize_sample_rate(sample_rate)

    def reset(self):
        with self._lock:
            self.prev_palette = None
            self.last_palette = None

    @staticmethod
    def _to_float_mono(chunk):
        if chunk is None:
            return None
        arr = np.asarray(chunk)
        if arr.size == 0:
            return None
        arr = arr.astype(np.float32, copy=False).ravel()
        if not np.all(np.isfinite(arr)):
            return None
        return arr

    def _extract_palette(self, chunk):
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        if not np.isfinite(rms) or rms < MIN_RMS:
            return None
        gain = max(rms * self.gain_scale, 1.0)
        results = extract_colors_from_audio(
            audio_array=chunk,
            sr=self.sample_rate,
            N=self.palette_size,
            gain=gain,
        )
        return results[0] if results else None

    def _update_palette(self, audio_chunk):
        """Blend in a fresh palette when audio is energetic.

        On silence or a failed extraction the previous palette is kept so the
        quantized video keeps flowing.
        """
        with self._lock:
            chunk = self._to_float_mono(audio_chunk)
            new_palette = None
            if chunk is not None:
                try:
                    new_palette = self._extract_palette(chunk)
                except Exception as exc:
                    print(f"[ERROR] extract_colors_from_audio failed: {exc}")

            if new_palette is not None:
                palette_array = np.asarray(new_palette, dtype=np.int16)
                if palette_array.size and int(palette_array.max()) >= MIN_PALETTE_BRIGHTNESS:
                    self.last_palette = new_palette
                    if self.prev_palette is not None:
                        blended = (
                            np.asarray(self.prev_palette) * (1.0 - self.blend_alpha)
                            + np.asarray(new_palette, dtype=float) * self.blend_alpha
                        ).astype(int).tolist()
                    else:
                        blended = new_palette
                    self.prev_palette = blended

            return self.prev_palette

    def process(self, frame, audio_chunk=None):
        """Quantize one BGR frame.

        Returns a quantized BGR numpy array, or ``None`` when there is no
        usable audio and no palette has been established yet (the caller can
        then decide to skip the frame or surface a status message).
        """
        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
            return frame

        palette = self._update_palette(audio_chunk)
        if palette is None:
            return None

        try:
            rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        except cv2.error as exc:
            print(f"[ERROR] cvColor conversion failed: {exc}")
            return frame

        try:
            quantized_rgb = quantize_array(rgb, palette)
            return cv2.cvtColor(quantized_rgb, cv2.COLOR_RGB2BGR)
        except Exception as exc:
            print(f"[ERROR] quantize_image failed: {exc}")
            return frame


# Backwards-compatible module-level entry point (shared default instance).
_default_quantizer = None


def process_frame(frame, audio_chunk=None, skip_audio=False):
    """Legacy single-instance API kept for compatibility.

    ``skip_audio=True`` now quantizes with the cached palette instead of
    raising, so alternating frames no longer error out.
    """
    global _default_quantizer
    if _default_quantizer is None:
        _default_quantizer = AudioPaletteQuantizer()
    return _default_quantizer.process(frame, None if skip_audio else audio_chunk)
