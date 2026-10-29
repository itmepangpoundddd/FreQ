#!/usr/bin/env python3
"""Off-air auditions for sources that are not a file.

The queue's Preview button covers three very different things:

* a file (a local WAV/MP3 or a cached YouTube download) — decoded by the
  native editor core, see `audio_editor.EditSession`;
* a **live signal** (`.m3u8`, or any URL ffmpeg can open) — demuxed by
  ffmpeg into raw PCM and pushed into a render ring;
* a **microphone** — captured and live-mixed into that same ring by the
  native core (`render_emergency_mic`).

Both of the last two need a render engine of their own, started on the
**monitor** endpoint, so the program output keeps its own device and an
audition during a live broadcast is heard only by the operator.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Optional

try:
    import native_audio_render as _nar
except Exception:  # pragma: no cover - only without a native build
    _nar = None

FRAME_BYTES = 4          # s16le stereo
RATE = 44100
MAX_MONITOR_MICS = 4     # the engine's live-mic ceiling, same as the player's


def native_available() -> bool:
    return _nar is not None


def output_endpoints() -> list:
    """``[{'id': …, 'name': …}]`` — the WASAPI render endpoints, if known."""
    try:
        import native_audio_output as nao
        return list(nao.list_output_devices())
    except Exception:
        return []


def endpoint_for_name(monitor_name: str) -> str:
    """The WASAPI id of the monitor device the editor was told to use.

    The editor picks a device by *name* (its own enumeration); the render
    engine starts one by *endpoint id*. Matching the names is enough in
    practice, and an unknown/empty name means "the system default", which is
    exactly what the editor's own preview falls back to.
    """
    wanted = str(monitor_name or "").strip()
    if not wanted:
        return ""
    for dev in output_endpoints():
        if str(dev.get("name") or "") == wanted:
            return str(dev.get("id") or "")
    # A truncated/annotated name still identifies one device well enough.
    for dev in output_endpoints():
        name = str(dev.get("name") or "")
        if wanted and (wanted in name or name in wanted):
            return str(dev.get("id") or "")
    return ""


class MonitorAudition:
    """A live signal or a microphone playing on the monitor endpoint only."""

    def __init__(self) -> None:
        self._engine = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._kind = ""              # "stream" | "mic"
        self._mic_count = 0
        self._started = 0.0
        self._frames = 0
        self._lock = threading.Lock()

    # ── State ──────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._engine is not None

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def mic_count(self) -> int:
        """How many microphones the current audition opened."""
        return self._mic_count

    @property
    def ended(self) -> bool:
        """True once a stream's feeder stopped on its own (link dropped)."""
        if self._kind != "stream":
            return False
        thread = self._thread
        return thread is not None and not thread.is_alive()

    @property
    def position(self) -> float:
        """Seconds of audio handed to the monitor device."""
        if self._kind == "mic":
            return max(0.0, time.monotonic() - self._started)
        return self._frames / float(RATE)

    # ── Start ──────────────────────────────────────────────────────
    def start_stream(self, url: str, monitor_name: str = "",
                     volume: float = 0.8) -> tuple:
        """Audition a live link. Returns ``(ok, detail)``."""
        if _nar is None:
            return False, "the native render engine is not built"
        import player as player_mod

        exe = player_mod._ffmpeg_exe()
        if not exe or not (os.path.isfile(exe) or shutil.which(exe)):
            return False, "FFmpeg is not installed"
        self.stop()
        endpoint = endpoint_for_name(monitor_name)
        try:
            engine = _nar.render_new()
            _nar.render_start(engine, endpoint or None)
            _nar.render_set_volume(engine, max(0.0, min(1.0, float(volume))))
        except Exception as err:
            return False, f"the monitor device could not be opened ({err})"
        cmd = [exe, "-v", "quiet",
               *player_mod._STREAM_IN_FLAGS,
               "-i", str(url), "-ac", "2", "-ar", str(RATE),
               "-f", "s16le", "-y", "pipe:1"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
        except Exception as err:
            try:
                _nar.render_stop(engine)
            except Exception:
                pass
            return False, f"the link could not be opened ({err})"
        self._engine = engine
        self._proc = proc
        self._kind = "stream"
        self._started = time.monotonic()
        self._frames = 0
        self._stop = threading.Event()
        stop_event = self._stop
        self._thread = threading.Thread(
            target=self._pump, args=(engine, proc, stop_event),
            name="freq-monitor-audition", daemon=True)
        self._thread.start()
        return True, ""

    def _pump(self, engine, proc, stop_event: threading.Event) -> None:
        chunk = RATE * FRAME_BYTES        # 1 s of stereo s16le
        try:
            while not stop_event.is_set():
                data = proc.stdout.read(chunk)
                if not data:
                    break
                off = 0
                while off < len(data) and not stop_event.is_set():
                    written = _nar.render_write(engine, data[off:])
                    if written == 0:
                        time.sleep(0.03)      # ring full — realtime pacing
                        continue
                    off += written * FRAME_BYTES
                    with self._lock:
                        self._frames += written
        except Exception:
            pass
        finally:
            for closer in (proc.stdout.close, proc.kill):
                try:
                    closer()
                except Exception:
                    pass

    def start_mic(self, monitor_name: str = "", volume: float = 0.8,
                  mics=None) -> tuple:
        """Audition one or more microphones on the monitor endpoint (no air).

        ``mics`` is ``[(capture_endpoint_id_or_None, gain), …]`` — exactly the
        spec list the queue item would air, so an operator can monitor the
        same microphones the segment will use. ``None`` means the OS default
        capture device.
        """
        if _nar is None:
            return False, "the native render engine is not built"
        specs = list(mics or [(None, 1.0)])[:MAX_MONITOR_MICS]
        if not specs:
            specs = [(None, 1.0)]
        self.stop()
        endpoint = endpoint_for_name(monitor_name)
        engine = None
        opened = 0
        try:
            engine = _nar.render_new()
            _nar.render_start(engine, endpoint or None)
            _nar.render_set_volume(engine, max(0.0, min(1.0, float(volume))))
            for mic_endpoint, gain in specs:
                try:
                    _nar.render_emergency_mic(engine, mic_endpoint or None,
                                              max(0.5, min(4.0, float(gain))))
                    opened += 1
                except Exception:
                    continue
            if opened == 0:
                raise RuntimeError("no configured microphone could be opened")
        except Exception as err:
            try:
                if engine is not None:
                    _nar.render_emergency_mic_stop(engine)
                    _nar.render_stop(engine)
            except Exception:
                pass
            return False, f"the microphone could not be opened ({err})"
        self._engine = engine
        self._proc = None
        self._kind = "mic"
        self._mic_count = opened
        self._started = time.monotonic()
        self._frames = 0
        return True, ""

    # ── Stop ───────────────────────────────────────────────────────
    def stop(self) -> None:
        """Release the monitor engine, the feeder and the ffmpeg child."""
        try:
            self._stop.set()
        except Exception:
            pass
        proc = self._proc
        self._proc = None
        if proc is not None:
            for closer in (proc.kill, proc.stdout.close):
                try:
                    closer()
                except Exception:
                    pass
        engine = self._engine
        self._engine = None
        if engine is not None and _nar is not None:
            for call in (lambda: _nar.render_emergency_mic_stop(engine),
                         lambda: _nar.render_stop(engine)):
                try:
                    call()
                except Exception:
                    pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=1.5)
            except Exception:
                pass
        self._kind = ""
        self._mic_count = 0
        self._frames = 0
