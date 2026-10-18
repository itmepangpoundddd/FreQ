#!/usr/bin/env python3
"""Dual Output [dual-0927] — mirror the broadcast mix to a second device.

Send EVERYTHING the main output plays (music, live mics, EQ/master chain,
auto gain — the full program) to a second speaker at the same time: a
monitor in another room, a co-host's headset, a second interface, …

How it works — the playback path is NOT touched:
  1. the main device plays exactly as before (WASAPI engine or pygame);
  2. a native WASAPI LOOPBACK capture taps the main device's mix
     (the same tap the VU meter uses);
  3. a SECOND native render engine (its own C++ instance, so it can run
     on a different endpoint than the music engine) plays the tapped
     PCM on the chosen second device.

The tap delivers stereo s16le frames and ``render_write`` consumes
stereo s16le frames (it divides byte count by 4), so PCM flows through
without conversion. When the main device's mix rate differs from the
engine's canonical 44.1 kHz, a small linear-interpolation resampler in
this module converts (48 kHz taps are the common case).

Everything runs on one background pump thread ("freq-dual-output"); the
UI thread only starts/stops it. ``stop()`` is idempotent and safe from
``_on_close``.
"""

from __future__ import annotations

import logging
import threading
from array import array
from typing import Optional


class DualOutputMirror:
    """Tap the main output device and re-play it on a second endpoint."""

    TARGET_RATE = 44100          # native_audio_render canonical rate
    TARGET_CHANNELS = 2
    CHUNK = 65536                # bytes per tap read (== render_write cap)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mirror = None       # native_audio_render engine capsule
        self._module = None       # the imported module
        self._tap = None          # native_audio_capture Capture (loopback)
        self._endpoint: Optional[str] = None
        self._volume: float = 1.0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False
        self._last_error = ""

    # ─────────────────────────────────────
    # State
    # ─────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._running

    @property
    def endpoint(self) -> Optional[str]:
        return self._endpoint

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, v: float) -> None:
        self._volume = max(0.0, min(2.0, float(v)))
        with self._lock:
            if self._mirror is not None and self._module is not None:
                try:
                    self._module.render_set_volume(self._mirror, self._volume)
                except Exception:
                    pass

    def stats(self) -> dict:
        """Snapshot for settings/status displays (no native calls)."""
        with self._lock:
            return {
                "running": self._running,
                "endpoint": self._endpoint,
                "volume": self._volume,
                "error": self._last_error,
            }

    # ─────────────────────────────────────
    # Control (UI thread)
    # ─────────────────────────────────────
    def start(self, endpoint_id: str, volume: float = 1.0) -> bool:
        """Open the second engine on ``endpoint_id`` and start mirroring."""
        if not endpoint_id:
            return False
        self.stop()
        try:
            import native_audio_render as nar
        except Exception as err:
            self._last_error = f"native_audio_render unavailable: {err}"
            return False
        try:
            import native_audio_capture as nac
        except Exception as err:
            self._last_error = f"native_audio_capture unavailable: {err}"
            return False
        engine = None
        tap = None
        try:
            engine = nar.render_new()
            nar.render_set_volume(engine, max(0.0, min(2.0, volume)))
            nar.render_start(engine, endpoint_id)   # raises on failure
            # LOOPBACK tap of the MAIN (default) device — the mirror
            # follows everything that device plays, FreQ or not.
            tap = nac.Capture(loopback=True)
            tap.start()
        except Exception as err:
            self._last_error = str(err)
            try:
                if tap is not None:
                    tap.stop()
            except Exception:
                pass
            try:
                if engine is not None:
                    nar.render_stop(engine)
            except Exception:
                pass
            return False
        with self._lock:
            self._module = nar
            self._mirror = engine
            self._tap = tap
            self._endpoint = endpoint_id
            self._volume = max(0.0, min(2.0, volume))
            self._last_error = ""
            self._stop.clear()
            self._running = True
        self._thread = threading.Thread(
            target=self._pump, name="freq-dual-output", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the pump and release both engines. Idempotent."""
        with self._lock:
            engine, tap, module = self._mirror, self._tap, self._module
            self._mirror = None
            self._tap = None
            self._module = None
            self._endpoint = None
            self._running = False
            self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        if tap is not None:
            try:
                tap.stop()
            except Exception:
                pass
        if engine is not None and module is not None:
            try:
                module.render_stop(engine)
            except Exception:
                pass

    def change_endpoint(self, endpoint_id: str) -> bool:
        """Hot-swap the second device while running (brief gap expected)."""
        if not endpoint_id:
            return False
        with self._lock:
            module, engine, volume = self._module, self._mirror, self._volume
        if engine is None:
            return self.start(endpoint_id, volume)
        try:
            module.render_stop(engine)
            module.render_set_volume(engine, volume)
            module.render_start(engine, endpoint_id)
        except Exception as err:
            self._last_error = str(err)
            return False
        with self._lock:
            self._endpoint = endpoint_id
        return True

    # ─────────────────────────────────────
    # Pump (background thread)
    # ─────────────────────────────────────
    def _pump(self) -> None:
        """Move PCM: main-device loopback tap → second render engine."""
        log = logging.getLogger("freq.dual_output")
        while not self._stop.wait(0.02):
            with self._lock:
                engine, tap, module = self._mirror, self._tap, self._module
            if engine is None or tap is None:
                continue
            try:
                rate = tap.samplerate
            except Exception:
                rate = self.TARGET_RATE
            try:
                data = tap.read(self.CHUNK)   # bytes | None (s16le stereo)
                if data:
                    if rate != self.TARGET_RATE:
                        data = self._resample(data, rate)
                    if data:
                        module.render_write(engine, data)
            except Exception as err:
                # One failed read/write must not kill the pump; a dead
                # engine is repaired by the next start/change_endpoint.
                self._last_error = str(err)
                log.debug("dual output pump: %s", err)

    def _resample(self, data: bytes, in_rate: int) -> bytes:
        """Linear-interpolation resampler for non-44.1k taps (48k etc).

        Runs on the pump thread; per-chunk stateless — monitoring
        quality, not mastering quality. Output frame count scales by
        TARGET/in_rate so the same wall-clock duration is preserved.
        """
        frames = len(data) // 4
        if frames < 2:
            return b""
        out_frames = max(1, int(frames * self.TARGET_RATE / in_rate))
        src = array("h")
        src.frombytes(data[: frames * 4])
        dst = array("h", bytes(out_frames * 4))
        step = (frames - 1) / out_frames
        for i in range(out_frames):
            pos = i * step
            i0 = int(pos)
            if i0 >= frames - 1:
                i0 = frames - 2
            frac = pos - i0
            l0 = src[i0 * 2]
            r0 = src[i0 * 2 + 1]
            l1 = src[i0 * 2 + 2]
            r1 = src[i0 * 2 + 3]
            dst[i * 2] = int(l0 + (l1 - l0) * frac)
            dst[i * 2 + 1] = int(r0 + (r1 - r0) * frac)
        return dst.tobytes()
