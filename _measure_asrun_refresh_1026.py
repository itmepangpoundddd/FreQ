#!/usr/bin/env python3
"""How long does the GUI thread block while the as-run tab redraws?

The 20-minute soak measured a 1.64 s Tk round trip at the moment it asked for a
forced as-run refresh on a day that had grown. This reproduces that one moment
deterministically: a day with N aired items, a forced refresh, and a ping every
20 ms timed exactly like the soak times it.

Run:  python _measure_asrun_refresh_1026.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_asrun_refresh_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gui  # noqa: E402

WORK = Path.cwd() / "asrun"
WORK.mkdir(parents=True, exist_ok=True)
ITEMS = 200


def main() -> int:
    app = gui.RadioApp()
    app.withdraw()
    app.asrun.configure(enabled=True, folder=str(WORK))

    # A real day: ITEMS aired items, closed in order, written by asrun itself.
    for i in range(ITEMS):
        app._asrun_begin("music", title=f"song {i}", artist="Soak")
        app._asrun_end_active("natural")
    print(f"seeded: {app.asrun.stats()['items']} item(s) on "
          f"{app.asrun._today()}")

    app._build_asrun_tab()
    app._on_sidebar_tab_switch(app._tab_names.index("As-run"), "As-run")
    app.update()

    pings: list[float] = []

    def ping() -> None:
        t0 = time.perf_counter()
        app.after(0, lambda: pings.append(time.perf_counter() - t0))
        app.after(20, ping)

    def do_refresh() -> None:
        mark = len(pings)
        t0 = time.perf_counter()
        app._refresh_asrun(force=True)
        call = time.perf_counter() - t0
        # let the sliced build finish, timing the loop the whole way
        app.after(6000, lambda: report(call, mark))

    def report(call: float, mark: int) -> None:
        rows = len(app.asrun_rows_frame.winfo_children())
        worst = max(pings[mark:]) * 1000 if len(pings) > mark else 0.0
        print(f"refresh call returned in {call * 1000:.0f} ms · "
              f"worst GUI round trip during the redraw = {worst:.0f} ms · "
              f"{rows} row(s) drawn")
        app.quit()

    app.after(300, do_refresh)
    app.after(300, ping)
    app.after(8000, app.quit)
    app.mainloop()
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
