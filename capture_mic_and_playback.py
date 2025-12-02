import sys
import traceback
import sounddevice as sd
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox

#!/usr/bin/env python3
"""
capture_mic_and_playback.py

Basic microphone capture + playback monitor with a simple Tkinter GUI to select
input/output devices and start/stop low-latency monitoring.

Dependencies:
    pip install sounddevice
(uses tkinter from stdlib)

Usage:
    Run this file. Select input and output device, adjust volume, click Start.
"""


# ---------- Utilities ----------

def list_audio_devices():
        """Return list of (index, name, max_input_channels, max_output_channels, default_samplerate)."""
        devices = []
        for i, dev in enumerate(sd.query_devices()):
                devices.append(
                        (i, dev["name"], dev.get("max_input_channels", 0), dev.get("max_output_channels", 0), dev.get("default_samplerate", 44100))
                )
        return devices

def format_device_entry(dev_tuple):
        i, name, in_ch, out_ch, sr = dev_tuple
        role = []
        if in_ch > 0:
                role.append(f"in:{in_ch}")
        if out_ch > 0:
                role.append(f"out:{out_ch}")
        role_str = ",".join(role) if role else "no IO"
        return f"{i} - {name} ({role_str})"

def adjust_channels(indata, out_channels):
        """Adjust number of channels: replicate or trim as needed."""
        in_ch = indata.shape[1]
        if in_ch == out_channels:
                return indata
        if in_ch < out_channels:
                # replicate channels
                reps = int(np.ceil(out_channels / in_ch))
                stacked = np.tile(indata, (1, reps))
                return stacked[:, :out_channels]
        else:
                # trim extra channels
                return indata[:, :out_channels]

# ---------- GUI + Streaming ----------

class MonitorApp:
        def __init__(self, root):
                self.root = root
                root.title("Mic Monitor - Input -> Earphones")
                self.devices = list_audio_devices()

                # Widgets
                frm = ttk.Frame(root, padding=10)
                frm.grid(row=0, column=0, sticky="nsew")

                ttk.Label(frm, text="Input device:").grid(row=0, column=0, sticky="w")
                self.in_combo = ttk.Combobox(frm, state="readonly", width=60)
                self.in_combo['values'] = [format_device_entry(d) for d in self.devices if d[2] > 0]
                if self.in_combo['values']:
                        self.in_combo.current(0)
                self.in_combo.grid(row=0, column=1, sticky="ew")

                ttk.Label(frm, text="Output device:").grid(row=1, column=0, sticky="w")
                self.out_combo = ttk.Combobox(frm, state="readonly", width=60)
                self.out_combo['values'] = [format_device_entry(d) for d in self.devices if d[3] > 0]
                if self.out_combo['values']:
                        self.out_combo.current(0)
                self.out_combo.grid(row=1, column=1, sticky="ew")

                ttk.Label(frm, text="Sample rate (Hz):").grid(row=2, column=0, sticky="w")
                self.sr_var = tk.StringVar()
                default_sr = int(self.devices[self._first_input_index()][4]) if self.devices else 44100
                self.sr_var.set(str(default_sr))
                self.sr_entry = ttk.Entry(frm, textvariable=self.sr_var, width=20)
                self.sr_entry.grid(row=2, column=1, sticky="w")

                ttk.Label(frm, text="Volume:").grid(row=3, column=0, sticky="w")
                self.volume = tk.DoubleVar(value=1.0)
                self.vol_slider = ttk.Scale(frm, from_=0.0, to=2.0, orient="horizontal", variable=self.volume)
                self.vol_slider.grid(row=3, column=1, sticky="ew")

                # Buttons
                btn_frm = ttk.Frame(frm)
                btn_frm.grid(row=4, column=0, columnspan=2, pady=(8,0))
                self.start_btn = ttk.Button(btn_frm, text="Start", command=self.start_stream)
                self.start_btn.grid(row=0, column=0, padx=5)
                self.stop_btn = ttk.Button(btn_frm, text="Stop", command=self.stop_stream, state="disabled")
                self.stop_btn.grid(row=0, column=1, padx=5)
                self.refresh_btn = ttk.Button(btn_frm, text="Refresh Devices", command=self.refresh_devices)
                self.refresh_btn.grid(row=0, column=2, padx=5)

                self.status_var = tk.StringVar(value="Idle")
                self.status_label = ttk.Label(frm, textvariable=self.status_var, foreground="blue")
                self.status_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6,0))

                # Stream handle
                self.stream = None

                # Clean close
                root.protocol("WM_DELETE_WINDOW", self.on_close)

        def _first_input_index(self):
                for idx, d in enumerate(self.devices):
                        if d[2] > 0:
                                return idx
                return 0

        def refresh_devices(self):
                try:
                        self.devices = list_audio_devices()
                        in_vals = [format_device_entry(d) for d in self.devices if d[2] > 0]
                        out_vals = [format_device_entry(d) for d in self.devices if d[3] > 0]
                        self.in_combo['values'] = in_vals
                        self.out_combo['values'] = out_vals
                        if in_vals:
                                self.in_combo.current(0)
                        if out_vals:
                                self.out_combo.current(0)
                        self.status_var.set("Device list refreshed")
                except Exception as e:
                        self.status_var.set(f"Error refreshing: {e}")

        def parse_selection_index(self, sel):
                if not sel:
                        return None
                try:
                        idx = int(sel.split(" - ", 1)[0])
                        return idx
                except Exception:
                        return None

        def start_stream(self):
                if self.stream:
                        self.status_var.set("Already running")
                        return
                in_sel = self.in_combo.get()
                out_sel = self.out_combo.get()
                if not in_sel or not out_sel:
                        messagebox.showerror("Selection error", "Please select both input and output devices.")
                        return
                in_idx = self.parse_selection_index(in_sel)
                out_idx = self.parse_selection_index(out_sel)
                if in_idx is None or out_idx is None:
                        messagebox.showerror("Selection error", "Could not parse device selections.")
                        return

                try:
                        sr = int(float(self.sr_var.get()))
                        if sr <= 0:
                                raise ValueError()
                except Exception:
                        messagebox.showerror("Sample rate error", "Please enter a valid sample rate (positive integer).")
                        return

                try:
                        in_info = sd.query_devices(in_idx)
                        out_info = sd.query_devices(out_idx)
                        in_ch = in_info.get("max_input_channels", 1)
                        out_ch = out_info.get("max_output_channels", 1)
                        channels = max(1, in_ch, out_ch)
                except Exception as e:
                        messagebox.showerror("Device error", f"Failed to query devices: {e}")
                        return

                def callback(indata, outdata, frames, time, status):
                        if status:
                                # status can be printed or reflected in the GUI
                                print("Stream status:", status, file=sys.stderr)
                        try:
                                # indata shape: (frames, in_channels)
                                # Make outdata shape match out channels
                                adjusted = adjust_channels(indata, out_ch)
                                vol = float(self.volume.get())
                                outdata[:] = adjusted * vol
                        except Exception as e:
                                # If something goes wrong in callback, zero output to avoid noise
                                outdata.fill(0)
                                print("Callback error:", e, file=sys.stderr)
                                traceback.print_exc()

                try:
                        # Open duplex stream mapping input->output by device indices.
                        self.stream = sd.Stream(device=(in_idx, out_idx),
                                                                        samplerate=sr,
                                                                        blocksize=0,  # let sounddevice choose
                                                                        dtype='float32',
                                                                        channels=channels,
                                                                        callback=callback)
                        self.stream.start()
                        self.status_var.set(f"Running (SR={sr} Hz) — input {in_idx} -> output {out_idx}")
                        self.start_btn.config(state="disabled")
                        self.stop_btn.config(state="normal")
                        self.in_combo.config(state="disabled")
                        self.out_combo.config(state="disabled")
                        self.sr_entry.config(state="disabled")
                        self.refresh_btn.config(state="disabled")
                except Exception as e:
                        self.stream = None
                        messagebox.showerror("Stream error", f"Failed to start stream:\n{e}")
                        self.status_var.set("Failed to start")

        def stop_stream(self):
                if self.stream:
                        try:
                                self.stream.stop()
                                self.stream.close()
                        except Exception:
                                pass
                        self.stream = None
                self.status_var.set("Stopped")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.in_combo.config(state="readonly")
                self.out_combo.config(state="readonly")
                self.sr_entry.config(state="normal")
                self.refresh_btn.config(state="normal")

        def on_close(self):
                try:
                        self.stop_stream()
                finally:
                        self.root.destroy()

# ---------- Entry point ----------

def main():
        try:
                root = tk.Tk()
        except Exception as e:
                print("Tkinter not available or failed to initialize:", e, file=sys.stderr)
                return

        app = MonitorApp(root)
        root.mainloop()

if __name__ == "__main__":
        main()