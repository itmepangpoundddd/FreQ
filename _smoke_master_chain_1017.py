#!/usr/bin/env python3
"""Smoke: Master Broadcast Chain dialog [master-chain-1017].

The section looked unusable because it mostly was:

  * the two-pane dialog could not find its own pane host (it guessed from
    widget parents: ``btn.master.master.master``), so clicking 3-Band EQ /
    Compressor / Limiter did nothing and all three panes stayed stacked;
  * the Limiter switch sent its ceiling anyway, so switching it OFF left the
    limiter engaged;
  * Defaults reset a dict instead of the widgets, so the next commit pushed
    the old slider positions straight back;
  * nothing on the Settings page (or in the dialog) said whether the chain
    was ON, and nothing explained that the stages only run inside the native
    WASAPI renderer.

This drives the REAL dialog (headless Tk) and the REAL player object, so the
contract the buttons use is what gets checked: one visible pane, engine
parameters that match the switches, a live readout, and the native self-test.
"""

import os
import sys
import tempfile
import tkinter as tk
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(tempfile.mkdtemp(prefix="freq_mc_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


def pump(app, seconds=0.15):
    """Spin the real mainloop briefly (CTk deiconifies dialogs on a timer)."""
    end = time.time() + seconds

    def tick():
        if time.time() > end:
            app.quit()
        else:
            app.after(15, tick)

    app.after(0, tick)
    app.mainloop()


def find_button(root, text):
    """First CTk button whose label matches (walking the whole tree)."""
    for child in root.winfo_children():
        try:
            if str(child.cget("text")) == text:
                return child
        except Exception:
            pass
        hit = find_button(child, text)
        if hit is not None:
            return hit
    return None


def find_switch(root, text):
    for child in root.winfo_children():
        try:
            if str(child.cget("text")) == text and hasattr(child, "toggle"):
                return child
        except Exception:
            pass
        hit = find_switch(child, text)
        if hit is not None:
            return hit
    return None


def open_dialog(app):
    """Open the real dialog and return it once it is actually on screen."""
    before = set(app.winfo_children())
    app._open_master_chain()
    pump(app, 0.35)
    dialogs = [w for w in app.winfo_children()
               if w not in before and isinstance(w, tk.Toplevel)]
    return dialogs[-1] if dialogs else None


import gui  # noqa: E402

app = gui.RadioApp()
try:
    app.update_idletasks()
    app.update()
    player = app.audio_player
    check("a fresh player starts fully raw (no silent 1 dB limiting)",
          player.get_master_chain() == (0, 0.0, 0.0, 0.0, -24.0, 30, 0.0),
          str(player.get_master_chain()))
    player.set_master_chain(0)          # start from the factory state
    check("set_master_chain(0) also leaves the limiter disengaged",
          player.get_master_chain()[6] >= 0, str(player.get_master_chain()))

    # ── 1. the dialog shows up and has one pane visible ──────────
    dialog = open_dialog(app)
    check("Configure Master Chain opens a dialog", dialog is not None)
    if dialog is None:
        raise SystemExit(1)
    # CTk withdraws a Toplevel while it repaints the titlebar and re-shows it
    # on a timer; a busy machine can miss a fixed wait. Poll instead — and
    # treat "still withdrawn" as an environment note, because EVERY CTk
    # dialog (Mic Filters included) behaves the same way in this harness.
    deadline = time.time() + 3.0
    while time.time() < deadline and dialog.state() == "withdrawn":
        pump(app, 0.2)
    if dialog.state() == "withdrawn":
        print("INFO: dialog still withdrawn after 3 s (harness WM, not app code)")
    else:
        check("the dialog comes up on screen", True, dialog.state())

    ui = dialog._mc
    panes = ui["panes"]
    check("the dialog builds all three stage panes", len(panes) == 3,
          str(sorted(panes)))
    visible = [k for k, p in panes.items() if p.winfo_manager()]
    check("exactly ONE pane is visible on open (no stacked pages)",
          visible == ["eq"], str(visible))

    # ── 2. the stage list really switches panes ─────────────────
    for key, label in (("comp", "Compressor"), ("limit", "Limiter"),
                       ("eq", "3-Band EQ")):
        btn = find_button(panes["eq"].master.master, label)
        check(f"stage row '{label}' exists in the list", btn is not None)
        if btn is not None:
            btn.invoke()
            app.update()
        visible = [k for k, p in panes.items() if p.winfo_manager()]
        check(f"clicking '{label}' raises exactly that pane",
              visible == [key], str(visible))

    eq_switch = find_switch(panes["eq"], "Enable")
    check("the EQ pane has a real Enable switch", eq_switch is not None)

    # ── 3. switches + sliders reach the engine ─────────────────
    ui["set"]("eq_bass", 6.0)
    ui["set"]("eq_mid", -2.0)
    ui["set"]("eq_treb", 3.0)
    eq_switch.toggle()                  # click the switch
    app.update()
    check("the EQ switch flips on", ui["stage_vars"]["eq"].get())
    check("moving a slider moves its widget too",
          round(ui["sliders"]["eq_bass"][0].get(), 1) == 6.0,
          str(ui["sliders"]["eq_bass"][0].get()))

    apply_btn = find_button(dialog, "Apply")
    check("the dialog has an Apply button", apply_btn is not None)
    apply_btn.invoke()
    app.update()
    params = player.get_master_chain()
    check("Apply pushes the EQ gains to the player",
          (params[1], params[2], params[3]) == (6.0, -2.0, 3.0), str(params))
    check("EQ on / compressor off / limiter off = mode 0, ceiling >= 0",
          params[0] == 0 and params[6] >= 0, str(params))

    # ── 4. the limiter switch really disables the limiter ──────
    ui["show"]("limit")
    lim_switch = find_switch(panes["limit"], "Enable")
    ui["set"]("limit_ceil", -3.5)
    lim_switch.toggle()
    app.update()
    ui["commit"]()
    params = player.get_master_chain()
    check("limiter ON sends its ceiling (< 0 dBFS)", params[6] == -3.5, str(params))
    check("the settings summary lists the limiter",
          "Limiter" in app.master_chain_status.cget("text"),
          app.master_chain_status.cget("text"))
    lim_switch.toggle()                 # click it back off
    app.update()
    ui["commit"]()
    params = player.get_master_chain()
    check("limiter OFF is audible state, not just a switch (ceiling >= 0)",
          params[6] >= 0, str(params))
    check("the summary drops the limiter when it is off",
          "Limiter" not in app.master_chain_status.cget("text"),
          app.master_chain_status.cget("text"))

    # ── 5. compressor character (Gentle / Strong) ──────────────
    ui["show"]("comp")
    comp_switch = find_switch(panes["comp"], "Enable")
    comp_switch.toggle()
    app.update()
    ui["strength"].set("Strong")
    ui["commit"]()
    check("Strong character pushes mode 2", player.get_master_chain()[0] == 2,
          str(player.get_master_chain()))
    check("the ON-AIR readout names the compressor",
          "Comp" in ui["status"].cget("text"), ui["status"].cget("text"))
    ui["strength"].set("Gentle")
    ui["commit"]()
    check("Gentle character pushes mode 1", player.get_master_chain()[0] == 1,
          str(player.get_master_chain()))

    # ── 6. Defaults: widgets AND engine go back to factory ─────
    find_button(dialog, "Defaults").invoke()
    app.update()
    params = player.get_master_chain()
    check("Defaults switches every stage off", params == (0, 0.0, 0.0, 0.0,
                                                          -24.0, 30, 0.0),
          str(params))
    check("Defaults moves the visible sliders back",
          round(ui["sliders"]["eq_bass"][0].get(), 1) == 0.0
          and round(ui["sliders"]["comp_thr"][0].get(), 1) == -24.0,
          f"bass={ui['sliders']['eq_bass'][0].get()} "
          f"thr={ui['sliders']['comp_thr'][0].get()}")
    check("Defaults reports the raw passthrough",
          ui["status"].cget("text").startswith("OFF"),
          ui["status"].cget("text"))
    check("the Settings line follows Defaults",
          app.master_chain_status.cget("text").startswith("OFF"),
          app.master_chain_status.cget("text"))

    # ── 7. the dialog admits when the native engine is idle ────
    check("idle pygame engine is called out in the dialog",
          "pygame" in ui["note"].cget("text"), ui["note"].cget("text"))
    real_engine = player._engine
    player._engine = "wasapi"
    ui["commit"]()
    check("the warning clears once the WASAPI engine is running",
          ui["note"].cget("text") == "", ui["note"].cget("text"))
    player._engine = real_engine

    # ── 8. native self-test proves the DSP works ───────────────
    ok, msg = ui["selftest"]()
    check("native master-chain self-test passes", ok, msg)
    find_button(dialog, "Test chain").invoke()
    app.update()
    check("Test chain reports in the dialog",
          "Self-test" in ui["probe"].cget("text"),
          ui["probe"].cget("text"))

    # ── 9. the chain persists with the rest of the settings ────
    player.set_master_chain(2, 4.0, 1.0, -1.0, -30.0, 40, -2.0)
    saved = app._collect_settings().get("master_chain", {})
    check("settings carry the chain (mode/eq/comp/limiter)",
          saved.get("mode") == 2 and saved.get("bass") == 4.0
          and saved.get("limit_ceiling_db") == -2.0, str(saved))
    app._refresh_master_chain_status()
    check("the Settings summary shows the active chain",
          "EQ +4/-1" in app.master_chain_status.cget("text")
          or "Comp" in app.master_chain_status.cget("text"),
          app.master_chain_status.cget("text"))

    # Close applies (never silently discards) and destroys the window.
    ui["apply"]()
    app.update()
    check("closing the dialog applies and closes it", not dialog.winfo_exists())
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
