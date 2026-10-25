# image_quantizer/quantizer.py
"""Color-quantize images to a custom palette.

Uses a 3D color lookup table (default 6 bits/channel => 64^3 = 262k entries)
built from the palette. Mapping a frame becomes a single indexed lookup
instead of one full pass over the pixels per palette color, which matters for
real-time video. LUT entries are exact nearest-color matches; bucket centers
are at most 2/255 per channel away from any real pixel value, which is
invisible for a posterization effect.

Also provides exact nearest-color code used to build the LUT and kept
available for tests / offline use.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# Process pixels in chunks to bound peak memory for large inputs.
_PIXEL_CHUNK = 1 << 18

_LUT_BITS = 6
_LUT_N = 1 << _LUT_BITS                    # buckets per channel (64)
_LUT_SHIFT = 8 - _LUT_BITS                 # pixel >> shift = bucket index
_LUT_SHAPE = (_LUT_N, _LUT_N, _LUT_N)

# Single cached LUT + palette. Keyed by the exact palette bytes so a stable
# palette (silence, paused, quiet mic) costs nothing, and rebuilds (~2 ms)
# happen only when the blended palette actually changes.
_cache = {"key": None, "lut": None, "pal": None}


def _nearest_indices(pixels: np.ndarray, palette_array: np.ndarray) -> np.ndarray:
    """Exact: index of the nearest palette color for each pixel row."""
    count = pixels.shape[0]
    pal = np.asarray(palette_array, dtype=np.float32)
    pal_norm2 = np.einsum("ij,ij->i", pal, pal)          # (P,)

    # Bound the temporary (chunk, P, 3) work to ~4 M floats per pass.
    chunk = max(1, (1 << 22) // max(3 * pal.shape[0], 1))
    best_idx = np.empty(count, dtype=np.int32)

    for start in range(0, count, chunk):
        end = min(start + chunk, count)
        c = pixels[start:end].astype(np.float32)
        # |c - p|^2 = |c|^2 - 2 c.p + |p|^2, computed per chunk in one BLAS
        # pass over all palette colors at once (much faster than a 7-pass
        # loop over temp arrays).
        d = c @ pal.T
        d *= np.float32(-2.0)
        d += pal_norm2
        d += np.einsum("ij,ij->i", c, c)[:, None]
        best_idx[start:end] = np.argmin(d, axis=1)

    return best_idx


def _prepare_palette(palette):
    pal = np.asarray(palette, dtype=np.float32).reshape(-1, 3)
    if pal.shape[0] == 0:
        raise ValueError("palette must contain at least one color")
    pal_u8 = np.clip(pal, 0, 255).astype(np.uint8)
    return pal, pal_u8


def _build_lut(pal: np.ndarray) -> np.ndarray:
    half = 1 << (_LUT_SHIFT - 1)
    base = np.arange(_LUT_N, dtype=np.int32) << _LUT_SHIFT
    centers = (base + half).astype(np.float32)
    g = np.meshgrid(centers, centers, centers, indexing="ij")
    cube = np.stack(g, axis=-1).astype(np.float32).reshape(-1, 3)
    return _nearest_indices(cube, pal).astype(np.uint8).reshape(_LUT_SHAPE)


def _get_lut(pal: np.ndarray, pal_u8: np.ndarray):
    key = pal_u8.tobytes()
    cached = _cache
    if cached["key"] == key:
        return cached["lut"], cached["pal"]
    lut = _build_lut(pal)
    cached["key"] = key
    cached["lut"] = lut
    cached["pal"] = pal_u8
    return lut, pal_u8


def _quantize_with_lut(arr: np.ndarray, pal: np.ndarray, pal_u8: np.ndarray) -> np.ndarray:
    lut, pal_out = _get_lut(pal, pal_u8)
    buckets = arr >> _LUT_SHIFT
    idx = lut[buckets[..., 0], buckets[..., 1], buckets[..., 2]]
    return pal_out[idx].astype(np.uint8, copy=False).reshape(arr.shape)


def quantize_array(image_rgb, palette):
    """Quantize an (H, W, 3) uint8 RGB array to the given palette.

    Returns a new (H, W, 3) uint8 array where every pixel is a palette color.
    """
    arr = np.asarray(image_rgb)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) RGB array, got shape {arr.shape}")
    pal, pal_u8 = _prepare_palette(palette)
    return _quantize_with_lut(np.ascontiguousarray(arr), pal, pal_u8)


def quantize_bgr(image_bgr, palette_rgb):
    """Quantize an (H, W, 3) uint8 BGR frame directly (OpenCV convention).

    Channel-nearest is invariant under permuting both the pixel channels and
    the palette channels, so we simply flip the palette and skip the two
    cvtColor round-trips the caller used to pay for.
    """
    arr = np.asarray(image_bgr)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected an (H, W, 3) BGR array, got shape {arr.shape}")
    pal, pal_u8 = _prepare_palette(palette_rgb)
    pal_bgr, pal_bgr_u8 = np.ascontiguousarray(pal[:, ::-1]), np.ascontiguousarray(pal_u8[:, ::-1])
    return _quantize_with_lut(np.ascontiguousarray(arr), pal_bgr, pal_bgr_u8)


def quantize_image(image_input, palette):
    """Quantize an image using a custom palette.

    Parameters:
        image_input (str or PIL.Image.Image): Path to image or a PIL Image object
        palette (list of tuples): List of RGB tuples to quantize the image to

    Returns:
        PIL.Image.Image: Quantized image
    """
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
