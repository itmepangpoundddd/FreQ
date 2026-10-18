#!/usr/bin/env python3
"""Smoke: Settings → ".freq Round-Trip" panel [roundtrip-1017].

The operator could save settings and watch the values come back, but had no
way to SEE the contract: which file would be written, what this session
would put in it right now, and what the last file actually contained before
restoring. A panel that lies is worse than no panel, so every claim below is
checked against the real file on disk and against a real restart:

  * the box exists on the Settings tab, is read-only, and typing into it
    cannot rewrite the report;
  * with no file it says so and refuses to invent values;
  * the REAL Master Chain switch + autosave widgets show up in the
    "WILL WRITE" block without a manual refresh;
  * saving through the REAL Save All Settings path flips the report to
    "LAST WRITTEN BY THIS SESSION" / `unchanged`, and the numbers in the
    report match the JSON on disk (including the group count);
  * an edit after the save reads `CHANGED`, and a reload reads
    `RESTORED FROM THE LAST FILE` / `ok` again;
  * the Refresh button really re-renders a stale box;
  * a SEPARATE process (the actual restart) reports the same values;
  * a legacy settings block without those keys is labelled
    `(file: not stored -> default)` instead of pretending they matched;
  * a hand-edited file with junk in those keys cannot break the panel.

The app autosaves on its first poll, so the "no file yet" and "legacy file"
regimes are established deliberately (delete the file / disable the restored
autosave) rather than by racing the clock.

Phases (this file is its own child process):
  * default        — parent: build the UI, drive it, save, spawn children
  * --restart DIR  — fresh app in DIR, dump the panel text + live state
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
# RadioQueue resolves its path relative to the cwd — never touch the real
# radio_queue.freq of the checkout.
os.chdir(tempfile.mkdtemp(prefix="freq_roundtrip_smoke_"))

RESULTS = []
SELF = str(Path(__file__).resolve())


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.2):
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
    """Run this file as a child and decode its output as UTF-8.

    The app prints Thai text and emoji. With `text=True` and no encoding,
    Python decodes with the locale codec — on a Thai Windows (cp874) the
    reader thread raised UnicodeDecodeError and `proc.stdout` came back as
    None, so the restart assertions could never see the child's RESULT line.
    """
    proc = subprocess.run([sys.executable, SELF, phase, *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def report(payload):
    print("RESULT " + json.dumps(payload, sort_keys=True))


def box_text(app):
    return app.freq_roundtrip_box.get("1.0", "end")


def flat(text):
    """Whitespace-insensitive view, so assertions survive padding edits."""
    return " | ".join(" ".join(line.split()) for line in text.splitlines())


def write_freq(directory, settings):
    payload = {
        "format": "freq", "version": 2, "queue": [], "library": [],
        "history": [], "presets": {}, "current_index": -1,
        "settings": settings,
    }
    path = Path(directory) / "radio_queue.freq"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ────────────────────────────────────────────────────────────────
# Child phase: the actual restart
# ────────────────────────────────────────────────────────────────
def phase_restart(directory):
    os.chdir(directory)
    import gui

    app = gui.RadioApp()
    try:
        app.update_idletasks()
        app.update()
        report({
            "box": box_text(app),
            "report": app._freq_roundtrip_text(),
            "box_equals_report":
                box_text(app).strip() == app._freq_roundtrip_text().strip(),
            "state": str(app.freq_roundtrip_box._textbox.cget("state")),
            "master_chain_immediate": bool(app._master_chain_immediate),
            "autosave_enabled": bool(app._autosave_enabled),
            "autosave_interval": int(app._autosave_interval),
            "path": str(app.rq._save_path),
        })
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

    freq = Path(os.getcwd()) / "radio_queue.freq"

    app = RadioApp()
    try:
        app.update_idletasks()
        app.update()

        # ── 1. the panel exists and is read-only ───────────────────
        panel = getattr(app, "freq_roundtrip_box", None)
        check("the Settings tab has a .freq round-trip panel", panel is not None)
        if panel is None:
            return 1
        check("the panel is read-only (state=disabled)",
              str(panel._textbox.cget("state")) == "disabled",
              str(panel._textbox.cget("state")))
        guarded = box_text(app)
        panel.insert("1.0", "SHOULD NOT APPEAR")     # as if the operator typed
        app.update()
        check("typing into the panel cannot rewrite the report",
              box_text(app) == guarded, "the textbox accepted input")
        raw = panel._textbox.count("1.0", "end", "displaylines")
        display = int(raw[0] if isinstance(raw, (tuple, list)) else raw)
        logical = len([l for l in guarded.splitlines() if l.strip()])
        check("the report wraps instead of clipping in the narrow tab",
              str(panel._textbox.cget("wrap")) == "word" and display > logical,
              f"wrap={panel._textbox.cget('wrap')} display={display} logical={logical}")

        # ── 2. with no file at all, the panel must not invent values ─
        if freq.exists():
            freq.unlink()
        app._freq_restored_settings = {}
        app._freq_restored_source = "loaded"
        app.autosave_var.set(False)          # real UI path: no re-write race
        app._toggle_autosave()
        app._refresh_freq_roundtrip()
        text = box_text(app)
        check("the panel names the file it would write",
              "radio_queue.freq" in text, flat(text))
        check("with no file it says it is not written yet",
              "(not written yet)" in text, flat(text))
        check("with no settings it says so instead of guessing",
              "no settings block" in text, flat(text))
        check("and it says a restart would start from factory defaults",
              "VERDICT: the last file had no settings" in text, flat(text))
        check("a fresh session reports Immediate OFF",
              "master chain immediate = OFF" in text, flat(text))
        check("the autosave switch turned off through the real handler",
              "autosave = OFF / 60 s" in text and app._autosave_enabled is False,
              flat(text))

        # ── 3. drive the REAL Master Chain dialog switch ───────────
        before = set(app.winfo_children())
        app._open_master_chain()
        pump(app, 0.4)
        dialogs = [w for w in app.winfo_children()
                   if w not in before and isinstance(w, tk.Toplevel)]
        dialog = dialogs[-1] if dialogs else None
        check("the Master Chain dialog opens", dialog is not None)
        switch = (find_switch(dialog, "Immediate mode (apply while you move)")
                  if dialog is not None else None)
        check("the Immediate mode switch is there", switch is not None)
        if switch is not None:
            switch.toggle()
        app.update()
        close = find_button(dialog, "Close") if dialog is not None else None
        if close is not None:
            close.invoke()
        pump(app, 0.4)
        check("the operator's path turned Immediate mode ON",
              app._master_chain_immediate is True,
              str(app._master_chain_immediate))

        app.autosave_interval_entry.delete(0, "end")
        app.autosave_interval_entry.insert(0, "123")
        app._on_autosave_interval_change()
        check("the autosave widgets drove the live state",
              app._autosave_enabled is False and app._autosave_interval == 123,
              f"{app._autosave_enabled}/{app._autosave_interval}")

        text = box_text(app)
        check("the panel picked up Immediate ON without a manual refresh",
              "master chain immediate = ON" in text, flat(text))
        check("the panel picked up autosave OFF / 123 s",
              "autosave = OFF / 123 s" in text, flat(text))
        check("the file still has no settings, so the verdict is unchanged",
              "VERDICT: the last file had no settings" in text, flat(text))

        # ── 4. saving through the real path ───────────────────────
        app._save_settings_to_freq()
        app.update()
        check("Save All Settings wrote radio_queue.freq", freq.exists())

        saved = json.loads(freq.read_text(encoding="utf-8"))["settings"]
        check("the file carries master_chain_immediate=True",
              saved.get("master_chain_immediate") is True,
              repr(saved.get("master_chain_immediate")))
        check("the file carries autosave {False, 123}",
              saved.get("autosave") == {"enabled": False, "interval": 123},
              repr(saved.get("autosave")))

        text = box_text(app)
        check("after saving the panel reports the file exists",
              "(exists," in text and "radio_queue.freq" in text, flat(text))
        check("after saving the source is this session's write",
              "LAST WRITTEN BY THIS SESSION" in text, flat(text))
        check("after saving the verdict is unchanged",
              "VERDICT: unchanged since load" in text, flat(text))
        check("the panel's own value rows read ok after the save",
              "(file: ON)  ok" in text and "(file: OFF / 123 s)  ok" in text,
              flat(text))
        check("the group count in the panel equals the groups on disk",
              f"settings groups = {len(saved)}" in text,
              f"panel text missing the disk count {len(saved)}")

        # ── 5. a live edit makes the panel say so ─────────────────
        app._master_chain_immediate = False      # no hook fires on purpose
        app._refresh_freq_roundtrip()
        text = box_text(app)
        check("an edited value shows CHANGED, not a silent match",
              "master chain immediate = OFF  (file: ON)  CHANGED - save to keep" in text,
              flat(text))
        check("the verdict warns that a restart would differ",
              "VERDICT: changed since load" in text, flat(text))
        check("the WILL WRITE block shows the new value",
              "  master chain immediate = OFF" in text, flat(text))

        # ── 6. reload restores the file's values ──────────────────
        app.rq.load_from_file(str(freq))
        app._restore_settings(app.rq.last_settings)
        app.update()
        check("the reload put Immediate mode back ON",
              app._master_chain_immediate is True,
              str(app._master_chain_immediate))
        check("the reload put autosave back to OFF / 123 s",
              app._autosave_enabled is False and app._autosave_interval == 123,
              f"{app._autosave_enabled}/{app._autosave_interval}")
        text = box_text(app)
        check("the panel now says RESTORED FROM THE LAST FILE",
              "RESTORED FROM THE LAST FILE" in text, flat(text))
        check("and both rows match the file again",
              "(file: ON)  ok" in text and "(file: OFF / 123 s)  ok" in text,
              flat(text))

        # ── 7. the Refresh button really re-renders ───────────────
        app._autosave_enabled = True             # no hook fires on purpose
        app.update()
        stale = box_text(app)
        check("the box is stale until something refreshes it",
              "autosave = OFF / 123 s" in stale, flat(stale))
        refresh = find_button(app.tab_settings, "🔄  Refresh preview")
        check("the Refresh preview button is on the Settings tab",
              refresh is not None)
        if refresh is not None:
            refresh.invoke()
            app.update()
        fresh = box_text(app)
        check("pressing Refresh re-renders the live values",
              "  autosave = ON / 123 s" in fresh, flat(fresh))
        check("the widget text is exactly the builder's report",
              box_text(app).strip() == app._freq_roundtrip_text().strip(),
              "box and report diverged")

        # ── 8. a legacy settings block must not be faked ──────────
        app._remember_freq_snapshot({"volume": 55.0}, source="loaded")
        app._refresh_freq_roundtrip()
        text = box_text(app)
        check("a key the file never stored says so (no crash, no guess)",
              "master chain immediate = ON  (file: not stored -> default)" in text
              and "autosave = ON / 123 s  (file: not stored -> default)" in text,
              flat(text))
        check("a legacy block still renders a verdict",
              "VERDICT: " in text, flat(text))

        # ── 9. a hand-edited block cannot break the panel ─────────
        app._remember_freq_snapshot(
            {"volume": 55.0, "autosave": "on", "master_chain_immediate": "yes"},
            source="loaded")
        app._refresh_freq_roundtrip()
        text = box_text(app)
        check("a non-dict autosave is reported as not usable",
              "autosave = ON / 123 s  (file: not stored -> default)" in text,
              flat(text))
        check("a junk truthy Immediate value is shown as the restore reads it",
              "master chain immediate = ON  (file: ON)" in text, flat(text))
        check("the panel still renders the WILL WRITE block",
              "WILL WRITE" in text and "settings groups = " in text, flat(text))

        # ── 10. put the file back and leave a real state behind ───
        app.rq.load_from_file(str(freq))
        app._restore_settings(app.rq.last_settings)
        app.update()
        app._master_chain_immediate = True
        app.autosave_var.set(False)
        app._toggle_autosave()
        app.autosave_interval_entry.delete(0, "end")
        app.autosave_interval_entry.insert(0, "123")
        app._on_autosave_interval_change()
        app._save_settings_to_freq()
        app.update()
    finally:
        try:
            app.destroy()
        except Exception:
            pass

    on_disk = json.loads(freq.read_text(encoding="utf-8"))["settings"]
    check("the file on disk is Immediate ON / autosave OFF / 123 s",
          on_disk.get("master_chain_immediate") is True
          and on_disk.get("autosave") == {"enabled": False, "interval": 123},
          repr({k: on_disk.get(k) for k in ("master_chain_immediate", "autosave")}))

    # ── 11. a real restart reads the same story ──────────────────
    rc, out, err = child("--restart", os.getcwd())
    check("the restarting process exits cleanly", rc == 0,
          (err.strip().splitlines() or [""])[-1])
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    check("the restarting process reported its panel", line is not None,
          (out.strip().splitlines() or [""])[-1])
    if line:
        got = json.loads(line[len("RESULT "):])
        check("after the restart the panel says RESTORED FROM THE LAST FILE",
              "RESTORED FROM THE LAST FILE" in got["box"], flat(got["box"]))
        check("after the restart the panel shows Immediate ON",
              "master chain immediate = ON" in got["box"], flat(got["box"]))
        check("after the restart the panel shows autosave OFF / 123 s",
              "autosave = OFF / 123 s" in got["box"], flat(got["box"]))
        check("after the restart the verdict is unchanged",
              "VERDICT: unchanged since load" in got["box"], flat(got["box"]))
        check("after the restart the file line reports an existing file",
              "(exists," in got["box"] and "modified " in got["box"],
              flat(got["box"]))
        check("the restarted app really holds those values",
              got["master_chain_immediate"] is True
              and got["autosave_enabled"] is False
              and got["autosave_interval"] == 123, str(got))
        check("the restarted panel widget matches the builder",
              got["box_equals_report"] is True, "box and report diverged")
        check("the restarted panel is still read-only",
              got["state"] == "disabled", got["state"])
        check("the restarted session loaded the file we saved",
              Path(got["path"]).resolve() == freq.resolve(), got["path"])

    # ── 12. a legacy file that keeps autosave OFF must stay intact ─
    legacy_dir = tempfile.mkdtemp(prefix="freq_roundtrip_legacy_")
    legacy_path = write_freq(legacy_dir, {"volume": 55.0,
                                          "autosave": {"enabled": False,
                                                       "interval": 300}})
    before_stat = legacy_path.stat()
    rc, out, err = child("--restart", legacy_dir)
    check("the legacy-file process exits cleanly", rc == 0,
          (err.strip().splitlines() or [""])[-1])
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    check("the legacy-file process reported its panel", line is not None,
          (out.strip().splitlines() or [""])[-1])
    if line:
        got = json.loads(line[len("RESULT "):])
        txt = got["box"]
        check("a legacy file's missing key is labelled, not invented",
              "master chain immediate = OFF  (file: not stored -> default)" in txt,
              flat(txt))
        check("the legacy file's own autosave setting is reported",
              "autosave = OFF / 300 s" in txt, flat(txt))
        check("the legacy file's volume is reported",
              "volume = 55 %" in txt, flat(txt))
        check("the legacy app really restored autosave OFF / 300 s",
              got["autosave_enabled"] is False
              and got["autosave_interval"] == 300, str(got))
        check("the legacy app did not invent an Immediate ON",
              got["master_chain_immediate"] is False, str(got))
    after_stat = legacy_path.stat()
    check("opening a legacy file did not rewrite it",
          after_stat.st_mtime == before_stat.st_mtime
          and after_stat.st_size == before_stat.st_size,
          f"{before_stat.st_size}B/{before_stat.st_mtime} -> "
          f"{after_stat.st_size}B/{after_stat.st_mtime}")

    print(f"\n=== {sum(1 for _, ok, _ in RESULTS if ok)} passed, "
          f"{sum(1 for _, ok, _ in RESULTS if not ok)} failed ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print("FAILED:", name, "—", detail)
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--restart":
        sys.exit(phase_restart(sys.argv[2]))
    sys.exit(main())
