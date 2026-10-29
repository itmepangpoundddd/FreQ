#!/usr/bin/env python3
"""Smoke: Air-Check track splitting [split-0928].

Verifies that:
  1. TrackLog accepts/rejects events correctly (sorted, clamped)
  2. naming: sanitize + build_segment_name produce Windows-safe names
  3. split_recording cuts a REAL wav into per-track files with the
     right durations, named from real metadata
  4. edge cases: no events / empty file / tiny segments / duplicate
     offsets / events past EOF
  5. GUI wiring: _play_index tags a TrackLog event while recording,
     checkbox default off, persist round-trips split_tracks
"""

import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — run from a throwaway dir so the
# user's real radio_queue.freq is never touched.
os.chdir(tempfile.mkdtemp(prefix="freq_split_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ── 1. TrackLog ──────────────────────────────────────────────────
from aircheck_split import (TrackLog, build_segment_name,  # noqa: E402
                            sanitize_name, split_recording,
                            MIN_SEGMENT_SEC)

tl = TrackLog()
e1 = tl.add(kind="music", title="B", artist="Artist", offset=30.0)
e2 = tl.add(kind="music", title="A", artist="Artist", offset=5.0)
check("add returns event", e1 is not None and e2 is not None)
check("events kept sorted by offset",
      [e["offset"] for e in tl.events()] == [5.0, 30.0])
check("rejects negative offset", tl.add(offset=-1.0) is None)
check("rejects non-numeric offset", tl.add(offset="abc") is None)
check("rejects None offset", tl.add() is None)
tl.clear()
check("clear empties log", len(tl) == 0)

# ── 2. naming ────────────────────────────────────────────────────
check("sanitize strips illegal chars",
      sanitize_name('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij")
check("sanitize collapses whitespace, strips outer dots (inner dots kept)",
      sanitize_name("  Multi \n Space...\tName. ") == "Multi Space... Name")
check("sanitize truncates long names",
      len(sanitize_name("x" * 500)) == 80)
check("empty becomes Untitled",
      build_segment_name(1, "", "") == "track_001 - Untitled.wav")
check("artist-title format",
      build_segment_name(7, "Artist", "Title") == "track_007 - Artist - Title.wav")
check("index is zero-padded",
      build_segment_name(12, "A", "B").startswith("track_012 - "))

# ── helpers: build a real wav ────────────────────────────────────
def make_wav(path, seconds, rate=8000):
    """One sine track per second boundary, distinct freq per second."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for sec in range(int(math.ceil(seconds))):
            freq = 400 + 100 * (sec % 5)
            frames = bytearray()
            for i in range(int(rate)):
                v = int(9000 * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<h", v)
            w.writeframes(bytes(frames))


tmp = tempfile.mkdtemp(prefix="split_wav_")
wav_path = os.path.join(tmp, "aircheck_test.wav")
make_wav(wav_path, 60.0)   # 60 s file

# ── 3. real split with metadata names ────────────────────────────
events = [
    {"kind": "music", "title": "First Song", "artist": "The Band",
     "duration": 25.0, "offset": 0.0},
    {"kind": "music", "title": 'Second / Song: "Deluxe"', "artist": "A/B",
     "duration": 20.0, "offset": 25.0},
    {"kind": "mic", "title": "Live Talk", "artist": "",
     "duration": 15.0, "offset": 45.0},
]
segs = split_recording(wav_path, events)
check("three segments produced", len(segs) == 3, str(len(segs)))
names = sorted(os.path.basename(s["path"]) for s in segs)
check("files named from real metadata",
      names == ["track_001 - The Band - First Song.wav",
                "track_002 - AB - Second Song Deluxe.wav",
                "track_003 - Live Talk.wav"], str(names))
durs = {s["path"]: s["duration"] for s in segs}
ok_dur = all(abs(s["duration"] - (s["end"] - s["start"])) < 0.05 for s in segs)
check("segment durations consistent", ok_dur)
first = [s for s in segs if "First Song" in s["path"]][0]
check("first segment runs from 0 to next offset",
      first["start"] == 0.0 and abs(first["end"] - 25.0) < 0.01,
      f"{first['start']}..{first['end']}")
last = [s for s in segs if "Live Talk" in s["path"]][0]
check("last segment runs to EOF", abs(last["end"] - 60.0) < 0.01,
      f"end={last['end']}")
# verify actual wav durations on disk
with wave.open(first["path"], "rb") as w:
    d = w.getnframes() / w.getframerate()
check("cut file duration matches (~25 s)", abs(d - 25.0) < 0.2, f"{d:.2f}s")
with wave.open(last["path"], "rb") as w:
    d2 = w.getnframes() / w.getframerate()
check("cut file duration matches (~15 s)", abs(d2 - 15.0) < 0.2, f"{d2:.2f}s")
check("segments carry kind tags",
      {s["kind"] for s in segs} == {"music", "mic"})

# ── 4. edge cases ────────────────────────────────────────────────
check("no events -> no files", split_recording(wav_path, []) == [])
check("None events -> no files", split_recording(wav_path, None) == [])
check("missing file -> no files",
      split_recording(os.path.join(tmp, "nope.wav"), events) == [])
# events past EOF are clamped/dropped; two at same offset merge
weird = [
    {"kind": "music", "title": "X", "artist": "", "offset": 55.0},
    {"kind": "music", "title": "Y", "artist": "", "offset": 999.0},
    {"kind": "music", "title": "Z", "artist": "", "offset": 30.0},
    {"kind": "music", "title": "Z-dup", "artist": "", "offset": 30.01},
]
segs2 = split_recording(wav_path, weird)
# Y past EOF dropped; Z+Z-dup merged (30 s apart too close); X at 55 s
# yields Z:30..55 and X:55..60 (both >= MIN_SEGMENT_SEC)
check("past-EOF dropped, near-duplicates merged",
      len(segs2) == 2, str([os.path.basename(s["path"]) for s in segs2]))
# tiny final segment (< MIN_SEGMENT_SEC) is dropped
tiny = [{"kind": "music", "title": "Head", "artist": "", "offset": 0.0},
        {"kind": "music", "title": "Tail", "artist": "", "offset": 59.5}]
segs3 = split_recording(wav_path, tiny)
check("tiny tail segment dropped",
      len(segs3) == 1 and "Head" in segs3[0]["path"],
      str([os.path.basename(s["path"]) for s in segs3]))
# progress callback fires
seen = []
split_recording(wav_path, events, progress=lambda i, n: seen.append((i, n)))
check("progress callback fires per event", len(seen) == 3, str(seen))

# ── 5. GUI wiring on the real RadioApp ───────────────────────────
from gui import RadioApp  # noqa: E402

app = RadioApp()
try:
    check("split checkbox exists and defaults OFF",
          hasattr(app, "aircheck_split_var")
          and app.aircheck_split_var.get() is False)
    check("TrackLog initialized", len(app._aircheck_tracklog) == 0)

    # tag one event with a fake native recording in progress
    app._aircheck_native_started_at = __import__("time").time() - 10.0
    import native_aircheck as na
    real_state = na.aircheck_state
    na.aircheck_state = lambda: 1   # pretend a native recording is live
    try:
        app.aircheck_split_var.set(True)
        song = type("S", (), {"title": "Sathu", "artist": "Pro Boy",
                              "source": "youtube", "duration": 200.0})()
        idx = app.rq.current_index
        if idx is None or idx < 0 or idx >= len(app.rq.queue):
            app.rq.queue.insert(0, song)
            app.rq.current_index = 0
        app._aircheck_note_track()
        n_events = len(app._aircheck_tracklog)
        check("note_track tags event while recording",
              n_events == 1, f"events={n_events}")
        if n_events:
            ev = app._aircheck_tracklog.events()[0]
            check("event carries real metadata + ~10 s offset",
                  ev["title"] == "Sathu" and ev["artist"] == "Pro Boy"
                  and 9.0 <= ev["offset"] <= 12.0, str(ev))
        # checkbox off -> no tagging
        app._aircheck_tracklog.clear()
        app.aircheck_split_var.set(False)
        app._aircheck_note_track()
        check("checkbox OFF disables tagging", len(app._aircheck_tracklog) == 0)
        app.aircheck_split_var.set(True)
    finally:
        na.aircheck_state = real_state
finally:
    try:
        app.destroy()
    except Exception:
        pass

# ── 6. persist round-trip ────────────────────────────────────────
app2 = RadioApp()
try:
    app2.aircheck_split_var.set(True)
    data = app2._collect_settings()
    check("persist carries split_tracks=True",
          data.get("aircheck", {}).get("split_tracks") is True)
    app2.aircheck_split_var.set(False)
    app2._restore_settings(dict(data))
    check("restore reads split_tracks back",
          app2.aircheck_split_var.get() is True)
finally:
    try:
        app2.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
