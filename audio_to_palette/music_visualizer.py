import numpy as np
import librosa
import colorsys

def extract_colors_from_audio(audio_path=None, audio_array=None, sr=None, chunk_sec=1, N=5, gain=1.5):
    """
    Extract dynamic colors from an audio file.

    Parameters:
        audio_path (str): Path to audio file
        chunk_sec (float): Chunk length in seconds
        N (int): Number of frequency bands / colors
        gain (float): Brightness multiplier for RGB values

    Returns:
        List of lists: colors per chunk [[R,G,B], [R,G,B], ...]
    """

    if audio_array is not None:
        y = audio_array
        if sr is None:
            raise ValueError("sr must be provided when passing audio_array")
        # use the whole array as a single chunk
        chunk_size = len(y)
        num_chunks = 1
    else:
        y, sr = librosa.load(audio_path, sr=None)
        chunk_size = int(chunk_sec * sr)
        num_chunks = int(np.ceil(len(y) / chunk_size))

    all_colors = []

    # precompute hues
    hues = np.linspace(0, 360, N, endpoint=False)

    for i in range(num_chunks):

        start_sample = i * chunk_size
        end_sample = min((i + 1) * chunk_size, len(y))

        yc = y[start_sample:end_sample]

        # FFT and magnitude
        fft_result = np.fft.rfft(yc)
        magnitude = np.abs(fft_result)

        # frequency bins
        L = len(yc)
        freqs = np.fft.rfftfreq(L, d=1/sr)

        # logarithmic frequency edges
        f_min = 20
        f_max = sr / 2
        edges = f_min * (f_max/f_min) ** (np.arange(N+1)/N)

        # vectorized band energy computation
        band_energies = np.array([
            np.sum(magnitude[(freqs >= edges[j]) & (freqs < edges[j+1])])
            for j in range(N)
        ], dtype=np.float32)

        # # L1 normalization
        # l1_norm_energies = band_energies / np.sum(band_energies)
        
        # L1 normalization
        sum_energy = np.sum(band_energies)
        if sum_energy == 0:
            # If chunk is silent, assign equal energy to all bands
            l1_norm_energies = np.ones_like(band_energies) / len(band_energies)
        else:
            l1_norm_energies = band_energies / sum_energy


        # assign colors using HSV -> RGB
        # vectorized: compute r,g,b in one go
        rgb_colors = [colorsys.hsv_to_rgb(h/360, 1.0, min(l1_norm_energies[idx]*gain, 1.0))
                      for idx, h in enumerate(hues)]

        # scale to 0-255
        colors_list = [tuple(int(c*255) for c in color) for color in rgb_colors]

        all_colors.append(colors_list)

    return all_colors
