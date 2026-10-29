#!/usr/bin/env python3
"""Smoke: Browse Audio takes SEVERAL files, and 🎲 covers that path too.

Drives the real Add-tab button and the real dialog hook:

  1. the button says it can pick several and is wired to `_pick_file`
  2. `_pick_file` opens the MULTI-select dialog (askopenfilenames), not the
     single-file one
  3. three picked files all land in the queue, in the picked order, with the
     filename as the title and a real file path
  4. a single file still works even when Tk hands back a bare string
  5. cancel / empty selection adds nothing and never raises
  6. with 🎲 Random positions ON every picked file lands AHEAD of the item on
     air (never on or before it), and with it OFF they go to the end in order
  7. the picker writes nothing: the files stay byte-identical and no queue
     file appears on disk
"""

import hashlib
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_browse_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ── fixtures: real (tiny) WAV files so mutagen/pygame see a real file ──
work = Path(tempfile.mkdtemp(prefix="browse_audio_"))


def make_wav(path: Path, seconds: float = 0.2) -> Path:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(8000)
        frames = int(8000 * seconds)
        fh.writeframes(b"".join(struct.pack("<h", 0) for _ in range(frames)))
    return path


files = [make_wav(work / f"{name}.wav") for name in ("Track One", "Track Two", "Track Three")]
digests = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}

# ── the real app ──────────────────────────────────────────────────
import gui as gui_mod  # noqa: E402
from gui import RadioApp  # noqa: E402
from radio_manager import Song  # noqa: E402

calls = []
toasts = []
gui_mod.messagebox.showinfo = lambda *a, **k: calls.append(("info", a))
gui_mod.messagebox.showwarning = lambda *a, **k: calls.append(("warn", a))
gui_mod.messagebox.showerror = lambda *a, **k: calls.append(("error", a))

dialog_answer = {"value": ()}


def fake_multi(**kwargs):
    calls.append(("multi", kwargs))
    return dialog_answer["value"]


def fake_single(**kwargs):
    calls.append(("single", kwargs))
    return ""


gui_mod.filedialog.askopenfilenames = fake_multi
gui_mod.filedialog.askopenfilename = fake_single

app = RadioApp()
app._show_toast = lambda text="", **kw: toasts.append(text)


def on_air():
    app.rq.queue = [Song(title="on air", duration=100.0, source="file",
                         file_path="C:/music/onair.mp3")]
    app.rq.library = list(app.rq.queue)
    app.rq.current_index = 0
    app._refresh_queue()


def pick(paths):
    calls.clear()
    toasts.clear()
    dialog_answer["value"] = paths
    app._pick_file()


def kinds():
    return [c[0] for c in calls]


# 1. the button
check("the Add tab has the Browse Audio button",
      hasattr(app, "browse_audio_btn") and app.browse_audio_btn.winfo_exists())
check("its label says it can pick several",
      "several" in (app.browse_audio_btn.cget("text") or "").lower(),
      app.browse_audio_btn.cget("text"))
check("the button is wired to _pick_file",
      "pick_file" in str(app.browse_audio_btn.cget("command")))

# 2. it opens the multi-select dialog
on_air()
app._queue_random_insert = False
pick(tuple(str(f) for f in files))
check("the picker is the MULTI-select dialog", "multi" in kinds(), str(kinds()))
check("the single-file dialog is not used", "single" not in kinds(), str(kinds()))
check("the dialog asks for several files (Ctrl/Shift hint)",
      "several" in str(calls[0][1].get("title", "")).lower(),
      str(calls[0][1].get("title")))
check("the dialog still filters to audio formats",
      any("*.mp3" in str(ft) for ft in calls[0][1].get("filetypes", [])),
      str(calls[0][1].get("filetypes"))[:80])

# 3. all three land in the queue, in order, at the end
titles = [s.title for s in app.rq.queue]
check("all three picked files reach the queue",
      titles == ["on air", "Track One", "Track Two", "Track Three"], str(titles))
check("each queued item keeps its real file path",
      [s.file_path for s in app.rq.queue[1:]] == [str(f) for f in files],
      str([s.file_path for s in app.rq.queue[1:]]))
check("each queued item is a local file song",
      all(s.source == "file" for s in app.rq.queue[1:]),
      str([s.source for s in app.rq.queue[1:]]))
check("a known length is read from the real files",
      all(abs(s.duration - 0.2) < 0.05 for s in app.rq.queue[1:]),
      str([round(s.duration, 3) for s in app.rq.queue[1:]]))
check("the label summarises the batch",
      "3 files" in (app.file_label.cget("text") or ""), app.file_label.cget("text"))
check("the toast reports the batch and the queue length",
      any("Added 3 files" in t and "Queue: 4" in t for t in toasts), str(toasts[-1:]))

# 4. a single file, handed back as a bare string
on_air()
calls.clear()
toasts.clear()
gui_mod.filedialog.askopenfilenames = lambda **k: (calls.append(("multi", k)),
                                                   str(files[0]))[1]
app._pick_file()
check("one picked file still works (bare string from Tk)",
      [s.title for s in app.rq.queue] == ["on air", "Track One"],
      str([s.title for s in app.rq.queue]))
check("a single pick is labelled by name",
      (app.file_label.cget("text") or "").endswith("Track One"),
      app.file_label.cget("text"))
gui_mod.filedialog.askopenfilenames = fake_multi

# 5. cancel / empty
on_air()
pick(())
check("cancelling the dialog adds nothing",
      [s.title for s in app.rq.queue] == ["on air"], str(len(app.rq.queue)))
before_label = app.file_label.cget("text")
pick(("", "   "))
check("an empty/garbage answer adds nothing and does not raise",
      [s.title for s in app.rq.queue] == ["on air"]
      and app.file_label.cget("text") == before_label,
      str(len(app.rq.queue)))

# 6. 🎲 Random positions covers the picker
on_air()
app.random_insert_switch.toggle()          # the real switch, like a click
check("🎲 is ON through the real switch", app._queue_random_insert_on())
on_air()
pick(tuple(str(f) for f in files))
landed = [s.title for s in app.rq.queue]
check("🎲 keeps the item on air in slot 0", landed[0] == "on air", str(landed))
check("🎲 never drops a picked file on or before the item on air",
      all(i > app.rq.current_index
          for i, s in enumerate(app.rq.queue) if s.title != "on air"),
      str([(i, s.title) for i, s in enumerate(app.rq.queue)]))
check("🎲 scatters all three files into the upcoming queue",
      sorted(landed) == sorted(["on air", "Track One", "Track Two", "Track Three"]),
      str(landed))
check("🎲 says it scattered in the toast",
      any("scattered" in t for t in toasts), str(toasts[-1:]))

# scatter, not strictly append: with 3 files the order is shuffled at least once
scatter_orders = set()
for _ in range(12):
    on_air()
    pick(tuple(str(f) for f in files))
    scatter_orders.add(tuple(s.title for s in app.rq.queue))
check("🎲 really randomises the landing order (not a plain append)",
      len(scatter_orders) > 1, f"{len(scatter_orders)} distinct orders")

app.random_insert_switch.toggle()
check("🎲 OFF again appends at the end", not app._queue_random_insert_on())
on_air()
pick(tuple(str(f) for f in files))
check("with 🎲 OFF the batch is appended in picked order",
      [s.title for s in app.rq.queue] ==
      ["on air", "Track One", "Track Two", "Track Three"],
      str([s.title for s in app.rq.queue]))

# 7. nothing on disk changed
check("the picked files are byte-identical",
      all(hashlib.sha256(f.read_bytes()).hexdigest() == digests[f] for f in files))
check("the picker writes nothing next to the audio files",
      sorted(p.name for p in work.iterdir()) ==
      sorted(f"{n}.wav" for n in ("Track One", "Track Two", "Track Three")),
      str(sorted(p.name for p in work.iterdir())))

# ── summary ───────────────────────────────────────────────────────
try:
    app.destroy()
except Exception:
    pass

passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"\n{passed}/{len(RESULTS)} passed")
sys.exit(0 if passed == len(RESULTS) else 1)
