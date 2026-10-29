#!/usr/bin/env python3
"""Open the operator's real .m3u8 signal through the real app.

Runs the genuine RadioApp: paste the link into the Add tab field, press the
real add button, then play the row and watch the position against the wall
clock for ~15 s. Nothing is stubbed except the toast/messagebox sinks.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(tempfile.mkdtemp(prefix="freq_real_link_"))

URL = ("https://cdn-edge.iiptvcdn.com/radio_edge/"
       "45d4-d521-cbfc-d686-0123/playlist.m3u8")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


import gui as gui_mod          # noqa: E402
from gui import RadioApp       # noqa: E402

calls, toasts = [], []
gui_mod.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
gui_mod.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
gui_mod.messagebox.showerror = lambda *a, **k: calls.append(("error", a))

app = RadioApp()
app._show_toast = lambda text="", **kw: toasts.append(text)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


# 1. the probe the app uses before airing anything
ok, detail = app.audio_player.probe_stream(URL, timeout=12)
check("the link probes as playable audio", ok, str(detail))
check("the probe names the codec", "aac" in str(detail).lower(), str(detail))

# 2. paste it into the Add tab and press the real button
app.rq.queue = []
app.rq.library = []
app.rq.current_index = -1
app._refresh_queue()
app.stream_url_entry.delete(0, "end")
app.stream_url_entry.insert(0, URL)
app.stream_name_entry.delete(0, "end")
app.stream_name_entry.insert(0, "IIP TV Radio")
app._add_stream()
pump(0.2)
check("the link becomes one queue item", len(app.rq.queue) == 1,
      str(len(app.rq.queue)))
song = app.rq.queue[0] if app.rq.queue else None
check("it is queued as a stream", getattr(song, "source", "") == "stream",
      getattr(song, "source", ""))
check("the exact link is stored", getattr(song, "url", "") == URL,
      str(getattr(song, "url", "")))
check("the operator's name is used", getattr(song, "title", "") == "IIP TV Radio",
      str(getattr(song, "title", "")))

# 3. the row reads LIVE, not 00:00
canvas = app._ensure_queue_canvas()
app._queue_selected = 0
items = app._draw_queue_row(canvas, 0)
texts = [str(canvas.itemcget(i, "text")) for i in items if canvas.type(i) == "text"]
check("the row is stamped LIVE", "LIVE" in texts, str(texts))
check("the row wears the 📡 badge", app._queue_row_icon(song) == "📡",
      app._queue_row_icon(song))

# 4. press play — the app probes, connects, then airs it
app._play_index(0)
deadline = time.monotonic() + 25
while time.monotonic() < deadline and not app.audio_player.is_playing:
    pump(0.1)
check("pressing play puts the link on air", app.audio_player.is_playing,
      f"engine={app.audio_player.engine}")
check("the total column says LIVE", str(app.np_time_total.cget("text")) == "LIVE",
      str(app.np_time_total.cget("text")))
check("the status says it is live",
      "live" in str(app.np_status.cget("text")).lower(),
      str(app.np_status.cget("text")))

# 5. audio really flows, and keeps flowing in realtime for 15 s
pump(1.5)
pos_a = app.audio_player.get_position()
wall_a = time.monotonic()
pump(15.0)
pos_b = app.audio_player.get_position()
wall_b = time.monotonic()
drift = (pos_b - pos_a) - (wall_b - wall_a)
check("audio flows (position advances)", pos_b - pos_a > 10.0,
      f"{pos_a:.2f} → {pos_b:.2f} s")
check("it stays in realtime (no stall/burst)", abs(drift) < 2.0,
      f"drift {drift:+.2f} s over {wall_b - wall_a:.1f} s wall clock")
check("it is still on air after 15 s", app.audio_player.is_playing,
      str(app.np_status.cget("text")))
check("no UI error surfaced during the run",
      not [c for c in calls if c[0] == "error"], str(calls))
check("the progress tick keeps repainting", not app._playback_stalled()
      if hasattr(app, "_playback_stalled") else True)

# the ffmpeg feeder really is the source
feeder = getattr(app.audio_player, "_decoder", None)
check("a live feeder thread is reading the link",
      feeder is not None and feeder.is_alive())
decoder_proc = None
try:
    decoder_proc = app.audio_player._decoder_proc  # may not exist
except Exception:
    pass

# 6. stopping must not leave the feeder running
app.audio_player.stop()
pump(0.5)
check("stop ends playback", not app.audio_player.is_playing)
check("stop leaves no feeder thread running",
      feeder is not None and not feeder.is_alive())
check("stop leaves no ffmpeg child alive",
      decoder_proc is None or decoder_proc.poll() is not None,
      str(getattr(decoder_proc, "poll", lambda: None)()))

# 7. start it a second time — a re-connect must work after a stop
app.rq.current_index = -1
app._playing = False
app._play_index(0)
deadline = time.monotonic() + 25
while time.monotonic() < deadline and not app.audio_player.is_playing:
    pump(0.1)
p2 = app.audio_player.get_position()
pump(3.0)
check("a second connect works and audio advances",
      app.audio_player.is_playing and app.audio_player.get_position() - p2 > 1.5,
      f"{p2:.2f} → {app.audio_player.get_position():.2f} s")
app.audio_player.stop()
pump(0.3)

passed = sum(1 for _n, ok, _d in RESULTS if ok)
print("\n" + "=" * 62)
print(f"  {passed}/{len(RESULTS)} checks passed")
print("=" * 62)
raise SystemExit(0 if passed == len(RESULTS) else 1)
