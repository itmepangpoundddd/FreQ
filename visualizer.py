#!/usr/bin/env python3
"""
Real-time Audio Visualizer for FreQ
Displays spectrum analyzer while playing audio.

Primary input is raw s16le stereo PCM (fed from the streaming engine through
audio_meter). FFT computation happens in the native C++ module when available
and falls back to pure Python otherwise, so the visualizer works without numpy.
"""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from collections import deque

from audio_meter import compute_spectrum, samples_to_stereo_pcm


class SpectrumAnalyzer:
    """Audio spectrum analyzer working on raw PCM bytes."""

    def __init__(self, sample_rate: int = 44100, buffer_size: int = 1024):
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.bins = buffer_size // 2

    def compute_from_pcm(self, audio_data: bytes) -> list[float]:
        """Compute 32 log-frequency bars (0.0-1.0) from s16le stereo PCM."""
        if not audio_data:
            return [0.0] * 32
        try:
            return compute_spectrum(audio_data)
        except Exception:
            return [0.0] * 32

    def compute_spectrum(self, audio_data) -> list[float]:
        """Legacy entry point — accepts PCM bytes or an iterable of samples."""
        if isinstance(audio_data, (bytes, bytearray, memoryview)):
            return self.compute_from_pcm(bytes(audio_data))
        # Legacy path: list of float samples → s16le stereo PCM. The
        # quantization/clipping happens in C++ when the native module is
        # available (one pass, no per-sample Python loop).
        try:
            stereo = samples_to_stereo_pcm(
                list(audio_data)[: self.buffer_size]
            )
        except Exception:
            return [0.0] * 32
        return self.compute_from_pcm(stereo)


class VisualizerWidget:
    """Tkinter canvas widget for audio visualization."""

    def __init__(self, parent, width: int = 800, height: int = 200,
                 bar_count: int = 32, theme: dict = None):
        self.width = width
        self.height = height
        self.bar_count = bar_count
        self.theme = theme or {
            "bg": "#0a0e14",
            "bar1": "#58a6ff",
            "bar2": "#bc8cff",
            "bar3": "#f778ba",
            "peak": "#ffffff",
        }

        self.canvas = tk.Canvas(
            parent, width=width, height=height,
            bg=self.theme["bg"], highlightthickness=0
        )

        # Bar state for smooth animation
        self._bar_values = [0.0] * bar_count
        self._bar_peaks = [0.0] * bar_count
        self._peak_hold = [0] * bar_count
        self._spectrum = SpectrumAnalyzer()
        self._last_update = 0.0
        self._lock = threading.Lock()

        # Gradient colors for bars
        self._colors = self._create_gradient(bar_count)

        # Animation
        self._running = False
        self._fps = 60
        self._frame_time = 1000 // self._fps

    def _create_gradient(self, count: int) -> list[str]:
        """Create gradient colors for bars."""
        colors = []
        for i in range(count):
            ratio = i / max(count - 1, 1)
            if ratio < 0.5:
                # Blue to purple
                r = int(88 + (188 - 88) * (ratio * 2))
                g = int(166 + (140 - 166) * (ratio * 2))
                b = int(255 + (255 - 255) * (ratio * 2))
            else:
                # Purple to pink
                r = int(188 + (247 - 188) * ((ratio - 0.5) * 2))
                g = int(140 + (120 - 140) * ((ratio - 0.5) * 2))
                b = int(255 + (186 - 255) * ((ratio - 0.5) * 2))
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        return colors

    def pack(self, **kwargs):
        """Pack the canvas widget."""
        self.canvas.pack(**kwargs)

    def grid(self, **kwargs):
        """Grid the canvas widget."""
        self.canvas.grid(**kwargs)

    def update_pcm(self, pcm_bytes: bytes) -> None:
        """Update bars from raw s16le stereo PCM (preferred entry point)."""
        raw = self._spectrum.compute_from_pcm(pcm_bytes)
        self._apply_bars(raw)

    def update_spectrum(self, audio_samples):
        """Update spectrum with new audio data (bytes or legacy samples)."""
        if isinstance(audio_samples, (bytes, bytearray, memoryview)):
            self.update_pcm(bytes(audio_samples))
            return
        raw = self._spectrum.compute_spectrum(audio_samples)
        self._apply_bars(raw)

    def _apply_bars(self, raw: list[float]) -> None:
        """Merge computed band values into the animated bar state."""
        if not raw:
            return
        with self._lock:
            self._last_update = time.monotonic()
            # Upsample/downsample the computed bands to the widget bar count.
            step = len(raw) / self.bar_count
            for i in range(self.bar_count):
                lo = int(i * step)
                hi = max(lo + 1, int((i + 1) * step))
                target = max(raw[lo:hi]) if lo < len(raw) else 0.0
                # Smooth attack, faster decay
                if target > self._bar_values[i]:
                    self._bar_values[i] = target * 0.8 + self._bar_values[i] * 0.2
                else:
                    self._bar_values[i] = target * 0.3 + self._bar_values[i] * 0.7

    def _decay_if_stale(self) -> None:
        """Let bars fall smoothly when no audio has arrived recently."""
        with self._lock:
            if time.monotonic() - self._last_update > 0.15:
                for i in range(self.bar_count):
                    self._bar_values[i] *= 0.85

    def _draw(self):
        """Draw the spectrum bars."""
        self._decay_if_stale()
        self.canvas.delete("all")

        bar_width = max(1, (self.width - (self.bar_count + 1) * 2) // self.bar_count)
        gap = 2
        max_bar_height = self.height - 20

        for i in range(self.bar_count):
            x = i * (bar_width + gap) + gap
            value = min(1.0, self._bar_values[i])
            bar_height = int(value * max_bar_height)

            # Draw bar
            y_top = self.height - bar_height - 10
            y_bottom = self.height - 10

            if bar_height > 0:
                self.canvas.create_rectangle(
                    x, y_top, x + bar_width, y_bottom,
                    fill=self._colors[i], outline=""
                )

            # Draw peak
            if value > self._bar_peaks[i]:
                self._bar_peaks[i] = value
                self._peak_hold[i] = 30  # Hold for 30 frames
            else:
                if self._peak_hold[i] > 0:
                    self._peak_hold[i] -= 1
                else:
                    self._bar_peaks[i] = max(0, self._bar_peaks[i] - 0.02)

            peak_height = int(self._bar_peaks[i] * max_bar_height)
            if peak_height > 0:
                peak_y = self.height - peak_height - 10
                self.canvas.create_rectangle(
                    x, peak_y - 2, x + bar_width, peak_y,
                    fill=self.theme["peak"], outline=""
                )

    def start(self):
        """Start the visualization animation."""
        self._running = True
        self._animate()

    def stop(self):
        """Stop the visualization animation."""
        self._running = False

    def _animate(self):
        """Animation loop — a draw error must not kill the loop, and the
        loop must not outlive a destroyed canvas (torn-down app)."""
        if not self._running:
            return

        try:
            self._draw()
        except Exception:
            # One bad frame is not fatal; log and keep animating.
            import logging
            logging.getLogger("freq").exception("visualizer frame failed")
        try:
            self.canvas.after(self._frame_time, self._animate)
        except Exception:
            # Canvas destroyed (app closing) — stop quietly.
            self._running = False

    def clear(self):
        """Clear the display."""
        with self._lock:
            self._bar_values = [0.0] * self.bar_count
            self._bar_peaks = [0.0] * self.bar_count
            self._peak_hold = [0] * self.bar_count
        self._draw()
