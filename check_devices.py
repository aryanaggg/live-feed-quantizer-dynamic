import pyaudio

p = pyaudio.PyAudio()

print("=== AUDIO DEVICES ===")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    print(i, info["name"], 
          "| Input:", info["maxInputChannels"], 
          "| Output:", info["maxOutputChannels"])
