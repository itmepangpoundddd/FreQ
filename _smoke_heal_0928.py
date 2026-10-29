#!/usr/bin/env python3
"""Smoke: Sentinel self-heal ladder [heal-0928].

Verifies (deterministic, injected clock — no audio, no Tk):
  1. mode off = events ignored entirely (default behaviour unchanged)
  2. dry_run plans skip/restart/alert but calls NO callback
  3. active executes the ladder one step per event, cooldown-gated
  4. sustained audio resets the ladder (no skip storms)
  5. UI wiring on the real RadioApp: mode dropdown exists, settings
     persist the mode, the default is off
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# Isolate the user's real radio_queue.freq (RadioQueue saves to cwd).
os.chdir(tempfile.mkdtemp(prefix="freq_heal_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


from sentinel_heal import (  # noqa: E402
    RecoveryLadder, MODE_OFF, MODE_DRY_RUN, MODE_ACTIVE,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


calls = []
clock = FakeClock()
ladder = RecoveryLadder(
    on_skip=lambda: calls.append("skip"),
    on_restart=lambda: calls.append("restart"),
    on_alert=lambda info: calls.append("alert"),
    clock=clock,
)

# ── 1. default off ───────────────────────────────────────────────
check("default mode is off", ladder.mode == MODE_OFF)
check("off ignores events", ladder.handle({"silent_for": 10}) is None)
check("off called nothing", calls == [])

# ── 2. dry run: plan, never execute ──────────────────────────────
ladder.configure(mode=MODE_DRY_RUN)
a1 = ladder.handle({"silent_for": 10})
check("dry_run plans skip", a1 == "skip" and calls == [],
      f"action={a1} calls={calls}")
clock.advance(50)
a2 = ladder.handle({"silent_for": 20})
check("dry_run plans restart next", a2 == "restart" and calls == [])
clock.advance(100)
a3 = ladder.handle({"silent_for": 30})
check("dry_run plans alert third", a3 == "alert" and calls == [])
snap = ladder.snapshot()
check("dry_run took zero real actions",
      snap["actions_taken"] == {"skip": 0, "restart": 0, "alert": 0},
      str(snap["actions_taken"]))

# ── 3. active: executes, escalates, cooldown-gated ───────────────
# Fresh ladder: mode chosen before events start (the real usage); the
# dry-run ladder above deliberately kept its own escalation state.
ladder_a = RecoveryLadder(
    on_skip=lambda: calls.append("skip"),
    on_restart=lambda: calls.append("restart"),
    on_alert=lambda info: calls.append("alert"),
    clock=clock,
)
ladder_a.configure(mode=MODE_ACTIVE)
a = ladder_a.handle({"silent_for": 11})
check("active executes skip", a == "skip" and calls == ["skip"],
      f"action={a} calls={calls}")
# The sentinel rate-limits events to ~30 s apart while dead air continues,
# so escalate with realistic gaps; cooldowns then gate repeats.
clock.advance(35)
check("escalates to restart on the next event",
      ladder_a.handle({"silent_for": 45}) == "restart"
      and calls == ["skip", "restart"])
clock.advance(35)
check("escalates to alert on the third event",
      ladder_a.handle({"silent_for": 80}) == "alert"
      and calls == ["skip", "restart", "alert"])
clock.advance(35)
check("alert cooldown suppresses the repeat",
      ladder_a.handle({"silent_for": 120}) is None
      and len(calls) == 3)
clock.advance(350)
check("alert repeats after its cooldown (stays at alert)",
      ladder_a.handle({"silent_for": 500}) == "alert"
      and calls.count("skip") == 1 and calls.count("restart") == 1
      and calls.count("alert") == 2)

# ── 4. sustained audio resets the ladder ─────────────────────────
clock2 = FakeClock()
ladder2 = RecoveryLadder(on_skip=lambda: calls.append("skip2"),
                         on_restart=lambda: calls.append("restart2"),
                         clock=clock2)
ladder2.configure(mode=MODE_ACTIVE)
a = ladder2.handle({"silent_for": 12})
check("first event skips", a == "skip")
# 5 s of audio — NOT enough to reset (needs 15 s sustained)
for _ in range(5):
    clock2.advance(1)
    ladder2.note_audio(True)
ladder2.note_audio(False)                    # blip ends the streak
clock2.advance(400)
a = ladder2.handle({"silent_for": 12})
check("short audio blip does NOT reset ladder", a == "restart",
      f"got {a}, want restart (still escalated)")
# full 15 s sustained audio → reset to step 0
for _ in range(16):
    clock2.advance(1)
    ladder2.note_audio(True)
snap2 = ladder2.snapshot()
check("sustained audio resets ladder",
      snap2["step"] == 0 and snap2["recovered"] >= 1, str(snap2))

# fresh scenario: the storm guard after recovery
clock3 = FakeClock()
ladder3 = RecoveryLadder(on_skip=lambda: calls.append("skip3"),
                         on_restart=lambda: calls.append("restart3"),
                         clock=clock3)
ladder3.configure(mode=MODE_ACTIVE)
a = ladder3.handle({"silent_for": 12})
check("storm-guard scenario: first event skips", a == "skip")
for _ in range(16):
    clock3.advance(1)
    ladder3.note_audio(True)                 # sustained → reset
# re-silence 2 s later → skip still inside its 45 s cooldown:
# a flapping stream cannot skip-spam
clock3.advance(2)
a = ladder3.handle({"silent_for": 12})
check("re-silence right after recovery is suppressed", a is None,
      f"got {a}")
clock3.advance(45)
a = ladder3.handle({"silent_for": 12})
check("skip available again after its cooldown", a == "skip", f"got {a}")

# ── 5. UI wiring on the real app ─────────────────────────────────
from gui import RadioApp  # noqa: E402

app = RadioApp()
try:
    app.update_idletasks()
    check("heal ladder constructed off by default",
          app.sentinel_heal.mode == "off")
    check("heal mode dropdown exists", hasattr(app, "sentinel_heal_mode_var"))
    app.sentinel_heal_mode_var.set("dry_run")
    app._on_sentinel_mode_change()
    check("settings dropdown changes mode",
          app.sentinel_heal.mode == "dry_run")
    persisted = app._collect_settings()["silence_sentinel"]["heal_mode"]
    check("settings persist carries heal_mode", persisted == "dry_run",
          str(persisted))
    # restore path
    app._restore_settings({"silence_sentinel": {
        "enabled": False, "silence_seconds": 10.0, "threshold": 0.004,
        "heal_mode": "active"}})
    check("restore applies heal_mode", app.sentinel_heal.mode == "active")
    app.sentinel_heal_mode_var.set("off")
    app._on_sentinel_mode_change()
    check("back to off", app.sentinel_heal.mode == "off")
    # structural: the sentinel module must stay action-free
    sent_src = (Path(__file__).parent / "silence_sentinel.py").read_text(
        encoding="utf-8")
    banned = [t for t in ("skip_next", "_play_index", "reopen_output",
                          "play_file", "messagebox")
              if t in sent_src]
    check("sentinel module contains no recovery actions", not banned,
          str(banned))
finally:
    try:
        app.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
