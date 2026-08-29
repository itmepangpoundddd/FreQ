#!/usr/bin/env python3
"""
Real-time Audio Visualizer for FreQ
Displays spectrum analyzer while playing audio.
"""

from __future__ import annotations

import math
import struct
import threading
import tkinter as tk
from collections import deque
from typing import Optional

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


class SpectrumAnalyzer:
    """Audio spectrum analyzer using FFT."""
    
    def __init__(self, sample_rate: int = 44100, buffer_size: int = 1024):
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.bins = buffer_size // 2
        self._window = self._hann_window(buffer_size)
    
    @staticmethod
    def _hann_window(size: int):
        """Create Hann window function for FFT."""
        if NUMPY_AVAILABLE:
            return np.hanning(size)
        return [0.5 * (1 - math.cos(2 * math.pi * i / size)) for i in range(size)]
    
    def compute_spectrum(self, audio_data) -> list[float]:
        """
        Compute frequency spectrum from audio samples.
        Returns list of magnitudes (0-1 normalized).
        """
        if NUMPY_AVAILABLE:
            data = np.array(audio_data, dtype=np.float64)
            if len(data) < self.buffer_size:
                data = np.pad(data, (0, self.buffer_size - len(data)))
            else:
                data = data[:self.buffer_size]
            
            # Apply window
            windowed = data * self._window
            
            # FFT
            fft = np.fft.rfft(windowed)
            magnitudes = np.abs(fft)
            
            # Normalize
            if magnitudes.max() > 0:
                magnitudes = magnitudes / magnitudes.max()
            
            return magnitudes.tolist()
        else:
            # Simple fallback without numpy
            return [0.0] * (self.buffer_size // 2 + 1)


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
    
    def update_spectrum(self, audio_samples):
        """Update spectrum with new audio data."""
        raw = self._spectrum.compute_spectrum(audio_samples)
        
        # Downsample to bar_count
        if len(raw) > self.bar_count:
            step = len(raw) // self.bar_count
            for i in range(self.bar_count):
                chunk = raw[i * step:(i + 1) * step]
                target = sum(chunk) / len(chunk) if chunk else 0
                # Smooth attack, faster decay
                if target > self._bar_values[i]:
                    self._bar_values[i] = target * 0.8 + self._bar_values[i] * 0.2
                else:
                    self._bar_values[i] = target * 0.3 + self._bar_values[i] * 0.7
    
    def _draw(self):
        """Draw the spectrum bars."""
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
        """Animation loop."""
        if not self._running:
            return
        
        self._draw()
        self.canvas.after(self._frame_time, self._animate)
    
    def clear(self):
        """Clear the display."""
        self._bar_values = [0.0] * self.bar_count
        self._bar_peaks = [0.0] * self.bar_count
        self._peak_hold = [0] * self.bar_count
        self._draw()
