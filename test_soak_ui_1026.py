#!/usr/bin/env python3
"""20-minute soak of the REAL app: rundown + queue UI + as-run.

Drives `RadioApp` the way an operator does — songs play, ad/jingle breaks
fire on their counters, the per-song end marks stop the rundown (and the
operator starts it again), the queue scrolls, sidebar tabs switch, the
search box filters, marks are clicked and the as-run log is refreshed — for
a wall-clock budget with silent audio, so a studio can be left running.

The whole run happens inside Tk's own `mainloop()`, exactly like production:
pumping with `update()` instead would break the player's finish callback
(tkinter refuses `after()` from the monitor thread unless the main thread is
really in the loop), and a test that cannot reproduce a track change proves
nothing.

What proves "no hiccup":
  * every contained UI error the app reports is counted (one = failure)
  * a Tk round trip (`after(0)` → executed) is timed every few seconds:
    the worst one is the stall evidence, not an average
  * working set and thread count are sampled with Win32 APIs (no psutil)
  * the as-run chain must still verify clean and grow the whole time
  * at the end the app must still accept Play, report a position and
    close itself without raising

The app runs in a throwaway folder, so the studio's radio_queue.freq is
never touched (the test asserts its mtime is unchanged).

Run:  python test_soak_ui_1026.py --minutes 20
      python test_soak_ui_1026.py --minutes 1     (quick smoke of the harness)
"""

from __future__ import annotations

import argparse
import ctypes
import os
import random
import subprocess
import sys
import tempfile
import time
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


# ── Win32 process introspection (no third-party deps) ──────────
class _PMCs(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_k32.GetCurrentProcess.argtypes = []
_k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
_k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMCs),
                                         ctypes.c_uint32]


def rss_bytes() -> int:
    pmc = _PMCs()
    pmc.cb = ctypes.sizeof(_PMCs)
    h = _k32.GetCurrentProcess()
    if _k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
        return int(pmc.WorkingSetSize)
    return 0


def thread_count() -> int:
    TH32CS_SNAPTHREAD = 0x4
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == -1:
        return -1

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
                    ("th32ThreadID", ctypes.c_uint32),
                    ("th32OwnerProcessID", ctypes.c_uint32),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", ctypes.c_uint32)]

    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(THREADENTRY32)
    pid = k32.GetCurrentProcessId()
    n = 0
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            n += 1
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return n


# ── Silent audio fixtures ──────────────────────────────────────
import player as player_mod  # noqa: E402  (needs ROOT on sys.path)


def make_silence(work: Path, name: str, seconds: float,
                 rate: int = 44100) -> str:
    """A silent track of the given length (mp3 — the engine's normal input)."""
    wav = work / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    r = subprocess.run(
        [player_mod._ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-q:a", "4", str(mp3)], capture_output=True)
    if r.returncode != 0 or not mp3.is_file():
        raise SystemExit(f"ffmpeg could not build {name}: "
                         f"{r.stderr.decode(errors='ignore')[-400:]}")
    wav.unlink(missing_ok=True)
    return str(mp3)


WORK = Path(tempfile.mkdtemp(prefix="freq_soak_ui_"))
STUDIO_PLAYLIST = ROOT / "radio_queue.freq"
STUDIO_MTIME = STUDIO_PLAYLIST.stat().st_mtime if STUDIO_PLAYLIST.exists() else None
os.chdir(WORK)                     # autosave/close land here, never on the studio file

import gui as gui_mod  # noqa: E402
from gui import RadioApp  # noqa: E402
from radio_manager import Song  # noqa: E402

MODES = ("auto", "auto", "break", "auto", "stop", "auto", "break_stop", "auto")
TICK_MS = 20                       # how often the soak looks at the app
PING_EVERY = 5.0                   # seconds between UI round-trip measurements
SAMPLE_EVERY = 30.0                # seconds between resource samples
ACT_EVERY = 2.0                    # seconds between UI interactions


def build_tracks(work: Path, count: int, offset: int = 0) -> list[tuple[str, str]]:
    """(path, end_mode) per song — short silent tracks with mixed end marks."""
    out = []
    rng = random.Random(1000 + offset)
    for i in range(count):
        secs = rng.uniform(5.0, 8.0)
        out.append((make_silence(work, f"t{offset + i:03d}", secs),
                    MODES[(offset + i) % len(MODES)]))
    return out


def add_songs(app, tracks: list[tuple[str, str]], tag: str) -> None:
    for i, (path, mode) in enumerate(tracks):
        app.rq.queue.append(Song(title=f"{tag} {i}", artist="Soak",
                                 duration=6.0, source="local",
                                 file_path=path, id=f"{tag}{i}",
                                 end_mode=mode))


class _Event:
    """Stand-in for a Tk mouse event (only x/y/state are read)."""
    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.x, self.y, self.state = x, y, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--batch", type=int, default=24)
    args = ap.parse_args()

    print("=" * 64)
    print(f"  FreQ — UI soak ({args.minutes:g} min): rundown + queue + as-run")
    print(f"  working folder: {WORK}")
    print("=" * 64)

    tracks = build_tracks(WORK, args.batch)
    ads = [make_silence(WORK, "ad1", 3.0), make_silence(WORK, "ad2", 2.5)]
    jingle = make_silence(WORK, "jingle", 2.0)

    app = RadioApp()

    # Every contained UI error is a soak failure — the app must not lean on
    # its own error containment to get through the run.
    errors: list[str] = []
    real_report = app.report_callback_exception

    def report(exc, val, tb):
        errors.append("".join(traceback.format_exception(exc, val, tb))[-500:])
        try:
            real_report(exc, val, tb)
        except Exception:
            pass

    app.report_callback_exception = report

    app.asrun.configure(enabled=True, folder=str(WORK / "asrun"))
    add_songs(app, tracks, "run")
    app.commercial.enabled = True
    app.commercial.ads = list(ads)
    app.commercial.interval = 4
    app.jingle.enabled = True
    app.jingle.jingle_file = jingle
    app.jingle.interval = 6
    app.volume_var.set(0.0)
    app.rq.current_index = 0
    app._play_index(0)

    state = {
        "pings": [], "ping_misses": 0, "advances": 0, "restarts": 0,
        "batches": 0, "rss_peak": 0, "rss_base": 0, "thr_base": 0,
        "rss_end": 0, "thr_end": 0, "fatal": None, "stopped_at": 0.0,
        "last_index": app.rq.current_index, "started": 0.0,
        "final": {"play_ok": False, "flowing": False, "pos": 0.0,
                  "ping_ok": False, "fired": []},
    }

    def mark_span():
        card_right = max(gui_mod.QUEUE_CARD_PAD + gui_mod.QUEUE_CARD_MIN_W,
                         app._queue_canvas_width() - gui_mod.QUEUE_CARD_PAD)
        return app._queue_mark_span(card_right)

    def interact(rng) -> None:
        """One thing an operator might touch, chosen at random."""
        action = rng.randrange(8)
        parent = getattr(app.queue_frame, "_parent_canvas", None)
        if action == 0 and parent is not None:
            parent.yview_moveto(rng.uniform(0.0, 1.0))
        elif action == 1:
            _left, right = mark_span()
            pos = rng.choice(sorted(app._queue_items) or [0])
            app._on_queue_canvas_press(_Event(
                right - 3, pos * gui_mod.QUEUE_ROW_H + gui_mod.QUEUE_CARD_H // 2))
        elif action == 2:
            idx = rng.randrange(len(app._tab_names))
            app._on_sidebar_tab_switch(idx, app._tab_names[idx])
        elif action in (3, 4):
            entry = app.queue_search_entry
            entry.delete(0, "end")
            if action == 3:
                entry.insert(0, rng.choice(("run", "Soak", "zzz")))
            app._filter_queue()
        elif action == 5:
            app._refresh_queue()
        elif action == 6:
            app._refresh_queue_markers()
        elif app._current_sidebar_tab == "As-run":
            app._refresh_asrun(force=True)

    # ── Final validation, still inside the mainloop (so `after()` from the
    # player's monitor thread keeps working until the very end) ──
    def finalize() -> None:
        try:
            state["final"]["play_ok"] = bool(True)
            idx = max(0, min(app.rq.current_index, len(app.rq.queue) - 1))
            app._playing = True
            app._play_index(idx)
        except Exception:
            state["fatal"] = traceback.format_exc()
        app.after(900, final_sample)

    def final_sample() -> None:
        try:
            state["final"]["pos"] = app.audio_player.get_position()
            state["final"]["flowing"] = (state["final"]["pos"] > 0.0
                                        and app.audio_player.is_playing)
            state["final"]["play_ok"] = bool(app._playing)
        except Exception:
            state["fatal"] = traceback.format_exc()
        t0 = time.perf_counter()
        app.after(0, lambda: state["final"]["fired"].append(time.perf_counter() - t0))
        app.after(250, lambda: (state["final"].__setitem__(
            "ping_ok", bool(state["final"]["fired"])
            and state["final"]["fired"][0] < 0.5), app.quit()))

    def tick() -> None:
        try:
            now = time.time()
            if now >= state["deadline"]:
                app.after(100, finalize)
                return
            rng = state["rng"]

            # the rundown moved on by itself
            if app.rq.current_index != state["last_index"]:
                state["advances"] += 1
                state["last_index"] = app.rq.current_index

            # it stopped (stop mark / break+stop / end of queue): after a
            # moment the operator starts it again
            if not app._playing and not app._ad_playing and not app._jingle_playing:
                if not state["stopped_at"]:
                    state["stopped_at"] = now
                elif now - state["stopped_at"] >= 0.6:
                    state["stopped_at"] = 0.0
                    if app.rq.current_index + 1 < len(app.rq.queue):
                        app._next_track()
                        state["restarts"] += 1
                    else:
                        n = state["batches"] + 1
                        add_songs(app, build_tracks(WORK, 12, offset=n * 100),
                                  f"run{n}")
                        state["batches"] = n
                        app._refresh_queue()
                        app._play_index(app.rq.current_index + 1)
                    state["last_index"] = app.rq.current_index
            else:
                state["stopped_at"] = 0.0

            if now >= state["next_act"]:
                state["next_act"] = now + ACT_EVERY
                interact(rng)

            # UI round trip: how long the event loop takes to come back
            if now >= state["next_ping"]:
                state["next_ping"] = now + PING_EVERY
                t0 = time.perf_counter()
                fired: list[float] = []
                app.after(0, lambda: fired.append(time.perf_counter() - t0))
                app.after(int(PING_EVERY * 1000 * 0.8), lambda: (
                    state["pings"].append(fired[0]) if fired
                    else state.__setitem__("ping_misses",
                                           state["ping_misses"] + 1)))

            if now >= state["next_sample"]:
                state["next_sample"] = now + SAMPLE_EVERY
                delta = rss_bytes() - state["rss_base"]
                state["rss_peak"] = max(state["rss_peak"], delta)
                state["rss_end"] = rss_bytes()
                state["thr_end"] = thread_count()
                stats = app.asrun.stats()
                pm = max(state["pings"]) * 1000 if state["pings"] else 0.0
                print(f"  … {int(now - state['started']):3d}s  "
                      f"queue={len(app.rq.queue):3d} drew={len(app._queue_items):2d} "
                      f"advanced={state['advances']:3d} "
                      f"restarts={state['restarts']:2d} air={stats['items']:3d} "
                      f"rss=+{delta / 1e6:5.1f}MB thr={state['thr_end']} "
                      f"ping_max={pm:.0f}ms", flush=True)
        except Exception:
            state["fatal"] = traceback.format_exc()
            try:
                app.quit()
            except Exception:
                pass
            return
        app.after(TICK_MS, tick)

    # Warm-up before the baseline: window laid out, engine open, first item
    # already on air.
    def warm() -> None:
        for _ in range(10):
            app.update()
            time.sleep(0.05)
        state["rss_base"], state["thr_base"] = rss_bytes(), thread_count()
        state["rss_end"], state["thr_end"] = state["rss_base"], state["thr_base"]
        state["rng"] = random.Random(1026)
        state["started"] = time.time()
        state["deadline"] = state["started"] + args.minutes * 60
        state["next_act"] = state["started"] + ACT_EVERY
        state["next_ping"] = state["started"] + PING_EVERY
        state["next_sample"] = state["started"] + SAMPLE_EVERY
        print(f"\nbaseline: rss={state['rss_base'] / 1e6:.1f} MB, "
              f"threads={state['thr_base']}, queue={len(app.rq.queue)}")
        print(f"engine: {app.audio_player.engine} · as-run: "
              f"{app.asrun.stats()['items']} item(s)")
        print(f"budget: {args.minutes:g} min\n", flush=True)
        app.after(TICK_MS, tick)

    app.after(50, warm)
    app.mainloop()
    elapsed = time.time() - state["started"] if state["started"] else 0.0

    # ── what the run produced ──
    queue_len = len(app.rq.queue)
    drew = len(app._queue_items)
    close_error = ""
    try:
        app.after(0, app.quit)      # no-op if already left the loop
    except Exception:
        pass
    try:
        app._on_close()             # seals the day: no item may stay open
    except Exception:
        close_error = traceback.format_exc()
        try:
            app.destroy()
        except Exception:
            pass
    day = app.asrun._today()
    stats = app.asrun.stats()
    verify = app.asrun.verify(day)

    print(f"\nrun finished: {elapsed:.0f}s, {state['advances']} track changes, "
          f"{state['restarts']} operator restarts, {state['batches']} queue "
          f"extensions, {len(state['pings'])} UI pings")

    print("\n" + "=" * 64)
    print("  Result")
    print("=" * 64)
    pings = state["pings"]
    p95 = (sorted(pings)[min(int(len(pings) * 0.95), len(pings) - 1)]
           if pings else 0.0)
    fatal = state["fatal"]
    check("ran the whole budget without a fatal error", fatal is None,
          (fatal or "").strip().splitlines()[-1] if fatal else "")
    check("no UI callback raised for the whole run", not errors,
          f"{len(errors)} contained error(s): {errors[:1]}")
    check("the event loop never stalled (worst round trip < 1 s)",
          bool(pings) and max(pings) < 1.0,
          f"worst={max(pings) * 1000 if pings else 0:.0f} ms over {len(pings)} pings")
    check("… and stayed snappy (95th percentile < 250 ms)", bool(pings) and p95 < 0.25,
          f"p95={p95 * 1000:.0f} ms")
    check("every scheduled ping was delivered", state["ping_misses"] == 0,
          f"{state['ping_misses']} missed")
    check("the rundown kept moving",
          state["advances"] >= max(3, int(args.minutes * 3)),
          f"{state['advances']} track changes in {elapsed / 60:.1f} min")
    check("the end marks stopped the rundown and it resumed",
          state["restarts"] >= 1, f"{state['restarts']} restart(s)")
    check("the queue kept growing without stalling the list",
          queue_len == args.batch + state["batches"] * 12,
          f"{queue_len} songs, {state['batches']} extension(s), "
          f"{drew} row(s) drawn")
    check("memory stayed inside a band", state["rss_peak"] < 150e6,
          f"peak Δ+{state['rss_peak'] / 1e6:.1f} MB, end "
          f"{(state['rss_end'] - state['rss_base']) / 1e6:+.1f} MB")
    check("threads returned to baseline", abs(state["thr_end"] - state["thr_base"]) <= 4,
          f"{state['thr_base']} → {state['thr_end']}")
    check("as-run recorded the whole air time", stats["items"] >= state["advances"],
          f"{stats['items']} item(s) for {state['advances']} track change(s)")
    check("the as-run chain still verifies clean", bool(verify.get("ok")),
          f"state={verify.get('state')} errors={verify.get('errors')}")
    check("no as-run item was left open", stats.get("open", 0) == 0,
          f"{stats.get('open', 0)} open")
    fin = state["final"]
    check("audio still flowed at the end", fin["flowing"] and fin["play_ok"],
          f"position={fin['pos']:.2f}s playing={fin['play_ok']}")
    check("the UI still answered right away at the end", fin["ping_ok"],
          str([round(x * 1000) for x in fin["fired"]]))
    check("the app closed itself without raising", not close_error,
          close_error.strip().splitlines()[-1] if close_error else "")
    saved = WORK / "radio_queue.freq"
    check("autosave wrote the queue to the working folder",
          saved.is_file() and saved.stat().st_size > 1000,
          f"{saved.stat().st_size if saved.is_file() else 0} bytes")
    studio_now = (STUDIO_PLAYLIST.stat().st_mtime
                  if STUDIO_PLAYLIST.exists() else None)
    check("the studio playlist was never touched", studio_now == STUDIO_MTIME,
          f"{STUDIO_MTIME} → {studio_now}")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 64}")
    print(f"Soak results: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, _, detail in failed:
        print(f"  FAIL {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
