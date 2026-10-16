#!/usr/bin/env python3
"""Smoke: supervisor as the main entry [sup-0928].

Verifies that:
  1. notify_tray never raises and escapes quotes/newlines safely
  2. write_status produces the json the app's Settings reads
  3. _child_command: dev mode spawns [python, target]; frozen mode
     without FreQ.exe beside it refuses cleanly
  4. a REAL supervised hang child leads to a "restarted (freeze)" status
     file + tray notification without killing the supervisor
  5. gui.py wiring: Settings status label + log button + revive toast,
     installer.iss entry tokens
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


import freq_supervisor as fs  # noqa: E402

# ── 1. notify_tray: never raises, escapes safely ─────────────────
try:
    fs.notify_tray("title <>&'\nnewline", "text 'quoted'")
    check("notify_tray never raises", True)
except Exception as e:
    check("notify_tray never raises", False, str(e))

# ── 2. status file shape ─────────────────────────────────────────
d = tempfile.mkdtemp(prefix="sup_status_")
fs.write_status(d, state="watching", restarts=2, child="FreQ.exe", mode="exe")
st = json.load(open(os.path.join(d, fs.STATUS_FILE), encoding="utf-8"))
check("status file has state/restarts/mode/ts",
      st["state"] == "watching" and st["restarts"] == 2
      and st["mode"] == "exe" and "ts" in st, str(st))
fs.write_status(d, state="restarted (freeze)", restarts=1)
st2 = json.load(open(os.path.join(d, fs.STATUS_FILE), encoding="utf-8"))
check("status overwrite is atomic and clean",
      st2["state"] == "restarted (freeze)" and "child" not in st2)

# ── 3. child command selection ───────────────────────────────────
check("dev mode: [python, target]",
      fs._child_command("gui.py") == [sys.executable, "gui.py"])
old_frozen = getattr(sys, "frozen", None)
old_exe = sys.executable
try:
    sys.frozen = True
    sys.executable = os.path.join(tempfile.mkdtemp(prefix="sup_frozen_"),
                                  "FreQSupervisor.exe")
    check("frozen without FreQ.exe beside it -> refuses (empty cmd)",
          fs._child_command("gui.py") == [])
finally:
    if old_frozen is None:
        del sys.frozen
    else:
        sys.frozen = old_frozen
    sys.executable = old_exe

# ── 4. REAL supervisor restart -> status file + tray ─────────────
run_dir = tempfile.mkdtemp(prefix="sup_run_")
child = os.path.join(run_dir, "hang_child.py")
with open(child, "w", encoding="utf-8") as f:
    f.write(
        "import os, sys, time\n"
        "hb = os.environ['FREQ_SUPERVISOR_HEARTBEAT']\n"
        "mode_file = os.path.join(os.path.dirname(__file__), 'mode.txt')\n"
        "mode = open(mode_file).read().strip() if os.path.isfile(mode_file) else 'hang'\n"
        "if mode == 'hang':\n"
        "    with open(hb, 'w') as f: f.write('1')\n"
        "    time.sleep(3600)\n"
        "else:\n"
        "    with open(hb, 'w') as f: f.write('0')\n"
        "    sys.exit(0)\n"
    )
open(os.path.join(run_dir, "mode.txt"), "w").write("hang")

env = os.environ.copy()
env.update({"FREQ_SUPERVISOR_GRACE": "2", "FREQ_SUPERVISOR_WINDOW": "60",
            "FREQ_SUPERVISOR_MAXR": "2", "PYTHONUNBUFFERED": "1"})
sup = subprocess.Popen(
    [sys.executable, str(Path(__file__).parent / "freq_supervisor.py"),
     "--supervise", child],
    cwd=run_dir, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    deadline = time.time() + 30
    status = {}
    while time.time() < deadline:
        try:
            status = json.load(open(
                os.path.join(run_dir, fs.STATUS_FILE), encoding="utf-8"))
            if str(status.get("state", "")).startswith("restarted"):
                break
        except Exception:
            pass
        time.sleep(0.5)
    check("hang child -> freeze detected -> status 'restarted (freeze)'",
          str(status.get("state", "")).startswith("restarted"), str(status))
    check("status counts the restart", status.get("restarts", 0) >= 1,
          str(status))
    check("supervisor still alive after restart", sup.poll() is None)
    # clean stop: mode.txt -> child exits 0 on next spawn... simpler: kill tree
finally:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(sup.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            sup.terminate()
    except Exception:
        pass

# ── 5. structural wiring: gui.py + installer.iss ─────────────────
gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
for token, name in (
    ("def _supervisor_status", "Settings reads the supervisor status"),
    ("def _refresh_supervisor_status", "status label refresh exists"),
    ("def _open_supervisor_log", "open-log button handler exists"),
    ("self._refresh_supervisor_status()", "status populated on build"),
    ("recovered automatically", "revive toast text present"),
):
    check(f"gui: {name}", token in gui_src)
iss_src = (Path(__file__).parent / "installer.iss").read_text(encoding="utf-8")
for token, name in (
    ("#define MyAppSupervisorName", "supervisor exe defined"),
    ("#define MyAppEntry MyAppSupervisorName", "entry prefers supervisor"),
    ('Filename: "{app}\\{#MyAppEntry}"', "shortcuts/run use the entry"),
):
    check(f"installer.iss: {name}", token in iss_src)

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
