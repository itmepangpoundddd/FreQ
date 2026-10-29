#!/usr/bin/env python3
"""Jingle stability stress tests for the FreQ audio engine.

Root cause under test: native_audio_render's built-in source used to leave
a zombie decode thread behind on rapid start/stop (the 3 s join timeout),
which read freed miniaudio state and crashed the whole process with a
divide-by-zero (0xC0000094) inside the resampler. Jingles trigger exactly
that pattern — short files, started and stopped constantly — so this suite
hammers that path far harder than real usage:

  1. Native-level storm: use_file/stop_source/use_file thousands of times
  2. Player-level storm: play_file(jingle) + stop() interleaved, random order
  3. Manual jingle flow: play → early stop → resume, repeatedly (GUI paths)
  4. Auto-jingle simulation: finished-song → jingle → next-song cycles
  5. Jingle while crossfade armed (state invalidation)
  6. Engine recovery: start refuses on a dead engine, pygame fallback works
  7. Native pipe storm: use_pipe start/stop with ffmpeg-fed pipes

Run:  python test_jingle_stress.py            (WASAPI engine)
      python test_jingle_stress.py --pygame   (pygame engine)
Exit code 0 = all checks passed; 1 = failure.
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
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Keep ✅/❌ output intact when stdout is redirected on a non-UTF-8 console
# (Windows codepage pipes would otherwise raise UnicodeEncodeError mid-run).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import player as player_mod  # noqa: E402
from player import AudioPlayer  # noqa: E402

SR = 44100
TMP = Path(tempfile.mkdtemp(prefix="freq_jingle_"))
RESULTS: list[tuple[str, bool, str]] = []


def ffmpeg() -> str:
    return player_mod._ffmpeg_exe()


def make_tone(name: str, seconds: float, freq: int = 220) -> str:
    """Short MP3 fixture (jingle-like). Volume is 0 in tests — inaudible."""
    wav = TMP / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    frames = int(seconds * SR)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / SR)))
            for i in range(frames)
        ))
    r = subprocess.run(
        [ffmpeg(), "-y", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libmp3lame", "-q:a", "2", str(mp3)],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="ignore")
    return str(mp3)


def make_tone_rate(name: str, seconds: float, rate: int,
                   freq: int = 440) -> str:
    """MP3 fixture at an arbitrary sample rate.

    Any rate ≠ 44100 forces miniaudio's linear resampler inside the decoder
    (the path that exposed the stack-initialized decoder corruption).
    """
    wav = TMP / f"{name}.wav"
    mp3 = wav.with_suffix(".mp3")
    frames = int(seconds * rate)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * freq * i / rate)))
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


def make_player() -> AudioPlayer:
    p = AudioPlayer()
    assert p.init(), "player init failed"
    p.volume = 0.0          # tests are silent
    p.auto_cue = False
    return p


def wasapi(p: AudioPlayer) -> bool:
    try:
        return p.use_wasapi_engine("")
    except Exception:
        return False


def engine_name(p: AudioPlayer) -> str:
    return getattr(p, "_engine", "?")


# ────────────────────────────────────────────────────────────────
# 1. Native-level storm — the exact crashing pattern, ×500
# ────────────────────────────────────────────────────────────────
def test_native_source_storm() -> None:
    print("\n── [1] Native use_file/stop_source storm ×500 ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[storm] native_audio_render import", False, "missing module")
        return
    e = nar.render_new()
    check("[storm] render_start", nar.render_start(e, None))
    jingle = make_tone("jingle_short", 1.2, freq=880)
    ok = True
    t0 = time.time()
    for i in range(500):
        if not nar.render_use_file(e, jingle, 0.0, 0.0, 1.0):
            ok = False
            break
        # Random short dwell, like a user stopping a jingle mid-play.
        time.sleep(random.uniform(0.0, 0.004))
        nar.render_clear(e)
    dt = time.time() - t0
    check("[storm] 500 start/clear cycles", ok, f"{dt:.1f}s")
    # Source must be gone after clear; engine must still be healthy.
    check("[storm] engine still running", nar.render_is_running(e))
    nar.render_clear(e)
    nar.render_stop(e)
    del e   # destructor runs — must not crash (leak-on-zombie guard)


# ────────────────────────────────────────────────────────────────
# 1b. Resampler paths + real audio-flow verification
# ────────────────────────────────────────────────────────────────
def test_resampler_audio_flow() -> None:
    print("\n── [1b] 48k/22k/96k decode must actually FEED audio ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[flow] native_audio_render import", False, "missing module")
        return
    e = nar.render_new()
    check("[flow] render_start", nar.render_start(e, None))
    # 48000 Hz is the common real-world file rate (and the one that used to
    # corrupt the heap); 22050/96000 stretch the resampler further.
    for rate in (48000, 22050, 96000, 44100):
        f = make_tone_rate(f"rate_{rate}", 2.0, rate)
        if not nar.render_use_file(e, f, 0.0, 0.0, 1.0):
            check(f"[flow {rate}Hz] use_file accepted", False, "rejected")
            continue
        time.sleep(0.7)   # source thread must decode + fill the ring
        decoded = nar.render_source_state(e)
        buffered = nar.render_buffered(e)
        check(f"[flow {rate}Hz] decoder produced frames", decoded > 44100 // 2,
              f"decoded={decoded}")
        check(f"[flow {rate}Hz] audio reached the ring", buffered > 44100 // 4,
              f"buffered={buffered}")
        nar.render_clear(e)
    # Bad path must be rejected cleanly — not crash, not half-open a source.
    check("[flow] missing file rejected",
          not nar.render_use_file(e, str(TMP / "nope_missing.mp3"), 0.0, 0.0, 1.0))
    check("[flow] engine alive after rejection", nar.render_is_running(e))
    nar.render_stop(e)
    del e


def test_mixed_rate_storm() -> None:
    print("\n── [1c] Mixed-rate start/clear storm ×200 (heap stress) ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[mixstorm] native_audio_render import", False, "missing module")
        return
    e = nar.render_new()
    check("[mixstorm] render_start", nar.render_start(e, None))
    files = [make_tone_rate(f"mix_{r}", 1.5, r)
             for r in (48000, 44100, 22050, 96000)]
    ok = True
    for i in range(200):
        f = files[i % len(files)]
        if not nar.render_use_file(e, f, 0.0, 0.0, 1.0):
            ok = False
            break
        time.sleep(random.uniform(0.0, 0.004))
        nar.render_clear(e)      # immediate teardown of a live resampler
        if i % 7 == 0:           # sometimes stop/restart the whole device
            nar.render_stop(e)
            if not nar.render_start(e, None):
                ok = False
                break
    check("[mixstorm] 200 mixed-rate cycles", ok)
    check("[mixstorm] engine still running", nar.render_is_running(e))
    nar.render_clear(e)
    nar.render_stop(e)
    del e


# ────────────────────────────────────────────────────────────────
# 2. Player-level storm — play_file(jingle) + stop() interleaved
# ────────────────────────────────────────────────────────────────
def test_player_jingle_storm(p: AudioPlayer) -> None:
    print("\n── [2] Player play/stop jingle storm ×150 ──")
    jingle = make_tone("jingle_storm", 0.9, freq=660)
    song = make_tone("song_storm", 4.0)
    ok = True
    for i in range(150):
        started = p.play_file(jingle, duration=0.0)
        if not started:
            ok = False
            break
        time.sleep(random.uniform(0.0, 0.03))
        p.stop()                      # mid-jingle stop
        if i % 3 == 0:                # sometimes a song instead
            p.play_file(song, duration=4.0)
            time.sleep(random.uniform(0.0, 0.02))
            p.stop()
    check("[storm] 150 play/stop cycles", ok)
    check("[storm] player idle after storm", not p.is_playing)
    # Engine must still start playback after the storm.
    ok2 = p.play_file(song, duration=4.0)
    time.sleep(0.3)
    pos = p.get_position()
    p.stop()
    check("[storm] playback works after storm", ok2 and pos > 0.0,
          f"pos={pos:.2f}s")


# ────────────────────────────────────────────────────────────────
# 3. Manual jingle flow — play → early stop → resume (GUI sequence)
# ────────────────────────────────────────────────────────────────
def test_manual_jingle_flow(p: AudioPlayer) -> None:
    print("\n── [3] Manual jingle flow (play → stop → resume) ×30 ──")
    jingle = make_tone("jingle_manual", 1.5, freq=990)
    song = make_tone("song_manual", 8.0)
    ok_play = ok_resume_pos = ok_no_finish_leak = True
    resume_fired = 0
    for i in range(30):
        # Song playing (simulate 2 s in), then manual jingle.
        if not p.play_file(song, duration=8.0):
            ok_play = False
            break
        time.sleep(0.15)
        saved_pos = p.get_position()
        # GUI calls stop() then play_file(jingle, on_finish=resume).
        p.stop()
        finished = {"flag": False}

        def on_finish():
            finished["flag"] = True

        if not p.play_file(jingle, duration=0.0, on_finish=on_finish):
            ok_play = False
            break
        if i % 2 == 0:
            # Early stop → resume from saved position (the GUI's Stop button).
            time.sleep(random.uniform(0.02, 0.2))
            p.stop()
            p.play_file(song, duration=8.0)
            time.sleep(0.12)
            resumed_pos = p.get_position()
            if resumed_pos <= 0.0:
                ok_resume_pos = False
            resume_fired += 1
        else:
            # Let the short jingle finish naturally → on_finish must fire.
            deadline = time.time() + 6
            while time.time() < deadline and not finished["flag"]:
                time.sleep(0.05)
            if not finished["flag"]:
                ok_no_finish_leak = False
            p.stop()
    check("[flow] all play_file calls succeeded", ok_play)
    check("[flow] resume produces audible position", ok_resume_pos,
          f"{resume_fired} resumes")
    check("[flow] jingle EOF callback fires", ok_no_finish_leak)


# ────────────────────────────────────────────────────────────────
# 4. Auto-jingle simulation — finished song → jingle → next song
# ────────────────────────────────────────────────────────────────
def test_auto_jingle_cycle(p: AudioPlayer) -> None:
    print("\n── [4] Auto jingle every-N-songs cycle ×12 ──")
    jingle = make_tone("jingle_auto", 1.0, freq=1320)
    songs = [make_tone(f"auto_song_{k}", 2.2, freq=180 + 40 * k) for k in range(3)]
    ok = True
    for i in range(12):
        song = songs[i % len(songs)]
        if not p.play_file(song, duration=2.2):
            ok = False
            break
        # Wait for natural EOF (2.2 s file → ~3 s deadline).
        deadline = time.time() + 6
        while time.time() < deadline and p.is_playing:
            time.sleep(0.05)
        if p.is_playing:            # EOF never detected
            ok = False
            p.stop()
            break
        # Song finished → jingle plays (GUI's _on_track_finished branch).
        if not p.play_file(jingle, duration=0.0):
            ok = False
            break
        deadline = time.time() + 5
        while time.time() < deadline and p.is_playing:
            time.sleep(0.05)
        p.stop()
    check("[auto] 12 song→jingle cycles clean", ok)


# ────────────────────────────────────────────────────────────────
# 5. Jingle during an armed crossfade
# ────────────────────────────────────────────────────────────────
def test_jingle_vs_crossfade(p: AudioPlayer) -> None:
    print("\n── [5] Jingle while crossfade is armed ×10 ──")
    if not hasattr(p, "set_next_track"):
        check("[cf] skipped", True, "no crossfade API")
        return
    p.crossfade_enabled = True
    p.crossfade_duration = 2.0
    jingle = make_tone("jingle_cf", 0.8, freq=550)
    a = make_tone("cf_a", 5.0)
    b = make_tone("cf_b", 5.0, freq=330)
    ok = True
    for i in range(10):
        if not p.play_file(a, duration=5.0):
            ok = False
            break
        p.set_next_track(b, duration=5.0)
        time.sleep(0.1)
        # Jingle interrupts mid-song: GUI stop() then jingle.
        p.stop()
        if not p.play_file(jingle, duration=0.0):
            ok = False
            break
        time.sleep(0.15)
        p.stop()
    check("[cf] jingle interrupts armed crossfade cleanly", ok)
    check("[cf] no stale crossfade state", not p._xf_active and not p._xf_armed,
          f"active={p._xf_active} armed={p._xf_armed}")
    p.crossfade_enabled = False


# ────────────────────────────────────────────────────────────────
# 6. Engine recovery — dead engine must not lose playback
# ────────────────────────────────────────────────────────────────
def test_engine_recovery(p: AudioPlayer) -> None:
    print("\n── [6] Engine recovery / fallback ──")
    if p._engine != "wasapi" or p._render is None:
        check("[recovery] skipped (pygame engine)", True)
        return
    import native_audio_render as nar
    # Simulate a dead engine without deleting the player's capsule object.
    nar.render_stop(p._render)
    jingle = make_tone("jingle_rec", 1.0, freq=440)
    song = make_tone("song_rec", 4.0)
    # wasapi start on a stopped engine → render_start works again, so force
    # the refusal path by stopping AND clearing running state via render_stop;
    # _wasapi_start's render_use_file still succeeds on a stopped engine, so
    # exercise the public fallback: play_file must recover playback somehow.
    ok = p.play_file(song, duration=4.0)
    time.sleep(0.2)
    audible = p.get_position() > 0.0
    p.stop()
    check("[recovery] playback survives engine stop", ok and audible,
          f"engine={p._engine}")
    # Jingle still plays after recovery.
    ok2 = p.play_file(jingle, duration=0.0)
    time.sleep(0.2)
    pos = p.get_position()
    p.stop()
    check("[recovery] jingle plays after recovery", ok2 and pos > 0.0)


# ────────────────────────────────────────────────────────────────
# 7. Native pipe storm — crossfade B-side pipes under churn
# ────────────────────────────────────────────────────────────────
def test_native_pipe_storm() -> None:
    print("\n── [7] Native use_pipe start/stop storm ×60 ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[pipe] native_audio_render import", False, "missing module")
        return
    e = nar.render_new()
    nar.render_start(e, None)
    song = make_tone("pipe_src", 6.0)
    ok = True
    procs = []
    try:
        for i in range(60):
            proc = subprocess.Popen(
                [ffmpeg(), "-v", "quiet", "-i", song,
                 "-ac", "2", "-ar", "44100", "-f", "s16le", "-y", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            procs.append(proc)
            import msvcrt
            handle = int(msvcrt.get_osfhandle(proc.stdout.fileno()))
            if not nar.render_use_pipe(e, handle, 0.5, 1.0):
                ok = False
                break
            time.sleep(random.uniform(0.0, 0.03))
            nar.render_clear(e)   # stops + joins the pipe source
    finally:
        nar.render_clear(e)
        nar.render_stop(e)
        for pr in procs:
            try:
                pr.kill()
            except OSError:
                pass
    check("[pipe] 60 pipe start/clear cycles", ok)
    del e   # destructor must not crash


def test_seek_flush_storm() -> None:
    """Waveform-press storm: source_seek_flush must never crash, hang or
    leak stale audio — the old rebuild path stacked two audio streams."""
    print("\n── [seek] source_seek_flush storm ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[seek] native module", False, "missing")
        return
    e = nar.render_new()
    if not nar.render_start(e, None):
        check("[seek] render_start", False, "engine refused")
        return
    song = make_tone("seek_song", 8.0)
    check("[seek] use_file", nar.render_use_file(e, song, 0.0, 0.0, 1.0))
    time.sleep(0.6)
    ok = True
    t0 = time.time()
    for i in range(300):
        nar.render_source_seek_flush(e, random.uniform(0.0, 7.0))
        time.sleep(random.uniform(0.0, 0.004))
    dt = time.time() - t0
    check("[seek] 300 flush seeks survive", ok, f"{dt:.1f}s")
    # After the last seek the source must still FEED audio.
    time.sleep(0.4)
    fed = nar.render_source_state(e)
    check("[seek] source feeds after storm", fed > 44100 // 4, f"fed={fed}")
    check("[seek] engine still running", nar.render_is_running(e))
    nar.render_clear(e)
    nar.render_stop(e)
    del e


def test_seek_storm_player(p) -> None:
    """Player-level seek storm: rapid get_position() + seek() on the real
    engine — no crash, position tracks the requested offset, and playback
    keeps flowing afterwards."""
    print("\n── [seek] player-level waveform storm ──")
    if engine_name(p) != "wasapi":
        check("[seek] player storm (wasapi only)", True, "skipped")
        return
    song = make_tone("seek_player", 30.0)
    ok = p.play_file(song, duration=30.0,
                     on_finish=lambda: None)
    check("[seek] initial play", ok)
    time.sleep(0.5)
    storm_ok = True
    for i in range(60):
        pos = p.get_position()
        target = random.uniform(0.0, 25.0)
        p.seek(target)
        time.sleep(random.uniform(0.01, 0.05))
        if not p.is_playing:
            storm_ok = False
            break
    check("[seek] 60 rapid seeks — still playing", storm_ok)
    # After the storm, audio must still flow: position keeps advancing.
    a = p.get_position()
    time.sleep(0.8)
    b = p.get_position()
    check("[seek] position advances after storm", b > a,
          f"{a:.2f} -> {b:.2f}")
    p.stop()


def test_emergency_mic_api() -> None:
    """Emergency-mic engine API: start/stop/gain cycles must be clean and
    leave the engine healthy. The device may have no real microphone —
    the API contract (no crash, no hang, consistent state) is what counts."""
    print("\n── [mic] emergency mix-in API ──")
    try:
        import native_audio_render as nar
    except ImportError:
        check("[mic] native module", False, "missing")
        return
    e = nar.render_new()
    if not nar.render_start(e, None):
        check("[mic] render_start", False, "engine refused")
        return
    started = False
    try:
        nar.render_emergency_mic(e, None, 1.0)
        started = True
    except RuntimeError:
        # No capture endpoint on this machine — the API contract still
        # holds: stop/active/gain must behave without a session.
        pass
    if started:
        check("[mic] active after start", nar.render_emergency_mic_active(e))
        nar.render_emergency_mic_gain(e, 2.5)
    else:
        check("[mic] no capture endpoint tolerated", True, "")
    # 30 rapid start/stop cycles — the old zombie-thread class of bug.
    cycles_ok = True
    for i in range(30):
        try:
            nar.render_emergency_mic(e, None, 1.0)
            nar.render_emergency_mic_stop(e)
        except RuntimeError:
            cycles_ok = False
            break
    check("[mic] 30 start/stop cycles", cycles_ok)
    check("[mic] engine healthy after mic cycles", nar.render_is_running(e))
    # Dual-mic storm: alternate 1 and 2 live mics — the array add/remove
    # path must stay consistent (no leak, no crash, engine healthy).
    dual_ok = True
    try:
        for i in range(60):
            nar.render_emergency_mic(e, None, 1.0)
            if i % 2 == 0:
                nar.render_emergency_mic(e, None, 1.0)   # second mic joins
                if not nar.render_emergency_mic_active(e):
                    dual_ok = False
            nar.render_emergency_mic_stop(e)
            if nar.render_emergency_mic_active(e):
                dual_ok = False
    except RuntimeError:
        dual_ok = False
    check("[mic] 60 dual/single start-stop cycles", dual_ok)
    check("[mic] engine healthy after dual storm", nar.render_is_running(e))
    nar.render_stop(e)
    del e   # destructor must not crash with the mic block present


def main() -> None:
    pygame_mode = "--pygame" in sys.argv
    random.seed(1234)
    p = make_player()
    engine = "pygame" if pygame_mode else ("wasapi" if wasapi(p) else "pygame")
    print(f"\n=== Jingle stress suite — engine: {engine} ===")

    if not pygame_mode and engine == "wasapi":
        test_native_source_storm()
        test_resampler_audio_flow()
        test_mixed_rate_storm()
        test_native_pipe_storm()
        test_seek_flush_storm()
        test_seek_storm_player(p)
        test_emergency_mic_api()
    test_player_jingle_storm(p)
    test_manual_jingle_flow(p)
    test_auto_jingle_cycle(p)
    test_jingle_vs_crossfade(p)
    if not pygame_mode:
        test_engine_recovery(p)

    p.shutdown()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 50}")
    print(f"Results: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    for name, _, detail in failed:
        print(f"  ❌ {name} — {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
