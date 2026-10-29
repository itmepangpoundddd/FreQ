#!/usr/bin/env python3
"""Smoke: live streams (.m3u8) — Add-tab section, queue row and real playback.

Everything is driven through the real widgets and the real player:

  1. the Add tab carries a 📡 section with a .m3u8 entry, an optional name
     entry and a button wired to `_add_stream`
  2. an empty box and an unusable string are both refused, with the queue
     untouched (a link that is not a link must never become a "song")
  3. a real local HLS playlist — wrapped in quotes/brackets like a "copy
     link" button produces — is queued as source="stream" with no length
  4. the queue row says LIVE instead of 00:00 and wears the 📡 badge
  5. pressing play on that row probes the link, starts the live feed and
     reports 📡 Live; the position really advances (audio is flowing)
  6. the same playlist served over HTTP (a genuine signal link) probes and
     plays the same way
  7. a dead link fails the probe with a reason, and the item is reported
     failed instead of pretending to air
  8. stopping the item kills the ffmpeg feeder — no orphan process
  9. Edit on a stream row refuses politely (there is no file to cut)
"""

import http.server
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_stream_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ── fixture: a real 6 s HLS playlist (ffmpeg) ───────────────────────
import player as player_mod  # noqa: E402

FFMPEG = player_mod._ffmpeg_exe()
hls_dir = Path(tempfile.mkdtemp(prefix="hls_src_"))
PLAYLIST = hls_dir / "live.m3u8"
gen = subprocess.run(
    [FFMPEG, "-v", "error", "-y",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
     "-c:a", "aac", "-b:a", "96k",
     "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
     "-hls_segment_filename", str(hls_dir / "seg%03d.ts"),
     str(PLAYLIST)],
    capture_output=True)
if gen.returncode != 0 or not PLAYLIST.is_file():
    print("cannot build the HLS fixture:", gen.stderr.decode(errors="replace")[-400:])
    raise SystemExit(2)

# Serve the same playlist over HTTP so the "real link" path is covered too.
class _Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(hls_dir), **kw)

    def log_message(self, *a):
        pass

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
LIVE_URL = f"http://127.0.0.1:{port}/live.m3u8"

# ── the real app ────────────────────────────────────────────────────
import gui as gui_mod  # noqa: E402
from gui import RadioApp  # noqa: E402
from radio_manager import Song  # noqa: E402

calls = []
toasts = []
gui_mod.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
gui_mod.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
gui_mod.messagebox.showerror = lambda *a, **k: calls.append(("error", a))

app = RadioApp()
app._show_toast = lambda text="", **kw: toasts.append(text)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def reset_queue(songs=None):
    app.rq.queue = list(songs or [])
    app.rq.library = list(app.rq.queue)
    app.rq.current_index = -1
    app._refresh_queue()


def submit(text: str, name: str = ""):
    calls.clear()
    toasts.clear()
    app.stream_url_entry.delete(0, "end")
    app.stream_url_entry.insert(0, text)
    app.stream_name_entry.delete(0, "end")
    if name:
        app.stream_name_entry.insert(0, name)
    app._add_stream()
    pump(0.05)


def btn_text(widget) -> str:
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


# 1. the section exists and is wired
check("the Add tab has the live-stream link box",
      hasattr(app, "stream_url_entry")
      and ".m3u8" in str(app.stream_url_entry.cget("placeholder_text") or ""),
      str(getattr(app, "stream_url_entry", "")))
check("there is an optional name box",
      hasattr(app, "stream_name_entry")
      and "name" in str(app.stream_name_entry.cget("placeholder_text") or "").lower())
check("the button says what it does",
      "stream" in btn_text(getattr(app, "btn_stream_add", None)).lower(),
      btn_text(getattr(app, "btn_stream_add", None)))
check("the button is wired to _add_stream",
      "_add_stream" in str(app.btn_stream_add.cget("command")))

# 2. nothing / nonsense is refused, queue untouched
reset_queue()
submit("")
check("an empty box is refused with a warning",
      [c for c in calls if c[0] == "warn"] and not app.rq.queue, str(calls))
submit("https://")
check("a bare scheme is refused", app.rq.queue == [], str(app.rq.queue))
submit("just some words")
check("plain text is refused rather than queued",
      [c for c in calls if c[0] == "error"] and not app.rq.queue, str(calls))

# 3. a real playlist, pasted the way copy-link produces it
submit(f'"{PLAYLIST}"', name="Studio feed")
check("the pasted playlist becomes one queue item", len(app.rq.queue) == 1,
      str(len(app.rq.queue)))
song = app.rq.queue[0] if app.rq.queue else None
check("it is a stream item", getattr(song, "source", "") == "stream",
      getattr(song, "source", ""))
check("the link is stored", getattr(song, "url", "") == str(PLAYLIST),
      str(getattr(song, "url", "")))
check("wrapping quotes were stripped", '"' not in str(getattr(song, "url", "")))
check("it has no length (live)", abs(float(getattr(song, "duration", -1))) < 0.001,
      str(getattr(song, "duration", None)))
check("the typed name is used", getattr(song, "title", "") == "Studio feed",
      str(getattr(song, "title", "")))
check("the boxes are cleared after adding",
      not app.stream_url_entry.get() and not app.stream_name_entry.get())
check("the operator is told where it landed",
      toasts and "#1" in toasts[-1], str(toasts))

# an unnamed link gets a label from the link itself
reset_queue()
submit(str(PLAYLIST))
check("an unnamed link is labelled from the link",
      app.rq.queue and app.rq.queue[0].title == "live",
      app.rq.queue[0].title if app.rq.queue else "")

# 4. the row itself
canvas = app._ensure_queue_canvas()
app._queue_selected = 0
items = app._draw_queue_row(canvas, 0)
texts = [str(canvas.itemcget(i, "text")) for i in items
         if canvas.type(i) == "text"]
check("the row is stamped LIVE, not 00:00", "LIVE" in texts, str(texts))
check("the row wears the 📡 badge",
      app._queue_row_icon(app.rq.queue[0]) == "📡",
      app._queue_row_icon(app.rq.queue[0]))

# 5. press play: probe, then real live audio
reset_queue([Song(title="Studio feed", artist="Live stream", source="stream",
                  url=str(PLAYLIST), duration=0.0)])
app._play_index(0)
deadline = time.monotonic() + 20
while time.monotonic() < deadline and not app.audio_player.is_playing:
    pump(0.05)
check("pressing play starts the live feed", app.audio_player.is_playing,
      f"engine={app.audio_player.engine}")
check("the player shows a live item, not a length",
      str(app.np_time_total.cget("text")) == "LIVE",
      str(app.np_time_total.cget("text")))
check("the status says it is live",
      "live" in str(app.np_status.cget("text")).lower(),
      str(app.np_status.cget("text")))
first_pos = app.audio_player.get_position()
pump(1.6)
second_pos = app.audio_player.get_position()
check("audio really flows (position advances)",
      second_pos - first_pos > 1.0, f"{first_pos:.2f} → {second_pos:.2f} s")
check("a live item has no fixed duration",
      app.audio_player.get_duration() == 0,
      str(app.audio_player.get_duration()))

# 8. stopping must not leave ffmpeg behind
feeder = getattr(app.audio_player, "_decoder", None)
app.audio_player.stop()
pump(0.3)
check("stop ends playback", not app.audio_player.is_playing)
check("stop leaves no feeder thread running",
      feeder is not None and not feeder.is_alive())
app.rq.current_index = -1
app._playing = False

# 6. the same playlist as a genuine HTTP signal link
ok, detail = app.audio_player.probe_stream(LIVE_URL)
check("a real http .m3u8 link probes successfully", ok, str(detail))
check("the probe names the codec", "aac" in str(detail).lower(), str(detail))
reset_queue([Song(title="Live URL", artist="Live stream", source="stream",
                  url=LIVE_URL, duration=0.0)])
app._play_index(0)
deadline = time.monotonic() + 20
while time.monotonic() < deadline and not app.audio_player.is_playing:
    pump(0.05)
check("an http link plays on air too", app.audio_player.is_playing)
pos_a = app.audio_player.get_position()
pump(1.2)
check("the http feed is advancing",
      app.audio_player.get_position() - pos_a > 0.7,
      f"{pos_a:.2f} → {app.audio_player.get_position():.2f} s")
app.audio_player.stop()
pump(0.2)
app.rq.current_index = -1
app._playing = False

# 7. a dead link is reported, not aired
dead = hls_dir / "nope.m3u8"
ok, why = app.audio_player.probe_stream(str(dead))
check("a dead link fails the probe", not ok, str(why))
check("the failure comes with a reason", bool(str(why).strip()), str(why))
reset_queue([Song(title="Dead feed", artist="Live stream", source="stream",
                  url=str(dead), duration=0.0)])
app._play_index(0)
deadline = time.monotonic() + 20
# Wait for the verdict: the probe runs off the UI thread and the progress
# tick (500 ms) applies it, so polling "still connecting" is the real test.
while (time.monotonic() < deadline
       and "connect" in str(app.np_status.cget("text")).lower()):
    pump(0.05)
check("a dead feed does not go on air", not app.audio_player.is_playing)
check("the operator sees it failed",
      "fail" in str(app.np_status.cget("text")).lower()
      or any("fail" in t.lower() for t in toasts),
      f"{app.np_status.cget('text')} / {toasts}")
check("the failed item is not left marked as on air",
      app.rq.current_index == -1 and not app._playing,
      f"index={app.rq.current_index} playing={app._playing}")

# 8b. the item survives a playlist save/load round trip
reset_queue([Song(title="Saved feed", artist="Live stream", source="stream",
                  url=LIVE_URL, duration=0.0)])
app.rq.save_to_file()
reset_queue()
app.rq.load_from_file()
check("the stream item comes back from the playlist file",
      len(app.rq.queue) == 1, str(len(app.rq.queue)))
beat = app.rq.queue[0] if app.rq.queue else None
check("it is still a stream after reloading",
      getattr(beat, "source", "") == "stream",
      str(getattr(beat, "source", "")))
check("its link survived the round trip",
      getattr(beat, "url", "") == LIVE_URL, str(getattr(beat, "url", "")))

# 9. Edit on a live row: there is no file, and it says so
reset_queue([Song(title="Studio feed", source="stream", url=LIVE_URL)])
app._queue_selected = 0
calls.clear()
app._open_selected_editor()
check("Edit on a live row explains there is nothing to cut",
      any("info" in c[0] for c in calls), str(calls))

# tidy up
try:
    app.audio_player.stop()
    app.rq.save_to_file()
except Exception:
    pass
httpd.shutdown()
shutil.rmtree(hls_dir, ignore_errors=True)

passed = sum(1 for _n, ok, _d in RESULTS if ok)
print("\n" + "=" * 62)
print(f"  {passed}/{len(RESULTS)} checks passed")
print("=" * 62)
raise SystemExit(0 if passed == len(RESULTS) else 1)
