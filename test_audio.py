import sounddevice as sd
import time

def callback(indata, frames, time_info, status):
    print("Chunk received:", indata.shape)

stream = sd.InputStream(
    samplerate=44100,
    channels=1,
    blocksize=2048,
    callback=callback
)

print("Starting stream...")
stream.start()

for i in range(50):
    time.sleep(0.1)
