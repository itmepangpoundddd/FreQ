#!/usr/bin/env python3
"""FreQ process supervisor — the UI runs as a child process.

Lesson from the 0926 freeze (Silence Sentinel deadlock, caught live by
py-spy): an in-process watchdog can log a hung UI thread, but nothing
inside a deadlocked process can rescue it. This supervisor runs OUTSIDE:

  * spawns gui.py (or any entry script) as a child process
  * the child touches a heartbeat file every second (gui.py's
    UiFreezeWatchdog does this when FREQ_SUPERVISED is set)
  * heartbeat stalls > grace  → the child is hung → taskkill the whole
    tree and start a fresh one (the queue lives in radio_queue.freq,
    autosaved every 60 s — a restart loses at most one autosave)
  * child exits 0 (user closed the window, or the watchdog's clean-stop
    heartbeat marker) → supervisor exits too
  * child keeps crashing → crash-loop guard: after N crashes inside the
    window the supervisor gives up instead of spinning forever

Usage:
    python freq_supervisor.py               # supervise gui.py beside it
    python freq_supervisor.py --supervise app.py

Env knobs (used by the selftest):
    FREQ_SUPERVISOR_GRACE   stall grace seconds   (default 8.0)
    FREQ_SUPERVISOR_WINDOW  crash-loop window s   (default 120.0)
    FREQ_SUPERVISOR_MAXR    max restarts/window   (default 3)

Exit codes: 0 clean, 3 gave up (crash loop), 4 bad usage.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

SUPERVISOR_LOG = "freq-supervisor.log"
CLEAN_STOP_MARKER = "0"          # heartbeat content written on clean exit
STATUS_FILE = "freq-supervisor-status.json"   # read by the app's Settings

# A broken tray notification must never take the supervisor down:
# PowerShell balloon or Windows toast, whichever the OS offers.
_TRAY_PS = (
    "[Windows.Forms.Application]::EnableVisualStyles();"
    "$n=New-Object Windows.Forms.NotifyIcon;"
    "$n.Icon=[System.Drawing.SystemIcons]::Information;"
    "$n.Visible=$true;"
    "$n.BalloonTipTitle='{title}';"
    "$n.BalloonTipText='{text}';"
    "$n.ShowBalloonTip(5000);"
    "Start-Sleep -Seconds 6; $n.Dispose()"
)


def notify_tray(title: str, text: str) -> None:
    """Fire-and-forget Windows tray notification (no dependencies).

    Runs detached so a slow/missing PowerShell can never block the
    supervision loop; every failure is silently ignored — a notification
    is cosmetic, recovery is not.
    """
    if sys.platform != "win32":
        return

    def _enc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&amp;")
                .replace(">", "&gt;").replace("'", """''""").replace("\n", " "))

    ps = _TRAY_PS.format(title=_enc(title), text=_enc(text))
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
             "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def write_status(dir_path: str, **fields) -> None:
    """Best-effort status file for the app's Settings page."""
    try:
        import json
        fields["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = os.path.join(dir_path, STATUS_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(fields, f)
        os.replace(tmp, os.path.join(dir_path, STATUS_FILE))
    except Exception:
        pass


def _log(dir_path: str, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}".encode(
        "ascii", "replace").decode("ascii")
    print(line, flush=True)
    try:
        with open(os.path.join(dir_path, SUPERVISOR_LOG), "a",
                  encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_heartbeat(path: str) -> str:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _child_env(heartbeat_path: str) -> dict:
    env = os.environ.copy()
    env["FREQ_SUPERVISED"] = "1"
    env["FREQ_SUPERVISOR_HEARTBEAT"] = heartbeat_path
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _child_command(target: str) -> list:
    """How to launch the child in both dev and packaged layouts.

    - dev:      [python, gui.py]
    - packaged: [FreQ.exe]  (windowed PyInstaller build beside the
                supervisor exe — no console, same folder)
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        exe = os.path.join(base, "FreQ.exe")
        if os.path.isfile(exe):
            return [exe]
        # Frozen supervisor without the app beside it — nothing to run.
        return []
    return [sys.executable, target]


def supervise(target: str) -> int:
    here = str(Path(target).resolve().parent)
    hb_path = os.path.join(
        tempfile.gettempdir(), f"freq-supervisor-{os.getpid()}.hb")
    grace = float(os.environ.get("FREQ_SUPERVISOR_GRACE", 8.0))
    startup_grace = max(grace * 6.0, 60.0)   # first heartbeat may lag imports
    window = float(os.environ.get("FREQ_SUPERVISOR_WINDOW", 120.0))
    max_restarts = int(os.environ.get("FREQ_SUPERVISOR_MAXR", 3))

    crashes: deque[float] = deque()
    frozen = bool(getattr(sys, "frozen", False))
    child_cmd = _child_command(target)
    if not child_cmd:
        _log(here, "packaged child (FreQ.exe) not found next to the "
                   "supervisor — nothing to supervise")
        return 4
    write_status(here, state="watching", restarts=0,
                 child=os.path.basename(child_cmd[0]),
                 mode="exe" if frozen else "dev")

    def spawn() -> subprocess.Popen:
        kwargs = {}
        if sys.platform == "win32":
            # Own process group: taskkill /T can take the whole tree down.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(
            child_cmd, cwd=here, env=_child_env(hb_path), **kwargs)

    def kill_tree(proc: subprocess.Popen) -> None:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=15)
            else:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    _log(here, f"supervising {child_cmd[0]} , "
               f"grace={grace}s, crash limit={max_restarts}/{window:.0f}s")
    proc = spawn()
    _log(here, f"child started pid={proc.pid}")
    last_content: str = ""
    last_change = time.time()
    stopping = False            # heartbeat said "clean exit in progress"

    while True:
        time.sleep(1.0)
        content = _read_heartbeat(hb_path)
        if content != last_content:
            last_content = content
            last_change = time.time()
        if content == CLEAN_STOP_MARKER:
            stopping = True

        rc = proc.poll()
        if rc is not None:
            if stopping or rc == 0:
                _log(here, f"child exited cleanly (rc={rc}) — bye")
                write_status(here, state="stopped", restarts=len(crashes))
                try:
                    os.remove(hb_path)
                except Exception:
                    pass
                return 0
            crashes.append(time.time())
            while crashes and crashes[0] < time.time() - window:
                crashes.popleft()
            _log(here, f"child crashed rc={rc} "
                       f"({len(crashes)}/{max_restarts} in window)")
            notify_tray("FreQ recovered",
                        f"The app stopped responding and was restarted "
                        f"({len(crashes)}/{max_restarts}).")
            if len(crashes) >= max_restarts:
                _log(here, "crash loop — giving up; run gui.py directly "
                           "to see the traceback")
                notify_tray("FreQ stopped",
                            "Repeated crashes — auto-restart gave up. "
                            "Start FreQ again to retry.")
                write_status(here, state="gave_up", restarts=len(crashes))
                try:
                    os.remove(hb_path)
                except Exception:
                    pass
                return 3
            proc = spawn()
            _log(here, f"restarted child pid={proc.pid}")
            write_status(here, state="restarted", restarts=len(crashes))
            last_content, last_change, stopping = "", time.time(), False
            continue

        if stopping:
            continue

        # Child alive — judge the heartbeat.
        never_beat = last_content == ""
        allowed = startup_grace if never_beat else grace
        if time.time() - last_change > allowed:
            _log(here, f"heartbeat stalled {time.time() - last_change:.0f}s "
                       f"(pid={proc.pid}) — killing + restarting")
            kill_tree(proc)
            crashes.append(time.time())
            while crashes and crashes[0] < time.time() - window:
                crashes.popleft()
            notify_tray("FreQ recovered",
                        "The app froze and was automatically restarted.")
            write_status(here, state="restarted (freeze)", restarts=len(crashes))
            if len(crashes) >= max_restarts:
                _log(here, "too many stalls in a row — giving up")
                notify_tray("FreQ stopped",
                            "Repeated freezes — auto-restart gave up. "
                            "Start FreQ again to retry.")
                write_status(here, state="gave_up", restarts=len(crashes))
                try:
                    os.remove(hb_path)
                except Exception:
                    pass
                return 3
            proc = spawn()
            _log(here, f"restarted child pid={proc.pid}")
            last_content, last_change, stopping = "", time.time(), False


def main() -> int:
    # Piped output on Windows uses the locale codec (cp874 here) which
    # cannot encode em-dashes — reconfigure so logs never crash the
    # supervisor itself.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    target = "gui.py"
    if "--supervise" in args:
        i = args.index("--supervise")
        if i + 1 >= len(args):
            print("usage: freq_supervisor.py [--supervise SCRIPT.py]")
            return 4
        target = args[i + 1]
    # Frozen exe: target file need not exist (the child is FreQ.exe);
    # in dev mode require it.
    if not getattr(sys, "frozen", False) and not Path(target).is_file():
        print(f"target not found: {target}")
        return 4
    return supervise(target)


if __name__ == "__main__":
    sys.exit(main())
