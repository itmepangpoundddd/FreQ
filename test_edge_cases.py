#!/usr/bin/env python3
"""Edge-case tests for the Crossfade + WASAPI engines.

Covers scenarios the original E2E tests did not:
  1. Track B shorter than the fade window
  2. Single-track queue (repeat-one style: handoff re-queues same song)
  3. Pause/resume around the crossfade trigger (position must freeze)
  4. Seek after arm — stale mix must be invalidated, no stale handoff
  5. stop() while a crossfade is pending — no handoff, engine fully reset
  6. Re-arm race: set_next_track called twice → only the last track arms
  7. Native pause semantics (WASAPI): emitted freezes, ring intact, resume

Run:  python test_edge_cases.py            (WASAPI engine)
      python test_edge_cases.py --pygame   (pygame engine)
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import player as player_mod  # noqa: E402
from player import AudioPlayer  # noqa: E402

SR = 44100
TMP = Path(tempfile.mkdtemp(prefix="freq_edge_"))
RESULTS: list[tuple[str, bool, str]] = []


def ffmpeg() -> str:
    return player_mod._ffmpeg_exe()


def make_song(name: str, seconds: float) -> str:
    """Smooth-sine MP3 fixture (pure tone = music, no silent head for auto-cue).

    Tests run at volume 0 — nothing here should ever be audible.
    """
    wav = TMP / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    frames = int(seconds * SR)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        # Continuous 220 Hz sine — no discontinuities (a sawtooth-ish ramp
        # clicks at every step and sounds like static through the speakers).
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / SR)))
            for i in range(frames)
        ))
    r = subprocess.run(
        [ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-q:a", "2", str(mp3)],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="ignore")
    return str(mp3)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def wait_arm(p: AudioPlayer, timeout: float = 8.0) -> bool:
    """Pump the trigger until a crossfade is armed/prepared."""
    deadline = time.time() + timeout
    while time.time() < deadline and not (p._xf_armed or p._xf_prepared):
        p._check_crossfade_trigger()
        time.sleep(0.1)
    return bool(p._xf_armed or p._xf_prepared)


def make_player() -> AudioPlayer:
    p = AudioPlayer()
    assert p.init(), "pygame mixer init failed"
    p.volume = 0.0            # tests must be SILENT (assertions are counters)
    p.crossfade_enabled = True  # _check_crossfade_trigger requires the flag
    return p


def wasapi(p: AudioPlayer) -> bool:
    try:
        import native_audio_render  # noqa: F401
    except ImportError:
        return False
    return p.use_wasapi_engine("")


# ─── 1. B shorter than the fade window ───────────────────────
def test_b_shorter_than_fade(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] B shorter than fade (2s B, 4s fade)"
    a = make_song("shortA", 6.0)
    b = make_song("shortB", 2.0)
    p.play_file(a, duration=6.0)
    time.sleep(0.4)
    p.set_next_track(b, duration=2.0, index=1)
    if not wait_arm(p):
        check(name, False, "never armed")
        p.stop()
        return
    if engine == "wasapi":
        # Fade ramp must end with B, not keep fading past B's audio.
        fade_s = p._xf_fade_total / SR
        check(name + " — fade clamped to B", fade_s <= 2.1,
              f"fade={fade_s:.2f}s")
    else:
        mix = p._xf_mixed_path
        ok = bool(mix) and os.path.isfile(mix)
        check(name + " — mix file exists", ok)
        if ok:
            r = subprocess.run(
                [player_mod._ffprobe_exe(), "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", mix],
                capture_output=True, text=True)
            dur = float(r.stdout.strip() or 0)
            check(name + " — mix duration sane", 2.0 <= dur <= 9.0,
                  f"mix={dur:.2f}s")
        check(name + " — pending duration non-negative",
              p._xf_pending_duration >= 0.0,
              f"pending={p._xf_pending_duration:.2f}s")
    p.stop()


# ─── 2. Single-track queue (repeat-one style handoff) ────────
def test_single_track_queue(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] single-track queue"
    a = make_song("singleA", 5.0)
    fired: list = []
    p.play_file(a, duration=5.0)
    time.sleep(0.4)

    def handoff(off, idx):
        fired.append((off, idx))
        p.clear_next_track()  # GUI re-queues the same song

    p.set_next_track(a, duration=5.0, index=0, handoff=handoff)
    if not wait_arm(p):
        check(name + " — armed", False, "never armed")
        p.stop()
        return
    deadline = time.time() + 10
    while time.time() < deadline and not fired:
        time.sleep(0.1)
    check(name + " — handoff fired exactly once", len(fired) == 1,
          f"fired={len(fired)}")
    check(name + " — state clean after handoff",
          not p._xf_armed and not p._xf_active and p._xf_pending_path is None)
    p.stop()


# ─── 3. Pause/resume around the trigger ──────────────────────
def test_pause_around_trigger(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] pause/resume around trigger"
    a = make_song("pauseA", 8.0)
    b = make_song("pauseB", 6.0)
    p.play_file(a, duration=8.0)
    time.sleep(0.5)
    p.set_next_track(b, duration=6.0, index=1)
    # Pause BEFORE the trigger window (remaining ≈ 7.5 s > fade + 1.5).
    p.pause()
    time.sleep(0.6)
    pos_at_pause = p.get_position()
    time.sleep(0.6)
    frozen = abs(p.get_position() - pos_at_pause) < 0.05
    check(name + " — position frozen while paused", frozen,
          f"pos {pos_at_pause:.2f}s → {p.get_position():.2f}s")
    p._check_crossfade_trigger()
    check(name + " — no render while paused", not p._xf_preloading)
    p.resume()
    time.sleep(0.3)
    check(name + " — resume continues", p.is_playing and not p.is_paused)
    p.stop()


# ─── 4. Seek after arm — stale mix invalidated ───────────────
def test_seek_after_arm(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] seek after arm invalidates mix"
    a = make_song("seekA", 8.0)
    b = make_song("seekB", 6.0)
    fired: list = []
    p.play_file(a, duration=8.0)
    time.sleep(0.4)
    p.set_next_track(b, duration=6.0, index=1,
                     handoff=lambda off, idx: fired.append(1))
    if not wait_arm(p):
        check(name + " — armed first", False, "never armed")
        p.stop()
        return
    p.seek(1.0)  # jump backward — queued mix is now stale
    time.sleep(0.5)
    stale = p._xf_armed or p._xf_prepared or p._xf_active
    check(name + " — crossfade state cleared after seek", not stale)
    deadline = time.time() + 3
    while time.time() < deadline and not fired:
        time.sleep(0.1)
    check(name + " — no stale handoff", not fired, f"fired={len(fired)}")
    p.stop()


# ─── 5. stop() while a crossfade is pending ──────────────────
def test_stop_during_pending(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] stop during pending crossfade"
    a = make_song("stopA", 8.0)
    b = make_song("stopB", 6.0)
    fired: list = []
    p.play_file(a, duration=8.0)
    time.sleep(0.4)
    p.set_next_track(b, duration=6.0, index=1,
                     handoff=lambda off, idx: fired.append(1))
    p._check_crossfade_trigger()
    p.stop()
    time.sleep(0.4)
    check(name + " — no handoff after stop", not fired)
    check(name + " — engine fully reset",
          not p._is_playing and not p._xf_armed and not p._xf_active
          and p._xf_pending_path is None)
    if engine == "wasapi":
        import native_audio_render as nar
        check(name + " — ring cleared", nar.render_buffered(p._render) == 0)


# ─── 6. Re-arm race: set_next_track twice ────────────────────
def test_rearm_race(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] set_next_track twice (re-arm)"
    a = make_song("raceA", 8.0)
    b1 = make_song("raceB1", 6.0)
    b2 = make_song("raceB2", 6.0)
    p.play_file(a, duration=8.0)
    time.sleep(0.4)
    p.set_next_track(b1, duration=6.0, index=1)
    p.set_next_track(b2, duration=6.0, index=2)
    if not wait_arm(p):
        check(name, False, "never armed")
        p.stop()
        return
    armed_to = os.path.basename(p._xf_b_source or p._xf_pending_path or "")
    check(name + " — armed to the LAST queued track",
          os.path.basename(b2) in armed_to, f"armed={armed_to}")
    p.stop()


# ─── 7. Native pause semantics (WASAPI only) ─────────────────
def test_native_pause_semantics() -> None:
    name = "[native] render_pause semantics"
    try:
        import native_audio_render as nar
    except ImportError:
        check(name, False, "native module missing")
        return
    eng = nar.render_new()
    nar.render_start(eng, None)
    nar.render_set_volume(eng, 0.0)   # silent — counters are what we assert
    # 0.5 s of a quiet 220 Hz sine (AC — a DC block thumps the speakers).
    tone = b"".join(
        struct.pack("<hh",
                    int(3000 * math.sin(2 * math.pi * 220 * i / SR)),
                    int(3000 * math.sin(2 * math.pi * 220 * i / SR)))
        for i in range(SR // 2)
    )
    accepted = nar.render_write(eng, tone)
    # Unit regression: buffered must count FRAMES (bytes/4), not samples.
    check(name + " — buffered counts frames",
          nar.render_buffered(eng) == SR // 2,
          f"accepted={accepted} buffered={nar.render_buffered(eng)}")
    time.sleep(0.35)
    e1 = nar.render_emitted(eng)
    nar.render_pause(eng, 1)
    time.sleep(0.5)
    e2 = nar.render_emitted(eng)
    check(name + " — emitted frozen while paused", e1 == e2,
          f"emitted {e1} → {e2}")
    ring = nar.render_buffered(eng)
    check(name + " — ring intact during pause", ring > 0,
          f"buffered={ring} frames")
    nar.render_pause(eng, 0)
    time.sleep(0.4)
    e3 = nar.render_emitted(eng)
    check(name + " — resume continues", e3 > e2, f"emitted {e2} → {e3}")
    nar.render_stop(eng)


def main() -> None:
    pygame_mode = "--pygame" in sys.argv
    p = make_player()
    engine = "pygame" if pygame_mode else ("wasapi" if wasapi(p) else "pygame")
    print(f"\n=== Edge-case suite — engine: {engine} ===\n")

    if engine == "wasapi":
        test_native_pause_semantics()
    test_b_shorter_than_fade(p, engine)
    test_single_track_queue(p, engine)
    test_pause_around_trigger(p, engine)
    test_seek_after_arm(p, engine)
    test_stop_during_pending(p, engine)
    test_rearm_race(p, engine)

    p.shutdown()
    print(f"\n=== Summary: {engine} ===")
    fails = [r for r in RESULTS if not r[1]]
    for n, ok, d in fails:
        print(f"  ❌ {n} — {d}")
    print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} passed"
          + (" — all green ✅" if not fails else f" — {len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
