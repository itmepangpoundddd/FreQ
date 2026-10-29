#!/usr/bin/env python3
"""Smoke: mouse-wheel volume control [wheel-1017].

Drives the REAL RadioApp (headless, silent) and turns the wheel on the
actual CTkSlider surfaces, because CTk sliders do not expose a widget the
user can hit directly: CTkBaseClass.bind() forwards every sequence to the
slider's internal canvas, and that canvas is what a real wheel notch lands
on. A binding placed on the CTkSlider object instead of that canvas would
look fine here only if we clicked the object — so we always generate on
``slider._canvas``.

Verifies:
  1. the wheel is bound on every volume bar (main, dual, master, channels)
  2. one notch = ±5 % on the main bar, and label + player volume follow
  3. multi-notch deltas (-240) and high-resolution deltas (60) step right
  4. values clamp at the bar's own range (0–100, dual 0–200)
  5. the X11 wheel buttons (Button-4/5) work as well as <MouseWheel>
  6. a wheel notch on a channel fader moves THAT channel only
  7. CTk's own canvas bindings survive (add="+"): dragging still bound
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq / settings (relative cwd paths).
os.chdir(tempfile.mkdtemp(prefix="freq_wheel_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def notch(slider, delta=120, button=None):
    """One real wheel notch on the surface the pointer actually hits."""
    if button:
        slider._canvas.event_generate(f"<Button-{button}>", x=6, y=6)
    else:
        slider._canvas.event_generate("<MouseWheel>", delta=delta)


from gui import RadioApp  # noqa: E402

app = RadioApp()
try:
    app.update_idletasks()
    app.update()

    main = app.volume_slider
    dual = app.dual_vol_slider
    master = app.mixer_master_slider
    chans = app.mixer_channels

    # ── 1. every volume bar answers the wheel ───────────────────
    surfaces = {"main": main, "dual": dual, "master": master}
    for cid, ch in chans.items():
        surfaces[f"channel {cid}"] = ch.fader
    for name, slider in surfaces.items():
        canvas = slider._canvas
        bound = all(bool(canvas.bind(seq))
                    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"))
        check(f"{name} volume bar answers the wheel", bound)

    # ── 2. one notch = ±5 %, everything follows ─────────────────
    main.set(80.0)
    app._on_volume_change(80.0)
    notch(main, 120)
    app.update()
    check("one notch up: 80 -> 85", main.get() == 85.0, f"{main.get()}")
    check("the % readout follows the wheel",
          app.volume_label.cget("text") == "85%",
          app.volume_label.cget("text"))
    check("the playback engine follows the wheel",
          abs(app.audio_player.volume - 0.85) < 1e-6,
          f"{app.audio_player.volume}")
    notch(main, -120)
    app.update()
    check("one notch down: 85 -> 80", main.get() == 80.0, f"{main.get()}")

    # ── 3. multi-notch + high-resolution deltas ─────────────────
    notch(main, -240)
    app.update()
    check("a double notch (-240) moves 10 %", main.get() == 70.0, f"{main.get()}")
    notch(main, 120 * 3)
    app.update()
    check("a triple notch (+360) moves 15 %", main.get() == 85.0, f"{main.get()}")
    notch(main, 60)
    app.update()
    check("a high-resolution delta (60) still moves one notch",
          main.get() == 90.0, f"{main.get()}")

    # ── 4. clamping at the bar's own range ──────────────────────
    for _ in range(40):
        notch(main, -120)
    app.update()
    check("the main bar clamps at 0", main.get() == 0.0, f"{main.get()}")
    for _ in range(40):
        notch(main, 120)
    app.update()
    check("the main bar clamps at 100", main.get() == 100.0, f"{main.get()}")
    # The readout is driven by the value we HAND to the callback: CTkSlider.set
    # clamps the bar itself, so only the label/mirror would show 105 % if the
    # step maths let the value run past the top.
    check("the % readout never runs past the top",
          app.volume_label.cget("text") == "100%",
          app.volume_label.cget("text"))
    check("clamping at 100 still reaches full player volume",
          abs(app.audio_player.volume - 1.0) < 1e-6, f"{app.audio_player.volume}")
    for _ in range(5):
        notch(main, -120)
    for _ in range(40):
        notch(main, -120)
    app.update()
    check("the % readout never runs below zero",
          app.volume_label.cget("text") == "0%",
          app.volume_label.cget("text"))

    dual.set(190.0)
    for _ in range(6):
        notch(dual, 120)
    app.update()
    check("the dual bar clamps at its own 200 % ceiling",
          dual.get() == 200.0, f"{dual.get()}")
    check("the dual readout stops at 200 %",
          app.dual_vol_label.cget("text") == "200%",
          app.dual_vol_label.cget("text"))
    notch(dual, -120)
    app.update()
    check("the dual bar steps down from the ceiling", dual.get() == 195.0,
          f"{dual.get()}")

    # ── 5. X11 wheel buttons ────────────────────────────────────
    main.set(50.0)
    app._on_volume_change(50.0)
    notch(main, button=4)
    app.update()
    check("X11 Button-4 (wheel up) raises the volume", main.get() == 55.0,
          f"{main.get()}")
    notch(main, button=5)
    app.update()
    check("X11 Button-5 (wheel down) lowers the volume", main.get() == 50.0,
          f"{main.get()}")

    # ── 6. a channel fader moves only its own channel ───────────
    music = chans["music"]
    music.fader.set(100.0)
    app.volume_var.set(80.0)
    main.set(80.0)
    notch(music.fader, -120)
    app.update()
    check("a wheel notch on the Music fader moves that channel only",
          music.fader.get() == 95.0 and app.volume_var.get() == 80.0,
          f"channel={music.fader.get()} master={app.volume_var.get()}")
    check("the channel's own dB readout follows its fader",
          music.vol_label.cget("text").endswith("dB"),
          music.vol_label.cget("text"))

    # ── 6b. the notch stops at the bar (no outer scroll handler) ─
    # Tk runs the bindings of one widget in registration order and stops at
    # the first "break", so a later add="+" handler must never see the event.
    later = []
    main._canvas.bind("<MouseWheel>", lambda e: later.append(1), add="+")
    notch(main, 120)
    app.update()
    check("the notch is consumed at the bar (break keeps outer handlers out)",
          later == [], f"outer handlers ran {len(later)}x")

    # ── 7. CTk's internal canvas bindings survive ───────────────
    drag_seqs = tuple(seq for seq in ("<Button-1>", "<B1-Motion>")
                      if bool(main._canvas.bind(seq)))
    check("dragging the bar still works (CTk canvas bindings preserved)",
          len(drag_seqs) == 2,
          f"kept {drag_seqs}")
    check("the wheel binding did not take the drag slot over",
          bool(main._canvas.bind("<MouseWheel>"))
          and bool(main._canvas.bind("<Button-1>")))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
finally:
    try:
        app.destroy()
    except Exception:
        pass

sys.exit(1 if any(not ok for _, ok, _ in RESULTS) else 0)
