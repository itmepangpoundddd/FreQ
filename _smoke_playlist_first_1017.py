#!/usr/bin/env python3
"""Smoke: playlist import dialog — third option "Add only first video" [first-1017].

Drives the REAL RadioApp + REAL PlaylistRangeDialog (headless Tk) and only
stubs the network layer, so the wiring the buttons actually use is exercised:

  1. the dialog offers exactly three answers and the new option's label is
     "Add only first video in the playlist"
  2. picking it (radiobutton → OK button) returns "first"
  3. Enter confirms / Esc cancels, and a long first-video title is shown
     without clipping the buttons out of the window
  4. the "+ Add Video" button routes a playlist URL into this dialog
  5. result "first" fetches exactly entry #1 (no more), "all" fetches 1..N,
     a range still fetches its slice, cancel fetches nothing
  6. a one-video playlist asks nothing and imports that video
  7. the real extract_playlist_range(entries, 1, 1) touches exactly one video
"""

import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue persists to a cwd-relative file — keep the user's real one intact.
os.chdir(tempfile.mkdtemp(prefix="freq_first_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, until=None, timeout=4.0):
    """Spin the REAL mainloop until `until()` is true (or the timeout expires).

    worker threads call `app.after(0, ...)`; tkinter refuses that while the
    main thread is only calling update(), so the test must behave like the app:
    enter mainloop and quit from a scheduled callback.
    """
    end = time.time() + timeout

    def tick():
        if time.time() > end or (until is not None and until()):
            app.quit()
        else:
            app.after(20, tick)

    app.after(0, tick)
    app.mainloop()
    return until() if until is not None else True


import gui  # noqa: E402
from radio_manager import Song, YouTubeManager  # noqa: E402

ENTRIES = [{"title": f"Video {i}", "url": f"https://www.youtube.com/watch?v=v{i:02d}"}
           for i in range(1, 13)]
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL_test_first_1017"


# ── 1-3. the dialog itself ─────────────────────────────────────
def find_options(widget, found=None):
    """Map radiobutton value → widget (the rows the user actually clicks)."""
    found = {} if found is None else found
    for child in widget.winfo_children():
        try:
            value = child.cget("value")
        except Exception:
            value = None
        if value in ("all", "first", "range"):
            found[value] = child
        find_options(child, found)
    return found


def find_label(widget, text):
    for child in widget.winfo_children():
        try:
            if str(child.cget("text")) == text:
                return child
        except Exception:
            pass
        hit = find_label(child, text)
        if hit is not None:
            return hit
    return None


app = gui.RadioApp()
try:
    app.update_idletasks()
    app.update()

    dlg = gui.PlaylistRangeDialog(app, 12, first_title="Opening Track")
    dlg.update()
    options = find_options(dlg)
    check("dialog offers exactly three answers (all / first / range)",
          set(options) == {"all", "first", "range"}, str(sorted(options)))
    check('the new option is labelled "Add only first video in the playlist"',
          "first" in options
          and options["first"].cget("text") == "Add only first video in the playlist",
          options["first"].cget("text") if "first" in options else "missing")
    check("the playlist's opening track is shown next to the option",
          find_label(dlg, "Opening Track") is not None)

    options["first"].invoke()          # click the radiobutton
    dlg.update()
    check("choosing it relabels the confirm button",
          dlg._ok_btn.cget("text").strip() == "Add First Video",
          dlg._ok_btn.cget("text"))
    dlg._ok_btn.invoke()               # then the real button
    check('confirming returns "first"', dlg.result == "first", str(dlg.result))
    check("confirming closes the dialog", not dlg.winfo_exists())

    # Enter / Esc on a focused dialog.
    dlg = gui.PlaylistRangeDialog(app, 12)
    dlg.update()
    dlg.focus_force()
    app.update()
    dlg._choice.set("first")
    dlg.event_generate("<Return>")
    app.update()
    check("Enter confirms the highlighted choice", dlg.result == "first", str(dlg.result))

    dlg = gui.PlaylistRangeDialog(app, 12)
    dlg.update()
    dlg.focus_force()
    app.update()
    dlg.event_generate("<Escape>")
    app.update()
    check("Esc cancels", dlg.result is None, str(dlg.result))
    check("Esc closes the dialog", not dlg.winfo_exists())

    dlg = gui.PlaylistRangeDialog(app, 250)
    dlg.update()
    check("a large playlist's dialog is not clipped (buttons fit)",
          dlg.winfo_height() >= dlg.winfo_reqheight(),
          f"h={dlg.winfo_height()} req={dlg.winfo_reqheight()}")
    dlg.destroy()

    dlg = gui.PlaylistRangeDialog(app, 12, first_title="A very long opening track title " * 3)
    dlg.update()
    check("a long first-video title does not clip the buttons",
          dlg.winfo_height() >= dlg.winfo_reqheight(),
          f"h={dlg.winfo_height()} req={dlg.winfo_reqheight()}")
    dlg.destroy()

    # ── network stubs (never touch yt-dlp) ─────────────────────
    _Stub = {}


    class StubDialog(tk.Toplevel):
        """Stands in for the picker: records what it was asked, answers preset."""

        preset = None
        instances = 0
        last = {}

        def __init__(self, parent, total, first_title=""):
            super().__init__(parent)
            StubDialog.instances += 1
            StubDialog.last = {"total": total, "first_title": first_title}
            self.result = StubDialog.preset
            self.withdraw()
            self.after(30, self.destroy)


    extract_calls = []

    def fake_extract(entries, start, end):
        extract_calls.append((start, end))
        entry = entries[start - 1]
        return [Song(title=entry.get("title", "?"), artist="Uploader",
                     duration=100.0, source="youtube",
                     url=entry.get("url", ""))]

    real_extract = YouTubeManager.extract_playlist_range
    gui.PlaylistRangeDialog = StubDialog
    YouTubeManager.extract_playlist_range = staticmethod(fake_extract)
    YouTubeManager.count_playlist = classmethod(lambda cls, url: (len(ENTRIES), ENTRIES))
    gui.song_cache.cache_song_background = lambda *a, **k: None

    def import_via_dialog(preset, total=12, entries=None, via_button=False, timeout=4.0):
        """Run one import; returns (dialog instances, songs added, extract calls)."""
        entries = ENTRIES if entries is None else entries
        StubDialog.preset = preset
        StubDialog.instances = 0
        StubDialog.last = {}
        before = len(app.rq.queue)
        del extract_calls[:]
        if via_button:
            app.yt_url_entry.delete(0, "end")
            app.yt_url_entry.insert(0, PLAYLIST_URL)
            app._add_youtube()
        else:
            app._show_playlist_dialog(PLAYLIST_URL, total, entries)
        pump(app, until=lambda: bool(extract_calls) and len(app.rq.queue) > before,
             timeout=timeout)
        # let the main-thread "config/settle" callbacks (button text, toast) run
        pump(app, timeout=0.25)
        return StubDialog.instances, len(app.rq.queue) - before, list(extract_calls)

    # ── 4-5. the Add Video button reaches the picker, "first" fetches #1 ──
    inst, added, calls = import_via_dialog("first", via_button=True)
    check('"+ Add Video" asks the playlist question when given a playlist link',
          inst == 1, f"{inst} dialog(s)")
    check("the dialog is told how many videos the playlist holds",
          StubDialog.last.get("total") == 12, str(StubDialog.last))
    check("the dialog is told which video opens the playlist",
          StubDialog.last.get("first_title") == "Video 1", str(StubDialog.last))
    check('"Add only first video" fetches entry #1 and nothing else',
          calls == [(1, 1)], str(calls))
    check('"Add only first video" adds exactly one song', added == 1, f"{added} added")

    for preset, expected in (("all", (1, 12)), ((2, 5), (2, 5))):
        inst, added, calls = import_via_dialog(preset)
        check(f'preset {preset!r} still fetches {expected}',
              calls == [expected], str(calls))

    inst, added, calls = import_via_dialog(None, timeout=1.5)
    check("cancel fetches nothing and adds nothing",
          calls == [] and added == 0, f"calls={calls} added={added}")
    check("the picker really opened for the playlist", inst == 1, f"{inst} dialog(s)")
    check("cancel restores the Add Playlist button",
          app.btn_yt_playlist.cget("state") == "normal"
          and app.btn_yt_playlist.cget("text") == "📋 Add Playlist",
          f"{app.btn_yt_playlist.cget('state')} / {app.btn_yt_playlist.cget('text')}")

    inst, added, calls = import_via_dialog(None, total=1, entries=ENTRIES[:1], timeout=2.0)
    check("a one-video playlist asks nothing",
          inst == 0, f"{inst} dialog(s)")
    check("a one-video playlist imports that video", calls == [(1, 1)] and added == 1,
          f"calls={calls} added={added}")

    # ── 7. the real range slicer honours start=end=1 ───────────
    seen = []

    class FakeYDL:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=False):
            seen.append(url)
            return {"title": f"Title of {url[-3:]}", "uploader": "UP",
                    "duration": 61.0, "webpage_url": url, "thumbnail": ""}

    YouTubeManager.is_available = classmethod(lambda cls: True)
    YouTubeManager._ydl = staticmethod(lambda: FakeYDL())
    songs = real_extract(ENTRIES, 1, 1)
    check("the real slicer returns exactly one song for #1-#1",
          len(songs) == 1, f"{len(songs)} song(s)")
    check("the real slicer contacts only the FIRST video",
          seen == [ENTRIES[0]["url"]], str(seen))
    check("the returned song is the playlist's opening video",
          bool(songs) and songs[0].url == ENTRIES[0]["url"],
          songs[0].url if songs else "none")
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
