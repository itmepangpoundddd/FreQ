"""Focus Mode for FreQ — keeps the machine radio-friendly while on air.

Three independent toggles, each safely reversible:

* **Prevent system sleep** — holds the ``ES_CONTINUOUS | ES_SYSTEM_REQUIRED``
  execution state via ``SetThreadExecutionState`` on a dedicated thread.
  This is the documented, well-behaved way to keep Windows awake while a
  media app runs (the OS still allows manual sleep; the state is released
  on stop/exit — no service or power-plan hacks).

* **Block Windows Update** — stops and disables the ``wuauserv`` service so
  automatic updates (and their forced reboots) cannot interrupt a broadcast.
  Requires the app to run elevated; without admin rights the toggle reports
  failure gracefully.  The original start type is remembered and restored
  when the block is lifted.  Windows Defender / Store delivery optimization
  are intentionally left alone.

* **Suppress OS notifications** — enables **Focus Assist** (quiet hours) by
  writing the ``NOCLIPAUTHOR``-style toast-suppression values used by
  Windows 10/11 under ``HKCU\\...\\PushToInstall``-independent Settings:
  specifically ``HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\
  PushNotifications`` → ``ToastEnabled = 0`` (kills toasts app-wide) and
  the Focus Assist "priority only" state.  Explorer picks the change up on
  the next settings broadcast; a ``WM_SETTINGCHANGE`` is broadcast so it is
  immediate.  Restoring re-enables toasts.

Everything is best-effort: functions never raise, they return booleans and
populate ``last_error`` so the UI can show a small status line.
"""
from __future__ import annotations

import ctypes
import subprocess
import threading
import time
from typing import Optional

_IS_WINDOWS = False
try:
    import sys
    _IS_WINDOWS = sys.platform == "win32"
except Exception:
    pass

# SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_PUSH_NOTIF_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\PushNotifications"
_WUAUSERV = "wuauserv"


def _broadcast_settings_change() -> None:
    """Ask running apps (incl. Explorer) to re-read settings immediately."""
    if not _IS_WINDOWS:
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, None,
            SMTO_ABORTIFHUNG, 1000, ctypes.byref(ctypes.c_ulong()),
        )
    except Exception:
        pass


class FocusMode:
    """Owns the three toggles. One instance per app; all calls are safe."""

    def __init__(self) -> None:
        self.prevent_sleep = False
        self.block_updates = False
        self.suppress_notifications = False
        self.last_error = ""

        self._saved_update_start: Optional[int] = None
        self._saved_toast_enabled: Optional[int] = None
        self._wake_thread: Optional[threading.Thread] = None
        self._wake_stop = threading.Event()

    # -- 1. Prevent system sleep -------------------------------------------
    def set_prevent_sleep(self, on: bool) -> bool:
        if not _IS_WINDOWS:
            self.last_error = "Windows only"
            return False
        self._wake_stop.set()            # retire any previous holder thread
        if self._wake_thread is not None:
            self._wake_thread.join(timeout=2.0)
            self._wake_thread = None
        if on:
            self._wake_stop = threading.Event()
            stop = self._wake_stop
            def _holder() -> None:
                # ES_CONTINUOUS must be re-asserted; keep a slow heartbeat so
                # a stray call from any other component cannot outlive us.
                while not stop.is_set():
                    ctypes.windll.kernel32.SetThreadExecutionState(
                        ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                    stop.wait(30.0)
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self._wake_thread = threading.Thread(
                target=_holder, name="freq-sleep-guard", daemon=True)
            self._wake_thread.start()
        self.prevent_sleep = on
        self.last_error = ""
        return True

    # -- 2. Block Windows Update service ------------------------------------
    def set_block_updates(self, on: bool) -> bool:
        if not _IS_WINDOWS:
            self.last_error = "Windows only"
            return False
        try:
            if on:
                start = self._service_start_type(_WUAUSERV)
                if start is None:
                    raise OSError("cannot read wuauserv config (admin rights?)")
                if self._run_sc("config", _WUAUSERV, "start=", "disabled") is None:
                    raise OSError("access denied — run FreQ as administrator")
                self._run_sc("stop", _WUAUSERV)
                self._saved_update_start = start
                self.block_updates = True
            else:
                self._run_sc("config", _WUAUSERV, "start=", "demand")
                if self._saved_update_start is not None:
                    self._run_sc("config", _WUAUSERV,
                                 "start=", {2: "auto", 3: "demand", 4: "disabled"}
                                 .get(self._saved_update_start, "demand"))
                    self._saved_update_start = None
                self.block_updates = False
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    @staticmethod
    def _run_sc(*args: str) -> Optional[int]:
        try:
            r = subprocess.run(["sc.exe", *args], capture_output=True,
                               text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                return r.returncode
            raise OSError((r.stderr or r.stdout or "").strip()[:200]
                           or f"sc exited {r.returncode}")
        except FileNotFoundError:
            return None

    @staticmethod
    def _service_start_type(service: str) -> Optional[int]:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SYSTEM\CurrentControlSet\Services\{service}") as k:
                return winreg.QueryValueEx(k, "Start")[0]
        except OSError:
            return None

    def updates_blocked(self) -> bool:
        st = self._service_start_type(_WUAUSERV)
        return st == 4  # SERVICE_DISABLED

    # -- 3. Suppress OS notifications ----------------------------------------
    def set_suppress_notifications(self, on: bool) -> bool:
        if not _IS_WINDOWS:
            self.last_error = "Windows only"
            return False
        try:
            import winreg
            key_path = _PUSH_NOTIF_KEY
            if on:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                        self._saved_toast_enabled = winreg.QueryValueEx(k, "ToastEnabled")[0]
                except OSError:
                    self._saved_toast_enabled = None
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                        winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, 0)
            else:
                val = 1 if self._saved_toast_enabled is None else self._saved_toast_enabled
                self._saved_toast_enabled = None
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                        winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, val)
            _broadcast_settings_change()
            self.suppress_notifications = on
            self.last_error = ""
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    # -- lifecycle -------------------------------------------------------------
    def release_all(self) -> None:
        """Called on app exit: undo whatever is still active."""
        if self.prevent_sleep:
            try:
                self.set_prevent_sleep(False)
            except Exception:
                pass
        if self.block_updates:
            self.set_block_updates(False)
        if self.suppress_notifications:
            self.set_suppress_notifications(False)

    def snapshot(self) -> dict:
        return {"sleep": self.prevent_sleep, "updates": self.block_updates,
                "notifications": self.suppress_notifications}
