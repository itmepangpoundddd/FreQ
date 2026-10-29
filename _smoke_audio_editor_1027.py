#!/usr/bin/env python3
"""Smoke suite for the clip editor: native DSP, undo, save, monitor, UI.

Run:  python _smoke_audio_editor_1027.py

The numeric checks run the REAL native .pyd on a generated stereo WAV; the UI
checks build the REAL window (and the REAL RadioApp, to prove the Edit button
is wired into the queue panel) and drive the same handlers a click would.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS = 0
FAIL: list[str] = []


def check(name: str, ok, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL {name}" + (f"  ({detail})" if detail else ""))


def write_wav(path: str, seconds: float = 2.0, rate: int = 44100) -> None:
    """Stereo 440 Hz test tone: left 0.5, right 0.25 (peak = -6.02 dBFS)."""
    frames = int(seconds * rate)
    data = bytearray()
    for i in range(frames):
        v = math.sin(2 * math.pi * 440 * i / rate)
        data += struct.pack("<hh", int(v * 16384), int(v * 8192))
    with open(path, "wb") as fp:
        fp.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt ")
        fp.write(struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16))
        fp.write(b"data" + struct.pack("<I", len(data)) + data)


def near(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol


def test_native(path: str) -> None:
    print("\n[native DSP]")
    import native_editor as ne

    check("module exposes Editor + list_devices",
          hasattr(ne, "Editor") and hasattr(ne, "list_devices"))
    ed = ne.Editor(path)
    check("decoded length", ed.frames == 88200, str(ed.frames))
    check("decoded rate", ed.sample_rate == 44100, str(ed.sample_rate))
    check("decoded channels", ed.channels == 2, str(ed.channels))
    check("duration property", near(ed.duration, 2.0, 0.01), str(ed.duration))
    check("peak of the test tone is -6 dBFS",
          near(ed.stats(0, ed.frames)["peak_db"], -6.02, 0.1),
          str(ed.stats(0, ed.frames)))

    peaks = ed.peaks(0, ed.frames, 64)
    check("peaks returns two numbers per bucket", len(peaks) == 128, str(len(peaks)))
    check("peaks span the signal",
          near(max(peaks), 0.375, 0.02) and near(min(peaks), -0.375, 0.02))

    # gain +4 dB then -4 dB restores the peak
    before = ed.stats(0, ed.frames)["peak_db"]
    ed.gain(0, ed.frames, 4.0)
    check("gain +4 dB", near(ed.stats(0, ed.frames)["peak_db"], before + 4, 0.05))
    ed.gain(0, ed.frames, -4.0)
    check("gain -4 dB brings it back",
          near(ed.stats(0, ed.frames)["peak_db"], before, 0.05))

    # normalize to -3 dBFS
    applied = ed.normalize(0, ed.frames, -3.0)
    check("normalize reports the applied gain", near(applied, 3.02, 0.1), str(applied))
    check("normalize hits -3 dBFS",
          near(ed.stats(0, ed.frames)["peak_db"], -3.0, 0.05))
    ed.gain(0, ed.frames, -applied)     # undo by hand

    # linear fade in: quiet at the start, full at the end
    ed.fade(0, 44100, 0, 1)
    head = ed.stats(0, 4410)["rms_db"]
    tail = ed.stats(40000, 44100)["rms_db"]
    check("linear fade in ramps up", tail - head > 20, f"{head:.1f} -> {tail:.1f}")

    # reverse of a constant tone stays the same level
    level = ed.stats(0, 44100)["rms_db"]
    ed.reverse(0, 44100)
    check("reverse keeps the level", near(ed.stats(0, 44100)["rms_db"], level, 0.01))

    # delete / insert / paste geometry
    n = ed.frames
    ed.delete(1000, 3000)
    check("delete removes frames", ed.frames == n - 2000, str(ed.frames))
    ed.insert_silence(0, 500)
    check("insert silence adds frames", ed.frames == n - 1500, str(ed.frames))
    check("inserted silence is silent",
          ed.stats(0, 500)["peak_db"] <= -100, str(ed.stats(0, 500)))
    ed.copy(0, 500)
    check("clipboard length", ed.clip_frames == 500, str(ed.clip_frames))
    ed.paste(ed.frames)
    check("paste appends at the end", ed.frames == n - 1000, str(ed.frames))

    out = path.replace(".wav", "_native.wav")
    ed.save_wav(out)
    check("save_wav writes a file", os.path.getsize(out) > 44, str(os.path.getsize(out)))
    again = ne.Editor(out)
    check("saved WAV round-trips the frame count", again.frames == ed.frames,
          f"{again.frames} != {ed.frames}")
    check("saved WAV keeps 44.1 kHz stereo",
          again.sample_rate == 44100 and again.channels == 2)
    del again, ed


def test_session(path: str) -> None:
    print("\n[session / undo]")
    from audio_editor import EditSession

    s = EditSession(path)
    n = s.frames
    base = s.stats(0, n)["peak_db"]

    # A fade keeps the PEAK (the loud end of the ramp) and lowers the RMS,
    # so the check has to watch the energy, not the peak.
    base_rms = s.stats(0, 8820)["rms_db"]
    s.fade(0, 8820, 3, True)
    faded_rms = s.stats(0, 8820)["rms_db"]
    check("fade changes the selection", faded_rms < base_rms - 1.0,
          f"{base_rms:.1f} -> {faded_rms:.1f}")
    check("session reports dirty", s.dirty)
    s.undo()
    check("undo restores the fade", near(s.stats(0, 8820)["rms_db"], base_rms, 0.01))
    s.redo()
    check("redo re-applies the fade", near(s.stats(0, 8820)["rms_db"], faded_rms, 0.01))
    s.undo()

    s.delete(1000, 2000)
    check("delete shortens", s.frames == n - 1000, str(s.frames))
    s.undo()
    check("undo delete restores the length", s.frames == n, str(s.frames))
    s.redo()
    check("redo delete shortens again", s.frames == n - 1000)
    s.undo()

    s.copy(0, 4410)
    s.paste(8820)
    check("paste lengthens", s.frames == n + 4410, str(s.frames))
    s.undo()
    check("undo paste removes exactly the paste", s.frames == n, str(s.frames))
    s.redo()
    check("redo paste re-inserts audio", s.frames == n + 4410)
    s.undo()

    s.trim_to(4410, 8820)
    check("trim keeps only the selection", s.frames == 4410, str(s.frames))
    s.undo()
    s.undo()
    check("undo trim restores the whole track", s.frames == n, str(s.frames))

    s.insert_silence(0, 441)
    check("insert silence lengthens", s.frames == n + 441)
    s.undo()
    check("undo insert silence", s.frames == n, str(s.frames))

    s.gain(0, 4410, -6.0)
    g = s.stats(0, 4410)["peak_db"]
    s.undo()
    check("undo gain", near(s.stats(0, 4410)["peak_db"], base, 0.01), str(g))

    applied = s.normalize(0, n, -2.0)
    check("normalize reaches the target",
          near(s.stats(0, n)["peak_db"], -2.0, 0.05), str(applied))
    s.undo()
    check("undo normalize", near(s.stats(0, n)["peak_db"], base, 0.01))

    s.reverse(0, n)
    s.undo()
    check("undo reverse", near(s.stats(0, n)["peak_db"], base, 0.01))

    # An uninterrupted run of edits must undo back to exactly the start.
    for _ in range(20):
        s.insert_silence(0, 100)
    check("20 inserts lengthen the track", s.frames == n + 2000, str(s.frames))
    while s.can_undo:
        s.undo()
    check("everything undoes back to the original length", s.frames == n, str(s.frames))

    # History is bounded: older steps fall off instead of growing forever.
    for _ in range(60):
        s.insert_silence(0, 100)
    check("undo history is bounded", len(s._undo) <= s.MAX_UNDO, str(len(s._undo)))
    check("undo stack constant holds", s.MAX_UNDO == 40)
    while s.can_undo:
        s.undo()
    check("a pruned history still ends at a valid track",
          s.frames >= n and s.frames % 1 == 0, str(s.frames))

    target = path.replace(".wav", "_session.wav")
    s.save(target)
    check("save clears dirty", not s.dirty)
    check("save wrote a real WAV", os.path.getsize(target) > 1000)


def test_unicode_paths() -> None:
    """Thai file names must round-trip: load, save, and land on disk.

    The native writer's narrow fopen() goes through the Windows ANSI code
    page, which turned a Thai name into mojibake and made the queue report
    "original missing". The session bridges non-ASCII paths, so this is the
    regression that matters.
    """
    print("\n[non-ASCII file names]")
    from audio_editor import EditSession

    folder = tempfile.mkdtemp(prefix="freq_thai_")
    source = os.path.join(folder, "สาธุ (Sathu) - ทดสอบ [Official MV].wav")
    write_wav(source, seconds=1.0)
    check("a Thai-named file is created for the test", os.path.isfile(source))

    session = EditSession(source)
    check("a Thai-named file loads in the editor",
          session.frames == 44100, str(session.frames))
    session.fade(0, 4410, 0, True)

    target = os.path.join(folder, "สาธุ (Sathu) - แก้ไขแล้ว (edited).wav")
    session.save(target)
    check("a Thai-named export lands on the exact path", os.path.isfile(target))
    check("no mojibake leftovers beside it",
          sorted(os.listdir(folder))
          == sorted([os.path.basename(source), os.path.basename(target)]),
          str([f for f in os.listdir(folder)]))
    check("the exported bytes are a real WAV",
          os.path.getsize(target) > 1000, str(os.path.getsize(target)))
    reopened = EditSession(target)
    check("the Thai-named export re-opens at the same length",
          reopened.frames == session.frames, f"{reopened.frames} != {session.frames}")
    reopened.close()
    session.close()

    # A plain ASCII path still writes straight through.
    plain = os.path.join(folder, "plain.wav")
    again = EditSession(source)
    again.save(plain)
    check("ASCII exports are unchanged", os.path.isfile(plain))
    again.close()


def test_monitor(path: str) -> None:
    print("\n[monitor device]")
    import time

    from audio_editor import EditSession, list_monitor_devices

    devices = list_monitor_devices()
    check("device list always offers the default", devices and devices[0][1] == -1,
          str(devices[:2]))
    check("device list has names", all(isinstance(name, str) and name
                                       for name, _i in devices))

    s = EditSession(path)
    ok = s.preview(0, s.frames, -1, 0.02)
    check("preview starts on the default device", ok)
    time.sleep(0.25)
    pos = s.position
    check("preview position advances", pos > 0, str(pos))
    s.stop()
    time.sleep(0.05)
    check("preview stops", not s.playing)
    check("empty range is refused", s.preview(10, 10) is False)


def test_ui(path: str) -> None:
    print("\n[editor window + queue button]")
    import customtkinter as ctk

    from editor_window import EditorWindow

    app = None
    root = None
    # Building the real app loads (and can auto-save) the working playlist.
    # The operator's file is put back byte-for-byte afterwards.
    playlist = Path("radio_queue.freq")
    backup = playlist.read_bytes() if playlist.exists() else None
    try:
        import gui as gui_mod
        from gui import RadioApp

        app = RadioApp()
        app.update_idletasks()
        check("queue panel has an Edit button",
              hasattr(app, "btn_queue_edit"))
        check("Edit button hidden with no selection",
              not app.btn_queue_edit.winfo_manager())
        app._queue_selected = 0
        app._update_queue_filter_btn()
        app.update_idletasks()
        check("Edit button appears with a selection",
              bool(app.btn_queue_edit.winfo_manager()))
        app._queue_selected = None
        app._update_queue_filter_btn()
        app.update_idletasks()
        check("Edit button hides again",
              not app.btn_queue_edit.winfo_manager())
        check("Edit button opens the editor",
              callable(getattr(app, "_open_selected_editor", None)))
        check("edited audio is linked back to the queue",
              callable(getattr(app, "_on_song_edited", None)))

        # A YouTube row has no file_path: it plays from the download cache.
        from radio_manager import Song

        original_get = gui_mod.song_cache.get
        gui_mod.song_cache.get = lambda sid: path if sid == "cached1" else None
        try:
            yt = Song(id="cached1", title="Café | Vol: 1", artist="X",
                      source="youtube", url="https://youtu.be/1")
            check("YouTube item resolves its cached audio",
                  app._editable_audio_path(yt) == path,
                  str(app._editable_audio_path(yt)))
            never = Song(id="never-downloaded", title="nope", source="youtube",
                         url="https://youtu.be/2")
            check("an un-cached YouTube item has nothing to edit",
                  app._editable_audio_path(never) == "")
            mic = Song(id="mic1", title="mic", source="mic")
            check("mic segments are refused", app._editable_audio_path(mic) == "")

            app._on_song_edited(yt, path, 12.5)
            check("edited YouTube item now plays from the WAV",
                  yt.source == "file" and yt.file_path == path,
                  f"{yt.source} / {yt.file_path}")
            check("the YouTube URL is kept as provenance",
                  yt.url == "https://youtu.be/1")
            check("duration follows the edit", yt.duration == 12.5, str(yt.duration))

            # A relative path would break playback after a restart from a
            # different working directory, so the queue keeps it absolute.
            app._on_song_edited(yt, os.path.join("edits", "x.wav"), 3.0)
            check("the queue stores an absolute path",
                  os.path.isabs(yt.file_path), yt.file_path)
        finally:
            gui_mod.song_cache.get = original_get

        # ── Editing an item that has never been played downloads it first ──
        from editor_window import EditorWindow as _EW

        download_calls: list = []
        original_cache = gui_mod.song_cache.cache_song

        def fake_cache(song_id, title, url, duration=0.0, on_progress=None):
            download_calls.append((song_id, title, url))
            if on_progress:
                on_progress("downloading", 0.42)
            time.sleep(0.05)
            return path

        def pump(seconds: float):
            deadline = time.time() + seconds
            while time.time() < deadline:
                app.update()
                time.sleep(0.02)

        def editor_windows():
            return [w for w in app.winfo_children()
                    if isinstance(w, _EW)]

        pending = Song(id="pending1", title="Pending Song",
                       source="youtube", url="https://youtu.be/9")
        gui_mod.song_cache.cache_song = fake_cache
        try:
            app.rq.queue.append(pending)
            app._queue_selected = len(app.rq.queue) - 1
            app._open_selected_editor()
            check("Edit downloads an un-played item before opening",
                  len(download_calls) == 1, str(download_calls))
            check("the Edit button shows the download",
                  "\u23f3" in app.btn_queue_edit.cget("text"),
                  app.btn_queue_edit.cget("text"))
            opened = []
            deadline = time.time() + 5
            while time.time() < deadline and not opened:
                app.update()
                time.sleep(0.02)
                opened = editor_windows()
            check("the editor opens once the audio has landed", bool(opened))
            check("the Edit button is labelled again",
                  app.btn_queue_edit.cget("text").strip() == "Edit",
                  app.btn_queue_edit.cget("text"))
            for win in opened:
                win.session.dirty = False
                win.session.close()
                win.destroy()
            app.update()

            # A failed download says so and opens nothing.
            errors: list = []
            original_error = gui_mod.messagebox.showerror
            gui_mod.messagebox.showerror = lambda title, msg="", **k: errors.append(msg)
            gui_mod.song_cache.cache_song = lambda *a, **k: None
            try:
                pending2 = Song(id="pending2", title="Broken Song",
                                source="youtube", url="https://youtu.be/bad")
                app.rq.queue.append(pending2)
                app._queue_selected = len(app.rq.queue) - 1
                app._open_selected_editor()
                pump(1.0)
                check("a failed download reports instead of opening",
                      len(errors) == 1 and "nothing to edit" in errors[0].lower(),
                      str(errors))
                check("no editor window after a failed download",
                      not editor_windows(), str(editor_windows()))
                app.rq.queue.pop()
            finally:
                gui_mod.messagebox.showerror = original_error
            app.rq.queue.pop()
        finally:
            gui_mod.song_cache.cache_song = original_cache
            app._queue_selected = None
        root = app
    except Exception:
        print("  ! RadioApp could not be built:", traceback.format_exc(limit=2))
        root = ctk.CTk()
        root.withdraw()
        FAIL.append("RadioApp build")
    if root is None:
        root = ctk.CTk()
        root.withdraw()

    saved: list = []
    win = EditorWindow(root, path, display_name="Smoke tone")
    win._on_save = lambda song, target, duration: saved.append((target, duration))
    win.update_idletasks()
    win.update()

    check("window drew a waveform", len(win.canvas.find_withtag("wave")) > 100,
          str(len(win.canvas.find_withtag("wave"))))
    check("monitor dropdown is populated", len(win._devices) >= 1)
    check("fade curve box offers four shapes", len(win.curve_box.cget("values")) == 4)
    check("time readout is live", "/" in win.time_label.cget("text"))

    win.sel_start, win.sel_end = 4410, 22050
    win._redraw_all()
    check("selection label shows the range",
          "selection" in win.sel_label.cget("text"))

    before = win.session.stats(4410, 22050)["rms_db"]
    win._fade_out()
    after = win.session.stats(4410, 22050)["rms_db"]
    check("Fade Out button processes the selection", after < before - 1.0,
          f"{before:.1f} -> {after:.1f}")
    check("selection survives a fade", win._selection() == (4410, 22050))
    win._undo()
    check("Undo button restores it",
          near(win.session.stats(4410, 22050)["rms_db"], before, 0.01))

    win._normalize()
    check("Normalize button hits -1 dBFS",
          near(win.session.stats(4410, 22050)["peak_db"], -1.0, 0.05))
    win._undo()

    win.sel_start, win.sel_end = 4410, 22050
    win._trim()
    check("Trim keeps only the selection", win.session.frames == 22050 - 4410,
          str(win.session.frames))
    win._delete()
    check("Delete on no selection changes nothing", win.session.frames == 17640)

    # The playhead tick must not scan the buffer: a 4-minute scan holds the
    # audio lock for ~30 ms, which is what the monitor heard as stutter.
    stats_calls = {"n": 0}
    original_stats = win.session.stats

    def counting_stats(*args, **kwargs):
        stats_calls["n"] += 1
        return original_stats(*args, **kwargs)

    win.session.stats = counting_stats
    for _ in range(10):
        win._paint_playhead()
    win.session.stats = original_stats
    check("the playhead tick never scans the buffer", stats_calls["n"] == 0,
          f"{stats_calls['n']} scans")

    # And playback keeps up with the wall clock while those ticks run.
    win.sel_start = win.sel_end = None
    end = min(win.session.frames, win.session.sample_rate * 3)
    win.session.set_volume(0.0)          # silent: the device still runs
    started = win.session.preview(0, end, -1, 0.0)
    check("monitor preview starts for the continuity check", started)
    t0 = time.time()
    while time.time() - t0 < 1.2 and win.session.playing:
        win.playhead = int((time.time() - t0) * win.session.sample_rate)
        win._paint_playhead()
        win.update_idletasks()
        time.sleep(0.06)
    elapsed = time.time() - t0
    played = win.session.position / max(1, win.session.sample_rate)
    win._stop()
    check("preview audio keeps up with the clock while drawing",
          elapsed - played < 0.3, f"elapsed {elapsed:.2f}s played {played:.2f}s")

    win._save_copy()
    check("Save reports back to the queue", len(saved) == 1, str(saved))
    if saved:
        target, duration = saved[0]
        check("saved file exists", os.path.exists(target), target)
        check("saved duration matches the edit",
              near(duration, (22050 - 4410) / 44100, 0.01), str(duration))
        original = os.path.getsize(path)
        check("original file was not modified",
              abs(os.path.getsize(path) - original) < 1
              and Path(target).name != Path(path).name)

    # A cached download is saved into the caller's folder with a filename made
    # safe from a title full of Windows-illegal characters.
    from editor_window import safe_stem

    check("titles are made Windows-safe",
          not any(ch in safe_stem('a/b\\c:d*e?f"g<h>i|j')
                  for ch in '/\\:*?"<>|'),
          safe_stem('a/b\\c:d*e?f"g<h>i|j'))
    check("an empty title still yields a name",
          safe_stem("") != "", safe_stem(""))
    edits_dir = os.path.join(tempfile.mkdtemp(prefix="freq_edits_"), "edits")
    saved.clear()
    win2 = EditorWindow(root, path, display_name='Bedroom | Audio: Vol.1',
                        save_dir=edits_dir)
    win2._on_save = lambda song, target, duration: saved.append((target, duration))
    win2._save_copy()
    check("Save (copy) creates the target folder", len(saved) == 1, str(saved))
    if saved:
        target = Path(saved[0][0])
        check("cached saves land in the given folder",
              target.parent == Path(edits_dir), str(target.parent))
        check("saved name comes from the title, not the cache hash",
              target.name.startswith("Bedroom"), target.name)
        check("no illegal characters in the saved name",
              not any(ch in target.name for ch in ':|?*"<>/\\'), target.name)
    win2.session.dirty = False
    win2.session.close()
    win2.destroy()

    win.session.dirty = False
    win.session.close()
    win.destroy()
    try:
        root.destroy()
    except Exception:
        pass
    if backup is not None and playlist.exists():
        try:
            if playlist.read_bytes() != backup:
                playlist.write_bytes(backup)
                print("  (restored radio_queue.freq after the app build)")
        except Exception:
            pass


def main() -> int:
    print("=" * 62)
    print("  Audio editor smoke — native DSP, undo, monitor, UI")
    print("=" * 62)
    tmp = tempfile.mkdtemp(prefix="freq_editor_")
    path = os.path.join(tmp, "tone.wav")
    write_wav(path)
    try:
        test_native(path)
        test_session(path)
        test_unicode_paths()
        test_monitor(path)
        test_ui(path)
    except Exception:
        traceback.print_exc()
        FAIL.append("unexpected exception")

    print("\n" + "=" * 62)
    total = PASS + len(FAIL)
    print(f"  {PASS}/{total} checks passed")
    for name in FAIL:
        print(f"  FAILED: {name}")
    print("=" * 62)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
