"""One-off: build a realistic as-run day, then capture the As-run tab as PNG.

Writes only into a temp folder (never the repo's logs/), walks the day's
clock through a fake ``now()`` so the rows carry believable times, and
leaves one item open on purpose (the "on air right now" row).
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, HERE)
# RadioApp autosaves to a RELATIVE radio_queue.freq — run from a throwaway
# directory so this capture can never rewrite the operator's real preset.
work = tempfile.mkdtemp(prefix="asrun_capture_")
os.chdir(work)

import gui  # noqa: E402
import asrun  # noqa: E402
from PIL import ImageGrab  # noqa: E402

app = gui.RadioApp()
app.asrun.configure(enabled=True, folder=os.path.join(work, "logs", "asrun"))
app.asrun_switch_var.set(True)
app.asrun_switch.configure(text="Record")

today = datetime.now().replace(microsecond=0)


class WalkDT(datetime):
    """A clock that advances one entry per call (start, end, start, end…)."""

    times: list = []
    idx = 0

    @classmethod
    def now(cls, tz=None):
        value = cls.times[min(cls.idx, len(cls.times) - 1)]
        cls.idx += 1
        return value


PLAN = [
    # (kind, title, artist, aired_seconds, ended_why) — None = still on air
    ("anthem", "national anthem", "", 65, "natural"),
    ("music", "รักเธอคนเดียว", "บอย โกสิยพงษ์", 248, "natural"),
    ("jingle", "Station ID", "", 8, "natural"),
    ("music", "ฝนตกที่ปลายฟ้า", "ปาล์มมี่", 232, "natural"),
    ("music", "ความทรงจำ", "Bodyslam", 210, "anthem-cut"),
    ("ad", "โฆษณา-น้ำปลาทิพรส", "", 27, "natural"),
    ("mic", "ช่วงข่าวเช้า", "", 312, "natural"),
    ("music", "คิดถึง", "แสตมป์", 226, "natural"),
    ("ad", "โฆษณา-ประกันภัย", "", 31, "natural"),
    ("music", "รักเดียว", "Tattoo Colour", 198, "jingle-cut"),
    ("jingle", "Jingle เวรเช้า", "", 6, "natural"),
    ("relay", "external stream relay", "", 180, "stopped"),
    ("stream", "FreQ Live", "", 900, "stopped"),
    ("music", "ยังไม่จบ", "ศิลปิน", 240, None),
]

times = []
clock = today.replace(hour=8, minute=0, second=0)
for _kind, _title, _artist, secs, why in PLAN:
    times.append(clock)
    clock = clock + timedelta(seconds=secs + (3 if why else 0))
    times.append(clock)

real_dt = asrun.datetime
asrun.datetime = WalkDT
WalkDT.times = times
try:
    for kind, title, artist, secs, why in PLAN:
        source_type = {"stream": "icecast", "relay": "relay", "ad": "file"}.get(kind, "file")
        item_id = app._asrun_begin(
            kind, title=title, artist=artist, duration=float(secs),
            path="" if kind in ("stream", "relay") else rf"D:\music\{title}.mp3",
            url="Icecast://localhost:8000/stream" if kind == "stream" else "",
            source_type=source_type, channel=kind,
            reason={
                "music": "queue · local file",
                "mic": "live mic from the queue",
                "jingle": "automatic jingle (every 5 songs)",
                "ad": "automatic ad break (every 10 songs)",
                "anthem": "anthem time (automatic)",
                "stream": "broadcast stream started",
                "relay": "playing a fetched relay segment",
            }.get(kind, ""))
        if why is not None:
            app.asrun.end(item_id, why, actual_duration=float(secs))
finally:
    asrun.datetime = real_dt

app._asrun_last_store = None
tab_index = app._tab_names.index("As-run")
app._on_sidebar_tab_switch(tab_index, "As-run")
for _ in range(8):
    app.update()


def bbox_of(widget):
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    return (x, y, x + widget.winfo_width(), y + widget.winfo_height())


app.attributes("-topmost", True)
app.lift()
for _ in range(4):
    app.update()
ImageGrab.grab(bbox_of(app.sidebar_frame)).save(
    os.path.join(HERE, "_ui_asrun_tab_1020.png"))
ImageGrab.grab(bbox_of(app)).save(os.path.join(HERE, "_ui_asrun_1020.png"))

stats = app.asrun.stats()
print("day:", stats["date"], "items:", stats["items"],
      "by_kind:", stats["by_kind"], "open:", stats["open"],
      "chain:", app.asrun.verify()["ok"])
settings = app._collect_settings()
print("settings[asrun]:", settings.get("asrun"))
app.destroy()
