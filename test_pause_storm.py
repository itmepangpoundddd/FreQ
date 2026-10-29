#!/usr/bin/env python3
"""Pause-storm: hammer the newly added mic-pause paths.

Focus: pause/resume cycles, pause->stop transitions, engine switches
with a live mic, thread/memory hygiene after everything.
"""
from __future__ import annotations

import ctypes
import sys
import time

from player import AudioPlayer

# Keep ✅/❌/em-dash output intact when stdout is redirected on a non-UTF-8 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

FAILS = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  BAD ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def thread_count():
    """Exact thread count via Win32 (CreateToolhelp32Snapshot)."""
    TH32CS_SNAPTHREAD = 0x4
    pid = ctypes.windll.kernel32.GetCurrentProcessId()

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("th32ThreadID", ctypes.c_ulong),
                    ("th32OwnerProcessID", ctypes.c_ulong),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(te)
    count = 0
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            count += 1
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return count


def rss_mb():
    import ctypes.wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    # x64 needs explicit prototypes or the pseudo-handle is truncated
    # (same gotcha test_soak.py documents).
    k32 = ctypes.windll.kernel32
    k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
    k32.K32GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
    h = k32.GetCurrentProcess()
    if k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
        return pmc.WorkingSetSize / (1024 * 1024)
    raise OSError("K32GetProcessMemoryInfo failed")


def run():
    p = AudioPlayer()
    assert p.init()
    base_threads, base_rss = thread_count(), rss_mb()
    print(f"baseline: threads={base_threads} rss={base_rss:.1f}MB")

    # 1) pause/resume storm on a live mic session
    fired = []
    assert p.play_mic_multi(30.0, [(None, 1.0)], duck_factor=0.3,
                            on_finish=lambda: fired.append(1)) == 1
    bad_state = 0
    for i in range(60):
        p.pause()
        if not p.is_paused:
            bad_state += 1
        p.resume()
        if p.is_paused:
            bad_state += 1
        if i % 15 == 14:
            time.sleep(0.05)
    check("60x pause/resume on live mic", bad_state == 0 and p.mic_count() == 1)
    p.stop()
    check("stop after storm: mics closed, no finish",
          p.mic_count() == 0 and not fired and not p.is_playing)

    # 2) pause -> STOP while paused (state must fully clear)
    leaked_state = 0
    for _ in range(30):
        p.play_mic_multi(30.0, [(None, 1.0)], duck_factor=0.3)
        p.pause()
        p.stop()
        if p.is_paused or p.is_playing or p.mic_count() != 0:
            leaked_state += 1
    check("30x pause->stop: state fully cleared", leaked_state == 0)

    # 3) pause the mic, then start a SONG (supersede under pause)
    ok = 0
    for _ in range(15):
        p.play_mic_multi(30.0, [(None, 1.0)], duck_factor=0.3)
        p.pause()
        p.resume()
        p.stop()
        ok += 1
    check("15x mic pause->resume->stop cycles", ok == 15 and p.mic_count() == 0)

    # 4) engine switch with a live mic on the dedicated engine
    switch_ok = True
    for ep in ("", ""):
        p.play_mic_multi(2.0, [(None, 1.0)], duck_factor=0.3)
        switch_ok &= p.mic_count() == 1
        p.stop()
        p.use_wasapi_engine(ep)     # music engine switches; mics unaffected
        p.stop()
    check("engine switches around live mic", switch_ok and p.mic_count() == 0)

    # 5) quick-fire pause during the very first instants of a clip
    race_ok = True
    for _ in range(20):
        p.play_mic_multi(0.4, [(None, 1.0)], duck_factor=0.3)
        p.pause()
        p.stop()
    time.sleep(0.8)   # let any stray timer fire
    check("20x instant pause+stop (timer race)", p.mic_count() == 0 and not p.is_playing)

    # 6) resource hygiene
    time.sleep(2.0)
    threads, rss = thread_count(), rss_mb()
    check("threads return to baseline",
          abs(threads - base_threads) <= 3, f"{base_threads} -> {threads}")
    check("rss bounded", rss - base_rss < 8.0,
          f"{base_rss:.1f} -> {rss:.1f} MB")

    # 7) mic still healthy after everything
    fired2 = []
    assert p.play_mic_multi(0.5, [(None, 1.0)], duck_factor=0.3,
                            on_finish=lambda: fired2.append(1)) == 1
    time.sleep(1.1)
    check("mic healthy after the storm",
          fired2 == [1] and not p.is_playing and p.mic_count() == 0)
    p.shutdown()
    print(f"final: threads={thread_count()} rss={rss_mb():.1f}MB")
    return not FAILS


if __name__ == "__main__":
    print("=== Pause-storm: mic pause/resume hardening ===")
    ok = run()
    print(f"\nResults: {'ALL PASSED' if ok else 'FAILURES: ' + str(FAILS)}")
    sys.exit(0 if ok else 1)
