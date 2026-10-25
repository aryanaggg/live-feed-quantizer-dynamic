import numpy as np
import pytest

from sound_device_processor import AudioPaletteQuantizer, process_frame

SR = 8000


def _tone(seconds=0.5, freq=220, amp=0.4):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _frame():
    return np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)


def test_returns_none_without_audio():
    quantizer = AudioPaletteQuantizer()
    assert quantizer.process(_frame(), None) is None


def test_returns_none_for_silent_audio_before_any_palette():
    quantizer = AudioPaletteQuantizer()
    silence = np.zeros((SR // 10,), dtype=np.float32)
    assert quantizer.process(_frame(), silence) is None


def test_quantizes_with_audio_and_output_uses_palette():
    quantizer = AudioPaletteQuantizer(sample_rate=SR)
    out = quantizer.process(_frame(), _tone())
    assert out is not None
    assert out.shape == (32, 32, 3)
    assert out.dtype == np.uint8
    # every output pixel must be exactly one of the palette colors
    # (process() returns BGR; the palette is RGB, so flip channels first)
    palette = {tuple(c) for c in np.asarray(quantizer.last_palette)}
    pixels = {tuple(p) for p in out[..., ::-1].reshape(-1, 3)}
    assert pixels <= palette


def test_silence_reuses_last_palette():
    quantizer = AudioPaletteQuantizer(sample_rate=SR)
    assert quantizer.process(_frame(), _tone()) is not None
    silence = np.zeros((SR // 10,), dtype=np.float32)
    out = quantizer.process(_frame(), silence)
    assert out is not None  # video keeps flowing while the mic is quiet


def test_skip_audio_legacy_flag_uses_cached_palette():
    first = process_frame(_frame(), _tone())
    assert first is not None
    second = process_frame(_frame(), skip_audio=True)
    assert second is not None  # previously this raised ValueError


def test_instances_have_isolated_state():
    a = AudioPaletteQuantizer()
    b = AudioPaletteQuantizer()
    assert a.process(_frame(), _tone()) is not None
    assert b.process(_frame(), None) is None  # b never saw audio


def test_sample_rate_is_sanitized():
    quantizer = AudioPaletteQuantizer(sample_rate="garbage")
    assert quantizer.sample_rate == 44100
    quantizer.set_sample_rate(0)
    assert quantizer.sample_rate == 44100
    quantizer.set_sample_rate(48000)
    assert quantizer.sample_rate == 48000


def test_invalid_audio_chunk_is_ignored_not_fatal():
    quantizer = AudioPaletteQuantizer(sample_rate=SR)
    assert quantizer.process(_frame(), float("nan")) is None
    assert quantizer.process(_frame(), np.array([np.inf, 0.0], dtype=np.float32)) is None
