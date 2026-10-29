#!/usr/bin/env python3
"""Smoke: YouTube Mix / radio links [yt-mix-1017].

Reported bug: pasting
``https://www.youtube.com/watch?v=s4ktJNKP3Kk&list=RDEMXVAiPuUmkerumByyZBXHIg&start_radio=1``
into + Add Video popped "No videos found in playlist", although the video
plays fine in a browser.

Cause: the link's ``list=RD…`` is a Mix. yt-dlp answers "This playlist type
is unviewable" and returns zero entries, so the playlist flow could only ever
fail. The link still carries a valid ``v=`` id, so the app now treats a Mix
as the video it opens with — from BOTH buttons.

Checks (real RadioApp + real routing, only the network layer stubbed):
  1. is_mix_playlist / video_url_from classify the reported link correctly
  2. + Add Video on a Mix link adds exactly that video (no picker, no error)
  3. + Add Playlist on a Mix link falls back to the video
  4. a genuinely unreadable playlist (no video id) still reports an error
  5. a normal playlist link keeps routing to the picker (no regression)
"""

import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_yt_mix_"))

RESULTS = []
MIX_URL = ("https://www.youtube.com/watch?v=s4ktJNKP3Kk"
           "&list=RDEMXVAiPuUmkerumByyZBXHIg&start_radio=1")
WATCH = "https://www.youtube.com/watch?v=s4ktJNKP3Kk"
PL_URL = "https://www.youtube.com/playlist?list=PLabc123"


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.5):
    """Run the real mainloop (worker threads call app.after)."""
    end = time.time() + seconds

    def tick():
        if time.time() > end:
            app.quit()
        else:
            app.after(15, tick)

    app.after(0, tick)
    app.mainloop()


import gui  # noqa: E402
from radio_manager import Song, YouTubeManager  # noqa: E402

# ── 1. classification ──────────────────────────────────────────
check("the reported link is recognised as a YouTube Mix",
      YouTubeManager.is_mix_playlist(MIX_URL))
check("a normal playlist link is NOT a Mix",
      not YouTubeManager.is_mix_playlist(PL_URL))
check("a plain watch link is NOT a Mix",
      not YouTubeManager.is_mix_playlist(WATCH))
check("the Mix's video id is extracted",
      YouTubeManager.video_url_from(MIX_URL) == WATCH,
      YouTubeManager.video_url_from(MIX_URL))
check("a youtu.be short link yields its video id",
      YouTubeManager.video_url_from("https://youtu.be/s4ktJNKP3Kk?list=RDabc")
      == WATCH,
      YouTubeManager.video_url_from("https://youtu.be/s4ktJNKP3Kk?list=RDabc"))
check("a bare playlist link has no video id",
      YouTubeManager.video_url_from(PL_URL) == "",
      YouTubeManager.video_url_from(PL_URL))

app = gui.RadioApp()
try:
    app.update_idletasks()
    app.update()

    fetched = []
    counted = []
    errors = []
    toasts = []
    dialogs = {"opened": 0, "preset": None}

    def fake_video_info(url):
        fetched.append(url)
        return Song(title="Mix track", artist="YT", duration=180.0,
                    source="youtube", url=url)

    class StubDialog(tk.Toplevel):
        """Records that the picker opened, then cancels itself."""

        def __init__(self, parent, total, first_title=""):
            super().__init__(parent)
            dialogs["opened"] += 1
            dialogs["preset"] = {"total": total, "first_title": first_title}
            self.result = None
            self.withdraw()
            self.after(30, self.destroy)

    def fake_count(url):
        counted.append(url)
        return (0, [])

    YouTubeManager.extract_video_info = staticmethod(fake_video_info)
    YouTubeManager.count_playlist = staticmethod(fake_count)
    gui.PlaylistRangeDialog = StubDialog
    gui.messagebox.showerror = lambda title, msg, **k: errors.append(msg)
    gui.messagebox.showwarning = lambda title, msg, **k: errors.append(msg)
    gui.song_cache.cache_song_background = lambda *a, **k: None
    app._show_toast = lambda text, **k: toasts.append(text)

    def add_video(url):
        del fetched[:], counted[:], errors[:], toasts[:]
        dialogs["opened"] = 0
        before = len(app.rq.queue)
        app.yt_url_entry.delete(0, "end")
        app.yt_url_entry.insert(0, url)
        app._add_youtube()
        pump(app, 1.2)
        return len(app.rq.queue) - before

    def add_playlist(url):
        del fetched[:], counted[:], errors[:], toasts[:]
        dialogs["opened"] = 0
        before = len(app.rq.queue)
        app.yt_playlist_entry.delete(0, "end")
        app.yt_playlist_entry.insert(0, url)
        app._add_youtube_playlist()
        pump(app, 1.2)
        return len(app.rq.queue) - before

    # ── 2. Add Video with a Mix link ───────────────────────────
    added = add_video(MIX_URL)
    check("+ Add Video on a Mix link adds one song", added == 1, f"{added} added")
    check("the picker never opens for a Mix link", dialogs["opened"] == 0,
          f"{dialogs['opened']} dialog(s)")
    check("the video id is fetched, not the unviewable list",
          fetched == [WATCH], str(fetched))
    # Never even TRIES to list the Mix — otherwise a fallback could mask the
    # regression and the user would wait for a doomed count round-trip.
    check("Add Video does not query the unviewable list at all",
          counted == [], str(counted))
    check("no error dialog is shown", errors == [], str(errors)[:120])
    check("the user is told it was a Mix", any("Mix" in t for t in toasts),
          str(toasts)[:120])

    # ── 3. Add Playlist with a Mix link (count 0) ──────────────
    added = add_playlist(MIX_URL)
    check("+ Add Playlist on a Mix link falls back to the video",
          added == 1 and fetched == [WATCH], f"added={added} fetched={fetched}")
    check("the fallback only happens after the list really came back empty",
          len(counted) == 1, f"{len(counted)} count call(s)")
    check("the fallback shows no error dialog", errors == [], str(errors)[:120])
    check("the playlist entry is cleared after the fallback",
          app.yt_playlist_entry.get() == "", app.yt_playlist_entry.get())

    # ── 4. truly unreadable playlist (no video id) still errors ─
    added = add_playlist(PL_URL)
    check("an unreadable playlist with no video id errors out",
          added == 0 and len(errors) == 1, f"added={added} errors={errors[:1]}")
    check("the error explains Mix/radio links",
          bool(errors) and "Mix" in errors[0], str(errors)[:160])

    # ── 5. a normal playlist still goes to the picker ──────────
    YouTubeManager.count_playlist = staticmethod(
        lambda url: (3, [{"title": "A", "url": "https://youtu.be/a"},
                         {"title": "B", "url": "https://youtu.be/b"},
                         {"title": "C", "url": "https://youtu.be/c"}]))
    added = add_playlist(PL_URL)
    check("a normal playlist opens the picker", dialogs["opened"] == 1,
          f"{dialogs['opened']} dialog(s)")
    check("the picker receives the playlist size",
          dialogs["preset"] and dialogs["preset"]["total"] == 3,
          str(dialogs["preset"]))
    check("cancelling the picker adds nothing", added == 0, f"{added} added")
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
