#!/usr/bin/env python3
"""Silence Sentinel (U4 — Ultimate Automation, dry-run first).

Watches the broadcast output level and reports when it stays below a
silence threshold for too long. In **dry-run** mode (the default) it only
raises a callback / logs a warning — it NEVER touches playback, the queue
or the engine. Recovery actions belong to a later Ultimate build; this
module deliberately contains none.

Design rules (Ultimate ground rules):
  * opt-in: nothing happens unless ``enabled`` is set (default False)
  * passive: in dry-run the sentinel is a pure observer; playback is
    never started, stopped, paused or re-queued from here
  * testable: no tkinter, no audio hardware — callers feed it levels
    (e.g. from the same WASAPI loopback tap that drives the VU meter)

Usage:
    sent = SilenceSentinel(on_silence=lambda info: log.warning(...))
    sent.configure(enabled=True, silence_seconds=12.0, threshold=0.004)
    ...
    sent.observe(levels, playing, mic_active)   # ~every GUI tick (250 ms)
    ...
    sent.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("freq.sentinel")

# Defaults documented for the on-air use case: a song fade / quiet passage
# must not trip it, but dead air (jingle failed, engine stalled) should.
DEFAULT_SILENCE_SECONDS = 10.0
DEFAULT_THRESHOLD = 0.004          # linear peak (≈ -48 dBFS)
DEFAULT_ENABLED = False            # opt-in, always
DEFAULT_DRY_RUN = True             # dry-run until recovery actions exist


class SilenceSentinel:
    """Passive silence watchdog fed with output levels by the caller."""

    def __init__(
        self,
        on_silence: Optional[Callable[[Dict[str, Any]], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_silence = on_silence
        self._clock = clock
        self._lock = threading.Lock()

        # Configuration (guard every field with the lock; observe() runs
        # on the GUI thread while stop()/configure() may come from anywhere).
        self._enabled = DEFAULT_ENABLED
        self._dry_run = DEFAULT_DRY_RUN
        self._threshold = DEFAULT_THRESHOLD
        self._silence_seconds = DEFAULT_SILENCE_SECONDS

        # Runtime state
        self._silence_since: Optional[float] = None
        self._last_report: Optional[float] = None
        self._min_report_gap = 30.0      # never spam the same alert

        # Counters for tests / the daily report
        self.silence_events = 0
        self.last_event: Optional[Dict[str, Any]] = None

    # ── configuration ────────────────────────────────────────────────

    def configure(
        self,
        enabled: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        silence_seconds: Optional[float] = None,
        threshold: Optional[float] = None,
        min_report_gap: Optional[float] = None,
    ) -> None:
        """Update configuration. Safe to call while running."""
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if dry_run is not None:
                self._dry_run = bool(dry_run)
            if threshold is not None:
                self._threshold = max(0.0, float(threshold))
            if silence_seconds is not None:
                # 1 s floor: shorter windows are meaningless at the GUI
                # tick rate and would turn the sentinel into a stutter.
                self._silence_seconds = max(1.0, float(silence_seconds))
            if min_report_gap is not None:
                self._min_report_gap = max(0.0, float(min_report_gap))
            # Config changes reset the window so a threshold tightened
            # mid-song does not instantly fire on already-elapsed quiet.
            self._silence_since = None

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def dry_run(self) -> bool:
        with self._lock:
            return self._dry_run

    # ── observation ──────────────────────────────────────────────────

    def observe(
        self,
        levels: Optional[Tuple[float, float]],
        playing: bool,
        mic_active: bool = False,
    ) -> None:
        """Feed one measurement tick.

        ``levels`` is the (left, right) linear peak from the output tap —
        pass the same value the VU meter received (``None`` when the tap
        is unavailable). ``mic_active`` marks live-mic segments: a mic on
        air counts as audio even when music is quiet.
        """
        with self._lock:
            if not self._enabled:
                self._silence_since = None
                return

            now = self._clock()

            # Not broadcasting right now → dead air is expected; this is a
            # broadcast-output sentinel, not a "please play something" nag.
            if not playing and not mic_active:
                self._silence_since = None
                return

            # A live mic segment IS the broadcast: while one is on air the
            # output tap is not the signal to judge, so stand down. (The
            # tap carries the full mix; zero levels under a live mic mean
            # the mic pipeline is muted/routed elsewhere — a real condition,
            # but deciding it belongs to the active-mode build, not dry-run.)
            if mic_active:
                self._silence_since = None
                return

            if levels is None:
                # Tap unavailable: cannot distinguish silence from a broken
                # meter — refuse to guess (Ultimate rule: no data → no nag).
                self._silence_since = None
                return

            peak = max(0.0, float(levels[0]), float(levels[1]))

            if peak > self._threshold:
                self._silence_since = None
                return

            if self._silence_since is None:
                self._silence_since = now
                return

            silent_for = now - self._silence_since
            if silent_for >= self._silence_seconds:
                self._fire(silent_for, peak, now)

    def _fire(self, silent_for: float, peak: float, now: float) -> None:
        """Report one silence event (dry-run: report only, by design)."""
        # Rate-limit FIRST, then re-arm the window — a rate-limited report
        # must still count as "handled" so the next threshold crossing
        # inside the same dead-air stretch is swallowed by the gap check
        # (continuous dead air = one alert per gap, not a storm).
        rate_limited = self._last_report is not None and \
            (now - self._last_report) < self._min_report_gap
        if rate_limited:
            self._silence_since = now
            return
        self._last_report = now
        self._silence_since = now
        self.silence_events += 1

        info = {
            "silent_for": round(silent_for, 3),
            "peak": peak,
            "threshold": self._threshold,
            "dry_run": self._dry_run,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.last_event = info
        logger.warning(
            "SILENCE%s: output silent for %.1fs (peak %.4f <= %.4f)",
            " (dry-run)" if self._dry_run else "",
            silent_for, peak, self._threshold,
        )
        callback = self._on_silence
        if callback is not None:
            try:
                callback(info)
            except Exception:
                logger.exception("on_silence callback failed")

    # ── lifecycle ────────────────────────────────────────────────────

    def stop(self) -> None:
        """Stop watching and clear the silence window."""
        with self._lock:
            self._enabled = False
            self._silence_since = None

    def snapshot(self) -> Dict[str, Any]:
        """State for the About/daily report — counters and current window."""
        with self._lock:
            silent_for = None
            if self._silence_since is not None:
                silent_for = round(self._clock() - self._silence_since, 3)
            return {
                "enabled": self._enabled,
                "dry_run": self._dry_run,
                "threshold": self._threshold,
                "silence_seconds": self._silence_seconds,
                "silence_events": self.silence_events,
                "silent_for_now": silent_for,
                "last_event": dict(self.last_event) if self.last_event else None,
            }
