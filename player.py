"""
╔══════════════════════════════════════════════════════════════╗
║            🎵  FreQ — Audio Playback Engine                  ║
║     Audio Player — Download + Play via pygame     ║
╚══════════════════════════════════════════════════════════════╝

Supports:
  - YouTube: download audio via yt-dlp, play with pygame
  - Local files: play directly
  - Play / Pause / Resume / Stop / Seek
  - Auto-advance to next track
  - Progress callback for GUI

dependencies:
    pip install pygame yt-dlp
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

try:
    import pygame

    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

try:
    import yt_dlp

    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False

# Safe print: some consoles (e.g. Thai cp874) cannot encode emoji / unicode
# in log lines — swallow encoding errors instead of crashing playback.
import builtins as _builtins
_original_print = _builtins.print


def _safe_print(*args, **kwargs):
    try:
        _original_print(*args, **kwargs)
    except (UnicodeEncodeError, ValueError, OSError):
        pass


_builtins.print = _safe_print


def find_ffmpeg() -> Optional[str]:
    """Find ffmpeg location — checks PATH, common install dirs, and bundled location."""
    import shutil
    # 1. Already on PATH?
    if shutil.which("ffmpeg"):
        return None  # yt-dlp finds it on PATH
    # 2. Common Windows install locations
    candidates = []
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
            Path(r"C:\Program Files\ffmpeg\bin"),
            Path.home() / "Documents" / "yt-dlp",
            Path(__file__).parent / "ffmpeg",
            Path(__file__).parent / "deps" / "ffmpeg-essentials",
            Path(sys.executable).parent / "ffmpeg",
        ]
    else:
        candidates = [
            Path("/usr/local/bin"),
            Path.home() / ".local" / "bin",
        ]
    for d in candidates:
        if d.is_dir() and (d / "ffmpeg").exists():
            return str(d)
        if d.is_dir() and (d / "ffmpeg.exe").exists():
            return str(d)
    return None


def _ffmpeg_exe() -> str:
    """Resolve the ffmpeg executable — bundled deps dir, install dir, or PATH."""
    loc = find_ffmpeg()
    if loc:
        exe = Path(loc) / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if exe.exists():
            return str(exe)
    return "ffmpeg"


def _ffprobe_exe() -> str:
    """Resolve the ffprobe executable — bundled deps dir, install dir, or PATH."""
    loc = find_ffmpeg()
    if loc:
        exe = Path(loc) / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if exe.exists():
            return str(exe)
    return "ffprobe"


def auto_gain_linear(lufs: float, target: float = -14.0,
                     max_db: float = 6.0) -> float:
    """Linear multiplier that moves ``lufs`` toward ``target``.

    Clamped to ±max_db — boosting beyond that risks audible clipping on
    full-scale peaks; the crossfade engine uses the same ±6 dB ceiling.
    """
    db = max(-max_db, min(max_db, target - lufs))
    return 10.0 ** (db / 20.0)


class _NativeChunkSource:
    """Adapts the embedded miniaudio Decoder to the ``proc.stdout`` interface
    used by the ffmpeg subprocess paths (``read(n)`` / ``close()``), so both
    decoder backends share the same feeder code. Also mirrors the
    ``kill()`` attribute the ffmpeg cleanup paths call.
    """

    def __init__(self, decoder) -> None:
        self._dec = decoder
        self.stdout = self
        self.eof = False

    def read(self, n: int):
        if self.eof:
            return b""
        frames = max(1, n // 4)
        data = self._dec.read(frames)
        if data is None:
            self.eof = True
            return b""
        return data

    def close(self) -> None:
        try:
            self._dec.close()
        except Exception:
            pass

    def kill(self) -> None:
        self.close()


class AudioPlayer:
    """Audio player — Uses pygame.mixer for playback"""

    def __init__(self) -> None:
        self._initialized = False
        self._lock = threading.Lock()
        self._temp_dir: Optional[Path] = None
        self._current_file: Optional[str] = None
        self._is_playing = False
        self._is_paused = False
        self._play_start_time: float = 0.0
        self._pause_offset: float = 0.0
        self._volume: float = 0.8
        self._duration: float = 0.0
        self._on_finish: Optional[Callable] = None
        # Fired after the engine auto-recovers from a removed output device
        # (unplugged / Bluetooth dropped). Receives the dead track's path.
        self.on_device_lost: Optional[Callable[[str], None]] = None
        self._progress_thread: Optional[threading.Thread] = None
        # Each playback monitor gets its own cancellation event. A shared
        # boolean can be reset by a new track before the old monitor exits.
        self._progress_stop_event = threading.Event()
        self._callback_queue: list[tuple[Callable, tuple]] = []
        self._callback_lock = threading.Lock()
        # Seek state
        self._seek_offset: float = 0.0      # seconds offset from original file start
        self._eq_gains: tuple = (0.0, 0.0, 0.0)   # bass/mid/treble dB
        self._original_file: Optional[str] = None  # full original file (before seek trim)
        self._seek_trimmed_files: list[str] = []   # temp files created by seek
        # Auto-cue: skip leading silence at the start of each track
        self._auto_cue: bool = False
        # Crossfade engine: queue the next track so playback flows
        # A-tail → equal-power mix → B without a stop/start gap.
        self._crossfade_enabled: bool = False
        self._crossfade_duration: float = 4.0
        self._xf_pending_path: Optional[str] = None     # queued next-track file
        self._xf_pending_offset: float = 0.0            # where B starts inside it
        self._xf_pending_duration: float = 0.0          # reported duration of B
        self._xf_pending_index: int = -1                # queue index of B
        self._xf_b_source: Optional[str] = None         # B's original path (seeks)
        self._xf_mixed_path: Optional[str] = None       # temp mix file WE created
        self._xf_prepared: bool = False                 # file is rendered+loaded
        self._xf_preloading: bool = False               # render thread running
        self._xf_armed: bool = False                    # mix was queued to SDL
        self._xf_handoff: Optional[Callable] = None     # advance callback
        self._xf_active: bool = False                   # waiting for SDL end event
        self._xf_handoff_fired: bool = False
        # YouTube download state
        self._yt_ready_file: Optional[str] = None  # resolved file path after YT download
        # ── WASAPI render engine (C++) ──
        # Playback route: "pygame" (media framework on the OS default
        # device) or "wasapi" (native render stream on a chosen endpoint).
        self._engine: str = "pygame"
        self._render = None                    # native_audio_render capsule
        self._render_module = None             # the imported module
        self._render_endpoint: Optional[str] = None
        self._render_base: int = 0             # emitted counter at play start
        self._render_active: bool = False
        self._render_done: bool = False        # A decoder reached EOF
        self._render_b_done: bool = False      # B (crossfade) reached EOF
        self._decoder: Optional[threading.Thread] = None
        self._decoder_stop = threading.Event()
        self._xf_b_proc = None                 # B decoder process (wasapi fade)
        self._xf_native_feeder = False         # B is fed by the C++ engine
        self._xf_fade_total: int = 0
        self._xf_fade_pos: int = 0
        self._xf_mix_started: bool = False
        self._xf_gain_b: float = 1.0
        # Loudness Auto-Gain: normalizes every track toward one target LUFS.
        self._ag_enabled: bool = False
        self._ag_target: float = -14.0
        self._ag_gain: float = 1.0            # linear multiplier for current track
        self._ag_seq: int = 0                 # token invalidating stale measurements
        # Emergency live mic (WASAPI engine mix-in).
        self._mic_live_stop = threading.Event()
        self._mic_live_thread: Optional[threading.Thread] = None
        self._mic_live_on_finish: Optional[Callable] = None
        self._mic_duck_restore: Optional[float] = None

    # ─────────────────────────────────────
    # Init / Shutdown
    # ─────────────────────────────────────
    def init(self) -> bool:
        """Initialize pygame mixer"""
        if self._initialized:
            return True
        if not PYGAME_AVAILABLE:
            print("  ⚠️  pygame not installed — pip install pygame")
            return False
        try:
            pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=2048)
            pygame.mixer.init()
            self._temp_dir = Path(tempfile.mkdtemp(prefix="freq_"))
            self._initialized = True
            self._apply_volume()
            return True
        except Exception as e:
            print(f"  ⚠️  Cannot init pygame mixer: {e}")
            return False

    def shutdown(self) -> None:
        """Shutdown mixer and cleanup"""
        self.stop()
        self.stop_emergency_mic()
        if self._initialized:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
            self._initialized = False
        # Cleanup temp files
        self._cleanup_seek_files()
        # The mix file may survive stop() when it was the loaded track;
        # the mixer is down now, so it is safe to dispose.
        mixed = self._xf_mixed_path
        self._xf_mixed_path = None
        if mixed and os.path.isfile(mixed):
            try:
                os.remove(mixed)
            except Exception:
                pass
        if self._temp_dir and self._temp_dir.exists():
            try:
                import shutil
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return PYGAME_AVAILABLE and self._initialized

    # ─────────────────────────────────────
    # Volume
    # ─────────────────────────────────────
    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))
        self._apply_volume()

    # ── Loudness Auto-Gain ──────────────────
    def _effective_volume(self) -> float:
        """User volume × per-track auto-gain (linear)."""
        return max(0.0, self._volume * self._ag_gain)

    def _apply_volume(self) -> None:
        """Push the effective volume to the active engine."""
        eff = self._effective_volume()
        if self._engine == "wasapi" and self._render is not None:
            try:
                self._render_module.render_set_volume(self._render, min(2.0, eff))
            except Exception:
                logging.getLogger("freq.player").exception(
                    "render_set_volume failed (engine=%s)", self._engine)
        if self._initialized:
            try:
                # pygame caps at 1.0 — boost beyond that is WASAPI-only.
                pygame.mixer.music.set_volume(min(1.0, eff))
            except Exception:
                pass
        self._apply_eq()

    def set_eq(self, bass: float, mid: float, treble: float) -> None:
        """Set the 3-band EQ (dB, -12..+12) and push it to the engine."""
        self._eq_gains = (max(-12.0, min(12.0, bass)),
                          max(-12.0, min(12.0, mid)),
                          max(-12.0, min(12.0, treble)))
        self._apply_eq()

    def get_eq(self):
        return tuple(self._eq_gains)

    def _apply_eq(self) -> None:
        """Push the EQ gains to the WASAPI engine (pygame has no EQ path)."""
        if self._engine == "wasapi" and self._render is not None:
            try:
                b, m, t = self._eq_gains
                self._render_module.render_set_eq(self._render, b, m, t)
            except Exception:
                logging.getLogger("freq.player").exception("render_set_eq failed")

    @property
    def auto_gain_enabled(self) -> bool:
        return self._ag_enabled

    @auto_gain_enabled.setter
    def auto_gain_enabled(self, v: bool) -> None:
        self.set_auto_gain(v)

    @property
    def auto_gain_target(self) -> float:
        return self._ag_target

    @auto_gain_target.setter
    def auto_gain_target(self, v: float) -> None:
        self.set_auto_gain(self._ag_enabled, v)

    def set_auto_gain(self, enabled: bool, target: float = None) -> None:
        """Enable loudness normalization toward ``target`` LUFS (default -14)."""
        self._ag_enabled = bool(enabled)
        if target is not None:
            try:
                self._ag_target = float(target)
            except (TypeError, ValueError):
                pass
        if not self._ag_enabled:
            self._ag_seq += 1          # discard in-flight measurements
            self._ag_gain = 1.0
            self._apply_volume()
        else:
            # Measure the current track right away (cached → instant).
            self._schedule_auto_gain(self._original_file)

    def _schedule_auto_gain(self, filepath: Optional[str]) -> None:
        """Measure ``filepath`` in the background and apply its gain.

        Gain resets to neutral immediately so a fresh track starts at
        normal volume; the measured gain lands a moment later.
        """
        if not self._ag_enabled:
            return
        self._ag_seq += 1
        seq = self._ag_seq
        self._ag_gain = 1.0
        self._apply_volume()
        if not filepath or not os.path.isfile(filepath):
            return

        def _work() -> None:
            try:
                from audio_meter import measure_file_lufs
                lufs = measure_file_lufs(filepath)
                gain = auto_gain_linear(lufs, self._ag_target)
            except Exception:
                logging.getLogger("freq.player").exception(
                    "auto-gain: LUFS measure failed for %s", filepath)
                return
            if seq != self._ag_seq or not self._ag_enabled:
                return                 # track changed / feature turned off
            self._ag_gain = gain
            self._apply_volume()

        threading.Thread(target=_work, daemon=True, name="freq-autogain").start()

    # ─────────────────────────────────────
    # WASAPI render engine (C++)
    # ─────────────────────────────────────
    @property
    def engine(self) -> str:
        """Active playback engine: "pygame" or "wasapi"."""
        return self._engine

    def use_wasapi_engine(self, endpoint_id: str = "") -> bool:
        """Route playback through the C++ WASAPI render engine.

        Audio plays on the given endpoint *only* — the OS default device
        stays untouched. Returns False when the native module or the
        endpoint is unavailable (pygame remains active).
        """
        try:
            import native_audio_render as nar
        except ImportError:
            print("  ⚠️  native_audio_render not available — staying on pygame")
            return False
        if not self._initialized:
            self.init()
        if self._render is None:
            self._render = nar.render_new()
            self._render_module = nar
        try:
            nar.render_start(self._render, endpoint_id or None)
        except Exception as e:
            print(f"  ⚠️  WASAPI render start failed: {e}")
            return False
        nar.render_set_volume(self._render, min(2.0, self._effective_volume()))
        self._render_endpoint = endpoint_id or None
        self._engine = "wasapi"
        return True

    def move_to_endpoint(self, endpoint_id: str) -> bool:
        """Switch the output endpoint; the current track keeps playing.

        Migrates playback (pygame → WASAPI, or WASAPI → another endpoint)
        by restarting the current track on the new route at the same
        position. The OS default device is never touched.
        """
        was_pygame = self._engine == "pygame"
        was_playing = self.is_playing
        pos = self.get_position()
        cur = self._original_file or self._current_file
        dur = self._duration
        if not self.use_wasapi_engine(endpoint_id):
            return False
        if was_playing and cur and os.path.isfile(cur):
            # Start directly at the captured position — restarting at 0 and
            # then calling seek(pos) double-seeks (audio jumps back to the
            # top while the UI shows the old position).
            if self._wasapi_start(cur, pos if pos > 0.3 else 0.0, dur):
                if was_pygame:
                    try:
                        pygame.mixer.music.stop()  # old route falls silent
                    except Exception:
                        pass
        return True

    def _recover_device_lost(self) -> None:
        """The selected output device vanished (unplugged/Bluetooth dropped).

        The C++ engine shuts itself down after repeated device failures,
        which makes render_is_running() go false while _render_active is
        still set — the watchdog in _pump_render lands here. Migrates to
        the system default endpoint at the same position; falls back to
        pygame when even the default endpoint refuses to start.
        """
        if not self._render_active:
            return
        self._render_active = False     # keep the watchdog from re-firing
        self.stop_emergency_mic()       # the mic died with the old device
        was_playing = self._is_playing
        cur = self._original_file or self._current_file
        self._decoder_stop.set()        # unstick the backpressured decoder
        self._kill_b_proc()
        print("  ⚠️  Output device lost — migrating to the default endpoint")
        ok = False
        if was_playing and cur and os.path.isfile(cur):
            ok = self.move_to_endpoint("")
        if not ok:
            self.use_pygame_engine()
        cb = self.on_device_lost
        if cb:
            try:
                cb(cur or "")
            except Exception:
                pass

    def use_pygame_engine(self) -> None:
        """Switch back to pygame playback (restarts the current track)."""
        was_playing = self.is_playing
        pos = self.get_position()
        cur = self._original_file or self._current_file
        dur = self._duration
        self._engine = "pygame"
        self._render_active = False
        self._render_done = False
        self._render_b_done = False
        self._decoder_stop.set()
        if self._render is not None:
            try:
                self._render_module.render_stop(self._render)
            except Exception:
                pass
        if was_playing and cur and os.path.isfile(cur) and pos > 0.5:
            if self.play_file(cur, duration=dur):
                self.seek(pos)

    def _render_position(self) -> float:
        """Playback position (seconds) of the WASAPI engine."""
        if self._render is None or not self._render_active:
            return 0.0
        try:
            emitted = self._render_module.render_emitted(self._render)
            return max(0.0, (emitted - self._render_base) / 44100.0)
        except Exception:
            return 0.0

    def _open_native_decoder(self, filepath: str):
        """Open the embedded miniaudio decoder (no ffmpeg needed).

        Returns a Decoder or None when the module is missing or the codec
        is unsupported (e.g. AAC/M4A) — callers fall back to ffmpeg.
        """
        try:
            import native_audio_decode as nad
        except ImportError:
            return None
        try:
            return nad.Decoder(filepath, rate=44100, channels=2)
        except Exception:
            return None

    def _ensure_decoder(self, filepath: str) -> bool:
        """Decode ``filepath`` to s16le stereo 44.1 kHz PCM in a thread.

        The embedded miniaudio decoder is preferred (in-process, works
        without ffmpeg); unsupported codecs fall back to the ffmpeg
        subprocess. Either way PCM is pushed into the WASAPI ring; the
        ring's backpressure paces the decode to realtime.
        """
        dec = self._open_native_decoder(filepath)
        if dec is None and not self._ffmpeg_available():
            print(f"  ⚠️  cannot decode '{filepath}': codec not supported by the "
                  "embedded decoder and ffmpeg is unavailable")
            return False
        self._decoder_stop.set()
        self._decoder_stop = threading.Event()
        stop_event = self._decoder_stop
        nar = self._render_module

        def _decode():
            try:
                if dec is not None:
                    proc = _NativeChunkSource(dec)
                else:
                    proc = subprocess.Popen(
                        [_ffmpeg_exe(), "-v", "quiet", "-i", filepath,
                         "-ac", "2", "-ar", "44100", "-f", "s16le", "-y", "pipe:1"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )
            except Exception:
                # Treat a dead decoder as EOF so end/crossfade logic can
                # still proceed instead of hanging in silence forever.
                import traceback
                print("  ⚠️  decoder setup failed:")
                traceback.print_exc()
                self._render_done = True
                return
            chunk = 44100 * 4  # 1 s of stereo s16le
            try:
                while not stop_event.is_set():
                    data = proc.stdout.read(chunk)
                    if not data:
                        break
                    off = 0
                    while off < len(data) and not stop_event.is_set():
                        w = nar.render_write(self._render, data[off:])
                        if w == 0:
                            time.sleep(0.03)  # ring full — realtime pacing
                            continue
                        off += w * 4
                if not stop_event.is_set():
                    self._render_done = True
            except Exception:
                import traceback
                print("  ⚠️  decoder thread failed:")
                traceback.print_exc()
                self._render_done = True  # decoder died — unblock end logic
            finally:
                for closer in (proc.stdout.close, proc.kill):
                    try:
                        closer()
                    except Exception:
                        pass
                # Release the file handle (native decoder holds the wav open).
                try:
                    held = getattr(proc, "_dec", None)
                    if held is not None:
                        held.close()
                except Exception:
                    pass

        self._decoder = threading.Thread(target=_decode, daemon=True)
        self._decoder.start()
        return True

    def _start_b_decoder(self, filepath: str) -> bool:
        """Open the crossfade B decoder (chunks are pulled lazily).

        Prefers an ffmpeg PIPE when available — the C++ engine can adopt
        the pipe handle and feed/fade B itself with no Python pump. Falls
        back to the embedded miniaudio decoder (Python pump) when ffmpeg
        is missing.
        """
        if self._ffmpeg_available():
            try:
                self._xf_b_proc = subprocess.Popen(
                    [_ffmpeg_exe(), "-v", "quiet", "-i", filepath,
                     "-ac", "2", "-ar", "44100", "-f", "s16le", "-y", "pipe:1"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:
                pass
        dec = self._open_native_decoder(filepath)
        if dec is not None:
            self._xf_b_proc = _NativeChunkSource(dec)
            return True
        self._xf_b_proc = None
        return False

    def _kill_b_proc(self) -> None:
        """Terminate the crossfade B decoder subprocess (if any)."""
        proc = self._xf_b_proc
        self._xf_b_proc = None
        self._xf_native_feeder = False
        if proc is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    def _pump_render(self) -> None:
        """Periodic WASAPI upkeep: crossfade chunk-mixing + end detection."""
        if (self._engine != "wasapi" or not self._is_playing
                or self._is_paused or self._render is None):
            return
        nar = self._render_module
        # Device-loss watchdog: a clean stop() clears _render_active, so
        # "active but not running" can only mean the endpoint is gone
        # (unplugged / Bluetooth dropped / driver reset).
        if self._render_active and not nar.render_is_running(self._render):
            self._recover_device_lost()
            return
        # Built-in C++ source: mirror its EOF into the player state so
        # end detection keeps working. The engine has ONE active source:
        # before the crossfade handoff it is A (→ _render_done); after
        # the handoff it is B's pipe (→ _render_b_done + release ffmpeg).
        try:
            eof = nar.render_source_eof(self._render)
        except Exception:
            eof = False
        if eof:
            if self._xf_native_feeder:
                self._render_b_done = True
                self._kill_b_proc()
            else:
                self._render_done = True
        pos = self._render_position()

        # Start the crossfade render (opens B's decoder) near the track's
        # end — the trigger is engine-agnostic; on WASAPI it arms the
        # chunk-mixing path below instead of a pre-rendered file.
        self._check_crossfade_trigger()

        # ── Crossfade: append B chunks (faded) once A is nearly done. A's
        # decoder EOF leaves its tail in the ring, so B mixed in from now
        # overlaps the true tail — one clock, no drift.
        if self._xf_b_proc is not None:
            remaining = (self._duration - pos) if self._duration > 0 else 999.0
            fade_s = self._xf_fade_total / 44100.0
            # The ring is a single FIFO: A's decoder and this feeder are two
            # producers — writing B while A is still flowing interleaves A/B
            # chunks in random order and garbles the audio (crackle). So B
            # starts only after A has FULLY flushed through the ring: A plays
            # out completely, then B fades in seamlessly behind it.
            a_flushed = (self._render_done
                         and nar.render_buffered(self._render) <= 0.3 * 44100)
            if (remaining <= fade_s + 0.05 and a_flushed
                    and not self._xf_mix_started):
                self._xf_mix_started = True
                self._xf_pending_offset = 0.0   # B starts (at 0) right now
                # Promote the timeline to B: keep the same output clock but
                # shift the offset so get_position() reports B's position.
                with self._lock:
                    self._seek_offset = -pos
                    if self._xf_pending_duration > 0:
                        self._duration = self._xf_pending_duration
                    if self._xf_pending_path:
                        self._original_file = self._xf_pending_path
                self._schedule_auto_gain(self._original_file)
                self._finish_crossfade()  # UI handoff: B is audible now
                # Hand B's pipe to the C++ engine — it reads, fades in and
                # feeds the ring itself; no Python chunk pump on the path.
                try:
                    import msvcrt
                    handle = int(msvcrt.get_osfhandle(
                        self._xf_b_proc.stdout.fileno()))
                    self._xf_native_feeder = nar.render_use_pipe(
                        self._render, handle, fade_s, self._xf_gain_b)
                    if not self._xf_native_feeder:
                        logging.getLogger("freq.player").warning(
                            "native pipe feeder rejected the handle")
                except Exception:
                    logging.getLogger("freq.player").exception(
                        "native pipe feeder handoff failed")
                    self._xf_native_feeder = False
                if self._xf_native_feeder:
                    return
            if self._xf_mix_started:
                if getattr(self, "_xf_native_feeder", False):
                    return   # C++ engine is feeding B — nothing to pump
                from audio_meter import crossfade_mix
                stalled = 0
                while nar.render_buffered(self._render) < 44100:
                    try:
                        chunk = self._xf_b_proc.stdout.read(44100 * 4)
                    except Exception:
                        chunk = b""
                    if not chunk:
                        try:
                            self._xf_b_proc.stdout.close()
                            self._xf_b_proc.kill()
                        except Exception:
                            pass
                        self._xf_b_proc = None
                        self._render_b_done = True
                        break
                    frames = len(chunk) // 4
                    if self._xf_fade_pos < self._xf_fade_total:
                        # NOTE: positional args — the native module does not
                        # accept keyword arguments (METH_VARARGS).
                        chunk = crossfade_mix(
                            b"", bytes(chunk), 0, 0, 0,
                            1.0, self._xf_gain_b,
                            self._xf_fade_pos, self._xf_fade_total,
                        )
                        self._xf_fade_pos += frames
                    # Feed with backpressure — a partial write must be
                    # retried or the remaining bytes are lost forever.
                    off = 0
                    while off < len(chunk):
                        w = nar.render_write(self._render, chunk[off:])
                        if w == 0:
                            stalled += 1
                            if stalled > 400 or not self._is_playing:
                                break   # engine stopped — drop the rest
                            time.sleep(0.025)
                            continue
                        stalled = 0
                        off += w * 4
                    if not self._is_playing:
                        return
                return

        # ── End detection: decoder(s) finished and the ring nearly drained.
        if ((self._render_done or self._render_b_done)
                and nar.render_buffered(self._render) <= 0.25 * 44100):
            self._finish_playback()

    def _finish_playback(self) -> None:
        """Shared end-of-track handling for both engines."""
        self._is_playing = False
        self._is_paused = False
        self._render_active = False
        self._current_file = None
        self.stop_emergency_mic()       # never leak the live mic into the next track
        cb = self._on_finish
        self._on_finish = None
        if cb:
            try:
                cb()
            except Exception as e:
                print(f"  ⚠️  finish callback failed: {e}")

    def _wasapi_start(self, filepath: str, seek_offset: float,
                      duration: float) -> bool:
        """Start WASAPI playback of ``filepath`` (shared by play/seek)."""
        nar = self._render_module
        if nar is None or self._render is None:
            return False
        # Refuse early when the engine is already down (device lost / driver
        # reset) — a start into a dead engine leaves _render_active set with
        # nothing feeding, and the caller sees "success" with silence.
        try:
            if not nar.render_is_running(self._render):
                logging.getLogger("freq.player").warning(
                    "WASAPI engine is not running — refusing start for %s",
                    filepath)
                return False
        except Exception:
            return False
        with self._lock:
            # Starting a new track must always sound: a paused engine (jingle/
            # anthem pause, user pause) left native silence feeding the device
            # — the anthem played "silently" because pause was never lifted.
            if self._is_paused:
                self._is_paused = False
                self._pause_offset = 0.0
                try:
                    self._render_module.render_pause(self._render, 0)
                except Exception:
                    pass
            self._render_done = False
            self._render_b_done = False
            self._kill_b_proc()
            self._xf_mix_started = False
            self._xf_fade_total = 0
            self._xf_fade_pos = 0
            self._xf_gain_b = 1.0
            self._xf_pending_path = None
            self._xf_pending_index = -1
            self._xf_pending_offset = 0.0
            self._xf_prepared = False
            self._xf_preloading = False
            self._xf_armed = False
            self._xf_active = False
            self._xf_handoff_fired = False
            self._xf_handoff = None
            self._xf_mixed_path = None
            self._xf_native_feeder = False
            try:
                nar.render_clear(self._render)
                nar.render_set_volume(self._render, min(2.0, self._effective_volume()))
                b, m, t = self._eq_gains
                nar.render_set_eq(self._render, b, m, t)
            except Exception:
                logging.getLogger("freq.player").exception(
                    "render engine (re)start failed for %s", filepath)
                return False
            self._current_file = filepath
            self._original_file = filepath
            self._render_active = True
            self._render_base = nar.render_emitted(self._render)
            self._is_playing = True
            self._is_paused = False
            self._seek_offset = seek_offset
            self._play_start_time = time.time()
            self._pause_offset = 0.0
            # A zero/stale duration leaves the progress bar wrong for the
            # whole track — probe the real length whenever it is missing.
            if duration and duration > 0:
                self._duration = duration
            else:
                self._duration = self._get_file_duration() or duration
            self._start_progress_monitor()
        self._schedule_auto_gain(filepath)
        # Built-in C++ source: the engine decodes and feeds the ring
        # itself — the Python decode pump is no longer on the audio path
        # (and works even when ffmpeg is absent, miniaudio decodes).
        try:
            if not nar.render_use_file(self._render, filepath,
                                       seek_offset, 0.0, 1.0):
                # Codec miniaudio cannot open (e.g. AAC/M4A) — fall back
                # to the legacy ffmpeg-fed Python pump.
                return self._ensure_decoder(filepath)
        except Exception:
            logging.getLogger("freq.player").exception(
                "render_use_file failed for %s", filepath)
            return self._ensure_decoder(filepath)
        return True

    # ─────────────────────────────────────
    # Play a Song
    # ─────────────────────────────────────
    def play_file(self, filepath: str, duration: float = 0.0,
                  on_finish: Optional[Callable] = None) -> bool:
        """Play audio file from path"""
        if not self.available:
            return False
        if not os.path.isfile(filepath):
            print(f"  ❌  File not found: {filepath}")
            return False

        # WASAPI engine: decode into the native render stream instead of
        # pygame's music channel. Called OUTSIDE the lock — _wasapi_start
        # locks internally (threading.Lock is not reentrant).
        if self._engine == "wasapi" and self._render is not None:
            seek = 0.0
            if self._auto_cue:
                skipped, cue_path = self._apply_auto_cue(filepath)
                if cue_path:
                    filepath = cue_path
                    seek = skipped
            if self._wasapi_start(filepath, seek, duration):
                self._on_finish = on_finish
                return True
            # Recovery ladder: the engine refused a new source (device lost,
            # driver reset, endpoint gone). A hard False here aborts every
            # jingle/anthem caller mid-cycle — migrate to the default
            # endpoint and retry once, then fall back to pygame so playback
            # itself can never be lost to a dead engine.
            logging.getLogger("freq.player").warning(
                "WASAPI start failed for %s — recovering the playback engine",
                filepath)
            recovered = False
            try:
                if self.use_wasapi_engine(""):
                    recovered = self._wasapi_start(filepath, seek, duration)
            except Exception:
                logging.getLogger("freq.player").exception(
                    "WASAPI engine recovery failed for %s", filepath)
            if recovered:
                self._on_finish = on_finish
                return True
            self.use_pygame_engine()   # final fallback: the classic route
            if self._engine != "pygame":
                self._on_finish = on_finish
                return False
            # fall through to the pygame path below

        with self._lock:
            try:
                # Track the original full file for seek operations.
                # If this is a fresh play (not a seek-trimmed file), reset seek state.
                if not filepath.startswith(str(self._temp_dir or "")):
                    self._original_file = filepath
                elif self._original_file is None:
                    self._original_file = filepath
                # else: seek-trimmed file, keep existing _original_file

                # Auto-cue: swap in a silence-trimmed file before the first
                # load so the track starts right at the music (single load).
                if self._auto_cue and filepath == self._original_file:
                    skipped, cue_path = self._apply_auto_cue(filepath)
                    if cue_path:
                        filepath = cue_path
                        self._seek_offset = skipped
                    else:
                        self._seek_offset = 0.0
                else:
                    self._seek_offset = 0.0

                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                # Reset crossfade state for the new track (dispose the
                # previous mix file — it has finished playing by now).
                old_mixed = self._xf_mixed_path
                self._xf_mixed_path = None
                if old_mixed and old_mixed != filepath and os.path.isfile(old_mixed):
                    try:
                        os.remove(old_mixed)
                    except Exception:
                        pass
                self._xf_pending_path = None
                self._xf_pending_offset = 0.0
                self._xf_pending_duration = 0.0
                self._xf_pending_index = -1
                self._xf_b_source = None
                self._xf_prepared = False
                self._xf_preloading = False
                self._xf_armed = False
                self._xf_active = False
                self._xf_handoff_fired = False
                self._xf_handoff = None
                self._current_file = filepath
                self._is_playing = True
                self._is_paused = False
                self._play_start_time = time.time()
                self._pause_offset = 0.0
                self._duration = duration
                self._on_finish = on_finish
                self._start_progress_monitor()
                self._schedule_auto_gain(self._original_file or filepath)
                return True
            except Exception as e:
                print(f"  ❌  Cannot play file: {e}")
                return False

    def play_youtube(self, url: str, duration: float = 0.0,
                     song_id: str = "",
                     title: str = "",
                     on_finish: Optional[Callable] = None,
                     on_progress: Optional[Callable] = None,
                     start_position: float = 0.0) -> bool:
        """Download YouTube audio and play (check cache first)"""
        if not self.available:
            if on_progress:
                on_progress("error", 0)
            return False
        if not YT_DLP_AVAILABLE:
            print("  ⚠️  yt-dlp not installed — pip install yt-dlp")
            if on_progress:
                on_progress("error", 0)
            return False

        # ── Check cache first ──
        from cache import cache as song_cache
        if song_id and song_cache.has(song_id):
            cached_path = song_cache.get(song_id)
            if cached_path and os.path.isfile(cached_path):
                # Keep the resolved path available to the GUI so it can load
                # the waveform and to the streamer for cache playback.
                self._yt_ready_file = cached_path
                if on_progress:
                    on_progress("cached", 100)
                self.play_file(cached_path, duration, on_finish)
                if start_position > 0:
                    self.seek(start_position)
                return True

        # ── Download in background thread ──
        def _download_and_play():
            try:
                if on_progress:
                    on_progress("downloading", 0)

                outtmpl = str(self._temp_dir / "%(id)s.%(ext)s")

                def _progress_hook(d):
                    if d.get("status") == "downloading" and on_progress:
                        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        downloaded = d.get("downloaded_bytes") or 0
                        pct = (downloaded / total * 100) if total else 0
                        on_progress("downloading", pct)
                    elif d.get("status") == "finished" and on_progress:
                        on_progress("processing", 100)

                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": outtmpl,
                    "progress_hooks": [_progress_hook],
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                }
                _ffmpeg_loc = find_ffmpeg()
                if _ffmpeg_loc:
                    ydl_opts["ffmpeg_location"] = _ffmpeg_loc

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    base = os.path.splitext(filename)[0]
                    filename = base + ".mp3"
                    if not os.path.exists(filename):
                        filename = ydl.prepare_filename(info)

                if on_progress:
                    on_progress("playing", 100)

                actual_duration = duration
                if not actual_duration and info:
                    actual_duration = float(info.get("duration") or 0)

                # Store the resolved filename for callers (e.g. streaming)
                self._yt_ready_file = filename

                # Save to cache if song_id provided
                if song_id and os.path.isfile(filename):
                    from cache import cache as song_cache
                    import shutil
                    cache_path = str(song_cache.cache_dir / f"{song_id}.mp3")
                    try:
                        shutil.copy2(filename, cache_path)
                        from cache import CacheEntry
                        import time as _time
                        entry = CacheEntry(
                            song_id=song_id,
                            title=title or "",
                            url=url,
                            filepath=cache_path,
                            duration=actual_duration,
                            cached_at=_time.time(),
                            access_count=0,
                            last_accessed=_time.time(),
                        )
                        with song_cache._lock:
                            song_cache._entries[song_id] = entry
                            song_cache._evict_if_needed()
                            song_cache._save_index()
                    except Exception:
                        pass

                if not self.play_file(filename, actual_duration, on_finish):
                    if on_progress:
                        on_progress("error", 0)
                    return
                if start_position > 0:
                    self.seek(start_position)

            except Exception as e:
                print(f"  ❌  YouTube download failed: {e}")
                if on_progress:
                    on_progress("error", 0)

        threading.Thread(target=_download_and_play, daemon=True).start()
        return True


    def pause(self) -> None:
        if not self.available:
            return
        with self._lock:
            if self._is_playing and not self._is_paused:
                if self._engine == "wasapi":
                    # WASAPI native pause: the stream plays silence while the
                    # ring stays frozen, so resume continues exactly here.
                    # (Muting alone would keep draining the ring — playback
                    # would silently advance while paused.)
                    self._pause_offset = self._render_position()
                    self._is_paused = True
                    if self._render is not None:
                        try:
                            # NOTE: positional args — native module is METH_VARARGS.
                            self._render_module.render_pause(self._render, 1)
                        except Exception:
                            pass
                    return
                try:
                    pygame.mixer.music.pause()
                    # Capture the position BEFORE marking paused —
                    # get_position() returns _pause_offset once paused.
                    self._pause_offset = self.get_position()
                    self._is_paused = True
                except Exception:
                    pass

    def resume(self) -> None:
        if not self.available:
            return
        with self._lock:
            if self._is_paused:
                if self._engine == "wasapi":
                    self._is_paused = False
                    self._play_start_time = time.time() - self._pause_offset
                    if self._render is not None:
                        try:
                            self._render_module.render_pause(self._render, 0)
                        except Exception:
                            pass
                    return
                try:
                    pygame.mixer.music.unpause()
                    self._is_paused = False
                    self._play_start_time = time.time() - self._pause_offset
                except Exception:
                    pass

    def reopen_output(self, endpoint_id: Optional[str] = None) -> bool:
        """Re-open audio output (WASAPI) or re-init the mixer (pygame)."""
        # WASAPI engine: restart the render stream on the given endpoint —
        # the buffered audio is preserved, so the track keeps flowing.
        if self._engine == "wasapi":
            try:
                import native_audio_render as nar
            except ImportError:
                return False
            if self._render is None:
                self._render = nar.render_new()
                self._render_module = nar
            try:
                nar.render_stop(self._render)
                nar.render_start(self._render, endpoint_id or None)
            except Exception as e:
                print(f"  ⚠️  Cannot move WASAPI output: {e}")
                return False
            self._render_endpoint = endpoint_id or None
            return True
        return self._reopen_pygame_output()

    def _reopen_pygame_output(self) -> bool:
        """Re-initialize the mixer so playback follows the new default output.

        The currently playing track keeps running: position, seek state and
        the finish callback are preserved and playback resumes on the new
        device within a few milliseconds. Returns True when the mixer was
        successfully re-opened.
        """
        if not PYGAME_AVAILABLE:
            return False
        with self._lock:
            was_playing = self._is_playing and not self._is_paused
            position = self.get_position() if self._is_playing else 0.0
            try:
                if self._initialized:
                    pygame.mixer.quit()
                    self._initialized = False
                pygame.mixer.init()
                pygame.mixer.music.set_volume(max(0.0, min(1.0, self._effective_volume())))
                self._initialized = True
            except Exception as e:
                print(f"  ⚠️  Cannot reopen audio output: {e}")
                return False
            if self._current_file and os.path.isfile(self._current_file):
                try:
                    pygame.mixer.music.load(self._current_file)
                    if was_playing:
                        pygame.mixer.music.play()
                        # Re-align the wall-clock position tracker across the reload.
                        self._play_start_time = time.time() - position
                    elif self._is_paused:
                        pygame.mixer.music.play()
                        pygame.mixer.music.pause()
                        self._play_start_time = time.time() - position
                except Exception as e:
                    print(f"  ⚠️  Cannot resume on new output: {e}")
            return True

    def stop(self) -> None:
        if not self.available:
            return
        with self._lock:
            self._progress_stop_event.set()
            self._decoder_stop.set()
            # An emergency mic must never outlive the transport that
            # spawned it (skip/stop/new track/app close).
            self.stop_emergency_mic()
            if self._engine == "wasapi":
                if self._render is not None:
                    try:
                        # Un-pause first or the stream stays silent forever.
                        self._render_module.render_pause(self._render, 0)
                        self._render_module.render_clear(self._render)
                    except Exception:
                        pass
                self._render_active = False
                self._render_done = False
                self._render_b_done = False
                self._kill_b_proc()
                self._xf_mix_started = False
            else:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
            self._is_playing = False
            self._is_paused = False
            self._current_file = None
            self._on_finish = None
            self._seek_offset = 0.0
            self._original_file = None
            # A stopped transport has no timeline — any pending/armed
            # crossfade is dead. (Inline resets: we already hold the
            # non-reentrant lock; _invalidate_crossfade would deadlock.)
            self._ag_seq += 1
            self._ag_gain = 1.0
            self._xf_pending_path = None
            self._xf_pending_offset = 0.0
            self._xf_pending_duration = 0.0
            self._xf_pending_index = -1
            self._xf_b_source = None
            self._xf_native_feeder = False
            self._xf_prepared = False
            self._xf_preloading = False
            self._xf_armed = False
            self._xf_active = False
            self._xf_handoff_fired = False
            self._xf_handoff = None
            self._cleanup_seek_files()

    def get_position(self) -> float:
        """Current position in seconds (accounting for seek offset)"""
        if not self.available:
            return 0.0
        if not self._is_playing:
            return 0.0
        if self._engine == "wasapi" and self._render_active:
            if self._is_paused:
                return self._seek_offset + self._pause_offset
            return self._seek_offset + self._render_position()
        if self._is_paused:
            return self._seek_offset + self._pause_offset
        return self._seek_offset + (time.time() - self._play_start_time)

    def get_duration(self) -> float:
        return self._duration

    def seek(self, seconds: float) -> None:
        """Seek to an absolute position (in seconds) within the current song.

        Uses ffmpeg to create a trimmed audio file starting from the
        desired position, then reloads it into pygame.mixer.
        """
        if not self.available:
            return
        if not self._current_file or not os.path.isfile(self._current_file):
            print("  ⚠️  No song loaded — cannot seek")
            return
        # WASAPI seeks natively through the embedded decoder — ffmpeg is
        # only needed for the pygame-engine trimmed-file path, checked there.

        # Clamp to valid range
        total = self._duration if self._duration > 0 else self._get_file_duration()
        seconds = max(0.0, min(seconds, max(0.0, total - 0.1)))

        # Remember the file we were playing from (original, not previously trimmed)
        source = self._original_file or self._current_file

        # WASAPI: seek INSIDE the engine — the embedded decoder jumps and
        # everything the OLD position had queued (ring + device buffer) is
        # dropped in the same step. No engine rebuild, no gap, no stale
        # tail racing the new position (the old rebuild path stacked two
        # audio streams when the waveform was pressed repeatedly). Covers
        # seek-to-start too — a rebuild for that case also dropped the
        # WASAPI output entirely and layerad pygame on top of it.
        if self._engine == "wasapi" and self._render is not None and self._render_active:
            try:
                self._render_module.render_source_seek_flush(self._render, seconds)
                # A seek jumps the timeline: any armed/pending crossfade
                # mix belongs to the OLD timeline and must die with it
                # (same contract the rebuild path honoured via _do_seek).
                self._kill_b_proc()
                self._invalidate_crossfade()
                with self._lock:
                    self._seek_offset = seconds
                    self._play_start_time = time.time()
                    self._pause_offset = 0.0
                    self._render_done = False
                    self._render_b_done = False
                return
            except Exception:
                logging.getLogger("freq.player").exception(
                    "engine seek failed for %s @ %.1fs — rebuilding instead",
                    source, seconds)

        # If seeking to the very start, just restart from original file
        if seconds <= 0.1:
            self._do_seek(source, 0.0, total)
            return

        # WASAPI: rebuild the decode pipeline from the new offset.
        # The embedded decoder seeks natively — no ffmpeg needed, no
        # silent failure that would desync the progress bar from audio.
        if self._engine == "wasapi" and self._render is not None:
            self._wasapi_start(source, seconds, total)
            return

        # Use ffmpeg to create a trimmed file from the seek point
        try:
            if not self._temp_dir:
                self._temp_dir = Path(tempfile.mkdtemp(prefix="freq_"))
            seek_file = str(self._temp_dir / f"seek_{int(seconds * 1000)}.mp3")

            cmd = [
                _ffmpeg_exe(), "-y",
                "-ss", str(seconds),
                "-i", source,
                "-c:a", "libmp3lame",
                "-q:a", "2",
                "-avoid_negative_ts", "make_zero",
                seek_file,
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=30
            )
            if result.returncode != 0 or not os.path.isfile(seek_file):
                print(f"  ⚠️  ffmpeg seek failed: {result.stderr.decode(errors='ignore')[:200]}")
                return

            self._seek_trimmed_files.append(seek_file)
            self._do_seek(seek_file, seconds, total)

        except subprocess.TimeoutExpired:
            print("  ⚠️  ffmpeg seek timed out")
        except FileNotFoundError:
            print("  ⚠️  ffmpeg not found — seek unavailable")
        except Exception as e:
            print(f"  ⚠️  Seek failed: {e}")

    # ─────────────────────────────────────
    # Emergency live microphone (WASAPI mix-in)
    # ─────────────────────────────────────
    def play_mic_live(self, duration: float, device_endpoint: str = "",
                      mic_gain: float = 1.0, duck_factor: float = 0.3,
                      on_finish: Optional[Callable] = None) -> bool:
        """Duck the music and mix a LIVE microphone into the output.

        The mic lives inside the C++ render engine — its audio is mixed
        into the same device buffers as the music, so the delay is the
        OS capture latency (~20 ms), not a record-then-play round trip.
        Follows the engine's output endpoint automatically. Returns
        False on the pygame engine or when the capture endpoint cannot
        be opened (callers fall back to the recorded-segment path).
        """
        if self._engine != "wasapi" or self._render is None:
            return False
        nar = self._render_module
        # Dual mic: a second call while a mic is already live JOINS that
        # session — it must not reset the timer chain, the duck state or
        # the on_finish callback owned by the first mic.
        joining = self.emergency_mic_active()
        try:
            if not nar.render_is_running(self._render):
                return False
            prev = self._volume
            try:
                if duck_factor > 0.0 and duck_factor < 1.0:
                    # Dual mic: the second mic joins an already-ducked
                    # session — ducking again would compound (0.3×0.3).
                    if self._mic_duck_restore is None:
                        self._volume = prev * duck_factor
                        self._apply_volume()
                        self._mic_duck_restore = prev
                nar.render_emergency_mic(self._render,
                                         device_endpoint or None, mic_gain)
            except Exception:
                self._restore_mic_duck()
                raise
        except Exception:
            logging.getLogger("freq.player").exception(
                "emergency mic failed to start")
            return False
        if joining:
            return True   # second mic rides the first session's timer/callback
        # A mic segment IS the programme: when nothing else is playing,
        # give the UI a working timeline (position/duration) so the
        # progress bar advances. Without this the transport stays "not
        # playing" from the previous stop() and get_position() returns
        # 0 for the whole segment. When a song is still playing under the
        # emergency mic, its timeline is kept — the music never stopped.
        if not self._is_playing:
            self._is_playing = True
            self._is_paused = False
            self._current_file = None
            self._duration = max(0.0, duration)
            self._seek_offset = 0.0
            self._pause_offset = 0.0
            self._play_start_time = time.time()
        self._mic_live_stop.set()      # retire any previous sticky session
        self._mic_live_thread = None
        self._mic_live_stop.clear()
        self._mic_live_on_finish = on_finish
        self._mic_live_thread = threading.Thread(
            target=self._mic_live_timer, args=(max(0.0, duration),),
            daemon=True)
        self._mic_live_thread.start()
        return True

    def _restore_mic_duck(self) -> None:
        prev = self._mic_duck_restore
        self._mic_duck_restore = None
        if prev is not None:
            self._volume = prev
            self._apply_volume()

    def _mic_live_timer(self, duration: float) -> None:
        if duration <= 0:
            # Sticky emergency mode: stays open until stop()/shutdown sets
            # the event (or the user toggles the button off).
            self._mic_live_stop.wait()
            return
        if self._mic_live_stop.wait(duration):
            return   # transport stopped the mic — no finish callback
        cb = self._mic_live_on_finish
        self._mic_live_on_finish = None
        self.stop_emergency_mic()
        # A mic segment IS the programme: if the mic owned the transport
        # (no song underneath), its end is the track's end — clear the
        # playing state or the progress bar would freeze at full forever.
        if self._is_playing and self._current_file is None:
            self._is_playing = False
            self._current_file = None
        if cb is not None:
            try:
                cb()
            except Exception:
                logging.getLogger("freq.player").exception(
                    "emergency mic on_finish failed")

    def stop_emergency_mic(self) -> None:
        """Turn the live mic off and un-duck the music."""
        self._mic_live_stop.set()
        self._mic_live_thread = None
        self._mic_live_on_finish = None
        if self._engine == "wasapi" and self._render is not None:
            try:
                self._render_module.render_emergency_mic_stop(self._render)
            except Exception:
                pass
        self._restore_mic_duck()

    def emergency_mic_active(self) -> bool:
        if self._engine == "wasapi" and self._render is not None:
            try:
                return bool(self._render_module.render_emergency_mic_active(
                    self._render))
            except Exception:
                return False
        return False

    def seek_relative(self, delta: float) -> None:
        """Seek relative to current position (+delta forward, -delta backward)"""
        current = self.get_position()
        self.seek(current + delta)

    def _do_seek(self, filepath: str, seek_seconds: float, duration: float) -> None:
        """Internal: reload playback from filepath with updated offset tracking."""
        was_paused = self._is_paused
        self._progress_stop_event.set()
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        # Seeking jumps the timeline: a rendered/queued crossfade mix belongs
        # to the OLD timeline. stop() drops SDL's queued file — the pending
        # state must follow, or the handoff fires on stale data later.
        self._invalidate_crossfade()

        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            if was_paused:
                pygame.mixer.music.pause()
            self._current_file = filepath
            self._is_playing = True
            self._is_paused = was_paused
            self._seek_offset = seek_seconds
            self._play_start_time = time.time()
            self._pause_offset = 0.0
            self._duration = duration
            self._start_progress_monitor()
        except Exception as e:
            print(f"  ❌  Cannot play seeked file: {e}")

    def _get_file_duration(self) -> float:
        """Probe file duration — native decoder first, ffprobe fallback."""
        if not self._current_file or not os.path.isfile(self._current_file):
            return 0.0
        # Embedded decoder knows the exact frame count (works without ffmpeg).
        dec = self._open_native_decoder(self._current_file)
        if dec is not None:
            try:
                total = dec.total_frames
                if total > 0:
                    return total / 44100.0
            except Exception:
                pass
            finally:
                try:
                    dec.close()
                except Exception:
                    pass
        if not self._ffmpeg_available():
            return 0.0
        try:
            result = subprocess.run(
                [
                    _ffprobe_exe(), "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    self._current_file,
                ],
                capture_output=True, text=True, timeout=10,
            )
            return float(result.stdout.strip() or "0")
        except Exception:
            return 0.0

    @staticmethod
    def _ffmpeg_available() -> bool:
        """Check if ffmpeg is available (bundled, install dir, or PATH)."""
        exe = _ffmpeg_exe()
        if not exe:
            return False
        try:
            result = subprocess.run(
                [exe, "-version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, TypeError, OSError):
            return False

    def _cleanup_seek_files(self) -> None:
        """Remove temporary seek-trimmed files."""
        for fp in self._seek_trimmed_files:
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except Exception:
                pass
        self._seek_trimmed_files.clear()
        # Crossfade: dispose only the mix file WE rendered (never the
        # pending next-track source, which is the user's own music file).
        mixed = self._xf_mixed_path
        self._xf_mixed_path = None
        if mixed and mixed != self._current_file and os.path.isfile(mixed):
            try:
                os.remove(mixed)
            except Exception:
                pass

    @property
    def is_playing(self) -> bool:
        return self._is_playing and not self._is_paused

    @property
    def auto_cue(self) -> bool:
        """Whether playback skips leading silence when a track starts."""
        return self._auto_cue

    @auto_cue.setter
    def auto_cue(self, enabled: bool) -> None:
        self._auto_cue = bool(enabled)

    def _apply_auto_cue(self, filepath: str) -> tuple[float, Optional[str]]:
        """Measure + trim leading silence on ``filepath`` (auto-cue).

        Measures the silent head with the native silence detector (cached
        per file) and, when there is one, trims the file with ffmpeg —
        the same mechanism seek uses. Returns ``(seconds_skipped,
        replacement_path)``; ``(0.0, None)`` means play from the top.
        """
        try:
            from audio_meter import detect_leading_silence
            lead = detect_leading_silence(filepath)
        except Exception:
            return 0.0, None
        if lead <= 0.05:
            return 0.0, None
        if not self._ffmpeg_available() or not self._temp_dir:
            return 0.0, None
        try:
            cue_file = str(self._temp_dir / f"cue_{os.path.basename(filepath)}")
            cmd = [
                _ffmpeg_exe(), "-y", "-ss", f"{lead:.3f}", "-i", filepath,
                "-c:a", "libmp3lame", "-q:a", "2",
                "-avoid_negative_ts", "make_zero", cue_file,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode != 0 or not os.path.isfile(cue_file):
                return 0.0, None
            self._seek_trimmed_files.append(cue_file)
            print(f"  ⏱️  Auto-cue: skipping {lead:.2f}s of silence")
            return lead, cue_file
        except Exception as e:
            print(f"  ⚠️  Auto-cue failed: {e}")
            return 0.0, None

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_busy(self) -> bool:
        if not self.available:
            return False
        try:
            return pygame.mixer.music.get_busy()
        except Exception:
            return False

    @property
    def crossfade_enabled(self) -> bool:
        return self._crossfade_enabled

    @crossfade_enabled.setter
    def crossfade_enabled(self, enabled: bool) -> None:
        self._crossfade_enabled = bool(enabled)
        if not enabled:
            self.clear_next_track()

    @property
    def crossfade_duration(self) -> float:
        return self._crossfade_duration

    @crossfade_duration.setter
    def crossfade_duration(self, seconds: float) -> None:
        try:
            self._crossfade_duration = max(1.0, float(seconds))
        except (TypeError, ValueError):
            pass

    @property
    def crossfade_active(self) -> bool:
        """True while a crossfade mix is queued and playing on the music channel."""
        return self._xf_active

    def set_next_track(
        self, filepath: str, duration: float = 0.0, index: int = -1,
        handoff: Optional[Callable] = None,
    ) -> None:
        """Queue the next track so the player can crossfade into it.

        Call after the current track has started playing. When the current
        track approaches its end, the engine renders an equal-power overlap
        of the current track's tail into the next track's head (C++ mixer,
        loudness-matched gains), queues the result behind the current
        output with ``pygame.mixer.music.queue`` — SDL switches between the
        two buffers with zero gap — and fires ``handoff(b_offset, index)``
        right at the switch so the caller can update queue/UI state.
        """
        if not self.available or not os.path.isfile(filepath):
            return
        with self._lock:
            self._xf_pending_path = filepath
            self._xf_b_source = filepath
            self._xf_pending_offset = 0.0
            self._xf_pending_duration = duration
            self._xf_pending_index = index
            self._xf_prepared = False
            self._xf_preloading = False
            self._xf_handoff = handoff

    def clear_next_track(self) -> None:
        """Drop a queued crossfade track (e.g. queue order changed)."""
        with self._lock:
            if not self._xf_armed:
                self._xf_pending_path = None
                self._xf_pending_index = -1
                self._xf_prepared = False
                self._xf_preloading = False

    def _invalidate_crossfade(self) -> None:
        """Drop any pending/armed crossfade mix.

        Seeking (or any timeline jump) invalidates a rendered mix: the
        queued file belongs to the OLD timeline and must never hand off —
        otherwise a stale mix plays and the queue advances on wrong data.
        """
        with self._lock:
            self._xf_pending_path = None
            self._xf_pending_offset = 0.0
            self._xf_pending_duration = 0.0
            self._xf_pending_index = -1
            self._xf_prepared = False
            self._xf_preloading = False
            self._xf_armed = False
            self._xf_active = False
            self._xf_handoff_fired = False
            self._xf_handoff = None

    def _render_and_arm_crossfade(self) -> None:
        """Worker: build the overlap file, load it, queue it on SDL."""
        with self._lock:
            pending = self._xf_pending_path
            index = self._xf_pending_index
            reported = self._xf_pending_duration
            if not pending:
                return
            self._xf_preloading = True
            current = self._current_file
            offset = self._seek_offset
        if not pending:
            return
        try:
            from audio_meter import build_crossfade_file, measure_file_lufs
            pos = offset + (time.time() - self._play_start_time
                            if not self._is_paused else self._pause_offset)
            remaining = self._duration - pos if self._duration > 0 else 0.0
            overlap = max(1.0, min(self._crossfade_duration,
                                   max(0.5, remaining - 0.2)))
            if self._duration > 0 and remaining - overlap < 0.1:
                overlap = max(0.5, remaining * 0.5)
            ga = gb = 1.0
            if current and os.path.isfile(current):
                la = measure_file_lufs(current)
                lb = measure_file_lufs(pending)
                if math.isfinite(la) and math.isfinite(lb):
                    gb = 10 ** ((la - lb) / 20.0)
                    gb = max(0.5, min(2.0, gb))
                    ga = 1.0
            # WASAPI engine: stream B's chunks faded into the live ring —
            # the two tracks genuinely share one output clock.
            if self._engine == "wasapi":
                with self._lock:
                    if not self._start_b_decoder(pending):
                        self._xf_pending_path = None
                        self._xf_pending_index = -1
                        self._xf_preloading = False
                        return
                    self._xf_pending_offset = 0.0
                    self._xf_pending_duration = reported
                    self._xf_pending_index = index
                    # The gain ramp must finish exactly when B ends — clamp
                    # the window to B's length or a short B leaves A's tail
                    # playing un-faded after the fade "ended".
                    fade_secs = min(overlap, reported) if reported > 0 else overlap
                    self._xf_fade_total = int(fade_secs * 44100)
                    self._xf_fade_pos = 0
                    self._xf_gain_b = gb
                    # NOTE: _xf_handoff was already stored by set_next_track.
                    self._xf_preloading = False
                    self._xf_armed = True
                print(f"  🎚️  Crossfade armed (WASAPI): overlap {overlap:.1f}s, "
                      f"gain B ×{gb:.2f}")
                return
            res = build_crossfade_file(current or "", pending, overlap,
                                       gain_a=ga, gain_b=gb)
            if not res:
                with self._lock:
                    self._xf_pending_path = None
                    self._xf_pending_index = -1
                return
            xf_path, b_offset = res
            with self._lock:
                # Re-check state: seek/stop/new track during the render
                # invalidates the handoff. Dispose the fresh mix file.
                if (self._xf_pending_path != pending
                        or not self._is_playing
                        or self._xf_armed):
                    try:
                        os.remove(xf_path)
                    except Exception:
                        pass
                    return
                try:
                    # NOTE: queue() alone — a load() here would stop the
                    # currently playing track and idle the mixer.
                    pygame.mixer.music.queue(xf_path)
                except Exception:
                    self._xf_pending_path = None
                    self._xf_pending_index = -1
                    try:
                        os.remove(xf_path)
                    except Exception:
                        pass
                    return
                self._xf_mixed_path = xf_path
                self._xf_pending_offset = b_offset
                self._xf_pending_path = xf_path
                # B shorter than the overlap window → negative duration;
                # clamp so the switch guard keeps the old (valid) duration.
                self._xf_pending_duration = max(0.0, reported - b_offset)
                self._xf_prepared = True
                self._xf_preloading = False
                self._xf_armed = True
                self._xf_active = True
                self._xf_handoff_fired = False
                print(f"  🎚️  Crossfade armed: overlap {overlap:.1f}s, "
                      f"gain B ×{gb:.2f}")
        except Exception as e:
            print(f"  ⚠️  Crossfade render failed: {e}")
            with self._lock:
                self._xf_pending_path = None
                self._xf_pending_index = -1
                self._xf_preloading = False

    def _check_crossfade_trigger(self) -> None:
        """Called by the progress monitor — starts the render near the end."""
        if not self._crossfade_enabled:
            return
        with self._lock:
            if (not self._is_playing or self._is_paused
                    or self._xf_prepared or self._xf_preloading
                    or self._xf_armed or self._xf_active
                    or not self._xf_pending_path):
                return
            if self._duration <= 0:
                return
            pos = self._seek_offset + (time.time() - self._play_start_time)
            remaining = self._duration - pos
            # Start early enough that the render (1-2 s) finishes before the
            # mix point; never before 3/4 of the track has actually played.
            if remaining > self._crossfade_duration + 1.5:
                return
            if remaining < self._crossfade_duration * 0.35:
                return  # too late — let the normal end path handle it
            self._xf_preloading = True
        threading.Thread(
            target=self._render_and_arm_crossfade, daemon=True
        ).start()

    def _check_crossfade_switch(self) -> None:
        """Detect that SDL switched from track A into the queued mix (B).

        Re-anchors the wall-clock timeline to B (offset, duration, original
        file for future seeks) and fires the handoff so the GUI can advance
        the queue exactly when B becomes audible.
        """
        with self._lock:
            if not self._xf_armed or self._xf_handoff_fired or self._is_paused:
                return
            pos = self._seek_offset + (time.time() - self._play_start_time)
            if pos < self._duration - 0.15:
                return
            self._seek_offset = self._xf_pending_offset
            self._play_start_time = time.time()
            if self._xf_pending_duration > 0:
                self._duration = self._xf_pending_duration
            self._current_file = self._xf_pending_path
            if self._xf_b_source:
                self._original_file = self._xf_b_source
                self._xf_b_source = None
            self._xf_armed = False
            self._xf_active = False
        self._schedule_auto_gain(self._original_file)
        self._finish_crossfade()

    def _finish_crossfade(self) -> None:
        """The mixed file ended — hand off to the GUI for queue/UI updates.

        Keeps ``_is_playing`` true while handing off so the SDL mixer is
        never torn down between the queued file ending and the next
        ``play_file`` call (which would reopen the audio device).
        """
        with self._lock:
            if self._xf_handoff_fired:
                return
            self._xf_handoff_fired = True
            handoff = self._xf_handoff
            b_offset = self._xf_pending_offset
            index = self._xf_pending_index
            self._xf_handoff = None
            self._xf_active = False
            self._xf_armed = False
            self._xf_prepared = False
            self._xf_pending_path = None
            self._xf_pending_index = -1
        if handoff:
            try:
                handoff(b_offset, index)
            except Exception as e:
                print(f"  ⚠️  Crossfade handoff failed: {e}")

    # ─────────────────────────────────────
    # Progress Monitor
    # ─────────────────────────────────────
    def _start_progress_monitor(self) -> None:
        """Monitor playback progress and auto-advance"""
        # Signal the previous monitor, but do not wait while holding the
        # playback lock. The previous monitor will exit on its own.
        self._progress_stop_event.set()
        stop_event = threading.Event()
        self._progress_stop_event = stop_event

        def _monitor():
            started_at = time.monotonic()
            while not stop_event.wait(0.25):
                if not self._is_playing or self._is_paused:
                    continue
                if self._engine == "wasapi":
                    # C++ engine: pump chunk crossfades and end detection.
                    self._pump_render()
                    continue
                # pygame can briefly report False from get_busy() while a
                # newly loaded file is being decoded. This matters most for
                # short jingles.
                # Crossfade: start the overlap render near the track's end,
                # then detect the seamless SDL switch into the queued mix.
                self._check_crossfade_trigger()
                self._check_crossfade_switch()
                if not self.is_busy and time.monotonic() - started_at >= 0.5:
                    # Song finished
                    self._is_playing = False
                    self._is_paused = False
                    self._current_file = None
                    if self._on_finish:
                        self._on_finish()
                    break

        self._progress_thread = threading.Thread(target=_monitor, daemon=True)
        self._progress_thread.start()


# Global player instance
player = AudioPlayer()
