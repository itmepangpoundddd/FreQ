"""Capture the queue rows with the end-of-song mark, and measure the layout.

Rows 1..4 carry one mode each (auto / break / stop / break_stop) so the
image shows all four marks; the numbers printed here are what the painter
actually drew (mark column vs. the clock / length / title columns).
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(tempfile.mkdtemp(prefix="freq_capture_endmark_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gui
from radio_manager import Song
from PIL import ImageGrab

app = gui.RadioApp()
app.attributes("-topmost", True)
app.lift()
for _ in range(8):
    app.update()
    time.sleep(0.15)

titles = [
    ("ใจเป็นนาย กายเป็นบ่าว - เล็ก รัชเมศฐ์ [Official MV]", "Genierock", "auto"),
    ("คุยได้ - POLYCAT [Official MV]", "Genierock", "break"),
    ("乾坤 - COCKTAIL x ศิริพร [JOOX 100x100]", "Genierock", "stop"),
    ("บุษบา - member [Official MV]", "ลุลา", "break_stop"),
    ("ใจเหนือเหมือ", "Silly Fools", "auto"),
]
app.rq.queue = [
    Song(title=t, artist=a, duration=300.0 + i * 37, source="local", id=f"c{i}",
         end_mode=m)
    for i, (t, a, m) in enumerate(titles)
]
app.rq.current_index = 0
app._playing = True
app._refresh_queue()
for _ in range(8):
    app.update()
    time.sleep(0.15)

canvas = app._queue_canvas
width = app._queue_canvas_width()
card_right = max(gui.QUEUE_CARD_PAD + gui.QUEUE_CARD_MIN_W,
                 width - gui.QUEUE_CARD_PAD)
mark_left, mark_right = app._queue_mark_span(card_right)
print(f"canvas width   : {width}")
print(f"card           : {gui.QUEUE_CARD_PAD}..{card_right}")
print(f"mark column    : {mark_left}..{mark_right}")
print(f"text columns   : end at {mark_left - gui.QUEUE_MARK_GAP}")

# What is drawn, per row — the mark is now vector geometry (tagged), and
# every text column has to stop short of it.
for pos in sorted(app._queue_items):
    song = app._queue_view[pos][1]
    texts, icons = [], []
    for item in app._queue_items[pos]:
        if canvas.type(item) == "text":
            texts.append((canvas.itemcget(item, "text"), round(canvas.coords(item)[0])))
        elif any(t.startswith("endmark-") for t in canvas.gettags(item)):
            icons.append((canvas.type(item), canvas.bbox(item)))
    print(f"row {pos} [{app._song_end_mode(song):10s}] icons={icons}")
    print(f"        texts={texts}")
    assert icons, "no mark drawn"
    for _shape, box in icons:
        assert box[0] >= mark_left - 1 and box[2] <= mark_right + 1, box
        assert box[3] - box[1] >= 12, box
    for text, x in texts:
        assert x is None or x <= mark_left - 6, (text, x, mark_left)

x, y = app.winfo_rootx(), app.winfo_rooty()
shot = os.path.join(HERE, "_ui_endmarks_1021.png")
ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(shot)
app.destroy()
print(f"saved {shot}")
