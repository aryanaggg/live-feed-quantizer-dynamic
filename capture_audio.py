import pyaudio
import numpy as np

class AudioProcessor:
    def __init__(self, sr=44100, packet_size=0.05):
        self.sr = sr
        self.chunk = int(sr * (packet_size))
        self.latest_audio = np.zeros(self.chunk, dtype=np.float32)

        self.audio = pyaudio.PyAudio()

        # -------- INPUT (MIC) --------
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

        # -------- OUTPUT (SPEAKER) --------
        DEVICE_ID = 0
        info = self.audio.get_device_info_by_index(DEVICE_ID)
        allowed_channels = int(info["maxOutputChannels"])

        try:
            self.play_stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=allowed_channels,
                rate=sr,
                output=True,
                output_device_index=DEVICE_ID,
                frames_per_buffer=self.chunk
            )
        except Exception as e:
            print("[ERROR] Failed to open speaker:", e)
            self.play_stream = None

    def update(self):
        """Read one packet."""
        if self.stream is None:
            return

        try:
            data = self.stream.read(self.chunk, exception_on_overflow=False)
            self.latest_audio = np.frombuffer(data, dtype=np.float32)
        except Exception as e:
            print("[ERROR] mic read failed:", e)

    def play_latest(self):
        """Play the last captured audio chunk back to the speakers."""
        if self.play_stream is None:
            return
        
        try:
            self.play_stream.write(self.latest_audio.tobytes())
        except Exception as e:
            print("[ERROR] playback failed:", e)

    def get_latest_audio(self):
        return self.latest_audio.copy()

    def close(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.play_stream:
            self.play_stream.stop_stream()
            self.play_stream.close()
        self.audio.terminate()
