#!/usr/bin/env python3
"""Headless smoke: UiFreezeWatchdog catches a deliberate UI-thread stall.

Verifies on a real Tk event loop that the watchdog:
  1. stays SILENT while the UI thread pumps normally
  2. logs a WARNING with a stack trace when the UI thread blocks >250 ms
  3. the stack names the actual blocking code (time.sleep in our lambda)
  4. logs the resume line after the stall ends
  5. stop() ends the watcher thread cleanly
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq: RadioQueue saves to a RELATIVE
# path in the cwd, so run the app from a throwaway directory.
import os, tempfile as _tf
os.chdir(_tf.mkdtemp(prefix="freq_watchdog_smoke_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from gui import UiFreezeWatchdog  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    import customtkinter as ctk

    root = ctk.CTk()
    root.withdraw()

    records: list[logging.LogRecord] = []
    handler = logging.Handler()  # noqa: E402  (capture everything)

    def _emit(rec: logging.LogRecord) -> None:
        records.append(rec)

    handler.emit = _emit  # type: ignore[method-assign]
    lg = logging.getLogger("freq.gui")
    lg.addHandler(handler)
    old_level = lg.level
    lg.setLevel(logging.DEBUG)

    wd = UiFreezeWatchdog(root, threshold=0.25, heartbeat_ms=50,
                          watch_interval=0.02)
    wd.start()
    try:
        # ── Phase 1: quiet pumping — watchdog must say nothing ──
        deadline = time.time() + 0.5
        while time.time() < deadline:
            root.update()
            time.sleep(0.005)
        quiet = [r for r in records if "stall" in r.getMessage()]
        check("no false positives while idle", not quiet,
              f"{len(quiet)} records")

        # ── Phase 2: deliberate 0.6 s block on the UI thread ──
        root.after(50, lambda: time.sleep(0.6))
        deadline = time.time() + 2.0
        while time.time() < deadline:
            root.update()
            time.sleep(0.005)

        stalls = [r for r in records if "stalled" in r.getMessage()]
        check("stall logged as WARNING", bool(stalls)
              and all(r.levelno == logging.WARNING for r in stalls),
              f"{len(stalls)} record(s): "
              f"{stalls[0].getMessage().splitlines()[0] if stalls else '-'}")
        check("stack trace names the blocking code",
              bool(stalls) and "time.sleep" in stalls[0].getMessage(),
              "stack captured" if stalls and "time.sleep" in stalls[0].getMessage()
              else (stalls[0].getMessage()[:120] if stalls else "-"))
        resumed = [r for r in records if "resumed" in r.getMessage()]
        check("resume logged after the stall", bool(resumed),
              resumed[0].getMessage() if resumed else "-")

        # ── Phase 3: stop() ends the watcher thread ──
        wd.stop()
        t = wd._thread
        t.join(timeout=2.0)
        check("watcher thread stops cleanly", not t.is_alive(),
              f"alive={t.is_alive()}")
    finally:
        lg.removeHandler(handler)
        lg.setLevel(old_level)
        try:
            wd.stop()
        except Exception:
            pass
        root.destroy()

    print(f"\nsummary: {sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} passed")
    sys.exit(1 if any(not ok for _, ok, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
