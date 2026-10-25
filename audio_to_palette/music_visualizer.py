"""Map audio to color palettes.

Frequency bands map to hues spread around the color wheel; band energy sets
the brightness/value of each hue so percussive, bass-heavy, or treble-heavy
audio produces distinctly different palettes.
"""

from __future__ import annotations

import colorsys

import numpy as np


def extract_colors_from_audio(audio_path=None, audio_array=None, sr=None,
                              chunk_sec=1.0, N=5, gain=1.5):
    """
    Extract dynamic colors from an audio file or raw audio chunk.

    Parameters:
        audio_path (str): Path to audio file (decoded with librosa, imported
            lazily so it doesn't slow down the real-time path)
        audio_array (np.ndarray): Raw PCM mono audio (use instead of file)
        sr (float): Sample rate; required when passing audio_array
        chunk_sec (float): Chunk length in seconds (file mode only)
        N (int): Number of frequency bands / colors
        gain (float): Brightness multiplier for RGB values

    Returns:
        List of lists: colors per chunk [[R,G,B], [R,G,B], ...]
    """
    N = max(1, int(N))
    gain = max(float(gain), 0.0)

    if audio_array is not None:
        if sr is None:
            raise ValueError("sr must be provided when passing audio_array")
        y = np.asarray(audio_array, dtype=np.float32).ravel()
        sr = float(sr)
        chunk_size = len(y)
        if chunk_size == 0:
            return []
        num_chunks = 1
    else:
        if not audio_path:
            raise ValueError("provide either audio_path or (audio_array, sr)")
        import librosa  # lazy: only needed for offline file mode
        y, sr = librosa.load(audio_path, sr=None)
        chunk_size = max(1, int(chunk_sec * sr))
        num_chunks = int(np.ceil(len(y) / chunk_size)) if len(y) else 0

    # precompute hues: one hue per frequency band
    hues = np.linspace(0, 360, N, endpoint=False)
    f_min = 20.0
    f_max = max(sr / 2.0, f_min + 1.0)
    # logarithmic frequency edges
    edges = f_min * (f_max / f_min) ** (np.arange(N + 1) / N)

    all_colors = []

    for i in range(num_chunks):
        start_sample = i * chunk_size
        end_sample = min((i + 1) * chunk_size, len(y))
        yc = y[start_sample:end_sample]

        L = len(yc)
        if L == 0:
            continue

        # FFT magnitude
        magnitude = np.abs(np.fft.rfft(yc))
        freqs = np.fft.rfftfreq(L, d=1.0 / sr)

        # vectorized band edges -> bin index ranges (freqs in [edges[j], edges[j+1]))
        starts = np.searchsorted(freqs, edges[:-1], side="left")
        ends = np.searchsorted(freqs, edges[1:], side="left")
        band_energies = np.array(
            [magnitude[s:e].sum() for s, e in zip(starts, ends)],
            dtype=np.float32,
        )

        # L1 normalization; silence falls back to equal energy across bands
        sum_energy = float(band_energies.sum())
        if sum_energy > 0:
            l1_norm_energies = band_energies / sum_energy
        else:
            l1_norm_energies = np.full(N, 1.0 / N, dtype=np.float32)

        # hue per band, energy sets brightness
        colors_list = []
        for idx, hue in enumerate(hues):
            value = min(float(l1_norm_energies[idx]) * gain, 1.0)
            r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 1.0, value)
            colors_list.append((int(r * 255), int(g * 255), int(b * 255)))

        all_colors.append(colors_list)

    return all_colors
