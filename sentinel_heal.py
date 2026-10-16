#!/usr/bin/env python3
"""Sentinel self-heal ladder (U4 — Ultimate Automation, 0928.1).

Consumes SilenceSentinel events and decides on a recovery action. The
sentinel itself stays a passive observer BY DESIGN (structural tests
forbid playback calls in silence_sentinel.py); every action lives here,
behind injected callbacks, behind a mode switch, and behind cooldowns.

Modes:
  "off"      — ignore events entirely (default)
  "dry_run"  — decide + log what WOULD be done, execute nothing
  "active"   — execute the ladder through the injected callbacks

Ladder (one step per event, cooldown-gated, escalates while dead air
continues and RESETS after audio is sustained again):
  1. skip    — advance to the next track          (on_skip)
  2. restart — reopen the output engine           (on_restart)
  3. alert   — raise a human attention alarm      (on_alert)
After step 3 the ladder stays at "alert": alerts repeat on their cooldown
but never escalate to skip/restart storms.

Recovery semantics: the caller feeds note_audio(True/False) every UI
tick; counters reset only after SUSTAIN_RECOVERY seconds of continuous
audio, so a 3-second blip of music between failures cannot loop the
ladder back to step 1 and cause a skip storm.

Everything is clock-injected and callback-injected: no tkinter, no audio
hardware, fully deterministic under test.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("freq.sentinel_heal")

MODE_OFF = "off"
MODE_DRY_RUN = "dry_run"
MODE_ACTIVE = "active"
MODES = (MODE_OFF, MODE_DRY_RUN, MODE_ACTIVE)

DEFAULT_SKIP_COOLDOWN = 45.0
DEFAULT_RESTART_COOLDOWN = 90.0
DEFAULT_ALERT_COOLDOWN = 300.0
SUSTAIN_RECOVERY = 15.0        # seconds of audio before counters reset


class RecoveryLadder:
    """Cooldown-gated escalation policy for sentinel silence events."""

    def __init__(
        self,
        on_skip: Optional[Callable[[], None]] = None,
        on_restart: Optional[Callable[[], None]] = None,
        on_alert: Optional[Callable[[Dict[str, Any]], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_skip = on_skip
        self._on_restart = on_restart
        self._on_alert = on_alert
        self._clock = clock
        self._lock = threading.Lock()

        self._mode = MODE_OFF
        self._step = 0                       # 0 = idle, 1..3 = ladder
        self._last_action_ts: Dict[str, float] = {}
        self._cooldowns = {
            "skip": DEFAULT_SKIP_COOLDOWN,
            "restart": DEFAULT_RESTART_COOLDOWN,
            "alert": DEFAULT_ALERT_COOLDOWN,
        }
        # Sustained-audio tracking for the recovery reset.
        self._audio_since: Optional[float] = None
        self._recovered_steps = 0
        # Counters for the settings/daily report. ``actions_taken`` counts
        # EXECUTED actions only; dry-run plans are counted separately so
        # a report never mistakes a rehearsal for the real thing.
        self.actions_taken: Dict[str, int] = {"skip": 0, "restart": 0, "alert": 0}
        self.planned: Dict[str, int] = {"skip": 0, "restart": 0, "alert": 0}
        self.last_action: Optional[Dict[str, Any]] = None

    # ── configuration ────────────────────────────────────────────────
    def configure(self, mode: Optional[str] = None) -> None:
        with self._lock:
            if mode is not None and mode in MODES:
                self._mode = mode

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    # ── event intake ─────────────────────────────────────────────────
    def handle(self, info: Dict[str, Any]) -> Optional[str]:
        """Consume one silence event; return the action (or would-be
        action in dry-run). None = nothing done (off / suppressed)."""
        with self._lock:
            if self._mode == MODE_OFF:
                return None
            now = self._clock()
            step = min(self._step + 1, 3)
            action = ("skip", "restart", "alert")[step - 1]
            cooldown = self._cooldowns[action]
            last = self._last_action_ts.get(action)
            if last is not None and (now - last) < cooldown:
                return None                       # suppressed by cooldown
            self._step = step
            self._last_action_ts[action] = now
            dry = self._mode == MODE_DRY_RUN
            if dry:
                self.planned[action] += 1
            else:
                self.actions_taken[action] += 1
            detail = {
                "action": action,
                "step": step,
                "dry_run": dry,
                "silent_for": info.get("silent_for"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.last_action = detail
        # Run callbacks OUTSIDE the lock (GUI reads snapshot()).
        logger.warning(
            "SILENCE HEAL %s: %s (step %d)%s",
            "plan" if dry else "action", action, detail["step"],
            f" — dead air {info.get('silent_for', 0):.0f}s",
        )
        if dry:
            return action
        callback = {"skip": self._on_skip,
                    "restart": self._on_restart,
                    "alert": self._on_alert}.get(action)
        if callback is not None:
            try:
                # Contract: on_skip/on_restart take no arguments; on_alert
                # receives the event info dict. Call accordingly so a
                # zero-arg action callback never explodes on arity.
                callback(info) if action == "alert" else callback()
            except Exception:
                logger.exception("recovery action %s failed", action)
        return action

    # ── audio feedback (recovery reset) ──────────────────────────────
    def note_audio(self, present: bool) -> None:
        """Feed the audio-present state once per UI tick. Counters reset
        only after SUSTAIN_RECOVERY seconds of CONTINUOUS audio."""
        with self._lock:
            now = self._clock()
            if present:
                if self._audio_since is None:
                    self._audio_since = now
                elif (now - self._audio_since) >= SUSTAIN_RECOVERY \
                        and self._step:
                    self._step = 0
                    self._recovered_steps += 1
                    logger.info(
                        "SILENCE HEAL: audio sustained %.0fs — ladder reset",
                        now - self._audio_since,
                    )
            else:
                self._audio_since = None

    # ── reporting ────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode,
                "step": self._step,
                "actions_taken": dict(self.actions_taken),
                "planned": dict(self.planned),
                "recovered": self._recovered_steps,
                "last_action": dict(self.last_action) if self.last_action else None,
            }
