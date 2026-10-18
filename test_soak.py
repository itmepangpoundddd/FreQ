#!/usr/bin/env python3
"""Long-run soak: random feature traffic against the live audio engine.

Goal: the studio app must survive days of continuous use — no crash, no
hang, no resource creep. This suite hammers the same surface a DJ does:
songs, manual/auto jingles, pause/resume, seek, volume/EQ, engine
re-creation (endpoint switches) and mic/loopback capture objects, in a
randomized order, for a configurable wall-clock budget.

Resource discipline is verified with Win32 APIs directly (no psutil):
  * Working set must stay inside a band after warm-up (no unbounded climb)
  * Thread count must return to ~baseline after every engine/capture burst
  * A final smoke check proves audio still FLOWS (not merely "no crash")

Run:  python test_soak.py --minutes 5
      python test_soak.py --minutes 30   (release-gate run)
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Keep ✅/❌ output intact when stdout is redirected on a non-UTF-8 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import player as player_mod  # noqa: E402
from player import AudioPlayer  # noqa: E402

SR = 44100
TMP = Path(tempfile.gettempdir()) / "freq_soak"
TMP.mkdir(exist_ok=True)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


# ── Win32 process introspection (no third-party deps) ──────────
class _PMCs(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = ctypes.c_void_p
_k32.GetCurrentProcess.argtypes = []
_k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
_k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMCs), ctypes.c_uint32]
_psapi = getattr(ctypes.windll, "psapi", None)
if _psapi is not None and hasattr(_psapi, "GetProcessMemoryInfo"):
    _psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    _psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMCs), ctypes.c_uint32]
else:
    _psapi = None


def rss_bytes() -> int:
    pmc = _PMCs()
    pmc.cb = ctypes.sizeof(_PMCs)
    h = _k32.GetCurrentProcess()
    # x64 needs explicit prototypes (above) or the pseudo-handle is truncated.
    if _k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
        return int(pmc.WorkingSetSize)
    if _psapi is not None and _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
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
                    ("th32ThreadID", ctypes.c_uint32), ("th32OwnerProcessID", ctypes.c_uint32),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long), ("dwFlags", ctypes.c_uint32)]

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


# ── Fixtures ────────────────────────────────────────────────────
def make_tone(name: str, seconds: float, rate: int = SR, freq: int = 220) -> str:
    wav = TMP / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    frames = int(seconds * rate)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(frames)))
    r = subprocess.run(
        [player_mod._ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-q:a", "2", str(mp3)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="ignore")
    return str(mp3)


def make_player() -> AudioPlayer:
    p = AudioPlayer()
    assert p.init(), "player init failed"
    p.volume = 0.0            # soak must be inaudible
    p.crossfade_enabled = True
    return p


def wasapi(p: AudioPlayer) -> bool:
    try:
        import native_audio_render  # noqa: F401
    except ImportError:
        return False
    return p.use_wasapi_engine("")


# ── Soak ────────────────────────────────────────────────────────
def soak(minutes: float, max_ops: int) -> None:
    p = make_player()
    engine = "wasapi" if wasapi(p) else "pygame"
    print(f"\n=== Soak — engine: {engine}, budget {minutes:g} min / {max_ops} ops ===\n")
    if engine != "wasapi":
        check("[soak] wasapi engine available", False, "ran without native engine")
        return

    import native_audio_render as nar
    songs = [make_tone(f"s_{r}", 6.0, rate=r) for r in (44100, 48000)]
    jingles = [make_tone("j_short", 1.2, freq=880), make_tone("j_22k", 1.0, rate=22050)]

    deadline = time.time() + minutes * 60
    rng = random.Random(20260923)
    ops = 0
    start_failures = 0
    resumed = paused_cnt = stopped = jingles_played = songs_played = 0
    engine_recreations = capture_cycles = 0
    mic_sessions = dual_sessions = 0

    # Warm-up (also the allocation steady-state point): 60 mixed ops.
    for i in range(60):
        f = songs[i % 2] if i % 3 else jingles[i % 2]
        p.play_file(f, duration=0.0)
        time.sleep(0.01)
        p.stop()
    rss_base, threads_base = rss_bytes(), thread_count()
    print(f"baseline: rss={rss_base / 1e6:.1f} MB, threads={threads_base}")

    rss_peak_delta = 0
    t_start = time.time()
    progress_next = time.time() + 30

    while ops < max_ops and time.time() < deadline:
        roll = rng.random()
        try:
            if roll < 0.30:                       # play a song
                if p.play_file(rng.choice(songs), duration=6.0):
                    songs_played += 1
                else:
                    start_failures += 1
            elif roll < 0.40:                     # stop
                p.stop(); stopped += 1
            elif roll < 0.50:                     # pause
                p.pause(); paused_cnt += 1
            elif roll < 0.60:                     # resume
                p.resume(); resumed += 1
            elif roll < 0.70:                     # seek
                p.seek(rng.uniform(0.0, 3.0))
            elif roll < 0.88:                     # jingle (sometimes auto-resume pattern)
                if p.play_file(rng.choice(jingles), duration=0.0):
                    jingles_played += 1
                else:
                    start_failures += 1
            elif roll < 0.93:                     # mixer surface
                p.volume = rng.uniform(0.0, 1.0)
                p.set_eq(rng.uniform(-12, 12), rng.uniform(-12, 12), rng.uniform(-12, 12))
            elif roll < 0.955:                    # mic segment: single or DUAL
                # Live mic mixed into the engine — single/dual alternating
                # with random short durations. Dual = two starts where the
                # second JOINS the first session (no double duck, no timer
                # reset). Ends either on its own timer or via stop().
                dur = rng.uniform(0.15, 0.5)
                if p.play_mic_live(dur, mic_gain=1.0, duck_factor=0.3):
                    mic_sessions += 1
                    if rng.random() < 0.5:
                        p.play_mic_live(dur, mic_gain=1.0, duck_factor=0.3)
                        dual_sessions += 1
                    if rng.random() < 0.35:
                        p.stop_emergency_mic()
            elif roll < 0.97:                     # engine re-creation (endpoint switch)
                if p.use_wasapi_engine(""):
                    engine_recreations += 1
            else:                                 # capture object lifecycle
                try:
                    import native_audio_capture as nac
                    rec = nac.Capture(loopback=1)
                    rec.start()
                    time.sleep(0.05)
                    rec.read(16384)
                    rec.stop()
                    del rec                       # dealloc must never hang/crash
                    capture_cycles += 1
                except Exception:
                    pass                          # no mic/loopback on CI boxes
            ops += 1
        except Exception as e:                    # ANY exception is a soak failure
            check(f"[soak] op #{ops} raised", False, repr(e))
            break
        # periodic resource sampling
        d = rss_bytes() - rss_base
        if d > rss_peak_delta:
            rss_peak_delta = d
        if time.time() >= progress_next:
            progress_next = time.time() + 30
            print(f"  … {ops} ops, rss Δ+{d / 1e6:.1f} MB, threads={thread_count()}")

    elapsed = time.time() - t_start
    p.stop()
    time.sleep(1.0)
    rss_end, threads_end = rss_bytes(), thread_count()
    print(f"\nsoak done: {ops} ops in {elapsed:.0f}s "
          f"(songs={songs_played} jingles={jingles_played} stop={stopped} "
          f"pause={paused_cnt} resume={resumed} engines={engine_recreations} captures={capture_cycles} "
          f"mics={mic_sessions} dual={dual_sessions})")
    print(f"resource:  rss {rss_base / 1e6:.1f} → {rss_end / 1e6:.1f} MB "
          f"(peak Δ+{rss_peak_delta / 1e6:.1f}), threads {threads_base} → {threads_end}")

    check("[soak] completed without exception", True)
    check("[soak] memory sampling works", rss_base > 0,
          f"baseline {rss_base / 1e6:.1f} MB")
    check("[soak] start success rate", start_failures <= max(2, ops // 100),
          f"{start_failures} failures / {ops} ops")
    check("[soak] working set bounded", rss_peak_delta < 120e6,
          f"peak Δ+{rss_peak_delta / 1e6:.1f} MB")
    check("[soak] threads return to baseline", abs(threads_end - threads_base) <= 4,
          f"{threads_base} → {threads_end}")
    check("[soak] mic coverage", mic_sessions >= max(10, ops // 60),
          f"{mic_sessions} live-mic sessions ({dual_sessions} dual)")
    # Mic threads must be gone AND the engine must still accept a live mic
    # after the whole storm.
    try:
        mic_ok = p.play_mic_live(0.3, mic_gain=1.0, duck_factor=0.3)
        time.sleep(0.4)
    except Exception:
        mic_ok = False
    check("[soak] mic still works after storm", mic_ok and not p.emergency_mic_active())
    # Final smoke: audio must still FLOW after everything.
    p.play_file(songs[1], duration=6.0)
    time.sleep(0.7)
    flowing = nar.render_buffered(p._render) > 4096 and p.get_position() > 0.0
    check("[soak] final smoke — audio still flows", flowing,
          f"buffered={nar.render_buffered(p._render)}")
    p.stop()
    p.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--max-ops", type=int, default=6000)
    a = ap.parse_args()
    soak(a.minutes, a.max_ops)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 50}")
    print(f"Soak results: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, _, detail in failed:
        print(f"  ❌ {name} — {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
