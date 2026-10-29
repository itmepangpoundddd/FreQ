#!/usr/bin/env python3
"""Smoke: queue keyboard navigation + jump to now playing [nav-1018].

Drives the REAL RadioApp (headless, silent) rather than a simulated copy of
the navigation maths. The list is virtualized, so the two things that can go
wrong silently are:

  * the selection moves but the VIEW never follows (the row is off screen,
    which makes the feature useless in a 4,000-song queue), and
  * the key is not consumed, so Up/Down ALSO changes the volume while the
    user is walking down the list.

Verifies:
  1. wiring: the canvas owns Up/Down/Prior/Next/Home/End/Return, the window
     owns Ctrl+J, and the 🎯 Now button calls the same handler
  2. first Down selects the first row, then rows walk one step per key
  3. End / Home land on the last / first VISIBLE row and the view follows
  4. PageDown moves about a screenful (never fewer than 2 rows)
  5. Shift+arrow extends a contiguous range (positions, not raw indexes)
  6. jump-to-now-playing reveals a row far down the list, and refuses with a
     toast (no crash, no lost selection) when nothing plays or the filter
     hides the playing song
  7. real key delivery: with the list focused, Down moves the selection and
     leaves the volume alone; with the window focused, Down still means
     volume (the pre-existing shortcut is untouched)
  8. Enter plays the selected row
"""

import os
import re
import tkinter as tk
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a PATH RELATIVE to the cwd — run somewhere throwaway.
os.chdir(tempfile.mkdtemp(prefix="freq_nav_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.35):
    """Run the Tk event loop for a while (the list follows a poll timer)."""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def fire_key_binding(widget, sequence, keysym="Down", keycode=116, state=0):
    """Run the REAL Tcl callback Tk registered for `sequence`.

    A headless session cannot grant OS focus, and Tk delivers key events to
    the focus window, so synthetic keypresses are not reliable here. The
    callback below is the exact script Tk runs when the key is pressed on a
    live desktop; only Tk's own pattern matching is skipped.
    """
    script = widget.bind(sequence)
    assert script, f"{sequence} is not bound at all"
    match = re.search(r"\[([^\s\]]+)\s", script)
    assert match, f"unexpected bind script: {script!r}"
    values = {
        "%#": "1", "%b": "0", "%f": "0", "%h": "0", "%k": str(keycode),
        "%s": str(state), "%t": "0", "%w": "0", "%x": "0", "%y": "0",
        "%A": keysym, "%E": "1", "%K": keysym,
        "%N": "0", "%W": ".", "%T": "2",
        "%X": "0", "%Y": "0", "%D": "0",
    }
    widget.tk.call(match.group(1),
                   *[values[f] for f in tk.Misc._subst_format])


def canvas_script(app, sequence):
    return app._queue_canvas.bind(sequence)


def drawn_positions(app):
    return sorted(getattr(app, "_queue_items", {}) or {})


import types  # noqa: E402

from radio_manager import Song  # noqa: E402
from gui import RadioApp, QUEUE_ROW_H  # noqa: E402

app = RadioApp()
toasts = []
app._show_toast = lambda text, duration=3000: toasts.append(text)
plays = []
app._play_index = lambda index, *a, **k: plays.append(index)

TOTAL = 300
try:
    app.update_idletasks()
    for n in range(TOTAL):
        app.rq.add_song(Song(title=f"Song {n}", artist="Tester",
                             duration=120.0, source="local"))
    app._refresh_queue()
    pump(app, 0.6)

    canvas = app._queue_canvas

    # ── 1. wiring ───────────────────────────────────────────────
    for sequence in ("<Up>", "<Down>", "<Prior>", "<Next>", "<Home>", "<End>",
                     "<Return>", "<Shift-Up>", "<Shift-Down>"):
        check(f"list canvas owns {sequence}", bool(canvas_script(app, sequence)),
              str(canvas_script(app, sequence)))
    check("the window owns Ctrl+J (jump to now playing)",
          bool(app.bind("<Control-j>")))
    check("the 🎯 Now button exists in the queue header",
          hasattr(app, "btn_queue_now") and bool(app.btn_queue_now.winfo_exists()),
          str(getattr(app, "btn_queue_now", None)))

    # ── 2. walking the rows ─────────────────────────────────────
    check("nothing selected at startup",
          app._queue_selected is None and not app._queue_selection,
          f"selected={app._queue_selected}")

    app._queue_nav_rows(1)
    check("the first Down selects the first row (no selection yet)",
          app._queue_selected == 0, f"selected={app._queue_selected}")
    check("the selection list holds exactly that row",
          app._queue_selection == [0], str(app._queue_selection))

    for _ in range(5):
        app._queue_nav_rows(1)
    check("five more Downs walk five rows",
          app._queue_selected == 5, f"selected={app._queue_selected}")
    app._queue_nav_rows(-1)
    check("Up walks back one row", app._queue_selected == 4,
          f"selected={app._queue_selected}")
    check("each step consumes the key (returns 'break')",
          app._queue_nav_rows(-1) == "break")

    # ── 3. edges + the view follows ─────────────────────────────
    parent = app.queue_frame._parent_canvas
    # The selection walked past the fold once (6 rows > 5 visible), so the
    # view may sit one row down — it must not have jumped anywhere far.
    before = parent.yview()[0]
    check("the view is still near the top after a short walk",
          before < 0.01, f"yview={before}")

    app._queue_nav_edge(1)                       # End
    pump(app, 0.2)
    check("End selects the last row",
          app._queue_selected == TOTAL - 1, f"selected={app._queue_selected}")
    check("End scrolls the list (it did not before)",
          parent.yview()[0] > 0.5, f"yview={parent.yview()[0]:.3f}")
    last_pos = app._queue_pos_by_index[app._queue_selected]
    check("the row End selected is actually DRAWN",
          last_pos in drawn_positions(app),
          f"pos={last_pos} drawn={drawn_positions(app)}")
    check("a selected row below the fold is fully on screen",
          last_pos >= app._queue_visible_top()
          and last_pos < app._queue_visible_top() + app._queue_viewport_rows(),
          f"pos={last_pos} top={app._queue_visible_top()} "
          f"rows={app._queue_viewport_rows()}")

    app._queue_nav_edge(-1)                      # Home
    pump(app, 0.2)
    check("Home selects row 0 and returns to the top",
          app._queue_selected == 0 and parent.yview()[0] <= 0.001,
          f"selected={app._queue_selected} yview={parent.yview()[0]:.3f}")

    # ── 4. a screenful per PageDown ─────────────────────────────
    rows = app._queue_viewport_rows()
    app._queue_nav_page(1)
    moved = app._queue_selected
    check("PageDown moves about a screenful of rows",
          max(2, rows - 1) == moved, f"moved={moved} viewport_rows={rows}")
    pos = app._queue_pos_by_index[app._queue_selected]
    check("PageDown left the row visible",
          pos in drawn_positions(app), f"pos={pos}")
    app._queue_nav_page(-1)
    check("PageUp comes back to row 0", app._queue_selected == 0,
          f"selected={app._queue_selected}")

    # ── 5. Shift+arrow extends a range ──────────────────────────
    app._queue_select_index(2)                   # like a click on row 2
    app._queue_nav_rows(3, extend=True)
    check("Shift+arrow builds a contiguous range from the anchor",
          app._queue_selection == [2, 3, 4, 5], str(app._queue_selection))
    check("the moving end of the range is the selection",
          app._queue_selected == 5, f"selected={app._queue_selected}")
    app._queue_nav_rows(-1)
    check("a plain arrow collapses the range again",
          app._queue_selection == [4] and app._queue_shift_anchor is None,
          f"sel={app._queue_selection} anchor={app._queue_shift_anchor}")

    # ── 6. jump to now playing ──────────────────────────────────
    app.rq.current_index = 180
    app._refresh_queue_markers()
    parent.yview_moveto(0.0)
    app._queue_select_index(0)
    pump(app, 0.2)
    check("the view is at the top while #180 plays further down",
          parent.yview()[0] <= 0.001)

    toasts.clear()
    app._queue_jump_to_playing()
    pump(app, 0.2)
    check("Ctrl+J selects the song on air",
          app._queue_selected == 180, f"selected={app._queue_selected}")
    check("Ctrl+J scrolled to it",
          parent.yview()[0] > 0.4, f"yview={parent.yview()[0]:.3f}")
    check("the playing row is drawn after the jump",
          app._queue_pos_by_index[180] in drawn_positions(app),
          str(drawn_positions(app)))
    check("the jump tells the user where it went",
          any("#181" in t for t in toasts), str(toasts))

    toasts.clear()
    app.rq.current_index = -1                     # nothing playing
    app._queue_jump_to_playing()
    check("jumping with nothing playing only toasts (no crash, no deselect)",
          app._queue_selected == 180 and any("Nothing is playing" in t
                                             for t in toasts),
          f"selected={app._queue_selected} toasts={toasts}")

    app.rq.current_index = 120
    app._queue_select_index(0)
    toasts.clear()
    app.btn_queue_now.invoke()
    pump(app, 0.2)
    check("the 🎯 button does exactly what Ctrl+J does",
          app._queue_selected == 120 and any("#121" in t for t in toasts),
          f"selected={app._queue_selected} toasts={toasts}")

    app.rq.current_index = 180
    app._queue_filter = "song 5"                  # hides #180 (Song 180)
    app._refresh_queue()
    pump(app, 0.3)
    toasts.clear()
    app._queue_jump_to_playing()
    check("a filter that hides the playing song is reported, not ignored",
          any("hidden" in t for t in toasts), str(toasts))
    check("the filtered view really does not contain #180",
          180 not in app._queue_pos_by_index)
    app._queue_nav_edge(1)
    check("navigation respects the filtered list (End = last match)",
          app._queue_view[-1][0] == app._queue_selected,
          f"selected={app._queue_selected} view_last={app._queue_view[-1][0]}")
    app._queue_filter = ""
    app._refresh_queue()
    pump(app, 0.3)

    # ── 7. the real bind scripts (focus-independent) ──────────────
    # Clicking a row must move focus to the list, otherwise the arrows never
    # reach it and the whole feature is unreachable with the mouse.
    app._queue_canvas.yview_moveto(0.0)
    asked = []
    real_focus_set = app._queue_canvas.focus_set
    app._queue_canvas.focus_set = lambda *a: (asked.append(1), real_focus_set(*a))[1]
    app._on_queue_canvas_press(types.SimpleNamespace(
        x=120, y=int(2.5 * QUEUE_ROW_H), state=0, x_root=0, y_root=0))
    app._queue_canvas.focus_set = real_focus_set
    pump(app, 0.1)
    check("clicking a row asks the list to take the keyboard focus",
          bool(asked), f"focus_set calls={len(asked)}")
    check("that click still selects the row it landed on",
          app._queue_selected == 2, f"selected={app._queue_selected}")

    app._queue_select_index(3)
    volume_before = app.volume_slider.get()
    fire_key_binding(canvas, "<Down>")
    pump(app, 0.15)
    check("the list's own <Down> callback moves the selection",
          app._queue_selected == 4, f"selected={app._queue_selected}")
    check("…and the handler returns 'break' (the volume key never fires)",
          app._on_queue_canvas_key(types.SimpleNamespace(keysym="Down")) == "break",
          repr(app._on_queue_canvas_key(types.SimpleNamespace(keysym="Down"))))
    for keysym, sequence in (("Up", "<Up>"), ("Prior", "<Prior>"),
                             ("Next", "<Next>"), ("Home", "<Home>"),
                             ("End", "<End>")):
        check(f"{sequence} is consumed too (returns 'break')",
              app._on_queue_canvas_key(types.SimpleNamespace(keysym=keysym)) == "break")

    volume_before = app.volume_slider.get()
    app._queue_select_index(4)
    fire_key_binding(app, "<Down>")
    pump(app, 0.15)
    check("the window's <Down> still means volume (unchanged)",
          app.volume_slider.get() < volume_before,
          f"{volume_before} → {app.volume_slider.get()}")
    check("…and it leaves the queue selection alone",
          app._queue_selected == 4, f"selected={app._queue_selected}")

    # ── 8. Enter plays ──────────────────────────────────────────
    app._queue_select_index(7)
    plays.clear()
    fire_key_binding(canvas, "<Return>", keysym="Return", keycode=36)
    pump(app, 0.15)
    check("Enter plays the selected row", plays == [7], str(plays))

finally:
    app.destroy()

failed = [name for name, ok, _ in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:")
    for name in failed:
        print("  -", name)
sys.exit(1 if failed else 0)
