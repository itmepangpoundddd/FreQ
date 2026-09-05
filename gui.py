#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║            🎙️  FreQ — Native Desktop GUI                   ║
║     Desktop Radio Playlist Manager          ║
╚══════════════════════════════════════════════════════════════╝

dependencies:
    pip install customtkinter yt-dlp

Run:
    python gui.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import Optional

import customtkinter as ctk

# ── Drag-and-drop (optional) ──
try:
    from tkinterdnd2 import DnD  # type: ignore
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

from radio_manager import (
    RadioQueue,
    Song,
    SongStatus,
    YouTubeManager,
    generate_demo_songs,
)
from audio import AudioDeviceManager
from player import AudioPlayer
from mic_recorder import record_wav, SOUNDDEVICE_AVAILABLE
from cache import cache as song_cache
from icons import cached_icon, get_tk_image, ICON_PATHS
from tooltips import ToolTip
from streaming import streamer, StreamProtocol, StreamState, FFMPEG_AVAILABLE, WEBRTC_AVAILABLE
from keys_tab import build_keys_tab, DEFAULT_KEYS as _DEFAULT_KEYS
from log_handler import setup_logging, memory_handler
from visualizer import VisualizerWidget
from scheduler import Scheduler, ScheduleAction, ScheduleEvent
from livelan import LiveLAN
from titlebar import CustomTitleBar
from update_checker import (
    check_for_updates_async, open_download_page, format_release_notes,
    UpdateInfo, CURRENT_VERSION
)

from features import (
    PlaybackModes, RepeatMode, CrossfadeSettings, JingleConfig,
    MicConfig, CommercialBreak, VolumeNormalizer, EQSettings,
    AudioEditor, ThemeManager, EditResult, NationalAnthem,
)

# ═══════════════════════════════════════════════════════════════
# Theme
# ═══════════════════════════════════════════════════════════════

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg":           "#0a0e14",
    "bg_card":      "#131920",
    "bg_hover":     "#1a2230",
    "bg_selected":  "#1c3050",
    "bg_input":     "#0d1219",
    "border":       "#262d38",
    "border_focus": "#3d4a5c",
    "text":         "#e8edf4",
    "text2":        "#8b95a5",
    "text3":        "#4e5769",
    "accent":       "#58a6ff",
    "accent_hover": "#79b8ff",
    "green":        "#3fb950",
    "green_dark":   "#238636",
    "red":          "#f85149",
    "red_dark":     "#b62324",
    "orange":       "#d29922",
    "purple":       "#bc8cff",
    "playing_bg":   "#0f1e30",
    "playing_border": "#1f4e7f",
    "selected_border": "#3d4a5c",
    "spectrum_1":   "#58a6ff",
    "spectrum_2":   "#bc8cff",
    "spectrum_3":   "#f778ba",
}


# ═══════════════════════════════════════════════════════════════
# Custom Widgets
# ═══════════════════════════════════════════════════════════════

class CollapsibleSection(ctk.CTkFrame):
    """A collapsible accordion section with header toggle."""

    def __init__(self, parent, title: str, icon: str = "▶",
                 fg_color=COLORS["bg_card"], **kwargs):
        super().__init__(parent, fg_color=fg_color, corner_radius=8, **kwargs)

        self._expanded = True
        self._icon = icon
        self._title = title

        # Header (clickable)
        self._header = ctk.CTkFrame(self, fg_color="transparent", corner_radius=8)
        self._header.pack(fill="x", padx=4, pady=(4, 0))

        self._toggle_btn = ctk.CTkButton(
            self._header, text=f"{icon} {title}", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            text_color=COLORS["accent"], height=30,
            corner_radius=6, command=self._toggle,
        )
        self._toggle_btn.pack(fill="x")

        # Content frame
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.content.pack(fill="both", expand=True)
            self._toggle_btn.configure(text=f"▼ {self._title}")
        else:
            self.content.pack_forget()
            self._toggle_btn.configure(text=f"▶ {self._title}")

    def expand(self) -> None:
        if not self._expanded:
            self._toggle()

    def collapse(self) -> None:
        if self._expanded:
            self._toggle()

    @property
    def is_expanded(self) -> bool:
        return self._expanded


class PillTabBar(ctk.CTkFrame):
    """Custom pill-style tab bar with horizontal scroll."""

    def __init__(self, parent, on_switch=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._tabs: list[dict] = []
        self._active_idx = 0
        self._on_switch = on_switch

        # Inner scrollable frame for tabs
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            orientation="horizontal",
            height=46,
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text3"],
        )
        self._scroll.pack(fill="x", expand=True)

    def add_tab(self, label: str, icon: str = "") -> int:
        idx = len(self._tabs)
        # Convert icon name to actual Unicode character
        icon_char = icon if icon else ""
        text = f"{icon_char} {label}" if icon_char else label
        btn = ctk.CTkButton(
            self._scroll, text=text, height=34, corner_radius=17,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=COLORS["bg_hover"],
            hover_color=COLORS["border"],
            text_color=COLORS["text2"],
            command=lambda i=idx: self._select(i),
        )
        btn.pack(side="left", padx=2, pady=6)
        self._tabs.append({"btn": btn, "label": label, "icon": icon})
        return idx

    def _select(self, idx: int) -> None:
        if idx == self._active_idx:
            return
        # Deactivate old
        old = self._tabs[self._active_idx]
        old["btn"].configure(
            fg_color=COLORS["bg_hover"],
            text_color=COLORS["text2"],
        )
        # Activate new
        self._active_idx = idx
        new = self._tabs[idx]
        new["btn"].configure(
            fg_color=COLORS["accent"],
            text_color="#ffffff",
        )
        if self._on_switch:
            self._on_switch(idx, new["label"])

    def set_active(self, idx: int) -> None:
        if 0 <= idx < len(self._tabs):
            self._select(idx)

    @property
    def active_index(self) -> int:
        return self._active_idx


# ═══════════════════════════════════════════════════════════════
# Mixer Channel Widget (OBS-style)
# ═══════════════════════════════════════════════════════════════

class MixerChannelWidget(ctk.CTkFrame):
    """Single mixer channel: VU meter + fader + Mute/Solo."""

    def __init__(self, parent, label: str, color: str = COLORS["accent"],
                 channel_id: str = "music", on_solo=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8, **kwargs)
        self.channel_id = channel_id
        self._label = label
        self._color = color
        self._muted = False
        self._solo = False
        self._volume = 80.0
        self._gain_db = 0.0  # -12 to +12 dB
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._rms_l = 0.0
        self._rms_r = 0.0
        self._hold_l = 0.0
        self._hold_r = 0.0
        self._hold_counter = 0
        self._active = False
        self._on_solo = on_solo  # callback when solo changes

        self._build_ui()

    def _build_ui(self) -> None:
        # ── Left side: label + M/S inline on same row ──
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", padx=(6, 0), pady=4)

        # Row 1: Label + M + S inline
        name_row = ctk.CTkFrame(left, fg_color="transparent")
        name_row.pack(anchor="w")

        lbl = ctk.CTkLabel(name_row, text=self._label,
                           font=ctk.CTkFont(size=10, weight="bold"),
                           text_color=COLORS["text2"])
        lbl.pack(side="left", padx=(0, 4))

        self.mute_btn = ctk.CTkButton(
            name_row, text="M", width=20, height=18, corner_radius=3,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=COLORS["text2"],
            command=self._toggle_mute,
        )
        self.mute_btn.pack(side="left", padx=1)

        self.solo_btn = ctk.CTkButton(
            name_row, text="S", width=20, height=18, corner_radius=3,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=COLORS["text2"],
            command=self._toggle_solo,
        )
        self.solo_btn.pack(side="left", padx=1)

        # Row 2: dB readout
        self.db_label = ctk.CTkLabel(left, text="-inf dB",
                                     font=ctk.CTkFont(size=8),
                                     text_color=COLORS["text3"], anchor="w")
        self.db_label.pack(anchor="w")

        # ── Center: VU meter + fader (horizontal strip) ──
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.pack(side="left", fill="both", expand=True, padx=4)

        # Horizontal VU meter (L/R side by side)
        meter_row = ctk.CTkFrame(center, fg_color="transparent")
        meter_row.pack(fill="x")

        self.meter = tk.Canvas(meter_row, height=16, bg=COLORS["bg"], highlightthickness=0)
        self.meter.pack(fill="x")
        self._meter_h = 16
        self._meter_w = 200

        # Horizontal fader
        self._vol_var = ctk.DoubleVar(value=self._volume)
        self.fader = ctk.CTkSlider(
            center, from_=0, to=100,
            variable=self._vol_var,
            orientation="horizontal",
            width=140, height=10,
            fg_color=COLORS["border"],
            progress_color=self._color,
            button_color=self._color,
            button_hover_color=COLORS["accent_hover"],
            command=self._on_fader_change,
        )
        self.fader.set(self._volume)
        self.fader.pack(fill="x", pady=(1, 0))

        # ── Right side: dB readout + gain inline ──
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.pack(side="right", padx=(0, 6), pady=4)

        self.vol_label = ctk.CTkLabel(right, text="0.0 dB",
                                      font=ctk.CTkFont(size=9, weight="bold"),
                                      text_color=COLORS["text"], width=60, anchor="e")
        self.vol_label.pack(anchor="e")

        gain_row = ctk.CTkFrame(right, fg_color="transparent")
        gain_row.pack(anchor="e")

        self.gain_dn_btn = ctk.CTkButton(
            gain_row, text="-", width=18, height=16, corner_radius=3,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text3"],
            command=lambda: self._adjust_gain(-1.0),
        )
        self.gain_dn_btn.pack(side="left", padx=1)

        self.gain_label = ctk.CTkLabel(gain_row, text="0.0 dB",
                                       font=ctk.CTkFont(size=8),
                                       text_color=COLORS["accent"], width=44)
        self.gain_label.pack(side="left")

        self.gain_up_btn = ctk.CTkButton(
            gain_row, text="+", width=18, height=16, corner_radius=3,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text3"],
            command=lambda: self._adjust_gain(1.0),
        )
        self.gain_up_btn.pack(side="left", padx=1)

    def _on_fader_change(self, val: float) -> None:
        self._volume = float(val)
        self._update_vol_label()

    def _adjust_gain(self, delta: float) -> None:
        """Adjust gain by delta dB (-12 to +12)."""
        self._gain_db = max(-12.0, min(12.0, self._gain_db + delta))
        self.gain_label.configure(text=f"Gain: {self._gain_db:+.1f} dB")
        self._update_vol_label()

    def _update_vol_label(self) -> None:
        """Update volume label showing effective dB."""
        import math
        if self._volume > 0.001:
            effective = (self._volume / 100.0) * (10 ** (self._gain_db / 20.0))
            if effective > 0.001:
                db = 20 * math.log10(effective)
                self.vol_label.configure(text=f"{db:+.1f} dB")
            else:
                self.vol_label.configure(text="-inf dB")
        else:
            self.vol_label.configure(text="-inf dB")

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        if self._muted:
            self.mute_btn.configure(text="M", fg_color=COLORS["red"], text_color="#fff")
        else:
            self.mute_btn.configure(text="M", fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])

    def _toggle_solo(self) -> None:
        self._solo = not self._solo
        if self._solo:
            self.solo_btn.configure(text="S", fg_color="#eab308", text_color="#000")
        else:
            self.solo_btn.configure(text="S", fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])
        if self._on_solo:
            self._on_solo(self.channel_id, self._solo)

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_solo(self) -> bool:
        return self._solo

    @property
    def effective_volume(self) -> float:
        """Effective volume with gain applied (0.0 - 1.0)."""
        if self._muted:
            return 0.0
        base = self._volume / 100.0
        gain_linear = 10 ** (self._gain_db / 20.0)
        return max(0.0, min(1.0, base * gain_linear))

    @property
    def volume(self) -> float:
        return self._volume if not self._muted else 0.0

    def update_levels(self, rms_l: float, rms_r: float, active: bool = True) -> None:
        """Update meter levels (0.0 - 1.0)."""
        self._active = active
        if active:
            self._rms_l = rms_l
            self._rms_r = rms_r
            self._peak_l = max(self._rms_l * 1.08, self._peak_l * 0.88)
            self._peak_r = max(self._rms_r * 1.08, self._peak_r * 0.88)
        else:
            self._rms_l *= 0.85
            self._rms_r *= 0.85
            self._peak_l *= 0.9
            self._peak_r *= 0.9
        self._hold_counter += 1
        if self._hold_counter > 12:
            self._hold_l = max(self._hold_l - 0.015, 0.0)
            self._hold_r = max(self._hold_r - 0.015, 0.0)
            self._hold_counter = 0
        self._hold_l = max(self._hold_l, self._peak_l)
        self._hold_r = max(self._hold_r, self._peak_r)
        self._draw_meter()
        # dB readout
        avg = (self._rms_l + self._rms_r) / 2.0
        if avg > 0.001:
            import math
            db = 20 * math.log10(avg)
            self.db_label.configure(text=f"{db:.1f} dB")
        else:
            self.db_label.configure(text="-inf dB")

    @staticmethod
    def _db_color(level: float) -> str:
        if level < 0.6:
            return "#22c55e"
        elif level < 0.85:
            return "#eab308"
        else:
            return "#ef4444"

    def _draw_meter(self) -> None:
        c = self.meter
        c.delete("all")
        w = c.winfo_width() or 200
        h = c.winfo_height() or 28
        c.configure(width=w, height=h)
        seg_w = 3
        seg_gap = 1
        ch_h = 10
        gap = 2
        # L top half, R bottom half
        lx = 0
        ly = 0
        rx = 0
        ry = ch_h + gap

        for ch_idx, (rms, peak, hold) in enumerate([
            (self._rms_l, self._peak_l, self._hold_l),
            (self._rms_r, self._peak_r, self._hold_r),
        ]):
            sy = ly if ch_idx == 0 else ry
            n_segs = max(2, w // (seg_w + seg_gap))
            for i in range(n_segs):
                frac = (i + 1) / n_segs
                sx = i * (seg_w + seg_gap)
                if frac <= rms:
                    color = self._db_color(frac)
                elif frac <= peak:
                    color = "#0f2a1a" if frac <= 0.6 else ("#2a2200" if frac <= 0.85 else "#2a0f0f")
                else:
                    color = COLORS["bg_hover"]
                c.create_rectangle(sx, sy, sx + seg_w, sy + ch_h,
                                   fill=color, outline="")
            # Peak hold marker
            if hold > 0.02:
                hx = int(hold * n_segs) * (seg_w + seg_gap)
                c.create_rectangle(hx, sy, hx + seg_w, sy + ch_h,
                                   fill="white", outline="")


# ═══════════════════════════════════════════════════════════════
# VU Meter Widget
# ═══════════════════════════════════════════════════════════════

class VumeterWidget(tk.Canvas):
    """Stereo VU meter with L/R bars and dB scale."""

    def __init__(self, parent, width=300, height=36, bg=COLORS["bg_card"], **kwargs):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, **kwargs)
        self._width = width
        self._height = height
        self._peak_l = 0.0  # 0.0 – 1.0
        self._peak_r = 0.0
        self._rms_l = 0.0
        self._rms_r = 0.0
        self._hold_l = 0.0   # peak hold
        self._hold_r = 0.0
        self._hold_counter = 0
        self.bind("<Configure>", self._on_resize)
        self._draw()

    def update_levels(self, volume: float, playing: bool) -> None:
        """Update VU meter levels based on current volume and state."""
        import random
        if playing:
            base = volume / 100.0
            self._rms_l = max(0.0, min(1.0, base * (0.7 + random.random() * 0.3)))
            self._rms_r = max(0.0, min(1.0, base * (0.7 + random.random() * 0.3)))
            self._peak_l = max(self._rms_l * (1.0 + random.random() * 0.15), self._peak_l * 0.85)
            self._peak_r = max(self._rms_r * (1.0 + random.random() * 0.15), self._peak_r * 0.85)
        else:
            self._rms_l *= 0.8
            self._rms_r *= 0.8
            self._peak_l *= 0.9
            self._peak_r *= 0.9
        # Peak hold decay
        self._hold_counter += 1
        if self._hold_counter > 15:  # hold for ~15 frames
            self._hold_l = max(self._hold_l - 0.02, 0.0)
            self._hold_r = max(self._hold_r - 0.02, 0.0)
            self._hold_counter = 0
        self._hold_l = max(self._hold_l, self._peak_l)
        self._hold_r = max(self._hold_r, self._peak_r)
        self._draw()

    def clear(self) -> None:
        self._peak_l = self._peak_r = 0.0
        self._rms_l = self._rms_r = 0.0
        self._hold_l = self._hold_r = 0.0
        self.delete("all")

    def _on_resize(self, event) -> None:
        self._width = event.width
        self._height = event.height
        self._draw()

    @staticmethod
    def _db_color(level: float) -> str:
        """Return color based on dB level: green < yellow < red."""
        if level < 0.6:
            return "#22c55e"   # green
        elif level < 0.85:
            return "#eab308"   # yellow
        else:
            return "#ef4444"   # red

    def _draw(self) -> None:
        self.delete("all")
        w = self._width
        h = self._height
        if w < 40 or h < 10:
            return

        # Layout: [label L] [bars L] | [label R] [bars R]
        label_w = 14
        gap = 8
        total_bar_w = (w - label_w * 2 - gap) // 2
        bar_h = 20
        seg_w = 5
        seg_gap = 1
        n_segs = max(4, total_bar_w // (seg_w + seg_gap))
        bar_area_w = n_segs * (seg_w + seg_gap)
        y_top = 4

        # dB scale labels across top
        db_marks = [(-48, 0.0), (-36, 0.25), (-24, 0.5), (-18, 0.625), (-12, 0.75), (-6, 0.875), (0, 1.0)]
        for db_val, frac in db_marks:
            x = label_w + frac * bar_area_w
            self.create_text(x, y_top - 1, text=str(db_val),
                             fill=COLORS["text3"], font=ctk.CTkFont(size=7), anchor="s")

        y_bar = y_top + 8

        def draw_channel(label: str, rms: float, peak: float, hold: float, x_start: int) -> None:
            # Label
            self.create_text(x_start, y_bar + bar_h // 2, text=label,
                             fill=COLORS["text2"], font=ctk.CTkFont(size=9, weight="bold"), anchor="w")
            bx = x_start + label_w
            # Segments
            for i in range(n_segs):
                frac = (i + 1) / n_segs
                sx = bx + i * (seg_w + seg_gap)
                if frac <= rms:
                    color = self._db_color(frac)
                elif frac <= peak:
                    color = self._db_color(frac)
                    # dimmer for peak region
                    color = "#1a3a5a" if frac <= 0.6 else ("#4a3a0a" if frac <= 0.85 else "#4a1a1a")
                else:
                    color = COLORS["bg_hover"]
                self.create_rectangle(sx, y_bar, sx + seg_w, y_bar + bar_h,
                                      fill=color, outline="")
            # Peak hold marker
            if hold > 0.02:
                hx = bx + int(hold * n_segs) * (seg_w + seg_gap)
                self.create_rectangle(hx, y_bar - 1, hx + seg_w, y_bar,
                                      fill="white", outline="")

        draw_channel("L", self._rms_l, self._peak_l, self._hold_l, 0)
        draw_channel("R", self._rms_r, self._peak_r, self._hold_r, bar_area_w + label_w + gap)


# Waveform Widget
# ═══════════════════════════════════════════════════════════════

class WaveformWidget(tk.Canvas):
    """Audio waveform display with playback position marker."""

    def __init__(self, parent, width=600, height=48, bg=COLORS["bg_card"],
                 wave_color=COLORS["accent"], played_color="#2a5a8a",
                 **kwargs):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, **kwargs)
        self._wave_color = wave_color
        self._played_color = played_color
        self._samples: list[float] = []  # normalized 0.0-1.0
        self._position: float = 0.0       # 0.0-1.0
        self._width = width
        self._height = height
        self._bar_width = 2
        self._gap = 1
        self.bind("<Configure>", self._on_resize)

    def set_waveform(self, samples: list[float]) -> None:
        """Set waveform amplitude data (list of floats 0.0-1.0)."""
        self._samples = samples
        self._draw()

    def set_position(self, pct: float) -> None:
        """Update playback position (0.0-1.0)."""
        self._position = max(0.0, min(1.0, pct))
        self._draw()

    def clear(self) -> None:
        self._samples = []
        self._position = 0.0
        self.delete("all")

    def _on_resize(self, event) -> None:
        self._width = event.width
        self._height = event.height
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self._samples:
            return
        w = self._width
        h = self._height
        mid = h / 2
        bar_w = max(1, self._bar_width)
        gap = self._gap
        step = bar_w + gap
        n_bars = max(1, w // step)
        # Resample to fit width
        resampled = []
        chunk = max(1, len(self._samples) // n_bars)
        for i in range(n_bars):
            start = i * chunk
            end = min(start + chunk, len(self._samples))
            if start < len(self._samples):
                val = max(self._samples[start:end]) if end > start else self._samples[start]
            else:
                val = 0.0
            resampled.append(val)
        pos_idx = int(self._position * len(resampled))
        for i, amp in enumerate(resampled):
            x = i * step
            bar_h = max(2, int(amp * (h - 4) / 2))
            color = self._played_color if i < pos_idx else self._wave_color
            # Draw from center
            self.create_rectangle(x, mid - bar_h, x + bar_w, mid + bar_h,
                                  fill=color, outline="", width=0)

    @staticmethod
    def generate_from_file(filepath: str, num_samples: int = 200) -> list[float]:
        """Generate waveform data from an audio file using ffmpeg."""
        import subprocess
        import struct
        try:
            cmd = [
                "ffmpeg", "-i", filepath,
                "-ac", "1", "-ar", "11025", "-f", "s16le",
                "-t", "120",  # max 2 minutes
                "-y", "pipe:1"
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode != 0 or not result.stdout:
                return WaveformWidget._generate_silence(num_samples)
            raw = result.stdout
            samples = struct.unpack(f"<{len(raw)//2}h", raw)
            if not samples:
                return WaveformWidget._generate_silence(num_samples)
            # Downsample to num_samples
            chunk_size = max(1, len(samples) // num_samples)
            waveform = []
            for i in range(num_samples):
                start = i * chunk_size
                end = min(start + chunk_size, len(samples))
                chunk = samples[start:end]
                if chunk:
                    rms = (sum(s*s for s in chunk) / len(chunk)) ** 0.5
                    waveform.append(rms)
                else:
                    waveform.append(0.0)
            # Normalize to 0.0-1.0
            peak = max(waveform) if waveform else 1.0
            if peak > 0:
                waveform = [v / peak for v in waveform]
            return waveform
        except Exception:
            return WaveformWidget._generate_silence(num_samples)

    @staticmethod
    def _generate_silence(n: int) -> list[float]:
        return [0.01] * n


# ═══════════════════════════════════════════════════════════════
# Time Picker Dialog
# ═══════════════════════════════════════════════════════════════

class TimePickerDialog(tk.Toplevel):
    """Time picker popup using raw tkinter for reliable rendering."""

    def __init__(self, parent, initial: str = "08:00", title: str = "Select Time"):
        super().__init__(parent)
        self.result = None
        self.title(title)
        W, H = 320, 340
        self.geometry(f"{W}x{H}")
        self.resizable(False, False)
        self.configure(bg="#1a1a2e")
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # Parse initial time
        try:
            parts = initial.strip().split(":")
            self._hour = int(parts[0]) % 24
            self._minute = int(parts[1]) % 60
        except (ValueError, IndexError):
            self._hour = 8
            self._minute = 0

        BG = "#1a1a2e"
        CARD = "#252540"
        FG = "#ffffff"
        ACCENT = "#3b82f6"
        MUTED = "#6b7280"

        # Title
        tk.Label(self, text=title, bg=BG, fg=FG,
                 font=("Segoe UI", 13, "bold")).pack(pady=(16, 4))

        # Time display
        self.time_label = tk.Label(self, text="", bg=BG, fg=ACCENT,
                                   font=("Segoe UI", 32, "bold"))
        self.time_label.pack(pady=(0, 14))

        # Hour row
        hf = tk.Frame(self, bg=CARD)
        hf.pack(fill="x", padx=20, pady=3)
        tk.Label(hf, text="Hour", bg=CARD, fg=MUTED, width=5,
                 font=("Segoe UI", 10)).pack(side="left", padx=6)
        tk.Button(hf, text="◀", bg=CARD, fg=FG, bd=0, width=3,
                  activebackground=ACCENT, activeforeground=FG,
                  font=("Segoe UI", 12, "bold"),
                  command=lambda: self._adjust("hour", -1)).pack(side="left", padx=2, pady=4)
        self.hour_label = tk.Label(hf, text="08", bg=CARD, fg=FG, width=3,
                                   font=("Segoe UI", 16, "bold"))
        self.hour_label.pack(side="left", padx=2)
        tk.Button(hf, text="▶", bg=CARD, fg=FG, bd=0, width=3,
                  activebackground=ACCENT, activeforeground=FG,
                  font=("Segoe UI", 12, "bold"),
                  command=lambda: self._adjust("hour", 1)).pack(side="left", padx=2, pady=4)

        # Minute row
        mf = tk.Frame(self, bg=CARD)
        mf.pack(fill="x", padx=20, pady=3)
        tk.Label(mf, text="Min", bg=CARD, fg=MUTED, width=5,
                 font=("Segoe UI", 10)).pack(side="left", padx=6)
        tk.Button(mf, text="◀", bg=CARD, fg=FG, bd=0, width=3,
                  activebackground=ACCENT, activeforeground=FG,
                  font=("Segoe UI", 12, "bold"),
                  command=lambda: self._adjust("min", -5)).pack(side="left", padx=2, pady=4)
        self.min_label = tk.Label(mf, text="00", bg=CARD, fg=FG, width=3,
                                   font=("Segoe UI", 16, "bold"))
        self.min_label.pack(side="left", padx=2)
        tk.Button(mf, text="▶", bg=CARD, fg=FG, bd=0, width=3,
                  activebackground=ACCENT, activeforeground=FG,
                  font=("Segoe UI", 12, "bold"),
                  command=lambda: self._adjust("min", 5)).pack(side="left", padx=2, pady=4)

        # All display labels exist before the first refresh.
        self._update_display()

        # Quick presets
        pf = tk.Frame(self, bg=CARD)
        pf.pack(fill="x", padx=20, pady=(10, 3))
        tk.Label(pf, text="Quick:", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=6, pady=6)
        for h in [6, 7, 8, 12, 17, 18, 20]:
            tk.Button(pf, text=f"{h:02d}:00", bg=CARD, fg=FG, bd=0,
                      activebackground=ACCENT, activeforeground=FG,
                      font=("Segoe UI", 9), width=4,
                      command=lambda h=h: self._set_preset(h, 0)).pack(side="left", padx=2, pady=6)

        # OK / Cancel
        bf = tk.Frame(self, bg=CARD)
        bf.pack(fill="x", padx=20, pady=(12, 14))
        tk.Button(bf, text="Cancel", bg=CARD, fg=FG, bd=0, height=2,
                  activebackground=MUTED, activeforeground=FG,
                  font=("Segoe UI", 11),
                  command=self._cancel).pack(side="left", expand=True, fill="x", padx=(6, 4), pady=6)
        tk.Button(bf, text="  OK  ", bg="#22c55e", fg="#000", bd=0, height=2,
                  activebackground="#16a34a", activeforeground="#000",
                  font=("Segoe UI", 11, "bold"),
                  command=self._ok).pack(side="left", expand=True, fill="x", padx=(4, 6), pady=6)

        # Center on parent
        self.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width() - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{max(0,px)}+{max(0,py)}")

    def _update_display(self):
        self.time_label.configure(text=f"{self._hour:02d}:{self._minute:02d}")
        self.hour_label.configure(text=f"{self._hour:02d}")
        self.min_label.configure(text=f"{self._minute:02d}")

    def _adjust(self, field: str, delta: int):
        if field == "hour":
            self._hour = (self._hour + delta) % 24
        else:
            self._minute = (self._minute + delta) % 60
        self._update_display()

    def _set_preset(self, h: int, m: int):
        self._hour = h
        self._minute = m
        self._update_display()

    def _ok(self):
        self.result = f"{self._hour:02d}:{self._minute:02d}"
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class PlaylistRangeDialog(tk.Toplevel):
    """Popup to choose playlist import range when >100 songs."""

    def __init__(self, parent, total: int):
        super().__init__(parent)
        self.result = None  # None = cancel, "all" = all, (start, end) = range
        self.title("Playlist Import")
        W, H = 380, 420
        self.geometry(f"{W}x{H}")
        self.resizable(False, False)
        self.configure(bg="#1a1a2e")
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._total = total
        BG = "#1a1a2e"
        CARD = "#252540"
        FG = "#ffffff"
        ACCENT = "#3b82f6"
        MUTED = "#6b7280"
        WARN = "#f59e0b"

        # Title
        tk.Label(self, text="📋 Playlist Import", bg=BG, fg=FG,
                 font=("Segoe UI", 14, "bold")).pack(pady=(16, 4))
        tk.Label(self, text=f"{total} songs found", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 11)).pack(pady=(0, 10))

        if total > 100:
            tk.Label(self, text="⚠️ Large playlist — pick a range to avoid slow imports",
                     bg=BG, fg=WARN, font=("Segoe UI", 9),
                     wraplength=320).pack(pady=(0, 10))

        # Option: All songs
        self._choice = tk.StringVar(value="all")
        all_frame = tk.Frame(self, bg=CARD)
        all_frame.pack(fill="x", padx=24, pady=4)
        tk.Radiobutton(all_frame, text=f"Add all {total} songs", variable=self._choice,
                       value="all", bg=CARD, fg=FG, selectcolor="#3b3b5c",
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 11)).pack(side="left", padx=8, pady=6)

        # Option: Range
        range_frame = tk.Frame(self, bg=CARD)
        range_frame.pack(fill="x", padx=24, pady=4)
        tk.Radiobutton(range_frame, text="Add songs from #", variable=self._choice,
                       value="range", bg=CARD, fg=FG, selectcolor="#3b3b5c",
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 11)).pack(side="left", padx=(8, 4), pady=6)

        # Start spinbox
        self._start_var = tk.IntVar(value=1)
        start_sb = tk.Spinbox(range_frame, from_=1, to=total, width=5,
                              textvariable=self._start_var, bg="#3b3b5c", fg=FG,
                              buttonbackground="#4b4b6c", font=("Segoe UI", 11),
                              insertbackground=FG)
        start_sb.pack(side="left", padx=2, pady=6)

        tk.Label(range_frame, text="to #", bg=CARD, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left", padx=2, pady=6)

        # End spinbox
        self._end_var = tk.IntVar(value=min(100, total))
        end_sb = tk.Spinbox(range_frame, from_=1, to=total, width=5,
                            textvariable=self._end_var, bg="#3b3b5c", fg=FG,
                            buttonbackground="#4b4b6c", font=("Segoe UI", 11),
                            insertbackground=FG)
        end_sb.pack(side="left", padx=2, pady=6)

        # Range info label
        self._range_info = tk.Label(range_frame, text="(100 songs)", bg=CARD,
                                    fg=MUTED, font=("Segoe UI", 9))
        self._range_info.pack(side="left", padx=6, pady=6)

        # Quick picks
        qp = tk.Frame(self, bg=CARD)
        qp.pack(fill="x", padx=24, pady=(6, 4))
        tk.Label(qp, text="Quick:", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(8, 4), pady=4)
        for n in [25, 50, 100, 200]:
            if n <= total:
                tk.Button(qp, text=f"1-{n}", bg=CARD, fg=FG, bd=0,
                          activebackground=ACCENT, activeforeground=FG,
                          font=("Segoe UI", 9), width=4,
                          command=lambda e=n: self._set_range(1, e)).pack(side="left", padx=2, pady=4)

        # Warning label for large all
        self._warn_label = tk.Label(self, text="", bg=BG, fg="#ef4444",
                                    font=("Segoe UI", 9), wraplength=320)
        self._warn_label.pack(pady=(4, 0))

        # OK / Cancel buttons
        bf = tk.Frame(self, bg=CARD)
        bf.pack(fill="x", padx=24, pady=(12, 14))
        tk.Button(bf, text="Cancel", bg=CARD, fg=FG, bd=0, height=2,
                  activebackground=MUTED, activeforeground=FG,
                  font=("Segoe UI", 11),
                  command=self._cancel).pack(side="left", expand=True, fill="x", padx=(6, 4), pady=6)
        self._ok_btn = tk.Button(bf, text="  Add Songs  ", bg="#22c55e", fg="#000", bd=0, height=2,
                                 activebackground="#16a34a", activeforeground="#000",
                                 font=("Segoe UI", 11, "bold"),
                                 command=self._ok)
        self._ok_btn.pack(side="left", expand=True, fill="x", padx=(4, 6), pady=6)

        # Trace changes
        self._choice.trace_add("write", lambda *_: self._on_choice_change())
        self._start_var.trace_add("write", lambda *_: self._on_range_change())
        self._end_var.trace_add("write", lambda *_: self._on_range_change())
        self._on_choice_change()

        # Center on parent
        self.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width() - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{max(0,px)}+{max(0,py)}")

    def _on_choice_change(self):
        choice = self._choice.get()
        if choice == "all" and self._total > 100:
            self._warn_label.configure(text=f"⚠️ Importing all {self._total} songs may be slow")
            self._ok_btn.configure(text=f"  Add All {self._total}  ")
        elif choice == "all":
            self._warn_label.configure(text="")
            self._ok_btn.configure(text=f"  Add All {self._total}  ")
        else:
            self._warn_label.configure(text="")
            self._on_range_change()

    def _on_range_change(self):
        try:
            s = self._start_var.get()
            e = self._end_var.get()
            if s < 1: s = 1
            if e > self._total: e = self._total
            if s > e: s, e = e, s
            count = e - s + 1
            self._range_info.configure(text=f"({count} songs)")
            self._ok_btn.configure(text=f"  Add Songs #{s}-{e}  ")
        except (tk.TclError, ValueError):
            pass

    def _set_range(self, s, e):
        self._choice.set("range")
        self._start_var.set(s)
        self._end_var.set(min(e, self._total))

    def _ok(self):
        if self._choice.get() == "all":
            self.result = "all"
        else:
            s = self._start_var.get()
            e = self._end_var.get()
            if s < 1: s = 1
            if e > self._total: e = self._total
            if s > e: s, e = e, s
            self.result = (s, e)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ═══════════════════════════════════════════════════════════════
# Main Application
# ═══════════════════════════════════════════════════════════════

class RadioApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        # Icon helper — returns CTkImage or None (fallback to emoji)
        self._icons_available = bool(ICON_PATHS)

        self.rq = RadioQueue()
        if self.rq._save_path.exists():
            self.rq.load_from_file()

        self.title("FreQ — Radio Playlist Manager")
        self.geometry("1920x1080")
        self.minsize(900, 600)
        self.configure(fg_color=COLORS["bg"])

        # Set window icon from logo.png (auto-generated from logo.svg)
        self._set_window_icon()

        

        self._playing = False
        self._yt_loading = False
        self.audio_mgr = AudioDeviceManager()
        self.audio_player = AudioPlayer()
        self.audio_player.init()

        # Initialize centralized logging
        setup_logging()

        # View toggle state
        self._show_queue = True
        self._show_sidebar = True
        self._show_now_playing = True

        # Autosave state
        self._autosave_enabled = True
        self._autosave_interval = 60  # seconds
        self._autosave_last = 0.0

        self._build_ui()
        self._setup_clipboard_support()
        self._restore_settings(self.rq.last_settings)
        self._refresh_queue()
        self._update_now_playing()
        self._refresh_device_list()
        self._start_progress_timer()        # Auto-save on close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Drag-and-drop
        self._dnd_active = False
        self._setup_dnd()

        # Keyboard shortcuts
        self.bind("<space>", self._on_global_space)
        self.bind("<Right>", lambda e: self._seek_forward())
        self.bind("<Left>", lambda e: self._seek_backward())
        self.bind("<Up>", lambda e: self._volume_up())
        self.bind("<Down>", lambda e: self._volume_down())
        self.bind("<n>", lambda e: self._next_track())
        self.bind("<p>", lambda e: self._prev_track())
        self.bind("<m>", lambda e: self._toggle_mute())
        self.bind("<Control-s>", lambda e: self._save())
        self.bind("<Control-o>", lambda e: self._open_file())
        self.bind("<Control-v>", self._on_paste)
        self.bind("<Control-V>", self._on_paste)


    # ─────────────────────────────────────────
    # Drag-and-Drop
    # ─────────────────────────────────────────
    def _on_global_space(self, event) -> None:
        """Space key: play/pause only when not typing in an entry widget."""
        # Don't intercept space when user is typing in an entry/textbox
        widget = event.widget
        try:
            wclass = widget.winfo_class()
        except Exception:
            wclass = ""
        if wclass in ("Entry", "Text"):
            return  # let the entry handle the space character
        self._toggle_play()

    def _on_paste(self, event=None) -> None:
        """Handle Ctrl+V paste into focused entry widget."""
        try:
            focused = getattr(event, "widget", None) if event else None
            focused = focused or self.focus_get()
            if focused is None:
                return
            wclass = focused.winfo_class()
            if wclass in ("Entry", "Text"):
                try:
                    text = self.clipboard_get()
                except Exception:
                    return
                if wclass == "Entry":
                    try:
                        focused.delete("sel.first", "sel.last")
                    except tk.TclError:
                        pass
                    focused.insert("insert", text)
                else:
                    try:
                        focused.delete("sel.first", "sel.last")
                    except tk.TclError:
                        pass
                    focused.insert("insert", text)
        except Exception:
            pass
        return "break"

    def _setup_clipboard_support(self) -> None:
        """Give every text input the same clipboard shortcuts and context menu."""
        def visit(widget):
            try:
                if widget.winfo_class() in ("Entry", "Text"):
                    self._bind_clipboard_shortcuts(widget)
                    if not getattr(widget, "_freq_clipboard_ready", False):
                        self._add_clipboard_menu(widget)
                for child in widget.winfo_children():
                    visit(child)
            except Exception:
                pass

        visit(self)

    def _bind_clipboard_shortcuts(self, widget) -> None:
        """Bind clipboard shortcuts without replacing widget default behavior."""
        widget.bind("<Control-c>", lambda e: (self._clipboard_action(widget, "copy"), "break")[1], add="+")
        widget.bind("<Control-x>", lambda e: (self._clipboard_action(widget, "cut"), "break")[1], add="+")
        widget.bind("<Control-v>", lambda e: (self._clipboard_action(widget, "paste"), "break")[1], add="+")

    def _clipboard_action(self, widget, action: str) -> None:
        """Run a standard clipboard action on an Entry or Text widget."""
        try:
            if action == "copy":
                widget.event_generate("<<Copy>>")
            elif action == "cut":
                widget.event_generate("<<Cut>>")
            elif action == "paste":
                self._on_paste(type("PasteEvent", (), {"widget": widget})())
            elif action == "select_all":
                if widget.winfo_class() == "Entry":
                    widget.select_range(0, "end")
                    widget.icursor("end")
                else:
                    widget.tag_add("sel", "1.0", "end")
                    widget.mark_set("insert", "end")
        except Exception:
            pass

    def _add_clipboard_menu(self, widget) -> None:
        """Attach copy/cut/paste actions to one text input."""
        if getattr(widget, "_freq_clipboard_ready", False):
            return
        widget._freq_clipboard_ready = True
        menu = tk.Menu(widget, tearoff=0, bg="#2b2b2b", fg="white",
                       activebackground="#3b82f6", activeforeground="white")
        menu.add_command(label="Copy", command=lambda: self._clipboard_action(widget, "copy"))
        menu.add_command(label="Cut", command=lambda: self._clipboard_action(widget, "cut"))
        menu.add_command(label="Paste", command=lambda: self._clipboard_action(widget, "paste"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._clipboard_action(widget, "select_all"))
        menu.add_command(label="Clear", command=lambda: widget.delete(0, "end") if widget.winfo_class() == "Entry" else widget.delete("1.0", "end"))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _paste_to_entry(self, entry) -> None:
        """Paste clipboard content to a specific entry widget."""
        try:
            text = self.clipboard_get()
            if text:
                try:
                    entry.delete("sel.first", "sel.last")
                except tk.TclError:
                    pass
                entry.insert("insert", text)
                entry.configure(border_color=COLORS["green"])
                entry.after(1500, lambda: entry.configure(border_color=COLORS["border"]))
        except Exception:
            pass

    def _add_paste_menu(self, widget) -> None:
        """Add right-click context menu with Paste option to a widget."""
        if getattr(widget, "_freq_clipboard_ready", False):
            return
        widget._freq_clipboard_ready = True
        import tkinter.messagebox as mb
        menu = tk.Menu(widget, tearoff=0, bg="#2b2b2b", fg="white",
                       activebackground="#3b82f6", activeforeground="white")
        menu.add_command(label="Copy", command=lambda: self._clipboard_action(widget, "copy"))
        menu.add_command(label="Cut", command=lambda: self._clipboard_action(widget, "cut"))
        menu.add_command(label="Paste", command=lambda: self._clipboard_action(widget, "paste"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._clipboard_action(widget, "select_all"))
        menu.add_command(label="Clear", command=lambda: widget.delete(0, "end"))
        menu.add_separator()
        menu.add_command(label="➕ Paste & Add",
                         command=lambda: self._paste_and_add(widget))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _paste_and_add(self, entry) -> None:
        """Paste from clipboard and immediately add the song."""
        self._paste_to_entry(entry)
        # Trigger the appropriate add action
        url = entry.get().strip()
        if url and "playlist" in entry.cget("placeholder_text").lower():
            self._add_youtube_playlist()
        elif url:
            self._add_youtube()

    def _throttled_refresh(self, delay: float = 1.0) -> None:
        """Queue refresh with throttle - max once per `delay` seconds"""
        import time
        now = time.time()
        if now - self._last_refresh_time >= delay:
            self._last_refresh_time = now
            self._refresh_queue()
            self._refresh_pending = False
        elif not self._refresh_pending:
            self._refresh_pending = True
            self.after(int(delay * 1000), self._do_deferred_refresh)

    def _do_deferred_refresh(self) -> None:
        self._last_refresh_time = 0.0
        self._refresh_queue()
        self._refresh_pending = False

    def _tip(self, widget, title: str, desc: str = "") -> ToolTip:
        """Add a hover tooltip to a widget."""
        return ToolTip(widget, text=title, description=desc)

    def _icon(self, name: str, size: int = 20, color: str = None):
        """Get a Material Icon CTkImage, or None to fallback to emoji."""
        if not self._icons_available:
            return None
        if color is None:
            color = COLORS["text2"]
        return get_tk_image(name, size, color)

    def _icon_btn(self, name: str, parent, size: int = 20, **kwargs):
        """Create a button with an icon image, falling back to text."""
        img = self._icon(name, size)
        if img:
            kwargs.pop("text", None)
            return ctk.CTkButton(parent, image=img, text="", **kwargs)
        # No image — ensure button has text so it's not blank
        if "text" not in kwargs or not kwargs["text"]:
            kwargs["text"] = name.replace("_", " ").title()
        return ctk.CTkButton(parent, **kwargs)

    def _setup_dnd(self) -> None:
        """Set up drag-and-drop if tkinterdnd2 is available"""
        if not DND_AVAILABLE:
            return
        try:
            self.dnd = DnD(self)
            self.dnd.bindtarget(self, "<Drop>", self._on_drop)
            self.dnd.bindtarget(self, "<DragEnter>", self._on_drag_enter)
            self.dnd.bindtarget(self, "<DragLeave>", self._on_drag_leave)
            self._dnd_active = True
        except Exception:
            self._dnd_active = False

    def _on_drag_enter(self, event) -> None:
        self.configure(fg_color=COLORS["accent"])

    def _on_drag_leave(self, event) -> None:
        self.configure(fg_color=COLORS["bg"])

    def _on_drop(self, event) -> None:
        """Drop handler - URLs and audio files"""
        self.configure(fg_color=COLORS["bg"])
        data = event.data.strip()
        if not data:
            return

        paths = self._parse_drop_data(data)
        audio_exts = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus"}
        added = 0

        for p in paths:
            if YouTubeManager.is_youtube_url(p):
                self.yt_url_entry.delete(0, "end")
                self.yt_url_entry.insert(0, p)
                self._add_youtube()
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in audio_exts:
                self._add_dropped_file(p)
                added += 1
            elif "http" in p.lower():
                self.yt_url_entry.delete(0, "end")
                self.yt_url_entry.insert(0, p)
                self._add_youtube()

        if added > 0:
            self._refresh_queue()
            self._refresh_library()

    @staticmethod
    def _parse_drop_data(data: str) -> list[str]:
        """Parse drop data - tkinterdnd2 wraps Windows paths in {}"""
        paths: list[str] = []
        brace_pattern = re.compile(r'\{([^}]+)\}')
        remaining = data
        for m in brace_pattern.finditer(data):
            paths.append(m.group(1))
            remaining = remaining.replace(m.group(0), "")
        for token in remaining.split():
            token = token.strip().strip('"').strip("'")
            if token:
                paths.append(token)
        return paths

    def _add_dropped_file(self, filepath: str) -> None:
        """Add a dropped audio file to the queue"""
        filename = os.path.splitext(os.path.basename(filepath))[0]

        duration = 0.0
        try:
            from mutagen import File as MutagenFile
            mf = MutagenFile(filepath)
            if mf and mf.info:
                duration = mf.info.length
        except Exception:
            try:
                import pygame
                if pygame.mixer.get_init():
                    snd = pygame.mixer.Sound(filepath)
                    duration = snd.get_length()
            except Exception:
                pass

        song = Song(
            title=filename,
            source="file",
            file_path=filepath,
            duration=duration,
        )
        self.rq.add_song(song)



    def _add_mic_segment(self) -> None:
        """Add a timed microphone recording as a normal queue item."""
        if not SOUNDDEVICE_AVAILABLE:
            messagebox.showerror("Microphone unavailable", "sounddevice is required for microphone recording.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Mic Segment")
        dialog.geometry("360x220")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Add microphone segment",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(16, 8))
        duration_row = ctk.CTkFrame(dialog, fg_color="transparent")
        duration_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(duration_row, text="Duration (seconds):", width=140, anchor="w").pack(side="left")
        duration_entry = ctk.CTkEntry(duration_row, width=90)
        duration_entry.insert(0, str(int(self.mic.timer_duration or 30)))
        duration_entry.pack(side="left")

        devices = getattr(self, "_mic_input_devices", self.audio_mgr.get_input_devices())
        device_names = ["Default"] + [d.name for d in devices]
        device_var = ctk.StringVar(value=self.mic_device_var.get() if hasattr(self, "mic_device_var") else "Default")
        device_row = ctk.CTkFrame(dialog, fg_color="transparent")
        device_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(device_row, text="Input mic:", width=140, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(device_row, variable=device_var, values=device_names, width=170).pack(side="left")

        def add_item() -> None:
            try:
                duration = float(duration_entry.get().strip())
                if not 0.5 <= duration <= 3600:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid duration", "Enter a duration from 0.5 to 3600 seconds.", parent=dialog)
                return
            selected = device_var.get()
            device_id = next((d.id for d in devices if d.name == selected), None)
            self.rq.add_song(Song(
                title="Microphone",
                artist="Live voice",
                duration=duration,
                source="mic",
                mic_duration=duration,
                mic_device_index=device_id,
            ))
            self._refresh_queue()
            dialog.destroy()

        ctk.CTkButton(dialog, text="Add to Queue", command=add_item,
                      fg_color=COLORS["green"], text_color="#000000").pack(pady=18)

    # ─────────────────────────────────────────
    # Build UI
    # ─────────────────────────────────────────
    def _build_ui(self) -> None:
        # Top bar
        self._build_toolbar()
        # Main area: left queue + right sidebar
        self._main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._main_frame.pack(fill="both", expand=True, padx=10, pady=(0, 0))

        # Left: Queue
        self._build_queue_panel(self._main_frame)
        # Right: Sidebar
        self._build_sidebar(self._main_frame)
        # Bottom: Now Playing
        self._build_now_playing()
        # Build mixer master fader (needs volume_var from now_playing)
        self._build_mixer_master_fader()
        # Hook dirty detection after all widgets are built
        self._hook_settings_dirty()
        # Show default tab
        self._on_sidebar_tab_switch(0, "Add")

    # ── Toolbar ──
    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0, height=52)
        bar.pack(fill="x", padx=0, pady=0)
        bar.pack_propagate(False)

        # Bottom border line
        ctk.CTkFrame(self, height=1, fg_color=COLORS["border"]).pack(fill="x")

        # Logo
        self._logo_img = self._load_logo()
        if self._logo_img:
            ctk.CTkLabel(bar, image=self._logo_img, text="").pack(side="left", padx=(12, 6), pady=4)
        else:
            ctk.CTkLabel(bar, text="🎙️", font=ctk.CTkFont(size=22)).pack(side="left", padx=(12, 6))

        # App name
        ctk.CTkLabel(
            bar, text="FreQ",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side="left", padx=(0, 4))

        ctk.CTkLabel(
            bar, text="Radio Playlist Manager",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text3"],
        ).pack(side="left", padx=(0, 20))

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=10)

        ctk.CTkButton(right, text="📂 Open", width=80, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._open_file).pack(side="left", padx=4)
        ToolTip(right.winfo_children()[-1], text="Open File", description="Load a .freq playlist file")
        ctk.CTkButton(right, text="💾 Save", width=80, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._save).pack(side="left", padx=4)
        ToolTip(right.winfo_children()[-1], text="Save", description="Save current queue to file")
        ctk.CTkButton(right, text="💾 Save As", width=90, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._save_as).pack(side="left", padx=4)
        ToolTip(right.winfo_children()[-1], text="Save As", description="Save queue to a new .freq file")
        ctk.CTkButton(right, text="🎵 Demo", width=90, height=32,
                       fg_color=COLORS["green"], text_color="#000",
                       hover_color="#2ea043",
                       command=self._load_demo).pack(side="left", padx=4)
        ToolTip(right.winfo_children()[-1], text="Load Demo", description="Load 10 demo songs to try the app")
        ctk.CTkButton(right, text="🧹 Cache", width=90, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._show_cache_menu).pack(side="left", padx=4)
        ToolTip(right.winfo_children()[-1], text="Cache Manager", description="View and clear downloaded song cache")
        self.btn_theme =        ctk.CTkButton(right, text="\U0001f319", width=36, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       font=ctk.CTkFont(size=14), command=self._toggle_theme)
        self.btn_theme.pack(side="left", padx=4)

        # New features buttons
        self.btn_visualizer = ctk.CTkButton(right, text="📊", width=36, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._toggle_visualizer)
        self.btn_visualizer.pack(side="left", padx=2)
        ToolTip(self.btn_visualizer, text="Audio Visualizer", description="Toggle spectrum analyzer")

        self.btn_scheduler = ctk.CTkButton(right, text="⏰", width=36, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._show_scheduler_dialog)
        self.btn_scheduler.pack(side="left", padx=2)
        ToolTip(self.btn_scheduler, text="Scheduler", description="Schedule playback events")

        self.btn_remote = ctk.CTkButton(right, text="📱", width=36, height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._toggle_remote_control)
        self.btn_remote.pack(side="left", padx=2)
        ToolTip(self.btn_remote, text="LiveLAN Remote", description="Start web remote for mobile")

        self.btn_anthem = ctk.CTkButton(right, text="🇹🇭", width=36, height=32,
                       fg_color=COLORS["green"], text_color="#000",
                       hover_color="#2ea043", corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._play_anthem_manual)
        self.btn_anthem.pack(side="left", padx=2)
        ToolTip(self.btn_anthem, text="National Anthem (Manual)", description="Play the national anthem now — for when the 08:00/18:00 schedule missed")

        self.btn_jingle = ctk.CTkButton(right, text="🔔", width=36, height=32,
                       fg_color=COLORS["purple"], text_color="#000",
                       hover_color="#b484d9", corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._play_jingle_manual)
        self.btn_jingle.pack(side="left", padx=2)
        ToolTip(self.btn_jingle, text="Jingle (Manual)", description="Play the jingle now — pauses music, then resumes the current song")

        # Separator
        ctk.CTkFrame(right, width=1, height=28, fg_color=COLORS["border"]).pack(side="left", padx=6)

        # View toggles
        self.btn_toggle_queue = ctk.CTkButton(right, text="📋", width=32, height=32,
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=14),
                       command=self._toggle_queue_visibility)
        self.btn_toggle_queue.pack(side="left", padx=2)
        ToolTip(self.btn_toggle_queue, text="Toggle Queue Panel", description="Show/Hide the queue list")

        self.btn_toggle_sidebar = ctk.CTkButton(right, text="📑", width=32, height=32,
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=14),
                       command=self._toggle_sidebar_visibility)
        self.btn_toggle_sidebar.pack(side="left", padx=2)
        ToolTip(self.btn_toggle_sidebar, text="Toggle Sidebar", description="Show/Hide the sidebar tabs")

        self.btn_toggle_np = ctk.CTkButton(right, text="🎵", width=32, height=32,
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=14),
                       command=self._toggle_np_visibility)
        self.btn_toggle_np.pack(side="left", padx=2)
        ToolTip(self.btn_toggle_np, text="Toggle Now Playing Bar", description="Show/Hide the bottom player bar")

    # ── Queue Panel (left) ──
    def _build_queue_panel(self, parent) -> None:
        self.queue_panel_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=12)
        self.queue_panel_frame.pack(side="left", fill="both", expand=True, padx=(0, 5), pady=5)
        panel = self.queue_panel_frame

        # Header
        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(hdr, text="📋  Queue", font=ctk.CTkFont(size=14, weight="bold"),
                      text_color=COLORS["text"]).pack(side="left")
        # Accent line under header
        ctk.CTkFrame(panel, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=12, pady=(0, 4))
        self.queue_stats_label = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont(size=11),
                                               text_color=COLORS["text3"])
        self.queue_stats_label.pack(side="right")

        # Search bar
        search_frame = ctk.CTkFrame(panel, fg_color="transparent")
        search_frame.pack(fill="x", padx=12, pady=(0, 4))
        self.queue_search_entry = ctk.CTkEntry(
            search_frame, placeholder_text="\U0001f50d Search songs...",
            fg_color=COLORS["bg_input"], border_color=COLORS["border"], height=30, corner_radius=8,
        )
        self.queue_search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.queue_search_entry.bind("<KeyRelease>", lambda e: self._filter_queue())
        self._queue_filter = ""

        # Queue list (scrollable)
        self.queue_frame = ctk.CTkScrollableFrame(
            panel, fg_color=COLORS["bg_card"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text3"],
        )
        self.queue_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Queue action buttons
        btn_bar = ctk.CTkFrame(panel, fg_color="transparent")
        btn_bar.pack(fill="x", padx=12, pady=(0, 10))

        # Clear button (red)
        clear_btn = ctk.CTkButton(btn_bar, text="  Clear", width=90, height=32, corner_radius=8,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       text_color="#ffffff", anchor="w",
                       command=self._clear_queue)
        clear_icon = self._icon("delete_sweep", 16, "#ffffff")
        if clear_icon:
            clear_btn.configure(image=clear_icon, compound="left")
        clear_btn.pack(side="left", padx=2)
        ToolTip(clear_btn, text="Clear Queue", description="Remove all songs from queue")

        # Move Up button
        up_btn = ctk.CTkButton(btn_bar, text="  Move Up", width=100, height=32, corner_radius=8,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       text_color=COLORS["text"], anchor="w",
                       command=lambda: self._move_selected(-1))
        up_icon = self._icon("arrow_upward", 16, COLORS["text"])
        if up_icon:
            up_btn.configure(image=up_icon, compound="left")
        up_btn.pack(side="left", padx=2)
        ToolTip(up_btn, text="Move Up", description="Move selected song up in queue")

        # Move Down button
        down_btn = ctk.CTkButton(btn_bar, text="  Move Down", width=110, height=32, corner_radius=8,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       text_color=COLORS["text"], anchor="w",
                       command=lambda: self._move_selected(1))
        down_icon = self._icon("arrow_downward", 16, COLORS["text"])
        if down_icon:
            down_btn.configure(image=down_icon, compound="left")
        down_btn.pack(side="left", padx=2)
        ToolTip(down_btn, text="Move Down", description="Move selected song down in queue")

        # Remove button (red)
        remove_btn = ctk.CTkButton(btn_bar, text="  Remove", width=100, height=32, corner_radius=8,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       text_color="#ffffff", anchor="w",
                       command=self._remove_selected)
        remove_icon = self._icon("delete", 16, "#ffffff")
        if remove_icon:
            remove_btn.configure(image=remove_icon, compound="left")
        remove_btn.pack(side="left", padx=2)
        ToolTip(remove_btn, text="Remove", description="Remove selected song from queue")

        self._queue_selected: Optional[int] = None

    # ── Sidebar (right) ──
    def _build_sidebar(self, parent) -> None:
        self.sidebar_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=10, width=380)
        self.sidebar_frame.pack(side="right", fill="y", padx=(5, 0), pady=5)
        self.sidebar_frame.pack_propagate(False)

        # Custom pill tab bar
        self._pill_tabs = PillTabBar(self.sidebar_frame, on_switch=self._on_sidebar_tab_switch)
        self._pill_tabs.pack(fill="x", padx=6, pady=(6, 0))

        # Tab indices
        self._tab_names = ["Add", "Library", "Preset", "History", "Log", "Settings", "Stream", "Mixer", "Keys"]
        self._tab_icons  = ["➕", "📚", "📂", "📜", "📋", "⚙️", "📡", "🎚️", "⌨️"]
        self._tab_idx = {}
        for i, (name, icon) in enumerate(zip(self._tab_names, self._tab_icons)):
            idx = self._pill_tabs.add_tab(name, icon)
            self._tab_idx[name] = idx

        # Content area
        self._tab_content = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self._tab_content.pack(fill="both", expand=True, padx=4, pady=(4, 4))

        # Create scrollable content panels (one per tab, stacked)
        self.tab_add = ctk.CTkScrollableFrame(self._tab_content, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        self.tab_library = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self.tab_presets = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self.tab_history = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self.tab_log = ctk.CTkFrame(self._tab_content, fg_color="transparent")
        self.tab_settings = ctk.CTkScrollableFrame(self._tab_content, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        self.tab_stream = ctk.CTkScrollableFrame(self._tab_content, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        self.tab_mixer = ctk.CTkScrollableFrame(self._tab_content, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        self.tab_keys = ctk.CTkScrollableFrame(self._tab_content, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])

        # Library wrapper
        lib_scroll = ctk.CTkScrollableFrame(self.tab_library, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        lib_scroll.pack(fill="both", expand=True)
        self.library_frame = lib_scroll

        # Presets wrapper
        preset_scroll = ctk.CTkScrollableFrame(self.tab_presets, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        preset_scroll.pack(fill="both", expand=True)
        self.preset_frame = preset_scroll

        # History wrapper
        hist_scroll = ctk.CTkScrollableFrame(self.tab_history, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        hist_scroll.pack(fill="both", expand=True)
        self.history_frame = hist_scroll

        # Log wrapper
        log_scroll = ctk.CTkScrollableFrame(self.tab_log, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        log_scroll.pack(fill="both", expand=True)
        self.log_scroll_frame = log_scroll

        # Build all tab contents
        self._build_add_tab()
        self._build_library_tab()
        self._build_presets_tab()
        self._build_history_tab()
        self._build_log_tab()
        self._build_settings_tab()
        self._build_settings_dirty_banner()
        self._build_streaming_tab()
        self._build_mixer_tab()
        self._build_keys_tab()

        # Show first tab
        self._sidebar_panels = {
            "Add": self.tab_add,
            "Library": self.tab_library,
            "Preset": self.tab_presets,
            "History": self.tab_history,
            "Log": self.tab_log,
            "Settings": self.tab_settings,
            "Stream": self.tab_stream,
            "Mixer": self.tab_mixer,
            "Keys": self.tab_keys,
        }
        self._current_sidebar_tab = "Add"
        self._pill_tabs.set_active(0)

    def _on_sidebar_tab_switch(self, idx: int, label: str) -> None:
        """Handle custom tab switch."""
        # Hide current
        if self._current_sidebar_tab in self._sidebar_panels:
            self._sidebar_panels[self._current_sidebar_tab].pack_forget()
        # Show new
        name = self._tab_names[idx]
        self._current_sidebar_tab = name
        if name in self._sidebar_panels:
            self._sidebar_panels[name].pack(fill="both", expand=True)

    # ── Add Song Tab ──
    def _build_add_tab(self) -> None:
        tab = self.tab_add

        # YouTube Section
        ctk.CTkFrame(tab, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(tab, text="🔗 Add from YouTube",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(12, 4))

        yt_row = ctk.CTkFrame(tab, fg_color="transparent")
        yt_row.pack(fill="x", padx=8, pady=(0, 4))

        self.yt_url_entry = ctk.CTkEntry(
            yt_row, placeholder_text="Paste YouTube URL here...",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.yt_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        # Right-click paste menu
        self._add_paste_menu(self.yt_url_entry)

        ctk.CTkButton(
            yt_row, text="📋", width=32, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
            command=lambda: self._paste_to_entry(self.yt_url_entry),
            font=ctk.CTkFont(size=16),
        ).pack(side="right")

        self.btn_yt_add = ctk.CTkButton(
            tab, text="➕ Add Video", height=32,
            fg_color=COLORS["accent"], hover_color="#4c9aed",
            command=self._add_youtube,
        )
        self.btn_yt_add.pack(fill="x", padx=8, pady=(0, 4))

        pl_row = ctk.CTkFrame(tab, fg_color="transparent")
        pl_row.pack(fill="x", padx=8, pady=(4, 4))

        self.yt_playlist_entry = ctk.CTkEntry(
            pl_row, placeholder_text="Paste YouTube Playlist URL...",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.yt_playlist_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._add_paste_menu(self.yt_playlist_entry)

        ctk.CTkButton(
            pl_row, text="📋", width=32, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
            command=lambda: self._paste_to_entry(self.yt_playlist_entry),
            font=ctk.CTkFont(size=16),
        ).pack(side="right")

        self.btn_yt_playlist = ctk.CTkButton(
            tab, text="📋 Add Playlist", height=32,
            fg_color=COLORS["green"], text_color="#000", hover_color="#2ea043",
            command=self._add_youtube_playlist,
        )
        self.btn_yt_playlist.pack(fill="x", padx=8, pady=(0, 8))

        # Separator
        ctk.CTkFrame(tab, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=8, pady=4)

        # Local File Section
        ctk.CTkFrame(tab, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(tab, text="📁 Add Local Audio File",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 4))

        self.file_label = ctk.CTkLabel(tab, text="No file selected",
                                        font=ctk.CTkFont(size=10),
                                        text_color=COLORS["text3"], anchor="w")
        self.file_label.pack(fill="x", padx=8)

        ctk.CTkButton(tab, text="📂 Browse Audio (mp3/wav/ogg)", height=32,
                       fg_color=COLORS["purple"], hover_color="#a371f7",
                       command=self._pick_file).pack(fill="x", padx=8, pady=(4, 8))

        ctk.CTkButton(tab, text="🎙 Add Mic Segment to Queue", height=32,
                       fg_color=COLORS["orange"], hover_color="#e3a928",
                       text_color="#000000",
                       command=self._add_mic_segment).pack(fill="x", padx=8, pady=(0, 8))

        # Separator
        ctk.CTkFrame(tab, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=8, pady=4)

        # Manual Section
        ctk.CTkFrame(tab, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(tab, text="✏️ Add Manually",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 4))

        self.manual_title = ctk.CTkEntry(tab, placeholder_text="Song title",
                                          fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.manual_title.pack(fill="x", padx=8, pady=(0, 4))

        self.manual_artist = ctk.CTkEntry(tab, placeholder_text="Artist (optional)",
                                           fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.manual_artist.pack(fill="x", padx=8, pady=(0, 4))

        self.manual_duration = ctk.CTkEntry(tab, placeholder_text="Duration (seconds)",
                                             fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.manual_duration.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkButton(tab, text="➕ Add Song", height=32,
                       fg_color=COLORS["accent"], hover_color="#4c9aed",
                       command=self._add_manual).pack(fill="x", padx=8, pady=(0, 8))

        # Separator
        ctk.CTkFrame(tab, height=1, fg_color=COLORS["border"]).pack(fill="x", padx=8, pady=4)

        # Insert Section
        ctk.CTkLabel(tab, text="📌 Insert Song at Position",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 4))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=8)
        self.insert_pos = ctk.CTkEntry(row, placeholder_text="Position", width=70,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.insert_pos.pack(side="left", padx=(0, 4))
        self.insert_title = ctk.CTkEntry(row, placeholder_text="Song title",
                                          fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.insert_title.pack(side="left", fill="x", expand=True)

        row2 = ctk.CTkFrame(tab, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(4, 0))
        self.insert_artist = ctk.CTkEntry(row2, placeholder_text="Artist",
                                           fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.insert_artist.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.insert_dur = ctk.CTkEntry(row2, placeholder_text="Seconds", width=70,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=8)
        self.insert_dur.pack(side="left")

        ctk.CTkButton(tab, text="📌 Insert", height=32,
                       fg_color=COLORS["orange"], text_color="#000", hover_color="#bb8009",
                       command=self._insert_song).pack(fill="x", padx=8, pady=(6, 8))

    # ── Library Tab ──
    def _build_library_tab(self) -> None:
        tab = self.tab_library

        self.search_entry = ctk.CTkEntry(
            tab, placeholder_text="🔍 Search songs...",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.search_entry.pack(fill="x", padx=8, pady=(8, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self._search_library())

        # library_frame is already created in _build_sidebar

    # ── Presets Tab ──
    def _build_presets_tab(self) -> None:
        tab = self.tab_presets

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(8, 6))

        self.preset_name_entry = ctk.CTkEntry(
            row, placeholder_text="Preset name",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.preset_name_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(row, text="💾 Save", width=70, height=32,
                       fg_color=COLORS["accent"], hover_color="#4c9aed",
                       command=self._save_preset).pack(side="right")

        # preset_frame is already created in _build_sidebar

    # ── History Tab ──
    def _build_history_tab(self) -> None:
        tab = self.tab_history
        # history_frame is already created in _build_sidebar

    # ── Log Tab ──
    def _build_log_tab(self) -> None:
        tab = self.tab_log

        # Header bar
        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.pack(fill="x", padx=4, pady=(8, 4))

        ctk.CTkLabel(hdr, text="📋 Application Log",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(side="left")

        # Filter dropdown
        self.log_level_var = ctk.StringVar(value="DEBUG")
        ctk.CTkOptionMenu(hdr, variable=self.log_level_var,
                           values=["DEBUG", "INFO", "WARNING", "ERROR"],
                           width=100, height=26,
                           fg_color=COLORS["bg_hover"],
                           button_color=COLORS["border"],
                           command=lambda _: self._refresh_log()).pack(side="left", padx=4)

        # Auto-scroll toggle
        self._log_auto_scroll = True
        self.log_autoscroll_btn = ctk.CTkButton(
            hdr, text="📌 Auto-scroll", width=100, height=26,
            fg_color=COLORS["accent"], hover_color="#4c9aed",
            corner_radius=6, font=ctk.CTkFont(size=10),
            command=self._toggle_log_autoscroll)
        self.log_autoscroll_btn.pack(side="left", padx=2)

        # Clear button
        ctk.CTkButton(hdr, text="🗑️ Clear", width=70, height=26,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       corner_radius=6, font=ctk.CTkFont(size=10),
                       command=self._clear_log).pack(side="right", padx=2)

        # Log count label
        self.log_count_label = ctk.CTkLabel(hdr, text="0 entries",
                                              font=ctk.CTkFont(size=10),
                                              text_color=COLORS["text3"])
        self.log_count_label.pack(side="right", padx=6)

        # Log text area
        self.log_text = ctk.CTkTextbox(
            tab, fg_color=COLORS["bg_input"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(family="Consolas", size=11),
            border_color=COLORS["border"], border_width=1,
            corner_radius=8, wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.log_text.configure(state="disabled")
        self._log_last_count = 0

    def _refresh_log(self) -> None:
        """Refresh the log textbox with latest entries."""
        try:
            level = self.log_level_var.get()
            entries = memory_handler.get_entries(min_level=level)
            count = len(entries)

            # Only update if there are new entries
            if count == self._log_last_count:
                return
            self._log_last_count = count

            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")

            # Color coding for log levels
            level_colors = {
                "DEBUG": COLORS["text3"],
                "INFO": COLORS["text"],
                "WARNING": COLORS["orange"],
                "ERROR": COLORS["red"],
                "CRITICAL": COLORS["red"],
            }

            for entry in entries:
                line = f"[{entry.timestamp}] [{entry.level:5s}] [{entry.logger}] {entry.message}\n"
                self.log_text.insert("end", line)

            self.log_count_label.configure(text=f"{count} entries")
            self.log_text.configure(state="disabled")

            if self._log_auto_scroll:
                self.log_text.see("end")
        except Exception:
            pass

    def _toggle_log_autoscroll(self) -> None:
        self._log_auto_scroll = not self._log_auto_scroll
        if self._log_auto_scroll:
            self.log_autoscroll_btn.configure(
                text="📌 Auto-scroll",
                fg_color=COLORS["accent"])
            self.log_text.see("end")
        else:
            self.log_autoscroll_btn.configure(
                text="📌 Pinned",
                fg_color=COLORS["bg_hover"])

    def _clear_log(self) -> None:
        memory_handler.clear()
        self._log_last_count = 0
        self._refresh_log()

    # ── Now Playing Bar (bottom) ──
    def _build_now_playing(self) -> None:
        # Accent top border
        self.np_accent = ctk.CTkFrame(self, fg_color=COLORS["accent"], height=3, corner_radius=0)
        self.np_accent.pack(fill="x", side="bottom")
        self.np_accent.pack_propagate(False)

        self.np_bar = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=0, height=120)
        self.np_bar.pack(fill="x", side="bottom")
        self.np_bar.pack_propagate(False)
        bar = self.np_bar

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        # Left: Song info
        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True)

        self.np_title = ctk.CTkLabel(info, text="No song playing",
                                      font=ctk.CTkFont(size=15, weight="bold"),
                                      text_color=COLORS["text"], anchor="w")
        self.np_title.pack(fill="x")

        self.np_artist = ctk.CTkLabel(info, text="Select a song from queue to start",
                                       font=ctk.CTkFont(size=11),
                                       text_color=COLORS["text2"], anchor="w")
        self.np_artist.pack(fill="x")

        self.np_status = ctk.CTkLabel(info, text="",
                                       font=ctk.CTkFont(size=10),
                                       text_color=COLORS["orange"], anchor="w")
        self.np_status.pack(fill="x")

        # VU Meter — stereo level display
        self.vu_meter = VumeterWidget(info, width=280, height=64, bg=COLORS["bg_card"])
        self.vu_meter.pack(fill="x", pady=(2, 0))

        self._download_mode = False
        self._jingle_playing = False
        self._jingle_was_playing = False
        self._ad_playing = False
        self._saved_playback_position = 0.0
        self._last_refresh_time = 0.0
        self._refresh_pending = False
        self.theme_mgr = ThemeManager()
        self.jingle = JingleConfig()
        self.mic = MicConfig()
        self.commercial = CommercialBreak()
        self.normalizer = VolumeNormalizer()
        self.eq = EQSettings()
        self.crossfade = CrossfadeSettings()
        self.editor = AudioEditor()
        self._refresh_pending = False
        self._settings_dirty = False
        self._restoring_settings = False
        self._pre_duck_volume = 80.0
        self._anthem_playing = False
        self._anthem_was_playing = False

        # Initialize new features
        self.visualizer = None  # Lazy init
        self.scheduler = Scheduler()
        self.anthem = NationalAnthem()
        self.remote_control = LiveLAN(app_instance=self)
        self._remote_running = False

        # Wire up anthem callbacks
        self.anthem.set_callbacks(
            play=self._anthem_play,
            pause=self._anthem_pause,
            resume=self._anthem_resume,
            status=lambda state, period: self.after(0, lambda: self._anthem_status(state, period)),
        )
        # Start anthem scheduler
        self.anthem.start()

        # Center: Controls
        controls = ctk.CTkFrame(inner, fg_color="transparent")
        controls.pack(side="left", padx=40)

        btn_row = ctk.CTkFrame(controls, fg_color="transparent")
        btn_row.pack()

        prev_btn = ctk.CTkButton(btn_row, text="⏮", width=44, height=44, corner_radius=22,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       font=ctk.CTkFont(size=16),
                       text_color=COLORS["text"],
                       command=self._prev_track)
        prev_btn.pack(side="left", padx=4)
        ToolTip(prev_btn, text="Previous", description="Play previous song")

        self.btn_play = ctk.CTkButton(
            btn_row, text="▶", width=56, height=56, corner_radius=28,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=20),
            text_color="#ffffff",
            command=self._toggle_play,
        )
        self.btn_play.pack(side="left", padx=4)
        ToolTip(self.btn_play, text="Play / Pause", description="Toggle playback")

        next_btn = ctk.CTkButton(btn_row, text="⏭", width=44, height=44, corner_radius=22,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       font=ctk.CTkFont(size=16),
                       text_color=COLORS["text"],
                       command=self._next_track)
        next_btn.pack(side="left", padx=4)
        ToolTip(next_btn, text="Next", description="Play next song")

        # Secondary row: Shuffle / Repeat / Crossfade
        mode_row = ctk.CTkFrame(controls, fg_color="transparent")
        mode_row.pack(pady=(6, 0))

        self.btn_shuffle = ctk.CTkButton(mode_row, text="🔀", width=36, height=30, corner_radius=8,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_shuffle)
        self.btn_shuffle.pack(side="left", padx=4)
        ToolTip(self.btn_shuffle, text="Shuffle", description="Toggle shuffle mode")

        self.btn_repeat = ctk.CTkButton(mode_row, text="🔁", width=36, height=30, corner_radius=8,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_repeat)
        self.btn_repeat.pack(side="left", padx=4)
        ToolTip(self.btn_repeat, text="Repeat", description="Toggle repeat mode")

        self.btn_crossfade = ctk.CTkButton(mode_row, text="🔄", width=36, height=30, corner_radius=8,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_crossfade)
        self.btn_crossfade.pack(side="left", padx=4)
        ToolTip(self.btn_crossfade, text="Crossfade", description="Toggle crossfade between songs")

        # Right: Device selector + Position
        right_frame = ctk.CTkFrame(inner, fg_color="transparent")
        right_frame.pack(side="right")

        # Device selector
        dev_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        dev_frame.pack(anchor="e", pady=(0, 2))

        ctk.CTkLabel(dev_frame, text="", font=ctk.CTkFont(size=11),
                      text_color=COLORS["text3"]).pack(side="left", padx=(0, 4))
        dev_icon = self._icon("headphones", 14, COLORS["text3"])
        if dev_icon:
            ctk.CTkLabel(dev_frame, image=dev_icon, text="").pack(side="left", padx=(0, 4))

        self.device_var = ctk.StringVar(value="Default")
        self.device_menu = ctk.CTkOptionMenu(
            dev_frame, variable=self.device_var,
            values=["Default"],
            width=180, height=26,
            fg_color=COLORS["bg_hover"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["text3"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=10),
            command=self._on_device_selected,
        )
        self.device_menu.pack(side="left")

        ctk.CTkButton(dev_frame, text="🔄", width=26, height=26,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       font=ctk.CTkFont(size=12),
                       command=self._refresh_device_list).pack(side="left", padx=(4, 0))
        ToolTip(dev_frame.winfo_children()[-1], text="Refresh Devices", description="Re-scan audio output devices")

        # Volume slider
        vol_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        vol_frame.pack(anchor="e", pady=(2, 2))

        self._muted = False
        self._saved_vol = 80.0

        self.btn_mute = ctk.CTkButton(
            vol_frame, text="🔊", width=28, height=26,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_mute,
        )
        self.btn_mute.pack(side="left", padx=(0, 2))

        ToolTip(self.btn_mute, text="Mute / Unmute", description="Toggle sound on/off")

        self.volume_var = ctk.DoubleVar(value=80.0)
        self.volume_slider = ctk.CTkSlider(
            vol_frame, from_=0, to=100,
            variable=self.volume_var,
            width=120, height=14,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color="#4c9aed",
            command=self._on_volume_change,
        )
        self.volume_slider.set(80.0)
        self.volume_slider.pack(side="left")

        self.volume_label = ctk.CTkLabel(vol_frame, text="80%",
                                          font=ctk.CTkFont(size=10),
                                          text_color=COLORS["text3"], width=32)
        self.volume_label.pack(side="left", padx=(4, 0))

        # Position label
        self.np_position = ctk.CTkLabel(right_frame, text="-- / --",
                                         font=ctk.CTkFont(size=12),
                                         text_color=COLORS["text3"])
        self.np_position.pack(anchor="e", pady=(2, 0))

        # Waveform display — above progress bar
        self.waveform = WaveformWidget(inner, width=400, height=40,
                                        bg=COLORS["bg_card"])
        self.waveform.pack(side="bottom", fill="x", padx=(0, 0), pady=(2, 0))

        # Progress bar — spans full width below everything
        prog_frame = ctk.CTkFrame(inner, fg_color="transparent")
        prog_frame.pack(side="bottom", fill="x", padx=(0, 0), pady=(2, 0))

        self.np_time_cur = ctk.CTkLabel(prog_frame, text="0:00",
                                         font=ctk.CTkFont(size=10),
                                         text_color=COLORS["text3"], width=50)
        self.np_time_cur.pack(side="left")

        self.np_progress = ctk.CTkSlider(
            prog_frame, from_=0, to=100,
            width=300, height=14,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color="#4c9aed",
        )
        self.np_progress.set(0)
        self.np_progress.pack(side="left", fill="x", expand=True, padx=8)

        self.np_time_total = ctk.CTkLabel(prog_frame, text="0:00",
                                           font=ctk.CTkFont(size=10),
                                           text_color=COLORS["text3"], width=50)
        self.np_time_total.pack(side="right")

    # ═══════════════════════════════════════
    # Queue Rendering
    # ═══════════════════════════════════════
    def _refresh_queue(self) -> None:
        """Refresh queue with batched rendering for large playlists."""
        # Clear old items
        for w in self.queue_frame.winfo_children():
            w.destroy()

        queue = self.rq.queue

        # Apply filter
        if self._queue_filter:
            queue = [s for i, s in enumerate(queue)
                     if self._queue_filter in s.title.lower()
                     or self._queue_filter in s.artist.lower()]

        total_dur = sum(s.duration for s in queue)

        if not queue:
            empty_frame = ctk.CTkFrame(self.queue_frame, fg_color="transparent")
            empty_frame.pack(pady=60, fill="x")
            ctk.CTkLabel(empty_frame, text="📭",
                          font=ctk.CTkFont(size=36)).pack()
            ctk.CTkLabel(empty_frame, text="Queue is empty",
                          font=ctk.CTkFont(size=14, weight="bold"),
                          text_color=COLORS["text2"]).pack(pady=(8, 4))
            ctk.CTkLabel(empty_frame, text="Add songs from YouTube, local files,\nor drag & drop files here",
                          font=ctk.CTkFont(size=11),
                          text_color=COLORS["text3"]).pack()
            self.queue_stats_label.configure(text="")
            self._queue_selected = None
            return

        self.queue_stats_label.configure(
            text=f"{len(queue)} songs · {self._fmt_dur(total_dur)}"
        )

        # Batched rendering: 20 items per frame, yield to UI between batches
        BATCH = 20
        total_items = len(queue)
        self._queue_render_cancelled = False

        def _render_batch(start: int) -> None:
            if self._queue_render_cancelled:
                return
            end = min(start + BATCH, total_items)
            for i in range(start, end):
                if self._queue_render_cancelled:
                    return
                self._create_queue_item(i, queue[i])
            if end < total_items:
                self.after(1, lambda: _render_batch(end))

        _render_batch(0)

    def _create_queue_item(self, i: int, song) -> None:
        """Create a single queue item widget."""
        is_current = i == self.rq.current_index
        is_playing_this = is_current and self._playing
        is_selected = (i == self._queue_selected)

        # Item background
        if is_current:
            item_bg = COLORS["bg_hover"]
            item_border = 1
            item_border_color = COLORS["accent"]
        elif is_selected:
            item_bg = COLORS["bg_hover"]
            item_border = 1
            item_border_color = COLORS["text3"]
        else:
            item_bg = COLORS["bg_card"]
            item_border = 0
            item_border_color = COLORS["bg_card"]

        item = ctk.CTkFrame(
            master=self.queue_frame,
            fg_color=item_bg,
            corner_radius=8,
        )
        if item_border:
            item.configure(border_width=item_border, border_color=item_border_color)
        item.pack(fill="x", pady=1, padx=2)

        inner = ctk.CTkFrame(item, fg_color="transparent")
        inner.pack(fill="x", padx=8, pady=6)

        # Status + Number
        status = "▶️" if is_playing_this else ("⏸️" if is_current else f"{i+1}.")
        ctk.CTkLabel(inner, text=status, width=28,
                      font=ctk.CTkFont(size=11),
                      text_color=COLORS["accent"] if is_current else COLORS["text3"],
                      anchor="w").pack(side="left")

        # Icon
        if song.source == "youtube":
            icon = "⚡" if song_cache.has(song.id) else "🔗"
        elif song.source == "mic":
            icon = "🎙"
        elif song.source == "file":
            icon = "📁"
        else:
            icon = "🎵"
        ctk.CTkLabel(inner, text=icon, width=20,
                      font=ctk.CTkFont(size=11),
                      text_color=COLORS["text"]).pack(side="left", padx=(0, 4))

        # Title + Artist
# Title + Artist
        info_frame = ctk.CTkFrame(inner, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(info_frame, text=song.title or "Unknown",
                      font=ctk.CTkFont(size=12, weight="bold" if is_current else "normal"),
                      text_color=COLORS["accent"] if is_current else COLORS["text"],
                      anchor="w").pack(anchor="w", fill="x")
        if song.artist:
            ctk.CTkLabel(info_frame, text=song.artist,
                          font=ctk.CTkFont(size=10),
                          text_color=COLORS["text2"], anchor="w").pack(anchor="w", fill="x")

        # Duration
        ctk.CTkLabel(inner, text=self._fmt_dur(song.duration), width=48,
                      font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"], anchor="e").pack(side="right")

        # Click handler
        idx = i
        item.bind("<Button-1>", lambda e, x=idx: self._on_queue_click(x))
        inner.bind("<Button-1>", lambda e, x=idx: self._on_queue_click(x))
        info_frame.bind("<Button-1>", lambda e, x=idx: self._on_queue_click(x))

        # Double-click to play
        item.bind("<Double-Button-1>", lambda e, x=idx: self._play_index(x))
        inner.bind("<Double-Button-1>", lambda e, x=idx: self._play_index(x))
        info_frame.bind("<Double-Button-1>", lambda e, x=idx: self._play_index(x))

        # Hover effects
        def _on_enter(e, w=item):
            if not is_current:
                w.configure(fg_color=COLORS["bg_hover"])
        def _on_leave(e, w=item, ibg=item_bg):
            if not is_current:
                w.configure(fg_color=ibg)
        for w in (item, inner, info_frame):
            w.bind("<Enter>", _on_enter)
            w.bind("<Leave>", _on_leave)

    def _on_queue_click(self, index: int) -> None:
        if self._queue_selected == index:
            return
        self._queue_selected = index
        self._refresh_queue()

    # ═══════════════════════════════════════
    # Playback
    # ═══════════════════════════════════════
    def _update_now_playing(self) -> None:
        idx = self.rq.current_index
        if idx >= 0 and idx < len(self.rq.queue):
            song = self.rq.queue[idx]
            self.np_title.configure(text=song.title or "Unknown")
            self.np_artist.configure(text=song.artist or "—")
            self.np_position.configure(text=f"{idx+1} / {len(self.rq.queue)}")
            self.btn_play.configure(text="⏸" if self._playing else "▶")
        else:
            self.np_title.configure(text="No song playing")
            self.np_artist.configure(text="Select a song from queue to start")
            self.np_position.configure(text="-- / --")
            self.btn_play.configure(text="▶")
            try:
                self.np_progress.set(0)
                self.np_time_cur.configure(text="0:00")
                self.np_time_total.configure(text="0:00")
                self.np_status.configure(text="")
                self.btn_play.configure(state="normal")
            except Exception:
                pass

    def _play_index(self, index: int) -> None:
        """Play song at index — download YouTube audio then play"""
        # Any manual/queue playback supersedes anthem/jingle playback — cancel
        # a pending anthem/jingle resume so it can't hijack the new selection.
        self._anthem_playing = False
        self._anthem_was_playing = False
        self._jingle_playing = False
        self._jingle_was_playing = False
        # Stop current playback
        self.audio_player.stop()
        self.rq.play(index)
        self._playing = True
        self._refresh_queue()
        self._update_now_playing()

        # Start real playback
        song = self.rq.queue[index]
        if song.source == "mic":
            self._play_mic_segment(song, index)
            return
        if song.source == "youtube" and song.url:
            self.np_time_total.configure(text=song.duration_str)
            self._download_mode = True
            self.np_status.configure(text="📥 Downloading... 0%", text_color=COLORS["orange"])
            self.btn_play.configure(state="disabled")
            # Consume saved playback position for YouTube
            yt_start = self._saved_playback_position
            self._saved_playback_position = 0.0
            self.audio_player.play_youtube(
                song.url,
                duration=song.duration,
                song_id=song.id,
                title=song.title,
                on_finish=lambda: self.after(0, self._on_track_finished),
                on_progress=lambda status, pct: self.after(0, self._on_download_progress, status, pct),
                start_position=yt_start,
            )
        elif song.file_path and os.path.isfile(song.file_path):
            self.np_time_total.configure(text=song.duration_str)
            # Apply volume normalizer gain if enabled
            if self.normalizer.enabled:
                gain = self.normalizer.get_gain(song.id, song.file_path)
                self.audio_player.volume = (self.volume_var.get() / 100.0) * gain
            self.audio_player.play_file(
                song.file_path,
                duration=song.duration,
                on_finish=lambda: self.after(0, self._on_track_finished),
            )
            # Restore playback position if applicable
            if self._saved_playback_position > 0:
                pos = self._saved_playback_position
                self._saved_playback_position = 0.0
                self.after(100, lambda p=pos: self.audio_player.seek(p))
            # Generate waveform
            self.after(100, lambda fp=song.file_path: self._load_waveform(fp))
            # Start streaming this file if streaming is active
            if streamer.is_streaming:
                streamer.on_track_change(song.title, song.artist, song.file_path)
        else:
            # Local song without file — simulate
            self.np_time_total.configure(text=song.duration_str)
            self.waveform.clear()

    def _play_mic_segment(self, song: Song, index: int) -> None:
        """Record and play a microphone queue item without blocking the GUI."""
        duration = song.mic_duration or song.duration
        device_index = song.mic_device_index
        output_path = str(Path(tempfile.gettempdir()) / f"freq_mic_{uuid.uuid4().hex}.wav")
        self._download_mode = True
        self.np_status.configure(text="🎙 Recording microphone...", text_color=COLORS["orange"])
        self.btn_play.configure(state="disabled")

        def _record() -> None:
            ok = record_wav(output_path, duration, device_index=device_index)

            def _start_recording() -> None:
                self._download_mode = False
                self.btn_play.configure(state="normal")
                if self.rq.current_index != index or not self._playing:
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    return
                if not ok:
                    self.np_status.configure(text="❌ Microphone recording failed", text_color=COLORS["red"])
                    self.rq.skip_next()
                    if self.rq.queue:
                        self._play_index(self.rq.current_index)
                    return
                self.np_status.configure(text="🎙 Playing microphone segment...", text_color=COLORS["orange"])
                def _mic_finished() -> None:
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    self.after(0, self._on_track_finished)

                if not self.audio_player.play_file(
                    output_path,
                    duration=duration,
                    on_finish=_mic_finished,
                ):
                    self.np_status.configure(text="❌ Cannot play microphone recording", text_color=COLORS["red"])
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    self.rq.skip_next()
                    if self.rq.queue:
                        self._play_index(self.rq.current_index)
                    return
                if streamer.is_streaming:
                    streamer.on_track_change(song.title, song.artist, output_path)

            self.after(0, _start_recording)

        threading.Thread(target=_record, daemon=True).start()

    def _toggle_play(self) -> None:
        if self._playing:
            # Pause
            self.audio_player.pause()
            self.rq.pause()
            self._playing = False
        else:
            # Resume or start
            if self.rq.current_index < 0 and self.rq.queue:
                self._play_index(0)
                return
            elif self.rq.current_index >= 0:
                if self.audio_player.available and self.audio_player.is_paused:
                    self.audio_player.resume()
                else:
                    self._play_index(self.rq.current_index)
                    return
            self._playing = True
        self._refresh_queue()
        self._update_now_playing()

    def _on_track_finished(self) -> None:
        """Song finished — check jingle/commercial, then go to next"""
        # --- Ad just finished — go to next song ---
        if self._ad_playing:
            self._ad_playing = False
            self.rq.skip_next()
            if self.rq.queue:
                self._play_index(self.rq.current_index)
            else:
                self._playing = False
                self._refresh_queue()
                self._update_now_playing()
            return

        # --- Jingle just finished — go to next song ---
        if self._jingle_playing:
            self._jingle_playing = False
            self.rq.skip_next()
            if self.rq.queue:
                self._play_index(self.rq.current_index)
            else:
                self._playing = False
                self._refresh_queue()
                self._update_now_playing()
            return

        # --- Normal song finished — check if ad or jingle should play ---
        # Commercial Break (checked first — higher priority)
        if self.commercial.enabled and self.commercial.ads:
            self.commercial.song_counter += 1
            if self.commercial.song_counter >= self.commercial.interval:
                self.commercial.song_counter = 0
                ad_file = self.commercial.ads[self.commercial.current_ad_index % len(self.commercial.ads)]
                self.commercial.current_ad_index += 1
                if os.path.isfile(ad_file):
                    self._ad_playing = True
                    self.np_status.configure(text="📢 Playing Ad...", text_color=COLORS["orange"])
                    self.audio_player.play_file(
                        ad_file, duration=0,
                        on_finish=lambda: self.after(0, self._on_track_finished),
                    )
                    return

        # Jingle
        if self.jingle.enabled and self.jingle.jingle_file:
            self.jingle.song_counter += 1
            if self.jingle.song_counter >= self.jingle.interval:
                self.jingle.song_counter = 0
                if os.path.isfile(self.jingle.jingle_file):
                    self._jingle_playing = True
                    self.np_status.configure(text="🎵 Jingle...", text_color=COLORS["purple"])
                    self.audio_player.play_file(
                        self.jingle.jingle_file, duration=0,
                        on_finish=lambda: self.after(0, self._on_track_finished),
                    )
                    return

        # --- Next song ---
        self.rq.skip_next()
        if self.rq.queue:
            self._play_index(self.rq.current_index)
        else:
            self._playing = False
            self._refresh_queue()
            self._update_now_playing()

    def _next_track(self) -> None:
        self.audio_player.stop()
        self.rq.skip_next()
        self._playing = True
        self._refresh_queue()
        self._update_now_playing()
        if self.rq.queue and self.rq.current_index >= 0:
            self._play_index(self.rq.current_index)

    def _prev_track(self) -> None:
        self.audio_player.stop()
        self.rq.skip_previous()
        self._playing = True
        self._refresh_queue()
        self._update_now_playing()
        if self.rq.queue and self.rq.current_index >= 0:
            self._play_index(self.rq.current_index)

    def _seek_forward(self) -> None:
        """Right arrow: seek forward 10 seconds"""
        if self._playing and self.audio_player.available:
            self.audio_player.seek_relative(10.0)

    def _seek_backward(self) -> None:
        """Left arrow: seek backward 10 seconds"""
        if self._playing and self.audio_player.available:
            self.audio_player.seek_relative(-10.0)

    def _volume_up(self) -> None:
        """Up arrow: volume +5%"""
        vol = min(100, self.volume_var.get() + 5)
        self.volume_var.set(vol)
        self._on_volume_change(vol)

    def _volume_down(self) -> None:
        """Down arrow: volume -5%"""
        vol = max(0, self.volume_var.get() - 5)
        self.volume_var.set(vol)
        self._on_volume_change(vol)

    # ═══════════════════════════════════════
    # Audio Device Selection
    # ═══════════════════════════════════════
    def _refresh_device_list(self) -> None:
        """Detect output devices and populate dropdown"""
        self.audio_mgr.refresh()
        output_devices = self.audio_mgr.get_output_devices()

        if not self.audio_mgr.available:
            names = ["(sounddevice not installed — pip install sounddevice)"]
        elif not output_devices:
            names = ["(No output devices found)"]
        else:
            names = ["Default"]
            for d in output_devices:
                # Truncate long names
                label = d.name if len(d.name) <= 40 else d.name[:37] + "..."
                names.append(label)

        self.device_menu.configure(values=names)

        # Restore selection
        selected = self.audio_mgr.get_selected_device()
        if selected and self.audio_mgr.available:
            for n in names:
                if selected.name in n:
                    self.device_var.set(n)
                    return
        self.device_var.set("Default")

    def _on_device_selected(self, choice: str) -> None:
        """When user selects a device from dropdown"""
        if not self.audio_mgr.available:
            return
        if choice == "Default":
            self.audio_mgr.set_device(-1)  # reset to system default
            return
        # Search device by name
        for d in self.audio_mgr.get_output_devices():
            if d.name in choice or choice in d.name:
                self.audio_mgr.set_device(d.id)
                break

    # ═══════════════════════════════════════
    # Volume Control
    # ═══════════════════════════════════════
    def _on_volume_change(self, value: float) -> None:
        """Adjust volume when slider moves"""
        vol = int(value)
        self.volume_label.configure(text=f"{vol}%")
        self.audio_player.volume = value / 100.0
        # Update mute icon based on volume
        if value == 0:
            self.btn_mute.configure(text="🔇")
            self._muted = True
        else:
            self._muted = False
            self._saved_vol = value
            self.btn_mute.configure(text="🔊")

    def _toggle_mute(self) -> None:
        """Mute/Unmute toggle"""
        if self._muted:
            # Unmute — restore saved volume
            self._muted = False
            self.volume_slider.set(self._saved_vol)
            self.audio_player.volume = self._saved_vol / 100.0
            self.volume_label.configure(text=f"{int(self._saved_vol)}%")
            self.btn_mute.configure(text="🔊")
        else:
            # Mute — save current volume then set to 0
            self._saved_vol = self.volume_var.get()
            self._muted = True
            self.volume_slider.set(0)
            self.audio_player.volume = 0.0
            self.volume_label.configure(text="0%")
            self.btn_mute.configure(text="🔇")

    # ═══════════════════════════════════════
    # View Toggle
    # ═══════════════════════════════════════
    def _toggle_queue_visibility(self) -> None:
        self._show_queue = not self._show_queue
        if self._show_queue:
            self.queue_panel_frame.pack(side="left", fill="both", expand=True, padx=(0, 5), pady=5)
            self.btn_toggle_queue.configure(fg_color=COLORS["accent"])
        else:
            self.queue_panel_frame.pack_forget()
            self.btn_toggle_queue.configure(fg_color=COLORS["bg_hover"])

    def _toggle_sidebar_visibility(self) -> None:
        self._show_sidebar = not self._show_sidebar
        if self._show_sidebar:
            self.sidebar_frame.pack(side="right", fill="y", padx=(5, 0), pady=5)
            self.btn_toggle_sidebar.configure(fg_color=COLORS["accent"])
        else:
            self.sidebar_frame.pack_forget()
            self.btn_toggle_sidebar.configure(fg_color=COLORS["bg_hover"])

    def _toggle_np_visibility(self) -> None:
        self._show_now_playing = not self._show_now_playing
        if self._show_now_playing:
            self.np_bar.pack(fill="x", side="bottom")
            self.btn_toggle_np.configure(fg_color=COLORS["accent"])
        else:
            self.np_bar.pack_forget()
            self.btn_toggle_np.configure(fg_color=COLORS["bg_hover"])

    

    # ═══════════════════════════════════════
    # Download Progress
    # ═══════════════════════════════════════
    def _on_download_progress(self, status: str, pct: float) -> None:
        """Update progress bar during YouTube download"""
        try:
            if status == "downloading":
                self._download_mode = True
                self.np_status.configure(text=f"📥 Downloading... {pct:.0f}%",
                                          text_color=COLORS["orange"])
                self.np_progress.set(pct)
                self.np_time_cur.configure(text=f"{pct:.0f}%")
                self.np_time_total.configure(text="100%")
            elif status == "processing":
                self.np_status.configure(text="🔄 Converting...",
                                          text_color=COLORS["purple"])
                self.np_progress.set(100)
            elif status == "cached":
                self._download_mode = False
                self.np_status.configure(text="⚡ Loaded from cache!", text_color=COLORS["green"])
                self.btn_play.configure(state="normal")
                self.np_progress.set(100)
                song = self.rq.queue[self.rq.current_index] if self.rq.current_index >= 0 else None
                if song:
                    self.np_time_cur.configure(text="0:00")
                    self.np_time_total.configure(text=song.duration_str)
                # Generate waveform from cached file
                if self.audio_player._yt_ready_file:
                    self.after(100, lambda fp=self.audio_player._yt_ready_file: self._load_waveform(fp))
                # Start streaming cached file if streaming is active
                if streamer.is_streaming and self.audio_player._yt_ready_file:
                    yt_file = self.audio_player._yt_ready_file
                    if song:
                        streamer.on_track_change(song.title, song.artist, yt_file)
            elif status == "playing":
                self._download_mode = False
                self.np_status.configure(text="", text_color=COLORS["text3"])
                self.btn_play.configure(state="normal")
                # Reset progress bar to playback mode
                song = self.rq.queue[self.rq.current_index] if self.rq.current_index >= 0 else None
                if song:
                    self.np_time_cur.configure(text="0:00")
                    self.np_time_total.configure(text=song.duration_str)
                self.np_progress.set(0)
                # Generate waveform from downloaded file
                if self.audio_player._yt_ready_file:
                    self.after(100, lambda fp=self.audio_player._yt_ready_file: self._load_waveform(fp))
                # Start streaming the downloaded file if streaming is active
                if streamer.is_streaming and self.audio_player._yt_ready_file:
                    yt_file = self.audio_player._yt_ready_file
                    if song:
                        streamer.on_track_change(song.title, song.artist, yt_file)
            elif status == "error":
                self._download_mode = False
                self.np_status.configure(text="❌ Download failed",
                                          text_color=COLORS["red"])
                self.btn_play.configure(state="normal")
                self.np_progress.set(0)
                self.np_time_cur.configure(text="0:00")
                self.np_time_total.configure(text="0:00")
        except Exception:
            pass

    # ═══════════════════════════════════════
    # Add Songs
    # ═══════════════════════════════════════
    def _add_youtube(self) -> None:
        url = self.yt_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a YouTube URL")
            return
        if not YouTubeManager.is_available():
            messagebox.showerror("Error", "yt-dlp not installed\nRun: pip install yt-dlp")
            return
        if YouTubeManager.is_playlist_url(url):
            messagebox.showinfo("Info", "Looks like a playlist. Use the 'Add Playlist' button instead")
            return

        self.btn_yt_add.configure(state="disabled", text="⏳ Loading...")

        def _worker():
            try:
                song = YouTubeManager.extract_video_info(url)
                if song:
                    self.after(0, lambda: self._on_yt_added(song))
                else:
                    self.after(0, lambda: messagebox.showerror("Error", "Cannot fetch video info"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.btn_yt_add.configure(state="normal", text="➕ Add Video"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_yt_added(self, song: Song) -> None:
        self.rq.add_song(song)
        self.yt_url_entry.delete(0, "end")
        self._refresh_queue()
        self._refresh_library()
        self._update_now_playing()
        # Auto-cache in background
        if song.source == "youtube" and song.url:
            song_cache.cache_song_background(song.id, song.title, song.url, song.duration)

    def _add_youtube_playlist(self) -> None:
        url = self.yt_playlist_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a YouTube Playlist URL")
            return
        if not YouTubeManager.is_available():
            messagebox.showerror("Error", "yt-dlp not installed")
            return

        self.btn_yt_playlist.configure(state="disabled", text="⏳ Counting songs...")

        def _count_worker():
            """Step 1: Fast count (no detail fetch)."""
            try:
                count, entries = YouTubeManager.count_playlist(url)
                if count == 0:
                    self.after(0, lambda: messagebox.showerror("Error", "No videos found in playlist"))
                    return
                # Show popup on main thread, then fetch range
                self.after(0, lambda: self._show_playlist_dialog(url, count, entries))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.btn_yt_playlist.configure(
                    state="normal", text="📋 Add Playlist"))

        threading.Thread(target=_count_worker, daemon=True).start()

    def _show_playlist_dialog(self, url: str, total: int, entries: list) -> None:
        """Step 2: Show popup (main thread), then fetch selected range."""
        if total > 100:
            dialog = PlaylistRangeDialog(self, total)
            self.wait_window(dialog)
            result = dialog.result
            if result is None:
                self.btn_yt_playlist.configure(state="normal", text="📋 Add Playlist")
                return
            elif result == "all":
                start, end = 1, total
            else:
                start, end = result
        else:
            start, end = 1, total

        count = end - start + 1
        self.btn_yt_playlist.configure(state="disabled", text=f"⏳ Loading {count} songs...")

        def _fetch_worker():
            try:
                songs = YouTubeManager.extract_playlist_range(entries, start, end)
                if songs:
                    for s in songs:
                        self.rq.add_song(s)
                self.after(0, lambda c=len(songs): self._on_playlist_added(c))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.btn_yt_playlist.configure(
                    state="normal", text="📋 Add Playlist"))

        threading.Thread(target=_fetch_worker, daemon=True).start()

    def _on_playlist_added(self, count: int) -> None:
        self.yt_playlist_entry.delete(0, "end")
        self._refresh_queue()
        self._refresh_library()
        self._update_now_playing()
        # Show notification
        total = len(self.rq.queue)
        self._show_toast(f"✅ Added {count} song{'s' if count != 1 else ''} from playlist  (Queue: {total} songs)", duration=4000)
        # Auto-cache all YouTube songs in background
        for s in self.rq.queue:
            if s.source == "youtube" and s.url:
                song_cache.cache_song_background(s.id, s.title, s.url, s.duration)

    def _add_manual(self) -> None:
        title = self.manual_title.get().strip()
        if not title:
            messagebox.showwarning("Warning", "Please enter a song title")
            return
        artist = self.manual_artist.get().strip()
        dur_str = self.manual_duration.get().strip()
        dur = float(dur_str) if dur_str else 0

        self.rq.add_song(Song(title=title, artist=artist, duration=dur, source="local"))
        self.manual_title.delete(0, "end")
        self.manual_artist.delete(0, "end")
        self.manual_duration.delete(0, "end")
        self._refresh_queue()
        self._refresh_library()

    def _pick_file(self) -> None:
        """Open file dialog for user to select audio file"""
        import subprocess
        from pathlib import Path

        filetypes = [
            ("Audio files", "*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.wma *.opus"),
            ("MP3", "*.mp3"),
            ("WAV", "*.wav"),
            ("OGG", "*.ogg"),
            ("FLAC", "*.flac"),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(
            title="Select Audio File",
            filetypes=filetypes,
        )
        if not path:
            return

        p = Path(path)
        filename = p.stem  # Filename without extension

        # Try to get duration with mutagen
        duration = 0.0
        try:
            from mutagen import File as MutagenFile
            mf = MutagenFile(path)
            if mf and mf.info:
                duration = mf.info.length
        except Exception:
            try:
                # Try pygame.mixer
                import pygame
                if pygame.mixer.get_init():
                    snd = pygame.mixer.Sound(path)
                    duration = snd.get_length()
            except Exception:
                pass

        song = Song(
            title=filename,
            source="file",
            file_path=path,
            duration=duration,
        )
        self.rq.add_song(song)
        self.file_label.configure(text=f"✅ {filename}")
        self._refresh_queue()
        self._refresh_library()
        messagebox.showinfo("Success", f"Added: {filename}")

    def _insert_song(self) -> None:
        pos_str = self.insert_pos.get().strip()
        title = self.insert_title.get().strip()
        if not pos_str or not title:
            messagebox.showwarning("Warning", "Please enter position and song title")
            return
        try:
            pos = int(pos_str)
        except ValueError:
            messagebox.showwarning("Warning", "Position must be a number")
            return

        artist = self.insert_artist.get().strip()
        dur_str = self.insert_dur.get().strip()
        dur = float(dur_str) if dur_str else 0

        self.rq.insert_song(pos, Song(title=title, artist=artist, duration=dur, source="local"))
        self.insert_pos.delete(0, "end")
        self.insert_title.delete(0, "end")
        self.insert_artist.delete(0, "end")
        self.insert_dur.delete(0, "end")
        self._refresh_queue()
        self._refresh_library()

    # ═══════════════════════════════════════
    # Queue Management
    # ═══════════════════════════════════════
    def _remove_selected(self) -> None:
        if self._queue_selected is None:
            messagebox.showinfo("Info", "Please select a song from the queue first")
            return
        self.rq.remove_song(self._queue_selected)
        self._queue_selected = None
        self._refresh_queue()
        self._update_now_playing()

    def _move_selected(self, direction: int) -> None:
        if self._queue_selected is None:
            messagebox.showinfo("Info", "Please select a song from the queue first")
            return
        new_idx = self._queue_selected + direction
        if not self.rq._valid_index(new_idx):
            return
        self.rq.move_song(self._queue_selected, new_idx)
        self._queue_selected = new_idx
        self._refresh_queue()

    def _clear_queue(self) -> None:
        if messagebox.askyesno("Confirm", "Clear entire queue?"):
            self.rq.clear_queue()
            self._playing = False
            self._queue_selected = None
            self._refresh_queue()
            self._update_now_playing()

    # ═══════════════════════════════════════
    # Library
    # ═══════════════════════════════════════
    def _refresh_library(self, query: str = "") -> None:
        for w in self.library_frame.winfo_children():
            w.destroy()

        songs = self.rq.library
        if query:
            ql = query.lower()
            songs = [s for s in songs if ql in s.title.lower() or ql in s.artist.lower()]

        if not songs:
            ctk.CTkLabel(self.library_frame, text="📚 No songs in library",
                          font=ctk.CTkFont(size=11),
                          text_color=COLORS["text3"]).pack(pady=20)
            return

        # Batched rendering for library
        BATCH = 30
        total = len(songs)
        self._lib_render_cancelled = False

        def _render_lib_batch(start: int) -> None:
            if self._lib_render_cancelled:
                return
            end = min(start + BATCH, total)
            for i in range(start, end):
                if self._lib_render_cancelled:
                    return
                self._create_library_item(songs[i])
            if end < total:
                self.after(1, lambda: _render_lib_batch(end))

        _render_lib_batch(0)

    def _create_library_item(self, song) -> None:
        """Create a single library item widget."""
        item = ctk.CTkFrame(self.library_frame, fg_color=COLORS["bg_hover"], corner_radius=6, height=40)
        item.pack(fill="x", pady=1, padx=2)
        item.pack_propagate(False)

        inner = ctk.CTkFrame(item, fg_color=COLORS["bg_hover"])
        inner.pack(fill="both", expand=True, padx=6, pady=0)

        icon = "🔗" if song.source == "youtube" else "🎵"
        ctk.CTkLabel(inner, text=icon, width=20,
                      font=ctk.CTkFont(size=10),
                      text_color=COLORS["text"]).pack(side="left", pady=8)

        info = ctk.CTkFrame(inner, fg_color=COLORS["bg_hover"])
        info.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=8)
        ctk.CTkLabel(info, text=song.title, font=ctk.CTkFont(size=11),
                      text_color=COLORS["text"], anchor="w").pack(anchor="w", fill="x")
        if song.artist:
            ctk.CTkLabel(info, text=song.artist, font=ctk.CTkFont(size=9),
                          text_color=COLORS["text2"], anchor="w").pack(anchor="w", fill="x")

        ctk.CTkLabel(inner, text=self._fmt_dur(song.duration), width=45,
                      font=ctk.CTkFont(size=9),
                      text_color=COLORS["text3"], anchor="e").pack(side="right", pady=8)

        s = song
        ctk.CTkButton(inner, text="➕", width=28, height=24,
                       fg_color=COLORS["green"], text_color="#000",
                       hover_color="#2ea043",
                       font=ctk.CTkFont(size=10),
                       command=lambda s=s: self._add_from_library(s)).pack(side="right", padx=(4, 0), pady=8)

    def _search_library(self) -> None:
        query = self.search_entry.get().strip()
        self._refresh_library(query)

    def _add_from_library(self, song: Song) -> None:
        self.rq.add_song(song)
        self._refresh_queue()

    # ═══════════════════════════════════════
    # Presets
    # ═══════════════════════════════════════
    def _refresh_presets(self) -> None:
        for w in self.preset_frame.winfo_children():
            w.destroy()

        if not self.rq.presets:
            ctk.CTkLabel(self.preset_frame, text="📂 No presets yet",
                          font=ctk.CTkFont(size=11),
                          text_color=COLORS["text3"]).pack(pady=20)
            return

        for name, ids in self.rq.presets.items():
            item = ctk.CTkFrame(self.preset_frame, fg_color=COLORS["bg_hover"], corner_radius=6)
            item.pack(fill="x", pady=3, padx=2)

            inner = ctk.CTkFrame(item, fg_color=COLORS["bg_hover"])
            inner.pack(fill="x", padx=10, pady=8)

            ctk.CTkLabel(inner, text=f"📂 {name}",
                          font=ctk.CTkFont(size=12, weight="bold"),
                          text_color=COLORS["text"], anchor="w").pack(side="left")
            ctk.CTkLabel(inner, text=f"{len(ids)} songs",
                          font=ctk.CTkFont(size=10),
                          text_color=COLORS["text3"], anchor="w").pack(side="left", padx=8)

            ctk.CTkButton(inner, text="📥", width=30, height=26,
                           fg_color=COLORS["accent"], hover_color="#4c9aed",
                           command=lambda n=name: self._load_preset(n)).pack(side="right")

    def _save_preset(self) -> None:
        name = self.preset_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Warning", "Please enter a preset name")
            return
        self.rq.save_preset(name)
        self.preset_name_entry.delete(0, "end")
        self._refresh_presets()

    def _load_preset(self, name: str) -> None:
        self.rq.load_preset(name)
        self._refresh_queue()
        self._refresh_library()
        self._update_now_playing()

    # ═══════════════════════════════════════
    # History
    # ═══════════════════════════════════════
    def _refresh_history(self) -> None:
        for w in self.history_frame.winfo_children():
            w.destroy()

        if not self.rq.history:
            ctk.CTkLabel(self.history_frame, text="📜 No history yet",
                          font=ctk.CTkFont(size=11),
                          text_color=COLORS["text3"]).pack(pady=20)
            return

        for i, song in enumerate(reversed(self.rq.history[-30:]), 1):
            inner = ctk.CTkFrame(self.history_frame, fg_color=COLORS["bg_hover"], corner_radius=4, height=34)
            inner.pack(fill="x", pady=1, padx=2)
            inner.pack_propagate(False)

            ctk.CTkLabel(inner, text=f"{i}.", width=30, anchor="w",
                          font=ctk.CTkFont(size=10),
                          text_color=COLORS["text3"]).pack(side="left", pady=6)
            icon = "🔗" if song.source == "youtube" else "🎵"
            ctk.CTkLabel(inner, text=f"{icon} {song.title}",
                          font=ctk.CTkFont(size=11),
                          text_color=COLORS["text"], anchor="w").pack(side="left", fill="x", expand=True, pady=6)

    # ═══════════════════════════════════════
    # Save / Load / Demo
    # ═══════════════════════════════════════
    # \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
    # Playback Modes
    # \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
    def _toggle_shuffle(self) -> None:
        self.rq.modes.toggle_shuffle()
        on = self.rq.modes.shuffle
        self.btn_shuffle.configure(fg_color=COLORS["accent"] if on else "transparent",
                                   text_color="#fff" if on else COLORS["text"])

    def _toggle_repeat(self) -> None:
        mode = self.rq.modes.toggle_repeat()
        icons = {RepeatMode.OFF: "\U0001f501", RepeatMode.REPEAT_ALL: "\U0001f501", RepeatMode.REPEAT_ONE: "\U0001f502"}
        colors = {RepeatMode.OFF: "transparent", RepeatMode.REPEAT_ALL: COLORS["accent"], RepeatMode.REPEAT_ONE: COLORS["green"]}
        self.btn_repeat.configure(text=icons[mode], fg_color=colors[mode],
                                  text_color="#fff" if mode != RepeatMode.OFF else COLORS["text"])

    def _toggle_crossfade(self) -> None:
        self.crossfade.enabled = not self.crossfade.enabled
        self.btn_crossfade.configure(fg_color=COLORS["accent"] if self.crossfade.enabled else "transparent",
                                     text_color="#fff" if self.crossfade.enabled else COLORS["text"])

    # ═════════════════════════════════════════
    # Visualizer
    # ═════════════════════════════════════════
    def _toggle_visualizer(self) -> None:
        """Toggle the audio visualizer display."""
        if self.visualizer is None:
            self.visualizer = VisualizerWidget(
                self.np_bar, width=400, height=100, bar_count=32,
                theme=COLORS
            )
            self.visualizer.pack(side="left", padx=10, pady=5)
            self.visualizer.start()
        else:
            if self.visualizer.canvas.winfo_viewable():
                self.visualizer.stop()
                self.visualizer.canvas.pack_forget()
            else:
                self.visualizer.canvas.pack(side="left", padx=10, pady=5)
                self.visualizer.start()

    def _update_visualizer(self) -> None:
        """Update visualizer with current audio data."""
        if self.visualizer and self.visualizer.canvas.winfo_viewable():
            # Get audio data from player
            try:
                if hasattr(self.audio_player, 'get_audio_data'):
                    data = self.audio_player.get_audio_data()
                    if data:
                        self.visualizer.update_spectrum(data)
            except Exception:
                pass
            self.after(50, self._update_visualizer)

    # ═════════════════════════════════════════
    # Remote Control
    # ═════════════════════════════════════════
    def _toggle_remote_control(self) -> None:
        """Toggle the web remote control server."""
        if self._remote_running:
            self.remote_control.stop()
            self._remote_running = False
            self._show_toast("🛑 Remote Control stopped")
        else:
            url = self.remote_control.start()
            self._remote_running = True
            self._show_toast(f"🌐 Remote Control: {url}")

    # ═════════════════════════════════════════
    # Scheduler UI
    # ═════════════════════════════════════════
    def _show_scheduler_dialog(self) -> None:
        """Show the scheduler dialog."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("⏰ Scheduler")
        dialog.geometry("500x400")
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(self)
        dialog.grab_set()

        # Header
        header = ctk.CTkFrame(dialog, fg_color=COLORS["accent"], height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="⏰  Scheduler", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="#ffffff").pack(pady=15)

        # Quick add
        add_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        add_frame.pack(fill="x", padx=20, pady=10)

        ctk.CTkLabel(add_frame, text="Add Quick Event:", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w")

        row = ctk.CTkFrame(add_frame, fg_color="transparent")
        row.pack(fill="x", pady=(5, 0))

        time_var = ctk.StringVar(value="30")
        ctk.CTkEntry(row, textvariable=time_var, width=60, placeholder_text="Minutes").pack(side="left", padx=(0, 5))
        ctk.CTkLabel(row, text="minutes from now", text_color=COLORS["text2"]).pack(side="left")

        def add_quick():
            mins = int(time_var.get() or 30)
            event = self.scheduler.create_quick_event(
                ScheduleAction.PLAY_JINGLE, mins
            )
            self._show_toast(f"⏰ Event scheduled in {mins} min")
            dialog.destroy()

        ctk.CTkButton(row, text="➕ Add", command=add_quick).pack(side="right")

        # Event list
        events_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        events_frame.pack(fill="both", expand=True, padx=20, pady=10)

        events = self.scheduler.list_events()
        if events:
            for event in events:
                e_frame = ctk.CTkFrame(events_frame, fg_color=COLORS["bg_card"], corner_radius=8)
                e_frame.pack(fill="x", pady=2)

                ctk.CTkLabel(e_frame, text=f"{event.action.value} @ {event.time}",
                             font=ctk.CTkFont(size=11), text_color=COLORS["text"]).pack(side="left", padx=10, pady=8)
        else:
            ctk.CTkLabel(events_frame, text="No scheduled events",
                         text_color=COLORS["text3"]).pack(pady=20)

        # Start scheduler
        self.scheduler.start()

    def _toggle_theme(self) -> None:
        global COLORS
        COLORS = self.theme_mgr.toggle()
        self._apply_theme()
        icon = "\u2600\ufe0f" if self.theme_mgr.is_dark else "\U0001f319"
        self.btn_theme.configure(text=icon)

    def _filter_queue(self) -> None:
        self._queue_filter = self.queue_search_entry.get().strip().lower()
        self._refresh_queue()

    def _show_cache_menu(self) -> None:
        """Show cache info and management buttons"""
        stats = song_cache.get_stats()
        msg = stats + "\n\nClear all cache?\n(Songs will be re-downloaded when played)"
        result = messagebox.askyesno("📦 Cache Manager", msg)
        if result:
            count = song_cache.clear()
            self._refresh_queue()
            messagebox.showinfo("Success", f"🧹 Cache cleared ({count} files)")

    def _open_file(self) -> None:
        """Open .freq file"""
        path = filedialog.askopenfilename(
            title="Open FreQ Playlist",
            filetypes=[("FreQ Playlist", "*.freq"), ("All files", "*.*")],
        )
        if not path:
            return
        self.rq.load_from_file(path)
        self._restore_settings(self.rq.last_settings)
        self._refresh_queue()
        self._refresh_library()
        self._refresh_presets()
        self._refresh_history()
        self._update_now_playing()

    def _save(self) -> None:
        """Save .freq file (use existing path, or Save As)"""
        self._sync_anthem_times()
        settings = self._collect_settings()
        if self.rq._save_path.exists() or self.rq._save_path.name != "radio_queue.freq":
            self.rq.save_to_file(settings=settings)
            messagebox.showinfo("Success", f"💾 Saved: {self.rq._save_path.name}")
        else:
            self._save_as()

    def _save_as(self) -> None:
        """Save .freq file — let user choose location"""
        self._sync_anthem_times()
        path = filedialog.asksaveasfilename(
            title="Save FreQ Playlist",
            defaultextension=".freq",
            filetypes=[("FreQ Playlist", "*.freq"), ("All files", "*.*")],
            initialfile=self.rq._save_path.stem,
        )
        if not path:
            return
        settings = self._collect_settings()
        self.rq.save_to_file(path, settings=settings)
        messagebox.showinfo("Success", f"💾 Saved: {Path(path).name}")

    # ── Settings save/restore ──
    def _collect_settings(self) -> dict:
        """Collect all current settings into a serializable dict."""
        # Playback position
        pos = 0.0
        if self._playing and self.audio_player.available:
            pos = self.audio_player.get_position()
        return {
            "volume": self.volume_var.get(),
            "playback_position": pos,
            "jingle": {
                "enabled": self.jingle.enabled,
                "jingle_file": self.jingle.jingle_file,
                "interval": self.jingle.interval,
                "fade_duration": self.jingle.fade_duration,
            },
            "mic": {
                "device_index": self.mic.device_index,
                "gain": self.mic.gain,
                "timer_duration": self.mic.timer_duration,
                "duck_volume": self.mic.duck_volume,
            },
            "commercial": {
                "enabled": self.commercial.enabled,
                "ads": self.commercial.ads,
                "interval": self.commercial.interval,
            },
            "normalizer": {
                "enabled": self.normalizer.enabled,
                "target_lufs": self.normalizer.target_lufs,
            },
            "eq": {
                "enabled": self.eq.enabled,
                "preset": self.eq.preset,
                "bass": self.eq.bass,
                "mid": self.eq.mid,
                "treble": self.eq.treble,
                "reverb": self.eq.reverb,
                "delay": self.eq.delay,
                "speed": self.eq.speed,
            },
            "crossfade": {
                "enabled": self.crossfade.enabled,
                "duration": self.crossfade.duration,
                "fade_in": self.crossfade.fade_in,
                "fade_out": self.crossfade.fade_out,
            },
            "anthem": self.anthem.config.to_dict(),
            "stream": {
                "name": self.stream_name.get() if hasattr(self, 'stream_name') else "FreQ Radio",
                "protocol": self.stream_protocol_var.get() if hasattr(self, 'stream_protocol_var') else "Icecast",
                "ssl": self.stream_ssl_var.get() if hasattr(self, 'stream_ssl_var') else False,
                "codec": self.stream_codec_var.get() if hasattr(self, 'stream_codec_var') else "mp3",
                "bitrate": self.stream_bitrate_var.get() if hasattr(self, 'stream_bitrate_var') else "128",
                # Protocol-specific fields
                "shoutcast": self._get_stream_fields(self._shoutcast_fields) if hasattr(self, '_shoutcast_fields') else {},
                "icecast": self._get_stream_fields(self._icecast_fields) if hasattr(self, '_icecast_fields') else {},
                "webrtc": self._get_stream_fields(self._webrtc_fields) if hasattr(self, '_webrtc_fields') else {},
            },
            "keybindings": self._collect_keybindings() if hasattr(self, '_key_rows') else {},
        }

    def _get_stream_fields(self, fields: dict) -> dict:
        """Extract values from stream field widgets."""
        result = {}
        for key in ["host", "port", "password", "mount", "bearer", "whip_url", "ice_server"]:
            if key in fields and hasattr(fields[key], 'get'):
                result[key] = fields[key].get()
        return result

    def _restore_settings(self, settings: dict) -> None:
        """Restore settings from a dict loaded from .freq file."""
        if not settings:
            return
        self._restoring_settings = True
        try:
            self._restore_settings_impl(settings)
        finally:
            self._restoring_settings = False
            self._clear_settings_dirty()

    def _restore_settings_impl(self, settings: dict) -> None:
        # Volume
        vol = settings.get("volume", 80.0)
        self.volume_var.set(vol)
        self.volume_slider.set(vol)
        self.volume_label.configure(text=f"{int(vol)}%")
        self._on_volume_change(vol)
        # Jingle
        j = settings.get("jingle", {})
        if j:
            self.jingle.enabled = j.get("enabled", False)
            self.jingle.jingle_file = j.get("jingle_file", "")
            self.jingle.interval = j.get("interval", 5)
            self.jingle.fade_duration = j.get("fade_duration", 1.0)
            self.jingle_var.set(self.jingle.enabled)
            self.jingle_interval_entry.delete(0, "end")
            self.jingle_interval_entry.insert(0, str(self.jingle.interval))
            if self.jingle.jingle_file:
                from pathlib import Path as _P
                self.jingle_label.configure(text=_P(self.jingle.jingle_file).name)
        # Mic
        m = settings.get("mic", {})
        if m:
            self.mic.device_index = m.get("device_index")
            self.mic.gain = m.get("gain", 1.0)
            self.mic.timer_duration = m.get("timer_duration", 30.0)
            self.mic.duck_volume = m.get("duck_volume", 0.3)
            self.mic_gain_slider.set(self.mic.gain)
            self.mic_gain_label.configure(text=f"{self.mic.gain:.1f}x")
            self.mic_duck_slider.set(self.mic.duck_volume)
            self.mic_duck_label.configure(text=f"{int(self.mic.duck_volume * 100)}%")
            self.mic_timer_entry.delete(0, "end")
            self.mic_timer_entry.insert(0, str(int(self.mic.timer_duration)))
            # Restore device selection in dropdown
            if self.mic.device_index is not None:
                for d in self._mic_input_devices:
                    if d.id == self.mic.device_index:
                        self.mic_device_var.set(d.name)
                        break
        # Commercial
        c = settings.get("commercial", {})
        if c:
            self.commercial.enabled = c.get("enabled", False)
            self.commercial.ads = c.get("ads", [])
            self.commercial.interval = c.get("interval", 10)
            self.commercial_var.set(self.commercial.enabled)
            self.commercial_count_label.configure(text=f"{len(self.commercial.ads)} ad files")
            self.ad_interval_entry.delete(0, "end")
            self.ad_interval_entry.insert(0, str(self.commercial.interval))
        # Normalizer
        n = settings.get("normalizer", {})
        if n:
            self.normalizer.enabled = n.get("enabled", False)
            self.normalizer.target_lufs = n.get("target_lufs", -14.0)
            self.norm_var.set(self.normalizer.enabled)
            self.norm_slider.set(self.normalizer.target_lufs)
            self.norm_label.configure(text=str(int(self.normalizer.target_lufs)))
        # EQ
        e = settings.get("eq", {})
        if e:
            self.eq.enabled = e.get("enabled", False)
            self.eq.preset = e.get("preset", "flat")
            self.eq.bass = e.get("bass", 0.0)
            self.eq.mid = e.get("mid", 0.0)
            self.eq.treble = e.get("treble", 0.0)
            self.eq.reverb = e.get("reverb", 0.0)
            self.eq.delay = e.get("delay", 0.0)
            self.eq.speed = e.get("speed", 1.0)
            self.eq_preset_var.set(self.eq.preset)
            for attr, sl in self.eq_sliders.items():
                sl.set(getattr(self.eq, attr))
        # Crossfade
        x = settings.get("crossfade", {})
        if x:
            self.crossfade.enabled = x.get("enabled", False)
            self.crossfade.duration = x.get("duration", 3.0)
            self.crossfade.fade_in = x.get("fade_in", True)
            self.crossfade.fade_out = x.get("fade_out", True)
        # Anthem
        a = settings.get("anthem", {})
        if a:
            self.anthem.config.enabled = a.get("enabled", True)
            self.anthem.config.morning_time = a.get("morning_time", "08:00")
            self.anthem.config.evening_time = a.get("evening_time", "18:00")
            self.anthem.config.anthem_file = a.get("anthem_file", "")
            self.anthem.config.duration = a.get("duration", 0.0)
            # Auto-detect duration from file if not saved or zero
            if self.anthem.config.duration <= 0 and self.anthem.config.anthem_file:
                self.anthem.config.duration = self._detect_anthem_duration()
            self.anthem.config.fade_in = a.get("fade_in", 2.0)
            self.anthem.config.fade_out = a.get("fade_out", 2.0)
            self.anthem.config.pause_before = a.get("pause_before", 1.0)
            self.anthem.config.resume_after = a.get("resume_after", True)
            self.anthem.config.play_daily = a.get("play_daily", True)
            self.anthem.config.days = a.get("days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
            # Update UI
            self.anthem_var.set(self.anthem.config.enabled)
            self.anthem_resume_var.set(self.anthem.config.resume_after)
            self.anthem_morning.delete(0, "end")
            self.anthem_morning.insert(0, self.anthem.config.morning_time)
            self.anthem_evening.delete(0, "end")
            self.anthem_evening.insert(0, self.anthem.config.evening_time)
            if self.anthem.config.anthem_file:
                from pathlib import Path as _P
                self.anthem_label.configure(text=_P(self.anthem.config.anthem_file).name)
        # Stream settings
        s = settings.get("stream", {})
        if s:
            if hasattr(self, 'stream_name'):
                self.stream_name.delete(0, "end")
                self.stream_name.insert(0, s.get("name", "FreQ Radio"))
            if hasattr(self, 'stream_protocol_var'):
                self.stream_protocol_var.set(s.get("protocol", "Icecast"))
            if hasattr(self, 'stream_ssl_var'):
                self.stream_ssl_var.set(s.get("ssl", False))
            if hasattr(self, 'stream_codec_var'):
                self.stream_codec_var.set(s.get("codec", "mp3"))
            if hasattr(self, 'stream_bitrate_var'):
                self.stream_bitrate_var.set(s.get("bitrate", "128"))
            # Restore protocol-specific fields
            self._restore_stream_fields(self._shoutcast_fields, s.get("shoutcast", {}))
            self._restore_stream_fields(self._icecast_fields, s.get("icecast", {}))
            self._restore_stream_fields(self._webrtc_fields, s.get("webrtc", {}))
            # Trigger protocol change to show correct fields
            self.after(100, self._on_protocol_change)
        # Playback position — will be restored when user presses play
        self._saved_playback_position = settings.get("playback_position", 0.0)
        # Keybindings
        self._restore_keybindings(settings.get("keybindings", {}))

    def _restore_stream_fields(self, fields: dict, values: dict) -> None:
        """Restore stream field values from saved settings."""
        if not values:
            return
        for key, val in values.items():
            if key in fields and hasattr(fields[key], 'delete'):
                fields[key].delete(0, "end")
                fields[key].insert(0, val)

    def _load_demo(self) -> None:
        demos = generate_demo_songs()
        self.rq.add_songs(demos)
        self._refresh_queue()
        self._refresh_library()
        self._update_now_playing()

    # ═══════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════
    # Logo
    # ═══════════════════════════════════════
    def _set_window_icon(self) -> None:
        """Set the window icon from logo.ico (Windows) or logo.png (other OS)"""
        from pathlib import Path
        base = Path(__file__).parent

        if sys.platform == "win32":
            ico_path = base / "logo.ico"
            if ico_path.exists():
                try:
                    self.iconbitmap(str(ico_path))
                    return
                except Exception:
                    pass

        # Fallback to PNG
        png_path = base / "logo.png"
        if png_path.exists():
            try:
                self._tk_icon = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self._tk_icon)
            except Exception:
                pass

    def _load_logo(self) -> Optional[ctk.CTkImage]:
        """Load PNG logo"""
        from pathlib import Path
        logo_path = Path(__file__).parent / "logo.png"

        if not logo_path.exists():
            return None

        # Load PNG
        try:
            from PIL import Image as PILImage
            pil_img = PILImage.open(logo_path)
            return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(40, 40))
        except Exception:
            return None

    @staticmethod
    def _generate_logo_png(path) -> None:
        """Create logo.png with Pillow"""
        from PIL import Image as PILImage, ImageDraw, ImageFont

        size = 400
        img = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        radius = int(size * 0.45)

        # Background circle
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(13, 17, 23, 255),
            outline=(48, 54, 61, 255),
            width=3,
        )

        # Sound wave bars
        bar_w = int(size * 0.04)
        gap = int(size * 0.025)
        heights = [0.22, 0.35, 0.50, 0.65, 0.50, 0.35, 0.22]
        colors = [
            (88, 166, 255), (100, 170, 255), (140, 160, 255),
            (188, 140, 255), (220, 120, 230), (240, 110, 200),
            (247, 120, 186),
        ]
        total_w = len(heights) * bar_w + (len(heights) - 1) * gap
        sx = cx - total_w // 2
        wave_y = cy - int(size * 0.08)

        for i, (h, c) in enumerate(zip(heights, colors)):
            bh = int(size * h)
            x = sx + i * (bar_w + gap)
            yt = wave_y - bh // 2
            draw.rounded_rectangle([x, yt, x + bar_w, yt + bh], radius=bar_w // 2, fill=c)

        # "FreQ" text
        try:
            font = ImageFont.truetype("arialbd.ttf", int(size * 0.20))
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", int(size * 0.20))
            except OSError:
                font = ImageFont.load_default()

        text = "FreQ"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, cy + int(size * 0.20)), text, fill=(230, 237, 243), font=font)

        # Dots
        dot_y = cy + int(size * 0.40)
        dot_r = int(size * 0.012)
        for dx, c in [(-int(size*0.10), (88,166,255)), (0, (188,140,255)), (int(size*0.10), (247,120,186))]:
            draw.ellipse([cx+dx-dot_r, dot_y-dot_r, cx+dx+dot_r, dot_y+dot_r], fill=c)

        img.save(str(path), "PNG")

    @staticmethod
    def _fmt_dur(secs: float) -> str:
        total = int(secs)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ─────────────────────────────────────
    # Progress Timer
    # ─────────────────────────────────────
    def _start_progress_timer(self) -> None:
        """Periodically update progress bar, logs, streaming status, autosave"""
        self._update_progress()
        self._refresh_log()
        self._update_stream_status()
        self._check_autosave()
        self._update_stream_status()
        self._update_anthem_next()
        self._update_mixer()
        self.after(500, self._start_progress_timer)

    def _update_anthem_next(self) -> None:
        """Update the 'Next anthem' label periodically."""
        if not hasattr(self, 'anthem_next_label'):
            return
        try:
            next_time = self.anthem.get_next_play_time()
            if next_time:
                self.anthem_next_label.configure(text=f"Next anthem: {next_time}")
        except Exception:
            pass

    def _update_stream_status(self) -> None:
        """Update streaming status label periodically."""
        if not streamer.is_streaming:
            return
        try:
            st = streamer.get_status()
            uptime = int(st.uptime)
            m, s = divmod(uptime, 60)
            h, m = divmod(m, 60)
            self.stream_info_label.configure(
                text=f"{st.protocol.name} @ {st.server_info} | {h:02d}:{m:02d}:{s:02d}")
        except Exception:
            pass

    def _check_autosave(self) -> None:
        """Auto-save if enabled and interval has elapsed."""
        import time
        if not self._autosave_enabled:
            return
        now = time.time()
        if now - self._autosave_last >= self._autosave_interval:
            self._autosave_last = now
            try:
                self._sync_anthem_times()
                settings = self._collect_settings()
                self.rq.save_to_file(settings=settings)
                if hasattr(self, 'autosave_status_label'):
                    from datetime import datetime
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.autosave_status_label.configure(
                        text=f"Last auto-saved: {ts}",
                        text_color=COLORS["green"])
            except Exception:
                pass

    def _schedule_refresh(self) -> None:
        """Schedule a throttled queue refresh"""
        self.after(100, self._throttled_refresh)

    def _load_waveform(self, filepath: str) -> None:
        """Generate and display waveform for an audio file (background)."""
        def _gen():
            try:
                samples = WaveformWidget.generate_from_file(filepath)
                self.after(0, lambda s=samples: self.waveform.set_waveform(s))
            except Exception:
                pass
        threading.Thread(target=_gen, daemon=True).start()

    def _show_toast(self, text: str, duration: int = 3000) -> None:
        """Show a floating toast notification that auto-hides."""
        try:
            toast = tk.Toplevel(self)
            toast.overrideredirect(True)
            toast.configure(bg="#22c55e")
            lbl = tk.Label(toast, text=text, bg="#22c55e", fg="#000",
                           font=("Segoe UI", 11, "bold"), padx=16, pady=8)
            lbl.pack()
            toast.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() - toast.winfo_width()) // 2
            y = self.winfo_y() + self.winfo_height() - 80
            toast.geometry(f"+{max(0,x)}+{max(0,y)}")
            toast.after(duration, toast.destroy)
        except Exception:
            pass

    def _update_progress(self) -> None:
        """Update progress bar and waveform based on player position"""
        try:
            if not self.audio_player.available:
                return
            # Don't overwrite progress bar during download
            if self._download_mode:
                return
            pos = self.audio_player.get_position()
            dur = self.audio_player.get_duration()
            if dur > 0:
                pct = min(100.0, (pos / dur) * 100)
                self.np_progress.set(pct)
                # Update waveform playhead
                self.waveform.set_position(pos / dur)
            self.np_time_cur.configure(text=self._fmt_dur(pos))
            if dur > 0:
                self.np_time_total.configure(text=self._fmt_dur(dur))
            # Update VU meter
            self.vu_meter.update_levels(self.volume_var.get(), self._playing)
        except Exception:
            pass

    # ─────────────────────────────────────
    # Settings Tab
    # ─────────────────────────────────────
    @staticmethod
    def _settings_sep(parent, padtop=10):
        """Add a subtle accent separator line between settings sections."""
        ctk.CTkFrame(parent, height=1, fg_color=COLORS["border"]).pack(
            fill="x", padx=12, pady=(padtop, 6))

    def _build_settings_tab(self) -> None:
        scroll = self.tab_settings

        # -- Jingle --
        ctk.CTkLabel(scroll, text="\U0001f3b5 Jingle", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(8, 4))
        self.jingle_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Enable Jingle", variable=self.jingle_var,
                       onvalue=True, offvalue=False,
                       command=lambda: setattr(self.jingle, "enabled", self.jingle_var.get())
        ).pack(anchor="w", padx=12)
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row, text="Every N songs:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.jingle_interval_entry = ctk.CTkEntry(row, width=50, fg_color=COLORS["bg"],
                                                    border_color=COLORS["border"])
        self.jingle_interval_entry.insert(0, "5")
        self.jingle_interval_entry.pack(side="left", padx=4)
        self.jingle_interval_entry.bind("<FocusOut>", lambda e: self._on_jingle_interval_change())
        self.jingle_interval_entry.bind("<Return>", lambda e: self._on_jingle_interval_change())

        ctk.CTkButton(scroll, text="\U0001f4c2 Select Jingle File", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._select_jingle).pack(fill="x", padx=8, pady=4)
        self.jingle_label = ctk.CTkLabel(scroll, text="No file selected",
                                          font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self.jingle_label.pack(anchor="w", padx=12)

        jingle_test_row = ctk.CTkFrame(scroll, fg_color="transparent")
        jingle_test_row.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(jingle_test_row, text="\u25b6  Play Now (Manual)", width=140, height=28,
                       fg_color=COLORS["purple"], hover_color="#b484d9",
                       corner_radius=8, font=ctk.CTkFont(size=10, weight="bold"),
                       text_color="#ffffff",
                       command=self._play_jingle_manual).pack(side="left", padx=4)
        ctk.CTkButton(jingle_test_row, text="\u23f9  Stop", width=70, height=28,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       corner_radius=8, font=ctk.CTkFont(size=10),
                       text_color="#ffffff",
                       command=self._jingle_stop).pack(side="left", padx=4)

        self._settings_sep(scroll)
        # -- Mic --
        ctk.CTkLabel(scroll, text="\U0001f3a4 Microphone", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))

        # Mic input device selector
        mic_dev_row = ctk.CTkFrame(scroll, fg_color="transparent")
        mic_dev_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(mic_dev_row, text="Input:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self._mic_input_devices = self.audio_mgr.get_input_devices()
        mic_device_names = ["Default"] + [d.name for d in self._mic_input_devices]
        self.mic_device_var = ctk.StringVar(value="Default")
        self.mic_device_menu = ctk.CTkOptionMenu(
            mic_dev_row, variable=self.mic_device_var,
            values=mic_device_names, width=180, height=26,
            fg_color=COLORS["bg_hover"], button_color=COLORS["border"],
            button_hover_color=COLORS["text3"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=10),
            command=self._on_mic_device_selected,
        )
        self.mic_device_menu.pack(side="left", padx=4)
        ctk.CTkButton(mic_dev_row, text="\U0001f504", width=26, height=26,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       corner_radius=8, font=ctk.CTkFont(size=10),
                       command=self._refresh_mic_devices).pack(side="left", padx=(4, 0))

        # Mic gain slider
        mic_gain_row = ctk.CTkFrame(scroll, fg_color="transparent")
        mic_gain_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(mic_gain_row, text="Gain:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.mic_gain_slider = ctk.CTkSlider(mic_gain_row, from_=0.0, to=3.0, width=140,
            command=lambda v: setattr(self.mic, 'gain', round(v, 2)))
        self.mic_gain_slider.set(1.0)
        self.mic_gain_slider.pack(side="left", padx=8)
        self.mic_gain_label = ctk.CTkLabel(mic_gain_row, text="1.0x", font=ctk.CTkFont(size=10),
                                            width=36, text_color=COLORS["text3"])
        self.mic_gain_label.pack(side="left")
        self.mic_gain_slider.configure(command=self._on_mic_gain_change)

        ctk.CTkButton(scroll, text="\U0001f50a Emergency Mic (Hold)", height=32,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       command=self._emergency_mic_toggle).pack(fill="x", padx=8, pady=2)

        # Duck volume when mic is active
        duck_row = ctk.CTkFrame(scroll, fg_color="transparent")
        duck_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(duck_row, text="Duck music to:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.mic_duck_slider = ctk.CTkSlider(duck_row, from_=0.0, to=1.0, width=120,
            command=self._on_mic_duck_change)
        self.mic_duck_slider.set(0.3)
        self.mic_duck_slider.pack(side="left", padx=8)
        self.mic_duck_label = ctk.CTkLabel(duck_row, text="30%", font=ctk.CTkFont(size=10),
                                            width=36, text_color=COLORS["text3"])
        self.mic_duck_label.pack(side="left")
        ctk.CTkLabel(duck_row, text="when mic active", font=ctk.CTkFont(size=9),
                      text_color=COLORS["text3"]).pack(side="left", padx=(4, 0))

        mic_row = ctk.CTkFrame(scroll, fg_color="transparent")
        mic_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(mic_row, text="Timer (sec):", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.mic_timer_entry = ctk.CTkEntry(mic_row, width=60, fg_color=COLORS["bg"],
                                              border_color=COLORS["border"])
        self.mic_timer_entry.insert(0, "30")
        self.mic_timer_entry.pack(side="left", padx=4)
        ctk.CTkButton(mic_row, text="\U0001f3a4 Timer Mic", width=100, height=28,
                       fg_color=COLORS["orange"], text_color="#000",
                       command=self._timer_mic_toggle).pack(side="left", padx=4)

        self._settings_sep(scroll)
        # -- National Anthem (เพลงชาติ) --
        ctk.CTkLabel(scroll, text="🇹🇭 National Anthem", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))

        self.anthem_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(scroll, text="Enable National Anthem", variable=self.anthem_var,
                       onvalue=True, offvalue=False,
                       command=lambda: setattr(self.anthem.config, "enabled", self.anthem_var.get())
        ).pack(anchor="w", padx=12)

        # Morning time
        morning_row = ctk.CTkFrame(scroll, fg_color="transparent")
        morning_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(morning_row, text="Morning:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.anthem_morning = ctk.CTkEntry(morning_row, width=60, fg_color=COLORS["bg"],
                                            border_color=COLORS["border"])
        self.anthem_morning.insert(0, "08:00")
        self.anthem_morning.pack(side="left", padx=4)
        self.anthem_morning.bind("<FocusOut>", lambda e: self._sync_anthem_times())
        self.anthem_morning.bind("<Return>", lambda e: self._sync_anthem_times())
        ctk.CTkButton(morning_row, text="🕐", width=28, height=24,
                       fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
                       font=ctk.CTkFont(size=13),
                       command=lambda: self._open_time_picker(self.anthem_morning)).pack(side="left", padx=2)
        ctk.CTkLabel(morning_row, text="(Thailand standard)", font=ctk.CTkFont(size=9),
                      text_color=COLORS["text3"]).pack(side="left")

        # Evening time
        evening_row = ctk.CTkFrame(scroll, fg_color="transparent")
        evening_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(evening_row, text="Evening:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.anthem_evening = ctk.CTkEntry(evening_row, width=60, fg_color=COLORS["bg"],
                                            border_color=COLORS["border"])
        self.anthem_evening.insert(0, "18:00")
        self.anthem_evening.pack(side="left", padx=4)
        self.anthem_evening.bind("<FocusOut>", lambda e: self._sync_anthem_times())
        self.anthem_evening.bind("<Return>", lambda e: self._sync_anthem_times())
        ctk.CTkButton(evening_row, text="🕐", width=28, height=24,
                       fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
                       font=ctk.CTkFont(size=13),
                       command=lambda: self._open_time_picker(self.anthem_evening)).pack(side="left", padx=2)
        ctk.CTkLabel(evening_row, text="(Thailand standard)", font=ctk.CTkFont(size=9),
                      text_color=COLORS["text3"]).pack(side="left")

        # Anthem file
        ctk.CTkButton(scroll, text="🎵 Select Anthem File", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._select_anthem).pack(fill="x", padx=8, pady=4)
        self.anthem_label = ctk.CTkLabel(scroll, text="No file selected",
                                          font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self.anthem_label.pack(anchor="w", padx=12)

        # Resume after anthem
        self.anthem_resume_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(scroll, text="Resume playback after anthem", variable=self.anthem_resume_var,
                       onvalue=True, offvalue=False,
                       command=lambda: setattr(self.anthem.config, "resume_after", self.anthem_resume_var.get())
        ).pack(anchor="w", padx=12, pady=(4, 0))

        # Next anthem time
        self.anthem_next_label = ctk.CTkLabel(scroll, text="Next anthem: Checking...",
                                              font=ctk.CTkFont(size=10), text_color=COLORS["text2"])
        self.anthem_next_label.pack(anchor="w", padx=12, pady=(4, 0))

        # Test anthem button
        anthem_test_row = ctk.CTkFrame(scroll, fg_color="transparent")
        anthem_test_row.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(anthem_test_row, text="\u25b6  Play Now (Manual)", width=140, height=28,
                       fg_color=COLORS["green"], hover_color="#2ea043",
                       corner_radius=8, font=ctk.CTkFont(size=10, weight="bold"),
                       text_color="#ffffff",
                       command=self._test_anthem_play).pack(side="left", padx=4)
        ctk.CTkButton(anthem_test_row, text="\u23f9  Stop", width=70, height=28,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       corner_radius=8, font=ctk.CTkFont(size=10),
                       text_color="#ffffff",
                       command=self._test_anthem_stop).pack(side="left", padx=4)

        self._settings_sep(scroll)
        # -- Commercial --
        ctk.CTkLabel(scroll, text="\U0001f4e2 Commercial Break", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        self.commercial_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Enable Ads", variable=self.commercial_var,
                       onvalue=True, offvalue=False,
                       command=lambda: setattr(self.commercial, "enabled", self.commercial_var.get())
        ).pack(anchor="w", padx=12)
        ad_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ad_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(ad_row, text="Every N songs:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.ad_interval_entry = ctk.CTkEntry(ad_row, width=50, fg_color=COLORS["bg"],
                                                border_color=COLORS["border"])
        self.ad_interval_entry.insert(0, "10")
        self.ad_interval_entry.pack(side="left", padx=4)
        self.ad_interval_entry.bind("<FocusOut>", lambda e: self._on_ad_interval_change())
        self.ad_interval_entry.bind("<Return>", lambda e: self._on_ad_interval_change())

        ctk.CTkButton(scroll, text="\U0001f4c2 Add Ad Files", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._add_commercial_files).pack(fill="x", padx=8, pady=4)
        self.commercial_count_label = ctk.CTkLabel(scroll, text="0 ad files",
                                                    font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self.commercial_count_label.pack(anchor="w", padx=12)

        self._settings_sep(scroll)
        # -- Normalization --
        ctk.CTkLabel(scroll, text="\U0001f50a Volume Normalization", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        self.norm_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Enable Normalization", variable=self.norm_var,
                       onvalue=True, offvalue=False,
                       command=lambda: setattr(self.normalizer, "enabled", self.norm_var.get())
        ).pack(anchor="w", padx=12)
        norm_row = ctk.CTkFrame(scroll, fg_color="transparent")
        norm_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(norm_row, text="Target LUFS:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.norm_slider = ctk.CTkSlider(norm_row, from_=-24, to=-6, width=120,
            command=lambda v: setattr(self.normalizer, "target_lufs", v))
        self.norm_slider.set(-14)
        self.norm_slider.pack(side="left", padx=8)
        self.norm_label = ctk.CTkLabel(norm_row, text="-14", font=ctk.CTkFont(size=10), width=32,
                                        text_color=COLORS["text3"])
        self.norm_label.pack(side="left")

        self._settings_sep(scroll)
        # -- EQ --
        ctk.CTkLabel(scroll, text="\U0001f3b6 EQ / Audio Effects", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        eq_presets = ["flat", "bass_boost", "treble_boost", "vocal", "rock", "pop", "jazz", "electronic"]
        self.eq_preset_var = ctk.StringVar(value="flat")
        ctk.CTkOptionMenu(scroll, variable=self.eq_preset_var, values=eq_presets, width=160, height=28,
            fg_color=COLORS["bg_hover"], button_color=COLORS["border"],
            command=lambda v: self._apply_eq_preset(v)).pack(anchor="w", padx=8, pady=2)

        self.eq_sliders = {}
        for label, attr, mn, mx in [("Bass", "bass", -12, 12), ("Mid", "mid", -12, 12), ("Treble", "treble", -12, 12)]:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=1)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=10), width=50,
                          text_color=COLORS["text2"]).pack(side="left")
            sl = ctk.CTkSlider(row, from_=mn, to=mx, width=200,
                command=lambda v, a=attr: setattr(self.eq, a, v))
            sl.set(0)
            sl.pack(side="left", padx=4)
            self.eq_sliders[attr] = sl

        self._settings_sep(scroll)
        # -- Audio Edit --
        ctk.CTkLabel(scroll, text="\u2702\ufe0f Audio Edit", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkButton(scroll, text="\U0001f4c2 Select Audio to Edit", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._select_edit_file).pack(fill="x", padx=8, pady=2)
        self.edit_file_label = ctk.CTkLabel(scroll, text="No file selected",
                                              font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self.edit_file_label.pack(anchor="w", padx=12)
        self._edit_file = ""

        edit_row = ctk.CTkFrame(scroll, fg_color="transparent")
        edit_row.pack(fill="x", padx=8, pady=4)
        for text, cmd in [("Trim", self._edit_trim), ("Fade In", self._edit_fadein),
                          ("Fade Out", self._edit_fadeout), ("Normalize", self._edit_normalize)]:
            ctk.CTkButton(edit_row, text=text, width=80, height=26,
                           fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                           font=ctk.CTkFont(size=9), command=cmd).pack(side="left", padx=2)

        self._settings_sep(scroll)
        # -- Cache --
        ctk.CTkLabel(scroll, text="\U0001f4e6 Cache", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkButton(scroll, text="\U0001f5d1 Clear Cache", height=28,
                       fg_color=COLORS["red"], hover_color="#da3633",
                       command=self._show_cache_menu).pack(fill="x", padx=8, pady=2)

        self._settings_sep(scroll)
        # -- Autosave --
        ctk.CTkLabel(scroll, text="\U0001f4be Autosave", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))

        self.autosave_var = ctk.BooleanVar(value=self._autosave_enabled)
        ctk.CTkSwitch(scroll, text="Enable Autosave", variable=self.autosave_var,
                       onvalue=True, offvalue=False,
                       command=self._toggle_autosave).pack(anchor="w", padx=12)

        as_row = ctk.CTkFrame(scroll, fg_color="transparent")
        as_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(as_row, text="Save every:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.autosave_interval_entry = ctk.CTkEntry(as_row, width=50, fg_color=COLORS["bg"],
                                                      border_color=COLORS["border"])
        self.autosave_interval_entry.insert(0, str(self._autosave_interval))
        self.autosave_interval_entry.pack(side="left", padx=4)
        ctk.CTkLabel(as_row, text="seconds", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.autosave_interval_entry.bind("<FocusOut>", lambda e: self._on_autosave_interval_change())
        self.autosave_interval_entry.bind("<Return>", lambda e: self._on_autosave_interval_change())

        self.autosave_status_label = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=9),
                                                    text_color=COLORS["text3"])
        self.autosave_status_label.pack(anchor="w", padx=12)

        self._settings_sep(scroll)
        # -- Theme --
        ctk.CTkLabel(scroll, text="\U0001f3a8 Theme", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        theme_row = ctk.CTkFrame(scroll, fg_color="transparent")
        theme_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkButton(theme_row, text="\u2600\ufe0f Light", width=80, height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._set_light_theme).pack(side="left", padx=4)
        ctk.CTkButton(theme_row, text="\U0001f319 Dark", width=80, height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._set_dark_theme).pack(side="left", padx=4)

        self._settings_sep(scroll)
        # -- Save / Load Settings --
        ctk.CTkLabel(scroll, text="\U0001f4be Settings", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(16, 4))
        ctk.CTkButton(scroll, text="\U0001f4be  Save All Settings", height=36,
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                       corner_radius=8, font=ctk.CTkFont(size=12, weight="bold"),
                       text_color="#ffffff",
                       command=self._save_settings_to_freq).pack(fill="x", padx=8, pady=4)
        self._save_settings_status = ctk.CTkLabel(scroll, text="",
                                                    font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self._save_settings_status.pack(anchor="w", padx=12)
        ctk.CTkButton(scroll, text="\U0001f4c2  Load Settings from File", height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       corner_radius=8, font=ctk.CTkFont(size=11),
                       command=self._load_settings_from_freq).pack(fill="x", padx=8, pady=(2, 8))

    # ── Streaming Tab ──
    def _build_streaming_tab(self) -> None:
        scroll = self.tab_stream

        # ── Connection Name ──
        ctk.CTkLabel(scroll, text="Name", font=ctk.CTkFont(size=11, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 2))
        self.stream_name = ctk.CTkEntry(scroll, placeholder_text="My Stream", width=280,
                                         fg_color=COLORS["bg_input"], border_color=COLORS["border"],
                                         corner_radius=6, height=32)
        self.stream_name.insert(0, "FreQ Radio")
        self.stream_name.pack(fill="x", padx=8, pady=(0, 4))

        # ── Protocol Selection ──
        ctk.CTkLabel(scroll, text="Type", font=ctk.CTkFont(size=11, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(4, 2))

        self.stream_protocol_var = ctk.StringVar(value="Icecast")
        self.stream_protocol_var.trace_add("write", lambda *_: self._on_protocol_change())

        proto_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        proto_frame.pack(fill="x", padx=8, pady=(0, 4))

        for proto in ["Shoutcast", "Icecast", "WebRTC"]:
            ctk.CTkRadioButton(proto_frame, text=proto, variable=self.stream_protocol_var,
                               value=proto, font=ctk.CTkFont(size=11),
                               text_color=COLORS["text2"],
                               fg_color=COLORS["accent"],
                               hover_color=COLORS["accent_hover"]).pack(side="left", padx=8)

        # ── SSL/TLS ──
        ssl_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        ssl_frame.pack(fill="x", padx=8, pady=(0, 4))
        self.stream_ssl_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(ssl_frame, text="Use SSL/TLS", variable=self.stream_ssl_var,
                        font=ctk.CTkFont(size=11), text_color=COLORS["text2"],
                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"]).pack(side="left")

        # ── Dynamic Fields Container ──
        self._stream_fields_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._stream_fields_frame.pack(fill="x", padx=0, pady=0)

        # Create all field frames (show/hide based on protocol)
        self._shoutcast_fields = self._build_shoutcast_fields()
        self._icecast_fields = self._build_icecast_fields()
        self._webrtc_fields = self._build_webrtc_fields()

        # ── Audio Settings ──
        ctk.CTkLabel(scroll, text="Audio", font=ctk.CTkFont(size=11, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 4))

        audio_row = ctk.CTkFrame(scroll, fg_color="transparent")
        audio_row.pack(fill="x", padx=8, pady=2)

        ctk.CTkLabel(audio_row, text="Codec:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"]).pack(side="left")
        self.stream_codec_var = ctk.StringVar(value="mp3")
        ctk.CTkOptionMenu(audio_row, variable=self.stream_codec_var,
                           values=["mp3", "aac", "ogg", "opus"], width=80, height=28,
                           fg_color=COLORS["bg_hover"], button_color=COLORS["border"]).pack(side="left", padx=4)

        ctk.CTkLabel(audio_row, text="Bitrate:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"]).pack(side="left")
        self.stream_bitrate_var = ctk.StringVar(value="128")
        ctk.CTkOptionMenu(audio_row, variable=self.stream_bitrate_var,
                           values=["64", "96", "128", "192", "256", "320"], width=60, height=28,
                           fg_color=COLORS["bg_hover"], button_color=COLORS["border"]).pack(side="left", padx=4)
        ctk.CTkLabel(audio_row, text="kbps", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"]).pack(side="left")

        # ── Status ──
        self.stream_status_label = ctk.CTkLabel(scroll, text="● Offline",
                                                  font=ctk.CTkFont(size=11, weight="bold"),
                                                  text_color=COLORS["text3"])
        self.stream_status_label.pack(anchor="w", padx=8, pady=(12, 4))

        self.stream_info_label = ctk.CTkLabel(scroll, text="",
                                                font=ctk.CTkFont(size=9),
                                                text_color=COLORS["text3"])
        self.stream_info_label.pack(anchor="w", padx=8)

        # ── Control Buttons ──
        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(fill="x", padx=8, pady=8)

        self.btn_stream_start = ctk.CTkButton(btn_frame, text="Start Stream", height=36,
                                                fg_color=COLORS["green"], hover_color="#2ea043",
                                                command=self._start_stream)
        self.btn_stream_start.pack(side="left", padx=4)
        ToolTip(self.btn_stream_start, text="Start Streaming", description="Begin broadcasting to server")

        self.btn_stream_stop = ctk.CTkButton(btn_frame, text="Stop", height=36,
                                               fg_color=COLORS["red"], hover_color="#da3633",
                                               command=self._stop_stream, state="disabled")
        self.btn_stream_stop.pack(side="left", padx=4)
        ToolTip(self.btn_stream_stop, text="Stop Streaming", description="Stop broadcasting to server")

        # ── Requirements ──
        ctk.CTkLabel(scroll, text="Requirements:", font=ctk.CTkFont(size=10, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 2))

        reqs = []
        if not FFMPEG_AVAILABLE:
            reqs.append("❌ FFmpeg not found - install from ffmpeg.org")
        else:
            reqs.append("✅ FFmpeg")
        if not WEBRTC_AVAILABLE:
            reqs.append("❌ aiortc not found - pip install aiortc (WebRTC only)")
        else:
            reqs.append("✅ aiortc")
        for req in reqs:
            ctk.CTkLabel(scroll, text=req, font=ctk.CTkFont(size=9),
                          text_color=COLORS["text3"]).pack(anchor="w", padx=12)

        # Show default (Icecast)
        self._on_protocol_change()

        # ═══ Stream Relay (Pull from URL) ═══
        ctk.CTkFrame(scroll, height=1, fg_color=COLORS["border"]).pack(
            fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(scroll, text="🔗 Stream Relay",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(4, 4))
        ctk.CTkLabel(scroll, text="Pull audio from an internet stream URL",
                      font=ctk.CTkFont(size=10), text_color=COLORS["text3"]).pack(anchor="w", padx=12)

        relay_url_row = ctk.CTkFrame(scroll, fg_color="transparent")
        relay_url_row.pack(fill="x", padx=8, pady=(6, 2))
        self.relay_url_entry = ctk.CTkEntry(
            relay_url_row, placeholder_text="http://stream.example.com:8000/radio.mp3",
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            corner_radius=6, height=32)
        self.relay_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        relay_btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        relay_btn_frame.pack(fill="x", padx=8, pady=4)
        self.btn_relay_start = ctk.CTkButton(
            relay_btn_frame, text="▶ Pull & Play", height=32,
            fg_color=COLORS["green"], hover_color="#2ea043",
            corner_radius=8, font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ffffff",
            command=self._start_relay)
        self.btn_relay_start.pack(side="left", padx=4)
        self.btn_relay_stop = ctk.CTkButton(
            relay_btn_frame, text="⏹ Stop", height=32,
            fg_color=COLORS["red"], hover_color="#da3633",
            corner_radius=8, font=ctk.CTkFont(size=11),
            text_color="#ffffff", state="disabled",
            command=self._stop_relay)
        self.btn_relay_stop.pack(side="left", padx=4)

        self.relay_status_label = ctk.CTkLabel(scroll, text="",
                                                font=ctk.CTkFont(size=10),
                                                text_color=COLORS["text3"])
        self.relay_status_label.pack(anchor="w", padx=12, pady=(2, 0))
        self._relay_process: Optional[subprocess.Popen] = None

    def _start_relay(self) -> None:
        """Pull audio from a stream URL using ffmpeg."""
        url = self.relay_url_entry.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Enter a stream URL first.")
            return
        if not FFMPEG_AVAILABLE:
            messagebox.showerror("FFmpeg required", "FFmpeg is needed for stream relay.")
            return
        self._stop_relay()
        self.relay_status_label.configure(text="🔗 Connecting...", text_color=COLORS["orange"])
        self.btn_relay_start.configure(state="disabled")
        self.btn_relay_stop.configure(state="normal")

        def _run():
            import tempfile, time as _t
            tmp_path = str(Path(tempfile.gettempdir()) / "freq_relay_stream.mp3")
            try:
                cmd = [
                    "ffmpeg", "-y",
                    "-reconnect", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                    "-i", url,
                    "-f", "mp3",
                    "-acodec", "libmp3lame",
                    "-ab", "192k",
                    "-ar", "44100",
                    "-ac", "2",
                    tmp_path
                ]
                self._relay_process = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.after(0, lambda: self.relay_status_label.configure(
                    text="🔗 Buffering...", text_color=COLORS["orange"]))
                # Wait for enough data to accumulate
                waited = 0
                while waited < 15:
                    _t.sleep(1)
                    waited += 1
                    if os.path.isfile(tmp_path) and os.path.getsize(tmp_path) > 65536:
                        break
                    if self._relay_process.poll() is not None:
                        raise RuntimeError("FFmpeg exited — check URL")
                if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) < 65536:
                    raise RuntimeError("Not enough data received")
                # Play via pygame
                self.after(0, lambda: self._play_relay_file(tmp_path))
                # Wait for ffmpeg to finish (stream ended)
                self._relay_process.wait()
            except Exception as e:
                self.after(0, lambda: self.relay_status_label.configure(
                    text=f"❌ {e}", text_color=COLORS["red"]))
                self.after(0, lambda: self.btn_relay_start.configure(state="normal"))
                self.after(0, lambda: self.btn_relay_stop.configure(state="disabled"))

        self._relay_playing = False
        threading.Thread(target=_run, daemon=True).start()

    def _play_relay_file(self, filepath: str) -> None:
        """Play the relay audio file."""
        if self.audio_player.available and os.path.isfile(filepath):
            self.audio_player.play_file(filepath, duration=0)
            self._relay_playing = True
            self.relay_status_label.configure(
                text="🔗 Playing relay stream", text_color=COLORS["green"])

    def _stop_relay(self) -> None:
        """Stop the relay stream."""
        if self._relay_process:
            try:
                self._relay_process.kill()
            except Exception:
                pass
            self._relay_process = None
        self._relay_playing = False
        self.relay_status_label.configure(text="Stopped", text_color=COLORS["text3"])
        self.btn_relay_start.configure(state="normal")
        self.btn_relay_stop.configure(state="disabled")

    def _build_shoutcast_fields(self) -> dict:
        """Build Shoutcast-specific fields."""
        frame = ctk.CTkFrame(self._stream_fields_frame, fg_color="transparent")
        fields = {}

        # Address + Port
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row, text="Address:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        fields["host"] = ctk.CTkEntry(row, placeholder_text="localhost", width=140,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["host"].insert(0, "localhost")
        fields["host"].pack(side="left", padx=4)

        ctk.CTkLabel(row, text="Port:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left", padx=(8,0))
        fields["port"] = ctk.CTkEntry(row, placeholder_text="8000", width=60,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["port"].insert(0, "8000")
        fields["port"].pack(side="left", padx=4)

        # Password
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row2, text="Password:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        fields["password"] = ctk.CTkEntry(row2, show="*", width=140,
                                            fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["password"].insert(0, "hackme")
        fields["password"].pack(side="left", padx=4)

        fields["frame"] = frame
        fields["widgets"] = [frame]
        return fields

    def _build_icecast_fields(self) -> dict:
        """Build Icecast-specific fields."""
        frame = ctk.CTkFrame(self._stream_fields_frame, fg_color="transparent")
        fields = {}

        # Address + Port
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row, text="Address:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        fields["host"] = ctk.CTkEntry(row, placeholder_text="localhost", width=140,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["host"].insert(0, "localhost")
        fields["host"].pack(side="left", padx=4)

        ctk.CTkLabel(row, text="Port:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left", padx=(8,0))
        fields["port"] = ctk.CTkEntry(row, placeholder_text="8000", width=60,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["port"].insert(0, "8000")
        fields["port"].pack(side="left", padx=4)

        # Password
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row2, text="Password:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        fields["password"] = ctk.CTkEntry(row2, show="*", width=140,
                                            fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["password"].insert(0, "hackme")
        fields["password"].pack(side="left", padx=4)

        # Mount + User
        row3 = ctk.CTkFrame(frame, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row3, text="Mountpoint:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        fields["mount"] = ctk.CTkEntry(row3, placeholder_text="/stream", width=100,
                                         fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["mount"].insert(0, "/stream")
        fields["mount"].pack(side="left", padx=4)

        ctk.CTkLabel(row3, text="User:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left", padx=(8,0))
        fields["user"] = ctk.CTkEntry(row3, placeholder_text="source", width=80,
                                        fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["user"].insert(0, "source")
        fields["user"].pack(side="left", padx=4)

        # Legacy checkbox
        row4 = ctk.CTkFrame(frame, fg_color="transparent")
        row4.pack(fill="x", padx=8, pady=2)
        fields["legacy_var"] = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(row4, text="Use legacy Icecast protocol", variable=fields["legacy_var"],
                        font=ctk.CTkFont(size=10), text_color=COLORS["text3"],
                        fg_color=COLORS["accent"]).pack(side="left")

        fields["frame"] = frame
        fields["widgets"] = [frame]
        return fields

    def _build_webrtc_fields(self) -> dict:
        """Build WebRTC-specific fields."""
        frame = ctk.CTkFrame(self._stream_fields_frame, fg_color="transparent")
        fields = {}

        # ICE Server
        ctk.CTkLabel(frame, text="ICE server (optional):", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(4, 2))
        fields["ice_server"] = ctk.CTkEntry(frame, placeholder_text="stun:stun.l.google.com:19302", width=280,
                                              fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["ice_server"].pack(fill="x", padx=8, pady=(0, 4))

        # WHIP URL
        ctk.CTkLabel(frame, text="WebRTC (WHIP) URL:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(0, 2))
        fields["whip_url"] = ctk.CTkEntry(frame, placeholder_text="https://example.com/whip", width=280,
                                            fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["whip_url"].pack(fill="x", padx=8, pady=(0, 4))

        # Bearer Token
        ctk.CTkLabel(frame, text="Bearer token (optional):", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(0, 2))
        fields["bearer"] = ctk.CTkEntry(frame, show="*", placeholder_text="", width=280,
                                          fg_color=COLORS["bg_input"], border_color=COLORS["border"], corner_radius=6, height=30)
        fields["bearer"].pack(fill="x", padx=8, pady=(0, 4))

        fields["frame"] = frame
        fields["widgets"] = [frame]
        return fields

    def _on_protocol_change(self) -> None:
        """Show/hide fields based on selected protocol."""
        proto = self.stream_protocol_var.get()

        # Hide all
        for w in self._shoutcast_fields["widgets"]:
            w.pack_forget()
        for w in self._icecast_fields["widgets"]:
            w.pack_forget()
        for w in self._webrtc_fields["widgets"]:
            w.pack_forget()

        # Show selected
        if proto == "Shoutcast":
            self._shoutcast_fields["frame"].pack(fill="x", padx=0, pady=0)
        elif proto == "Icecast":
            self._icecast_fields["frame"].pack(fill="x", padx=0, pady=0)
        elif proto == "WebRTC":
            self._webrtc_fields["frame"].pack(fill="x", padx=0, pady=0)

    def _start_stream(self) -> None:
        """Start streaming with current settings."""
        proto_name = self.stream_protocol_var.get()
        proto_map = {"Shoutcast": StreamProtocol.SHOUTCAST,
                      "Icecast": StreamProtocol.ICECAST,
                      "WebRTC": StreamProtocol.WEBRTC}
        proto = proto_map.get(proto_name, StreamProtocol.ICECAST)

        # Get fields from active protocol
        try:
            if proto_name == "Shoutcast":
                fields = self._shoutcast_fields
                host = fields["host"].get() or "localhost"
                port = int(fields["port"].get() or "8000")
                username = "source"
                password = fields["password"].get() or "hackme"
                mount = "/"
            elif proto_name == "Icecast":
                fields = self._icecast_fields
                host = fields["host"].get() or "localhost"
                port = int(fields["port"].get() or "8000")
                username = fields["user"].get() or "source"
                password = fields["password"].get() or "hackme"
                mount = fields["mount"].get() or "/stream"
            elif proto_name == "WebRTC":
                fields = self._webrtc_fields
                host = "webrtc"
                port = 0
                username = "source"
                password = fields["bearer"].get() or ""
                mount = fields["whip_url"].get() or ""
            else:
                return
        except (ValueError, KeyError) as e:
            messagebox.showerror("Invalid input", f"Check your settings: {e}")
            return

        streamer.configure(
            protocol=proto,
            host=host,
            port=port,
            username=username,
            password=password,
            mount=mount,
            stream_name=self.stream_name.get() or "FreQ Radio",
            codec=self.stream_codec_var.get(),
            bitrate=int(self.stream_bitrate_var.get()),
        )

        if streamer.start():
            self.stream_status_label.configure(text="🔴 Streaming", text_color=COLORS["red"])
            self.stream_info_label.configure(text=f"{proto_name} @ {host}:{port}")
            self.btn_stream_start.configure(state="disabled")
            self.btn_stream_stop.configure(state="normal")
            self._stream_current_if_playing()
        else:
            err = "FFmpeg not found" if not FFMPEG_AVAILABLE else "Connection failed — check server"
            if proto == StreamProtocol.WEBRTC and not WEBRTC_AVAILABLE:
                err = "aiortc not installed — pip install aiortc"
            self.stream_status_label.configure(text=f"❌ {err}", text_color=COLORS["red"])

    def _stream_current_if_playing(self) -> None:
        """Stream the currently playing song if one exists."""
        if not streamer.is_streaming:
            return
        idx = self.rq.current_index
        if idx < 0 or idx >= len(self.rq.queue):
            return
        song = self.rq.queue[idx]
        filepath = ""
        if song.file_path and os.path.isfile(song.file_path):
            filepath = song.file_path
        elif self.audio_player._yt_ready_file and os.path.isfile(self.audio_player._yt_ready_file):
            filepath = self.audio_player._yt_ready_file
        if filepath:
            streamer.on_track_change(song.title, song.artist, filepath)

    def _update_stream_status(self) -> None:
        """Periodically update stream status label."""
        try:
            if streamer.is_streaming:
                s = streamer.get_status()
                uptime = int(s.uptime)
                m, sec = divmod(uptime, 60)
                h, m = divmod(m, 60)
                time_str = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
                info = f"{time_str} | Connected to {s.server_info}"
                if s.listeners > 0:
                    info += f" | {s.listeners} listeners"
                self.stream_status_label.configure(text="🔴 Streaming", text_color=COLORS["red"])
                self.stream_info_label.configure(text=info)
            elif streamer.state == StreamState.RECONNECTING:
                self.stream_status_label.configure(text="⚠ Reconnecting", text_color=COLORS["orange"])
                self.stream_info_label.configure(text="Icecast connection lost; retrying...")
            elif streamer.state == StreamState.ERROR:
                self.stream_status_label.configure(text="❌ Stream error", text_color=COLORS["red"])
                self.stream_info_label.configure(text="Connection failed; check Icecast settings and logs")
            elif hasattr(self, '_relay_playing') and self._relay_playing:
                self.stream_status_label.configure(text="🔗 Relay active", text_color=COLORS["green"])
        except Exception:
            pass

    def _stop_stream(self) -> None:
        """Stop streaming."""
        streamer.stop()
        self.stream_status_label.configure(text="\u25cf Offline", text_color=COLORS["text3"])
        self.stream_info_label.configure(text="")
        self.btn_stream_start.configure(state="normal")
        self.btn_stream_stop.configure(state="disabled")

    # ═══════════════════════════════════════
    # Mixer Tab (OBS-style)
    # ═══════════════════════════════════════
    def _build_mixer_tab(self) -> None:
        """Build the OBS-style audio mixer panel."""
        tab = self.tab_mixer

        # Header (compact)
        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(hdr, text="🎚️  Audio Mixer",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text"]).pack(side="left")
        ctk.CTkFrame(tab, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=8, pady=(0, 4))

        # Channel strips — compact horizontal rows
        self.mixer_channels = {}
        channel_defs = [
            ("music",    "Music",    COLORS["accent"]),
            ("jingle",   "Jingle",   "#a855f7"),
            ("ads",      "Ads",      "#f97316"),
            ("anthem",   "Anthem",   "#ef4444"),
            ("mic",      "Mic",      "#22c55e"),
        ]
        for cid, label, color in channel_defs:
            ch = MixerChannelWidget(tab, label=label, color=color, channel_id=cid,
                                       on_solo=self._on_solo_change)
            ch.pack(fill="x", padx=6, pady=2)
            self.mixer_channels[cid] = ch

        # Master fader placeholder — built after now_playing creates volume_var
        self.mixer_fader_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.mixer_fader_frame.pack(fill="x", padx=12, pady=(4, 8))

    def _build_mixer_master_fader(self) -> None:
        """Build the master fader after volume_var exists."""
        fader_frame = self.mixer_fader_frame
        ctk.CTkLabel(fader_frame, text="Master Volume:",
                      font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"]).pack(side="left")
        self.mixer_master_slider = ctk.CTkSlider(
            fader_frame, from_=0, to=100,
            variable=self.volume_var,
            width=200, height=14,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            command=self._on_mixer_master_change,
        )
        self.mixer_master_slider.pack(side="left", padx=(8, 4))
        self.mixer_master_label = ctk.CTkLabel(fader_frame, text="80%",
                                                font=ctk.CTkFont(size=10),
                                                text_color=COLORS["text3"], width=36)
        self.mixer_master_label.pack(side="left")

    def _on_solo_change(self, channel_id: str, solo: bool) -> None:
        """Handle solo toggle — mute all other channels when any solo is active."""
        any_solo = any(ch.is_solo for ch in self.mixer_channels.values())
        for cid, ch in self.mixer_channels.items():
            if any_solo:
                # When any channel is soloed, unmuted non-solo channels become muted
                if ch.is_solo:
                    ch.mute_btn.configure(fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])
                else:
                    ch.mute_btn.configure(fg_color="#5a2020", text_color="#ff6666")
            else:
                # No solo active — restore normal mute state
                if ch.is_muted:
                    ch.mute_btn.configure(fg_color=COLORS["red"], text_color="#fff")
                else:
                    ch.mute_btn.configure(fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])

    def _on_mixer_master_change(self, val: float) -> None:
        """Handle master fader change in mixer."""
        vol = float(val)
        if hasattr(self, 'volume_label'):
            self.volume_label.configure(text=f"{int(vol)}%")
        if hasattr(self, 'mixer_master_label'):
            self.mixer_master_label.configure(text=f"{int(vol)}%")
        self.volume_var.set(vol)
        if self.audio_player.available:
            self.audio_player.volume = vol / 100.0
        self._mark_settings_dirty()

    # ─────────────────────────────────────────────────
    # Keys Tab — Keyboard Shortcuts
    # ─────────────────────────────────────────────────

    def _build_keys_tab(self) -> None:
        """Build the keyboard shortcuts binding tab."""
        self._key_rows = build_keys_tab(self.tab_keys, on_change=self._on_key_change)
        # Load saved bindings (defaults if none saved)
        self._keybindings = dict(_DEFAULT_KEYS)  # {id: (label, key, icon)}
        self._apply_keybindings()

    def _on_key_change(self, action_id: str, new_key: str) -> None:
        """Called when user rebinds a key."""
        info = self._keybindings.get(action_id)
        if info:
            self._keybindings[action_id] = (info[0], new_key, info[2])
        self._apply_keybindings()
        self._mark_settings_dirty()

    def _apply_keybindings(self) -> None:
        """Unbind old keys and bind new ones."""
        # Remove old bindings (except space which has special handling)
        for action_id, (_, key, _) in self._keybindings.items():
            if key == "Space":
                continue
            try:
                self.unbind(f"<{key}>")
            except Exception:
                pass
            # For Ctrl+Key we need different format
            if "+" in key:
                parts = key.split("+")
                mod = parts[0].lower()
                k = parts[1]
                binding = f"<{mod}-{k.lower()}>"
                try:
                    self.unbind(binding)
                except Exception:
                    pass

        # Map action_id to handler method
        handler_map = {
            "play_pause":  lambda e: self._toggle_play(),
            "next_track":  lambda e: self._next_track(),
            "prev_track":  lambda e: self._prev_track(),
            "seek_fwd":    lambda e: self._seek_forward(),
            "seek_bwd":    lambda e: self._seek_backward(),
            "vol_up":      lambda e: self._volume_up(),
            "vol_down":    lambda e: self._volume_down(),
            "mute":        lambda e: self._toggle_mute(),
            "save":        lambda e: self._save(),
            "open_file":   lambda e: self._open_file(),
            "paste":       self._on_paste,
        }

        # Bind new keys
        for action_id, (_, key, _) in self._keybindings.items():
            handler = handler_map.get(action_id)
            if not handler:
                continue
            if key == "Space":
                # Space uses special handler (skip if typing in entry)
                self.bind("<space>", self._on_global_space)
                continue
            if "+" in key:
                parts = key.split("+")
                mod = parts[0].lower()
                k = parts[1]
                binding = f"<{mod}-{k.lower()}>"
            else:
                binding = f"<{key.lower()}>"
            try:
                self.bind(binding, handler)
            except Exception:
                pass

    def _collect_keybindings(self) -> dict:
        """Collect current keybindings for saving."""
        result = {}
        for row in self._key_rows:
            result[row.action_id] = row.get_key()
        return result

    def _restore_keybindings(self, data: dict) -> None:
        """Restore keybindings from saved data."""
        if not data:
            return
        for row in self._key_rows:
            key = data.get(row.action_id)
            if key:
                row.key_var.set(key)
                info = self._keybindings.get(row.action_id)
                if info:
                    self._keybindings[row.action_id] = (info[0], key, info[2])
        self._apply_keybindings()

    def _update_mixer(self) -> None:
        """Update mixer channel meters with current audio levels."""
        import random as _rand
        try:
            vol = self.volume_var.get()
            playing = self._playing and not self._download_mode

            # Music channel — real volume-based level
            music_ch = self.mixer_channels.get("music")
            if music_ch:
                if playing:
                    base = vol / 100.0
                    ml = max(0.0, min(1.0, base * (0.65 + _rand.random() * 0.35)))
                    mr = max(0.0, min(1.0, base * (0.65 + _rand.random() * 0.35)))
                else:
                    ml = mr = 0.0
                music_ch.update_levels(ml, mr, playing)

            # Jingle channel — simulate level when jingle is playing
            jingle_ch = self.mixer_channels.get("jingle")
            if jingle_ch:
                if self._jingle_playing:
                    jl = 0.5 + _rand.random() * 0.4
                    jr = jl * 0.95
                else:
                    jl = jr = 0.0
                jingle_ch.update_levels(jl, jr, self._jingle_playing)

            # Ads channel — simulate level when ad is playing
            ads_ch = self.mixer_channels.get("ads")
            if ads_ch:
                if self._ad_playing:
                    al = 0.5 + _rand.random() * 0.4
                    ar = al * 0.95
                else:
                    al = ar = 0.0
                ads_ch.update_levels(al, ar, self._ad_playing)

            # Anthem channel — simulate level when anthem is playing
            anthem_ch = self.mixer_channels.get("anthem")
            if anthem_ch:
                anthem_active = getattr(self, '_anthem_playing', False)
                if anthem_active:
                    anl = 0.5 + _rand.random() * 0.4
                    anr = anl * 0.95
                else:
                    anl = anr = 0.0
                anthem_ch.update_levels(anl, anr, anthem_active)

            # Mic channel — real mic level if available, else simulate
            mic_ch = self.mixer_channels.get("mic")
            if mic_ch:
                mic_active = getattr(self.mic, 'enabled', False) if hasattr(self, 'mic') else False
                if mic_active:
                    mic_l = 0.4 + _rand.random() * 0.5
                    mic_r = mic_l * 0.95
                else:
                    mic_l = mic_r = 0.0
                mic_ch.update_levels(mic_l, mic_r, mic_active)

            # Master meter — combined levels
            if hasattr(self, 'master_meter'):
                if playing:
                    base = vol / 100.0
                    mml = max(0.0, min(1.0, base * (0.6 + _rand.random() * 0.4)))
                    mmr = max(0.0, min(1.0, base * (0.6 + _rand.random() * 0.4)))
                else:
                    mml = mmr = 0.0
                self.master_meter.update_levels(mml, mmr, playing)
        except Exception:
            pass

    # -- Settings Helpers --
    def _on_jingle_interval_change(self) -> None:
        try:
            val = int(self.jingle_interval_entry.get())
            if val < 1:
                val = 1
        except ValueError:
            val = 5
        self.jingle_interval_entry.delete(0, "end")
        self.jingle_interval_entry.insert(0, str(val))
        self.jingle.interval = val

    def _on_ad_interval_change(self) -> None:
        try:
            val = int(self.ad_interval_entry.get())
            if val < 1:
                val = 1
        except ValueError:
            val = 10
        self.ad_interval_entry.delete(0, "end")
        self.ad_interval_entry.insert(0, str(val))
        self.commercial.interval = val

    def _play_jingle_manual(self) -> None:
        """Manually play the jingle now — pauses music, plays the jingle,
        then resumes the interrupted song."""
        if self._jingle_playing or self._anthem_playing:
            self._show_toast("🔔 A jingle/anthem is already playing")
            return
        if not self.jingle.jingle_file:
            messagebox.showwarning("No jingle file", "Select a jingle file first.")
            return
        if not os.path.isfile(self.jingle.jingle_file):
            messagebox.showwarning("File not found", f"Jingle file not found:\n{self.jingle.jingle_file}")
            return
        # Save current state
        self._jingle_was_playing = self._playing
        self._jingle_saved_index = self.rq.current_index
        self._jingle_saved_position = 0.0
        if self._playing and self.audio_player.available:
            self._jingle_saved_position = self.audio_player.get_position()
            self.audio_player.pause()
        # Play jingle — resume when it finishes
        self._jingle_playing = True
        self.np_status.configure(text="🔔 Jingle (manual)...", text_color=COLORS["purple"])
        ok = self.audio_player.play_file(
            self.jingle.jingle_file, duration=0,
            on_finish=lambda: self.after(0, self._jingle_resume))
        if not ok:
            self._jingle_playing = False
            self._show_toast("⚠️ Cannot play jingle file")
            self._jingle_resume()  # restore whatever was playing before
            return
        self._playing = True
        self.btn_play.configure(text="⏸")

    def _jingle_stop(self) -> None:
        """Stop jingle playback and resume the previous song."""
        self.audio_player.stop()
        self._jingle_resume()

    def _jingle_resume(self) -> None:
        """Resume playback after a manual jingle."""
        def _do_resume():
            self._jingle_playing = False
            # Only restore playback if something was actually playing before
            # the jingle. _play_index clears _jingle_was_playing whenever the
            # user/queue starts a different track, so a stale resume never
            # hijacks a newer selection.
            if not self._jingle_was_playing:
                return
            idx = getattr(self, '_jingle_saved_index', self.rq.current_index)
            pos = getattr(self, '_jingle_saved_position', 0.0)
            if idx >= 0 and idx < len(self.rq.queue):
                self._saved_playback_position = pos
                self._play_index(idx)
                self._update_now_playing()
                self._refresh_queue()
        self.after(0, _do_resume)

    def _select_jingle(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.ogg *.flac")])
        if path:
            self.jingle.jingle_file = path
            self.jingle_label.configure(text=Path(path).name)

    def _save_settings_to_freq(self) -> None:
        """Save all settings (including queue) to .freq file."""
        self._sync_anthem_times()
        settings = self._collect_settings()
        try:
            self.rq.save_to_file(settings=settings)
            self._save_settings_status.configure(
                text=f"\u2705 Saved to {self.rq._save_path.name}",
                text_color="#4caf50",
            )
            self._clear_settings_dirty()
        except Exception as e:
            self._save_settings_status.configure(
                text=f"\u274c Save failed: {e}",
                text_color=COLORS["red"],
            )

    def _load_settings_from_freq(self) -> None:
        """Load settings from a .freq file."""
        path = filedialog.askopenfilename(
            title="Load Settings from .freq",
            filetypes=[("FreQ Playlist", "*.freq"), ("All files", "*.*")],
        )
        if not path:
            return
        self.rq.load_from_file(path)
        self._restore_settings(self.rq.last_settings)
        self._refresh_queue()
        self._refresh_library()
        self._refresh_presets()
        self._refresh_history()
        self._update_now_playing()
        self._clear_settings_dirty()
        self._save_settings_status.configure(
            text=f"\u2705 Loaded from {Path(path).name}",
            text_color="#4caf50",
        )

    # ── Settings Dirty State ──
    def _build_settings_dirty_banner(self) -> None:
        """Create floating unsaved-changes banner on the settings tab."""
        self._dirty_banner = ctk.CTkFrame(
            self.tab_settings, fg_color="#1a3a5c", corner_radius=10,
            border_color=COLORS["accent"], border_width=1,
        )
        # Pack at top — initially hidden via pack_forget
        ctk.CTkLabel(
            self._dirty_banner,
            text="\u26a0\ufe0f  Settings changed",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#e0e0e0",
        ).pack(side="left", padx=(12, 8), pady=8)
        ctk.CTkButton(
            self._dirty_banner, text="\U0001f4be Save", width=70, height=28,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            corner_radius=6, font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ffffff",
            command=self._save_settings_to_freq,
        ).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(
            self._dirty_banner, text="Discard", width=60, height=28,
            fg_color="transparent", hover_color="#2a2a2a",
            corner_radius=6, font=ctk.CTkFont(size=10),
            text_color="#999999", border_width=1, border_color="#555555",
            command=self._clear_settings_dirty,
        ).pack(side="left", padx=(0, 12), pady=8)

    def _mark_settings_dirty(self) -> None:
        """Show the unsaved-changes banner."""
        if self._settings_dirty or self._restoring_settings:
            return
        self._settings_dirty = True
        try:
            self._dirty_banner.pack(fill="x", padx=6, pady=(6, 0), after=None)
        except Exception:
            pass

    def _clear_settings_dirty(self) -> None:
        """Hide the unsaved-changes banner."""
        self._settings_dirty = False
        try:
            self._dirty_banner.pack_forget()
        except Exception:
            pass

    def _hook_settings_dirty(self) -> None:
        """Bind change events on key widgets to mark settings dirty."""
        # Switches (BooleanVar trace)
        for var in [self.anthem_var, self.anthem_resume_var, self.jingle_var,
                    self.commercial_var, self.norm_var]:
            var.trace_add("write", lambda *_: self.after(0, self._mark_settings_dirty))
        # Entry fields (FocusOut)
        for entry in [self.anthem_morning, self.anthem_evening,
                      self.jingle_interval_entry, self.ad_interval_entry,
                      self.autosave_interval_entry]:
            entry.bind("<FocusOut>", lambda e: self.after(0, self._mark_settings_dirty))
            entry.bind("<Return>", lambda e: self.after(0, self._mark_settings_dirty))
        # Mic duck slider
        self.mic_duck_slider.configure(command=lambda v: self._mark_settings_dirty())

    def _sync_anthem_times(self) -> None:
        """Sync anthem time entries to config."""
        try:
            self.anthem.config.morning_time = self.anthem_morning.get().strip()
            self.anthem.config.evening_time = self.anthem_evening.get().strip()
        except Exception:
            pass

    def _anthem_pause(self) -> None:
        """Pause current playback for anthem (called from scheduler thread)."""
        def _do_pause():
            self._anthem_was_playing = self._playing
            self._anthem_saved_index = self.rq.current_index
            self._anthem_saved_position = 0.0
            if self._playing and self.audio_player.available:
                self._anthem_saved_position = self.audio_player.get_position()
                self.audio_player.pause()
        self.after(0, _do_pause)

    def _anthem_play(self, filepath: str) -> None:
        """Play the anthem file (called from scheduler thread)."""
        def _do_play():
            self._anthem_playing = True
            # Auto-detect duration from file if not set
            dur = self.anthem.config.duration
            if dur <= 0:
                dur = self._detect_anthem_duration(filepath)
                self.anthem.config.duration = dur
            if self.audio_player.available:
                ok = self.audio_player.play_file(filepath, duration=dur)
                if not ok:
                    self._anthem_playing = False
                    return
                self._playing = True
                self.btn_play.configure(text="⏸")
                self.np_title.configure(text="🇹🇭 National Anthem")
                self.np_artist.configure(text="正在播放เพลงชาติ")
                if streamer.is_streaming:
                    streamer.on_track_change("National Anthem", "", filepath)
        self.after(0, _do_play)

    def _anthem_resume(self) -> None:
        """Resume original playback after anthem (called from scheduler thread)."""
        def _do_resume():
            self._anthem_playing = False
            # Only restore playback if something was actually playing before
            # the anthem. _play_index clears _anthem_was_playing whenever the
            # user/queue starts a different track, so a stale resume never
            # hijacks a newer selection.
            if not self._anthem_was_playing:
                return
            idx = getattr(self, '_anthem_saved_index', self.rq.current_index)
            pos = getattr(self, '_anthem_saved_position', 0.0)
            if idx >= 0 and idx < len(self.rq.queue):
                self._saved_playback_position = pos
                self._play_index(idx)
                self._update_now_playing()
                self._refresh_queue()
        self.after(0, _do_resume)

    def _anthem_status(self, state: str, period: str) -> None:
        """Update anthem status in the UI."""
        if hasattr(self, 'anthem_next_label'):
            if state == 'playing':
                self.anthem_next_label.configure(text=f"🇹🇭 Playing anthem ({period})...", text_color=COLORS["accent"])
            elif state == 'finished':
                next_time = self.anthem.get_next_play_time()
                self.anthem_next_label.configure(text=f"Next anthem: {next_time or '—'}", text_color=COLORS["text2"])

    def _play_anthem_manual(self) -> None:
        """Manually play the national anthem now.

        Fallback for when the scheduled play (e.g. 08:00) was missed —
        pauses current playback, plays the anthem, then resumes.
        """
        if self._anthem_playing:
            self._show_toast("🇹🇭 Anthem is already playing")
            return
        self._test_anthem_play()

    def _test_anthem_play(self) -> None:
        """Force-play the anthem now (for testing)."""
        if self._anthem_playing:
            self._show_toast("🇹🇭 Anthem is already playing")
            return
        self._sync_anthem_times()
        if not self.anthem.config.anthem_file:
            messagebox.showwarning("No anthem file", "Select an anthem file first.")
            return
        if not os.path.isfile(self.anthem.config.anthem_file):
            messagebox.showwarning("File not found", f"Anthem file not found:\n{self.anthem.config.anthem_file}")
            return
        # Save current state
        self._anthem_was_playing = self._playing
        self._anthem_saved_index = self.rq.current_index
        self._anthem_saved_position = 0.0
        if self._playing and self.audio_player.available:
            self._anthem_saved_position = self.audio_player.get_position()
            self.audio_player.pause()
        # Play anthem — auto-detect duration from file
        self._anthem_playing = True
        duration = self.anthem.config.duration
        if duration <= 0:
            duration = self._detect_anthem_duration()
            self.anthem.config.duration = duration
        if self.audio_player.available:
            ok = self.audio_player.play_file(
                self.anthem.config.anthem_file, duration=duration)
            if not ok:
                self._anthem_playing = False
                self._show_toast("⚠️ Cannot play anthem file")
                self._anthem_resume()  # restore whatever was playing before
                return
            self._playing = True
            self.btn_play.configure(text="⏸")
            if streamer.is_streaming:
                streamer.on_track_change("National Anthem", "", self.anthem.config.anthem_file)
        if hasattr(self, 'anthem_next_label'):
            self.anthem_next_label.configure(text="🇹🇭 Playing anthem (test)...", text_color=COLORS["accent"])
        # Auto-resume after duration via threading (no sleep on main thread)
        def _auto_resume():
            import time as _t
            _t.sleep(duration)
            self.after(0, self._anthem_resume)
            if hasattr(self, 'anthem_next_label'):
                self.after(0, lambda: self.anthem_next_label.configure(
                    text="Next anthem: " + (self.anthem.get_next_play_time() or "—"),
                    text_color=COLORS["text2"]))
        threading.Thread(target=_auto_resume, daemon=True).start()

    def _test_anthem_stop(self) -> None:
        """Stop anthem playback and resume normal playback."""
        self.audio_player.stop()
        self._anthem_resume()
        if hasattr(self, 'anthem_next_label'):
            self.anthem_next_label.configure(text="Next anthem: " + (self.anthem.get_next_play_time() or "—"),
                                              text_color=COLORS["text2"])

    def _detect_anthem_duration(self, filepath: str = None) -> float:
        """Detect anthem duration from audio file using mutagen."""
        fp = filepath or self.anthem.config.anthem_file
        if not fp or not os.path.isfile(fp):
            return 80.0
        try:
            from mutagen import File as MutagenFile
            mf = MutagenFile(fp)
            if mf and mf.info:
                return mf.info.length
        except Exception:
            pass
        return 80.0

    def _open_time_picker(self, entry) -> None:
        """Open time picker popup and set result to entry."""
        current = entry.get().strip() or "08:00"
        picker = TimePickerDialog(self, initial=current, title="Select Anthem Time")
        self.wait_window(picker)
        if picker.result:
            entry.delete(0, "end")
            entry.insert(0, picker.result)
            entry.configure(border_color=COLORS["green"])
            entry.after(1500, lambda: entry.configure(border_color=COLORS["border"]))
            self._sync_anthem_times()
            self._mark_settings_dirty()

    def _select_anthem(self) -> None:
        """Select national anthem file."""
        path = filedialog.askopenfilename(
            title="Select National Anthem File",
            filetypes=[("Audio", "*.mp3 *.wav *.ogg *.flac"), ("All files", "*.*")]
        )
        if path:
            self.anthem.config.anthem_file = path
            self.anthem_label.configure(text=Path(path).name)
            self.anthem.config.duration = self._detect_anthem_duration(path)

    def _on_mic_device_selected(self, name: str) -> None:
        """Set mic input device from dropdown selection."""
        if name == "Default":
            self.mic.device_index = None
        else:
            for d in self._mic_input_devices:
                if d.name == name:
                    self.mic.device_index = d.id
                    break

    def _refresh_mic_devices(self) -> None:
        """Rescan and refresh the mic input device list."""
        self.audio_mgr.refresh()
        self._mic_input_devices = self.audio_mgr.get_input_devices()
        names = ["Default"] + [d.name for d in self._mic_input_devices]
        self.mic_device_menu.configure(values=names)
        if self.mic.device_index is None:
            self.mic_device_var.set("Default")
        else:
            for d in self._mic_input_devices:
                if d.id == self.mic.device_index:
                    self.mic_device_var.set(d.name)
                    break

    def _on_mic_gain_change(self, v: float) -> None:
        """Update mic gain and label."""
        self.mic.gain = round(v, 2)
        self.mic_gain_label.configure(text=f"{self.mic.gain:.1f}x")

    def _on_mic_duck_change(self, v: float) -> None:
        """Update duck volume level."""
        self.mic.duck_volume = round(v, 2)
        pct = int(v * 100)
        self.mic_duck_label.configure(text=f"{pct}%")

    def _emergency_mic_toggle(self) -> None:
        self.mic.emergency = not self.mic.emergency
        if self.mic.emergency:
            # Save current volume and duck it
            self._pre_duck_volume = self.volume_var.get()
            duck_vol = self.mic.duck_volume * 100.0
            self.volume_var.set(duck_vol)
            self.volume_slider.set(duck_vol)
            self._on_volume_change(duck_vol)
            self.btn_mute.configure(text="🎤")
        else:
            # Restore original volume
            restore_vol = getattr(self, '_pre_duck_volume', 80.0)
            self.volume_var.set(restore_vol)
            self.volume_slider.set(restore_vol)
            self._on_volume_change(restore_vol)
            self.btn_mute.configure(text="🔊")

    def _timer_mic_toggle(self) -> None:
        try:
            dur = float(self.mic_timer_entry.get())
        except ValueError:
            dur = 30.0
        self.mic.timer_enabled = not self.mic.timer_enabled
        self.mic.timer_duration = dur
        if self.mic.timer_enabled:
            messagebox.showinfo("Mic Timer", f"Timer mic ON for {dur}s")
        else:
            messagebox.showinfo("Mic Timer", "Timer mic OFF")

    def _add_commercial_files(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("Audio", "*.mp3 *.wav *.ogg")])
        if paths:
            self.commercial.ads.extend(paths)
            self.commercial_count_label.configure(text=f"{len(self.commercial.ads)} ad files")

    def _apply_eq_preset(self, name: str) -> None:
        self.eq.apply_preset(name)
        for attr, sl in self.eq_sliders.items():
            sl.set(getattr(self.eq, attr))

    def _select_edit_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.ogg *.flac")])
        if path:
            self._edit_file = path
            self.edit_file_label.configure(text=Path(path).name)

    def _edit_trim(self) -> None:
        if not self._edit_file:
            messagebox.showwarning("Warning", "Select an audio file first")
            return
        # Ask for start/end
        from tkinter.simpledialog import askfloat
        start = askfloat("Trim", "Start (seconds):", initialvalue=0, minvalue=0)
        if start is None:
            return
        end = askfloat("Trim", "End (seconds):", initialvalue=30, minvalue=start + 1)
        if end is None:
            return
        result = self.editor.trim(self._edit_file, start, end)
        if result.success:
            messagebox.showinfo("Done", f"Trimmed → {Path(result.output_path).name}")
        else:
            messagebox.showerror("Error", result.error or "Trim failed")

    def _edit_fadein(self) -> None:
        if not self._edit_file:
            messagebox.showwarning("Warning", "Select an audio file first")
            return
        result = self.editor.fade_in(self._edit_file)
        if result.success:
            messagebox.showinfo("Done", f"Fade in → {Path(result.output_path).name}")
        else:
            messagebox.showerror("Error", result.error or "Fade in failed")

    def _edit_fadeout(self) -> None:
        if not self._edit_file:
            messagebox.showwarning("Warning", "Select an audio file first")
            return
        result = self.editor.fade_out(self._edit_file)
        if result.success:
            messagebox.showinfo("Done", f"Fade out → {Path(result.output_path).name}")
        else:
            messagebox.showerror("Error", result.error or "Fade out failed")

    def _edit_normalize(self) -> None:
        if not self._edit_file:
            messagebox.showwarning("Warning", "Select an audio file first")
            return
        result = self.editor.normalize(self._edit_file)
        if result.success:
            messagebox.showinfo("Done", f"Normalized → {Path(result.output_path).name}")
        else:
            messagebox.showerror("Error", result.error or "Normalize failed")

    def _toggle_autosave(self) -> None:
        self._autosave_enabled = self.autosave_var.get()
        if self._autosave_enabled:
            import time
            self._autosave_last = time.time()

    def _on_autosave_interval_change(self) -> None:
        try:
            val = int(self.autosave_interval_entry.get())
            if val < 5:
                val = 5
        except ValueError:
            val = 60
        self.autosave_interval_entry.delete(0, "end")
        self.autosave_interval_entry.insert(0, str(val))
        self._autosave_interval = val

    def _apply_theme(self) -> None:
        self.configure(fg_color=COLORS["bg"])
        self._refresh_queue()
        self._refresh_presets()
        self._refresh_history()

    def _set_light_theme(self) -> None:
        global COLORS
        COLORS = self.theme_mgr.set_light()
        self._apply_theme()

    def _set_dark_theme(self) -> None:
        global COLORS
        COLORS = self.theme_mgr.set_dark()
        self._apply_theme()

    # ─────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────
    def _on_close(self) -> None:
        """Save and shutdown cleanly"""
        self._sync_anthem_times()
        self.anthem.stop()
        settings = self._collect_settings()
        self.audio_player.shutdown()
        self.rq.save_to_file(settings=settings)
        self.destroy()

    def destroy(self) -> None:
        try:
            self.audio_player.shutdown()
        except Exception:
            pass
        super().destroy()


# ═══════════════════════════════════════════════════════════════
# Splash Screen
# ═══════════════════════════════════════════════════════════════

class FreQSplash(ctk.CTkToplevel):
    """Splash screen shown during app startup"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.overrideredirect(True)  # borderless
        self.configure(fg_color="#0a0e14")
        self.attributes("-topmost", True)

        # Size and center on screen
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 420, 320
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Rounded corners via frame
        outer = ctk.CTkFrame(self, fg_color="#131920", corner_radius=16)
        outer.pack(fill="both", expand=True, padx=2, pady=2)

        # Logo
        logo_frame = ctk.CTkFrame(outer, fg_color="transparent")
        logo_frame.pack(expand=True)

        # Animated equalizer bars
        bars_frame = ctk.CTkFrame(logo_frame, fg_color="transparent")
        bars_frame.pack(pady=(20, 10))
        self._bars = []
        bar_heights = [18, 28, 36, 24, 40, 20, 32]
        bar_colors = ["#58a6ff", "#79b8ff", "#bc8cff", "#f778ba", "#bc8cff", "#79b8ff", "#58a6ff"]
        for i, (bh, bc) in enumerate(zip(bar_heights, bar_colors)):
            bar = ctk.CTkFrame(bars_frame, width=14, height=bh, fg_color=bc, corner_radius=4)
            bar.pack(side="left", padx=3)
            bar.pack_propagate(False)
            self._bars.append((bar, bh))

        # App name
        ctk.CTkLabel(
            logo_frame, text="FreQ",
            font=ctk.CTkFont(size=32, weight="bold"),
            text_color="#58a6ff",
        ).pack(pady=(8, 2))

        ctk.CTkLabel(
            logo_frame, text="Radio Playlist Manager",
            font=ctk.CTkFont(size=12),
            text_color="#4e5769",
        ).pack()

        # Loading bar
        self._progress = 0.0
        self._progress_bar = ctk.CTkProgressBar(
            outer, width=300, height=4,
            fg_color="#1a2230",
            progress_color="#58a6ff",
            corner_radius=2,
        )
        self._progress_bar.pack(pady=(10, 6))
        self._progress_bar.set(0)

        self._status_label = ctk.CTkLabel(
            outer, text="Loading...",
            font=ctk.CTkFont(size=10),
            text_color="#4e5769",
        )
        self._status_label.pack(pady=(0, 16))

        # Start animation
        self._animate_bars(0)
        self._tick()

    def _animate_bars(self, step: int) -> None:
        """Animate equalizer bars up and down"""
        import random
        for bar, base_h in self._bars:
            new_h = max(12, base_h + random.randint(-10, 10))
            bar.configure(height=new_h)
        self._bar_step = step
        self._bar_job = self.after(150, lambda: self._animate_bars(step + 1))

    def _tick(self) -> None:
        """Advance progress bar"""
        self._progress += 0.02
        if self._progress >= 1.0:
            self._progress = 1.0
            self._progress_bar.set(1.0)
            self._status_label.configure(text="Ready!")
            self.after(300, self._finish)
            return
        self._progress_bar.set(self._progress)
        # Status messages
        if self._progress < 0.3:
            self._status_label.configure(text="Initializing audio engine...")
        elif self._progress < 0.6:
            self._status_label.configure(text="Loading playlist cache...")
        elif self._progress < 0.85:
            self._status_label.configure(text="Detecting audio devices...")
        else:
            self._status_label.configure(text="Almost ready...")
        self.after(30, self._tick)

    def _finish(self) -> None:
        """Fade out splash then call the completion callback"""
        self._fading = True
        self._fade_step(0.9)

    def _fade_step(self, alpha: float) -> None:
        if alpha <= 0.1:
            # Done fading — fire callback
            try:
                self.after_cancel(self._bar_job)
            except Exception:
                pass
            cb = getattr(self, '_on_done', None)
            if cb:
                cb()
            return
        self.attributes("-alpha", alpha)
        self.after(30, lambda: self._fade_step(alpha - 0.15))

    def on_complete(self, callback) -> None:
        """Set callback for when splash finishes"""
        self._on_done = callback


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1) Create the main app — withdrawn so it's invisible
    app = RadioApp()
    app.withdraw()

    # 2) Show splash (child of the hidden app)
    splash = FreQSplash(app)

    # 3) When splash finishes, reveal the main window
    def _on_splash_done():
        splash.destroy()
        app.after(50, _reveal)

    def _reveal():
        app.deiconify()
        app.state("zoomed")
        app.lift()
        app.focus_force()
        # Check for updates after window is visible
        app.after(1000, lambda: _check_updates(app))

    def _check_updates(app):
        """Check for updates asynchronously and show dialog if available."""
        def _on_update_check(update_info: Optional[UpdateInfo]):
            if update_info:
                app.after(0, lambda: _show_update_dialog(app, update_info))
        check_for_updates_async(callback=_on_update_check, silent=True)

    def _show_update_dialog(app, update_info: UpdateInfo):
        """Show a beautiful update notification dialog."""
        dialog = ctk.CTkToplevel(app)
        dialog.title("Update Available")
        dialog.geometry("480x420")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg"])
        dialog.transient(app)
        dialog.grab_set()
        
        # Center on parent
        app.update_idletasks()
        x = app.winfo_x() + (app.winfo_width() - 480) // 2
        y = app.winfo_y() + (app.winfo_height() - 420) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # Header
        header = ctk.CTkFrame(dialog, fg_color=COLORS["accent"], corner_radius=0, height=80)
        header.pack(fill="x")
        header.pack_propagate(False)
        
        ctk.CTkLabel(header, text="🚀  Update Available!",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#ffffff").pack(pady=(20, 0))
        ctk.CTkLabel(header, text=f"FreQ {update_info.version} is now available",
                     font=ctk.CTkFont(size=12),
                     text_color="#e0e0e0").pack(pady=(4, 0))
        
        # Content
        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Version info
        version_frame = ctk.CTkFrame(content, fg_color=COLORS["bg_card"], corner_radius=8)
        version_frame.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(version_frame, text="Version Info",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"], anchor="w").pack(padx=12, pady=(8, 4))
        
        info_grid = ctk.CTkFrame(version_frame, fg_color="transparent")
        info_grid.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkLabel(info_grid, text=f"Current: v{CURRENT_VERSION}",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text2"]).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ctk.CTkLabel(info_grid, text=f"Latest: v{update_info.version}",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["green"]).grid(row=0, column=1, sticky="w")
        
        if update_info.published_at:
            pub_date = update_info.published_at[:10]  # YYYY-MM-DD
            ctk.CTkLabel(info_grid, text=f"Released: {pub_date}",
                         font=ctk.CTkFont(size=10),
                         text_color=COLORS["text3"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        
        # Release notes
        ctk.CTkLabel(content, text="Release Notes",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text"], anchor="w").pack(anchor="w", pady=(8, 4))
        
        notes_text = format_release_notes(update_info.body, max_length=800)
        notes_box = ctk.CTkTextbox(content, fg_color=COLORS["bg_input"],
                                   text_color=COLORS["text"],
                                   font=ctk.CTkFont(size=11),
                                   corner_radius=8, height=120, wrap="word")
        notes_box.pack(fill="x", pady=(0, 10))
        notes_box.insert("1.0", notes_text)
        notes_box.configure(state="disabled")
        
        # Buttons
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(5, 0))
        
        def _open_download():
            open_download_page(update_info.url)
            dialog.destroy()
        
        def _skip():
            dialog.destroy()
        
        ctk.CTkButton(btn_frame, text="📥 Download Update", height=36,
                     fg_color=COLORS["green"], hover_color="#2ea043",
                     text_color="#ffffff", font=ctk.CTkFont(size=12, weight="bold"),
                     command=_open_download).pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        ctk.CTkButton(btn_frame, text="Maybe Later", height=36,
                     fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                     text_color=COLORS["text2"], font=ctk.CTkFont(size=12),
                     command=_skip).pack(side="left", expand=True, fill="x", padx=(5, 0))
    
    splash.on_complete(_on_splash_done)

    app.mainloop()
