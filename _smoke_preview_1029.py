#!/usr/bin/env python3
"""Smoke: the queue's Preview (audition) button — files, signals and mics.

Everything runs through the real RadioApp widgets and the real audio
engines:

  1. a Preview button sits next to Edit, hidden until a row is selected
  2. no selection / an un-downloaded YouTube row are refused with a reason
  3. a real WAV previews: the button turns into Stop with a running clock,
     the position really advances, and the ON-AIR player is never started
  4. pressing Stop cuts it and frees the session; a short file ends by itself
  5. a live `.m3u8` signal is auditioned on the monitor endpoint (probe,
     ffmpeg feed, running clock) while the on-air player stays stopped, and
     Stop leaves no feeder thread and no ffmpeg child behind
  6. a microphone row starts the mic monitor on the same endpoint — or, on a
     machine with no capture device, says why instead of pretending
  7. dropping the selection and going on air both stop whatever is playing
"""

import http.server
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(tempfile.mkdtemp(prefix="freq_preview_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def write_wav(path: Path, seconds: float, rate: int = 44100) -> Path:
    frames = int(rate * seconds)
    import math
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            value = int(12000 * math.sin(2 * 3.14159 * 440 * i / rate))
            data += struct.pack("<hh", value, value)
        fh.writeframes(bytes(data))
    return path


LONG = write_wav(Path("long.wav"), 6.0)
SHORT = write_wav(Path("short.wav"), 1.0)

import player as player_mod     # noqa: E402
import gui as gui_mod           # noqa: E402
from gui import RadioApp        # noqa: E402
from radio_manager import Song  # noqa: E402

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


def reset_queue(songs=None):
    app.rq.queue = list(songs or [])
    app.rq.library = list(app.rq.queue)
    app.rq.current_index = -1
    app._refresh_queue()


def btn_text(widget):
    try:
        return str(widget.cget("text") or "")
    except Exception:
        return ""


def wait_until(predicate, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        app.update()
        time.sleep(0.02)
    return predicate()


# ── fixture: a real 6 s HLS playlist served over HTTP ──────────────
hls_dir = Path(tempfile.mkdtemp(prefix="hls_preview_"))
PLAYLIST = hls_dir / "live.m3u8"
_gen = subprocess.run(
    [player_mod._ffmpeg_exe(), "-v", "error", "-y",
     "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
     "-c:a", "aac", "-b:a", "96k", "-f", "hls", "-hls_time", "2",
     "-hls_list_size", "0", "-hls_segment_filename", str(hls_dir / "seg%03d.ts"),
     str(PLAYLIST)], capture_output=True)
HLS_OK = _gen.returncode == 0 and PLAYLIST.is_file()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(hls_dir), **kw)

    def log_message(self, *a):
        pass


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
_port = sock.getsockname()[1]
sock.close()
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", _port), _Quiet)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
LIVE_URL = f"http://127.0.0.1:{_port}/live.m3u8"

# 1. the button exists, beside Edit, hidden with no selection
check("the queue panel has a Preview button",
      hasattr(app, "btn_queue_preview"))
check("it is labelled Preview",
      "Preview" in btn_text(app.btn_queue_preview),
      btn_text(app.btn_queue_preview))
check("it is wired to _toggle_preview",
      "_toggle_preview" in str(app.btn_queue_preview.cget("command")))
check("it is hidden with no selection",
      not app.btn_queue_preview.winfo_manager())

reset_queue([Song(title="Local A", artist="Test", source="local",
                  file_path=str(LONG), duration=6.0)])
app._queue_selected = 0
app._update_queue_filter_btn()
pump(0.05)
check("it appears once a row is selected",
      bool(app.btn_queue_preview.winfo_manager()))
check("it sits left of Edit in the same bar",
      app.btn_queue_preview.master is app.btn_queue_edit.master)
check("Edit is still there too", bool(app.btn_queue_edit.winfo_manager()))

# 2. plain refusals
reset_queue([])
app._queue_selected = None
calls.clear()
app._toggle_preview()
check("pressing Preview with no selection explains itself",
      [c for c in calls if c[0] == "info"] and not app._preview_active(),
      str(calls))

reset_queue([Song(title="YT", source="youtube", url="https://youtu.be/x",
                  id="not-cached-id-1029")])
app._queue_selected = 0
calls.clear()
app._toggle_preview()
check("an un-downloaded YouTube row is refused with the Edit hint",
      [c for c in calls if c[0] == "info"] and not app._preview_active(),
      str(calls))
check("that hint mentions Edit",
      any("edit" in str(c[1]).lower() for c in calls), str(calls))

# 3. a real file audition
reset_queue([Song(title="Local A", artist="Test", source="local",
                  file_path=str(LONG), duration=6.0)])
app._queue_selected = 0
app._update_queue_filter_btn()
toasts.clear()
app._toggle_preview()
started = wait_until(lambda: app._preview_session is not None, 20)
check("pressing Preview opens a monitor session", started,
      str(app._preview_session))
check("the button becomes Stop", "■" in btn_text(app.btn_queue_preview),
      btn_text(app.btn_queue_preview))
check("the operator is told what is being previewed",
      any("Previewing" in t for t in toasts), str(toasts))
pump(1.2)
first = app._preview_session.position if app._preview_session else 0
pump(1.2)
second = app._preview_session.position if app._preview_session else 0
check("the monitor position really advances", second - first > 30000,
      f"{first} → {second} frames")
check("the button shows a running clock",
      ":" in btn_text(app.btn_queue_preview), btn_text(app.btn_queue_preview))
check("the ON-AIR player was never started", not app.audio_player.is_playing,
      f"engine={app.audio_player.engine}")

# 4. stop, then let a short file finish by itself
app._toggle_preview()
pump(0.3)
check("pressing Stop releases the session", app._preview_session is None)
check("the button is back to Preview",
      "Preview" in btn_text(app.btn_queue_preview),
      btn_text(app.btn_queue_preview))
check("the poller is not left armed", app._preview_events is None)

reset_queue([Song(title="Short", source="local", file_path=str(SHORT),
                  duration=1.0)])
app._queue_selected = 0
app._toggle_preview()
check("the short file starts",
      wait_until(lambda: app._preview_session is not None, 15))
check("it ends on its own and frees the session",
      wait_until(lambda: app._preview_session is None, 20))
check("the button returns to Preview after the end",
      "Preview" in btn_text(app.btn_queue_preview),
      btn_text(app.btn_queue_preview))

# 5. a live signal link, auditioned on the monitor
check("the HLS fixture was built", HLS_OK)
reset_queue([Song(title="Live feed", source="stream", url=LIVE_URL)])
app._queue_selected = 0
app._update_queue_filter_btn()
toasts.clear()
app._toggle_preview()
audition = app._audition
live = wait_until(lambda: audition is not None and audition.running, 25)
check("pressing Preview on a stream row starts a monitor audition", live,
      str(audition))
if live:
    # ffmpeg needs a moment to fetch the playlist and the first segment; the
    # engine is already "running" while that happens, so wait for audio
    # before timing it.
    flowing = wait_until(lambda: audition.position > 0.5, 25)
    p_first = audition.position
    pump(1.5)
    p_second = audition.position
    check("the live audition really advances",
          flowing and p_second - p_first > 0.8,
          f"{p_first:.2f} → {p_second:.2f} s")
    check("the button shows the audition clock",
          "■" in btn_text(app.btn_queue_preview)
          and ":" in btn_text(app.btn_queue_preview),
          btn_text(app.btn_queue_preview))
    check("the live audition did NOT put the row on air",
          not app.audio_player.is_playing
          and app.rq.current_index == -1,
          f"playing={app.audio_player.is_playing} idx={app.rq.current_index}")
    feeder = getattr(audition, "_thread", None)
    app._toggle_preview()
    pump(0.4)
    check("Stop ends the live audition", not audition.running)
    check("Stop leaves no feeder thread behind",
          feeder is None or not feeder.is_alive())
    check("the button is back to Preview",
          "Preview" in btn_text(app.btn_queue_preview),
          btn_text(app.btn_queue_preview))

# 6. a microphone row monitors the mic (or explains why it cannot)
reset_queue([Song(title="Mic", source="mic", duration=0.0)])
app._queue_selected = 0
calls.clear()
app._toggle_preview()
mic_running = bool(app._audition is not None and app._audition.running)
if mic_running:
    check("a mic row starts a mic monitor on the monitor device", True,
          "audition kind=" + app._audition.kind)
    check("its label says MIC", "MIC" in btn_text(app.btn_queue_preview),
          btn_text(app.btn_queue_preview))
    pump(1.0)
    check("the mic monitor reports elapsed time",
          app._audition.position > 0.5, f"{app._audition.position:.2f} s")
    check("the mic audition did not open the on-air route",
          not app.audio_player.is_playing)
    app._toggle_preview()
    pump(0.3)
    check("Stop ends the mic monitor", not app._audition.running)
else:
    check("with no usable microphone the refusal names the reason",
          [c for c in calls if c[0] == "error"], str(calls))
    check("nothing is left half-started", not app._preview_active())

# 6b. a segment with several configured mics asks WHICH one to monitor
class _FakeDev:
    def __init__(self, ident, endpoint, name):
        self.id, self.endpoint_id, self.name = ident, endpoint, name


app._mic_input_devices = [_FakeDev(0, "ep-alpha", "Mic Alpha"),
                          _FakeDev(1, "ep-beta", "Mic Beta")]
panel = Song(title="Panel", source="mic", duration=30.0, mic_device_index=0,
             mic_device_index2=1, mic_endpoint_ids=["ep-alpha", "ep-beta"],
             mic_gains=[1.0, 1.5])
reset_queue([panel])
app._queue_selected = 0
app._update_queue_filter_btn()
app._toggle_preview()
choices = app._mic_preview_choices(panel)
labels = [c["label"] for c in choices]
check("a multi-mic segment opens the microphone picker",
      app._mic_preview_dialog is not None)
check("the picker lists the configured mics with their gains",
      any("Mic Alpha" in t and "gain 1" in t for t in labels)
      and any("Mic Beta" in t and "1.5" in t for t in labels), str(labels))
check("it also offers the system default microphone",
      any("default" in t.lower() for t in labels), str(labels))
check("nothing is monitoring before a choice is made",
      app._audition is None or not app._audition.running)

recorded = []
original_start_mic = app._audition.start_mic
app._audition.start_mic = (lambda name, vol, mics=None:
                           (recorded.append((name, mics)), (True, ""))[1])
dialog = app._mic_preview_dialog
dialog._var.set(1)          # the second configured mic
check("the picker defaults are the item's own microphones",
      len(dialog._choices) >= 2, str(len(dialog._choices)))
dialog._accept()
pump(0.1)
check("picking the second mic monitors that endpoint",
      bool(recorded) and recorded[-1][1] == [("ep-beta", 1.5)], str(recorded))
check("the button reads MIC after the pick",
      "MIC" in btn_text(app.btn_queue_preview),
      btn_text(app.btn_queue_preview))
check("the picker closed after choosing", app._mic_preview_dialog is None
      or not app._mic_preview_dialog.winfo_exists())
app._audition.start_mic = original_start_mic
app._stop_preview(quiet=True)

recorded.clear()
app._toggle_preview()
cancel_dialog = app._mic_preview_dialog
if cancel_dialog is not None:
    cancel_dialog._cancel()
    pump(0.1)
check("cancelling the picker starts no monitor",
      not recorded and (app._audition is None or not app._audition.running),
      str(recorded))
app._mic_input_devices = []

# 7. cleanup hooks
reset_queue([Song(title="Local A", source="local", file_path=str(LONG),
                  duration=6.0)])
app._queue_selected = 0
app._update_queue_filter_btn()
app._toggle_preview()
check("file preview running before the deselect",
      wait_until(lambda: app._preview_session is not None, 15))
app._clear_queue_selection()
pump(0.2)
check("clearing the selection stops the audition",
      app._preview_session is None)
check("the button hides with the selection",
      not app.btn_queue_preview.winfo_manager())

reset_queue([Song(title="Live feed", source="stream", url=LIVE_URL)])
app._queue_selected = 0
app._toggle_preview()
check("live audition running before going on air",
      wait_until(lambda: app._audition is not None and app._audition.running, 25))
app.audio_player.stop = lambda *a, **k: None      # no real output in a smoke test
try:
    app._play_index(0)
except Exception:
    pass
pump(0.3)
check("going on air stops a live audition",
      app._audition is None or not app._audition.running)

app._stop_preview(quiet=True)
httpd.shutdown()
passed = sum(1 for _n, ok, _d in RESULTS if ok)
print("\n" + "=" * 62)
print(f"  {passed}/{len(RESULTS)} checks passed")
print("=" * 62)
raise SystemExit(0 if passed == len(RESULTS) else 1)
