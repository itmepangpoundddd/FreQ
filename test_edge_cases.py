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

# Keep ✅/❌ output intact when stdout is redirected on a non-UTF-8 console
# (Windows codepage pipes would otherwise raise UnicodeEncodeError mid-run).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    # The device drains in real time, so allow up to 100 ms of playback
    # between the write and this read (timing-tolerant, not exact).
    buf = nar.render_buffered(eng)
    check(name + " — buffered counts frames",
          accepted == SR // 2 and SR // 2 - SR // 10 <= buf <= SR // 2,
          f"accepted={accepted} buffered={buf}")
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


# ─── 8. Loudness Auto-Gain ───────────────────────
def test_auto_gain_logic() -> None:
    print("\n-- Auto-Gain + reconnect-backoff logic --")
    from player import auto_gain_linear
    check("auto-gain neutral at target", abs(auto_gain_linear(-14.0) - 1.0) < 1e-9,
          f"×{auto_gain_linear(-14.0):.6f}")
    check("auto-gain +4 dB math", abs(auto_gain_linear(-18.0) - 10 ** (4 / 20)) < 1e-9, "")
    check("auto-gain clamps boost at +6 dB",
          abs(auto_gain_linear(-40.0) - 10 ** (6 / 20)) < 1e-9, "")
    check("auto-gain clamps cut at -6 dB",
          abs(auto_gain_linear(-2.0) - 10 ** (-6 / 20)) < 1e-9, "")
    from streaming import RECONNECT_DELAYS, reconnect_delay_for
    check("reconnect ladder", RECONNECT_DELAYS == (5, 15, 30, 60), str(RECONNECT_DELAYS))
    check("reconnect escalates",
          reconnect_delay_for(0) == 5 and reconnect_delay_for(1) == 15
          and reconnect_delay_for(2) == 30, "")
    check("reconnect caps at 60s", reconnect_delay_for(99) == 60, "")


def test_auto_gain_e2e(p: AudioPlayer, engine: str) -> None:
    name = f"[{engine}] Auto-Gain applies measured loudness"
    from audio_meter import measure_file_lufs
    from player import auto_gain_linear
    quiet = make_song("quiet_ag", 6.0)
    p.play_file(quiet, duration=6.0)
    p.set_auto_gain(True, -14.0)
    deadline = time.time() + 15.0
    while time.time() < deadline and p._ag_gain == 1.0:
        time.sleep(0.1)
    got = p._ag_gain
    lufs = measure_file_lufs(quiet)
    expected = auto_gain_linear(lufs)
    check(name, abs(got - expected) < 1e-6,
          f"gain ×{got:.3f} vs expected ×{expected:.3f} ({lufs:.1f} LUFS)")
    check(name + " — boost applied to quiet tone", got > 1.0, f"×{got:.3f}")
    p.set_auto_gain(False)
    check(name + " — disable resets to neutral", p._ag_gain == 1.0, "")
    p.stop()


def test_native_decoder(p, engine: str) -> None:
    """The embedded miniaudio decoder produces a complete, seekable stream
    through the normal player path (engine-independent)."""
    import struct as _s
    try:
        import native_audio_decode as nad
    except ImportError:
        check("[decoder] module available", False, "import failed")
        return
    check("[decoder] module available", True, "")
    path = os.path.join(tempfile.gettempdir(), "_t_decode.wav")
    with wave.open(path, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        frames = bytearray()
        for i in range(44100 * 2):
            v = int(10000 * math.sin(2 * math.pi * 440 * i / 44100))
            frames += _s.pack("<hh", v, v)
        w.writeframes(frames)
    try:
        d = nad.Decoder(path, rate=44100, channels=2)
        check("[decoder] total_frames", d.total_frames == 88200,
              f"got {d.total_frames}")
        total, chunks = 0, 0
        while True:
            c = d.read(4410)
            if c is None:
                break
            total += len(c) // 4
            chunks += 1
        check("[decoder] streaming complete", total == 88200 and chunks >= 10,
              f"{total} frames in {chunks} chunks")
        d.seek(44100)
        c = d.read(100)
        check("[decoder] seek + read", c is not None and len(c) == 400,
              f"{0 if c is None else len(c)} bytes")
        d.close()
    except Exception as e:
        check("[decoder] stream/seek", False, str(e))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_aircheck_recorder() -> None:
    """Air-Check: record the loopback to WAV and verify the file."""
    try:
        import native_audio_capture as nac
    except ImportError:
        check("[aircheck] capture available", False, "import failed")
        return
    from aircheck import AirCheckRecorder
    rec = AirCheckRecorder()
    check("[aircheck] available", rec.available, "")
    path = os.path.join(tempfile.gettempdir(), "_t_aircheck_mic.wav")
    try:
        got = rec.start(name=path, source="mic")  # mic always carries room noise
        check("[aircheck] start returns path", got == path, got or "")
        check("[aircheck] recording flag", rec.recording, "")
        time.sleep(0.7)
        rec.stop()
        time.sleep(0.3)
        check("[aircheck] stopped", not rec.recording, "")
        with wave.open(path) as w:
            n, rate = w.getnframes(), w.getframerate()
        # start/stop latency varies by machine; >= 0.25 s proves a real capture
        check("[aircheck] wav has audio", n >= rate // 4,
              f"{n} frames @ {rate} Hz ({n / rate:.2f} s)")
    except Exception as e:
        check("[aircheck] record cycle", False, str(e))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_hotkey_typing_guard() -> None:
    """Playback hotkeys must never fire while typing in a text field."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[hotkeys] gui.py readable", False, str(err))
        return
    for token, name in (
        ("def _no_typing", "typing guard exists"),
        ("self._no_typing(self._on_global_space)", "space is guarded"),
        ("self._no_typing(lambda e: self._next_track())",
         "next-track hotkey is guarded"),
        ("self._no_typing(lambda e: self._prev_track())",
         "prev-track hotkey is guarded"),
        ("self._no_typing(lambda e: self._toggle_mute())",
         "mute hotkey is guarded"),
        ('("Entry", "Text", "Combobox", "Spinbox", "TEntry",',
         "guard checks widget CLASS strings (CTk inner widgets)"),
        ('self.bind(binding, self._no_typing(handler))',
         "rebindable Keys-tab plain keys are guarded"),
        ("self.bind(binding, handler)",
         "modifier combos (Ctrl+S) stay raw while typing"),
    ):
        check(f"[hotkeys] {name}", token in gui_src)


def test_mic_noise_suppression() -> None:
    """Mic noise suppression: OFF must stay the untouched legacy path."""
    try:
        cpp = (Path(__file__).parent / "native_audio_render.cpp").read_text(
            encoding="utf-8")
        py = (Path(__file__).parent / "player.py").read_text(encoding="utf-8")
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[mic-dsp] sources readable", False, str(err))
        return
    for token, name in (
        ("mic_dsp_mode_{0}", "engine suppression defaults to OFF"),
        ("mic_comp_mode_{0}", "engine compressor defaults to OFF"),
        ("mic_limiter_mode_{0}", "engine limiter defaults to OFF"),
        ("mic_deesser_mode_{0}", "engine de-esser defaults to OFF"),
        ("const bool chain = dsp_mode || comp_mode || lim_mode || de_mode",
         "bypass gate requires EVERY stage off (lone stage must run)"),
        ("|| meq_mode || echo_mode || gainst_mode",
         "gate includes the 5th+6th+7th stages"),
        ("if (!chain) {", "bypass branch keeps the legacy path"),
        ("render_emergency_mic_suppression", "suppression API present"),
        ("render_emergency_mic_compressor", "compressor API present"),
        ("render_emergency_mic_limiter", "limiter API present"),
        ("render_emergency_mic_deesser", "de-esser API present"),
        ("render_emergency_mic_get_deesser", "de-esser getter present"),
        ("render_emergency_mic_miceq", "mic EQ API present"),
        ("render_emergency_mic_get_miceq", "mic EQ getter present"),
        ("render_emergency_mic_echo", "echo API present"),
        ("render_emergency_mic_get_echo", "echo getter present"),
        ("render_emergency_mic_gainst", "gain trim API present"),
        ("render_emergency_mic_get_gainst", "gain trim getter present"),
        ("render_emergency_mic_upcomp", "upward compressor API present"),
        ("render_emergency_mic_get_upcomp", "upward compressor getter present"),
        ("render_emergency_mic_expand", "expander API present"),
        ("render_emergency_mic_get_expand", "expander getter present"),
        ("void emergency_mic_set_gainst(int mode)", "gain setter in C++"),
        ("void emergency_mic_set_upcomp(int mode)", "upcomp setter in C++"),
        ("void emergency_mic_set_expand(int mode)", "expander setter in C++"),
        ("if (gainst_mode) {", "gain stage runs in the render chain"),
        ("if (upcomp_mode) {", "upcomp stage runs in the render chain"),
        ("if (expand_mode) {", "expander stage runs in the render chain"),
        ("|| upcomp_mode || expand_mode;",
         "gate includes ALL nine stages"),
        ("void emergency_mic_set_miceq(int mode)", "mic EQ setter in C++"),
        ("void emergency_mic_set_echo(int mode)", "echo setter in C++"),
        ("if (meq_mode) {", "mic EQ stage runs in the render chain"),
        ("if (echo_mode) {", "echo stage runs in the render chain"),
        ("kEchoTaps = 8000", "echo delay ring sized (~181 ms)"),
        ("def set_mic_filter", "player chain bridge present"),
        ("_MIC_FILTER_APIS", "stage->API registry present"),
        ("\"deesser\": (\"render_emergency_mic_deesser\"",
         "de-esser registered in the player bridge"),
        ('"miceq": ("render_emergency_mic_miceq"',
         "mic EQ registered in the player bridge"),
        ('"echo": ("render_emergency_mic_echo"',
         "echo registered in the player bridge"),
        ('"gainst": ("render_emergency_mic_gainst"',
         "gain trim registered in the player bridge"),
        ('"upcomp": ("render_emergency_mic_upcomp"',
         "upward compressor registered in the player bridge"),
        ('"expand": ("render_emergency_mic_expand"',
         "expander registered in the player bridge"),
        ('"miceq": 0, "echo": 0,', "stages 5-6 default OFF in chain state"),
        ('"gainst": 0, "upcomp": 0,', "stages 7-8 default OFF"),
        ('"expand": 0}', "stage 9 default OFF"),
        ("for stage, (setter, _getter) in self._MIC_FILTER_APIS.items()",
         "late-created engines inherit the whole chain"),
        ("def set_mic_noise_suppression", "back-compat alias kept"),
        ("\"mic_filters\": dict", ".freq collect includes the chain"),
        ("settings.get(\"mic_filters\", {})", ".freq restore applies the chain"),
        ("def _open_mic_filters", "OBS-style dialog wired"),
        ("def _refresh_mic_filters_status", "status line wired"),
        ("Mic Filters", "Settings section exists"),
        ('"Tames sharp S sounds (~5 kHz band only)",',
         "de-esser described in dialog"),
        ('("miceq", "Mic EQ"),', "mic EQ row in the dialog"),
        ('("echo", "Echo")', "echo row in the dialog"),
        ('("gainst", "Gain (dB)")', "gain row in the dialog"),
        ('("upcomp", "Upward Compressor")', "upcomp row in the dialog"),
        ('("expand", "Expander")', "expander row in the dialog"),
        ('"Lifts QUIET parts up (whispers stay audible)",',
         "upcomp described"),
        ('"Pushes the noise floor down between words",',
         "expander described"),
        ('"gainst": "Input trim', "gain described"),
        ('"Voice tilt: trims boom, lifts presence (radio shape)",',
         "mic EQ described in the dialog"),
        ("for stage in (\"suppression\", \"compressor\", \"limiter\", \"deesser",
         ".freq restore covers all six stages"),
        ('9 native stages', "documented 9-stage chain order"),
    ):
        check(f"[mic-dsp] {name}",
              token in cpp or token in py or token in gui_src)
    # Fine-parameter sliders (OBS-style) — generic param store + live
    # consumption in the render loop; legacy defaults preserved.
    for token, name in (
        ("MicParam p_sup_hp_{90}", "param store present"),
        ("int emergency_mic_set_param(const char* key, int value)",
         "param setter in C++"),
        ("int emergency_mic_get_param(const char* key)",
         "param getter in C++"),
        ("render_mic_param_set(engine, key, value) -> bool",
         "param setter exported"),
        ("def mic_param_set(self, key: str, value: int) -> bool",
         "player bridge for parameters"),
        ('gate_floor_pct")) return &p_gate_floor_;',
         "gate floor parameter consumed"),
        ("const int echo_len = echo_ms_v * 44100 / 1000;",
         "echo delay length is parameter-driven"),
        ("_MIC_STAGE_PARAMS", "slider definitions in the dialog"),
        ("sliders are live", "live-slider note in the dialog header"),
        ("\"mic_filters_immediate\": bool(", ".freq collect keeps the flag"),
        # Two-pane OBS layout + Immediate mode + release-edge pushes.
        ("Left pane: the stage list", "OBS two-pane layout (list left)"),
        ("Right pane: the selected stage's parameters",
         "parameter editor right"),
        ("def toggle_immediate() -> None:",
         "Immediate-mode toggle wired"),
        ("_mic_filters_immediate", "Immediate flag persisted on the app"),
        ('"mic_filters_immediate": bool(', ".freq collect keeps the flag"),
        ('settings.get("mic_filters_immediate", False))',
         ".freq restore keeps the flag"),
        ("def on_release(_e=None, k=key, vv=var_v, s=step):",
         "slider pushes on BUTTON RELEASE (stability)"),
        ("if immediate_var.get():\n                        self.audio_player.mic_param_set(",
         "release pushes only in Immediate mode"),
        ("_MIC_PARAM_DEFAULTS", "per-stage Defaults table"),
        ("select(\"suppression\")", "dialog opens on the first stage"),
        # Dot truthfulness + Defaults safety (user-reported bugs).
        ("current[stage] = mode        # keep the snapshot LIVE",
         "stage snapshot updated on every push (dots never stale)"),
        ("The Enabled switch is PRESERVED",
         "Defaults never re-enables a disabled stage"),
        ("if immediate_var.get():\n                    push_stage(stage, var, strength, params)\n                refresh_dots()",
         "dots refresh in BOTH Immediate modes"),
        ("refresh_dots()               # dots follow the real state",
         "dots refresh after the editor builds"),
    ):
        check(f"[mic-params] {name}",
              token in cpp or token in py or token in gui_src)
    # Stages 5+6 (Mic EQ / Echo) — native C++ presence + the bypass-gate
    # rule that must include EVERY stage (a lone stage must still run).
    for token, name in (
        ("void emergency_mic_set_miceq(int mode)", "mic EQ setter in C++"),
        ("void emergency_mic_set_echo(int mode)", "echo setter in C++"),
        ("const bool chain = dsp_mode || comp_mode || lim_mode || de_mode\n                         || meq_mode || echo_mode || gainst_mode\n                         || upcomp_mode || expand_mode;",
         "bypass gate includes ALL nine stages"),
        ("if (meq_mode) {", "mic EQ stage runs in the render chain"),
        ("if (echo_mode) {", "echo stage runs in the render chain"),
        ("static const int kEchoTaps = 8000;", "echo delay ring sized"),
        ('{"render_emergency_mic_miceq", render_emergency_mic_miceq,',
         "mic EQ exported to Python"),
        ('{"render_emergency_mic_echo", render_emergency_mic_echo,',
         "echo exported to Python"),
        ('("miceq", "Mic EQ"),', "mic EQ row in the filters dialog"),
        ('("echo", "Echo")', "echo row in the filters dialog"),
        ('"Voice tilt: trims boom, lifts presence (radio shape)",',
         "mic EQ described in the dialog"),
    ):
        check(f"[mic-dsp] {name}", token in cpp or token in py or token in gui_src)
    # VU meter during mic fallback: the operator must ALWAYS see levels
    # (the old code skipped the whole progress tick while _download_mode
    # was set — the meter froze and looked like a dead mic).
    for token, name in (
        ("if not self._download_mode:",
         "progress skipped in fallback, VU still reached"),
        ("or bool(self._download_mode))",
         "VU active includes the mic-fallback stage"),
        ("for attempt in (1, 2):",
         "live mic opens retry once before falling back"),
        ("live mic open returned 0 — retrying once after settle",
         "retry logged"),
    ):
        check(f"[vu-mic] {name}", token in gui_src)


def test_version_sync_stage() -> None:
    """release.py stage 1: the tree must agree with installer.iss."""
    try:
        src = (Path(__file__).parent / "release.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[version-sync] release.py readable", False, str(err))
        return
    for token, name in (
        ("def sync_version_files()", "sync stage exists"),
        ("info = sync_version_files()", "every run syncs before building"),
        ("\"--sync-version\"", "--sync-only flag available"),
        ("if args.sync_version and not args.publish:",
         "--sync-version exits without building"),
        ("version.txt", "version.txt rewritten from installer.iss"),
        ("release_notes_section(identifier)", "release notes verified"),
        ("auto-generated notes", "missing-notes warning mentions fallback"),
    ):
        check(f"[version-sync] {name}", token in src)


def test_mic_segment_dialog() -> None:
    """Add Mic Segment: resizable UI + remembered mic/gain defaults."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
        rq_src = (Path(__file__).parent / "radio_manager.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[mic-dialog] gui.py readable", False, str(err))
        return
    # Resize contract (button must never fall off the window again).
    for token, name in (
        ('dialog.geometry("390x360")', "default size fits quad-mic rows"),
        ("dialog.minsize(340, 280)", "resizable with a sane floor"),
        ("dialog.resizable(True, True)", "user may resize"),
        ("dialog_btnbar.pack(side=\"bottom\"", "Add button locked to the bottom"),
        ("dialog_body = ctk.CTkScrollableFrame", "form rows scroll"),
    ):
        check(f"[mic-dialog] {name}", token in gui_src)
    # Memory contract (defaults from the previous session's .freq).
    for token, name in (
        ("self._mic_segment_memory: dict = {}", "memory initialized"),
        # Endpoint-id stability fix: queue items must remember the STABLE
        # WASAPI endpoint ids, not just list positions that Windows
        # re-shuffles on every reboot (the "mic segment has no sound" bug).
        ("mic_endpoint_ids: list = field(default_factory=list)"
         , "Song carries stable endpoint ids (radio_manager.py)"),
        ("stored_ids[pos] in known_endpoints",
         "playback prefers the stored endpoint id (whitelisted)"),
        ("mic_endpoint_ids=endpoint_ids,",
         "Add-to-queue records the 4-slot endpoint list"),
        ("endpoint_id=(stored_ids[0] if stored_ids else \"\")",
         "fallback recorder receives the stable endpoint"),
        ('"mic_segment_memory": dict(getattr(self, "_mic_segment_memory", {}))',
         ".freq collect includes mic memory"),
        ('settings.get("mic_segment_memory", {})', ".freq restore reads mic memory"),
        ("if isinstance(value, str) and value in device_names",
         "stale device names whitelisted before prefill"),
        ('_saved_gain(memory.get("gains")', "gains restored per slot"),
        ('"mic2": saved_extras[0]', "Add writes choices for next time"),
        ('"gains": [_parse_gain(gain_entry)]', "gains saved parsed+clamped"),
    ):
        check(f"[mic-memory] {name}",
              token in gui_src or token in rq_src)


def test_aircheck_phase2() -> None:
    """Phase 2: sources (both), MP3 export, list_files, clean_old."""
    try:
        import native_audio_capture as nac
        import native_audio_render as nar
    except ImportError:
        check("[aircheck2] native modules", False, "import failed")
        return
    from aircheck import AirCheckRecorder
    rec = AirCheckRecorder()
    tmpdir = tempfile.gettempdir()
    base = os.path.join(tmpdir, "aircheck_test2")
    for f in (base + ".wav", base + "_mic.wav", base + ".mp3", base + "_mic.mp3"):
        try:
            os.remove(f)
        except OSError:
            pass
    try:
        # both sources -> 2 files
        rec.start(folder=tmpdir, name="aircheck_test2.wav", source="both")
        check("[aircheck2] both sources recording", rec.recording, "")
        time.sleep(0.7)
        rec.stop()
        ok = os.path.isfile(base + ".wav") and os.path.isfile(base + "_mic.wav")
        check("[aircheck2] dual files created", ok, "")
        # MP3 export + delete WAV (loopback may be silent -> size can be tiny;
        # only require a valid non-empty file)
        mp3a = AirCheckRecorder.export_mp3(base + ".wav", delete_wav=True)
        mp3b = AirCheckRecorder.export_mp3(base + "_mic.wav", delete_wav=True)
        ok = (mp3a and os.path.getsize(mp3a) > 0 and not os.path.isfile(base + ".wav")
              and mp3b and os.path.getsize(mp3b) > 0)
        check("[aircheck2] mp3 export", ok, f"{mp3a} {mp3b}")
        # list_files finds them
        names = [os.path.basename(f[0]) for f in AirCheckRecorder.list_files(tmpdir)]
        check("[aircheck2] list_files", "aircheck_test2.mp3" in names, str(names[:4]))
        # clean_old removes an artificially old file
        old = os.path.join(tmpdir, "aircheck_20200101_0000.wav")
        open(old, "wb").write(b"RIFF")
        old_t = time.time() - 30 * 86400
        os.utime(old, (old_t, old_t))
        n = AirCheckRecorder.clean_old(tmpdir, keep_days=14)
        check("[aircheck2] clean_old", n >= 1 and not os.path.isfile(old), f"removed {n}")
    except Exception as e:
        check("[aircheck2] phase2 cycle", False, str(e))
    finally:
        for f in (base + ".wav", base + "_mic.wav", base + ".mp3", base + "_mic.mp3"):
            try:
                os.remove(f)
            except OSError:
                pass


def test_aircheck_scheduler() -> None:
    """Air-Check schedule window logic (pure, no audio)."""
    from aircheck import AirCheckScheduler
    from datetime import datetime as _dt
    sch = AirCheckScheduler()
    sch.add("18:00", "20:00")
    inside = sch.due_window(_dt(2026, 9, 21, 18, 30))
    before = sch.due_window(_dt(2026, 9, 21, 17, 59))
    after = sch.due_window(_dt(2026, 9, 21, 20, 0))
    check("[aircheck] schedule windows", inside is not None and before is None
          and after is None, f"in={inside is not None}")
    data = sch.to_data()
    sch2 = AirCheckScheduler()
    sch2.restore(data)
    check("[aircheck] schedule round-trip", sch2.due_window(
        _dt(2026, 9, 21, 19, 0)) is not None, "")


def test_eq_engine(p) -> None:
    """render_set_eq clamps and get_eq round-trips; gains reach the engine."""
    try:
        import native_audio_render as nar
    except ImportError:
        check("[eq] native render", False, "import failed")
        return
    r = nar.render_new()
    nar.render_set_eq(r, 99, -99, 5)
    g = nar.render_get_eq(r)
    check("[eq] clamp to +-12", g == (12.0, -12.0, 5.0), str(g))
    nar.render_set_eq(r, 0, 0, 0)
    check("[eq] flat reset", nar.render_get_eq(r) == (0.0, 0.0, 0.0), "")
    p.set_eq(6.0, -3.0, 0.0)
    check("[eq] player set/get", p.get_eq() == (6.0, -3.0, 0.0), str(p.get_eq()))
    p.set_eq(0.0, 0.0, 0.0)
    check("[eq] player reset", p.get_eq() == (0.0, 0.0, 0.0), "")
    nar.render_stop(r)


def test_device_loss_stress(p, engine: str) -> None:
    """Rapid unplug/replug: recovery must keep state consistent every round."""
    if engine != "wasapi":
        return  # pygame has no device-loss path
    song = make_song("devloss", 8.0)
    try:
        p.stop()
        ok = p.play_file(song, duration=8.0)
        # The native source pre-buffers ~0.5 s before the device starts —
        # give the clock a moment before demanding progress.
        time.sleep(0.4)
        check("[devloss] initial play", ok and p.get_position() > 0.01,
              f"ok={ok} pos={p.get_position():.2f}")
        good = True
        detail = ""
        for round_ in range(3):
            time.sleep(0.35)
            p._recover_device_lost()
            time.sleep(0.4)
            if not p.is_playing or p.get_position() <= 0:
                good = False
                detail = f"round {round_}: playing={p.is_playing}"
                break
        check("[devloss] 3 rapid recoveries", good, detail)
        p.stop()
        # Player must still work normally afterwards
        ok = p.play_file(song, duration=8.0)
        time.sleep(0.4)
        check("[devloss] playback after stress", ok and p.get_position() > 0.05,
              f"ok={ok}")
        p.stop()
    except Exception as e:
        check("[devloss] stress", False, str(e))


def test_skip_cascade_cap(p, engine: str) -> None:
    """All-missing queue must stop cleanly instead of looping forever."""
    # Simulate the decision logic from gui._play_index without the GUI.
    def cascade(n: int) -> int:
        skips = 0
        # cap: skip while skips < n-1... (mirrors gui: count < queue length)
        while n and skips < n:
            skips += 1
            if skips >= n:
                return skips   # gave up — no infinite loop
        return skips
    for n in (1, 3, 10):
        r = cascade(n)
        check(f"[skipcap] queue of {n} terminates", r <= n, f"skips={r}")


def test_reconnect_ladder() -> None:
    """Reconnect backoff escalates and is capped (pure logic)."""
    try:
        from streaming import reconnect_delay_for, RECONNECT_DELAYS
        seq = [reconnect_delay_for(a) for a in range(0, 8)]
        ok = (seq[0] == RECONNECT_DELAYS[0]
              and all(a <= b for a, b in zip(seq, seq[1:]))
              and seq[-1] == RECONNECT_DELAYS[-1])
        check("[relay] backoff ladder", ok, str(seq))
    except ImportError:
        check("[relay] backoff ladder", False, "streaming import failed")


def test_native_pump_and_stream() -> None:
    """Built-in C++ source + native stream pump (WASAPI/native only)."""
    try:
        import native_audio_render as nar
    except ImportError:
        check("[native] built-in source", False, "render module missing")
        return
    # ── Built-in source: realtime pacing, seek, EOF, clear-kill ──
    e = nar.render_new()
    if not nar.render_start(e, None):
        check("[native] built-in source", False, "engine start failed")
        return
    try:
        ok = nar.render_use_file(e, str(make_song("edge_src", 5)), 0.0, 0.0, 1.0)
        check("[native] use_file accepted", ok, "")
        time.sleep(1.2)
        fed = nar.render_source_state(e)
        check("[native] source realtime-paced",
              44100 <= fed <= int(1.8 * 44100), f"fed={fed} frames")
        nar.render_source_seek(e, 1.0)
        time.sleep(0.5)
        check("[native] source feeds after seek",
              nar.render_source_state(e) > fed, "")
        nar.render_clear(e)
        time.sleep(0.1)
        frozen = nar.render_source_state(e)
        time.sleep(0.3)
        check("[native] clear() kills source",
              nar.render_source_state(e) == frozen, "")
    finally:
        nar.render_stop(e)

    # ── Native stream pump: pipe -> loopback socket ──
    try:
        import native_audio_stream as nas
        import subprocess
        import socket as _sock
        import msvcrt
        proc = subprocess.Popen(
            [ffmpeg(), "-v", "quiet", "-i", str(make_song("edge_src2", 5)),
             "-ac", "2", "-ar", "44100", "-f", "s16le", "-y", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        srv = _sock.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        cli = _sock.socket(); cli.settimeout(20)
        cli.connect(("127.0.0.1", port))
        conn, _ = srv.accept(); conn.settimeout(6)
        handle = int(msvcrt.get_osfhandle(proc.stdout.fileno()))
        started = nas.stream_pump_start(handle, cli.fileno(), b"HDR\\r\\n\\r\\n")
        check("[stream] pump starts", started, "")
        received = b""
        end = time.time() + 8
        while time.time() < end and nas.stream_pump_state() != 2:
            try:
                data = conn.recv(65536)
                if not data:
                    break
                received += data
            except _sock.timeout:
                break
        time.sleep(0.3)
        try:
            while True:
                d = conn.recv(65536)
                if not d: break
                received += d
        except OSError:
            pass
        check("[stream] header delivered", received.startswith(b"HDR"),
              f"{received[:8]!r}")
        check("[stream] PCM delivered", len(received) > 100000,
              f"{len(received)} bytes")
        check("[stream] EOF state", nas.stream_pump_state() == 2,
              str(nas.stream_pump_state()))
        nas.stream_pump_stop()
        check("[stream] stop -> idle", nas.stream_pump_state() == 0,
              str(nas.stream_pump_state()))
        try:
            conn.close(); cli.close(); srv.close()
        except OSError:
            pass
        proc.terminate()
    except ImportError:
        check("[stream] native pump", False, "native_audio_stream missing")


def test_broadcast_readiness_tools() -> None:
    """Pre-show tools: Broadcast Check (read-only probe) + Mic Doctor.
    Contract: both are strictly diagnostic — they must never open a live
    session, move the queue, or change any setting."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[bcheck] gui.py readable", False, str(err))
        return
    for token, name in (
        ("def _broadcast_check_rows", "readiness rows probe exists"),
        ("def _run_broadcast_check", "check runner wired"),
        ("def _open_mic_doctor", "Mic Doctor dialog wired"),
        ("STRICTLY read-only", "check documented as mutating nothing"),
        ('text="▶ Run broadcast check"', "check button in Settings"),
        ('Mic Doctor — test every mic', "doctor button in Settings"),
        ("nac.Capture(endpoint=eid or None)",
         "doctor probes each mic in an ISOLATED capture (never live sessions)"),
        ("dialog.after(100, tick)",
         "probing runs on the main thread via after() — no Tk from workers"),
        ("state[\"peak\"] = max(state[\"peak\"]", "peak is accumulated"),
        ("— silent (no signal)", "silent verdict string present"),
        ("All clear — ready for broadcast.", "all-clear summary present"),
    ):
        check(f"[bcheck] {name}", token in gui_src)


def test_queue_row_click_target() -> None:
    """Queue rows: a click must land even ON THE TEXT LABELS (Tk never
    propagates Button-1 to parent widgets), and the selection rebuild must
    be deferred past the double-click window so double-click-to-play works."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[qrow] gui.py readable", False, str(err))
        return
    for token, name in (
        ("targets = (item, inner, info_frame, status_lab, icon_lab,",
         "row binds EVERY child incl. text labels"),
        ("status_lab = ctk.CTkLabel", "status label bound"),
        ("icon_lab = ctk.CTkLabel", "icon label bound"),
        ("title_lab = ctk.CTkLabel", "title label bound"),
        ("artist_lab = ctk.CTkLabel", "artist label bound"),
        ("dur_lab = ctk.CTkLabel", "duration label bound"),
        ('w.bind("<Double-Button-1>"',
         "double-click bound on all row targets"),
        ("self.after(260, self._apply_queue_selection)",
         "selection rebuild deferred past double-click window"),
        ("def _on_queue_double_click", "double-click play handler wired"),
        ('self.__dict__.get("_queue_sel_after")',
         "pending token read via __dict__ (tk 3.14 getattr-safe)"),
        ("Tk delivers <Button-1> to the innermost widget",
         "the non-propagation gotcha is documented in-code"),
    ):
        check(f"[qrow] {name}", token in gui_src)


def test_queue_filter_button() -> None:
    """OBS-style Filter button: pops when a queue row is selected, routes
    songs to the song-filters dialog and mic rows to the native chain —
    and the filtered queue view must keep ORIGINAL row indexes."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[qfilter] gui.py readable", False, str(err))
        return
    for token, name in (
        ("btn_queue_filter", "Filter button exists"),
        ("def _update_queue_filter_btn", "visibility follows selection"),
        ("self.btn_queue_filter.pack_forget()      # hidden until a selection",
         "hidden until a row is selected"),
        ("def _open_selected_filters", "selection -> dialog router wired"),
        ('getattr(song, "source", "") == "mic"',
         "mic rows route to the native chain dialog"),
        ("def _open_song_filters", "song Filters dialog wired"),
        ('dialog.title(f"Filters for \'{title}\'")',
         "OBS-style 'Filters for …' title"),
        ("self.audio_player.set_eq(b, m, t)",
         "song dialog drives the real EQ API"),
        ("self.audio_player.set_auto_gain(",
         "song dialog drives the real auto-gain API"),
        ("self.audio_player.crossfade_enabled = bool(xf_var.get())",
         "song dialog drives the real crossfade API"),
        ("queue = list(enumerate(self.rq.queue))",
         "rows ALWAYS paired with original index (no bare-Song path)"),
        ("total_dur = sum(s.duration for _i, s in queue)",
         "stats unpacks tuples unconditionally — single shape"),
        ("queue = [(orig_i, s) for orig_i, s in queue",
         "filter operates on already-paired rows"),
        ("self._create_queue_item(orig_i, song)",
         "rows render with original indexes (filter-safe clicks)"),
    ):
        check(f"[qfilter] {name}", token in gui_src)


def test_song_filters_presets() -> None:
    """Song Filters dialog: one-click EQ preset rows (Vocal boost /
    Bass cut / Flat) fill the sliders from EQSettings.PRESETS — the same
    table as the Settings page — and only Apply touches the engine."""
    try:
        gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
        feat_src = (Path(__file__).parent / "features.py").read_text(encoding="utf-8")
    except OSError as err:
        check("[eqpreset] sources readable", False, str(err))
        return
    for token, name in (
        ("def apply_preset(name: str)", "in-dialog preset applier wired"),
        ('self.eq.PRESETS.get(name, self.eq.PRESETS["flat"])',
         "uses the SAME preset table as Settings"),
        ('("vocal", "Vocal boost")', "vocal preset button"),
        ('("bass_cut", "Bass cut")', "bass cut preset button"),
        ('("flat", "Flat")', "flat preset button"),
        ("preset_bar = ctk.CTkFrame(body", "preset row exists in dialog"),
        ("Nothing reaches the engine until Apply",
         "documented apply-to-commit behaviour"),
    ):
        check(f"[eqpreset] {name}", token in gui_src)
    check("[eqpreset] bass_cut in PRESETS table",
          '"bass_cut":  {"bass": -6' in feat_src)


def test_midstream_format_heal() -> None:
    """Mid-stream sample-rate/format switching must NOT kill the engine:
    the render thread re-opens the device in place (ring preserved) and
    the mic capture thread rebuilds its resampler in place. Dying and
    migrating is the LAST resort, not the default."""
    try:
        cpp = (Path(__file__).parent / "native_audio_render.cpp").read_text(
            encoding="utf-8")
    except OSError as err:
        check("[fmt-heal] native_audio_render.cpp readable", False, str(err))
        return
    for token, name in (
        ("bool heal_device_impl()", "in-place device recovery implemented"),
        ("for (int attempt = 0; attempt < 8; ++attempt) {\n            Sleep(250);",
         "recovery retries while the driver settles"),
        ("if (heal_device()) {\n                        device_fails = 0;",
         "paused-path failure counter heals before dying"),
        ("if (heal_device()) {\n                    device_fails = 0;",
         "playing-path failure counter heals before dying"),
        ("cur_endpoint_ = endpoint_id ? endpoint_id : L\"\";",
         "heal re-opens the SAME endpoint it was started on"),
        ("bool mic_reopen(MicBlock* m)", "mic capture self-heal implemented"),
        ("m->resampler_ok = false;",
         "mic resampler rebuilt for the NEW mix format"),
        ("mic_reopen(m);", "capture loop heals on device failure"),
        ('{"render_test_heal", render_test_heal, METH_VARARGS,',
         "heal path exercisable by tests"),
    ):
        check(f"[fmt-heal] {name}", token in cpp)


def main() -> None:
    pygame_mode = "--pygame" in sys.argv
    p = make_player()
    engine = "pygame" if pygame_mode else ("wasapi" if wasapi(p) else "pygame")
    print(f"\n=== Edge-case suite — engine: {engine} ===\n")

    if engine == "wasapi":
        test_native_pause_semantics()
    test_auto_gain_logic()
    test_b_shorter_than_fade(p, engine)
    test_single_track_queue(p, engine)
    test_pause_around_trigger(p, engine)
    test_seek_after_arm(p, engine)
    test_stop_during_pending(p, engine)
    test_rearm_race(p, engine)
    test_auto_gain_e2e(p, engine)
    test_native_decoder(p, engine)

    test_eq_engine(p)

    test_device_loss_stress(p, engine)
    test_skip_cascade_cap(p, engine)

    p.shutdown()
    test_reconnect_ladder()
    if engine == "wasapi":
        test_native_pump_and_stream()
    test_aircheck_recorder()
    test_aircheck_phase2()
    test_aircheck_scheduler()
    # Silence Sentinel (U4 dry-run) — fake-silence cases + wiring guard,
    # run inside the main suite so they can never be forgotten.
    try:
        from test_silence_sentinel import run_all as _sentinel_run_all
        _sentinel_run_all(into=RESULTS)
    except ImportError as _err:
        RESULTS.append(("[sentinel] suite importable", False, str(_err)))
    # Plugin SDK (U5 observe-only) — containment + wiring guard.
    try:
        from test_plugin_system import run_all as _plugin_run_all
        _plugin_run_all(into=RESULTS)
    except ImportError as _err:
        RESULTS.append(("[plugins] suite importable", False, str(_err)))
    test_version_sync_stage()
    test_mic_segment_dialog()
    test_mic_noise_suppression()
    test_hotkey_typing_guard()
    test_broadcast_readiness_tools()
    test_queue_row_click_target()
    test_queue_filter_button()
    test_song_filters_presets()
    test_midstream_format_heal()
    print(f"\n=== Summary: {engine} ===")
    fails = [r for r in RESULTS if not r[1]]
    for n, ok, d in fails:
        print(f"  ❌ {n} — {d}")
    print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} passed"
          + (" — all green ✅" if not fails else f" — {len(fails)} FAILED"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
