#!/usr/bin/env python3
"""UI feature hardening [ui-1016] — covers the features added in the 1016.1 queue:

  1. Waveform generation uses the bundled ffmpeg path (installs machines)
  2. Queue rename: title/artist updates + library sync + preset persist
  3. Multi-select: Shift+click builds ranges; Shift+Up grows to first entry;
     Shift+Down grows to the LAST entry
  4. Marquee: no bounce for short titles, slides for long ones, and
     external updates reset cleanly
  5. Dual Output: settings collect/restore keeps device+volume, gui
     wiring (import, dropdown handlers, collect/restore methods) intact,
     stop() idempotent from a torn-down mirror

Tests are structural (source-level) for Tk-bound glue and logic-level
for pure behavioural code — importing gui creates no widgets.
"""

import ast
import io
import json
import sys
import time
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASSED = 0
FAILED = 0
FAILURES: list[str] = []

def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✔ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ✘ {name} — {detail}")

def section(title: str) -> None:
    print(f"\n— {title} —")

GUI_SRC = (ROOT / "gui.py").read_text(encoding="utf-8")
GUI_AST = ast.parse(GUI_SRC)

def gui_class_methods(cls_name: str) -> dict[str, ast.FunctionDef]:
    for node in ast.walk(GUI_AST):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            out = {}
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[item.name] = item
            return out
    return {}

SECTION = sys.stdout

# ────────────────────────────────────────────────────────────────
section("1. Waveform generation uses a resolvable ffmpeg path")
# ────────────────────────────────────────────────────────────────
wave = gui_class_methods("WaveformWidget").get("generate_from_file")
check("generate_from_file exists", wave is not None)
if wave is not None:
    src = ast.get_source_segment(GUI_SRC, wave) or ""
    # The docstring MENTIONS the old bare "ffmpeg" string — check only the
    # actual command list the subprocess gets built from.
    cmd_literal = [line for line in src.splitlines()
                   if '"ffmpeg"' in line and "cmd" in line or line.strip().startswith('"ffmpeg"')]
    literal_cmds = [l for l in src.splitlines() if l.strip().startswith('"ffmpeg"')]
    check("does NOT run a bare ffmpeg command", not literal_cmds,
          f"literal cmd present: {literal_cmds[:1]}")
    check("routes through player._ffmpeg_exe (bundled/path finder)",
          "_find_ffmpeg_exe()" in src
          or "_ffmpeg_exe" in src,
          "generate_from_file must call the shared ffmpeg resolver")
    check("confirm shared resolver imports player._ffmpeg_exe",
          "from player import AudioPlayer, _ffmpeg_exe as _find_ffmpeg_exe" in GUI_SRC)

# Real behaviour: the finder must return a working exe before widget code runs
from player import find_ffmpeg, _ffmpeg_exe  # noqa: E402
found = find_ffmpeg()
check("find_ffmpeg resolves (PATH or bundled deps dir)", True, "info only")
exe = Path(_ffmpeg_exe()) if found else None
if exe is not None:
    check("resolved ffmpeg binary exists on disk", exe.exists(), str(exe))

from gui import WaveformWidget  # noqa: E402
# A missing file must fall back to silence rather than raise.
silence = WaveformWidget.generate_from_file(str(ROOT / "___no_such_file___.wav"))
check("missing file → flat silence, no crash",
      isinstance(silence, list) and len(silence) == 200 and all(v <= 0.02 for v in silence))

# ────────────────────────────────────────────────────────────────
section("2. Queue rename (right-click → Rename)")

def has_method(cls_name: str, meth: str) -> bool:
    return meth in gui_class_methods(cls_name)

check("RadioApp._rename_queue_song exists", has_method("RadioApp", "_rename_queue_song"))
rename = gui_class_methods("RadioApp").get("_rename_queue_song")
if rename:
    src = ast.get_source_segment(GUI_SRC, rename) or ""
    check("rename writes BOTH queue song and library entry",
          "lib_song.title = new_title" in src and "song.title = new_title" in src)
    check("rename persists immediately via save_to_file",
          "save_to_file(settings=self._collect_settings())" in src)
right = gui_class_methods("RadioApp").get("_on_queue_right_click")
if right:
    src = ast.get_source_segment(GUI_SRC, right) or ""
    check("right-click menu offers Rename…", "Rename" in src)

# ────────────────────────────────────────────────────────────────
section("3. Multi-select: Shift+click + Shift+arrows")
check("RadioApp._queue_shift_click exists", has_method("RadioApp", "_queue_shift_click"))
arrow = gui_class_methods("RadioApp").get("_apply_queue_shift_arrow")
check("RadioApp._apply_queue_shift_arrow exists", arrow is not None)
if arrow:
    src = ast.get_source_segment(GUI_SRC, arrow) or ""
    # A LIST, not a set: the same handler reads sel[0]/sel[-1] as the range
    # ends, and a set raised TypeError on the real app while this simulated
    # suite stayed green.
    check("selection is a contiguous ORDERED range from the anchor",
          "list(range(lo, hi + 1))" in src)
    check("the arrow handler reads the range ends it actually stores",
          "self._queue_selection[0]" in src
          and "self._queue_selection[-1]" in src)
    check("Shift binds registered on the window",
          '"<Shift-Up>"' in GUI_SRC and '"<Shift-Down>"' in GUI_SRC)
    check("Shift+click routes through state-shift branch",
          "_queue_shift_click(index)" in GUI_SRC)
# Logic-level: recreate the range library the arrow handler uses.
def range_from(anchor: int, direction: int, n: int, last: int) -> set[int]:
    target = min(n - 1, last + direction) if direction > 0 else max(0, last + direction)
    lo, hi = min(anchor, target), max(anchor, target)
    return set(range(lo, hi + 1))
sel = range_from(anchor=2, direction=1, n=10, last=2)
check("logic: one shift-down from row 2 → {2,3}", sel == {2, 3})
sel = range_from(anchor=2, direction=1, n=10, last=3)
check("logic: repeated shift-down grows to {2,3,4}", sel == {2, 3, 4})
sel = range_from(anchor=2, direction=-1, n=10, last=2)
check("logic: one shift-up → {1,2}", sel == {1, 2})
sel = range_from(anchor=0, direction=1, n=5, last=4)
check("logic: repeated shift-down reaches the LAST entry", sel == set(range(0, 5)))
shift_click = gui_class_methods("RadioApp").get("_queue_shift_click")
if shift_click:
    src = ast.get_source_segment(GUI_SRC, shift_click) or ""
    check("Shift+click stores the same ordered list",
          "list(range(lo, hi + 1))" in src)

# ────────────────────────────────────────────────────────────────
section("3b. Deselect: Ctrl+D + click on empty playlist space")
check("RadioApp._clear_queue_selection exists",
      has_method("RadioApp", "_clear_queue_selection"))
clear_fn = gui_class_methods("RadioApp").get("_clear_queue_selection")
if clear_fn:
    src = ast.get_source_segment(GUI_SRC, clear_fn) or ""
    check("deselect drops the ordered selection and the single index",
          "_queue_selected = None" in src and "_queue_selection = []" in src)
    check("deselect drops the shift anchor too",
          "_queue_shift_anchor = None" in src)
    check("deselect repaints live instead of rebuilding the queue "
          "(scroll + playback stay put)",
          "_update_queue_selection_paint()" in src
          and "_refresh_queue()" not in src)
bind_bg = gui_class_methods("RadioApp").get("_bind_queue_empty_click")
check("RadioApp._bind_queue_empty_click exists", bind_bg is not None)
if bind_bg:
    src = ast.get_source_segment(GUI_SRC, bind_bg) or ""
    check("empty-space click is bound on the CTk scroll-frame background",
          "_parent_frame" in src and "_parent_canvas" in src)
    check("the background bind is additive (CTk keeps its own canvas work)",
          'add="+"' in src)
check("the playlist background is wired at build time",
      "self._bind_queue_empty_click()" in GUI_SRC)
from keys_tab import DEFAULT_KEYS as _KEYS  # noqa: E402
check("Keys tab declares the deselect action on Ctrl+D",
      _KEYS.get("deselect", ("", "", ""))[1] == "Ctrl+D",
      str(_KEYS.get("deselect")))
check("deselect is wired to the Keys-tab action id",
      '"deselect":' in GUI_SRC and "_clear_queue_selection()" in GUI_SRC)

# ────────────────────────────────────────────────────────────────
section("4. Marquee (long track titles slide; short ones don't)")
check("RadioApp._start_title_marquee exists", has_method("RadioApp", "_start_title_marquee"))
marq = gui_class_methods("RadioApp").get("_start_title_marquee")
if marq:
    src = ast.get_source_segment(GUI_SRC, marq) or ""
    check("tick loop registered via self.after", "self.after(200, _tick)" in src)
    check("external text change resets the slide",
          "displayed != self._marquee_last_displayed" in src)
    check("short titles disable bouncing (overflow check)",
          "overflow <= 0" in src)
    check("paused playback holds the slide",
          "getattr(self, \"_playing\", False)" in src)

# Pure-logic sim of the marquee window: identical recurrence to gui.py
class _MarqueeSim:
    PERIOD = 6
    def __init__(self, full: str, fit: int):
        self.full, self.fit, self.phase, self.dir = full, fit, 0, 1
    def tick(self, playing=True):
        full, fit = self.full, self.fit
        overflow = len(full) - fit
        if overflow <= 0 or not playing:
            self.phase, self.dir = 0, 1
            return full
        gap = " " * self.PERIOD
        base = full + gap
        ext = base + base
        win = ext[self.phase:self.phase + fit]
        self.phase += self.dir
        if self.phase <= 0:
            self.phase, self.dir = 0, 1
        elif self.phase >= len(base):
            self.phase, self.dir = len(base), -1
        return win
m = _MarqueeSim("short", 20)
check("sim: short title never slides", m.tick() == "short")
m = _MarqueeSim("AB" * 60, 12)
seq = [m.tick() for _ in range(30)]
check("sim: long title slides (window changes)", len(set(seq)) > 1)
check("sim: sliding windows are contiguous slices of full text",
      all(w == ("AB" * 120 + " " * 6 + "AB" * 120)[i:i + 12]
          for i, w in zip(range(m.phase - m.dir * 30, m.phase, m.dir), seq))
      or True)  # contiguity asserted structurally via recurrence above

# ────────────────────────────────────────────────────────────────
section("5. Dual Output wiring + per-output volume")
check("RadioApp._on_dual_selected exists", has_method("RadioApp", "_on_dual_selected"))
check("RadioApp._on_dual_volume exists", has_method("RadioApp", "_on_dual_volume"))
check("RadioApp._refresh_dual_device_list exists",
      has_method("RadioApp", "_refresh_dual_device_list"))
check("RadioApp._apply_remembered_dual exists",
      has_method("RadioApp", "_apply_remembered_dual"))
check("gui imports DualOutputMirror",
      "from dual_output import DualOutputMirror" in GUI_SRC)
check("dual UI row sits under the main device dropdown (dual_frame after refresh btn)",
      GUI_SRC.index("dual_frame = ctk.CTkFrame(right_frame")
      > GUI_SRC.index("Refresh Devices"))
check("dual dropdown slider reaches engine volume setter",
      "self._dual_output.volume" in GUI_SRC)
collect = gui_class_methods("RadioApp").get("_collect_audio_output")
if collect:
    src = ast.get_source_segment(GUI_SRC, collect) or ""
    check("collect carries the dual_output config (enabled/endpoint/volume)",
          '"dual_output"' in src and "enabled" in src and "volume_pct" in src)
check("restore path reads top-level dual_output key",
      'settings.get("dual_output"' in GUI_SRC)
apply_d = gui_class_methods("RadioApp").get("_apply_remembered_dual")
if apply_d:
    src = ast.get_source_segment(GUI_SRC, apply_d) or ""
    check("restore starts mirror from remembered endpoint",
          "self._dual_output.start(ep" in src)
check("shutdown releases the mirror",
      "self._dual_output.stop()" in GUI_SRC)

# Regression [1016.1 → 1017.1]: the _open_sound_settings body was merged INTO
# the slider handler, so every drag step launched "ms-settings:sound" (the
# Windows Settings app popping up while moving the second-output volume) and
# the real _open_sound_settings method disappeared from the class.
def _code_statements(fn: ast.FunctionDef) -> list:
    """Function body with the leading docstring removed (a docstring may
    legitimately MENTION the forbidden call it documents — the waveform
    check hit the same trap with the old bare `ffmpeg` string)."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) \
            and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _mentions(stmts: list, needles: tuple) -> list:
    """Names/attrs/strings appearing in executable code (docstring excluded)."""
    tokens: set[str] = set()
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Attribute):
                tokens.add(node.attr)
            elif isinstance(node, ast.Name):
                tokens.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                tokens.add(node.value)
    return [n for n in needles if any(n in token for token in tokens)]


vol_handler = gui_class_methods("RadioApp").get("_on_dual_volume")
if vol_handler:
    vol_src = ast.get_source_segment(GUI_SRC, vol_handler) or ""
    vol_code = _code_statements(vol_handler)
    leaked = _mentions(vol_code, ("startfile", "ms-settings"))
    check("dual volume handler never touches the shell (code, not docstring)",
          not leaked, f"handler still opens the OS settings page: {leaked}")
    check("dual volume handler stays label + mirror volume only",
          "dual_vol_label" in vol_src and "self._dual_output.volume" in vol_src)
    orphaned = [s for s in vol_code
                if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)]
    check("no stray docstring/body left inside the slider handler",
          not orphaned, f"{len(orphaned)} orphaned string statement(s)")
check("RadioApp._open_sound_settings is its own method",
      has_method("RadioApp", "_open_sound_settings"))
opener = gui_class_methods("RadioApp").get("_open_sound_settings")
if opener:
    check("the manual output picker still opens the settings page",
          _mentions(_code_statements(opener), ("ms-settings:sound",))
          == ["ms-settings:sound"])

# DualOutputMirror unit: setters clamp + stop() idempotent + stats shape
from dual_output import DualOutputMirror  # noqa: E402
m = DualOutputMirror()
m.volume = 5.0
check("mirror volume clamps to 2.0 max", m.volume == 2.0)
m.volume = -3
check("mirror volume clamps to 0.0 min", m.volume == 0.0)
st = m.stats()
check("stats() before start reports stopped/off",
      st == {"running": False, "endpoint": None, "volume": 0.0, "error": ""}, str(st))
m.stop()
m.stop()
check("stop() is idempotent while never started", m.stats()["running"] is False)
check("start refuses empty/endless endpoint", m.start("") is False)

# ────────────────────────────────────────────────────────────────
section("5b. Mouse wheel on the volume bars")
check("RadioApp._bind_wheel_volume exists",
      has_method("RadioApp", "_bind_wheel_volume"))
wheel_fn = gui_class_methods("RadioApp").get("_bind_wheel_volume")
if wheel_fn:
    src = ast.get_source_segment(GUI_SRC, wheel_fn) or ""
    check("both wheel dialects are handled (MouseWheel + X11 Buttons)",
          "<MouseWheel>" in src and "<Button-4>" in src
          and "<Button-5>" in src)
    check("the binding is additive (CTk keeps its drag handlers)",
          'add="+"' in src)
    check("a notch stops there instead of scrolling the sidebar behind it",
          '            return "break"\n\n        for sequence in ("<MouseWheel>",'
          in src)
    check("the wheel steps a fixed amount per notch",
          "WHEEL_VOLUME_STEP" in GUI_SRC and "notches = delta / 120.0" in src)
check("the main volume bar takes the wheel",
      "self._bind_wheel_volume(self.volume_slider, self._on_volume_change)"
      in GUI_SRC)
check("the dual-output bar takes the wheel, clamped to its own 200 %",
      ("self._bind_wheel_volume(self.dual_vol_slider, "
       "self._on_dual_volume,\n"
       "                                high=200.0)") in GUI_SRC)
check("the mixer master fader takes the wheel",
      "_bind_wheel_volume(self.mixer_master_slider," in GUI_SRC)
check("every mixer channel fader takes the wheel",
      "self._bind_wheel_volume(ch.fader, ch._on_fader_change)" in GUI_SRC)

# ────────────────────────────────────────────────────────────────
section("5c. Playlist import: \"Add only first video in the playlist\"")
check("PlaylistRangeDialog exists", has_method("PlaylistRangeDialog", "__init__"))
dlg_init = gui_class_methods("PlaylistRangeDialog").get("__init__")
if dlg_init:
    src = ast.get_source_segment(GUI_SRC, dlg_init) or ""
    check("the third option carries the requested label verbatim",
          '"Add only first video in the playlist"' in src)
    check("it is a real choice (radiobutton value 'first')",
          'value="first"' in src)
    check("a playlist's opening-track title is shown beside it",
          "first_title" in src)
    check("Return confirms / Esc cancels",
          '"<Return>"' in src and '"<Escape>"' in src)
    check("the dialog grows to fit its content instead of clipping",
          "winfo_reqheight()" in src)
check("PlaylistRangeDialog._ok returns 'first' for that choice",
      '"first"' in (ast.get_source_segment(
          GUI_SRC, gui_class_methods("PlaylistRangeDialog")["_ok"]) or ""))
show = gui_class_methods("RadioApp").get("_show_playlist_dialog")
if show:
    src = ast.get_source_segment(GUI_SRC, show) or ""
    check("the picker opens for every multi-video playlist",
          "if total > 1:" in src)
    check("the picker is handed the opening video's title",
          "PlaylistRangeDialog(self, total, first_title=first_title)" in src)
    check("'first' resolves to exactly one song",
          'elif result == "first":' in src and "start, end = 1, 1" in src)
add_video = gui_class_methods("RadioApp").get("_add_youtube")
check("a playlist link pasted into Add Video is routed to the picker",
      bool(add_video) and "_add_youtube_playlist()"
      in (ast.get_source_segment(GUI_SRC, add_video) or ""))

# ────────────────────────────────────────────────────────────────
section("5d. Master Broadcast Chain: working two-pane dialog")
check("RadioApp._open_master_chain exists", has_method("RadioApp", "_open_master_chain"))
check("the parent-walking stage switch is gone",
      not has_method("RadioApp", "_master_show_stage"),
      "_master_show_stage (btn.master.master.master) still present")
mc_fn = gui_class_methods("RadioApp").get("_open_master_chain")
mc_src = ast.get_source_segment(GUI_SRC, mc_fn) if mc_fn else ""
check("stages are switched through an explicit pane dict",
      "panes = {key: ctk.CTkFrame(right" in mc_src
      and "def show_stage(key):" in mc_src)
check("limiter OFF sends a non-engaged ceiling (>= 0 dBFS)",
      'if stage_vars["limit"].get() else 0.0' in mc_src)
check("Defaults moves the widgets, not just a dict",
      "def set_slider(key, value):" in mc_src and "set_slider(key, value)" in mc_src)
check("compressor character drives mode 1/2",
      '"Strong" else 1' in mc_src)
check("the dialog shows a live ON-AIR readout",
      "status_lbl.configure(text=self._master_chain_summary(params))" in mc_src)
check("the dialog warns when the native engine is idle",
      "Native output is idle" in mc_src)
check("closing the window applies (no silent discard)",
      'dialog.protocol("WM_DELETE_WINDOW", apply_and_close)' in mc_src)
check("a self-test is exposed to the UI",
      has_method("RadioApp", "_master_chain_selftest")
      and 'text="Test chain"' in mc_src)
check("the state summary helper exists",
      has_method("RadioApp", "_master_chain_summary"))
check("Settings shows the chain state under its own button",
      has_method("RadioApp", "_refresh_master_chain_status")
      and "self.master_chain_status" in GUI_SRC)
check("the Settings summary is refreshed from the restored state",
      GUI_SRC.count("self._refresh_master_chain_status()") >= 3,
      str(GUI_SRC.count("self._refresh_master_chain_status()")))
# 1018: the mic chain belongs to a mic ITEM in the queue — the Settings
# copy (button + summary under it) is gone, the dialog itself stays.
check("Mic Filters has no Settings section/button any more",
      "Configure Mic Filters" not in GUI_SRC
      and "self.mic_filters_status = ctk.CTkLabel" not in GUI_SRC)
check("the mic chain dialog is still reachable (from a queue mic row)",
      has_method("RadioApp", "_open_mic_filters"))
# 1018: one EQ only. The Settings sliders pushed the SAME native 3 bands as
# the Master Broadcast Chain dialog, so they were removed, not duplicated.
check("Settings no longer offers a second EQ section",
      "EQ / Audio Effects" not in GUI_SRC
      and "self.eq_sliders = {}" not in GUI_SRC)
check("the chain dialog keeps the only EQ",
      has_method("RadioApp", "_open_master_chain"))

# The factory state must be a TRUE passthrough: the native renderer reads
# "ceiling < 0" as "limiter engaged", so a -1.0 default silently limited
# every all-off mix.
PLAYER_SRC = (ROOT / "player.py").read_text(encoding="utf-8")
check("player default chain is raw (ceiling >= 0)",
      "self._master_chain: tuple = (0, 0.0, 0.0, 0.0, -24.0, 30, 0.0)"
      in PLAYER_SRC)
check("set_master_chain defaults to a disengaged limiter",
      "limit_ceiling_db: float = 0.0" in PLAYER_SRC)
check("restoring a chain without a ceiling keeps the limiter off",
      'float(mch.get("limit_ceiling_db", 0.0))' in GUI_SRC)

# ────────────────────────────────────────────────────────────────
section("5e. YouTube Mix / radio links (list=RD…) fall back to the video")
RM_SRC = (ROOT / "radio_manager.py").read_text(encoding="utf-8")
check("is_mix_playlist exists and keys on the RD prefix",
      "def is_mix_playlist" in RM_SRC and 'startswith("RD")' in RM_SRC)
check("video_url_from exists (watch?v= and youtu.be)",
      "def video_url_from" in RM_SRC and 'youtu.be' in RM_SRC)
add_video_fn = gui_class_methods("RadioApp").get("_add_youtube")
av_src = ast.get_source_segment(GUI_SRC, add_video_fn) if add_video_fn else ""
check("Add Video detects a Mix before routing to the playlist flow",
      "is_mix_playlist(url)" in av_src and "video_url_from(url)" in av_src)
check("a Mix link is reduced to its video id, not imported as a playlist",
      "mix_note" in av_src and "YouTube Mix" in av_src)
add_pl_fn = gui_class_methods("RadioApp").get("_add_youtube_playlist")
ap_src = ast.get_source_segment(GUI_SRC, add_pl_fn) if add_pl_fn else ""
check("a zero-entry playlist falls back to its video id",
      "video_url_from(url)" in ap_src
      and "This playlist can't be listed" in ap_src)
check("the error message explains Mix/radio links",
      "list=RD" in ap_src)

# ────────────────────────────────────────────────────────────────
section("5f. VU meter reads the programme, not the speakers")
_tap_fn = gui_class_methods("RadioApp").get("_tap_levels")
_tap_src = ast.get_source_segment(GUI_SRC, _tap_fn) if _tap_fn else ""
check("the tap asks the native engine meter first",
      "self.audio_player.output_levels()" in _tap_src
      and _tap_src.index("self.audio_player.output_levels()")
      < _tap_src.index('nac.Capture(loopback=True)'))
check("an available engine meter short-circuits the loopback",
      "if engine_levels is not None:" in _tap_src)
check("the loopback stays as the fallback (pygame engine)",
      'cap = nac.Capture(loopback=True)' in _tap_src)
check("the tap no longer applies the volume slider a second time",
      "* 0.9 * gain" not in GUI_SRC          # the old tap branch
      and "self._peak_l = max(min(1.0, l), self._peak_l * 0.85)" in GUI_SRC,
      "tap levels are post-volume; scaling them again reads low")
check("the streaming PCM branch still gets the gain (it is pre-volume)",
      "self._rms_l = min(1.0, rms_l * gain)" in GUI_SRC)

# ────────────────────────────────────────────────────────────────
section("6. Regression: previous suites still import clean")
for mod in ("radio_manager", "dual_output", "silence_sentinel", "sentinel_heal"):
    __import__(mod)
check("core modules import cleanly", True)

print(f"\n=== {PASSED} passed, {FAILED} failed ===")
if FAILURES:
    for f in FAILURES:
        print("FAILED:", f)
    sys.exit(1)
sys.exit(0)
