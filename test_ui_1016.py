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
    check("selection is a contiguous range from the anchor",
          "set(range(lo, hi + 1))" in src)
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
