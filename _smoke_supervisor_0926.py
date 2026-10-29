#!/usr/bin/env python3
"""Headless smoke for freq_supervisor.py (process-isolation, freeze 0926).

Runs the supervisor as a real subprocess against a controllable child:

  1. normal child      → heartbeats, exits 0  → supervisor exits 0, no restart
  2. stop-marker child → writes "0", exits 1  → supervisor still exits 0 (clean)
  3. hang child        → beats then freezes   → killed + restarted; 2nd run exits 0
  4. crash child       → exits 1 instantly    → after MAXR restarts supervisor rc=3

Plus: the REAL UiFreezeWatchdog writes a live heartbeat file when
FREQ_SUPERVISED is set (verified with a withdrawn CTk root, no audio).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS: list[tuple[str, bool, str]] = []
FAST_ENV = {
    "FREQ_SUPERVISOR_GRACE": "2",
    "FREQ_SUPERVISOR_WINDOW": "60",
    "FREQ_SUPERVISOR_MAXR": "2",
    "PYTHONUNBUFFERED": "1",
}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


CHILD_SRC = r'''
import os, sys, time
# Mode comes from the env (the supervisor spawns the target WITHOUT extra
# args — exactly like it will spawn gui.py).
mode = os.environ["FREQ_TEST_MODE"]
hb = os.environ["FREQ_SUPERVISOR_HEARTBEAT"]
state = os.environ["FREQ_TEST_STATE"]

def touch(v=None):
    tmp = hb + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(time.time() if v is None else v))
    os.replace(tmp, hb)

def run_count():
    try:
        with open(state) as f:
            return int(f.read().strip())
    except Exception:
        return 0

with open(state, "w") as f:
    f.write(str(run_count() + 1))
n = run_count()

if mode == "crash":
    sys.exit(1)                       # never heartbeats
if mode == "normal":
    for _ in range(2):
        touch(); time.sleep(0.6)
    sys.exit(0)
if mode == "stopmarker":
    touch(); time.sleep(0.5)
    touch("0"); time.sleep(0.5)
    sys.exit(1)                       # crash-like exit AFTER the marker
if mode == "hang":
    marker = state + ".hangdone"
    if os.path.exists(marker):        # a previous run already hung once
        touch(); time.sleep(0.8)
        sys.exit(0)
    for _ in range(3):
        touch(); time.sleep(0.6)
    with open(marker, "w") as f:      # mark BEFORE hanging (deterministic)
        f.write("1")
    time.sleep(999)                   # UI "hangs" here
sys.exit(2)
'''


def run_supervisor(mode: str, workdir: Path, timeout: float = 40.0):
    state = workdir / "state.txt"
    if state.exists():
        state.unlink()
    env = os.environ.copy()
    env.update(FAST_ENV)
    env["FREQ_TEST_MODE"] = mode
    env["FREQ_TEST_STATE"] = str(state)
    child = workdir / f"child_{mode}.py"
    child.write_text(CHILD_SRC, encoding="utf-8")
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "freq_supervisor.py"),
         "--supervise", str(child)],
        capture_output=True, timeout=timeout, env=env,
        cwd=str(workdir))
    proc.stdout = (proc.stdout or b"").decode("utf-8", "replace")
    proc.stderr = (proc.stderr or b"").decode("utf-8", "replace")
    return proc, time.time() - t0


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="freq_sup_smoke_"))

    # ── 1. normal child: clean exit, no restart ──
    proc, dt = run_supervisor("normal", workdir)
    check("normal child → supervisor exits 0",
          proc.returncode == 0, f"rc={proc.returncode} in {dt:.1f}s")
    check("normal child → no restart happened",
          "restarted" not in proc.stdout, proc.stdout.strip().splitlines()[-1]
          if proc.stdout.strip() else "-")

    # ── 2. stop-marker child: crash-like exit but clean-stop marker ──
    proc, dt = run_supervisor("stopmarker", workdir)
    check("clean-stop marker → supervisor exits 0 (no restart)",
          proc.returncode == 0 and "restarted" not in proc.stdout,
          f"rc={proc.returncode} in {dt:.1f}s")

    # ── 3. hang child: killed + restarted exactly once ──
    proc, dt = run_supervisor("hang", workdir)
    check("hang child → supervisor exits 0 after recovery",
          proc.returncode == 0, f"rc={proc.returncode} in {dt:.1f}s")
    restarts = proc.stdout.count("restarted child")
    kills = proc.stdout.count("killing + restarting")
    check("hang child → exactly one kill+restart",
          restarts == 1 and kills == 1,
          f"kills={kills} restarts={restarts}")
    check("hang child → stall was DETECTED via heartbeat",
          "heartbeat stalled" in proc.stdout, "")

    # ── 4. crash child: crash-loop guard gives up with rc=3 ──
    proc, dt = run_supervisor("crash", workdir)
    check("crash loop → supervisor gives up rc=3",
          proc.returncode == 3, f"rc={proc.returncode} in {dt:.1f}s")
    check("crash loop → restarts capped at MAXR",
          proc.stdout.count("restarted child") == 1,
          f"restarts={proc.stdout.count('restarted child')} (MAXR=2 → 1)")

    # ── 5. REAL watchdog heartbeat wiring (withdrawn window, no audio) ──
    try:
        os.environ["FREQ_SUPERVISED"] = "1"
        hb = str(workdir / "real_gui.hb")
        os.environ["FREQ_SUPERVISOR_HEARTBEAT"] = hb
        import customtkinter as ctk
        from gui import UiFreezeWatchdog
        root = ctk.CTk(); root.withdraw()
        wd = UiFreezeWatchdog(root)
        wd.start()
        # Wait for the FIRST write (thread start race), then confirm it
        # keeps updating while the UI loop pumps.
        t0 = time.time()
        while time.time() - t0 < 2.0 and not os.path.isfile(hb):
            root.update(); time.sleep(0.02)
        m1 = os.path.getmtime(hb) if os.path.isfile(hb) else 0
        t0 = time.time()
        while time.time() - t0 < 2.5:
            root.update(); time.sleep(0.02)
        m2 = os.path.getmtime(hb) if os.path.isfile(hb) else 0
        check("real watchdog writes a live heartbeat file",
              m1 > 0 and m2 > m1, f"mtime {m1:.1f} → {m2:.1f}")
        wd.stop()
        time.sleep(0.2)
        content = open(hb).read().strip() if os.path.isfile(hb) else "?"
        check("watchdog stop() writes the clean-stop marker",
              content == "0", f"content={content!r}")
        root.destroy()
    finally:
        os.environ.pop("FREQ_SUPERVISED", None)
        os.environ.pop("FREQ_SUPERVISOR_HEARTBEAT", None)

    print(f"\nsummary: {sum(1 for _, ok, _ in RESULTS if ok)}/{len(RESULTS)} passed")
    sys.exit(1 if any(not ok for _, ok, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
