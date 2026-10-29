#!/usr/bin/env python3
"""Smoke: Studio Display — the control-room screen for Release 2026.1029.2.

Driven through the real RadioApp, the real queue and the real window:

  1. the toolbar carries a 📺 Studio button wired to the opener
  2. the state provider is read-only and safe with an empty queue
  3. with music queued it reports the song on air, its length and the next
     song (the same walk the rundown takes)
  4. a skip-flagged next song is stepped over — the screen never promises a
     song the rundown will skip
  5. a stop-note shows up as "STOP AT NOTE" instead of a song title
  6. the window renders title / artist / times / next up from that state
  7. the ON AIR lamp follows playback, and the status line says AD BREAK /
     JINGLE / MIC LIVE when one of those holds the stage
  8. the VU meter uses the tick's OWN measurement (levels are drained once
     per tick — a second read in the same tick would paint silence)
  9. F11 goes fullscreen, Esc leaves it; closing is clean and the window
     reopens on the next click
 10. the display never starts, stops or seeks playback (read-only)
"""

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(tempfile.mkdtemp(prefix="freq_studio_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


import gui as gui_mod           # noqa: E402
from gui import RadioApp        # noqa: E402
from radio_manager import Song  # noqa: E402

calls = []
gui_mod.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
gui_mod.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
gui_mod.messagebox.showerror = lambda *a, **k: calls.append(("error", a))
gui_mod.messagebox.askyesno = lambda *a, **k: True

app = RadioApp()
app._show_toast = lambda text="", **kw: None


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            app.update()
        except Exception:
            pass
        time.sleep(0.02)


def song(title, seconds=180.0, artist="Artist"):
    return Song(title=title, artist=artist, duration=seconds,
                file_path="x.mp3", source="local")


# ── 1. the toolbar entry point ──────────────────────────────────────
check("the toolbar has the 📺 Studio button",
      hasattr(app, "btn_studio")
      and "📺" in str(app.btn_studio.cget("text")))

app.btn_studio.invoke()
pump(0.2)
win = getattr(app, "studio_display", None)
check("clicking it opens the studio display",
      win is not None and bool(win.winfo_exists()))
if win is None:
    print("cannot continue without the window")
    raise SystemExit(1)

# ── 2. empty queue ──────────────────────────────────────────────────
state = app._studio_state()
check("the state is safe with an empty queue",
      state["title"] == "" and state["on_air"] is False
      and state["next_title"] in ("", "—"),
      f"next={state['next_title']!r}")
check("nothing airs → OFF AIR in the window",
      win.on_air_label.cget("text") == "OFF AIR",
      win.on_air_label.cget("text"))

# ── 3. music on air + next up ───────────────────────────────────────
app.rq.queue = [song("Track One", 200.0, "First Artist"),
                song("Track Two", 155.0, "Second Artist")]
app.rq.library = list(app.rq.queue)
app.rq.current_index = 0
app._refresh_queue()
pump(0.35)
state = app._studio_state()
check("the screen reports the song on air",
      state["title"] == "Track One" and state["artist"] == "First Artist",
      f"{state['title']!r} / {state['artist']!r}")
check("and its length in seconds", abs(state["duration"] - 200.0) < 0.01,
      str(state["duration"]))
check("next up is the following song",
      state["next_title"] == "Track Two" and "2:35" in state["next_sub"],
      f"{state['next_title']!r} / {state['next_sub']!r}")
check("the window shows that title",
      "Track One" in win.title_label.cget("text"),
      win.title_label.cget("text"))
check("the window shows the next song",
      "Track Two" in win.next_label.cget("text"),
      win.next_label.cget("text"))

# ── 4. skip flag is honoured ────────────────────────────────────────
app.rq.queue[1].skip = True
app.rq.queue.append(song("Track Three", 90.0, "Third Artist"))
app._refresh_queue()
pump(0.2)
state = app._studio_state()
check("a skip-flagged next song is stepped over",
      state["next_title"] == "Track Three",
      state["next_title"])
app.rq.queue[1].skip = False

# ── 5. a stop note is announced as such ─────────────────────────────
app.rq.add_note(1, "Station ID break", color="#d29922", action="stop")
app._refresh_queue()
pump(0.2)
state = app._studio_state()
check("a stop note reads STOP AT NOTE, not a song",
      "STOP" in state["next_title"].upper() and "Station ID" in state["next_sub"],
      f"{state['next_title']!r} / {state['next_sub']!r}")
app.rq.queue = [s for s in app.rq.queue if not app.rq.is_note(s)]
app.rq.current_index = 0
app._refresh_queue()
pump(0.2)

# ── 6. ON AIR lamp + status ────────────────────────────────────────
app._playing = True
state = app._studio_state()
check("playback lights the ON AIR lamp", state["on_air"] is True)
win._render_state(state)
check("the lamp text flips to ON AIR",
      win.on_air_label.cget("text") == "ON AIR",
      win.on_air_label.cget("text"))

app._ad_playing = True
check("an ad break is named in the header",
      "AD BREAK" in app._studio_state()["status"],
      app._studio_state()["status"])
app._ad_playing = False
app._jingle_playing = True
check("a jingle is named too",
      "JINGLE" in app._studio_state()["status"],
      app._studio_state()["status"])
app._jingle_playing = False
app._playing = False
pump(0.25)
check("stopping returns the lamp to OFF AIR",
      win.on_air_label.cget("text") == "OFF AIR",
      win.on_air_label.cget("text"))

# ── 7. the meter reads the tick's measurement ──────────────────────
app._last_tap_levels = (0.5, 0.25)
app._last_meter_active = True
state = app._studio_state()
check("the meter is fed from the cached tick levels, not a fresh tap",
      state["levels"] == (0.5, 0.25), str(state["levels"]))
win.meter.set_levels(state["levels"])
pump(0.1)
check("the meter accepts the levels", win.meter.levels == (0.5, 0.25),
      str(win.meter.levels))
items = win.meter.canvas.find_all()
check("the meter draws meters and a scale", len(items) > 6, str(len(items)))
win.meter.set_levels(None)
check("a missing measurement paints silence, not a crash",
      win.meter.levels == (0.0, 0.0), str(win.meter.levels))
app._last_tap_levels = None

# ── 8. clock ────────────────────────────────────────────────────────
pump(0.3)
clock = win.clock_label.cget("text")
check("the wall clock runs in HH:MM:SS",
      len(clock) == 8 and clock.count(":") == 2, clock)

# ── 9. fullscreen ───────────────────────────────────────────────────
win.toggle_fullscreen()
pump(0.05)
check("F11 goes fullscreen", win._fullscreen is True)
win._leave_fullscreen()
pump(0.05)
check("Esc leaves fullscreen", win._fullscreen is False)

# ── 10. read-only ───────────────────────────────────────────────────
started = []
app.audio_player.play_file = lambda *a, **k: started.append(a)
app.audio_player.stop = lambda *a, **k: started.append(("stop",))
app.audio_player.seek = lambda *a, **k: started.append(("seek",))
win._tick()
app._studio_state()
check("the display never starts, stops or seeks the player",
      not started, str(started[:3]))

# ── 11. close / reopen ──────────────────────────────────────────────
win.close()
pump(0.15)
try:
    alive = bool(win.winfo_exists())
except Exception:
    alive = False
check("closing the window is clean", alive is False)
app._open_studio_display()
pump(0.2)
again = getattr(app, "studio_display", None)
check("clicking again reopens it",
      again is not None and bool(again.winfo_exists()))
app._open_studio_display()
pump(0.1)
check("asking again raises the same window (one display only)",
      app.studio_display is again)
if again is not None:
    again.close()

# ── Summary ─────────────────────────────────────────────────────────
passed = sum(1 for _n, ok, _d in RESULTS if ok)
total = len(RESULTS)
print(f"\n{passed}/{total} checks passed")
if passed != total:
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} — {detail}")
raise SystemExit(0 if passed == total else 1)
