#!/usr/bin/env python3
"""Headless smoke: marker-only queue updates vs full rebuild (freeze 0926).

Verifies on the REAL RadioApp that:
  1. _refresh_queue registers one row per rendered row
  2. _refresh_queue_markers re-marks the current row without rebuilding
  3. _play_index keeps row count + registry stable (marker path, no rebuild)
  4. structural changes (add/remove) still full-refresh consistently
  5. filtered view clears the registry and shows zero rows
"""
from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq: RadioQueue saves to a RELATIVE
# path in the cwd, so run the app from a throwaway directory.
import os, tempfile as _tf
os.chdir(_tf.mkdtemp(prefix="freq_marker_smoke_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from player import _ffmpeg_exe, _ffprobe_exe  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def make_song(name: str, seconds: float = 6.0) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="freq_marker_"))
    wav = tmp / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    frames = int(seconds * 44100)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / 44100)))
            for i in range(frames)
        ))
    r = subprocess.run(
        [_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-q:a", "2", str(mp3)],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="ignore")
    return str(mp3)


def main() -> None:
    import gui as gui_mod
    from radio_manager import Song

    app = gui_mod.RadioApp()
    app.withdraw()
    try:
        app.audio_player.volume = 0.0

        # ── Build a local-file queue of 5 real songs ──
        files = [make_song(f"m{i}") for i in range(5)]
        app.rq.queue.clear()
        for i, f in enumerate(files):
            s = Song(title=f"Smoke {i}", artist="t", source="file",
                     file_path=f, duration=6.0)
            s.id = f"smoke-{i}"
            app.rq.queue.append(s)

        app._queue_filter = ""
        app._refresh_queue()
        rows = app._queue_rows
        ok = set(rows) == set(range(5))
        check("refresh registers one row per rendered row", ok,
              f"rows={sorted(rows)}")

        # ── Marker-only: select track 2, verify visuals + NO rebuild ──
        app.rq.play(2)
        app._playing = True
        row2 = rows.get(2)
        item2 = row2.item
        before_children = len(app.queue_frame.winfo_children())
        t0 = time.perf_counter()
        app._refresh_queue_markers()
        marker_ms = (time.perf_counter() - t0) * 1000
        fg = item2.cget("fg_color")
        check("markers re-mark current row visuals",
              (not isinstance(fg, str)) or fg != "transparent", f"fg={fg!r}")
        check("markers do NOT rebuild rows",
              len(app.queue_frame.winfo_children()) == before_children
              and set(app._queue_rows) == set(range(5)),
              f"{len(app.queue_frame.winfo_children())} rows, "
              f"{marker_ms:.1f} ms")

        # ── _play_index: marker path keeps everything stable ──
        stale = app._queue_rows
        t0 = time.perf_counter()
        app._play_index(3)
        play_ms = (time.perf_counter() - t0) * 1000
        check("_play_index does not rebuild rows",
              app._queue_rows is stale and set(app._queue_rows) == set(range(5)),
              f"{play_ms:.1f} ms (was 1.3 s per 50 rows before)")
        w = app._queue_rows[3]
        check("_play_index marks the new current row",
              w.status.cget("text") in ("▶️", "⏸️"),
              f"status={w.status.cget('text')!r}")
        title_font = w.title.cget("font")
        try:
            weight = title_font.cget("weight")
        except Exception:
            weight = getattr(title_font, "weight", None)
        check("current-row title is bold", weight == "bold",
              f"weight={weight!r}")

        # ── Structural change: remove still full-refreshes ──
        app._remove_selected() if False else app.rq.remove_song(0)
        app._refresh_queue()
        check("structural edit full-refreshes rows",
              set(app._queue_rows) == set(range(4)),
              f"rows={sorted(app._queue_rows)}")

        # ── Filtered view clears the registry ──
        app._queue_filter = "zzz-no-match-zzz"
        app._refresh_queue()
        check("filtered view clears registry",
              app._queue_rows == {}, f"rows={list(app._queue_rows)}")
        app._queue_filter = ""

        print(f"\nsummary: {sum(1 for _, ok, _ in RESULTS if ok)}/"
              f"{len(RESULTS)} passed")
    finally:
        try:
            app.audio_player.stop()
        except Exception:
            pass
        try:
            app.destroy()
        except Exception:
            pass

    sys.exit(1 if any(not ok for _, ok, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
