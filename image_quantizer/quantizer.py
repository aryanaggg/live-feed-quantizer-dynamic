# F:\code\projects\image_quantizer\quantizer.py

from PIL import Image
import numpy as np

# defining custom palette
# good
# palette = [(255, 141, 86), (255, 191, 203), (251, 232, 131), (6, 149, 148)]
# bad
# palette = [(255, 0, 0), (0, 253, 170), (0, 133, 255), (139, 0, 201), (210, 0, 64)]
# chatgpt
# palette = [
#     (255, 75, 110),   # hot strawberry pink
#     (255, 214, 0),    # vibrant yellow
#     (135, 0, 255),    # electric purple
#     (0, 209, 178),    # aqua mint
#     (28, 28, 28)      # deep charcoal (anchors shadows)
# ]
# cg 2
# palette = [
#     (255, 105, 180),  # bubblegum hot pink
#     (255, 230, 109),  # warm creamy yellow
#     (112, 190, 255),  # sky baby blue
#     (162, 119, 255),  # soft neon violet
#     (58, 58, 62)      # soft charcoal (not too dark)
# ]

def quantize_image(image_input, palette):
    """
    Quantize an image using a custom palette.
    
    Parameters:
        image_input (str or PIL.Image.Image): Path to image or a PIL Image object
        palette (list of tuples): List of RGB tuples to quantize the image to

    Returns:
        PIL.Image.Image: Quantized image
    """

    # Check if input is a path or already a PIL Image
    if isinstance(image_input, str):
        img = Image.open(image_input).convert('RGB')
    else:
        img = image_input.copy()  # Copy to avoid modifying original

    # Convert image and palette to NumPy arrays for fast computation
    img_array = np.array(img)
    pixels = img_array.reshape(-1, 3).astype(np.int16)
    palette_array = np.array(palette).astype(np.int16)

    # Compute squared Euclidean distance between each pixel and each palette color
    # Shape: (num_pixels, num_palette_colors)
    dists = np.sum((pixels[:, None, :] - palette_array[None, :, :]) ** 2, axis=2)

    # Find index of closest palette color for each pixel
    closest_idx = np.argmin(dists, axis=1)

    # Map pixels to closest palette color
    new_pixels = palette_array[closest_idx]

    # Reshape back to original image shape
    img_n_array = new_pixels.reshape(img_array.shape).astype(np.uint8)

    # Convert back to PIL Image
    img_n = Image.fromarray(img_n_array)
    return img_n

# End of module
