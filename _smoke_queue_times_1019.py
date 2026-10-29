#!/usr/bin/env python3
"""Smoke: the queue card's scheduled start / stop clock column (1019.1).

The card's right side now carries the wall-clock window a song will air in
(`HH:MM:SS–HH:MM:SS`) right next to its length. This suite drives the REAL widget
through a real Tk loop and checks:

  1. the schedule is anchored on the song playing right now (start = now −
     elapsed), so rows BEFORE it are in the past and rows after it carry the
     clock time they will really start at
  2. with nothing playing, row 0 starts now and every row continues from the
     one above it (stop[i] == start[i + 1])
  3. the string is really ON THE CANVAS (read back from the drawn items, not
     from the code text) and sits beside the duration
  4. malformed lengths (None / NaN / negative / a string) still get a stamp —
     the painter never raises and the row is still drawn
  5. a narrow card DROPS the clock instead of squeezing the title, and gets
     the room back
  6. the minute rolling over re-stamps the drawn rows (nothing else repaints)
"""
from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — never touch the operator's real queue.
os.chdir(tempfile.mkdtemp(prefix="freq_qtimes_smoke_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import datetime  # noqa: E402

from radio_manager import Song  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
CLOCK_RE = re.compile(r"^\d{2}:\d{2}:\d{2}–\d{2}:\d{2}:\d{2}$")


def make_wav(path: str, seconds: float) -> None:
    """A real, readable audio file — mutagen must be able to measure it."""
    import math
    import struct
    import wave

    frames = int(seconds * 44100)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 44100)))
            for i in range(frames)))


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def canvas_texts(canvas) -> list[str]:
    """Every text item actually materialised on the queue canvas."""
    out = []
    for item in canvas.find_all():
        try:
            if canvas.type(item) == "text":
                out.append(str(canvas.itemcget(item, "text")))
        except Exception:
            continue
    return out


def main() -> None:
    import gui as gui_mod

    app = gui_mod.RadioApp()
    app.withdraw()
    try:
        app.audio_player.volume = 0.0
        app._playing = False

        # ── A deterministic queue: the panel width is what the real card
        #    gets in a 922 px sidebar, so the layout maths is the shipped one.
        app._queue_canvas_width = lambda: 922
        app.rq.queue.clear()
        for i, (title, dur) in enumerate((("The Waiter", 241), ("Plant", 132),
                                          ("Mermaid", 344), ("Romantic Scene", 272))):
            s = Song(title=f"FOLK9 - {title} [Official MV]", artist="FOLK9",
                     source="file", file_path=f"/tmp/qtimes_{i}.mp3",
                     duration=float(dur))
            s.id = f"qtimes-{i}"
            app.rq.queue.append(s)
        app._queue_filter = ""
        app._refresh_queue()
        app.update()

        canvas = app._queue_canvas
        sched = app._queue_times
        check("schedule built for every row", len(sched) == len(app.rq.queue),
              f"rows={len(sched)}")
        check("nothing playing → row 0 starts now (±3 s)",
              abs(sched[0][0] - time.time()) <= 3.0,
              f"start0-now={sched[0][0] - time.time():+.1f}s")
        check("row 0 lasts exactly its length",
              abs((sched[0][1] - sched[0][0]) - 241.0) < 0.01,
              f"{sched[0][1] - sched[0][0]:.0f}s")
        check("rows continue from the one above (no gaps/distortion)",
              all(abs(sched[i][1] - sched[i + 1][0]) < 0.01
                  for i in range(len(sched) - 1)))
        check("a stamped row reads HH:MM:SS–HH:MM:SS", bool(CLOCK_RE.match(
            app._queue_row_times(0))), app._queue_row_times(0))

        # ── Anchored on the playing row: track 4 (index 3) at 1:40 in.
        app.rq.current_index = 3
        app._playing = True
        app.audio_player.get_position = lambda: 100.0
        app._refresh_queue()
        app.update()
        sched = app._queue_times
        check("playing row starts at now − elapsed",
              abs(sched[3][0] - (time.time() - 100.0)) <= 3.0,
              f"{app._queue_row_times(3)}")
        check("rows above it sit in the past by the lengths between them",
              abs(sched[0][0] - (sched[3][0] - (241 + 132 + 344))) < 0.01)
        check("the row before the playing one ends exactly when it starts",
              abs(sched[2][1] - sched[3][0]) < 0.01)

        # ── It is really drawn: read the clock string back off the canvas.
        drawn = canvas_texts(canvas)
        want = app._queue_row_times(0)
        check("the clock string is an actual canvas item", want in drawn,
              want)
        check("the length is still drawn beside it", "04:01" in drawn)
        check("one clock per visible row",
              sum(1 for t in drawn if CLOCK_RE.match(t)) == len(app.rq.queue),
              f"{sum(1 for t in drawn if CLOCK_RE.match(t))} clock items")

        # ── Hostile lengths: stamp anyway, never raise, keep drawing.
        app.rq.queue[1].duration = None
        app.rq.queue[2].duration = float("nan")
        app.rq.queue[0].duration = -5.0
        app.rq.queue.append(Song(title="Broken", artist="x", source="file",
                                 file_path="/tmp/qtimes_x.mp3", duration="abc"))
        app._refresh_queue()
        app.update()
        bad = app._queue_times
        check("malformed lengths still produce a stamp for every row",
              len(bad) == 5 and all(CLOCK_RE.match(app._queue_row_times(i))
                                    for i in range(5)),
              " · ".join(app._queue_row_times(i) for i in range(5)))
        check("a broken length wastes 0 s instead of breaking the column",
              all(math.isfinite(b[1]) and b[1] >= b[0] for b in bad))

        # ── Narrow card: the clock is dropped, the title gets the room back.
        app._queue_canvas_width = lambda: 200
        app._refresh_queue()
        app.update()
        narrow = canvas_texts(canvas)
        check("a narrow card drops the clock column",
              not any(CLOCK_RE.match(t) for t in narrow),
              f"{len(narrow)} text items")
        # The title is ellipsized to fit (canvas text cannot wrap) — what
        # matters is that it IS drawn, beside a length, with no clock.
        check("…but still shows the length and the title",
              any(t.startswith("FOLK9") for t in narrow)
              and any(re.match(r"^\d{2}:\d{2}$", t) for t in narrow),
              " · ".join(narrow))

        # ── The minute rolls: drawn rows are re-stamped (one tick, no rebuild).
        app._queue_canvas_width = lambda: 922
        app._refresh_queue()
        app.update()
        before_items = len(canvas.find_all())
        app._queue_clock_minute = 0            # pretend we woke up a while ago
        app._queue_view_tick()
        app.update()
        check("the minute roll updates the stored minute",
              app._queue_clock_minute == int(time.time() // 60))
        check("re-stamping repaints rows in place (no rebuild, no leak)",
              len(canvas.find_all()) == before_items,
              f"{before_items} → {len(canvas.find_all())} items")
        check("the column is still stamped after the roll",
              bool(CLOCK_RE.match(app._queue_row_times(0))))

        # ── Air time that is not a song: jingle / ad / anthem. ──
        for song in app.rq.queue:
            song.duration = 120.0
        jingle_wav = str(Path.cwd() / "jingle.wav")
        ad_wav = str(Path.cwd() / "ad.wav")
        make_wav(jingle_wav, 1.5)
        make_wav(ad_wav, 9.0)
        app._playing = False
        app.jingle.enabled, app.jingle.jingle_file = True, jingle_wav
        app.jingle.interval, app.jingle.song_counter = 2, 0
        app._refresh_queue()
        app.update()
        n_songs = len(app.rq.queue)
        cumulative, markers = app._queue_insert_plan(n_songs)
        check("a jingle lands after every N songs, at its real length",
              [k for k, _s, kind in markers if kind == "jingle"]
              == list(range(2, n_songs + 1, 2))
              and abs(cumulative[2] - 1.5) < 0.1,
              f"steps={[k for k, _s, _k in markers]} inserted={cumulative[-1]:.1f}s")
        raw = app._queue_times
        check("rows AFTER the insert are pushed by the jingle's length",
              abs((raw[2][0] - raw[0][0]) - (2 * 120.0 + 1.5)) < 0.2,
              f"row2-row0={raw[2][0] - raw[0][0]:.1f}s (want 241.5)")
        check("rows BEFORE the insert are untouched",
              abs((raw[1][0] - raw[0][0]) - 120.0) < 0.2)
        check("the trigger row's window CONTAINS its jingle (no invisible gap)",
              abs((raw[1][1] - raw[1][0]) - 121.5) < 0.2
              and abs(raw[2][0] - raw[1][1]) < 0.01,
              f"row1={raw[1][1] - raw[1][0]:.1f}s gap={raw[2][0] - raw[1][1]:.2f}s")
        events = [e for e in app._queue_events if e["kind"] == "jingle"]
        check("the jingle is a rundown entry with its own clock window",
              len(events) == 2 and abs(events[0]["secs"] - 1.5) < 0.1
              and abs(events[0]["start"] - (raw[1][1] - 1.5)) < 0.01,
              f"events={[(round(e['start'] - raw[0][0]), e['secs']) for e in events]}")

        # An ad that fires REPLACES the jingle check for that song.
        app.jingle.enabled = False
        app.commercial.enabled, app.commercial.ads = True, [ad_wav]
        app.commercial.interval, app.commercial.song_counter = 1, 0
        _cum, markers = app._queue_insert_plan(3)
        check("an ad break takes the slot a jingle would have taken",
              [kind for _k, _s, kind in markers] == ["ad", "ad", "ad"],
              f"kinds={[k for _k, _s, k in markers]}")
        check("the ad break carries the AD's length (round-robin file)",
              abs(markers[0][1] - 9.0) < 0.1, f"{markers[0][1]:.1f}s")
        app.commercial.interval, app.commercial.song_counter = 2, 0
        _cum, markers = app._queue_insert_plan(4)
        check("an ad every 2 songs shifts rows 2 and 4 by 9 s",
              abs(_cum[2] - 9.0) < 0.1 and abs(_cum[4] - 18.0) < 0.1,
              f"cum={_cum}")
        check("the ad break takes the slot a jingle would have taken"
              if False else "every insert carries a real length",
              all(secs > 0 for _k, secs, _kind in markers))

        # Anthem: a fixed wall-clock moment the queue cannot dodge.
        app.jingle.enabled = app.commercial.enabled = False
        moment = datetime.datetime.now() + datetime.timedelta(minutes=5)
        cfg = app.anthem.config
        cfg.enabled, cfg.duration = True, 80.0
        cfg.pause_before, cfg.fade_in, cfg.fade_out = 1.0, 2.0, 2.0
        cfg.morning_time = moment.strftime("%H:%M")
        # 6 hours later is past the horizon, so exactly ONE anthem folds.
        cfg.evening_time = (moment + datetime.timedelta(hours=6)).strftime("%H:%M")
        cfg.days = [d for d in cfg.days]           # every day, incl. today
        app._refresh_queue()
        app.update()
        sched = app._queue_times
        folds = app._anthem_folds(sched[0][0], sched[-1][1])
        raw_start = time.time()
        check("an anthem inside the window folds its air time in (once)",
              len(folds) == 1 and abs(folds[0][1] - 85.0) < 0.1,
              f"folds={[(round(a - raw_start), b) for a, b in folds]}")
        check("the air time is pause + fade-in + length + fade-out",
              abs(sched[-1][1] - (raw_start + len(sched) * 120.0 + 85.0)) < 4.0,
              f"last stop {app._clock_time(sched[-1][1])}")
        check("rows before the anthem keep their slot",
              abs(sched[0][0] - raw_start) < 3.0)
        anthem_events = [e for e in app._queue_events
                         if e["kind"] == "anthem"]
        check("the anthem is placed where it really cuts in, with its time",
              len(anthem_events) == 1
              and abs(anthem_events[0]["start"] - folds[0][0]) < 0.01
              and abs(anthem_events[0]["secs"] - 85.0) < 0.1,
              f"{[(app._clock_time(e['start']), e['secs']) for e in anthem_events]}")
        inside = next((i for i, (s0, s1) in enumerate(sched)
                       if s0 <= folds[0][0] <= s1), None)
        check("the song the anthem interrupts is long enough to hold it",
              inside is not None
              and sched[inside][1] >= folds[0][0] + 85.0 - 0.01,
              f"row={inside} stop={app._clock_time(sched[inside][1]) if inside is not None else '-'}")
        check("an anthem with no known length is NOT guessed",
              (setattr(cfg, "duration", 0.0),
               setattr(cfg, "anthem_file", "")) is not None
              and app._anthem_folds(raw_start, raw_start + 3600) == [],
              "an anthem of unknown length must fold 0 s")
        cfg.duration = 80.0

        # The header line names the next insert of each kind.
        app.rq.current_index = 0        # the plan counts from the row on air
        app.jingle.enabled, app.jingle.interval, app.jingle.song_counter = True, 3, 0
        app.commercial.enabled, app.commercial.interval = True, 4
        app.commercial.song_counter = 0
        app._refresh_queue()
        app.update()
        line = app._queue_next_insert_text()
        check("the header line names jingle / ad / anthem with a clock time",
              all(word in line for word in ("jingle", "ad", "anthem"))
              and len(re.findall(r"\d{2}:\d{2}:\d{2}", line)) >= 3, line)
        check("every listed insert also shows how long it lasts",
              len(re.findall(r"\(\d{2}:\d{2}\)", line)) >= 3, line)
        check("one entry per kind — repeats collapse instead of wrapping",
              line.count("jingle") == 1 and line.count("ad ") == 1,
              line)
        check("the collapsed repeats are counted once at the end",
              len(app._queue_events) <= 3
              or re.search(r"\+\d+ more", line) is not None,
              f"events={len(app._queue_events)} line={line!r}")
        check("the header line stays short enough for one row",
              len(line.splitlines()) == 1 and len(line) <= 110,
              f"lines={len(line.splitlines())} chars={len(line)}")
        check("each rundown event kind is a separate, stamped entry",
              sorted({e["kind"] for e in app._queue_events}) ==
              ["ad", "anthem", "jingle"],
              str(sorted({e["kind"] for e in app._queue_events})))
        check("the line is packed only while there is something to report",
              app.queue_next_label.winfo_manager() == "pack")

        # A crowded rundown: a jingle and an ad after EVERY song (8 events).
        # It used to print seven of them and wrap over three rows; it must now
        # stay one short row with a single "+N more" badge.
        app.jingle.interval, app.jingle.song_counter = 1, 0
        app.commercial.interval, app.commercial.song_counter = 1, 0
        app._refresh_queue()
        app.update()
        crowded = app._queue_next_insert_text()
        kinds = [e["kind"] for e in app._queue_events
                 if e.get("kind") in ("jingle", "ad", "anthem")]
        hidden = len(kinds) - len(set(kinds))
        check("many inserts of one kind collapse to one entry each",
              len(kinds) > 3 and all(crowded.count(k) <= 1 for k in set(kinds)),
              f"events={len(kinds)} line={crowded!r}")
        check("the hidden inserts are counted once with +N more",
              hidden > 0 and f"+{hidden} more" in crowded, crowded)
        check("the crowded rundown is still a single row",
              len(crowded.splitlines()) == 1 and len(crowded) <= 120,
              f"lines={len(crowded.splitlines())} chars={len(crowded)}")
        app.jingle.interval, app.jingle.song_counter = 3, 0
        app.commercial.interval, app.commercial.song_counter = 4, 0
        app.jingle.enabled = app.commercial.enabled = False
        cfg.enabled = False
        app._refresh_queue()
        app.update()
        check("a plain music queue hides the line again",
              app.queue_next_label.winfo_manager() == "",
              f"manager={app.queue_next_label.winfo_manager()!r}")
        cfg.enabled = True

        # A transfer still running delays everything after the row on air.
        app.audio_player.get_position = lambda: 0.0
        app.rq.current_index = 0
        app._playing = True
        app._download_mode = True
        app._dl_state = {"index": 0, "status": "downloading", "pct": 25.0,
                         "started": time.time() - 10.0}   # 2.5 %/s → 30 s left
        stall = app._download_stall_seconds()
        check("the remaining transfer time is estimated from its rate",
              abs(stall - 30.0) < 2.0, f"stall={stall:.1f}s")
        app._refresh_queue()
        app.update()
        sched = app._queue_times
        check("the downloading row starts when its file is ready",
              abs(sched[0][0] - (time.time() + stall)) < 1.5,
              f"start0-now={sched[0][0] - time.time():.1f}s stall={stall:.1f}s")
        check("the queue stays contiguous behind that wait",
              abs((sched[1][0] - sched[0][0]) - 120.0) < 1.0
              and abs(sched[1][0] - sched[0][1]) < 0.01,
              f"gap={sched[1][0] - sched[0][0]:.1f}s")
        wait = [e for e in app._queue_events if e["kind"] == "wait"]
        check("the wait is a rundown entry with its own clock window",
              len(wait) == 1 and wait[0]["status"] == "downloading"
              and abs(wait[0]["secs"] - stall) < 1.5,
              str(wait[:1]))
        transfer = app._queue_transfer_text()
        check("the download line says how much is left and when it lands",
              "download" in transfer and "queue waits" in transfer
              and re.search(r"ready ~\d{2}:\d{2}:\d{2}", transfer)
              is not None, transfer)
        check("converting has no percentage → no invented finish time",
              app._dl_state.update({"status": "processing", "pct": 0.0,
                                    "started": time.time() - 7.0}) is None
              and app._download_stall_seconds() == 0.0)
        app._refresh_queue()
        transfer = app._queue_transfer_text()
        check("the convert line shows elapsed time and admits the missing ETA",
              "convert" in transfer and "elapsed" in transfer
              and "ETA unknown" in transfer, transfer)
        app._convert_ratios = [0.05]          # measured: 5% of the audio length
        app._convert_song_secs = 120.0        # 120 s song → ~6 s conversion
        est = app._convert_estimate()
        check("a measured conversion speed yields a finish estimate",
              abs(est - 6.0) < 0.1, f"est={est:.2f}s")
        app._refresh_queue()
        transfer = app._queue_transfer_text()
        check("the convert line then shows a ready clock, marked est.",
              "ready ~" in transfer and "est." in transfer, transfer)
        app._download_mode = False
        app._dl_state = {}
        app._convert_ratios = []
        app._convert_song_secs = 0.0
    finally:
        try:
            app.destroy()
        except Exception:
            pass

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
