"""One-off: capture the toolbar (with the new New / Import buttons) as PNG.

Runs from a throwaway folder holding a COPY of the preset: launching the app
makes its autosave write radio_queue.freq within the first second, and the
operator's real playlist must never be rewritten by a screenshot tool.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_scratch = Path(tempfile.mkdtemp(prefix="freq_toolbar_shot_"))
_real = HERE / "radio_queue.freq"
if _real.exists():
    shutil.copy2(_real, _scratch / "radio_queue.freq")
os.chdir(_scratch)

import gui  # noqa: E402

from PIL import ImageGrab  # noqa: E402

app = gui.RadioApp()
app.attributes("-topmost", True)
app.lift()
for _ in range(8):
    app.update()
    time.sleep(0.12)


def bbox_of(widget, pad=0):
    x = widget.winfo_rootx() - pad
    y = widget.winfo_rooty() - pad
    return (x, y, x + widget.winfo_width() + pad * 2,
            y + widget.winfo_height() + pad * 2)


app.update()
time.sleep(0.5)
app.update()
ImageGrab.grab(bbox_of(app)).save(str(HERE / "_ui_toolbar_1020_full.png"))

# Zoom on the file-operations cluster (New / Open / Import / Save / Save As)
buttons = []


def walk(widget):
    for child in widget.winfo_children():
        try:
            if type(child).__name__ == "CTkButton":
                buttons.append(child)
        except Exception:
            pass
        walk(child)


walk(app)
target = None
for btn in buttons:
    try:
        if "Prepared" in (btn.cget("text") or ""):
            target = btn
    except Exception:
        pass
    try:
        if "New" in (btn.cget("text") or "") and "Save" not in (btn.cget("text") or ""):
            target = btn
            break
    except Exception:
        pass

if target is not None:
    frame = target.master
    ImageGrab.grab(bbox_of(frame, pad=6)).save(
        str(HERE / "_ui_toolbar_1020.png"))
    print("captured file-ops cluster:", [b.cget("text") for b in frame.winfo_children()
                                         if type(b).__name__ == "CTkButton"])
else:
    print("NEW BUTTON NOT FOUND")

print("captured", len(buttons), "buttons in the window")
app.destroy()
