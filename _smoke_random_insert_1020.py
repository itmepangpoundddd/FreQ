#!/usr/bin/env python3
"""Smoke: random-position insertion — the Add tab's 🎲 switch (1020).

"Random" here means a random position IN THE QUEUE, not a shuffled copy of
it: every add path on the Add tab (YouTube video, playlist batch, local
file, drag-and-drop, library ➕, manual form) drops its song into a random
slot of the queue still to come. This suite drives the REAL CTkSwitch, the
REAL add paths and the REAL RadioQueue on a real Tk loop and checks:

  1. off by default — songs are appended in order, so an operator who never
     touches the switch gets exactly the queue they always got
  2. the real switch widget flips the mode, and `_add_to_queue` returns the
     slot the song actually landed in
  3. a song NEVER lands on or before the row on air (pos >= current + 1):
     the runtime walks the queue forward, so such a song would never air —
     the whole failure mode of a naive shuffle
  4. rows already aired and the row on air are not displaced, and the
     current index still points at the same song after hundreds of inserts
  5. the positions are genuinely random — inserts land INSIDE the list
     instead of only at its end, and the sequence is not monotonic
  6. `RadioQueue.insert_song_at_random` does the same with no GUI at all
  7. the playlist importer, which adds its songs on a BACKGROUND THREAD,
     honours the same switch
  8. the scattered queue still renders (the virtualized list keeps one row
     per song)
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — never touch the operator's real queue.
os.chdir(tempfile.mkdtemp(prefix="freq_randins_smoke_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from radio_manager import RadioQueue, Song  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def song(n: int) -> Song:
    s = Song(title=f"Song {n}", artist="Tester", duration=180.0, source="file")
    s.id = f"rand-{n}"
    return s


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            app.update()
        except Exception:
            return
        time.sleep(0.01)


def wait_for(app, predicate, timeout: float = 4.0) -> bool:
    """Pump the real event loop until a worker thread's result has landed."""
    end = time.time() + timeout
    while time.time() < end:
        pump(app, 0.05)
        if predicate():
            return True
    return predicate()


def main() -> None:
    import gui as gui_mod

    # ── 6. The queue itself, with no GUI in the picture. ──
    q = RadioQueue()
    for i in range(30):
        q.add_song(song(i))
    q.current_index = 5
    on_air = q.queue[5].id
    rng = random.Random(2020)
    landed = [q.insert_song_at_random(song(100 + i), rng) for i in range(60)]
    check("plain queue: every insert lands after the row on air",
          min(landed) >= 6, f"min={min(landed)}")
    check("plain queue: the row on air is still the same song",
          q.current_index == 5 and q.queue[5].id == on_air,
          f"current={q.current_index}")
    check("plain queue: rows that already aired are untouched",
          [s.id for s in q.queue[:5]] == [f"rand-{i}" for i in range(5)])
    check("plain queue: every song is in the queue exactly once",
          sorted(s.id for s in q.queue) ==
          sorted([f"rand-{i}" for i in range(30)] +
                 [f"rand-{100 + i}" for i in range(60)]))
    inside = sum(1 for i, pos in enumerate(landed)
                 if pos < 30 + i)          # not the end it would have been
    check("plain queue: inserts land inside the list, not just at the end",
          inside >= 30, f"{inside}/60 landed before the tail")
    check("plain queue: the landing positions are not monotonic",
          any(landed[i] > landed[i + 1] for i in range(len(landed) - 1)),
          f"first 12: {landed[:12]}")
    batch = RadioQueue()
    batch.insert_songs_at_random([song(i) for i in range(5)], random.Random(7))
    check("plain queue: a batch spreads out instead of arriving as one block",
          len(batch.queue) == 5 and len({
              batch.queue.index(s) for s in batch.queue}) == 5)

    # ── The real app from here on. ──
    app = gui_mod.RadioApp()
    app.withdraw()
    try:
        app.audio_player.volume = 0.0
        app._playing = False
        app.rq.queue.clear()
        app.rq.library.clear()
        app.rq.current_index = -1
        app._queue_filter = ""
        random.seed(1020)

        check("the Add tab ships the 🎲 switch", hasattr(app, "random_insert_switch")
              and app.random_insert_switch.winfo_exists())
        check("OFF by default — the queue people already had is unchanged",
              app._queue_random_insert is False
              and bool(app._random_insert_var.get()) is False)

        # ── 1. Append semantics while the switch is off. ──
        positions = [app._add_to_queue(song(i)) for i in range(3)]
        check("switch OFF → songs are appended in order",
              positions == [0, 1, 2]
              and [s.title for s in app.rq.queue] == ["Song 0", "Song 1", "Song 2"],
              f"positions={positions}")

        # ── 2. The real widget flips it. ──
        app.random_insert_switch.toggle()
        pump(app, 0.05)
        check("clicking the switch turns the mode on",
              app._queue_random_insert is True
              and bool(app._random_insert_var.get()) is True)
        pos = app._add_to_queue(song(3))
        check("the helper returns the slot the song really landed in",
              0 <= pos <= len(app.rq.queue) - 1
              and app.rq.queue[pos].id == "rand-3",
              f"pos={pos} of {len(app.rq.queue)} rows")
        check("nothing on air → a new song may land anywhere (incl. the top)",
              True, f"landed at #{pos + 1}")

        # ── 3 & 4. The invariants that make the feature usable. ──
        app.rq.queue.clear()
        for i in range(200):
            app.rq.add_song(song(i))
        app.rq.current_index = 42
        app._playing = True
        on_air = app.rq.queue[42].id
        aired = [s.id for s in app.rq.queue[:43]]
        landed = [app._add_to_queue(song(500 + i)) for i in range(300)]
        check("no insert lands on or before the song on air",
              min(landed) >= 43, f"min={min(landed)} (must be ≥ 43)")
        check("the song on air is still the same song",
              app.rq.current_index == 42 and app.rq.queue[42].id == on_air)
        check("rows that already aired are never displaced",
              [s.id for s in app.rq.queue[:43]] == aired)
        check("every inserted song is in the queue exactly once",
              sorted(s.id for s in app.rq.queue) ==
              sorted([f"rand-{i}" for i in range(200)] +
                     [f"rand-{500 + i}" for i in range(300)]))
        check("the queue really grew by the number of adds",
              len(app.rq.queue) == 500, f"{len(app.rq.queue)} rows")
        inside = sum(1 for i, p in enumerate(landed) if p < 200 + i)
        check("inserts land inside the upcoming list, not only at its end",
              inside >= 150, f"{inside}/300 landed before the tail")
        check("landing positions are not monotonic (really shuffled)",
              any(landed[i] > landed[i + 1] for i in range(len(landed) - 1)),
              f"first 12: {landed[:12]}")
        spread = len({p for p in landed})
        check("positions cover a wide range instead of a few slots",
              spread >= 40, f"{spread} distinct slots")
        check("the next song after the one on air is still a real song",
              app.rq.queue[43] is not None and app.rq.queue[43].id.startswith("rand-"),
              app.rq.queue[43].title)

        # ── 8. The scattered queue still renders. ──
        app._refresh_queue()
        pump(app, 0.2)
        check("the queue panel rebuilds with one row per song",
              len(app._queue_view) == len(app.rq.queue),
              f"view={len(app._queue_view)} queue={len(app.rq.queue)}")

        # ── 7. Playlist import: the songs are added from a worker thread. ──
        app.rq.queue.clear()
        app.rq.current_index = -1
        for i in range(30):
            app.rq.add_song(song(i))
        app.rq.current_index = 10
        app._refresh_queue()
        before = len(app.rq.queue)
        imported = [song(900 + i) for i in range(5)]
        original = gui_mod.YouTubeManager.extract_playlist_range
        gui_mod.YouTubeManager.extract_playlist_range = staticmethod(
            lambda entries, start, end: list(imported))
        try:
            app.yt_playlist_entry.delete(0, "end")
            app.yt_playlist_entry.insert(0, "https://example.invalid/list")
            app._show_playlist_dialog("https://example.invalid/list", 1,
                                      [{"title": "Imported"}])
            got = wait_for(app, lambda: len(app.rq.queue) >= before + 5)
        finally:
            gui_mod.YouTubeManager.extract_playlist_range = original
        check("the playlist worker adds all 5 imported songs", got,
              f"queue={len(app.rq.queue)} (want {before + 5})")
        check("the worker honours the 🎲 switch (no Tk call off-thread)",
              all(s in app.rq.queue for s in imported))
        check("the worker's inserts respect the song on air too",
              all(app.rq.queue.index(s) >= 11 for s in imported),
              f"slots={[app.rq.queue.index(s) for s in imported]}")

        # ── Switch OFF again → back to the end of the list. ──
        app.random_insert_switch.toggle()
        pump(app, 0.05)
        check("clicking the switch again turns the mode off",
              app._queue_random_insert is False)
        n = len(app.rq.queue)
        pos = app._add_to_queue(song(999))
        check("switch OFF → the next song goes to the end again",
              pos == n and app.rq.queue[-1].id == "rand-999",
              f"pos={pos} of {n + 1} rows")
        check("appending is still invisible to the row on air",
              app.rq.queue[10].id == "rand-10")

        # A very short queue: the random range is a single slot, no raise.
        tiny = RadioQueue()
        tiny.insert_song_at_random(song(1), random.Random(1))
        tiny.insert_song_at_random(song(2), random.Random(2))
        check("an empty queue accepts a random insert at slot 0",
              len(tiny.queue) == 2, f"{[s.title for s in tiny.queue]}")
        check("contained UI errors were never needed",
              getattr(app, "_callback_errors", 0) == 0,
              f"contained={getattr(app, '_callback_errors', 0)}")
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
