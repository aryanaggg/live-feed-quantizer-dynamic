# main.py
import cv2
import numpy as np
import librosa
from PIL import Image

from capture_audio import start_audio, latest_audio_chunk
from capture_camera import start_camera
from audio_to_palette.music_visualizer import extract_colors_from_audio
from image_quantizer.quantizer import quantize_image
import pygame

from capture_camera import start_camera   # <-- importing our module

# Path to audio
audio_path = "F:\\code\\projects\\live-feed-quantizer-wd-dynamic-colors\\audio_to_palette\\palpal.mp3"

# Initialize audio
pygame.mixer.init()
pygame.mixer.music.load(audio_path)
pygame.mixer.music.play()

# Load audio for chunking
y, sr = librosa.load(audio_path, sr=None)


chunk_sec = 0.05
chunk_size = int(chunk_sec * sr)

prev_palette = None
blend_alpha = 1.0
paused = False

def process_frame(frame):
    """
    This function receives a frame from camera_feed.py,
    quantizes it based on audio palette, and returns the processed frame.
    """

    global prev_palette, paused

    # Handle pause/unpause
    key = cv2.waitKey(1) & 0xFF
    if key == ord(' '):        # space toggles pause
        if not paused:
            pygame.mixer.music.pause()
            paused = True
        else:
            pygame.mixer.music.unpause()
            paused = False

    # If paused -> return raw frame
    if paused:
        return frame

    # Song ended → return raw frame
    if not pygame.mixer.music.get_busy():
        return frame

    # Get current audio position
    current_ms = pygame.mixer.music.get_pos()
    if current_ms < 0:
        current_ms = 0

    current_sample = int((current_ms / 1000) * sr)
    end_sample = min(current_sample + chunk_size, len(y))

    audio_chunk = y[current_sample:end_sample]

    if len(audio_chunk) == 0:
        return frame

    # Extract colors from this audio slice
    palette_list = extract_colors_from_audio(
        audio_array=audio_chunk,
        sr=sr,
        N=10,
        gain=3
    )

    new_palette = palette_list[0]

    # Blend with previous palette if exists
    if prev_palette is not None:
        blended_palette = (
            np.array(prev_palette) * (1 - blend_alpha)
            + np.array(new_palette) * blend_alpha
        ).astype(int).tolist()
    else:
        blended_palette = new_palette

    prev_palette = blended_palette

    # Convert frame (BGR → RGB for PIL)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    # Quantize
    quantized_pil = quantize_image(pil_img, blended_palette)

    # Convert back (RGB → BGR)
    final_cv = cv2.cvtColor(np.array(quantized_pil), cv2.COLOR_RGB2BGR)

    return final_cv


if __name__ == "__main__":
    start_camera(process_frame)
