"""Measure monitor playback pace while the editor's playhead tick runs.

The old tick scanned the whole track for peak/RMS every 60 ms while holding
the audio buffer's lock, so the monitor device got silence for roughly a third
of every second. This harness plays a real file through the window's own
transport and compares the audio position it reached with the wall clock.

    python _measure_editor_stutter_1027.py "<file.wav>" [seconds]

A healthy run loses well under 0.3 s of audio over 4 s and performs ZERO
buffer scans on the tick.
"""
import sys
import time

import customtkinter as ctk

from editor_window import EditorWindow

path = sys.argv[1]
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

ctk.set_appearance_mode("dark")
root = ctk.CTk()
root.withdraw()
win = EditorWindow(root, path)
root.update()

rate = win.session.sample_rate or 44100

# How long ONE full-track level scan takes — the thing the tick used to run
# every 60 ms while holding the audio buffer's lock.
t0 = time.perf_counter()
win.session.stats(0, win.session.frames)
scan_ms = (time.perf_counter() - t0) * 1000

calls = [0]
real_stats = win.session.stats


def counted(*args, **kwargs):
    calls[0] += 1
    return real_stats(*args, **kwargs)


win.session.stats = counted

win._toggle_play()
if not win.session.playing:
    print("could not start preview playback (no output device?)")
    raise SystemExit(3)

start_pos = win.session.position
t0 = time.perf_counter()
while time.perf_counter() - t0 < seconds:
    root.update()
    time.sleep(0.01)
wall = time.perf_counter() - t0
played = (win.session.position - start_pos) / rate
win._stop()
root.update()

lost = wall - played
print(f"full-track level scan      : {scan_ms:6.1f} ms  (cached: one per edit)")
print(f"buffer scans during play   : {calls[0]}")
print(f"audio played in {wall:5.2f} s : {played:6.2f} s")
print(f"lost                       : {lost:+.2f} s "
      f"({(lost / wall * 100 if wall else 0):+.1f}%)")
ok = calls[0] == 0 and lost < 0.30
print("RESULT:", "PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
