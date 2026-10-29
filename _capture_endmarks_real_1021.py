"""Capture the REAL queue (radio_queue.freq) with the end-of-song marks.

Opens the app exactly as the operator does — same playlist, same window
geometry, same settings — screenshots the window, and closes it again
WITHOUT writing anything back: autosave is switched off before the first
event-loop tick and the window is destroyed instead of closed, so the
playlist file on disk must keep its original mtime.

The four marks are shown on four of the operator's own rows (auto / break /
stop / break+stop) so the layout can be judged; those choices live in
memory only. Row 5+ keeps the real state (＋ = normal).
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)          # load the operator's own radio_queue.freq
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import radio_manager
import gui
from PIL import ImageGrab

PLAYLIST = os.path.join(HERE, "radio_queue.freq")
mtime_before = os.path.getmtime(PLAYLIST)
size_before = os.path.getsize(PLAYLIST)

# The app autosaves on its first event-loop tick (inside RadioApp()), so the
# only reliable way to screenshot without touching the operator's playlist is
# to make the write itself a no-op while this process runs.
radio_manager.RadioQueue.save_to_file = (
    lambda self, path=None, settings=None: None)

app = gui.RadioApp()
# FIRST thing after construction: no autosave tick may touch the playlist.
app._autosave_enabled = False

app.attributes("-topmost", True)
app.lift()
for _ in range(12):
    app.update()
    time.sleep(0.12)

queue = app.rq.queue
print(f"playlist loaded      : {len(queue)} songs in the queue")
print(f"window               : {app.winfo_width()}x{app.winfo_height()}")

# Demonstration states on the operator's own rows (memory only).
demo = ("auto", "break", "stop", "break_stop")
for i, mode in enumerate(demo):
    if i < len(queue):
        queue[i].end_mode = mode
app._refresh_queue()
for _ in range(8):
    app.update()
    time.sleep(0.12)

canvas = app._queue_canvas
width = app._queue_canvas_width()
card_right = max(gui.QUEUE_CARD_PAD + gui.QUEUE_CARD_MIN_W,
                 width - gui.QUEUE_CARD_PAD)
mark_left, mark_right = app._queue_mark_span(card_right)
print(f"queue canvas         : {width} px · card 6..{card_right}")
print(f"mark column          : {mark_left}..{mark_right}")
print(f"text columns end at  : {mark_left - gui.QUEUE_MARK_GAP}")

for pos in sorted(app._queue_items):
    song = app._queue_view[pos][1]
    texts, icons = [], []
    for item in app._queue_items[pos]:
        if canvas.type(item) == "text":
            texts.append((canvas.itemcget(item, "text"), round(canvas.coords(item)[0])))
        elif any(t.startswith("endmark-") for t in canvas.gettags(item)):
            icons.append((canvas.type(item), canvas.bbox(item)))
    print(f"row {pos} [{app._song_end_mode(song):10s}] icons={icons}")
    for _shape, box in icons:
        assert box[0] >= mark_left - 1 and box[2] <= mark_right + 1, box
        assert box[3] - box[1] >= 12, box
    for text, x in texts:
        assert x <= mark_left - 6, (text, x, mark_left)

shot = os.path.join(HERE, "_ui_endmarks_real_1021.png")
x, y = app.winfo_rootx(), app.winfo_rooty()
ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(shot)
app.destroy()

mtime_after = os.path.getmtime(PLAYLIST)
size_after = os.path.getsize(PLAYLIST)
print(f"saved                : {shot}")
print(f"playlist untouched   : {mtime_after == mtime_before} "
      f"({size_before} -> {size_after} bytes)")
