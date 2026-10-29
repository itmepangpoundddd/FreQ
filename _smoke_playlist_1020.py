#!/usr/bin/env python3
"""Smoke: New playlist + Import from another .freq [playlist-1020].

Drives the real widgets and the real queue model:

  1. the toolbar really has the two new buttons, and pressing them runs the
     feature (found by their label and invoked like a click)
  2. Import APPENDS the songs of another .freq — the item on air keeps its
     slot, the library grows, and the same song is never added twice (file
     path for local files, URL for YouTube, batch and queue both checked)
  3. importing the same file a second time adds nothing
  4. a corrupt / empty / missing file is reported, never silently imported,
     and leaves the queue exactly as it was
  5. New empties the queue and resets the save target, but keeps the library,
     the history and the presets, and writes NOTHING to disk
  6. Ctrl+N / Ctrl+I are bound
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — run from a throwaway dir so the
# user's real radio_queue.freq is never touched.
os.chdir(tempfile.mkdtemp(prefix="freq_playlist_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


from radio_manager import RadioQueue, Song  # noqa: E402


# ── fixtures: two playlists, sharing one song ──────────────────────

def write_playlist(path, songs, *, empty=False):
    """Write a real .freq file (the same layout save_to_file uses)."""
    data = {
        "format": "freq", "version": 2,
        "queue": [] if empty else [s.to_dict() for s in songs],
        "library": [] if empty else [s.to_dict() for s in songs],
        "history": [], "presets": {}, "current_index": -1,
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return Path(path)


work = Path(tempfile.mkdtemp(prefix="playlists_"))
shared = Song(title="เพลงร่วม", artist="ศิลปิน", duration=180.0,
              source="file", file_path="C:/music/shared.mp3")
only_a = Song(title="เพลง A", artist="ศิลปิน A", duration=200.0,
              source="file", file_path="C:/music/a.mp3")
only_b = Song(title="เพลง B", artist="ศิลปิน B", duration=210.0,
              source="file", file_path="C:/music/b.mp3")
yt = Song(title="YT", artist="ช่อง", duration=120.0,
          source="youtube", url="https://youtu.be/abc123")

file_a = write_playlist(work / "a.freq", [shared, only_a])
file_b = write_playlist(work / "b.freq", [only_b, yt])
empty_file = write_playlist(work / "empty.freq", [], empty=True)
broken_file = work / "broken.freq"
broken_file.write_text("{not json", encoding="utf-8")


# ── the model on its own (fast, no widgets) ────────────────────────

q = RadioQueue()
q.queue = [Song(title="เปิดอยู่", duration=100.0, source="file",
                file_path="C:/music/onair.mp3")]
q.current_index = 0
before_sha = hashlib.sha256(Path(file_a).read_bytes()).hexdigest()
lib_before = len(q.library)
result = q.import_songs(q.read_playlist(file_a))
check("read_playlist reads the songs of another file",
      len(q.read_playlist(file_a)) == 2, str(len(q.read_playlist(file_a))))
check("import appends instead of replacing",
      len(q.queue) == 3 and q.queue[0].title == "เปิดอยู่", str(len(q.queue)))
check("import reports what it added", result["added"] == 2 and result["skipped"] == 0,
      str(result))
check("the playing item keeps its slot", q.current_index == 0)
again = q.import_songs(q.read_playlist(file_a))
check("importing the same file twice adds nothing",
      again["added"] == 0 and again["skipped"] == 2, str(again))
check("import left the source file byte-identical",
      hashlib.sha256(Path(file_a).read_bytes()).hexdigest() == before_sha)
check("the imported songs land in the library too",
      len(q.library) == lib_before + 2, str([s.title for s in q.library]))
dup_batch = q.import_songs(q.read_playlist(file_a) + q.read_playlist(file_a))
check("duplicates inside the batch itself are counted once",
      dup_batch["added"] == 0 and dup_batch["skipped"] == 4, str(dup_batch))
check("YouTube items are recognised by URL",
      RadioQueue.song_key(yt)[0] == "youtube"
      and RadioQueue.song_key(yt) == RadioQueue.song_key(
          Song(title="ชื่ออื่น", source="youtube", url="https://youtu.be/abc123")),
      str(RadioQueue.song_key(yt)))
check("a missing file yields no songs instead of raising",
      RadioQueue.read_playlist(work / "nope.freq") == [])
check("a corrupt file yields no songs instead of raising",
      RadioQueue.read_playlist(broken_file) == [])
check("an empty playlist is read as zero songs",
      RadioQueue.read_playlist(empty_file) == [])

q2 = RadioQueue()
q2.queue = [shared, only_a]
q2.library = [shared, only_a, only_b]
q2.history = [only_a]
q2.presets = {"เช้า": [shared.id]}
q2.current_index = 1
q2.new_playlist()
check("new_playlist empties the queue and clears the current index",
      q2.queue == [] and q2.current_index == -1)
check("new_playlist keeps the library, history and presets",
      len(q2.library) == 3 and q2.history == [only_a]
      and q2.presets == {"เช้า": [shared.id]})
check("new_playlist points Save back at the default file",
      Path(q2._save_path).name == "radio_queue.freq", str(q2._save_path))
check("new_playlist wrote nothing to disk",
      not Path("radio_queue.freq").exists()
      and hashlib.sha256(file_a.read_bytes()).hexdigest() == before_sha)

# ── the real widgets: buttons + handlers ───────────────────────────

import gui as gui_mod  # noqa: E402
from gui import RadioApp  # noqa: E402

warnings: list[str] = []
gui_mod.messagebox.askyesno = lambda *a, **k: True
gui_mod.messagebox.showwarning = lambda title, msg="", **k: warnings.append(msg)
gui_mod.messagebox.showinfo = lambda *a, **k: None

app = RadioApp()
toasts: list[str] = []
app._show_toast = lambda text="", **kw: toasts.append(text)


def find_button(text):
    """The real toolbar button carrying this label (None when missing)."""
    stack = list(app.winfo_children())
    while stack:
        widget = stack.pop()
        try:
            if type(widget).__name__ == "CTkButton" and text in (widget.cget("text") or ""):
                return widget
        except Exception:
            pass
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            pass
    return None


def press(button):
    """Click it the way the user does."""
    try:
        button.invoke()
    except AttributeError:                 # older CTk builds
        button._clicked()


try:
    new_btn = find_button("New")
    import_btn = find_button("Import")
    check("toolbar has the New playlist button", new_btn is not None)
    check("toolbar has the Import button", import_btn is not None)
    check("both buttons sit next to Open/Save",
          new_btn is not None and import_btn is not None
          and new_btn.master is import_btn.master
          and new_btn.winfo_manager() == "pack" == import_btn.winfo_manager(),
          str(new_btn.master if new_btn else None))
    check("Ctrl+N and Ctrl+I are bound",
          bool(app.bind("<Control-n>")) and bool(app.bind("<Control-i>")))

    # Import through the button
    app.rq.queue = [Song(title="เปิดอยู่", duration=100.0, source="file",
                         file_path="C:/music/onair.mp3")]
    app.rq.library = list(app.rq.queue)
    app.rq.current_index = 0
    gui_mod.filedialog.askopenfilenames = lambda **k: (str(file_a), str(file_b))
    app._refresh_queue()
    press(import_btn)
    titles = [s.title for s in app.rq.queue]
    check("the Import button appends both files' songs",
          titles == ["เปิดอยู่", "เพลงร่วม", "เพลง A", "เพลง B", "YT"],
          str(titles))
    check("the item on air is still first", app.rq.current_index == 0)
    check("the toast reports the import", any("Imported" in t for t in toasts),
          str(toasts[-1:]))

    # Import the same files again — nothing added
    toasts.clear()
    press(import_btn)
    check("a second import adds nothing",
          len(app.rq.queue) == 5 and any("Imported 0" in t for t in toasts),
          f"{len(app.rq.queue)} {toasts[-1:]}")

    # A file that cannot be read is reported, queue untouched
    warnings.clear()
    toasts.clear()
    gui_mod.filedialog.askopenfilenames = lambda **k: (str(broken_file),)
    press(import_btn)
    check("an unreadable file is reported, not silently ignored",
          bool(warnings) and "broken.freq" in warnings[0], str(warnings)[:160])
    check("an unreadable file leaves the queue exactly as it was",
          len(app.rq.queue) == 5, str(len(app.rq.queue)))

    # Empty playlist: no songs, same warning path, no change
    warnings.clear()
    gui_mod.filedialog.askopenfilenames = lambda **k: (str(empty_file),)
    press(import_btn)
    check("an empty playlist is reported and changes nothing",
          bool(warnings) and len(app.rq.queue) == 5, str(warnings)[:120])

    # New playlist through the button
    app.rq.history = [app.rq.queue[0]]
    app.rq.presets = {"เช้า": [app.rq.queue[1].id]}
    keep_library = len(app.rq.library)
    app.rq._save_path = file_a
    before = hashlib.sha256(file_a.read_bytes()).hexdigest()
    press(new_btn)
    check("the New button empties the queue", app.rq.queue == [],
          str(len(app.rq.queue)))
    check("New keeps the library, history and presets",
          len(app.rq.library) == keep_library and app.rq.history
          and app.rq.presets, f"{len(app.rq.library)} {len(app.rq.history)}")
    check("New resets the save target to the default file",
          Path(app.rq._save_path).name == "radio_queue.freq",
          str(app.rq._save_path))
    check("New does not touch the playlist that was open",
          hashlib.sha256(file_a.read_bytes()).hexdigest() == before)

    # The label the operator sees after New is on the toast, not a modal
    check("New says what it did", any("New playlist" in t for t in toasts),
          str(toasts[-1:]))
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
