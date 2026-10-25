# image_quantizer/quantizer.py
"""Color-quantize images to a custom palette.

Fast nearest-palette-color mapping implemented with incremental minimum
distance instead of one big (pixels x palette) distance matrix, which keeps
memory and CPU usage stable for real-time video frames.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# Process pixels in chunks to bound peak memory for large frames.
_PIXEL_CHUNK = 1 << 18


def _nearest_indices(pixels: np.ndarray, palette_array: np.ndarray) -> np.ndarray:
    """Return the index of the nearest palette color for each pixel row.

    Runs one pass per palette color (palettes are small, ~7 entries) and keeps
    a running best distance, avoiding the (num_pixels, num_colors, 3)
    intermediate tensor the previous implementation allocated per frame.
    """
    count = pixels.shape[0]
    best_idx = np.zeros(count, dtype=np.int32)
    best_dist = np.full(count, np.inf, dtype=np.float32)

    for i in range(palette_array.shape[0]):
        color = palette_array[i]
        for start in range(0, count, _PIXEL_CHUNK):
            end = min(start + _PIXEL_CHUNK, count)
            diff = pixels[start:end] - color
            dist = np.einsum("ij,ij->i", diff, diff)
            mask = dist < best_dist[start:end]
            if mask.any():
                best_dist[start:end][mask] = dist[mask]
                best_idx[start:end][mask] = i

    return best_idx


def quantize_array(image_rgb, palette):
    """Quantize an (H, W, 3) uint8 RGB array to the given palette.

    Parameters:
        image_rgb: numpy array, shape (H, W, 3), RGB uint8
        palette: iterable of RGB triples (ints 0-255)

    Returns:
        New (H, W, 3) uint8 numpy array where every pixel is a palette color.
    """
    arr = np.asarray(image_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) RGB array, got shape {arr.shape}")

    palette_array = np.asarray(palette, dtype=np.float32).reshape(-1, 3)
    if palette_array.shape[0] == 0:
        raise ValueError("palette must contain at least one color")

    height, width, _ = arr.shape
    pixels = arr.reshape(-1, 3).astype(np.float32)
    best_idx = _nearest_indices(pixels, palette_array)
    return palette_array[best_idx].reshape(height, width, 3).astype(np.uint8)


def quantize_image(image_input, palette):
    """Quantize an image using a custom palette.

    Parameters:
        image_input (str or PIL.Image.Image): Path to image or a PIL Image object
        palette (list of tuples): List of RGB tuples to quantize the image to

    Returns:
        PIL.Image.Image: Quantized image
    """
    # Check if input is a path or already a PIL Image
    if isinstance(image_input, str):
        img = Image.open(image_input).convert("RGB")
    elif isinstance(image_input, Image.Image):
        img = image_input
    else:
        raise TypeError("image_input must be a file path or PIL.Image.Image")

    # np.asarray copies pixel data, so the source image is never modified.
    img_array = np.asarray(img.convert("RGB"))
    quantized_array = quantize_array(img_array, palette)
    return Image.fromarray(quantized_array)

# End of module
