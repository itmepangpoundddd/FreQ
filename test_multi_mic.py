#!/usr/bin/env python3
"""Multi-mic (up to 4) stability tests.

Regression scope: old USB webcam mics crashed the whole process inside
PortAudio's DLL (0xC0000005 in libportaudio64bit.dll). Microphones now
travel exclusively through the native WASAPI engine — these tests trip
a hard alarm if any code path touches sounddevice for capture, and
hammer the 4-mic path with the problem devices still plugged in.
"""
from __future__ import annotations

import sys
import time
import traceback

import native_audio_capture as nac
import native_audio_render as nar
import mic_recorder

# Keep ✅/❌ output intact when stdout is redirected on a non-UTF-8 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        PASS.append(name)
        print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
    except Exception:
        FAIL.append(name)
        print(f"  ❌ {name}")
        traceback.print_exc()


def trip_portaudio():
    """Make any sounddevice capture attempt explode loudly."""
    try:
        import sounddevice as sd
        def _boom(*a, **k):
            raise AssertionError("PORTAUDIO TOUCHED A MICROPHONE")
        sd.rec = _boom
        sd.InputStream = _boom
        sd.query_devices = _boom
    except ImportError:
        pass


trip_portaudio()
mics = nac.enum_microphones()
print(f"capture endpoints on this machine: {[m[1] for m in mics]}")
problem = next((m for m in mics if "USB2.0 MIC" in str(m[1])), None)
print(f"problem webcam mic present: {bool(problem)}\n")


# ────────────────────────────────────────────────────────────
# 1. Engine-level: four real endpoints at once, storm stop/start
# ────────────────────────────────────────────────────────────
def quad_engine_open():
    r = nar.render_new()
    nar.render_start(r, None)
    eps = [m[0] for m in mics[:4]] or [None]
    specs = [(e or None, 1.0 + i * 0.25) for i, e in enumerate(eps)]
    for ep, g in specs:
        nar.render_emergency_mic(r, ep, g)
    n = nar.render_emergency_mic_count(r)
    nar.render_emergency_mic_stop(r)
    time.sleep(0.2)
    assert n == len(specs), f"expected {len(specs)} live mics, got {n}"
    assert nar.render_emergency_mic_count(r) == 0, "mics survived stop()"
    return f"{n} mics mixed with distinct gains"


def quad_storm():
    r = nar.render_new()
    nar.render_start(r, None)
    eps = [m[0] for m in mics[:4]] or [None]
    for cycle in range(40):
        for ep in eps:
            nar.render_emergency_mic(r, ep, 1.0)
        assert nar.render_emergency_mic_count(r) == len(eps), f"cycle {cycle}"
        nar.render_emergency_mic_stop(r)
        assert nar.render_emergency_mic_count(r) == 0, f"cycle {cycle} leftover"
    return "40 quad start/stop cycles clean"


def capacity_guard():
    r = nar.render_new()
    nar.render_start(r, None)
    eps = [m[0] for m in mics[:4]] or [None] * 4
    for ep in eps:
        nar.render_emergency_mic(r, ep, 1.0)
    before = nar.render_emergency_mic_count(r)
    try:
        nar.render_emergency_mic(r, None, 1.0)   # a 5th must be refused
    except Exception:
        pass   # refusing via exception is also correct
    after = nar.render_emergency_mic_count(r)
    nar.render_emergency_mic_stop(r)
    assert before == 4 and after == 4, (before, after)
    return "5th mic refused at the engine"


def problem_mic_all_slots():
    if not problem:
        return "no problem mic on this machine — skipped"
    ep = problem[0]
    r = nar.render_new()
    nar.render_start(r, None)
    for _ in range(4):
        nar.render_emergency_mic(r, ep, 1.0)
    assert nar.render_emergency_mic_count(r) == 4
    nar.render_emergency_mic_stop(r)
    assert nar.render_emergency_mic_count(r) == 0
    return "problem webcam mic in all 4 slots, no crash"


def gains_bounded():
    r = nar.render_new()
    nar.render_start(r, None)
    for gain in (0.0, -1.0, 99.0, 2.5):
        try:
            nar.render_emergency_mic(r, None, gain)
        except Exception:
            pass
        nar.render_emergency_mic_stop(r)
    nar.render_emergency_mic_stop(r)
    return "extreme gains never crash (clamped or refused)"


print("— Engine level —")
check("quad mic open/stop with per-mic gains", quad_engine_open)
check("quad start/stop storm ×40", quad_storm)
check("capacity guard (max 4)", capacity_guard)
check("problem webcam mic ×4 slots", problem_mic_all_slots)
check("extreme gain values safe", gains_bounded)


# ────────────────────────────────────────────────────────────
# 2. Player level: transport, timeline, single finish, joins
# ────────────────────────────────────────────────────────────
def player_quad_segment():
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.use_wasapi_engine("")
    eps = [m[0] for m in mics[:4]] or [None]
    specs = [(e or None, 1.0 + 0.1 * i) for i, e in enumerate(eps)]
    finished = []
    t0 = time.time()
    opened = p.play_mic_multi(1.2, specs, duck_factor=0.3,
                              on_finish=lambda: finished.append(1))
    dt = time.time() - t0
    assert opened == len(specs), opened
    positions = []
    for _ in range(6):
        time.sleep(0.2)
        positions.append(round(p.get_position(), 2))
    p.stop()
    assert not finished, "on_finish fired on stop() — must only fire at clip end"
    assert p.mic_count() == 0
    time.sleep(1.4)
    assert len(finished) == 1, f"finish callbacks: {len(finished)}"
    assert not p.is_playing
    assert positions[-1] > positions[0], "timeline frozen"
    return f"×{opened} mics, timeline {positions[0]}→{positions[-1]}s, finish ×1 ({dt:.2f}s)"


def player_pygame_music_mic_live():
    """Music on pygame (default engine) - mic segment must STILL be live."""
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.init() and p._engine == "pygame"
    fired = []
    eps = [m[0] for m in mics[:2]] or [None] * 2
    opened = p.play_mic_multi(3.0, [(e or None, 1.0) for e in eps],
                              duck_factor=0.3,
                              on_finish=lambda: fired.append(1))
    assert opened == len(eps), f"live mic refused on pygame engine ({opened})"
    positions = []
    for _ in range(4):
        time.sleep(0.2)
        positions.append(p.get_position())
    # Let the clip finish NATURALLY (no stop — a transport stop is
    # supposed to cancel the pending finish callback).
    time.sleep(2.7)   # clip end + timer wake-up margin
    assert len(fired) == 1, f"finish fired {len(fired)}x — natural end must fire it"
    assert not p.is_playing, "transport not cleared after the clip ended"
    assert positions[-1] > positions[0], "timeline frozen on pygame path"
    assert p.mic_count() == 0
    p.stop()
    return f"pygame music + {opened} live mics, timeline OK, finish x1"


def player_pygame_engine_reuse():
    """The dedicated mic engine is created once and reused, never leaked."""
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.init()
    for _ in range(10):
        assert p.play_mic_multi(0.05, [(None, 1.0)]) == 1
        p.stop()
        time.sleep(0.02)
    assert p._mic_render is not None, "mic engine lost between sessions"
    p.shutdown()
    assert p._mic_render is None, "mic engine leaked on shutdown"
    return "engine reused across 10 sessions, retired on shutdown"


def player_join_session():
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.use_wasapi_engine("")
    eps = [m[0] for m in mics[:3]] or [None] * 3
    finished = []
    first = p.play_mic_multi(1.0, [(eps[0] or None, 1.0)], duck_factor=0.3,
                             on_finish=lambda: finished.append("first"))
    # The second SEGMENT supersedes the first (its own callback rides in).
    second = p.play_mic_multi(1.0, [(e or None, 1.5) for e in eps[1:]],
                              duck_factor=0.3,
                              on_finish=lambda: finished.append("second"))
    assert first == 1 and second == 2, (first, second)
    time.sleep(1.7)
    assert finished == ["second"], f"finishes: {finished} — old timer must not fire"
    assert p.mic_count() == 0
    return "supersede: only the newest session's finish fired"


def player_supersede_capacity():
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.use_wasapi_engine("")
    eps = [m[0] for m in mics[:4]] or [None] * 4
    assert p.play_mic_multi(0.2, [(e or None, 1.0) for e in eps]) == 4
    # A fresh session retires the old mics first — capacity is always
    # available through the session API (the hard 4-ceiling itself is
    # engine-level and covered above).
    assert p.play_mic_multi(0.2, [(e or None, 1.0) for e in reversed(eps)]) == 4
    p.stop()
    assert p.mic_count() == 0
    return "supersede always frees capacity (4 -> 4)"


def record_problem_mic():
    if not problem:
        return "no problem mic on this machine — skipped"
    idx = mics.index(problem)
    ok = mic_recorder.record_wav("_mm_test.wav", 1.0, device_index=idx)
    import os
    if os.path.exists("_mm_test.wav"):
        os.remove("_mm_test.wav")
    assert ok, "problem mic failed through the native path"
    return "recorded via native engine, portaudio untouched"


def record_silent_fail_fast():
    ok = mic_recorder.record_wav("_mm_none.wav", 1.0, endpoint_id="{DEAD-BEEF}")
    import os
    if os.path.exists("_mm_none.wav"):
        os.remove("_mm_none.wav")
    assert not ok
    return "bogus endpoint fails gracefully (no crash, no file)"


print("\n— Player level —")
check("quad segment timeline + single finish", player_quad_segment)
check("pygame music + live mic", player_pygame_music_mic_live)
check("mic engine reuse/shutdown", player_pygame_engine_reuse)
check("join session keeps one timer", player_join_session)
check("player supersede capacity", player_supersede_capacity)
check("problem mic records natively", record_problem_mic)
check("bogus endpoint handled", record_silent_fail_fast)


# ────────────────────────────────────────────────────────────
# 3. Mixed storm: quad mics + song + jingle + stop cycling
# ────────────────────────────────────────────────────────────
def mixed_storm():
    import tempfile, random
    from pathlib import Path
    from player import AudioPlayer
    p = AudioPlayer()
    assert p.use_wasapi_engine("")
    eps = [m[0] for m in mics[:4]] or [None] * 4
    specs = [(e or None, 1.0 + 0.1 * i) for i, e in enumerate(eps)]
    wav = Path(tempfile.gettempdir()) / "freq_mm_storm.wav"
    import wave as _w
    if not wav.exists():
        with _w.open(str(wav), "wb") as f:
            f.setnchannels(2); f.setsampwidth(2); f.setframerate(44100)
            f.writeframes(b"\x00\x00" * 44100 * 2 * 5)
    failures = 0
    for cycle in range(25):
        op = random.choice(["song", "mics", "both", "stop"])
        try:
            if op in ("song", "both"):
                p.play_file(str(wav))
            if op in ("mics", "both"):
                assert p.play_mic_multi(0.15, specs, duck_factor=0.3) >= 1
            if op == "stop":
                p.stop()
        except Exception:
            failures += 1
        if cycle % 5 == 4:
            p.stop()
            time.sleep(0.05)
    p.stop()
    assert failures == 0, f"{failures} failed ops"
    assert p.mic_count() == 0
    return "25 mixed cycles (song/mics/both/stop) zero failures"


print("\n— Mixed storm —")
check("song + quad mic + stop ×25", mixed_storm)

print("\n" + "=" * 50)
print(f"Results: {len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
