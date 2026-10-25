import numpy as np
import pytest

from audio_to_palette.music_visualizer import extract_colors_from_audio

SR = 8000


def _tone(seconds=1.0, freq=200, amp=0.5):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_requires_sample_rate_with_array():
    with pytest.raises(ValueError, match="sr must be provided"):
        extract_colors_from_audio(audio_array=np.zeros(10, dtype=np.float32))


def test_returns_n_colors():
    colors = extract_colors_from_audio(audio_array=_tone(), sr=SR, N=5)
    assert len(colors) == 1
    assert len(colors[0]) == 5
    for color in colors[0]:
        assert len(color) == 3
        assert all(0 <= c <= 255 for c in color)


def test_all_rgb_colors_are_ints():
    colors = extract_colors_from_audio(audio_array=_tone(freq=80), sr=SR, N=7)[0]
    assert all(isinstance(c, int) for color in colors for c in color)


def test_silent_audio_still_returns_equal_energy_palette():
    colors = extract_colors_from_audio(audio_array=np.zeros(1000, dtype=np.float32), sr=SR, N=4)
    assert len(colors[0]) == 4  # silence falls back to equal-energy palette


def test_empty_audio_returns_no_chunks():
    assert extract_colors_from_audio(audio_array=np.array([], dtype=np.float32), sr=SR) == []


def test_energy_consistency_tone_vs_silence_headers():
    # A dominant low-frequency tone should put its energy in the first band,
    # so the first color should be brighter than the silent fallback's first color.
    tone = extract_colors_from_audio(audio_array=_tone(freq=40, amp=0.9), sr=SR, N=4)[0]
    silence = extract_colors_from_audio(audio_array=np.zeros(SR, dtype=np.float32), sr=SR, N=4, gain=0.3)[0]
    assert sum(tone[0]) >= sum(silence[0])
