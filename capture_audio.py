import threading
import time

import numpy as np
import pyaudio


class AudioProcessor:
    def __init__(self, sr=44100, packet_size=0.05):
        self.sr = sr
        self.chunk = int(sr * packet_size)
        self.latest_audio = np.zeros(self.chunk, dtype=np.float32)

        self.audio = pyaudio.PyAudio()

        try:
            self.stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=sr,
                input=True,
                frames_per_buffer=self.chunk
            )
        except Exception as e:
            print("[ERROR] Failed to open mic:", e)
            self.stream = None

        self.play_stream = None
        try:
            info = self.audio.get_default_output_device_info()
            allowed_channels = int(info.get("maxOutputChannels", 1))
            self.play_stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=allowed_channels,
                rate=sr,
                output=True,
                frames_per_buffer=self.chunk
            )
        except Exception:
            self.play_stream = None

    def update(self):
        """Read one packet from the microphone."""
        if self.stream is None:
            return

        try:
            data = self.stream.read(self.chunk, exception_on_overflow=False)
            self.latest_audio = np.frombuffer(data, dtype=np.float32)
        except Exception as e:
            print("[ERROR] mic read failed:", e)

    def get_latest_audio(self):
        return self.latest_audio.copy()

    def close(self):
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        if self.play_stream:
            try:
                self.play_stream.stop_stream()
                self.play_stream.close()
            except Exception:
                pass
        try:
            self.audio.terminate()
        except Exception:
            pass


_audio_processor = None
_audio_thread = None
_stop_event = None


def _audio_loop():
    while not _stop_event.is_set():
        if _audio_processor is None or _audio_processor.stream is None:
            time.sleep(0.1)
            continue
        _audio_processor.update()


def start_audio(sr=44100, packet_size=0.05):
    global _audio_processor, _audio_thread, _stop_event
    if _audio_processor is None:
        _audio_processor = AudioProcessor(sr=sr, packet_size=packet_size)

    if _stop_event is None:
        _stop_event = threading.Event()

    if _audio_thread is None or not _audio_thread.is_alive():
        _audio_thread = threading.Thread(target=_audio_loop, daemon=True)
        _audio_thread.start()

    return _audio_processor


def latest_audio_chunk():
    if _audio_processor is None:
        return np.zeros(0, dtype=np.float32)
    return _audio_processor.get_latest_audio()


def stop_audio():
    global _stop_event, _audio_thread, _audio_processor
    if _stop_event is not None:
        _stop_event.set()
    if _audio_thread is not None:
        _audio_thread.join(timeout=1.0)
    if _audio_processor is not None:
        _audio_processor.close()
    _audio_processor = None
    _audio_thread = None
    _stop_event = None
