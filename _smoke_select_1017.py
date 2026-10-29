#!/usr/bin/env python3
"""Smoke: queue selection + deselect [select-1017].

Drives the REAL RadioApp (headless, silent) instead of a simulated copy of
the selection maths — the 1016.1 suite has a simulated `range_from()` that
kept passing while the real handler crashed with
``TypeError: 'set' object is not subscriptable`` (the state was a set, the
arrow handler read sel[0]/sel[-1]).

Verifies:
  1. clicking a row selects it (single row)
  2. Shift+click / Shift+arrow build an ORDERED range and do not raise
  3. clicking empty playlist space deselects everything
  4. Ctrl+D deselects everything (through the real Tk binding)
  5. deselect is a no-op when nothing is selected, and never rebuilds the
     queue (scroll/playback must not be disturbed)
  6. the Filter button (selection-only UI) hides again after deselect
  7. the rebind path works: Ctrl+D is claimed by the Keys tab, the old
     '<ctrl-d>' spelling really is rejected by Tk, and rebinding leaves no
     stale second shortcut behind
"""

import os
import re
import sys
import tempfile
import time
import tkinter as tk
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq (RadioQueue saves to a PATH
# RELATIVE to the cwd) — run from a throwaway directory.
os.chdir(tempfile.mkdtemp(prefix="freq_select_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.35):
    """Run the Tk event loop for a while (row rendering is batched)."""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def _rejects_invalid_pattern(widget):
    """True when Tk refuses the pattern the old code built for 'Ctrl+D'.

    The pattern must be handed to the BINDING form: the query form
    (``bind . <ctrl-d>``) does not parse the sequence and happily returns
    nothing instead of raising, which is what made the old try/except look
    like a no-op instead of a bug.
    """
    try:
        widget.bind("<ctrl-d>", lambda e: None)
    except tk.TclError:
        return True
    widget.unbind("<ctrl-d>")
    return False


def fire_key_binding(widget, sequence, keysym="d", keycode=68, state=0x4):
    """Deliver `sequence` to `widget` the way Tk does.

    Synthetic key events cannot carry a letter keysym in this session
    ("no keycode for keysym d" — no real keyboard/keysym table), so we call
    the registered Tcl callback with the substitution values Tk itself would
    pass. Same callback, same Event object; only Tk's own pattern matching is
    skipped — which the bind-table assertions below cover separately.
    """
    script = widget.bind(sequence)
    assert script, f"{sequence} is not bound at all"
    match = re.search(r"\[([^\s\]]+)\s", script)
    assert match, f"unexpected bind script: {script!r}"
    callback = match.group(1)
    order = tk.Misc._subst_format
    values = {
        "%#": "1", "%b": "0", "%f": "0", "%h": "0", "%k": str(keycode),
        "%s": str(state), "%t": "0", "%w": "0", "%x": "0", "%y": "0",
        "%A": keysym, "%E": "1", "%K": keysym,
        "%N": str(ord(keysym)) if len(keysym) == 1 else "0",
        "%W": ".", "%T": "2", "%X": "0", "%Y": "0", "%D": "0",
    }
    widget.tk.call(callback, *[values[f] for f in order])


# ── 0. static wiring ────────────────────────────────────────────
from keys_tab import DEFAULT_KEYS  # noqa: E402
from radio_manager import Song  # noqa: E402

check("keys_tab declares a deselect action bound to Ctrl+D",
      DEFAULT_KEYS.get("deselect", ("", "", ""))[1] == "Ctrl+D",
      str(DEFAULT_KEYS.get("deselect")))

from gui import RadioApp, QUEUE_ROW_H  # noqa: E402

app = RadioApp()
try:
    app.update_idletasks()
    for n in range(6):
        app.rq.add_song(Song(title=f"Song {n}", artist="Tester",
                             duration=120.0, source="local"))
    app._refresh_queue()
    pump(app, 0.5)

    check("queue has rows to work with", len(app._queue_rows) >= 6,
          f"{len(app._queue_rows)} row widget(s)")
    check("selection state starts empty",
          not app._queue_selection and app._queue_selected is None)
    check("Keys tab lists the Deselect row (rebindable / resettable)",
          any(r.action_id == "deselect" for r in app._key_rows))

    # ── 1. clicking a row selects it ────────────────────────────
    app._on_queue_click(2)
    check("clicking a row selects exactly that row",
          app._queue_selected == 2 and app._queue_selection == [2],
          f"selected={app._queue_selected} sel={app._queue_selection}")

    # ── 2. shift range is an ordered list and never raises ──────
    shift_event = types.SimpleNamespace(state=0x0001)   # Shift held
    app._on_queue_click(4, event=shift_event)
    check("Shift+click builds the range 2..4 in order",
          app._queue_selection == [2, 3, 4], str(app._queue_selection))
    app._apply_queue_shift_arrow(1)
    check("Shift+Down extends the real range (no TypeError)",
          app._queue_selection == [2, 3, 4, 5], str(app._queue_selection))
    app._apply_queue_shift_arrow(-1)
    check("Shift+Up shrinks the range back from its far end",
          app._queue_selection == [2, 3, 4], str(app._queue_selection))
    check("legacy single-select helper reads the range end safely",
          app._queue_selection_or_none() == 4,
          str(app._queue_selection_or_none()))
    check("Filter button is shown while something is selected",
          app.btn_queue_filter.winfo_manager() != "",
          f"manager={app.btn_queue_filter.winfo_manager()!r}")

    # ── 3. clicking empty playlist space deselects ──────────────
    canvas = getattr(app.queue_frame, "_parent_canvas", None)
    inner = getattr(app.queue_frame, "_parent_frame", None)
    check("empty-space click is bound on the background widgets",
          bool(canvas is not None and inner is not None))
    if canvas is not None:
        canvas.event_generate("<Button-1>", x=8, y=8)
        app.update()
    check("click on empty playlist space deselects everything",
          app._queue_selected is None and app._queue_selection == [],
          f"selected={app._queue_selected} sel={app._queue_selection}")

    # The list is ONE virtualized canvas: rows are canvas items, so there is
    # no per-row widget (and no binding storm), and the canvas maps y → row.
    qcanvas = app._queue_canvas
    check("the queue is drawn on a single canvas",
          bool(qcanvas is not None and qcanvas.winfo_exists()))
    check("that canvas is bound once for all rows",
          bool(qcanvas.bind("<Button-1>"))
          and bool(qcanvas.bind("<Double-Button-1>")))
    check("the canvas spans the whole list (6 rows × row pitch)",
          int(qcanvas.cget("height")) == 6 * QUEUE_ROW_H,
          str(qcanvas.cget("height")))
    check("only the visible cards exist as canvas items",
          0 < len(qcanvas.find_all()) <= len(app._queue_rows) * 8,
          f"items={len(qcanvas.find_all())}")
    check("no queue row is a Tk widget any more",
          all(type(r).__name__ == "QueueRowHandle"
              for r in app._queue_rows.values()),
          str({i: type(r).__name__ for i, r in app._queue_rows.items()}))
    qcanvas.event_generate("<Button-1>", x=60, y=QUEUE_ROW_H // 2)
    app.update()
    check("a click anywhere on a row (its text included) selects it",
          app._queue_selection == [0], str(app._queue_selection))

    # ── 4. Ctrl+D through the real Tk binding ───────────────────
    app._on_queue_click(1)
    app._on_queue_click(3, event=shift_event)
    check("re-selected a range before the shortcut",
          app._queue_selection == [1, 2, 3], str(app._queue_selection))
    check("Ctrl+D is registered as a Tk key sequence",
          bool(app.bind("<Control-d>")), str(app.bind("<Control-d>"))[:44])
    check("the old lowercase '<ctrl-d>' spelling is rejected by Tk "
          "(why the shortcut never fired)",
          _rejects_invalid_pattern(app))
    fire_key_binding(app, "<Control-d>")
    app.update()
    check("Ctrl+D clears the selection",
          app._queue_selected is None and app._queue_selection == [],
          f"selected={app._queue_selected} sel={app._queue_selection}")

    # ── 5. deselect is a no-op when nothing is selected ─────────
    check("deselect reports False when there is nothing selected",
          app._clear_queue_selection() is False)
    app._on_queue_click(0)
    queue_before = len(getattr(app, "_queue_rows", {}))
    check("deselect reports True for a real selection",
          app._clear_queue_selection() is True)
    check("deselect keeps the queue and its rows untouched (no rebuild)",
          len(app.rq.queue) == 6
          and len(getattr(app, "_queue_rows", {})) == queue_before,
          f"rows={len(getattr(app, '_queue_rows', {}))} "
          f"queue={len(app.rq.queue)}")
    check("anchor is dropped too (no stale shift anchor)",
          app._queue_shift_anchor is None)

    # ── 6. selection-only UI hides again ────────────────────────
    pump(app, 0.15)
    check("Filter button hides after deselect",
          app.btn_queue_filter.winfo_manager() == "",
          f"manager={app.btn_queue_filter.winfo_manager()!r}")

    # ── 7. rebinding leaves exactly one shortcut ────────────────
    app._on_queue_click(0)
    app._on_key_change("deselect", "Ctrl+Shift+D")
    check("rebinding deselect to Ctrl+Shift+D binds the new combo",
          bool(app.bind("<Control-Shift-D>")))
    check("the OLD Ctrl+D binding is gone after the rebind (no stale shortcut)",
          not app.bind("<Control-d>"))
    app._on_key_change("deselect", "Ctrl+D")
    app._on_queue_click(1)
    fire_key_binding(app, "<Control-d>")
    check("restoring Ctrl+D works again after the round trip",
          app._queue_selection == [] and app._queue_selected is None,
          str(app._queue_selection))

    # ── 8. remove/move still work with an ordered list ──────────
    app._on_queue_click(1)
    app._on_queue_click(2, event=shift_event)
    app._move_selected(1)
    check("moving a selected block keeps a valid ordered selection",
          app._queue_selection == [2, 3] and app._queue_selected == 2,
          f"selected={app._queue_selected} sel={app._queue_selection}")
    app._remove_selected()
    check("removing the selected block clears the selection",
          app._queue_selected is None and app._queue_selection == []
          and len(app.rq.queue) == 4,
          f"queue={len(app.rq.queue)} sel={app._queue_selection}")
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
