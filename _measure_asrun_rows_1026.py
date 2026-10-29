#!/usr/bin/env python3
"""Where does the as-run tab spend its time? (measurement, not a test)

The 20-minute soak caught a ~1.6 s Tk round trip right when it switched to the
as-run tab and asked for a forced refresh. This measures the two halves of that
refresh — destroying the previous cards and building the new ones — for a day
that has N items, so the fix can be aimed at the half that actually costs.

Run:  python _measure_asrun_rows_1026.py            (N = 30, 60, 90, 120)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_asrun_measure_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gui  # noqa: E402

WORK = Path.cwd() / "asrun"
WORK.mkdir(parents=True, exist_ok=True)


def main() -> int:
    app = gui.RadioApp()
    app.withdraw()
    app.asrun.configure(enabled=True, folder=str(WORK))
    app._build_asrun_tab()
    frame = app.asrun_rows_frame

    for n in (30, 60, 90, 120):
        items = [dict(kind="music", title=f"song {i}", artist="Soak",
                      actual_duration=6.0, ended_why="natural",
                      ts_start="2026-10-26T10:00:00", open=False)
                 for i in range(n)]
        # Empty first so "build" is measured on its own.
        for child in frame.winfo_children():
            child.destroy()
        app.update()
        t0 = time.perf_counter()
        for item in items:
            app._asrun_row(frame, item)
        build = time.perf_counter() - t0
        app.update()
        t1 = time.perf_counter()
        for child in frame.winfo_children():
            child.destroy()
        destroy = time.perf_counter() - t1
        app.update()
        print(f"n={n:3d}  build={build * 1000:7.0f} ms  "
              f"destroy={destroy * 1000:6.0f} ms  "
              f"total={(build + destroy) * 1000:7.0f} ms  "
              f"per row={build / n * 1000:5.1f} ms")
        sys.stdout.flush()

    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
