# main.py
import cv2
import numpy as np
from PIL import Image

from capture_camera import start_camera
from capture_audio import AudioProcessor
from audio_to_palette.music_visualizer import extract_colors_from_audio
from image_quantizer.quantizer import quantize_image

AUDIO_SR = 44100
PALETTE_SIZE = 7
GAIN_SCALE = 15

audio = AudioProcessor()

prev_palette = None
blend_alpha = 0.8
paused = False


def process_frame(frame):
    global prev_palette, paused

    # --- Toggle PAUSE on SPACE ---
    key = cv2.waitKey(1) & 0xFF
    if key == ord(' '):
        paused = not paused
        if paused:
            print("[AUDIO] Paused")
        else:
            print("[AUDIO] Resumed")

    # If paused, return raw frame
    if paused:
        return frame

    # --- Get latest audio ---
    audio.update()
    audio.play_latest()
    chunk = audio.get_latest_audio()

    # If nothing available
    if chunk is None or len(chunk) == 0:
        return frame
    
    # adding responsive gain
    rms = np.sqrt(np.mean(chunk*chunk))
    gain = rms * GAIN_SCALE

    # --- Extract palette from audio ---
    new_palette = extract_colors_from_audio(
        audio_array=chunk,
        sr=AUDIO_SR,
        N=PALETTE_SIZE,
        gain=gain
    )[0]

    # --- Smooth blend ---
    if prev_palette is not None:
        blended_palette = (
            np.array(prev_palette) * (1 - blend_alpha)
            + np.array(new_palette) * blend_alpha
        ).astype(int).tolist()
    else:
        blended_palette = new_palette

    prev_palette = blended_palette

    # --- Convert frame to PIL (BGR → RGB) ---
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    # --- Quantize image ---
    quantized_pil = quantize_image(pil_img, blended_palette)

    # --- Convert back to OpenCV ---
    final_frame = cv2.cvtColor(np.array(quantized_pil), cv2.COLOR_RGB2BGR)

    return final_frame


if __name__ == "__main__":
    print("[MAIN] Starting camera + live audio analysis...")
    start_camera(process_frame)
    audio.close()
