#!/usr/bin/env python3
"""Smoke: persist Master Chain "Immediate" + Autosave in .freq [persist-1017].

Two operator preferences were invisible to the save file:

  * the Master Broadcast Chain's **Immediate mode** switch — it lived only
    in memory, so every restart silently dropped the operator back to
    "apply when you press Apply";
  * **Autosave** on/off and its interval — a restart always came back with
    autosave ON every 60 s, even after the operator turned it off.

This suite drives the REAL app the way the operator does (the real dialog
switch, the real Settings handlers), saves through the REAL Save path
(``_collect_settings`` → ``RadioQueue.save_to_file``) and then proves the
values come back in a SEPARATE process — the actual restart, not a second
``_restore_settings`` call in the same interpreter.

Phases (this file is its own child process):
  * default        — parent: drive the UI, save, spawn the two children
  * --read PATH    — fresh app, load PATH, restore, report + open the dialog
  * --defaults     — fresh app + a pre-change .freq (no new keys) and a
                     hand-edited one (garbage/hostile interval values)
"""

import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue resolves its save path relative to the cwd — keep the user's
# real radio_queue.freq out of this run.
os.chdir(tempfile.mkdtemp(prefix="freq_persist_smoke_"))

RESULTS = []
SELF = str(Path(__file__).resolve())


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.2):
    """Spin the real mainloop briefly (CTk repaints dialogs on a timer)."""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def find_widget(root, text, predicate):
    for child in root.winfo_children():
        try:
            if str(child.cget("text")) == text and predicate(child):
                return child
        except Exception:
            pass
        hit = find_widget(child, text, predicate)
        if hit is not None:
            return hit
    return None


def find_button(root, text):
    return find_widget(root, text, lambda w: True)


def find_switch(root, text):
    return find_widget(root, text, lambda w: hasattr(w, "toggle"))


def child(phase, *args):
    """Run one phase in a fresh interpreter and return (rc, stdout, stderr)."""
    # encoding="utf-8", errors="replace" is load-bearing on a Thai Windows
    # install: the app prints emoji ("💾 Saved → …") and the child's stdout is
    # UTF-8, while the locale default here is cp874. Without it the decode
    # raises INSIDE the reader thread, ``proc.stdout`` comes back as None and
    # the phase looks like a crash several lines later.
    proc = subprocess.run([sys.executable, SELF, phase, *args],
                          capture_output=True, text=True, timeout=240,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def report(payload):
    """Emit the phase's observed state for the parent to assert on."""
    print("RESULT " + json.dumps(payload, sort_keys=True))


# ────────────────────────────────────────────────────────────────
# Child phases
# ────────────────────────────────────────────────────────────────
def phase_read(path):
    """A real restart: a new process loads the .freq and restores it."""
    import gui

    app = gui.RadioApp()
    try:
        app.update_idletasks()
        app.rq.load_from_file(path)
        app._restore_settings(app.rq.last_settings)
        # The dialog is where the operator sees the switch, so read the
        # switch itself — not just the private attribute.
        before = set(app.winfo_children())
        app._open_master_chain()
        pump(app, 0.4)
        dialogs = [w for w in app.winfo_children()
                   if w not in before and isinstance(w, tk.Toplevel)]
        dialog = dialogs[-1] if dialogs else None
        switch = None
        if dialog is not None:
            switch = find_switch(dialog, "Immediate mode (apply while you move)")
        report({
            "master_chain_immediate": bool(app._master_chain_immediate),
            "autosave_enabled": bool(app._autosave_enabled),
            "autosave_interval": int(app._autosave_interval),
            "autosave_var": bool(app.autosave_var.get()),
            "autosave_entry": str(app.autosave_interval_entry.get()),
            "dialog": dialog is not None,
            "dialog_switch": None if switch is None else bool(switch.get()),
        })
        return 0
    finally:
        try:
            app.destroy()
        except Exception:
            pass


def phase_defaults():
    """Old .freq (no new keys) and a hand-edited one must not break startup."""
    import gui

    app = gui.RadioApp()
    try:
        app.update_idletasks()
        # 1. A file written before this change: neither key exists.
        legacy = {"volume": 55.0,
                  "master_chain": {"mode": 1, "bass": 3.0, "mid": 0.0,
                                   "treble": 0.0, "comp_thresh_db": -24.0,
                                   "comp_ratio_x10": 30, "limit_ceiling_db": 0.0}}
        app._restore_settings(legacy)
        legacy_state = {
            "master_chain_immediate": bool(app._master_chain_immediate),
            "autosave_enabled": bool(app._autosave_enabled),
            "autosave_interval": int(app._autosave_interval),
            "volume": float(app.volume_var.get()),
        }
        # 2. Hostile values: a 2-second save loop, a huge interval (which
        #    would silently disable autosave) and an unparseable string.
        coerced = {}
        for raw in (2, "abc", 10 ** 9, None, 300, 5):
            coerced[repr(raw)] = gui.RadioApp._coerce_autosave_interval(raw)
        app._restore_settings({"autosave": {"enabled": False, "interval": 2}})
        hostile_state = {
            "interval": int(app._autosave_interval),
            "entry": str(app.autosave_interval_entry.get()),
            "enabled": bool(app._autosave_enabled),
            "var": bool(app.autosave_var.get()),
        }
        report({"legacy": legacy_state, "coerced": coerced,
                "hostile": hostile_state})
        return 0
    finally:
        try:
            app.destroy()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────
# Parent
# ────────────────────────────────────────────────────────────────
def main():
    from gui import RadioApp

    freq = os.path.join(os.getcwd(), "persist_check.freq")

    app = RadioApp()
    try:
        app.update_idletasks()
        check("a fresh session defaults to Immediate OFF (dialogs are opt-in)",
              app._master_chain_immediate is False)
        check("a fresh session defaults to autosave ON every 60 s",
              app._autosave_enabled is True and app._autosave_interval == 60,
              f"{app._autosave_enabled}/{app._autosave_interval}")

        # ── drive the REAL UI, not the attributes ──────────────────
        before = set(app.winfo_children())
        app._open_master_chain()
        pump(app, 0.4)
        dialogs = [w for w in app.winfo_children()
                   if w not in before and isinstance(w, tk.Toplevel)]
        dialog = dialogs[-1] if dialogs else None
        check("Master Chain dialog opens", dialog is not None)
        if dialog is None:
            return 1
        switch = find_switch(dialog, "Immediate mode (apply while you move)")
        check("the Immediate mode switch is in the dialog", switch is not None)
        if switch is None:
            return 1
        check("the switch starts OFF on a fresh session", not switch.get())
        switch.toggle()
        app.update()
        close = find_button(dialog, "Close")
        check("the Close button applies and closes (the operator's path)",
              close is not None)
        if close is not None:
            close.invoke()
        pump(app, 0.4)
        check("closing with Immediate ON stores the flag on the app",
              app._master_chain_immediate is True,
              str(app._master_chain_immediate))

        # Autosave OFF, every 123 s — through the Settings widgets.
        app.autosave_var.set(False)
        app._toggle_autosave()
        app.autosave_interval_entry.delete(0, "end")
        app.autosave_interval_entry.insert(0, "123")
        app._on_autosave_interval_change()
        check("the Settings widgets drove the live state",
              app._autosave_enabled is False and app._autosave_interval == 123,
              f"{app._autosave_enabled}/{app._autosave_interval}")

        # ── save through the real Save path ────────────────────────
        settings = app._collect_settings()
        check("_collect_settings carries master_chain_immediate",
              settings.get("master_chain_immediate") is True,
              repr(settings.get("master_chain_immediate")))
        check("_collect_settings carries autosave {enabled, interval}",
              settings.get("autosave") == {"enabled": False, "interval": 123},
              repr(settings.get("autosave")))
        app.rq.save_to_file(freq, settings=settings)
    finally:
        try:
            app.destroy()
        except Exception:
            pass

    # ── the artifact on disk ───────────────────────────────────────
    raw = json.loads(Path(freq).read_text(encoding="utf-8"))
    saved = raw.get("settings", {})
    check("the .freq file has the Immediate flag",
          isinstance(saved.get("master_chain_immediate"), bool)
          and saved["master_chain_immediate"] is True,
          repr(saved.get("master_chain_immediate")))
    check("the .freq file has autosave enabled=False",
          saved.get("autosave", {}).get("enabled") is False,
          repr(saved.get("autosave")))
    check("the .freq file has autosave interval=123 as an int",
          isinstance(saved.get("autosave", {}).get("interval"), int)
          and saved["autosave"]["interval"] == 123,
          repr(saved.get("autosave")))

    # ── the restart ────────────────────────────────────────────────
    rc, out, err = child("--read", freq)
    check("the restarting process exits cleanly", rc == 0,
          (err.strip().splitlines() or [""])[-1])
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    check("the restarting process reported its state", line is not None,
          (out.strip().splitlines() or [""])[-1])
    if line:
        got = json.loads(line[len("RESULT "):])
        check("Immediate mode survives the restart",
              got["master_chain_immediate"] is True, str(got))
        check("autosave stays OFF across the restart",
              got["autosave_enabled"] is False, str(got))
        check("the autosave interval survives the restart",
              got["autosave_interval"] == 123, str(got))
        check("the Settings switch shows OFF after the restart",
              got["autosave_var"] is False, str(got))
        check("the Settings field shows 123 after the restart",
              got["autosave_entry"] == "123", str(got))
        check("the Master Chain dialog re-opens after the restart",
              got["dialog"] is True, str(got))
        check("the dialog's Immediate switch is ON after the restart",
              got["dialog_switch"] is True, str(got))

    # ── backward compatibility + hostile input ─────────────────────
    rc, out, err = child("--defaults")
    check("the legacy/garbage phase exits cleanly", rc == 0,
          (err.strip().splitlines() or [""])[-1])
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    check("the legacy/garbage phase reported its state", line is not None,
          (out.strip().splitlines() or [""])[-1])
    if line:
        got = json.loads(line[len("RESULT "):])
        legacy = got["legacy"]
        check("a pre-change .freq still restores (no new keys = no crash)",
              legacy["volume"] == 55.0, str(legacy))
        check("a pre-change .freq keeps Immediate OFF",
              legacy["master_chain_immediate"] is False, str(legacy))
        check("a pre-change .freq keeps the autosave defaults",
              legacy["autosave_enabled"] is True
              and legacy["autosave_interval"] == 60, str(legacy))
        co = got["coerced"]
        check("a 2 s interval is floored to 5 s", co["2"] == 5, str(co))
        check("unparseable text falls back to 60 s", co["'abc'"] == 60, str(co))
        check("a huge interval is capped at 24 h",
              co[str(10 ** 9)] == 86400, str(co))
        check("None falls back to 60 s", co["None"] == 60, str(co))
        check("a sane value passes through untouched",
              co["300"] == 300 and co["5"] == 5, str(co))
        hostile = got["hostile"]
        check("restoring a hostile interval lands on 5 s, not 2 s",
              hostile["interval"] == 5 and hostile["entry"] == "5",
              str(hostile))
        check("a restored autosave=enabled False turns the switch off",
              hostile["enabled"] is False and hostile["var"] is False,
              str(hostile))

    print(f"\n=== {sum(1 for _, ok, _ in RESULTS if ok)} passed, "
          f"{sum(1 for _, ok, _ in RESULTS if not ok)} failed ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print("FAILED:", name, "—", detail)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--read":
        sys.exit(phase_read(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "--defaults":
        sys.exit(phase_defaults())
    sys.exit(main())
