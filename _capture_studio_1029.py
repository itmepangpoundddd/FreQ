#!/usr/bin/env python3
"""Capture the Studio Display window for a visual check (Release 2026.1029.2).

Fills the screen with a realistic snapshot (music on air, next up, a moving
meter, ON AIR lit) and saves `_ui_studio_1029.png` — no playlist is touched.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(tempfile.mkdtemp(prefix="freq_studio_shot_"))

import customtkinter as ctk
from PIL import ImageGrab

ctk.set_appearance_mode("dark")

from radio_manager import Song            # noqa: E402
from studio_display import StudioDisplay  # noqa: E402

root = ctk.CTk()
root.geometry("1x1+4000+3000")   # park the parent so it cannot overlap the shot
root.configure(fg_color="#0a0e14")

state = {
    "title": "Midnight Drive (Extended Mix)",
    "artist": "Nova Kline  ·  Synthwave",
    "position": 96.0,
    "duration": 254.0,
    "live": False,
    "on_air": True,
    "status": "ON AIR",
    "next_title": "Sunset Boulevard",
    "next_sub": "The Coastline  ·  03:41",
    "levels": (0.62, 0.55),
}

win = StudioDisplay(root, lambda: state)
win.geometry("1180x680+120+120")
win.transient(None)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.02)


pump(1.2)
# Keep the meter alive for the shot (a static peek would decay to floor).
win.meter._peak = [-9.0, -12.0]
win.meter._peak_hold = [time.monotonic() + 5, time.monotonic() + 5]
win.meter.set_levels((0.62, 0.55))
pump(0.4)

x, y = win.winfo_rootx(), win.winfo_rooty()
shot = HERE / "_ui_studio_1029.png"
ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height())).save(shot)
print("saved", shot)
win.close()
root.destroy()
