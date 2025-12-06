# main.py
import cv2
import numpy as np
from PIL import Image

from audio_to_palette.music_visualizer import extract_colors_from_audio
from image_quantizer.quantizer import quantize_image

# Only import local audio for standalone mode
try:
    from capture_camera import start_camera
    from capture_audio import AudioProcessor
    audio = AudioProcessor()
except Exception as e:
    print(f"[WARNING] Local audio capture unavailable: {e}")
    audio = None

AUDIO_SR = 44100
PALETTE_SIZE = 7
GAIN_SCALE = 15

prev_palette = None
blend_alpha = 0.8
paused = False


def process_frame(frame, audio_chunk=None, skip_audio=False):
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
    # If an audio chunk is provided (from browser), use it. Otherwise use server-side AudioProcessor.
    if skip_audio or audio_chunk is None:
        chunk = audio_chunk
    else:
        chunk = audio_chunk

    # If nothing available
    if chunk is None or len(chunk) == 0:
        return frame
    
    # adding responsive gain
    rms = np.sqrt(np.mean(chunk*chunk))
    gain = rms * GAIN_SCALE

    # --- Extract palette from audio ---
    try:
        new_palette = extract_colors_from_audio(
            audio_array=chunk,
            sr=AUDIO_SR,
            N=PALETTE_SIZE,
            gain=gain
        )[0]
    except Exception as e:
        print(f"[ERROR] extract_colors_from_audio failed: {e}")
        return frame

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
    try:
        quantized_pil = quantize_image(pil_img, blended_palette)
    except Exception as e:
        print(f"[ERROR] quantize_image failed: {e}")
        return frame

    # --- Convert back to OpenCV ---
    try:
        final_frame = cv2.cvtColor(np.array(quantized_pil), cv2.COLOR_RGB2BGR)
    except Exception as e:
        print(f"[ERROR] cv2.cvtColor failed: {e}")
        return frame

    return final_frame


if __name__ == "__main__":
    print("[MAIN] Starting camera + live audio analysis...")
    start_camera(process_frame)
    audio.close()
