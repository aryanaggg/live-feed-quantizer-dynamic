import numpy as np
from PIL import Image

from image_quantizer.quantizer import quantize_array, quantize_image

RED = (255, 0, 0)
GREEN = (0, 255, 0)


def test_quantize_array_maps_to_nearest_color():
    img = np.zeros((2, 2, 3), dtype=np.uint8)
    img[0, 0] = (250, 10, 10)    # nearest to red
    img[1, 1] = (10, 240, 10)    # nearest to green
    out = quantize_array(img, [RED, GREEN])
    assert out[0, 0].tolist() == [255, 0, 0]
    assert out[1, 1].tolist() == [0, 255, 0]


def test_quantize_array_output_is_always_in_palette():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
    palette = [(255, 141, 86), (255, 191, 203), (251, 232, 131), (6, 149, 148)]
    out = quantize_array(img, palette)
    palette_set = set(map(tuple, np.asarray(palette).tolist()))
    pixels = set(map(tuple, out.reshape(-1, 3).tolist()))
    assert pixels <= palette_set


def test_quantize_image_accepts_pil_image():
    img = Image.new("RGB", (4, 4), color=(200, 30, 30))
    out = quantize_image(img, [RED])
    assert np.asarray(out)[0, 0].tolist() == [255, 0, 0]
    # source image must not be modified
    assert np.asarray(img)[0, 0].tolist() == [200, 30, 30]


def test_quantize_image_accepts_path(tmp_path):
    path = str(tmp_path / "img.png")
    Image.new("RGB", (4, 4), color=(0, 200, 0)).save(path)
    out = quantize_image(path, [GREEN])
    assert np.asarray(out)[0, 0].tolist() == [0, 255, 0]
