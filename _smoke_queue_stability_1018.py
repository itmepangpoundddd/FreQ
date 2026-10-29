#!/usr/bin/env python3
"""Smoke: queue stability under abuse [stability-1018].

The queue is now one virtualized canvas with a 40 ms scroll poller, deferred
selection repaints and card drawing from live Song objects. This drives all
of that with:

  1. random operations (refresh / filter / scroll / click / marker / add /
     remove / clear / shift-arrow) on a 300-song queue,
  2. songs with hostile data (duration None/NaN/inf/negative, no artist,
     empty title, no source, no id),
  3. a callback that raises on purpose — the app must CONTAIN it
     (report_callback_exception), not die,
  4. a refresh issued from a worker thread (must be re-dispatched, never
     touch Tk from that thread),
  5. destroy() while the 40 ms poller is armed (no `after` on a dead window).

Any escaped exception fails the run. The queue must also stay bounded: only
the visible cards exist as canvas items.
"""

import math
import os
import random
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_stability_"))

RESULTS = []
ESCAPED = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.004)


_prev_hook = sys.excepthook


def _hook(exc, val, tb):
    ESCAPED.append((exc, val))
    _prev_hook(exc, val, tb)


sys.excepthook = _hook

from radio_manager import Song  # noqa: E402
from gui import RadioApp, QUEUE_ROW_H, QUEUE_CARD_H  # noqa: E402

app = RadioApp()
app.update()
pump(app, 0.3)

hostile = [
    Song(title="", artist=None, duration=None, source=""),
    Song(title="NaN length", artist="A", duration=float("nan"), source="youtube"),
    Song(title="infinite", artist="B", duration=float("inf"), source="file"),
    Song(title="negative", artist="C", duration=-42.0, source="mic"),
    Song(title="no artist", artist="", duration=95.0, source="file"),
    Song(title="0123456789" * 40, artist="D" * 120, duration=1e9, source="youtube"),
]

rng = random.Random(1018)
with open(os.devnull, "w") as _devnull:
    import contextlib
    out = contextlib.redirect_stdout(_devnull)
    with out:
        for i in range(300):
            app.rq.add_song(Song(title=f"Song {i}", artist=f"Artist {i % 17}",
                                 duration=120.0 + i, source=("youtube", "file", "mic")[i % 3]))
app._refresh_queue()
pump(app, 0.2)

played = []
app._play_index = lambda index: played.append(index)   # never start audio

ops = 0
for step in range(700):
    pick = rng.random()
    ops += 1
    try:
        if pick < 0.20:
            app._refresh_queue(keep_scroll=rng.random() < 0.5)
        elif pick < 0.32:
            app._queue_filter = rng.choice(["", "Song 1", "Artist 3", "zzz"])
            app._refresh_queue()
        elif pick < 0.44:
            app.queue_frame._parent_canvas.yview_moveto(rng.random())
        elif pick < 0.58:
            y = rng.randrange(0, max(1, QUEUE_ROW_H * 8))
            app._on_queue_canvas_press(
                types.SimpleNamespace(x=rng.randrange(20, 400), y=y,
                                      state=0))
        elif pick < 0.66:
            y = rng.randrange(0, max(1, QUEUE_ROW_H * 8))
            app._on_queue_canvas_double(types.SimpleNamespace(x=50, y=y, state=0))
        elif pick < 0.74:
            app.rq.current_index = rng.randrange(0, max(1, len(app.rq.queue)))
            app._refresh_queue_markers()
        elif pick < 0.80:
            app._update_queue_selection_paint()
        elif pick < 0.86:
            app._apply_queue_shift_arrow(rng.choice((1, -1)))
        elif pick < 0.92:
            with contextlib.redirect_stdout(_devnull):
                app.rq.add_song(rng.choice(hostile))
            app._refresh_queue()
        elif pick < 0.96:
            if len(app.rq.queue) > 5:
                with contextlib.redirect_stdout(_devnull):
                    app.rq.remove_song(rng.randrange(0, len(app.rq.queue)))
                app._refresh_queue()
        else:
            app._queue_filter = ""
            app._queue_selected = app._queue_selected  # keep selection valid
            app._refresh_queue()
    except Exception as err:      # noqa: BLE001 — this is the thing we test
        print(f"FAIL: operation {step} ({pick:.2f}) raised {err!r}")
        ESCAPED.append((type(err), err))
        break
    if step % 25 == 0:
        pump(app, 0.02)

pump(app, 0.4)
check("700 random queue operations completed", ops == 700, f"ops={ops}")
check("no exception escaped to the interpreter", not ESCAPED,
      str(ESCAPED[:2]))
check("the app window is still alive", bool(app.winfo_exists()))
check("contained UI errors were never needed",
      getattr(app, "_callback_errors", 0) == 0,
      f"contained={getattr(app, '_callback_errors', 0)}")

# A known-good state for the structural checks (random removals can empty it).
app._queue_filter = ""
with contextlib.redirect_stdout(_devnull):
    while len(app.rq.queue) < 200:
        app.rq.add_song(Song(title=f"Refill {len(app.rq.queue)}",
                             artist="Refill", duration=90.0, source="file"))
app._refresh_queue()
pump(app, 0.2)

canvas = app._queue_canvas
if canvas is not None and canvas.winfo_exists():
    items = len(canvas.find_all())
    cards = [i for i in canvas.find_all() if canvas.type(i) == "polygon"]
    per_card = items / max(1, len(cards))
    # 8 items per card since 1021 (the body, marker, badge, start–stop
    # clock, length, title, artist, and the end-of-song mark) — the bound
    # moves with the design, the point of the check stays: the cost is per
    # VISIBLE card, never per song.
    check("only visible cards exist as canvas items",
          0 < items <= 40 * 9 and per_card <= 8.5,
          f"items={items} cards={len(cards)} per_card={per_card:.2f}")
    check("canvas height tracks the queue length",
          int(canvas.cget("height")) == len(app._queue_view) * QUEUE_ROW_H,
          f"height={canvas.cget('height')} rows={len(app._queue_view)}")
    check("the canvas is packed while the queue has songs",
          canvas.winfo_manager() == "pack")
else:
    check("queue canvas exists after the run", False, "canvas gone")

# hostile rows must still be readable, not just skipped
app._queue_filter = ""
app.rq.queue[:0] = list(hostile)
app._refresh_queue()
pump(app, 0.2)
pump(app, 0.2)
texts = [app._queue_canvas.itemcget(i, "text")
         for i in app._queue_canvas.find_all()
         if app._queue_canvas.type(i) == "text"]
check("malformed songs still render (no card is skipped)",
      sum(1 for t in texts if t) >= len(hostile) * 2,
      f"texts={len(texts)}")
check("a None duration renders as 00:00, not a crash", "00:00" in texts)

# ── containment: a raising callback must not take the app down ─────
def _boom():
    raise RuntimeError("deliberate callback failure")


app.after(0, _boom)
pump(app, 0.2)
check("a raising Tk callback is contained, not fatal",
      getattr(app, "_callback_errors", 0) >= 1,
      f"contained={getattr(app, '_callback_errors', 0)}")
check("the app still works after a contained error",
      bool(app.winfo_exists()) and app._queue_canvas is not None)

# ── worker-thread refresh must be re-dispatched ────────────────────
errors = []


def _worker():
    try:
        app._refresh_queue()          # must NOT touch Tk on this thread
    except Exception as err:          # noqa: BLE001
        errors.append(err)


t = threading.Thread(target=_worker, name="stability-worker")
t.start()
t.join(3.0)
before = len(app._queue_view)
pump(app, 0.3)
check("a worker-thread refresh raises nothing", not errors, str(errors[:1]))
check("the worker refresh lands on the UI thread",
      len(app._queue_view) == len(app.rq.queue),
      f"view={len(app._queue_view)} queue={len(app.rq.queue)} (was {before})")

# ── teardown while the 40 ms poller is armed ──────────────────────
app._refresh_queue()
pump(app, 0.15)
armed_token = vars(app).get("_queue_view_after")
armed = armed_token is not None
app.destroy()
time.sleep(0.15)
check("poller was armed before close", armed, f"token={armed_token}")
check("destroy() cancels the queue poller",
      vars(app).get("_queue_view_after") is None)
check("nothing escaped during teardown", not ESCAPED, str(ESCAPED[:2]))

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails or ESCAPED else 0)
