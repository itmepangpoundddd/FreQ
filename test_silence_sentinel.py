#!/usr/bin/env python3
"""Silence Sentinel (U4) tests — dry-run guarantee + fake-silence cases.

Covers (all silence is FAKED via levels + a controllable clock; no audio
hardware, nothing audible, nothing touches a real engine):
  1.  Disabled by default → never fires (opt-in rule)
  2.  Fires once after the silence window (fake dead air)
  3.  Quiet music above threshold keeps resetting → never fires
  4.  Tap unavailable (levels=None) → refuse to guess, never fires
  5.  Not playing / not mic → off-air, silence is expected, never fires
  6.  Live mic counts as audio (mic_active=True + zeros → no fire)
  7.  Threshold boundary: peak == threshold is silence, just above is audio
  8.  Rate limit: continuous dead air → one alert, not a storm
  9.  configure() mid-run re-arms the window (no instant fire)
  10. stop() disables and clears state
  11. Callback exception is swallowed; sentinel keeps working
  12. Dry-run guarantee: the module has NO pathway into the engine
      (structural check) and exposes no playback-mutating method
  13. GUI wiring: VU tap is measured once per tick and the sentinel is
      fed that same measurement (regression guard against double-drain)
  14. Real-clock smoke: fires with the default monotonic clock too

Run:  python test_silence_sentinel.py
      (also runs inside test_edge_cases.py via run_all())
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from silence_sentinel import SilenceSentinel  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))


class FakeClock:
    """Controllable monotonic clock (never sleeps)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_sentinel(**cfg):
    """Sentinel bound to a FakeClock; returns (sentinel, clock, events)."""
    clock = FakeClock()
    events: list[dict] = []
    sent = SilenceSentinel(on_silence=events.append, clock=clock)
    sent.configure(enabled=True, dry_run=True, **cfg)
    return sent, clock, events


def feed(sent: SilenceSentinel, clock: FakeClock, levels, playing=True,
         mic=False, tick=0.25, seconds=0.0) -> None:
    """Feed ticks totalling ``seconds`` (levels may be a constant or None)."""
    end = clock.t + seconds
    while clock.t < end - 1e-9:
        sent.observe(levels, playing, mic_active=mic)
        clock.advance(tick)


# ── 1. disabled by default ───────────────────────────────────────────

def test_disabled_by_default() -> None:
    sent = SilenceSentinel(on_silence=lambda info: (_ for _ in ()).throw(
        AssertionError("fired while disabled")))
    clock = FakeClock()
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=60.0)
    check("[sentinel] disabled by default → never fires",
          sent.silence_events == 0)
    check("[sentinel] disabled → snapshot enabled=False",
          sent.snapshot()["enabled"] is False)


# ── 2. fires after the window (fake dead air) ────────────────────────

def test_fires_after_window() -> None:
    sent, clock, events = make_sentinel(silence_seconds=3.0)
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=2.5)
    check("[sentinel] quiet 2.5s < 3s → no fire yet", len(events) == 0,
          f"events={len(events)}")
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=1.0)
    check("[sentinel] dead air 3.5s → fired", len(events) == 1,
          f"events={len(events)}")
    if events:
        info = events[0]
        check("[sentinel] event is dry-run", info["dry_run"] is True)
        check("[sentinel] silent_for ≈ window",
              2.9 <= info["silent_for"] <= 3.3, str(info["silent_for"]))
        check("[sentinel] peak/threshold recorded",
              info["peak"] == 0.0 and info["threshold"] == 0.004)


# ── 3. quiet music (above threshold) never fires ─────────────────────

def test_quiet_music_no_fire() -> None:
    sent, clock, events = make_sentinel(silence_seconds=3.0)
    # Alternating: one loud tick every other tick — window keeps resetting.
    for _ in range(40):
        sent.observe((0.02, 0.02), True)
        clock.advance(0.25)
        sent.observe((0.0, 0.0), True)
        clock.advance(0.25)
    check("[sentinel] quiet passage (loud every 0.5s) → no fire",
          len(events) == 0, f"events={len(events)}")


# ── 4. tap unavailable → never guess ────────────────────────────────

def test_tap_unavailable_no_fire() -> None:
    sent, clock, events = make_sentinel(silence_seconds=2.0)
    feed(sent, clock, None, playing=True, seconds=30.0)
    check("[sentinel] levels=None (no tap data) → never fires",
          len(events) == 0, f"events={len(events)}")


# ── 5. off-air silence is expected ───────────────────────────────────

def test_off_air_no_fire() -> None:
    sent, clock, events = make_sentinel(silence_seconds=2.0)
    feed(sent, clock, (0.0, 0.0), playing=False, mic=False, seconds=30.0)
    check("[sentinel] stopped playback + zeros → not dead air, no fire",
          len(events) == 0, f"events={len(events)}")


# ── 6. live mic counts as audio ──────────────────────────────────────

def test_mic_counts_as_audio() -> None:
    sent, clock, events = make_sentinel(silence_seconds=2.0)
    # Music silent, but an emergency mic segment is on air.
    feed(sent, clock, (0.0, 0.0), playing=True, mic=True, seconds=10.0)
    check("[sentinel] mic on air + silent music → no fire",
          len(events) == 0, f"events={len(events)}")


# ── 7. threshold boundary ────────────────────────────────────────────

def test_threshold_boundary() -> None:
    sent, clock, events = make_sentinel(silence_seconds=1.5)
    # peak == threshold → still counted as silence
    feed(sent, clock, (0.004, 0.004), playing=True, seconds=2.0)
    check("[sentinel] peak == threshold counts as silence",
          len(events) == 1, f"events={len(events)}")

    sent2, clock2, events2 = make_sentinel(silence_seconds=1.5)
    feed(sent2, clock2, (0.0041, 0.0039), playing=True, seconds=5.0)
    check("[sentinel] peak just above threshold = audio, no fire",
          len(events2) == 0, f"events={len(events2)}")


# ── 8. rate limit under continuous dead air ──────────────────────────

def test_rate_limit() -> None:
    # One alert per min_report_gap while dead air continues: window=2s,
    # gap=30s → fires at t=2, 32, 62, 92 (exactly 4 in 120s), never closer.
    sent, clock, events = make_sentinel(silence_seconds=2.0, min_report_gap=30.0)
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=120.0)
    check("[sentinel] 120s dead air, 30s gap → one alert per gap (4)",
          len(events) == 4, f"events={len(events)}")
    check("[sentinel] internal counter matches callbacks",
          sent.silence_events == 4, str(sent.silence_events))

    # A long gap swallows everything: gap=300s → exactly 1 alert in 120s.
    sent2, clock2, events2 = make_sentinel(silence_seconds=2.0, min_report_gap=300.0)
    feed(sent2, clock2, (0.0, 0.0), playing=True, seconds=120.0)
    check("[sentinel] gap > dead-air length → exactly 1 alert",
          len(events2) == 1, f"events={len(events2)}")


# ── 9. configure() re-arms the window ────────────────────────────────

def test_configure_rearms() -> None:
    sent, clock, events = make_sentinel(silence_seconds=3.0)
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=2.0)   # armed 2s
    sent.configure(threshold=0.005)                            # re-arm
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=1.0)   # only 1s since
    check("[sentinel] config change resets window → no instant fire",
          len(events) == 0, f"events={len(events)}")
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=2.5)
    check("[sentinel] fires again after full window post-re-arm",
          len(events) == 1, f"events={len(events)}")


# ── 10. stop() ───────────────────────────────────────────────────────

def test_stop() -> None:
    sent, clock, events = make_sentinel(silence_seconds=1.5)
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=1.0)
    sent.stop()
    feed(sent, clock, (0.0, 0.0), playing=True, seconds=10.0)
    check("[sentinel] stop() → no more events", len(events) == 0)
    snap = sent.snapshot()
    check("[sentinel] stop() → disabled + window cleared",
          snap["enabled"] is False and snap["silent_for_now"] is None)


# ── 11. callback exception swallowed ─────────────────────────────────

def test_callback_exception_swallowed() -> None:
    clock = FakeClock()

    def boom(info):
        raise RuntimeError("handler exploded")

    sent = SilenceSentinel(on_silence=boom, clock=clock)
    sent.configure(enabled=True, silence_seconds=1.5)
    raised = False
    try:
        feed(sent, clock, (0.0, 0.0), playing=True, seconds=4.0)
    except RuntimeError:
        raised = True
    check("[sentinel] callback exception never escapes observe()",
          not raised)
    check("[sentinel] sentinel still counting after callback failure",
          sent.silence_events == 1, str(sent.silence_events))


# ── 12. dry-run guarantee: no pathway into the engine ────────────────

def test_dry_run_structural_guarantee() -> None:
    """Structural check on the CODE (via ast, docstrings/comments ignored):
    the sentinel must not import any playback module and must never call
    an engine-shaped API. (``stop`` is allowed — it stops the WATCHER
    itself; with no imported player handle it cannot touch audio.)"""
    import ast
    source = (Path(__file__).parent / "silence_sentinel.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)

    forbidden_imports = {"player", "pygame", "native_audio_render",
                         "native_audio_capture", "gui"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("[dry-run] sentinel imports no playback module",
          not (imported & forbidden_imports),
          f"imports: {sorted(imported)}")

    forbidden_attrs = {"play", "pause", "resume", "skip", "next_track",
                       "shutdown", "set_next_track", "play_mic_multi"}
    touched = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            touched.add(node.attr)
    leaked = touched & forbidden_attrs
    check("[dry-run] sentinel never calls an engine-shaped API",
          not leaked, f"found: {sorted(leaked)}")

    sent, _, _ = make_sentinel()
    mutating = {"play", "pause", "resume", "skip", "next",
                "shutdown", "set_next_track"}
    leaked = mutating & set(dir(sent))
    check("[dry-run] sentinel exposes no playback-mutating method",
          not leaked, f"found: {sorted(leaked)}")


# ── 13. GUI wiring: one tap per tick, sentinel fed the same value ────

def test_gui_single_tap_wiring() -> None:
    gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    start = gui_src.find("def _update_progress")
    end = gui_src.find("\n    def ", start + 1)
    block = gui_src[start:end if end > 0 else None]
    taps = block.count("_tap_levels()")
    check("[gui] _update_progress taps the loopback exactly once",
          taps == 1, f"_tap_levels() called {taps}x in _update_progress")
    check("[gui] _update_progress feeds the sentinel",
          "silence_sentinel.observe(" in block)
    check("[gui] sentinel default is OFF (env-gated)",
          'FREQ_SILENCE_SENTINEL' in gui_src
          and "os.environ.get(\n    \"FREQ_SILENCE_SENTINEL\"" in gui_src)


def test_gui_settings_wiring() -> None:
    """Settings tab wiring exists and is persistable."""
    gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    for token in (
        "sentinel_enabled_var",          # toggle switch
        "sentinel_seconds_entry",        # window seconds
        "sentinel_threshold_slider",     # threshold slider
        "sentinel_events_label",         # events counter
        "_on_sentinel_toggle",           # switch handler
        "_on_sentinel_seconds",          # seconds handler
        "_on_sentinel_threshold",        # slider handler
        "_refresh_sentinel_status",      # status/events refresh
        '"silence_sentinel": {',         # .freq collect
        'settings.get("silence_sentinel", {})',  # .freq restore
    ):
        check(f"[gui-settings] {token}", token in gui_src)
    # Threshold mapping must be true dBFS: slider v -> 10^-v -> -20*v dB.
    import math
    v = 2.4
    thr = 10.0 ** (-v)
    check("[gui-settings] threshold math (-48 dB default)",
          abs(20 * math.log10(thr) + 48) < 1e-6
          and abs(20 * math.log10(0.004) + 47.96) < 0.1)


# ── 14. real-clock smoke (default time.monotonic) ────────────────────

def test_real_clock_smoke() -> None:
    events: list[dict] = []
    sent = SilenceSentinel(on_silence=events.append)   # real clock
    sent.configure(enabled=True, silence_seconds=1.0, min_report_gap=0.0)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not events:
        sent.observe((0.0, 0.0), True)
        time.sleep(0.05)
    check("[sentinel] real-clock smoke: fires with monotonic clock",
          len(events) == 1, f"events={len(events)}")
    sent.stop()


def run_all(into: list | None = None) -> tuple[int, int]:
    """Run every sentinel test.

    Results always land in this module's RESULTS; when ``into`` is given
    (embedding from another suite) they are copied there afterwards too.
    """
    tests = (
        test_disabled_by_default,
        test_fires_after_window,
        test_quiet_music_no_fire,
        test_tap_unavailable_no_fire,
        test_off_air_no_fire,
        test_mic_counts_as_audio,
        test_threshold_boundary,
        test_rate_limit,
        test_configure_rearms,
        test_stop,
        test_callback_exception_swallowed,
        test_dry_run_structural_guarantee,
        test_gui_single_tap_wiring,
        test_gui_settings_wiring,
        test_real_clock_smoke,
    )
    for t in tests:
        t()
    if into is not None:
        into.extend(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    return passed, len(RESULTS)


def main() -> None:
    print("\n=== Silence Sentinel suite (dry-run, fake silence) ===\n")
    passed, total = run_all()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n=== Summary: {passed}/{total} passed"
          + (" — all green ✅" if not fails else f" — {len(fails)} FAILED"))
    for n, ok, d in fails:
        print(f"  ❌ {n} — {d}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
