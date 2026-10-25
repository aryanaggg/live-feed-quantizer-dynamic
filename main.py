# main.py
"""Local window mode: webcam -> mic-driven palette quantizer -> fullscreen.

The web server (server.py) shares the same AudioPaletteQuantizer pipeline,
so both entry points behave identically.
"""
import cv2

from capture_audio import start_audio, latest_audio_chunk, stop_audio
from capture_camera import start_camera
from sound_device_processor import AudioPaletteQuantizer

AUDIO_SR = 44100
CHUNK_SEC = 0.05

quantizer = AudioPaletteQuantizer(sample_rate=AUDIO_SR)
paused = False


def process_frame(frame):
    """Process each camera frame using the current microphone audio palette."""
    global paused

    key = cv2.waitKey(1) & 0xFF
    if key == ord(' '):
        paused = not paused
        print("[AUDIO] Paused" if paused else "[AUDIO] Resumed")

    if paused:
        return frame

    processed = quantizer.process(frame, latest_audio_chunk())
    return processed if processed is not None else frame


if __name__ == "__main__":
    print("[MAIN] Starting audio capture from microphone...")
    start_audio(sr=AUDIO_SR, packet_size=CHUNK_SEC)
    try:
        start_camera(process_frame)
    finally:
        stop_audio()
