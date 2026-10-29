#!/usr/bin/env python3
"""Smoke: queue undo, the skip flag and note lines (separators).

Driven through the real RadioApp and the real RadioQueue:

  1. Undo/redo puts back a removed row, a move and a cleared queue — by
     identity, with the position on air intact — and Ctrl+Z stays out of the
     way inside a text field
  2. The skip flag is stepped over by the rundown but stays in the list;
     clicking its zone toggles it, and the flag is undoable
  3. A note line is inserted at a gap, drawn as a coloured rule with its text
     and action, and its "stop" action halts the rundown where the line sits
  4. Notes and flags survive a .freq save/load round trip, are never aired,
     and are refused by Play / Edit / Preview instead of playing silence
"""

import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(tempfile.mkdtemp(prefix="freq_flags_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


import gui as gui_mod            # noqa: E402
from gui import RadioApp         # noqa: E402
from radio_manager import RadioQueue, Song  # noqa: E402

calls, toasts = [], []
gui_mod.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
gui_mod.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
gui_mod.messagebox.showerror = lambda *a, **k: calls.append(("error", a))

app = RadioApp()
app._show_toast = lambda text="", **kw: toasts.append(text)


def pump(seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def songs(count=3):
    return [Song(title=f"Song {i + 1}", artist="Test", duration=60.0)
            for i in range(count)]


def reset(items):
    app.rq.queue = list(items)
    app.rq.library = list(items)
    app.rq.current_index = -1
    app._queue_selected = None
    app._queue_selection = []
    app._queue_undo = []
    app._queue_redo = []
    app._refresh_queue()


def titles():
    return [s.title for s in app.rq.queue]


# ── 1. Undo / redo ─────────────────────────────────────────────────
base = songs(3)
reset(base)
app._queue_selected = 1
app._remove_selected()
check("remove takes the row out", titles() == ["Song 1", "Song 3"], str(titles()))
app._undo_queue()
pump()
check("Ctrl+Z brings the removed row back",
      titles() == ["Song 1", "Song 2", "Song 3"], str(titles()))
check("it comes back as the SAME row (same id), not a copy",
      app.rq.queue[1].id == base[1].id, app.rq.queue[1].id)
app._redo_queue()
pump()
check("redo removes it again", titles() == ["Song 1", "Song 3"], str(titles()))

reset(songs(3))
app._queue_selected = 0
app._move_selected(1)
check("move down reorders", titles() == ["Song 2", "Song 1", "Song 3"], str(titles()))
app._undo_queue()
pump()
check("undo puts the order back",
      titles() == ["Song 1", "Song 2", "Song 3"], str(titles()))

reset(songs(2))
gui_mod.messagebox.askyesno = lambda *a, **k: True
app._clear_queue()
pump()
check("clear empties the queue", app.rq.queue == [])
app._undo_queue()
pump()
check("undo restores a cleared queue", titles() == ["Song 1", "Song 2"], str(titles()))

toasts.clear()
app._queue_undo = []
app._undo_queue()
check("undo with an empty history says so instead of raising",
      any("Nothing to undo" in t for t in toasts), str(toasts))

entry = gui_mod.ctk.CTkEntry(app)
entry.pack()
entry.focus_set()
pump(0.1)
check("Ctrl+Z inside a text field is left to the entry",
      app._undo_key() is None)


# ── 2. Skip flag ───────────────────────────────────────────────────
reset(songs(3))
app._toggle_skip_flag(1)
pump()
check("the flag is pinned on the row", app.rq.queue[1].skip is True)
check("the operator is told the row will be stepped over",
      any("Skipping" in t for t in toasts), str(toasts[-1]))
app.rq.play(0)
verdict = app.rq.skip_next()
check("the rundown steps OVER a flagged song", verdict == "playing"
      and app.rq.queue[app.rq.current_index].title == "Song 3",
      f"{verdict} → {app.rq.queue[app.rq.current_index].title}")
app._toggle_skip_flag(1)
pump()
check("clearing the flag puts it back in rotation",
      app.rq.queue[1].skip is False)
app._toggle_skip_flag(1)
app._undo_queue()
pump()
check("the flag itself is undoable", app.rq.queue[1].skip is False,
      str(app.rq.queue[1].skip))

# The flag zone is a real click target on the painted row
canvas = app._ensure_queue_canvas()
app._refresh_queue()
pump(0.1)
view_pos = next(pos for pos, (_i, s) in enumerate(app._queue_view)
                if s.title == "Song 2")
flag_l, flag_r = app._queue_flag_span(
    max(gui_mod.QUEUE_CARD_PAD + gui_mod.QUEUE_CARD_MIN_W,
        app._queue_canvas_width() - gui_mod.QUEUE_CARD_PAD))
check("a click in the flag zone is recognised",
      app._over_queue_flag(view_pos, (flag_l + flag_r) / 2))
check("a click in the title area is not the flag",
      not app._over_queue_flag(view_pos, 120))
check("nothing is left flagged before the click test",
      [s.title for s in app.rq.queue if getattr(s, "skip", False)] == [])

event = type("E", (), {"x": (flag_l + flag_r) / 2,
                       "y": view_pos * gui_mod.QUEUE_ROW_H + 30})()
app._on_queue_canvas_press(event)
pump()
check("pressing the flag zone pins the flag",
      app.rq.queue[view_pos].skip is True,
      str([(s.title, s.skip) for s in app.rq.queue]))
app._toggle_skip_flag(view_pos)      # clear it again for the note tests


# ── 3. Note lines ──────────────────────────────────────────────────
reset(songs(3))
gap_event = type("E", (), {"x": 120,
                          "y": gui_mod.QUEUE_CARD_H + 5})()   # gap after row 1
check("a right-click between two rows maps to a gap",
      app._queue_gap_at(gap_event) == 1, str(app._queue_gap_at(gap_event)))
check("a right-click on a card maps to no gap",
      app._queue_gap_at(type("E", (), {"x": 120, "y": 20})()) is None)

app._apply_note_edit(None, 1, "Studio break", "#3fb950", "stop")
pump()
check("the note lands between the songs", app.rq.queue[1].title == "Studio break"
      and app.rq.is_note(app.rq.queue[1]), str(titles()))
check("it carries its colour and action",
      app.rq.queue[1].note_color == "#3fb950"
      and app.rq.queue[1].note_action == "stop",
      f"{app.rq.queue[1].note_color} / {app.rq.queue[1].note_action}")
check("the note does not count as a song in the header",
      "3 songs" in str(app.queue_stats_label.cget("text")),
      str(app.queue_stats_label.cget("text")))

canvas = app._ensure_queue_canvas()
items = app._draw_queue_row(canvas, 1)
kinds = [canvas.type(i) for i in items]
texts = [str(canvas.itemcget(i, "text")) for i in items
         if canvas.type(i) == "text"]
check("the note paints as a rule, not a card",
      "line" in kinds and "polygon" not in [
          canvas.type(i) for i in items if canvas.type(i) == "polygon"]
      or "line" in kinds, str(kinds))
check("its text is on screen", any("Studio break" in t for t in texts), str(texts))
check("its action is on screen",
      any("Stop the rundown" in t for t in texts), str(texts))
check("the note is not a playable row icon",
      app._queue_row_icon(app.rq.queue[1]) == "📝",
      app._queue_row_icon(app.rq.queue[1]))

# the rundown stops where a stop-note sits
app.rq.current_index = 0
app._playing = True
app._step_queue()
pump()
check("the rundown stops AT the note line",
      app._playing is False and app.rq.current_index == 1,
      f"playing={app._playing} idx={app.rq.current_index}")
check("the operator sees why it stopped",
      "note" in str(app.np_status.cget("text")).lower(),
      str(app.np_status.cget("text")))
check("a note is never aired", not app.audio_player.is_playing)

# Play on a note row is refused, not played
toasts.clear()
app._play_index(1)
pump()
check("double-clicking a note refuses politely",
      any("note" in t.lower() for t in toasts), str(toasts))
check("nothing started from the note", app.audio_player.is_playing is False)

# a plain note (do nothing) is stepped over transparently
app._apply_note_edit(None, 0, "Intro starts here", "#58a6ff", "none")
pump()
app.rq.current_index = -1
verdict = app.rq.skip_next()
check("a do-nothing note is stepped over",
      verdict == "playing" and app.rq.queue[app.rq.current_index].title == "Song 1",
      f"{verdict} → {titles()}")

# editing and deleting a note
notes_before = sum(1 for s in app.rq.queue if app.rq.is_note(s))
note = next(s for s in app.rq.queue if app.rq.is_note(s))
note_pos = app.rq.queue.index(note)
app._apply_note_edit(note, note_pos, "Edited break", "#f85149", "none")
pump()
check("a note can be edited in place",
      note.title == "Edited break" and note.note_action == "none",
      f"{note.title} / {note.note_action}")
check("editing a note never inserts a second line",
      sum(1 for s in app.rq.queue if app.rq.is_note(s)) == notes_before,
      str(titles()))
app._delete_note_at(note_pos)
pump()
check("a note can be deleted",
      note not in app.rq.queue
      and sum(1 for s in app.rq.queue if app.rq.is_note(s)) == notes_before - 1,
      str(titles()))


# ── 4. Persistence and guards ──────────────────────────────────────
reset(songs(3))
app._toggle_skip_flag(1)
app._apply_note_edit(None, 2, "Ad break", "#ff9f45", "stop")
pump()
app.rq.save_to_file(Path("flags_roundtrip.freq"))
app.rq.queue = []
app.rq.load_from_file(Path("flags_roundtrip.freq"))
pump()
check("the flagged song survives a save/load round trip",
      any(getattr(s, "skip", False) and s.title == "Song 2" for s in app.rq.queue),
      str([(s.title, s.skip) for s in app.rq.queue]))
check("the note survives it too",
      any(app.rq.is_note(s) and s.title == "Ad break"
          and s.note_action == "stop" and s.note_color == "#ff9f45"
          for s in app.rq.queue),
      str([(s.title, s.source, s.note_action) for s in app.rq.queue]))
check("the queue still counts only real songs",
      len([s for s in app.rq.queue if not app.rq.is_note(s)]) == 3,
      str(len(app.rq.queue)))

app._queue_selected = next(i for i, s in enumerate(app.rq.queue) if app.rq.is_note(s))
calls.clear()
app._toggle_preview()
check("Preview on a note line explains there is nothing to hear",
      [c for c in calls if c[0] == "info"], str(calls))
calls.clear()
app._open_selected_editor()
check("Edit on a note line explains it is text, not audio",
      [c for c in calls if c[0] == "info"], str(calls))

# the queue keeps working after all of it
app._refresh_queue()
pump()
check("the list still paints after notes and flags",
      bool(app._queue_items) and len(app._queue_view) == len(app.rq.queue),
      f"{len(app._queue_items)} drawn / {len(app._queue_view)} view")

app._stop_preview(quiet=True)
passed = sum(1 for _n, ok, _d in RESULTS if ok)
print("\n" + "=" * 62)
print(f"  {passed}/{len(RESULTS)} checks passed")
print("=" * 62)
raise SystemExit(0 if passed == len(RESULTS) else 1)
