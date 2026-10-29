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

import logging
import os
import queue
import math
import re
import subprocess
import shutil
import sys
import tempfile
import time
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
    END_MODES,
    RadioQueue,
    Song,
    SongStatus,
    YouTubeManager,
    generate_demo_songs,
)
from audio import AudioDeviceManager
from audio_meter import meter as audio_meter
from player import AudioPlayer, _ffmpeg_exe as _find_ffmpeg_exe
import mic_recorder
from mic_recorder import record_wav, SOUNDDEVICE_AVAILABLE
from cache import cache as song_cache
from icons import cached_icon, get_tk_image, ICON_PATHS
from tooltips import ToolTip
from streaming import streamer, StreamProtocol, StreamState, FFMPEG_AVAILABLE, WEBRTC_AVAILABLE, _native_mix
from keys_tab import build_keys_tab, DEFAULT_KEYS as _DEFAULT_KEYS
from log_handler import setup_logging, memory_handler
from silence_sentinel import SilenceSentinel
from dual_output import DualOutputMirror
from plugin_system import PluginManager, PluginContext

# U4 dry-run Silence Sentinel.ultimate_toggle: env var "1"/"true" enables
# it; default is OFF (Ultimate rule: opt-in, and dry-run never touches
# playback — it only logs + counts silence events).
_silence_sentinel_enabled = os.environ.get(
    "FREQ_SILENCE_SENTINEL", "").strip().lower() in ("1", "true", "yes", "on")
from visualizer import VisualizerWidget
from scheduler import Scheduler, ScheduleAction, ScheduleEvent
from asrun import (AsRunLog, END_REASON_LABELS, LEGAL_MIN_DAYS,
                   DEFAULT_KEEP_DAYS)
from livelan import LiveLAN
from titlebar import CustomTitleBar
from update_checker import (
    check_for_updates_async, open_download_page, format_release_notes,
    UpdateInfo, CURRENT_VERSION, version_label, format_display_version
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

# Design tokens — single source of truth lives in theme.py.
# Never hardcode hex values, font sizes, or radii in widget code.
from theme import COLORS, FONT_SIZES, RADII  # noqa: E402


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

    def add_section(self, label: str) -> None:
        """Insert a non-clickable section divider between tab groups."""
        ctk.CTkLabel(self._scroll, text=label,
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COLORS["text3"]).pack(side="left", padx=(10, 0), pady=6)
        ctk.CTkFrame(self._scroll, fg_color=COLORS["border"], width=1, height=20,
                     corner_radius=0).pack(side="left", padx=(6, 0), pady=12)

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
                 channel_id: str = "music", on_solo=None, on_change=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=8, **kwargs)
        self.channel_id = channel_id
        self._label = label
        self._color = color
        self._muted = False
        self._solo = False
        self._volume = 100.0        # unity — the master fader owns overall level
        self._gain_db = 0.0  # -12 to +12 dB
        self._on_change = on_change  # fired on fader/gain/mute/solo changes
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
            name_row, text="M", width=20, height=18, corner_radius=4,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text2"],
            command=self._toggle_mute,
        )
        self.mute_btn.pack(side="left", padx=1)

        self.solo_btn = ctk.CTkButton(
            name_row, text="S", width=20, height=18, corner_radius=4,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text2"],
            command=self._toggle_solo,
        )
        self.solo_btn.pack(side="left", padx=1)

        # Row 2: dB readout
        self.db_label = ctk.CTkLabel(left, text="-inf dB",
                                     font=ctk.CTkFont(size=10),
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
                                      font=ctk.CTkFont(size=10, weight="bold"),
                                      text_color=COLORS["text"], width=60, anchor="e")
        self.vol_label.pack(anchor="e")

        gain_row = ctk.CTkFrame(right, fg_color="transparent")
        gain_row.pack(anchor="e")

        self.gain_dn_btn = ctk.CTkButton(
            gain_row, text="-", width=18, height=16, corner_radius=4,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text3"],
            command=lambda: self._adjust_gain(-1.0),
        )
        self.gain_dn_btn.pack(side="left", padx=1)

        self.gain_label = ctk.CTkLabel(gain_row, text="0.0 dB",
                                       font=ctk.CTkFont(size=10),
                                       text_color=COLORS["accent"], width=44)
        self.gain_label.pack(side="left")

        self.gain_up_btn = ctk.CTkButton(
            gain_row, text="+", width=18, height=16, corner_radius=4,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text3"],
            command=lambda: self._adjust_gain(1.0),
        )
        self.gain_up_btn.pack(side="left", padx=1)

    def _on_fader_change(self, val: float) -> None:
        self._volume = float(val)
        self._update_vol_label()
        if self._on_change:
            self._on_change(self.channel_id)

    def _adjust_gain(self, delta: float) -> None:
        """Adjust gain by delta dB (-12 to +12)."""
        self._gain_db = max(-12.0, min(12.0, self._gain_db + delta))
        self.gain_label.configure(text=f"Gain: {self._gain_db:+.1f} dB")
        self._update_vol_label()
        if self._on_change:
            self._on_change(self.channel_id)

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
        if self._on_change:
            self._on_change(self.channel_id)

    def _toggle_solo(self) -> None:
        self._solo = not self._solo
        if self._solo:
            self.solo_btn.configure(text="S", fg_color=COLORS["orange"], text_color="#000")
        else:
            self.solo_btn.configure(text="S", fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])
        if self._on_solo:
            self._on_solo(self.channel_id, self._solo)
        if self._on_change:
            self._on_change(self.channel_id)

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_solo(self) -> bool:
        return self._solo

    def get_state(self) -> dict:
        """Snapshot of fader/gain/mute/solo for .freq persistence."""
        return {
            "fader": round(self._volume, 1),
            "gain_db": round(self._gain_db, 1),
            "mute": self._muted,
            "solo": self._solo,
        }

    def set_state(self, st: dict) -> None:
        """Restore fader/gain/mute/solo (no callbacks — caller applies engine volumes)."""
        try:
            self._volume = max(0.0, min(100.0, float(st.get("fader", self._volume))))
            self.fader.set(self._volume)
            self._gain_db = max(-12.0, min(12.0, float(st.get("gain_db", self._gain_db))))
            self.gain_label.configure(text=f"Gain: {self._gain_db:+.1f} dB")
            self._update_vol_label()
            self._muted = bool(st.get("mute", False))
            self.mute_btn.configure(
                text="M",
                fg_color=COLORS["red"] if self._muted else COLORS["bg_hover"],
                text_color="#fff" if self._muted else COLORS["text2"],
            )
            self._solo = bool(st.get("solo", False))
            self.solo_btn.configure(
                text="S",
                fg_color=COLORS["orange"] if self._solo else COLORS["bg_hover"],
                text_color="#000" if self._solo else COLORS["text2"],
            )
        except Exception:
            pass

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
            return COLORS["green"]
        elif level < 0.85:
            return COLORS["orange"]
        else:
            return COLORS["red"]

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
                    color = COLORS["ramp_ok"] if frac <= 0.6 else (COLORS["ramp_warn"] if frac <= 0.85 else COLORS["ramp_err"])
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

_QUEUE_FONT_CACHE: dict = {}


def _queue_font(size: int, bold: bool = False):
    """Cached Tk font tuple for queue cards — sized in PIXELS, not points.

    A Tk size given in points grows with the OS display scaling, so the same
    card looked ~2x bigger on a 125-150 % Windows display than in the design.
    A negative Tk size means pixels, so `size` here is the exact em size the
    card is drawn with at any DPI. The family is read once from the CTk theme
    and the tuple is reused (a CTkFont per label was part of the old cost).
    """
    key = (size, bold)
    font = _QUEUE_FONT_CACHE.get(key)
    if font is None:
        try:
            family = ctk.ThemeManager.theme["CTkFont"]["family"]
        except Exception:
            family = "Segoe UI"
        font = (family, -size, "bold" if bold else "normal")
        _QUEUE_FONT_CACHE[key] = font
    return font


def _safe_seconds(value) -> float:
    """A finite, non-negative song length — malformed data becomes 0.0.

    `Song.duration` can be None (stream entries), NaN/inf (hand-edited
    .freq) or negative. Summing it for the queue header and drawing it per
    card must never raise, so every read goes through this.
    """
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(secs) or math.isinf(secs) or secs < 0:
        return 0.0
    return secs


# Virtualized queue geometry (module level so the row pitch is one number).
QUEUE_CARD_H = 68        # card height (matches the approved row design)
QUEUE_CARD_GAP = 15      # free space between two cards
QUEUE_CARD_MIN_W = 130   # a card never renders narrower than this
QUEUE_CARD_PAD = 6       # inset of the card from the panel edge
QUEUE_ROW_H = QUEUE_CARD_H + QUEUE_CARD_GAP   # scroll pitch, in pixels
QUEUE_OVERSCAN = 4       # extra cards drawn above/below the viewport
QUEUE_POLL_MS = 40       # how often the visible window follows the scroll

# Card right side: the song length is rightmost, the scheduled start–stop
# clock time sits just left of it, so the pair reads as one column down the
# list. Reserved once here, so the title/artist clipping cannot overlap it.
QUEUE_DUR_INSET = 14     # gap between the length and the card's right edge
QUEUE_DUR_W = 46         # width of the length column (fits h:mm:ss)
QUEUE_TIME_W = 108       # width of the "HH:MM:SS–HH:MM:SS" column
QUEUE_SIDE_W = QUEUE_DUR_INSET + QUEUE_DUR_W + QUEUE_TIME_W + 8

# End-of-song mark: the card's right edge belongs to a narrow column that
# says what happens when the song ends (＋ = normal, AD▶ = break then on,
# ■ = stop, AD■ = break then stop). It is still ONE canvas item per row, and
# the click zone is resolved from the same geometry, so the virtualized list
# stays as cheap as it was.
QUEUE_MARK_W = 40        # width of the mark column (fits two icons)
QUEUE_MARK_INSET = 8     # gap between the mark and the card's right edge
QUEUE_MARK_GAP = 8       # gap between the mark and the length column
QUEUE_FLAG_W = 20        # width of the skip-flag click zone, left of the mark

# The mark itself is VECTOR GEOMETRY — a ring, a play triangle, a stop
# square — not a font glyph. Tk cannot render SVG and its canvas shapes are
# aliased, so each icon is rasterized once at 4× from its paths and
# downsampled (Lanczos) into one small image the whole list shares: smooth
# edges, identical on every machine, still one canvas item per row.
QUEUE_MARK_ICON_W = 12   # one drawn icon is 12 px wide …
QUEUE_MARK_ICON_H = 16   # … and 16 px tall (the box an icon is fitted into)
QUEUE_MARK_ICON_GAP = 5  # space between two drawn icons
QUEUE_MARK_SS = 4        # supersampling factor for the anti-aliased icons

# The operator's own artwork: one PNG per action, baked from icons/*.svg by
# `make_end_icons.py` (Tk cannot read SVG and no SVG library is shipped).
# `auto` has no file — it keeps the quiet ring, because "nothing special" is
# not an action to advertise.
_END_ICON_PNG = {
    "break": "play_normally.png",
    "stop": "stop_immediately.png",
    "break_stop": "ads_jingle_then_stop.png",
}
_END_ICON_DIR = Path(__file__).resolve().parent / "icons"

# mode → ((shape, colour key), …). "break_stop" is the two icons side by
# side: break THEN stop.
_END_SHAPES = {
    "auto": (("ring", "text2"),),
    "break": (("play", "orange"),),
    "stop": (("stop", "red"),),
    "break_stop": (("play", "orange"), ("stop", "red")),
}

_END_LABEL = {
    "auto": "Normal — ads/jingle on their own schedule, then the next song",
    "break": "Ads/jingle after this song, then continue",
    "stop": "Stop after this song",
    "break_stop": "Ads/jingle after this song, then stop",
}


class QueueRowHandle:
    """Registry entry for one song in the virtualized queue list.

    Queue rows are canvas items now, not widgets: the whole list is a single
    ``tk.Canvas`` with only the on-screen rows drawn into it, so there is
    nothing to build per song. Call sites (marker repaints, keyboard
    selection, diagnostics) still index ``_queue_rows`` by ORIGINAL queue
    index, so each song keeps this featherweight handle instead of a widget.
    """

    __slots__ = ("_queue_index", "song")

    def __init__(self, index: int, song):
        self._queue_index = index
        self.song = song

    def winfo_exists(self) -> bool:
        """Always True while the list is built — rows are drawn on demand."""
        return True


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

    def update_levels(self, volume: float, playing: bool,
                      levels: Optional[tuple] = None) -> None:
        """Draw REAL output levels.

        Sources, in order of trust:
        1. ``levels`` — (peak_l, peak_r) already post-volume: the native
           render engine's own meter, or the WASAPI loopback tap when the
           engine has no meter (pygame). Both work for music AND live mics;
        2. the streaming PCM meter (``audio_meter.levels``) when a stream
           pipeline is feeding it — that PCM is pre-volume, so only this
           source gets scaled by ``gain``;
        3. otherwise decay to silence — never a synthetic animation.
        """
        rms_l, rms_r, peak_l, peak_r = audio_meter.levels()
        gain = max(0.0, min(1.0, volume / 100.0))
        if playing and (peak_l or peak_r):
            self._rms_l = min(1.0, rms_l * gain)
            self._rms_r = min(1.0, rms_r * gain)
            self._peak_l = max(min(1.0, peak_l * gain), self._peak_l * 0.85)
            self._peak_r = max(min(1.0, peak_r * gain), self._peak_r * 0.85)
        elif playing and levels:
            # Tap levels are already post-volume (the engine applies the
            # fader, and the loopback measures the device buffer), so
            # scaling by `gain` here again made the VU read low whenever
            # the slider was down.
            l, r = levels
            self._rms_l = min(1.0, l * 0.9)
            self._rms_r = min(1.0, r * 0.9)
            self._peak_l = max(min(1.0, l), self._peak_l * 0.85)
            self._peak_r = max(min(1.0, r), self._peak_r * 0.85)
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
            return COLORS["green"]   # green
        elif level < 0.85:
            return COLORS["orange"]   # yellow
        else:
            return COLORS["red"]   # red

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
                             fill=COLORS["text3"], font=ctk.CTkFont(size=10), anchor="s")

        y_bar = y_top + 8

        def draw_channel(label: str, rms: float, peak: float, hold: float, x_start: int) -> None:
            # Label
            self.create_text(x_start, y_bar + bar_h // 2, text=label,
                             fill=COLORS["text2"], font=ctk.CTkFont(size=10, weight="bold"), anchor="w")
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
                    color = COLORS["wave_ok"] if frac <= 0.6 else (COLORS["wave_warn"] if frac <= 0.85 else COLORS["wave_err"])
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

class UiFreezeWatchdog:
    """UI-thread freeze watchdog + supervisor heartbeat [sup-0926].

    An in-process watchdog alone cannot rescue a deadlocked UI — that is
    the supervisor's job (freq_supervisor.py runs gui.py as a child).
    What the watchdog CAN do:

      * the UI thread stamps a wall-clock value on every ``after`` tick
        (small, safe, never misses while the loop pumps);
      * a private WATCHER thread compares that stamp with the wall clock:
        if the UI thread has not ticked for ``threshold`` seconds it logs
        a WARNING with the UI thread's REAL stack (so the blocking code
        is named), and a "resumed" line after the loop comes back;
      * while the UI thread pumps, the WATCHER writes the supervisor
        heartbeat file every ``heartbeat_ms`` (when FREQ_SUPERVISED is
        set) — the supervisor kills + restarts the whole tree when the
        file stalls. All Tk calls stay on the UI thread.
    """

    def __init__(self, root=None, threshold: float = 0.25,
                 heartbeat_ms: int = 50, watch_interval: float = 0.02,
                 logger_name: str = "freq.gui") -> None:
        self._root = root
        self._threshold = float(threshold)
        self._heartbeat_ms = max(10, int(heartbeat_ms))
        self._watch_interval = float(watch_interval)
        self._log = logging.getLogger(logger_name)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._hb_path = os.environ.get("FREQ_SUPERVISOR_HEARTBEAT", "")
        self._supervised = os.environ.get("FREQ_SUPERVISED") == "1"
        # UI-thread state: the after-loop stamps ``ui_stamp`` with
        # time.monotonic(); the watcher only READS it (GIL-safe).
        self._ui_stamp = time.monotonic()
        self._watcher_started = False
        self._ui_loop_running = False
        self._thread = None
        self._stalled_reported = False
        self._last_hb_mono = 0.0

    def start(self) -> None:
        if self._watcher_started:
            return
        self._watcher_started = True
        self._ui_loop_running = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch, name="freq-ui-watchdog", daemon=True)
        self._thread.start()
        if self._root is not None:
            # Kick the UI-side ticker; after(0) so it runs immediately.
            try:
                self._root.after(0, self._ui_ticker)
            except Exception:
                self._ui_loop_running = False

    def stop(self) -> None:
        self._ui_loop_running = False
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        # The smoke contract reads ``_thread`` after stop() and joins it —
        # hand back the (already exited) thread object, not None.
        # Clean-stop marker: the supervisor sees "0" in the heartbeat
        # and exits instead of treating the close as a crash.
        if self._supervised and self._hb_path:
            self._write_hb_marker("0")

    # ── UI thread side (after-loop) ──
    def _ui_ticker(self) -> None:
        if not self._ui_loop_running:
            return
        self._ui_stamp = time.monotonic()
        try:
            if self._root is not None:
                self._root.after(self._heartbeat_ms, self._ui_ticker)
        except Exception:
            self._ui_loop_running = False

    # ── watcher thread side (no Tk calls) ──
    def _watch(self) -> None:
        main = threading.main_thread()
        main_ident = main.native_id if hasattr(main, "native_id") else main.ident
        self._log.debug("[watchdog] watcher armed (threshold=%.2fs)",
                        self._threshold)
        while not self._stop.wait(self._watch_interval):
            now_mono = time.monotonic()
            stalled = (now_mono - self._ui_stamp) > self._threshold
            if stalled and not self._stalled_reported:
                self._stalled_reported = True
                stack = self._main_thread_stack(main_ident)
                self._log.warning(
                    "[watchdog] UI thread stalled > %.2fs — stack of the "
                    "blocked main thread:\n%s", self._threshold, stack)
            elif not stalled and self._stalled_reported:
                self._stalled_reported = False
                self._log.warning("[watchdog] UI thread resumed after stall "
                                  "(gap %.2fs)", now_mono - self._ui_stamp)
            if self._supervised and self._hb_path and not stalled:
                if (now_mono - getattr(self, "_last_hb_mono", 0.0)) >= (
                        self._heartbeat_ms / 1000.0):
                    self._last_hb_mono = now_mono
                    self._write_hb_marker(str(time.time()))

    @staticmethod
    def _main_thread_stack(ident: Optional[int]) -> str:
        import traceback
        try:
            for tid, frame in sys._current_frames().items():
                if tid == ident or (hasattr(threading.main_thread(), "native_id")
                                    and tid == threading.main_thread().native_id):
                    return "".join(traceback.format_stack(frame)[-12:]).rstrip()
        except Exception:
            pass
        return "(stack unavailable)"

    def _write_hb_marker(self, content: str) -> None:
        try:
            tmp = self._hb_path + ".tmp"
            with open(tmp, "w", encoding="ascii") as f:
                f.write(content)
            os.replace(tmp, self._hb_path)
        except Exception:
            pass


class WaveformWidget(tk.Canvas):
    """Audio waveform display with playback position marker."""

    def __init__(self, parent, width=600, height=48, bg=COLORS["bg_card"],
                 wave_color=COLORS["accent"], played_color=COLORS["waveform_played"],
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
        self._on_seek = None
        self.configure(cursor="hand2")
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_click)

    def set_waveform(self, samples: list[float]) -> None:
        """Set waveform amplitude data (list of floats 0.0-1.0)."""
        self._samples = samples
        self._draw()

    def set_position(self, pct: float) -> None:
        """Update playback position (0.0-1.0)."""
        self._position = max(0.0, min(1.0, pct))
        self._draw()

    def set_on_seek(self, callback) -> None:
        """Register ``callback(fraction)`` — fired when the user clicks the bar."""
        self._on_seek = callback

    def _on_click(self, event) -> None:
        if not self._samples or not self._on_seek:
            return
        w = self.winfo_width() or self._width
        frac = max(0.0, min(1.0, event.x / max(1, w)))
        try:
            self._on_seek(frac)
        except Exception:
            pass

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
        """Generate waveform data from an audio file using ffmpeg.

        Uses the same ffmpeg resolution as the rest of the app
        (``player._ffmpeg_exe``): the bundled copy in deps/ffmpeg-essentials
        or ``{app}\\ffmpeg`` survives an install on machines with no
        ffmpeg on PATH — a bare ``"ffmpeg"`` command here used to fail
        there and the widget silently fell back to a flat line.
        """
        import subprocess
        import struct
        try:
            cmd = [
                _find_ffmpeg_exe(), "-i", filepath,
                "-ac", "1", "-ar", "11025", "-f", "s16le",
                "-t", "120",  # max 2 minutes
                "-y", "pipe:1"
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode != 0 or not result.stdout:
                return WaveformWidget._generate_silence(num_samples)
            raw = result.stdout

            # Bucket RMS/peak math: native C++ when available, pure Python
            # otherwise — no numpy required either way.
            try:
                from audio_meter import waveform_peaks
            except Exception:
                waveform_peaks = None
            if waveform_peaks is not None:
                waveform = waveform_peaks(raw, num_samples)
            else:
                samples = struct.unpack(f"<{len(raw)//2}h", raw)
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
        self.configure(bg=COLORS["bg_card"])
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

        BG = COLORS["bg_card"]
        CARD = COLORS["bg_hover"]
        FG = "#ffffff"
        ACCENT = COLORS["accent"]
        MUTED = COLORS["text2"]

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
        tk.Button(bf, text="  OK  ", bg=COLORS["green"], fg="#000", bd=0, height=2,
                  activebackground=COLORS["green_dark"], activeforeground="#000",
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
    """Popup to choose what a playlist link should import.

    Three answers: all N songs, only the playlist's FIRST video (a
    watch?v=..&list=.. link is usually pasted to grab its opening track), or
    a range. result = None (cancel) | "all" | "first" | (start, end).
    """

    def __init__(self, parent, total: int, first_title: str = ""):
        super().__init__(parent)
        self.result = None  # None = cancel, "all" = all, (start, end) = range
        # "first" = only the playlist's first video (result "first")
        self.title("Playlist Import")
        W, H = 380, 420
        self.geometry(f"{W}x{H}")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg_card"])
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._total = total
        BG = COLORS["bg_card"]
        CARD = COLORS["bg_hover"]
        FG = "#ffffff"
        ACCENT = COLORS["accent"]
        MUTED = COLORS["text2"]
        WARN = COLORS["orange"]

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
                       value="all", bg=CARD, fg=FG, selectcolor=COLORS["bg_selected"],
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 11)).pack(side="left", padx=8, pady=6)

        # Option: first video only — the added third choice. A playlist link
        # is often pasted just to grab the song it opens with.
        first_frame = tk.Frame(self, bg=CARD)
        first_frame.pack(fill="x", padx=24, pady=4)
        tk.Radiobutton(first_frame, text="Add only first video in the playlist",
                       variable=self._choice, value="first", bg=CARD, fg=FG,
                       selectcolor=COLORS["bg_selected"],
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 11)).pack(anchor="w", padx=8, pady=(6, 0))
        if first_title:
            tk.Label(first_frame, text=first_title, bg=CARD, fg=MUTED,
                     font=("Segoe UI", 9), wraplength=300,
                     justify="left").pack(anchor="w", padx=34, pady=(0, 6))

        # Option: Range
        range_frame = tk.Frame(self, bg=CARD)
        range_frame.pack(fill="x", padx=24, pady=4)
        tk.Radiobutton(range_frame, text="Add songs from #", variable=self._choice,
                       value="range", bg=CARD, fg=FG, selectcolor=COLORS["bg_selected"],
                       activebackground=CARD, activeforeground=FG,
                       font=("Segoe UI", 11)).pack(side="left", padx=(8, 4), pady=6)

        # Start spinbox
        self._start_var = tk.IntVar(value=1)
        start_sb = tk.Spinbox(range_frame, from_=1, to=total, width=5,
                              textvariable=self._start_var, bg=COLORS["bg_selected"], fg=FG,
                              buttonbackground=COLORS["border_focus"], font=("Segoe UI", 11),
                              insertbackground=FG)
        start_sb.pack(side="left", padx=2, pady=6)

        tk.Label(range_frame, text="to #", bg=CARD, fg=FG,
                 font=("Segoe UI", 11)).pack(side="left", padx=2, pady=6)

        # End spinbox
        self._end_var = tk.IntVar(value=min(100, total))
        end_sb = tk.Spinbox(range_frame, from_=1, to=total, width=5,
                            textvariable=self._end_var, bg=COLORS["bg_selected"], fg=FG,
                            buttonbackground=COLORS["border_focus"], font=("Segoe UI", 11),
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
        self._warn_label = tk.Label(self, text="", bg=BG, fg=COLORS["red"],
                                    font=("Segoe UI", 9), wraplength=320)
        self._warn_label.pack(pady=(4, 0))

        # OK / Cancel buttons
        bf = tk.Frame(self, bg=CARD)
        bf.pack(fill="x", padx=24, pady=(12, 14))
        tk.Button(bf, text="Cancel", bg=CARD, fg=FG, bd=0, height=2,
                  activebackground=MUTED, activeforeground=FG,
                  font=("Segoe UI", 11),
                  command=self._cancel).pack(side="left", expand=True, fill="x", padx=(6, 4), pady=6)
        self._ok_btn = tk.Button(bf, text="  Add Songs  ", bg=COLORS["green"], fg="#000", bd=0, height=2,
                                 activebackground=COLORS["green_dark"], activeforeground="#000",
                                 font=("Segoe UI", 11, "bold"),
                                 command=self._ok)
        self._ok_btn.pack(side="left", expand=True, fill="x", padx=(4, 6), pady=6)

        # Trace changes
        self._choice.trace_add("write", lambda *_: self._on_choice_change())
        self._start_var.trace_add("write", lambda *_: self._on_range_change())
        self._end_var.trace_add("write", lambda *_: self._on_range_change())
        self._on_choice_change()

        # Center on parent. The height is measured AFTER the widgets exist so
        # the third option (and a long first-video title) cannot be clipped by
        # a hard-coded box.
        self.update_idletasks()
        H = min(560, max(H, self.winfo_reqheight()))
        px = parent.winfo_x() + (parent.winfo_width() - W) // 2
        py = parent.winfo_y() + (parent.winfo_height() - H) // 2
        self.geometry(f"{W}x{H}+{max(0,px)}+{max(0,py)}")
        # Enter confirms the highlighted option, Esc cancels — the dialog now
        # opens for every multi-song playlist, so it must stay one keystroke.
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<KP_Enter>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

    def _on_choice_change(self):
        choice = self._choice.get()
        if choice == "first":
            self._warn_label.configure(text="")
            self._ok_btn.configure(text="  Add First Video  ")
        elif choice == "all" and self._total > 100:
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
        elif self._choice.get() == "first":
            self.result = "first"
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

# Tk only accepts its long modifier names inside an event pattern: "<ctrl-d>"
# raises "bad event type or keysym ctrl", while "<Control-d>" is valid. The
# Keys tab stores "Ctrl+D", and the old conversion lowercased the modifier —
# so every Ctrl/Alt combo raised, got swallowed by a bare except, and silently
# never bound (Ctrl+D could never reach _clear_queue_selection).
_TK_MODIFIERS = {
    "ctrl": "Control", "control": "Control",
    "shift": "Shift",
    "alt": "Alt",
    "meta": "Meta", "cmd": "Meta", "win": "Meta",
}


class MicPreviewDialog(ctk.CTkToplevel):
    """Pick which of a mic segment's microphones Preview should monitor.

    A mic queue item can carry up to four inputs (the same list it airs).
    Auditioning them one at a time is what an operator wants when checking
    levels or a dead cable, so the picker lists the item's own microphones
    and starts the monitor on the chosen one. ``choice`` is the picked dict,
    or None when the dialog is cancelled.
    """

    def __init__(self, parent, choices: list, on_pick, title: str = ""):
        super().__init__(parent)
        self.choice = None
        self._choices = list(choices)
        self._on_pick = on_pick
        self.title("Preview microphone")
        width = 460
        height = 132 + 34 * max(1, len(self._choices))
        self.geometry(f"{width}x{height}")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_card"])
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        ctk.CTkLabel(self, text="🎧 Monitor which microphone?",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=18,
                                                      pady=(14, 2))
        if title:
            ctk.CTkLabel(self, text=title[:64], anchor="w",
                         font=ctk.CTkFont(size=10),
                         text_color=COLORS["text3"]).pack(anchor="w", padx=18)
        self._var = ctk.IntVar(value=0)
        for index, choice in enumerate(self._choices):
            ctk.CTkRadioButton(
                self, text=choice.get("label", "microphone"),
                variable=self._var, value=index, height=26,
                text_color=COLORS["text"],
                fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            ).pack(anchor="w", padx=22, pady=3)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=16, pady=(8, 12))
        ctk.CTkButton(row, text="Cancel", width=88, height=30, corner_radius=8,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      text_color=COLORS["text"], command=self._cancel
                      ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(row, text="🎧 Monitor", width=112, height=30,
                      corner_radius=8, fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"],
                      command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._accept())

    def _accept(self) -> None:
        try:
            index = int(self._var.get())
        except Exception:
            index = 0
        if not 0 <= index < len(self._choices):
            index = 0
        self.choice = self._choices[index]
        callback, self._on_pick = self._on_pick, None
        try:
            self.grab_release()
            self.destroy()
        except Exception:
            pass
        if callback is not None:
            callback(self.choice)

    def _cancel(self) -> None:
        self.choice = None
        self._on_pick = None
        try:
            self.grab_release()
            self.destroy()
        except Exception:
            pass


def key_event_sequence(key: str):
    """Tk event pattern for a Keys-tab key string, e.g. "Ctrl+D" → "<Control-d>".

    Returns None for plain keys ("N", "Space") and malformed input — the
    caller keeps its own single-key path for those.

    A Shift combo on a letter uses the UPPERCASE keysym ("Ctrl+Shift+D" →
    "<Control-Shift-D>"): holding Shift makes the keyboard report "D", so a
    lowercase pattern would never match — the same reason this file already
    binds paste as both <Control-v> and <Control-V>.
    """
    parts = [p.strip() for p in (key or "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = [_TK_MODIFIERS.get(p.lower(), p) for p in parts[:-1]]
    detail = parts[-1]
    if len(detail) == 1 and detail.isalpha() and "Shift" in mods:
        detail = detail.upper()
    else:
        detail = detail.lower()
    return "<" + "-".join(mods + [detail]) + ">"


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
        # Unplugged output device → the player auto-migrates to the default
        # endpoint; this handler keeps the UI (dropdown/toast) in sync.
        self.audio_player.on_device_lost = self._on_device_lost

        # U4 Silence Sentinel — dry-run observer only (never touches the
        # engine/queue). Off unless FREQ_SILENCE_SENTINEL is set.
        self.silence_sentinel = SilenceSentinel(
            on_silence=self._silence_on_silence)
        if _silence_sentinel_enabled:
            self.silence_sentinel.configure(enabled=True, dry_run=True)

        # U5 Plugin SDK (observe-only) — user plugins from plugins/ get
        # lifecycle + playback events; they can log/notify/read a snapshot
        # but have NO pathway into the audio engine.
        self.plugin_manager = PluginManager(ctx=PluginContext(
            notify=self._plugin_notify,
            log=lambda m: logging.getLogger("freq.plugins.user").info("%s", m),
            snapshot=self._plugin_snapshot,
        ))
        _plugins_loaded, _plugin_errors = self.plugin_manager.load_all()
        if _plugins_loaded or _plugin_errors:
            logging.getLogger("freq.plugins").info(
                "plugins: %d loaded, %d failed to load",
                _plugins_loaded, len(_plugin_errors))
        self._emit_plugins("on_start")
        # Restored later from the .freq settings (plugin_enabled_map).

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

        # Master Broadcast Chain "Immediate mode" (apply while you move).
        # Declared here so a fresh session has the attribute and every
        # .freq round trip has something to write and read back.
        self._master_chain_immediate = False

        # .freq round-trip bookkeeping behind the Settings panel: the
        # settings block the running session last read from (or wrote to)
        # the file, and when. Informational only — nothing here reaches
        # the audio engine.
        self._freq_restored_from = ""
        self._freq_restored_at = ""
        self._freq_restored_source = "loaded"
        self._freq_restored_settings: dict = {}

        # Remembered "Add Mic Segment" choices (device NAMES + per-mic
        # gains), restored from the .freq settings at startup; the dialog
        # also updates it live so repeats within one session are prefilled.
        self._mic_segment_memory: dict = {}

        # P0 As-run log (opt-in): the append-only record of what actually
        # went to air. Created BEFORE the UI because the As-run tab renders
        # its state, and OFF by default so nothing changes for a station
        # that does not want it.
        self.asrun = AsRunLog(enabled=False, app_version=_splash_version())
        self.asrun_keep_days = DEFAULT_KEEP_DAYS
        self._asrun_day = ""          # "" = today (resolved on refresh)
        self._asrun_row_labels: list = []

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
        # Playback hotkeys (space/n/p/m/arrows) must NOT fire while the
        # user is typing in ANY text widget — a global bind on the window
        # fires even when an Entry/Text holds focus (bindtags), which used
        # to skip songs mid-typing in the settings fields.

        self.bind("<space>", self._no_typing(self._on_global_space))
        self.bind("<Right>", self._no_typing(lambda e: self._seek_forward()))
        self.bind("<Left>", self._no_typing(lambda e: self._seek_backward()))
        self.bind("<Up>", self._no_typing(lambda e: self._volume_up()))
        self.bind("<Down>", self._no_typing(lambda e: self._volume_down()))
        # Shift + Up/Down in the queue: extend the selection range
        # (Down → to the end of the list, Up → to the first entry).
        # Binds must sit BEFORE the plain volume bindings so shift is
        # ours to consume..
        self.bind("<Shift-Up>", lambda e: self._apply_queue_shift_arrow(-1))
        self.bind("<Shift-Down>", lambda e: self._apply_queue_shift_arrow(1))
        # Jump the queue to the song on air. Plain arrows belong to the
        # FOCUSED LIST (see _on_queue_canvas_key), so volume keeps Up/Down
        # everywhere else; this one is a modifier combo and works anywhere.
        self.bind("<Control-j>", lambda e: self._queue_jump_to_playing())
        self.bind("<Control-J>", lambda e: self._queue_jump_to_playing())
        self.bind("<n>", self._no_typing(lambda e: self._next_track()))
        self.bind("<p>", self._no_typing(lambda e: self._prev_track()))
        self.bind("<m>", self._no_typing(lambda e: self._toggle_mute()))
        self.bind("<Control-s>", lambda e: self._save())
        # Queue undo/redo. The handler bows out inside a text field, where
        # Ctrl+Z belongs to the entry itself.
        self.bind("<Control-z>", self._undo_key)
        self.bind("<Control-Z>", self._undo_key)
        self.bind("<Control-y>", self._redo_key)
        self.bind("<Control-Y>", self._redo_key)
        self.bind("<Control-Shift-Z>", self._redo_key)
        self.bind("<Control-o>", lambda e: self._open_file())
        self.bind("<Control-n>", lambda e: self._new_playlist())
        self.bind("<Control-i>", lambda e: self._import_playlist())
        self.bind("<Control-v>", self._on_paste)
        self.bind("<Control-V>", self._on_paste)


    # ─────────────────────────────────────────
    # Drag-and-Drop
    # ─────────────────────────────────────────
    def _no_typing(self, handler):
        """Wrap a hotkey handler so it NEVER fires while the user is
        typing in a text widget.

        focus_get() returns the INNER tk widget for CTk fields (e.g.
        .!ctkentry.!entry), so class-name strings (Entry/Text/Combobox/...)
        are the reliable signal — isinstance() against CTk classes misses.
        """

        def guarded(event):
            try:
                w = self.focus_get()
                wclass = w.winfo_class() if w is not None else ""
            except Exception:
                wclass = ""
            if wclass in ("Entry", "Text", "Combobox", "Spinbox", "TEntry",
                          "TCombobox", "TSpinbox"):
                return "break"   # typing — swallow, do NOT run the hotkey
            return handler(event)

        return guarded

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
        menu = tk.Menu(widget, tearoff=0, bg=COLORS["bg_card"], fg="white",
                       activebackground=COLORS["accent"], activeforeground="white")
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
        menu = tk.Menu(widget, tearoff=0, bg=COLORS["bg_card"], fg="white",
                       activebackground=COLORS["accent"], activeforeground="white")
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
        self._add_to_queue(song)



    def _add_mic_segment(self) -> None:
        """Add a timed microphone recording as a normal queue item."""
        if not (SOUNDDEVICE_AVAILABLE or getattr(mic_recorder, "NATIVE_CAPTURE_AVAILABLE", False)):
            messagebox.showerror("Microphone unavailable", "No microphone backend available.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Mic Segment")
        dialog.geometry("390x360")
        dialog.minsize(340, 280)
        dialog.resizable(True, True)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Add microphone segment",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(12, 6))
        # Fixed title on top, fixed Add button at the bottom, everything
        # between scrolls — the button stays reachable at ANY window size
        # (the quad-mic rows once pushed it off the 360x220 window).
        dialog_btnbar = ctk.CTkFrame(dialog, fg_color="transparent")
        dialog_btnbar.pack(side="bottom", fill="x", pady=(6, 14))
        dialog_body = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        dialog_body.pack(fill="both", expand=True)
        duration_row = ctk.CTkFrame(dialog_body, fg_color="transparent")
        duration_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(duration_row, text="Duration (seconds):", width=140, anchor="w").pack(side="left")
        duration_entry = ctk.CTkEntry(duration_row, width=90)
        duration_entry.insert(0, str(int(self.mic.timer_duration or 30)))
        duration_entry.pack(side="left")

        devices = getattr(self, "_mic_input_devices", self.audio_mgr.get_input_devices())
        device_names = ["Default"] + [d.name for d in devices]
        # Prefill with the last-used choices (this session or the .freq
        # preset). Unknown/stale names fall back to the toolbar selection.
        memory = getattr(self, "_mic_segment_memory", {}) or {}

        def _remembered(*keys: str) -> str:
            for key in keys:
                value = memory.get(key)
                if isinstance(value, str) and value in device_names:
                    return value
            return ""

        last_main = _remembered("mic")
        if not last_main and hasattr(self, "mic_device_var"):
            current = self.mic_device_var.get()
            if current in device_names:
                last_main = current

        def _saved_gain(values, index: int) -> str:
            try:
                return f"{float(values[index]):g}"
            except (TypeError, ValueError, IndexError):
                return "1.0"

        device_var = ctk.StringVar(value=last_main or "Default")
        device_row = ctk.CTkFrame(dialog_body, fg_color="transparent")
        device_row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(device_row, text="Input mic:", width=140, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(device_row, variable=device_var, values=device_names, width=170).pack(side="left")
        ctk.CTkLabel(device_row, text="Gain:").pack(side="left", padx=(14, 2))
        gain_entry = ctk.CTkEntry(device_row, width=52)
        gain_entry.insert(0, _saved_gain(memory.get("gains"), 0))
        gain_entry.pack(side="left")

        # Multi-mic: up to FOUR simultaneous inputs (interviews, panels,
        # guest DJs). Rows 2-4 are optional ("(none)"); every mic has
        # its own gain because cheap webcam mics differ wildly.
        extra_vars: list[ctk.StringVar] = []
        extra_gain_entries: list[ctk.Entry] = []
        extra_labels = ("Second mic:", "Third mic:", "Fourth mic:")
        for i, label in enumerate(extra_labels):
            var = ctk.StringVar(value=_remembered(f"mic{i + 2}") or "(none)")
            extra_vars.append(var)
            row = ctk.CTkFrame(dialog_body, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=4)
            ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
            ctk.CTkOptionMenu(row, variable=var,
                              values=["(none)"] + device_names, width=170).pack(side="left")
            ctk.CTkLabel(row, text="Gain:").pack(side="left", padx=(14, 2))
            entry = ctk.CTkEntry(row, width=52)
            entry.insert(0, _saved_gain(memory.get("gains"), i + 1))
            entry.pack(side="left")
            extra_gain_entries.append(entry)

        def _parse_gain(entry) -> float:
            try:
                value = float(entry.get().strip())
                return max(0.5, min(4.0, value))
            except (ValueError, AttributeError):
                return 1.0

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
            extra_ids: list = []
            for var in extra_vars:
                chosen = var.get()
                extra_ids.append(
                    next((d.id for d in devices if d.name == chosen), None)
                    if chosen not in ("(none)", "Default") else None)
            count = 1 + sum(1 for i in extra_ids if i is not None)
            title = f"Microphone ×{count}" if count > 1 else "Microphone"
            # Record the STABLE endpoint ids beside the indices — indices
            # are re-assigned by Windows on every reboot/unplug, ids are
            # permanent, and an index-only item used to resolve to the
            # WRONG (silent) microphone after a restart.
            def _endpoint_of(dev_id):
                return next((d.endpoint_id for d in devices
                             if d.id == dev_id and getattr(d, "endpoint_id", None)), None)
            endpoint_ids = [_endpoint_of(device_id)] + [
                _endpoint_of(i) for i in extra_ids]
            gains = [_parse_gain(gain_entry)] + [
                _parse_gain(entry) if device_id is not None else 1.0
                for entry, device_id in zip(extra_gain_entries, extra_ids)]
            gains = [g for g, d in zip(gains, [device_id] + extra_ids) if d is not None]
            self.rq.add_song(Song(
                title=title,
                artist="Live voice",
                duration=duration,
                source="mic",
                mic_duration=duration,
                mic_device_index=device_id,
                mic_device_index2=extra_ids[0],
                mic_device_index3=extra_ids[1],
                mic_device_index4=extra_ids[2],
                mic_gains=gains,
                # Full 4-slot list (None = Default/unused) — positions MUST
                # stay aligned with the mic_device_index slots above.
                mic_endpoint_ids=endpoint_ids,
            ))
            # Remember these choices as the next dialog's defaults; the
            # 60s autosave / close handler persists them into the .freq file.
            saved_extras = [v.get() if v.get() not in ("(none)", "Default") else ""
                            for v in extra_vars]
            self._mic_segment_memory = {
                "mic": selected,
                "mic2": saved_extras[0],
                "mic3": saved_extras[1],
                "mic4": saved_extras[2],
                "gains": [_parse_gain(gain_entry)]
                         + [_parse_gain(e) for e in extra_gain_entries],
            }
            self._refresh_queue()
            dialog.destroy()

        ctk.CTkButton(dialog_btnbar, text="Add to Queue", command=add_item,
                      fg_color=COLORS["green"], text_color="#000000").pack()

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
            ctk.CTkLabel(bar, text="🎙️", font=ctk.CTkFont(size=20)).pack(side="left", padx=(12, 6))

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

        # ── File operations: grouped on the LEFT, right after the brand ──
        file_ops = ctk.CTkFrame(bar, fg_color="transparent")
        file_ops.pack(side="left", padx=(0, 10), pady=6)

        ctk.CTkButton(file_ops, text="🆕 New", width=68, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._new_playlist).pack(side="left", padx=3)
        ToolTip(file_ops.winfo_children()[-1], text="New Playlist",
                description="Start an empty .freq playlist (library and history are kept)")
        ctk.CTkButton(file_ops, text="📂 Open", width=80, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._open_file).pack(side="left", padx=3)
        ToolTip(file_ops.winfo_children()[-1], text="Open File", description="Load a .freq playlist file")
        ctk.CTkButton(file_ops, text="📥 Import", width=84, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._import_playlist).pack(side="left", padx=3)
        ToolTip(file_ops.winfo_children()[-1], text="Import Playlist",
                description="Add the songs of another .freq file to this queue (duplicates skipped)")
        ctk.CTkButton(file_ops, text="💾 Save", width=80, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._save).pack(side="left", padx=3)
        ToolTip(file_ops.winfo_children()[-1], text="Save", description="Save current queue to file")
        ctk.CTkButton(file_ops, text="💾 Save As", width=90, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._save_as).pack(side="left", padx=3)
        ToolTip(file_ops.winfo_children()[-1], text="Save As", description="Save queue to a new .freq file")

        # Group divider: file ops (left) vs status/system utilities (right)
        ctk.CTkFrame(bar, fg_color=COLORS["border"], width=1, height=22,
                     corner_radius=0).pack(side="left", padx=(4, 8), pady=9)

        # ── Status & system utilities: RIGHT cluster ──
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=12, pady=6)

        ctk.CTkButton(right, text="Demo", width=90, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._load_demo).pack(side="left", padx=3)
        ToolTip(right.winfo_children()[-1], text="Load Demo", description="Load 10 demo songs to try the app")
        ctk.CTkButton(right, text="Cache", width=90, height=30,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"], corner_radius=8,
                       command=self._show_cache_menu).pack(side="left", padx=3)
        ToolTip(right.winfo_children()[-1], text="Cache Manager", description="View and clear downloaded song cache")

        self.btn_theme =        ctk.CTkButton(right, text="\U0001f319", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=14), command=self._toggle_theme)
        self.btn_theme.pack(side="left", padx=2)

        # New features buttons
        self.btn_visualizer = ctk.CTkButton(right, text="📊", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._toggle_visualizer)
        self.btn_visualizer.pack(side="left", padx=2)
        ToolTip(self.btn_visualizer, text="Audio Visualizer", description="Toggle spectrum analyzer")

        self.btn_scheduler = ctk.CTkButton(right, text="⏰", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._show_scheduler_dialog)
        self.btn_scheduler.pack(side="left", padx=2)
        ToolTip(self.btn_scheduler, text="Scheduler", description="Schedule playback events")

        self.btn_remote = ctk.CTkButton(right, text="📱", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._toggle_remote_control)
        self.btn_remote.pack(side="left", padx=2)
        ToolTip(self.btn_remote, text="LiveLAN Remote", description="Start web remote for mobile")

        self.btn_anthem = ctk.CTkButton(right, text="🇹🇭", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
                       font=ctk.CTkFont(size=16),
                       command=self._play_anthem_manual)
        self.btn_anthem.pack(side="left", padx=2)
        ToolTip(self.btn_anthem, text="National Anthem (Manual)", description="Play the national anthem now — for when the 08:00/18:00 schedule missed")

        self.btn_jingle = ctk.CTkButton(right, text="🔔", width=36, height=30,
                       fg_color="transparent", hover_color=COLORS["bg_hover"], corner_radius=8,
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

        # Studio Display: the control-room screen (now playing, next up, VU,
        # ON AIR, clock) for a second monitor.
        self.btn_studio = ctk.CTkButton(right, text="\U0001f4fa", width=32, height=32,
                       fg_color="transparent", hover_color=COLORS["bg_hover"],
                       border_width=1, border_color=COLORS["border"],
                       corner_radius=8, font=ctk.CTkFont(size=14),
                       command=self._open_studio_display)
        self.btn_studio.pack(side="left", padx=(6, 2))
        ToolTip(self.btn_studio, text="Studio Display",
                description="Control-room screen on a second monitor — now playing, next up, VU meter, ON AIR lamp and clock (F11 fullscreen)")

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
        # Second header line: when the next jingle / ad / anthem airs. It is
        # packed only while there is something to report, so a plain music
        # queue keeps the panel it always had.
        self.queue_next_label = ctk.CTkLabel(panel, text="",
                                             font=ctk.CTkFont(size=11),
                                             text_color=COLORS["text3"],
                                             anchor="w", justify="left")
        # Jump-to-now-playing, beside the stats: the mouse way to do what
        # Ctrl+J does (and the only one that works when the list is scrolled
        # away from the song that is actually on air).
        self.btn_queue_now = ctk.CTkButton(
            hdr, text="🎯 Now", width=74, height=26, corner_radius=8,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text"], font=ctk.CTkFont(size=11),
            command=self._queue_jump_to_playing)
        self.btn_queue_now.pack(side="right", padx=(0, 6))
        ToolTip(self.btn_queue_now, text="Jump to now playing",
                description="Scroll to and select the song on air (Ctrl+J)")

        # Search bar
        search_frame = ctk.CTkFrame(panel, fg_color="transparent")
        search_frame.pack(fill="x", padx=12, pady=(0, 4))
        self.queue_search_frame = search_frame     # the ⏭ line packs above it
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
        # Clicking the playlist background (not a row) drops the selection,
        # the way Explorer/Finder behave. The list canvas handles clicks on
        # rows itself, so a click ON a row never reaches these handlers.
        self._bind_queue_empty_click()

        # Queue action buttons
        btn_bar = ctk.CTkFrame(panel, fg_color="transparent")
        btn_bar.pack(fill="x", padx=12, pady=(0, 10))

        # Clear button (red)
        clear_btn = ctk.CTkButton(btn_bar, text="  Clear", width=90, height=32, corner_radius=8,
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
                       text_color="#ffffff", anchor="w",
                       command=self._remove_selected)
        remove_icon = self._icon("delete", 16, "#ffffff")
        if remove_icon:
            remove_btn.configure(image=remove_icon, compound="left")
        remove_btn.pack(side="left", padx=2)
        ToolTip(remove_btn, text="Remove", description="Remove selected song from queue")

        # Filter button (OBS-style): appears only while a row is selected —
        # opens the "Filters for '<item>'" dialog (EQ/Auto-Gain for songs,
        # the native mic filter chain for mic segments).
        self.btn_queue_filter = ctk.CTkButton(
            btn_bar, text="  Filter", width=96, height=32, corner_radius=8,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text"], anchor="w",
            command=self._open_selected_filters)
        filter_icon = self._icon("tune", 16, COLORS["text"])
        if filter_icon:
            self.btn_queue_filter.configure(image=filter_icon, compound="left")
        # Packed on the RIGHT so it pops up next to the selected row's end,
        # like the OBS window where Filters sits beside the item list.
        self.btn_queue_filter.pack(side="right", padx=2)
        self.btn_queue_filter.pack_forget()      # hidden until a selection
        ToolTip(self.btn_queue_filter, text="Filter",
                description="EQ, auto-gain and mic filters for the selected item")

        # Edit button: sits beside Filter and opens the native waveform
        # editor for the selected item (trim / cut / fade / normalize …).
        # Hidden until a row is selected, exactly like Filter.
        self.btn_queue_edit = ctk.CTkButton(
            btn_bar, text="  Edit", width=88, height=32, corner_radius=8,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text"], anchor="w",
            command=self._open_selected_editor)
        edit_icon = self._icon("content_cut", 16, COLORS["text"])
        if edit_icon:
            self.btn_queue_edit.configure(image=edit_icon, compound="left")
        self.btn_queue_edit.pack(side="right", padx=(2, 4))
        self.btn_queue_edit.pack_forget()        # hidden until a selection
        ToolTip(self.btn_queue_edit, text="Edit audio",
                description="Trim, cut, fade and normalize the selected song in "
                            "the native editor; the original file is never changed")

        # Preview (audition) button: sits beside Edit, hidden until a row is
        # selected. It plays the selected song on the MONITOR device only —
        # the on-air route is never touched, so an audition during a live
        # broadcast cannot leak into it.
        self.btn_queue_preview = ctk.CTkButton(
            btn_bar, text="  ▶ Preview", width=104, height=32, corner_radius=8,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            text_color=COLORS["text"], anchor="w",
            command=self._toggle_preview)
        self.btn_queue_preview.pack(side="right", padx=2)
        self.btn_queue_preview.pack_forget()     # hidden until a selection
        ToolTip(self.btn_queue_preview, text="Preview",
                description="Audition the selected song on the monitor device "
                            "— nothing goes on air and the program keeps playing")
        # Preview state (audition session, its loader poller and stop flag).
        self._preview_session = None
        self._preview_events = None
        self._preview_cancel = None
        self._preview_after = None
        self._preview_pending = False
        self._preview_started = False
        self._preview_title = ""
        self._audition = None        # live link / mic monitor (audition.py)
        self._mic_preview_dialog = None   # mic picker, while it is open
        # Queue undo/redo stacks: (label, deep-copied queue, position on air).
        self._queue_undo = []
        self._queue_redo = []

        self._queue_selected: Optional[int] = None
        # Random insertion (Add tab): the add paths read this plain bool, not
        # the Tk variable — playlist imports add their songs from a worker
        # thread and a StringVar/BooleanVar is only safe on the UI thread.
        self._queue_random_insert = False
        # Multi-select (Shift+click / Shift+arrow): the selected rows as an
        # ORDERED list (first = top of the range, last = newest row), the
        # anchor the range grows from, and live row widgets for repaints.
        # A list — not a set — because the arrow handler reads sel[0]/sel[-1];
        # with a set that raised TypeError and only the simulated test missed it.
        self._queue_selection: list = []
        self._queue_shift_anchor: Optional[int] = None
        self._queue_rows: dict = {}
        # Virtualized list state: the visible (orig_index, Song) pairs, the
        # single canvas that draws them, and the canvas items drawn right now.
        self._queue_view: list = []
        self._queue_pos_by_index: dict = {}
        self._queue_items: dict = {}
        self._queue_drawn = None
        self._queue_canvas = None
        self._queue_hover_pos: Optional[int] = None
        self._queue_view_after = None
        self._queue_cursor = ""      # last cursor installed on the list canvas
        # End-of-song mark icons (mode → Tk image), rendered once per app.
        self._end_photos: dict = {}

    # ── Sidebar (right) ──
    def _build_sidebar(self, parent) -> None:
        self.sidebar_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_card"], corner_radius=12, width=380)
        self.sidebar_frame.pack(side="right", fill="y", padx=(5, 0), pady=5)
        self.sidebar_frame.pack_propagate(False)

        # Custom pill tab bar
        self._pill_tabs = PillTabBar(self.sidebar_frame, on_switch=self._on_sidebar_tab_switch)
        self._pill_tabs.pack(fill="x", padx=6, pady=(6, 0))

        # Sidebar tabs, grouped by workflow: making music → on air → system.
        self._tab_names: list[str] = []
        self._tab_icons: list[str] = []
        self._tab_idx = {}
        for section, tabs in (
            ("MUSIC", [("Add", "➕"), ("Library", "📚"), ("Preset", "📂"), ("History", "📜")]),
            ("ON AIR", [("Stream", "📡"), ("Mixer", "🎚️"), ("As-run", "🧾")]),
            ("SYSTEM", [("Keys", "⌨️"), ("Settings", "⚙️"), ("Log", "📋")]),
        ):
            self._pill_tabs.add_section(section)
            for name, icon in tabs:
                self._tab_names.append(name)
                self._tab_icons.append(icon)
                self._tab_idx[name] = self._pill_tabs.add_tab(name, icon)

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
        self.tab_asrun = ctk.CTkFrame(self._tab_content, fg_color="transparent")
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

        # As-run wrapper
        asrun_scroll = ctk.CTkScrollableFrame(self.tab_asrun, fg_color=COLORS["bg"],
            scrollbar_button_color=COLORS["border"])
        asrun_scroll.pack(fill="both", expand=True)
        self.asrun_frame = asrun_scroll

        # Build all tab contents
        self._build_add_tab()
        self._build_library_tab()
        self._build_presets_tab()
        self._build_history_tab()
        self._build_log_tab()
        self._build_asrun_tab()
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
            "As-run": self.tab_asrun,
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
        if name == "As-run":
            # Re-read the day the moment the tab is opened (the poller only
            # keeps it current while it stays visible).
            self._refresh_asrun(force=True)

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
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
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
            fg_color=COLORS["accent"], text_color="#ffffff",
            hover_color=COLORS["accent_hover"],
            command=self._add_youtube_playlist,
        )
        self.btn_yt_playlist.pack(fill="x", padx=8, pady=(0, 8))

        # ── Random insertion ──
        # ONE switch for every add path on this tab (and for drops and the
        # library): ON means each new song is dropped into a random slot of
        # the queue still to come instead of always at the end of the list.
        rand_row = ctk.CTkFrame(tab, fg_color=COLORS["bg_hover"], corner_radius=8)
        rand_row.pack(fill="x", padx=8, pady=(0, 6))
        self._random_insert_var = ctk.BooleanVar(value=False)
        self.random_insert_switch = ctk.CTkSwitch(
            rand_row, text="\U0001f3b2 Random positions in the queue",
            variable=self._random_insert_var, onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=11),
            command=self._on_random_insert_toggle)
        self.random_insert_switch.pack(anchor="w", padx=8, pady=(7, 0))
        ctk.CTkLabel(rand_row, text="New songs scatter into the queue ahead",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left", anchor="w").pack(anchor="w", padx=8,
                                                       pady=(0, 7))

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

        self.browse_audio_btn = ctk.CTkButton(
            tab, text="📂 Browse Audio (pick several)", height=32,
            fg_color=COLORS["accent"], text_color="#ffffff",
            hover_color=COLORS["accent_hover"],
            command=self._pick_file)
        self.browse_audio_btn.pack(fill="x", padx=8, pady=(4, 8))

        ctk.CTkButton(tab, text="🎙 Add Mic Segment to Queue", height=32,
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"],
                       command=self._add_mic_segment).pack(fill="x", padx=8, pady=(0, 8))

        # ── Live stream (.m3u8 / HLS) ──
        # A signal link is queued like any other item but has no file: it is
        # pulled live by ffmpeg while it airs (see AudioPlayer.play_stream).
        ctk.CTkFrame(tab, height=2, fg_color=COLORS["accent"]).pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(tab, text="📡 Add Live Stream (.m3u8)",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["text2"]).pack(anchor="w", padx=8, pady=(8, 4))

        stream_row = ctk.CTkFrame(tab, fg_color="transparent")
        stream_row.pack(fill="x", padx=8, pady=(0, 4))
        self.stream_url_entry = ctk.CTkEntry(
            stream_row, placeholder_text="Paste .m3u8 signal link here...",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.stream_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._add_paste_menu(self.stream_url_entry)
        ctk.CTkButton(
            stream_row, text="📋", width=32, height=32,
            fg_color=COLORS["bg_card"], hover_color=COLORS["accent"],
            command=lambda: self._paste_to_entry(self.stream_url_entry),
            font=ctk.CTkFont(size=16),
        ).pack(side="right")

        self.stream_name_entry = ctk.CTkEntry(
            tab, placeholder_text="Stream name (optional)",
            fg_color=COLORS["bg"], border_color=COLORS["border"],
        )
        self.stream_name_entry.pack(fill="x", padx=8, pady=(0, 4))

        self.btn_stream_add = ctk.CTkButton(
            tab, text="📡 Add Stream to Queue", height=32,
            fg_color=COLORS["accent"], text_color="#ffffff",
            hover_color=COLORS["accent_hover"],
            command=self._add_stream,
        )
        self.btn_stream_add.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(tab, text="Plays live from the link — no download, the item runs until you stop it",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left", anchor="w").pack(anchor="w", padx=8, pady=(0, 8))

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
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
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
                       fg_color="transparent", border_width=1,
                       border_color=COLORS["border"], hover_color=COLORS["bg_hover"],
                       text_color=COLORS["text2"],
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
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
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
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            corner_radius=6, font=ctk.CTkFont(size=10),
            command=self._toggle_log_autoscroll)
        self.log_autoscroll_btn.pack(side="left", padx=2)

        # Clear button
        ctk.CTkButton(hdr, text="🗑️ Clear", width=70, height=26,
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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

    # ═══════════════════════════════════════
    # As-run Log Tab (P0)
    # ═══════════════════════════════════════
    # "What actually aired" — an append-only file per day (see asrun.py).
    # This tab is a VIEW of that file plus the switch that turns it on; it
    # never edits history, only reads it, seals it, exports it or prunes it.

    ASRUN_ICONS = {"music": "🎵", "mic": "🎙", "jingle": "🔔",
                   "ad": "📢", "anthem": "🇹🇭", "stream": "📡",
                   "relay": "🔗"}

    def _build_asrun_tab(self) -> None:
        tab = self.asrun_frame

        hdr = ctk.CTkFrame(tab, fg_color="transparent")
        hdr.pack(fill="x", padx=6, pady=(8, 2))
        ctk.CTkLabel(hdr, text="🧾 As-run Log",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        self.asrun_switch_var = ctk.BooleanVar(value=self.asrun.enabled)
        self.asrun_switch = ctk.CTkSwitch(
            hdr, text="Record", variable=self.asrun_switch_var,
            width=90, font=ctk.CTkFont(size=10),
            command=self._asrun_toggle_enabled)
        self.asrun_switch.pack(side="right")
        ToolTip(self.asrun_switch, text="As-run recording",
                description="Record what ACTUALLY aired: append-only, one "
                            "file per day, sealed with a hash chain. Off by "
                            "default; with it off nothing is written.")

        ctk.CTkLabel(tab, text="Records what ACTUALLY aired — append-only,\n"
                               "cannot be edited afterwards (hash chain verifies it)",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     wraplength=330, justify="left").pack(anchor="w", padx=6)

        nav = ctk.CTkFrame(tab, fg_color="transparent")
        nav.pack(fill="x", padx=4, pady=(6, 2))
        prev_btn = ctk.CTkButton(nav, text="◀", width=28, height=24,
                                 fg_color=COLORS["bg_hover"],
                                 hover_color=COLORS["border"],
                                 corner_radius=6,
                                 font=ctk.CTkFont(size=11),
                                 command=lambda: self._asrun_step_day(-1))
        prev_btn.pack(side="left")
        ToolTip(prev_btn, text="Previous day",
                description="Step back one day through the as-run log. Days "
                            "with no log show an empty table.")
        self.asrun_day_label = ctk.CTkLabel(
            nav, text="—", width=118, font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text"])
        self.asrun_day_label.pack(side="left", padx=2)
        next_btn = ctk.CTkButton(nav, text="▶", width=28, height=24,
                                 fg_color=COLORS["bg_hover"],
                                 hover_color=COLORS["border"],
                                 corner_radius=6,
                                 font=ctk.CTkFont(size=11),
                                 command=lambda: self._asrun_step_day(1))
        next_btn.pack(side="left")
        ToolTip(next_btn, text="Next day",
                description="Step forward one day. It stops at today — a day "
                            "that has not aired yet has nothing to show.")
        today_btn = ctk.CTkButton(nav, text="Today", width=52, height=24,
                                  fg_color=COLORS["bg_hover"],
                                  hover_color=COLORS["border"],
                                  corner_radius=6,
                                  font=ctk.CTkFont(size=9),
                                  command=self._asrun_goto_today)
        today_btn.pack(side="left", padx=4)
        ToolTip(today_btn, text="Jump to today",
                description="Back to the day that is still airing — the only "
                            "day whose last item can still be open.")

        self.asrun_summary_label = ctk.CTkLabel(
            tab, text="", font=ctk.CTkFont(size=10), wraplength=330,
            justify="left", text_color=COLORS["text2"])
        self.asrun_summary_label.pack(anchor="w", padx=6, pady=(2, 2))

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.pack(fill="x", padx=4, pady=(0, 2))
        # Six buttons have to share the sidebar width, so each one is a little
        # narrower than the old five — every one of them explains itself on
        # hover instead of stealing more room from the table.
        for text, command, color, title, description in (
            ("🔄 Refresh", self._asrun_refresh_now, COLORS["bg_hover"],
             "Reload the list",
             "Re-read the selected day right now and redraw every row. The tab "
             "already polls while it is open — this is the manual pull."),
            ("🔒 Seal", self._asrun_seal_day, COLORS["bg_hover"],
             "Seal this day",
             "Write the day's manifest (counts, aired seconds, file hash, chain "
             "state) so it can be proven later. Refused if the chain is broken."),
            ("🔒 All days", self._asrun_seal_all_days, COLORS["bg_hover"],
             "Seal every day",
             "Seal each earlier day that has no manifest yet. A day that was "
             "edited after sealing is refused, never sealed over."),
            ("✅ Verify", self._asrun_verify_now, COLORS["bg_hover"],
             "Verify this day",
             "Check every line against the hash chain and the sealed manifest, "
             "and list each problem: edited line, deleted row, truncated file."),
            ("📤 CSV", self._asrun_export_csv, COLORS["bg_hover"],
             "Export CSV",
             "Save the selected day as a UTF-8 CSV for Excel: what aired, when, "
             "how long and why it stopped."),
            ("🗑 Prune", self._asrun_prune, COLORS["red"],
             "Prune old days",
             "Delete log files older than the retention setting. Today is never "
             "deleted and the legal minimum still applies."),
        ):
            btn = ctk.CTkButton(actions, text=text, width=57, height=24,
                                fg_color=color, hover_color=COLORS["border"],
                                corner_radius=6, font=ctk.CTkFont(size=9),
                                command=command)
            btn.pack(side="left", padx=1)
            ToolTip(btn, text=title, description=description)
            if command == self._asrun_refresh_now:
                self.asrun_refresh_btn = btn

        keep_row = ctk.CTkFrame(tab, fg_color="transparent")
        keep_row.pack(fill="x", padx=6, pady=(0, 4))
        ctk.CTkLabel(keep_row, text="Keep (days):", font=ctk.CTkFont(size=10),
                     text_color=COLORS["text3"]).pack(side="left")
        self.asrun_keep_entry = ctk.CTkEntry(keep_row, width=58, height=24,
                                             font=ctk.CTkFont(size=10))
        self.asrun_keep_entry.pack(side="left", padx=3)
        self.asrun_keep_entry.insert(0, str(self.asrun_keep_days))
        ToolTip(self.asrun_keep_entry, text="Retention (days)",
                description="How many days of as-run logs to keep. Values "
                            "under the legal minimum are raised to it.")
        apply_btn = ctk.CTkButton(keep_row, text="Apply", width=38, height=24,
                                  fg_color=COLORS["bg_hover"],
                                  hover_color=COLORS["border"],
                                  corner_radius=6,
                                  font=ctk.CTkFont(size=9),
                                  command=self._asrun_apply_keep)
        apply_btn.pack(side="left")
        ToolTip(apply_btn, text="Save retention",
                description="Store the number of days in the preset. Nothing "
                            "is deleted until Prune is pressed.")
        ctk.CTkLabel(keep_row, text=f"(legal minimum {LEGAL_MIN_DAYS} days)",
                     font=ctk.CTkFont(size=9),
                     text_color=COLORS["text3"]).pack(side="left", padx=4)

        ctk.CTkFrame(tab, height=1, fg_color=COLORS["border"]).pack(
            fill="x", padx=6, pady=(2, 4))

        self.asrun_rows_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self.asrun_rows_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._asrun_last_count = None
        self._asrun_last_store = None

    # ── As-run tab helpers ──

    def _asrun_selected_day(self) -> str:
        from datetime import datetime as _dt
        return self._asrun_day or _dt.now().strftime("%Y-%m-%d")

    def _asrun_step_day(self, delta: int) -> None:
        from datetime import datetime as _dt, timedelta as _td
        try:
            current = _dt.strptime(self._asrun_selected_day(), "%Y-%m-%d")
        except ValueError:
            current = _dt.now()
        target = current + _td(days=delta)
        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if target > today:          # the future has no as-run record
            self._show_toast("⏭ That day is still in the future")
            return
        self._asrun_day = target.strftime("%Y-%m-%d")
        self._refresh_asrun(force=True)

    def _asrun_goto_today(self) -> None:
        self._asrun_day = ""
        self._refresh_asrun(force=True)

    def _refresh_asrun(self, force: bool = False) -> None:
        """Redraw the day table (cheap: only when the count moved)."""
        frame = getattr(self, "asrun_rows_frame", None)
        if frame is None:
            return
        try:
            from datetime import datetime as _dt
            day = self._asrun_selected_day()
            today = _dt.now().strftime("%Y-%m-%d")
            state = self.asrun.stats(day)
            # The DAY is part of the cache key: two days with the same item
            # count (both empty, say) must still be redrawn when navigating.
            store = (day, state["items"], state["lines"])
            if not force and store == self._asrun_last_store:
                return
            self._asrun_last_store = store

            self.asrun_day_label.configure(
                text=f"{day} (today)" if day == today else day)
            summary = self.asrun.summary_line(day)
            # Older days that were never sealed: nothing can be proven about
            # them, so the tab says so instead of looking quietly complete.
            gaps = self.asrun.gap_warning()
            if gaps:
                summary += f"\n{gaps}"
            if self.asrun.last_error:
                summary += f"\n⚠️ {self.asrun.last_error}"
                self.asrun_summary_label.configure(
                    text=summary, text_color=COLORS["red"])
            elif gaps:
                self.asrun_summary_label.configure(
                    text=summary, text_color=COLORS["orange"])
            else:
                self.asrun_summary_label.configure(
                    text=summary, text_color=COLORS["text2"])

            items = self.asrun.read_day(day)
            self._asrun_last_count = len(items)
            self._asrun_rebuild(frame, items)
        except Exception:
            logging.getLogger("freq.gui").exception("as-run tab refresh failed")

    # A card is four CTk widgets: ~14 ms to build and ~4 ms to destroy, measured
    # on a 90-item day (1.26 s + 0.42 s). Rebuilding a full broadcast day in one
    # go froze the window for over a second and a half, so the work is sliced
    # across `after(1)` ticks — a refresh never blocks the event loop for more
    # than a handful of cards, however long the day got.
    ASRUN_ROWS_PER_TICK = 10
    ASRUN_CLEAR_PER_TICK = 20

    def _asrun_rebuild(self, frame, items) -> None:
        """Redraw the day's cards in slices; a newer refresh cancels this one."""
        token = getattr(self, "_asrun_build_token", 0) + 1
        self._asrun_build_token = token
        pending = list(items)
        state = {"clearing": True}

        def step() -> None:
            if token != self._asrun_build_token:
                return
            try:
                if not frame.winfo_exists():
                    return
                if state["clearing"]:
                    old = frame.winfo_children()
                    if old:
                        for child in old[:self.ASRUN_CLEAR_PER_TICK]:
                            child.destroy()
                        self.after(1, step)
                        return
                    state["clearing"] = False
                    if not pending:
                        ctk.CTkLabel(
                            frame, text="No as-run entries for this day yet\n"
                                        "Turn on “Record” and start airing",
                            font=ctk.CTkFont(size=10),
                            text_color=COLORS["text3"],
                            justify="left").pack(anchor="w", padx=6, pady=12)
                        return
                for item in pending[:self.ASRUN_ROWS_PER_TICK]:
                    self._asrun_row(frame, item)
                del pending[:self.ASRUN_ROWS_PER_TICK]
                if pending:
                    self.after(1, step)
            except Exception:
                logging.getLogger("freq.gui").exception(
                    "as-run row build failed")

        step()

    def _asrun_row(self, parent, item: dict) -> None:
        row = ctk.CTkFrame(parent, fg_color=COLORS["bg_hover"], corner_radius=4)
        row.pack(fill="x", pady=1, padx=2)
        start = (item.get("ts_start") or "")[11:19] or "--:--:--"
        icon = self.ASRUN_ICONS.get(item.get("kind", ""), "•")
        title = item.get("title") or "?"
        if item.get("artist"):
            title = f"{item['artist']} — {title}"
        duration = item.get("actual_duration")
        if item.get("open"):
            status, color = "⏳ on air now", COLORS["orange"]
        else:
            status = END_REASON_LABELS.get(item.get("ended_why", ""),
                                            item.get("ended_why") or "—")
            color = {
                "natural": COLORS["green"], "missing": COLORS["red"],
                "error": COLORS["red"], "stopped": COLORS["text3"],
            }.get(item.get("ended_why", ""), COLORS["orange"])
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(3, 0))
        ctk.CTkLabel(top, text=f"{start} {icon}", width=76, anchor="w",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text3"]).pack(side="left")
        ctk.CTkLabel(top, text=title, anchor="w", justify="left",
                     font=ctk.CTkFont(size=10), text_color=COLORS["text"],
                     wraplength=196).pack(side="left", fill="x", expand=True)
        bottom = ctk.CTkFrame(row, fg_color="transparent")
        bottom.pack(fill="x", padx=4, pady=(0, 3))
        dur_text = f"{duration:.0f} s" if isinstance(duration, (int, float)) else "?"
        ctk.CTkLabel(bottom, text=f"{item.get('kind', '')} · {dur_text}",
                     width=76, anchor="w", font=ctk.CTkFont(size=9),
                     text_color=COLORS["text3"]).pack(side="left")
        ctk.CTkLabel(bottom, text=status, anchor="w", justify="left",
                     font=ctk.CTkFont(size=9), text_color=color,
                     wraplength=196).pack(side="left", fill="x", expand=True)

    def _asrun_refresh_now(self) -> None:
        """Re-read the selected day and redraw it — the manual pull.

        The tab already polls while it is visible; this is for the moment the
        operator wants the list brought up to date immediately (a jingle just
        ended, the file was opened by another program, …).
        """
        self._asrun_last_store = None
        self._asrun_last_count = None
        self._refresh_asrun(force=True)
        self._show_toast(f"🔄 Reloaded {self._asrun_selected_day()}")

    def _asrun_toggle_enabled(self) -> None:
        on = bool(self.asrun_switch_var.get())
        self.asrun.configure(enabled=on)
        self.asrun_switch.configure(text="Record" if on else "Off")
        self._mark_settings_dirty()
        if on:
            self._show_toast("🧾 As-run recording on → "
                             f"{self.asrun.folder}")
        else:
            self.asrun.end_active("stopped")
            self._show_toast("🧾 As-run recording off")
        self._refresh_asrun(force=True)

    def _asrun_apply_keep(self) -> None:
        try:
            days = int(self.asrun_keep_entry.get())
        except (TypeError, ValueError):
            days = DEFAULT_KEEP_DAYS
        if days < LEGAL_MIN_DAYS:
            self._show_toast(f"⚠️ Keep at least {LEGAL_MIN_DAYS} days")
            days = LEGAL_MIN_DAYS
        self.asrun_keep_days = days
        self.asrun.configure(keep_days=days)
        self.asrun_keep_entry.delete(0, "end")
        self.asrun_keep_entry.insert(0, str(days))
        self._mark_settings_dirty()
        self._show_toast(f"🗓 Keeping as-run for {days} days")

    def _asrun_seal_day(self) -> None:
        day = self._asrun_selected_day()
        manifest = self.asrun.write_manifest(day)
        if manifest is None:
            self._show_toast(f"⚠️ No log file for {day} to seal")
            return
        self._show_toast(f"🔒 Sealed {day} ({manifest['items']} items · "
                         f"chain {'intact' if manifest['chain_ok'] else 'broken'})")
        self._refresh_asrun(force=True)

    def _asrun_auto_seal(self) -> None:
        """Seal earlier unsealed days once a day, without being asked.

        Runs from the As-run poller, so a station that is left open for days
        still gets yesterday's manifest at rollover — and the first tick
        after start also closes any gap left by a crash or a powered-off
        machine. Refusals (a day whose chain is broken) are logged and left
        alone; they are never sealed over.
        """
        if not self.asrun.enabled:
            return
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        if getattr(self, "_asrun_seal_poll_day", "") == today:
            return
        self._asrun_seal_poll_day = today
        try:
            report = self.asrun.seal_all(today=today)
        except Exception:
            logging.getLogger("freq.asrun").exception("auto-seal failed")
            return
        if report["sealed"]:
            logging.getLogger("freq.asrun").info(
                "auto-sealed %s", ", ".join(report["sealed"]))
        if report["refused"]:
            logging.getLogger("freq.asrun").warning(
                "auto-seal refused %s", report["refused"])

    def _asrun_seal_all_days(self) -> None:
        """Seal every earlier day that has no manifest yet."""
        try:
            report = self.asrun.seal_all()
        except Exception:
            logging.getLogger("freq.gui").exception("as-run seal-all failed")
            self._show_toast("⚠️ Sealing all days failed")
            return
        sealed = report.get("sealed") or []
        refused = report.get("refused") or []
        if refused:
            # Never seal over a modified day silently: the day stays as it is
            # and the operator is told which one and why.
            first = refused[0]
            self._show_toast(
                f"⚠️ Could not seal {len(refused)} day(s) — {first['day']}: "
                f"{first.get('reason') or ''}"[:160], duration=6000)
        elif sealed:
            self._show_toast(f"🔒 Sealed {len(sealed)} day(s): "
                             f"{', '.join(sealed[:3])}"
                             + (" …" if len(sealed) > 3 else ""))
        else:
            self._show_toast("🔒 Every day is already sealed")
        self._refresh_asrun(force=True)

    def _asrun_verify_now(self) -> None:
        day = self._asrun_selected_day()
        result = self.asrun.verify(day)
        if not result["exists"]:
            messagebox.showinfo("As-run", f"{day}: no log file yet")
            return
        if result["ok"]:
            lines = [
                f"Day: {day}",
                f"Lines: {result['lines']} · Items: {result['items']}",
                f"manifest: {result['manifest']}",
                "Result: the hash chain is intact, no line was edited",
            ]
            messagebox.showinfo("✅ As-run verify", "\n".join(lines))
            return
        details = "\n".join(f"• {e}" for e in result["errors"][:12])
        messagebox.showwarning(
            "⚠️ As-run verify",
            f"Day: {day}\n{len(result['errors'])} problem(s) found:\n{details}")

    def _asrun_export_csv(self) -> None:
        day = self._asrun_selected_day()
        if not self.asrun.stats(day)["exists"]:
            self._show_toast(f"⚠️ No log for {day}")
            return
        path = filedialog.asksaveasfilename(
            title="Export as-run", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=f"asrun-{day}.csv")
        if not path:
            return
        written = self.asrun.export_csv(day, path)
        if written:
            self._show_toast(f"📤 {Path(written).name} "
                             f"({len(self.asrun.read_day(day))} items)")
        else:
            self._show_toast(f"⚠️ Export failed: {self.asrun.last_error}")

    def _asrun_prune(self) -> None:
        days = self.asrun.keep_days
        if not messagebox.askyesno(
                "Prune as-run",
                f"Delete as-run entries older than {days} days?\n"
                "(today is never deleted · a sealed day loses its manifest too)"):
            return
        try:
            removed = self.asrun.prune(days)
        except ValueError as err:
            messagebox.showerror("Prune as-run", str(err))
            return
        self._show_toast(f"🗑 Deleted {len(removed)} day(s)" if removed else "🗑 Nothing to delete")

    def _poll_asrun(self) -> None:
        """Timer tick: keep the visible day table current. Cheap when hidden.

        Also the place where an idle station seals itself: once a day (on
        rollover, and on the first tick after start) any earlier day without
        a manifest is sealed, even when nobody is looking at the tab. A day
        is never sealed over a broken chain — ``seal_all`` refuses and the
        reason is logged.
        """
        self._asrun_auto_seal()
        if getattr(self, "_current_sidebar_tab", "") != "As-run":
            return
        if not self.asrun.enabled and self._asrun_last_count is None:
            return
        self._refresh_asrun()

    # ── As-run write hooks (called from the playback paths) ──

    def _asrun_device(self) -> str:
        try:
            return self.device_var.get()
        except Exception:
            return ""

    def _asrun_recording_link(self) -> Optional[dict]:
        """Where this item starts inside the air-check file, when recording."""
        try:
            rec = getattr(self, "aircheck_recorder", None)
            if rec is not None and rec.recording and rec.path:
                return {"file": rec.path, "offset": round(float(rec.elapsed()), 3)}
        except Exception:
            pass
        return None

    def _asrun_begin(self, kind: str, song=None, *, title: str = "",
                     artist: str = "", duration=None, path: str = "",
                     url: str = "", source_type: str = "", channel: str = "",
                     reason: str = "", song_id: str = "",
                     extra: Optional[dict] = None) -> Optional[str]:
        """Open an as-run item. Never raises — a log failure must not stop air."""
        if not self.asrun.enabled:
            return None
        try:
            if song is not None:
                title = title or getattr(song, "title", "") or ""
                artist = artist or getattr(song, "artist", "") or ""
                if duration is None:
                    duration = getattr(song, "duration", None)
                song_id = song_id or getattr(song, "id", "") or ""
                if not path:
                    path = getattr(song, "file_path", "") or ""
                if not url:
                    url = getattr(song, "url", "") or ""
                if not source_type:
                    source_type = ("youtube"
                                   if getattr(song, "source", "") == "youtube"
                                   else "file")
            return self.asrun.begin(
                kind, title=title, artist=artist, song_id=song_id,
                duration=duration, path=path, url=url,
                source_type=source_type, channel=channel or kind,
                device=self._asrun_device(), reason=reason,
                recording=self._asrun_recording_link(), extra=extra)
        except Exception:
            logging.getLogger("freq.asrun").exception("as-run begin failed")
            return None

    def _asrun_end_active(self, ended_why: str = "natural",
                          kind: Optional[str] = None) -> None:
        """Close the open as-run item (optionally only when of ``kind``).

        Runs even when the switch has since been turned off: an item that
        opened while logging was on must be closed, not left dangling.
        """
        if not self.asrun.enabled and self.asrun.active_id is None:
            return
        try:
            self.asrun.end_active(ended_why, kind=kind)
        except Exception:
            logging.getLogger("freq.asrun").exception("as-run end failed")

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

        # Marquee for long titles: title past the widget edge just clips —
        # 460 px per paint tick. Pauses while paused.
        self.np_title = ctk.CTkLabel(info, text="No song playing",
                                      font=ctk.CTkFont(size=16, weight="bold"),
                                      text_color=COLORS["text"], anchor="w")
        self.np_title.pack(fill="x")
        self._start_title_marquee(self.np_title)

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
        # Conversion timing: yt-dlp's FFmpeg postprocessor reports no
        # percentage, so the rundown estimates a finish time from how long
        # conversions in THIS session actually took (seconds per audio second).
        self._convert_started: Optional[float] = None
        self._convert_song_secs = 0.0
        self._convert_ratios: list = []
        self._dl_plan_at = 0.0
        self._jingle_playing = False
        self._jingle_was_playing = False
        self._ad_playing = False
        # A break that was started by a "break then stop" end mark: when the
        # break finishes, the rundown ends instead of advancing.
        self._stop_after_break = False
        self._active_source_channel = "music"   # which mixer channel feeds audio_player now
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
        self._auto_gain_enabled = False
        self._auto_gain_target = -14.0
        self.editor = AudioEditor()
        self._refresh_pending = False
        self._settings_dirty = False
        self._restoring_settings = False
        self._pre_duck_volume = 80.0
        self._anthem_playing = False
        self._anthem_was_playing = False
        self._anthem_resume_token = 0
        self._anthem_resume_cancel = threading.Event()
        # WASAPI engine route: endpoint id when playback bypasses the OS
        # default via the C++ render engine (None = classic pygame/default).
        self._wasapi_device: Optional[str] = None

        # Initialize new features
        self.visualizer = None  # Lazy init
        self.focus_mode = None  # Focus Mode (lazy init on first toggle)
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
            confirm=lambda ok: self.after(0, lambda: self._anthem_confirm(ok)),
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

        self.btn_auto_gain = ctk.CTkButton(mode_row, text="📢", width=36, height=30, corner_radius=8,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_auto_gain)
        self.btn_auto_gain.pack(side="left", padx=4)
        ToolTip(self.btn_auto_gain, text="Loudness Auto-Gain",
                description="Play every track at the same loudness (-14 LUFS)")

        self.btn_auto_cue = ctk.CTkButton(mode_row, text="⏱", width=36, height=30, corner_radius=8,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_auto_cue)
        self.btn_auto_cue.pack(side="left", padx=4)
        ToolTip(self.btn_auto_cue, text="Auto-cue", description="Skip silence at the start of each song")

        # Right: Device selector + Position
        right_frame = ctk.CTkFrame(inner, fg_color="transparent")
        right_frame.pack(side="right")

        # Two UNIFORM output rows (device dropdown + its own loudness):
        # row 1 = main output, row 2 = Dual Output mirror. Same widget
        # widths in both rows so the columns line up instead of the old
        # ragged pile. The song position (xx / xx) sits alone underneath,
        # right-aligned.

        # ── Row 1: main output ──
        dev_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        dev_frame.pack(anchor="e", pady=(0, 3))

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

        self._muted = False
        self._saved_vol = 80.0
        self.btn_mute = ctk.CTkButton(
            dev_frame, text="🔊", width=28, height=26,
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=14),
            command=self._toggle_mute,
        )
        self.btn_mute.pack(side="left", padx=(6, 2))
        ToolTip(self.btn_mute, text="Mute / Unmute", description="Toggle sound on/off")

        self.volume_var = ctk.DoubleVar(value=80.0)
        self.volume_slider = ctk.CTkSlider(
            dev_frame, from_=0, to=100,
            variable=self.volume_var,
            width=120, height=14,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            command=self._on_volume_change,
        )
        self.volume_slider.set(80.0)
        self.volume_slider.pack(side="left")
        self._bind_wheel_volume(self.volume_slider, self._on_volume_change)

        self.volume_label = ctk.CTkLabel(dev_frame, text="80%",
                                          font=ctk.CTkFont(size=10),
                                          text_color=COLORS["text3"], width=36)
        self.volume_label.pack(side="left", padx=(4, 0))

        # ── Dual Output (mirror mix to a second speaker) — row 2, same shape ──
        # Default OFF = identical to the legacy single-output path.
        self._dual_output = DualOutputMirror()
        # Aliases kept for the original smoke-suite contract.
        self.dual_output = self._dual_output
        dual_frame = ctk.CTkFrame(right_frame, fg_color="transparent",
                                  width=dev_frame.winfo_reqwidth())
        dual_frame.pack(anchor="e", pady=(0, 3))

        spacer = ctk.CTkLabel(dual_frame, text="", width=18,
                              font=ctk.CTkFont(size=11))
        spacer.pack(side="left", padx=(0, 4))

        self.dual_device_var = ctk.StringVar(value="Off")
        self.dual_var = self.dual_device_var  # smoke-suite alias
        self.dual_menu = ctk.CTkOptionMenu(
            dual_frame, variable=self.dual_device_var,
            values=["Off"],
            width=180, height=26,
            fg_color=COLORS["bg_hover"],
            button_color=COLORS["border"],
            button_hover_color=COLORS["text3"],
            dropdown_fg_color=COLORS["bg_card"],
            dropdown_hover_color=COLORS["bg_hover"],
            font=ctk.CTkFont(size=10),
            command=self._on_dual_selected,
        )
        self.dual_menu.pack(side="left")
        ToolTip(self.dual_menu, text="Dual Output",
                description="Mirror the whole broadcast mix to a second speaker")

        ctk.CTkLabel(dual_frame, text="🔄", width=26, height=26,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(4, 0))

        self.dual_mute = ctk.CTkLabel(dual_frame, text="🔊",
                                      font=ctk.CTkFont(size=14), width=28)
        self.dual_mute.pack(side="left", padx=(6, 2))

        self.dual_vol_var = ctk.DoubleVar(value=100.0)
        self.dual_vol_slider = ctk.CTkSlider(
            dual_frame, from_=0, to=200,
            variable=self.dual_vol_var,
            width=120, height=14,
            fg_color=COLORS["border"],
            progress_color=COLORS["accent"],
            button_color=COLORS["accent"],
            command=self._on_dual_volume,
        )
        self.dual_vol_slider.pack(side="left")
        self._bind_wheel_volume(self.dual_vol_slider, self._on_dual_volume,
                                high=200.0)

        self.dual_vol_label = ctk.CTkLabel(dual_frame, text="100%",
                                           font=ctk.CTkFont(size=10),
                                           text_color=COLORS["text3"], width=36)
        self.dual_vol_label.pack(side="left", padx=(4, 0))

        # Populate dual device values (after refresh_device_list fills the list)
        self.dual_device_menu = self.dual_menu  # smoke-suite alias
        self._refresh_dual_device_list()

        # Position label — bottom-right, alone on its own row
        self.np_position = ctk.CTkLabel(right_frame, text="-- / --",
                                         font=ctk.CTkFont(size=12),
                                         text_color=COLORS["text3"])
        self.np_position.pack(anchor="e", pady=(6, 0))

        # Waveform display — above progress bar
        self.waveform = WaveformWidget(inner, width=400, height=40,
                                        bg=COLORS["bg_card"])
        self.waveform.pack(side="bottom", fill="x", padx=(0, 0), pady=(2, 0))
        self.waveform.set_on_seek(self._on_waveform_seek)

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
            button_hover_color=COLORS["accent_hover"],
        )
        self.np_progress.set(0)
        self.np_progress.pack(side="left", fill="x", expand=True, padx=8)

        self.np_time_total = ctk.CTkLabel(prog_frame, text="0:00",
                                           font=ctk.CTkFont(size=10),
                                           text_color=COLORS["text3"], width=50)
        self.np_time_total.pack(side="right")

    # ═══════════════════════════════════════
    # Queue Rendering (virtualized: one canvas, visible rows only)
    # ═══════════════════════════════════════

    def _refresh_queue(self, keep_scroll: bool = False) -> None:
        """Rebuild the queue MODEL, then draw only the visible rows.

        Safe to call from any thread: a worker (LAN remote, broadcast timer)
        is re-dispatched onto the UI thread instead of touching Tk directly —
        widgets are only ever built from the main thread.

        The list used to be one widget tree per song (CTkFrame first, plain
        Tk frames after): 400 songs meant 3,200 widgets and ~6 s of layout
        before the queue was usable. It is now ONE canvas as tall as the
        whole list — which keeps CTkScrollableFrame's native scrolling and
        scrollbar untouched — and only the rows inside the viewport are
        materialised as canvas items. A refresh therefore costs the same for
        6 songs and for 4,000.
        """
        if threading.current_thread() is not threading.main_thread():
            # Tk is main-thread only: reaching a widget from a worker is a
            # hard crash, not an exception. Re-dispatch and return.
            try:
                self.after(0, lambda: self._refresh_queue(keep_scroll))
            except Exception:
                pass
            return
        self._queue_view = []
        self._queue_rows = {}
        self._queue_items = {}
        self._queue_pos_by_index = {}
        self._queue_drawn = None
        self._queue_hover_pos = None
        self._queue_times = []
        self._queue_events = []
        self._queue_wait = 0.0
        self._queue_wait_kind = ""
        self._queue_clock_minute = None

        queue = self.rq.queue

        # Always pair rows with their ORIGINAL queue index — click
        # handlers and playback speak full-queue positions, so a filtered
        # view must not renumber rows (it used to select/play the WRONG
        # song whenever a search was active). Unconditional pairing also
        # means downstream code can never meet a bare Song again.
        queue = list(enumerate(self.rq.queue))
        if self._queue_filter:
            # `title`/`artist` can be None on hand-edited data: str() the read
            # instead of calling .lower() on whatever the Song happens to hold.
            needle = self._queue_filter
            queue = [(orig_i, s) for orig_i, s in queue
                     if needle in str(getattr(s, "title", "") or "").lower()
                     or needle in str(getattr(s, "artist", "") or "").lower()]

        total_dur = sum(_safe_seconds(getattr(s, "duration", 0))
                        for _i, s in queue if not self.rq.is_note(s))
        song_count = sum(1 for _i, s in queue if not self.rq.is_note(s))

        if not queue:
            self._hide_queue_canvas()
            self._clear_queue_children()
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
            # An empty queue can still be waiting on a transfer — keep the
            # rundown truthful (and drop it when nothing is happening).
            self._refresh_queue_next_label()
            self._queue_selected = None
            self._update_queue_filter_btn()
            return

        canvas = self._ensure_queue_canvas()
        self._clear_queue_children()     # the empty-state placeholder, if any
        if not canvas.winfo_manager():
            canvas.pack(fill="x")

        self._queue_view = queue
        self._queue_rows = {orig_i: QueueRowHandle(orig_i, s)
                            for orig_i, s in queue}
        self._queue_pos_by_index = {orig_i: pos
                                    for pos, (orig_i, _s) in enumerate(queue)}
        # Start/stop clock stamps for the whole view: computed once per
        # rebuild, then re-stamped when the minute changes (never per row).
        self._refresh_queue_clock()
        self._queue_clock_minute = int(time.time() // 60)
        self._refresh_queue_next_label()
        self.queue_stats_label.configure(
            text=f"{song_count} songs · {self._fmt_dur(total_dur)}")

        # Full-height canvas: CTk's outer canvas scrolls it, we only draw the
        # slice that is on screen. Height must follow the row count exactly,
        # or the scrollbar and the click→row mapping drift apart.
        canvas.configure(height=len(queue) * QUEUE_ROW_H)
        if not keep_scroll:
            parent = getattr(self.queue_frame, "_parent_canvas", None)
            if parent is not None:
                try:
                    parent.yview_moveto(0.0)     # a rebuilt list starts at top
                except Exception:
                    pass
        self._queue_marked_index = self.rq.current_index
        self._draw_queue_window(force=True)
        self._start_queue_poll()
        self._update_queue_filter_btn()

    def _ensure_queue_canvas(self):
        """The single canvas that hosts every queue row (created once)."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is not None and canvas.winfo_exists():
            return canvas
        canvas = tk.Canvas(self.queue_frame, bg=COLORS["bg_card"],
                           highlightthickness=0, bd=0, height=1,
                           yscrollincrement=1)
        canvas.pack(fill="x")
        canvas.bind("<Button-1>", self._on_queue_canvas_press)
        canvas.bind("<Double-Button-1>", self._on_queue_canvas_double)
        canvas.bind("<Button-3>", self._on_queue_canvas_context)
        canvas.bind("<Motion>", self._on_queue_canvas_motion)
        canvas.bind("<Leave>", self._on_queue_canvas_leave)
        canvas.bind("<Configure>",
                    lambda e: self._draw_queue_window(force=True))
        # Keyboard navigation is bound ON THE CANVAS: a click on a row moves
        # focus here, and every handler returns "break" so the window-level
        # Up/Down volume shortcuts never fire on the same keypress.
        for sequence in ("<Up>", "<Down>", "<Prior>", "<Next>",
                         "<Home>", "<End>"):
            canvas.bind(sequence, self._on_queue_canvas_key)
        canvas.bind("<Shift-Up>",
                    lambda e: self._queue_nav_rows(-1, extend=True))
        canvas.bind("<Shift-Down>",
                    lambda e: self._queue_nav_rows(1, extend=True))
        canvas.bind("<Return>", self._on_queue_canvas_enter)
        canvas.bind("<KP_Enter>", self._on_queue_canvas_enter)
        self._queue_canvas = canvas
        return canvas

    def _clear_queue_children(self) -> None:
        """Drop the empty-state placeholder; the list canvas itself is kept."""
        canvas = getattr(self, "_queue_canvas", None)
        for w in self.queue_frame.winfo_children():
            if w is not canvas:
                w.destroy()

    def _hide_queue_canvas(self) -> None:
        """Drop the list canvas while the empty-state placeholder is shown."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        canvas.delete("all")
        canvas.configure(height=1)
        canvas.pack_forget()

    # ── Geometry / viewport ──────────────────────────────────────

    def _queue_pos_at(self, event):
        """Row position (in the view) under a mouse event; None on empty space."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None:
            return None
        try:
            y = canvas.canvasy(event.y)
        except Exception:
            y = event.y
        pos = int(y // QUEUE_ROW_H)
        if not 0 <= pos < len(self._queue_view):
            return None
        # The 15 px gap belongs to no card: a click there is a click on the
        # playlist background (deselect), not on the card below it.
        return None if (y - pos * QUEUE_ROW_H) >= QUEUE_CARD_H else pos

    def _queue_visible_top(self) -> int:
        """First row in the CTk viewport, read from the scroll fraction.

        The canvas is exactly ``rows × QUEUE_ROW_H`` tall, so the top
        fraction of the outer canvas * row count is the top row index —
        no per-row widget lookups, no cache to keep in sync with scrolling.
        """
        n = len(self._queue_view)
        parent = getattr(self.queue_frame, "_parent_canvas", None)
        if parent is None or n == 0:
            return 0
        try:
            fraction = parent.yview()[0]
        except Exception:
            return 0
        return max(0, min(n - 1, int(round(fraction * n))))

    def _queue_viewport_rows(self) -> int:
        """Rows that fit on screen at once."""
        parent = getattr(self.queue_frame, "_parent_canvas", None)
        height = 0
        if parent is not None:
            try:
                height = parent.winfo_height()
            except Exception:
                height = 0
        if height <= 1:
            height = 420          # not laid out yet — a sane default
        return max(1, height // QUEUE_ROW_H)

    def _queue_canvas_width(self) -> int:
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None:
            return 340
        try:
            width = canvas.winfo_width()
        except Exception:
            width = 1
        return width if width > 1 else 340      # pre-layout fallback

    # ── Drawing ──────────────────────────────────────────────────

    def _draw_queue_window(self, force: bool = False) -> None:
        """Draw the rows inside the viewport — the only place rows are drawn."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        n = len(self._queue_view)
        if n == 0:
            canvas.delete("all")
            self._queue_items = {}
            self._queue_drawn = None
            return
        top = self._queue_visible_top()
        first = max(0, top - QUEUE_OVERSCAN)
        last = min(n, top + self._queue_viewport_rows() + QUEUE_OVERSCAN)
        if not force and (first, last) == self._queue_drawn:
            return
        canvas.delete("all")
        self._queue_items = {}
        for pos in range(first, last):
            try:
                self._queue_items[pos] = self._draw_queue_row(canvas, pos)
            except Exception:
                # One unreadable song must not blank the whole list (a
                # hand-edited .freq can hold a duration=None entry). The
                # broken card is skipped, logged, and the rest still draw.
                logging.getLogger("freq.queue").exception(
                    "queue card %d could not be drawn", pos)
        self._queue_drawn = (first, last)

    # ── Scheduled start / stop stamps ────────────────────────────

    @staticmethod
    def _clock_time(ts: float) -> str:
        """Wall-clock HH:MM:SS (24 h, local) for a scheduled moment.

        Seconds, not minutes: a queue row's window starts whenever the song
        before it ends, and at 4-minute songs the minute hand alone cannot
        tell two adjacent rows apart (`09:03–09:07` twice in a row reads as a
        bug). The panel is a broadcast rundown — operators line it up against
        a wall clock, so the column matches one.
        """
        try:
            return time.strftime("%H:%M:%S", time.localtime(ts))
        except (OverflowError, OSError, ValueError):
            return "--:--:--"

    def _probe_seconds(self, path: str) -> float:
        """Length of an audio file, read locally and cached (never plays it).

        Jingles and ads are files on disk and the timeline has to know how
        long they are BEFORE they air. mutagen reads the header only — no
        decode, no device. A missing/unreadable file counts as 0 s, so the
        schedule never invents air time it cannot justify.
        """
        if not path:
            return 0.0
        cache = getattr(self, "_probe_cache", None)
        if cache is None:
            cache = self._probe_cache = {}
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            cache[path] = (None, 0.0)
            return 0.0
        hit = cache.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
        secs = 0.0
        try:
            from mutagen import File as _MutagenFile
            mf = _MutagenFile(path)
            if mf is not None and getattr(mf, "info", None) is not None:
                secs = _safe_seconds(mf.info.length)
        except Exception:
            secs = 0.0
        cache[path] = (stamp, secs)
        return secs

    def _queue_insert_plan(self, steps: int):
        """Jingle / ad breaks the next `steps` songs will trigger.

        Returns `(cumulative, markers)`: `cumulative[k]` is the inserted
        seconds after k finished songs, `markers` is `(step, seconds,
        kind)` per insert so the header can say when the next one airs.
        The counters start where the live ones stand and the precedence
        mirrors `_on_track_finished` exactly: an ad ticks first and, when it
        fires, the handler returns before the jingle block — so a song that
        carries an ad never advances the jingle counter.
        """
        steps = max(0, int(steps))
        cumulative = [0.0] * (steps + 1)
        markers: list = []
        jingle_on = bool(getattr(self.jingle, "enabled", False)
                         and self.jingle.jingle_file)
        ads = list(getattr(self.commercial, "ads", []) or [])
        ad_on = bool(getattr(self.commercial, "enabled", False) and ads)
        jingle_len = (self._probe_seconds(self.jingle.jingle_file)
                      if jingle_on else 0.0)
        jingle_int = max(1, int(getattr(self.jingle, "interval", 5) or 5))
        ad_int = max(1, int(getattr(self.commercial, "interval", 10) or 10))
        j_count = int(getattr(self.jingle, "song_counter", 0) or 0)
        a_count = int(getattr(self.commercial, "song_counter", 0) or 0)
        ad_idx = int(getattr(self.commercial, "current_ad_index", 0) or 0)
        total = 0.0
        for step in range(1, steps + 1):
            fired = False
            if ad_on:
                a_count += 1
                if a_count >= ad_int:
                    a_count = 0
                    secs = self._probe_seconds(ads[ad_idx % len(ads)])
                    ad_idx += 1
                    fired = True
                    total += secs
                    markers.append((step, secs, "ad"))
            if not fired and jingle_on:
                j_count += 1
                if j_count >= jingle_int:
                    j_count = 0
                    total += jingle_len
                    markers.append((step, jingle_len, "jingle"))
            cumulative[step] = total
        return cumulative, markers

    def _anthem_air_seconds(self) -> float:
        """Air time one anthem costs: pause + fade-in + length + fade-out.

        An anthem with no known length returns 0 — its air time is then not
        guessed (and it is reported as `length ?` instead of a fake number).
        """
        try:
            cfg = self.anthem.config
            length = _safe_seconds(getattr(cfg, "duration", 0.0))
            if length <= 0:
                length = self._probe_seconds(getattr(cfg, "anthem_file", ""))
            if length <= 0:
                return 0.0
            return (length + _safe_seconds(getattr(cfg, "fade_in", 0.0))
                    + _safe_seconds(getattr(cfg, "fade_out", 0.0))
                    + _safe_seconds(getattr(cfg, "pause_before", 0.0)))
        except Exception:
            return 0.0

    def _anthem_schedule(self, after_ts: float, days: int = 3) -> list:
        """Every (moment, air-seconds) at/after `after_ts` for `days` days.

        Fixed wall-clock events, so they are generated from the CONFIG, not
        from the queue: an anthem whose moment lands in the middle of a song
        still has to be placed in the rundown. Disabled / unmeasured anthems
        return nothing at all.
        """
        import datetime
        try:
            cfg = self.anthem.config
            if not cfg.enabled:
                return []
            air = self._anthem_air_seconds()
            if air <= 0:
                return []
            wanted = [str(d).lower()[:3] for d in (getattr(cfg, "days", []) or [])]
            out = []
            day = datetime.datetime.fromtimestamp(after_ts)
            for offset in range(0, max(1, int(days))):
                d = day + datetime.timedelta(days=offset)
                if wanted and d.strftime("%a").lower()[:3] not in wanted:
                    continue
                for key in ("morning_time", "evening_time"):
                    raw = str(getattr(cfg, key, "") or "")
                    try:
                        hour, minute = (int(p) for p in raw.split(":")[:2])
                    except (ValueError, TypeError):
                        continue
                    moment = d.replace(hour=hour, minute=minute,
                                       second=0, microsecond=0).timestamp()
                    if moment > after_ts - 1:
                        out.append((moment, air))
            return sorted(out)
        except Exception:
            return []

    def _anthem_folds(self, first_ts: float, last_ts: float) -> list:
        """(moment, seconds) for every anthem airing inside the window."""
        return [(moment, air) for moment, air in self._anthem_schedule(first_ts)
                if moment <= last_ts + 1]

    def _convert_estimate(self) -> float:
        """Estimated seconds a conversion takes, measured from past runs.

        yt-dlp's FFmpeg postprocessor reports no percentage at all, so the
        only honest source is what conversions in THIS session actually took:
        seconds of conversion per second of audio. No measurement yet → 0,
        and the rundown says so instead of inventing a finish time.
        """
        try:
            ratios = sorted(float(r) for r in
                            (getattr(self, "_convert_ratios", None) or []))
            secs = float(getattr(self, "_convert_song_secs", 0.0) or 0.0)
            if not ratios or secs <= 0:
                return 0.0
            return max(0.0, ratios[len(ratios) // 2] * secs)
        except Exception:
            return 0.0

    def _download_stall_seconds(self) -> float:
        """Seconds left of the transfer the queue is waiting on (0 = none).

        A song still coming down the wire cannot start on time, so the row it
        belongs to — and every row after it — slides by this. A download rate
        only exists after a few seconds of progress; a CONVERSION has no
        percentage from yt-dlp at all, so it is estimated from the measured
        speed of earlier conversions (0 until one has been measured).
        """
        try:
            state = getattr(self, "_dl_state", None) or {}
            if not self._download_mode:
                return 0.0
            status = state.get("status")
            elapsed = time.time() - float(state.get("started") or 0.0)
            if status == "processing":
                est = self._convert_estimate()
                return max(0.0, est - elapsed) if est > 0 else 0.0
            if status != "downloading":
                return 0.0
            pct = float(state.get("pct") or 0.0)
            if pct < 1.0 or elapsed < 2.0:
                return 0.0
            return max(0.0, (100.0 - pct) / (pct / elapsed))
        except Exception:
            return 0.0

    def _finish_convert_measure(self) -> None:
        """Record how long the conversion that just ended really took.

        Stored as seconds of conversion per second of audio (median of the
        last five), which is what `_convert_estimate` extrapolates from for a
        different song. Nothing is recorded for cache hits or failed runs.
        """
        started = getattr(self, "_convert_started", None)
        self._convert_started = None
        if not started:
            return
        try:
            elapsed = time.time() - float(started)
            secs = float(getattr(self, "_convert_song_secs", 0.0) or 0.0)
            if elapsed < 1.0 or secs <= 0:
                return
            ratios = getattr(self, "_convert_ratios", None)
            if ratios is None:
                ratios = self._convert_ratios = []
            ratios.append(elapsed / secs)
            if len(ratios) > 5:
                del ratios[:-5]
        except Exception:
            pass

    def _maybe_refresh_download_plan(self) -> None:
        """Re-stamp the queue while a transfer runs — at most once a second.

        Progress callbacks arrive many times per second; repainting the
        visible cards and the rundown at that rate would burn the UI thread
        for the one number that changes (a shrinking wait).
        """
        now = time.time()
        if now - float(getattr(self, "_dl_plan_at", 0.0) or 0.0) < 1.0:
            return
        self._dl_plan_at = now
        try:
            self._refresh_queue_times()
        except Exception:
            pass

    def _queue_insert_plan_at(self) -> int:
        """View position of the row on air (0 when nothing is playing).

        Same condition the clock column has always used: only a row that is
        actually PLAYING is anchored on `now − elapsed`. A selected-but-not-
        playing row is just row 0 of a queue that starts now.
        """
        view = getattr(self, "_queue_view", []) or []
        if not getattr(self, "_playing", False):
            return 0
        try:
            current = self.rq.current_index
            pos = (getattr(self, "_queue_pos_by_index", None) or {}).get(current)
            if pos is not None and 0 <= pos < len(view):
                return pos
        except Exception:
            pass
        return 0

    def _queue_plan(self) -> dict:
        """The whole rundown: song windows + everything that is not a song.

        `rows[i] = (start, stop)` for every displayed song, where a row's stop
        already includes the jingle / ad break it triggers — so the column
        reads continuously instead of leaving an unexplained gap between two
        songs. `events` carries the non-song air time with its own clock
        window: jingle, ad, anthem (a fixed moment that cuts in) and the
        transfer the row on air is waiting for.

        Anchored on the track playing right now (`now − elapsed`), so the list
        is a playout log. A row with a missing / NaN / negative duration still
        gets a stamp — it just lasts 0 s. Total: never raises.
        """
        view = getattr(self, "_queue_view", []) or []
        now = time.time()
        base_at = self._queue_insert_plan_at()
        base = now
        if getattr(self, "_playing", False):
            try:
                base = now - float(self.audio_player.get_position())
            except Exception:
                base = now
        durations = [_safe_seconds(getattr(song, "duration", 0))
                     for _orig_i, song in view]
        rows = [None] * len(view)
        events: list = []
        # Rows already aired: song windows only — today's jingle / ad counters
        # say nothing about which breaks fired in the past, and inventing one
        # would put air time in a log that already happened. They are walked
        # BACKWARDS from the row on air (whose stop is now − elapsed), so the
        # list stays a playout log instead of sliding those rows forward.
        cursor = base
        for i in range(min(base_at, len(view)) - 1, -1, -1):
            rows[i] = (cursor - durations[i], cursor)
            cursor -= durations[i]
        cursor = base          # the row on air starts where the playout log is
        # The row on air cannot start before its download / conversion ends.
        wait = self._download_stall_seconds()
        wait_kind = ((getattr(self, "_dl_state", None) or {}).get("status")
                     if self._download_mode else "")
        if wait > 0 and base_at < len(view):
            began = now
            events.append({"kind": "wait", "status": wait_kind,
                           "start": began, "stop": began + wait,
                           "secs": wait, "pos": base_at})
            cursor += wait
        cumulative, markers = self._queue_insert_plan(len(view) - base_at)
        after: dict = {}
        for step, secs, kind in markers:
            after.setdefault(step, []).append((kind, secs))
        anthems = self._anthem_schedule(cursor)
        a_idx = 0
        # An anthem that already started during the wait still aired.
        while a_idx < len(anthems) and anthems[a_idx][0] <= cursor:
            moment, air = anthems[a_idx]
            a_idx += 1
            if moment >= base:
                events.append({"kind": "anthem", "start": moment,
                               "stop": moment + air, "secs": air,
                               "pos": base_at})
        for i in range(base_at, len(view)):
            start = cursor
            stop = start + durations[i]
            # A fixed anthem moment inside this song cuts in and pushes it.
            while a_idx < len(anthems) and anthems[a_idx][0] <= stop:
                moment, air = anthems[a_idx]
                a_idx += 1
                if moment < start:
                    continue
                stop += air
                events.append({"kind": "anthem", "start": moment,
                               "stop": moment + air, "secs": air,
                               "pos": i})
            step = i - base_at + 1
            for kind, secs in after.get(step, ()):
                events.append({"kind": kind, "start": stop,
                               "stop": stop + secs, "secs": secs, "pos": i})
                stop += secs
            rows[i] = (start, stop)
            cursor = stop
        for i, row in enumerate(rows):
            if row is None:                 # filtered/degenerate view guard
                rows[i] = (cursor, cursor)
        return {"rows": rows, "events": events, "wait": wait,
                "wait_kind": wait_kind, "base_at": base_at}

    def _queue_schedule(self) -> list:
        """(start, stop) epoch seconds for every row, in view order."""
        try:
            return self._queue_plan()["rows"]
        except Exception:
            logging.getLogger("freq.queue").exception("queue plan failed")
            return []

    def _refresh_queue_clock(self) -> None:
        """Recompute the rundown once: row windows + non-song events."""
        plan = self._queue_plan()
        self._queue_times = plan["rows"]
        self._queue_events = plan["events"]
        self._queue_wait = plan["wait"]
        self._queue_wait_kind = plan["wait_kind"]

    @staticmethod
    def _plan_icon(kind: str) -> str:
        return {"jingle": "🔔", "ad": "📢", "anthem": "🇹🇭",
                "wait": "📥"}.get(kind, "•")

    def _queue_transfer_text(self) -> str:
        """The transfer in flight, with the clock time it should be done."""
        try:
            if not self._download_mode:
                return ""
            state = getattr(self, "_dl_state", None) or {}
            status = state.get("status")
            started = float(state.get("started") or 0.0)
            elapsed = max(0.0, time.time() - started) if started else 0.0
            if status == "downloading":
                pct = float(state.get("pct") or 0.0)
                eta = self._download_stall_seconds()
                bits = [f"📥 download {pct:.0f}%"]
                if eta > 0:
                    bits.append(f"ready ~{self._clock_time(time.time() + eta)}")
                    bits.append(f"{self._fmt_dur(eta)} left")
                else:
                    bits.append("rate not known yet")
                bits.append("queue waits")
                return " · ".join(bits)
            if status == "processing":
                est = self._convert_estimate()
                bits = ["🔄 convert", f"{self._fmt_dur(elapsed)} elapsed"]
                if est > 0:
                    left = max(0.0, est - elapsed)
                    bits.append(f"ready ~{self._clock_time(time.time() + left)}")
                    bits.append(f"{self._fmt_dur(left)} left (est.)")
                else:
                    bits.append("ETA unknown — no conversion measured yet")
                bits.append("queue waits")
                return " · ".join(bits)
        except Exception:
            pass
        return ""

    def _queue_beyond_text(self) -> list:
        """Next jingle / ad that falls PAST the last queued song.

        Their clock time depends on music that is not queued yet, so the line
        says how many more songs it needs rather than inventing a stamp —
        that is also why an empty queue can never predict them.
        """
        view = getattr(self, "_queue_view", []) or []
        if not view:
            return []
        try:
            base_at = self._queue_insert_plan_at()
            remaining = len(view) - base_at
            jingle_int = max(1, int(getattr(self.jingle, "interval", 5) or 5))
            ad_int = max(1, int(getattr(self.commercial, "interval", 10) or 10))
            _cum, markers = self._queue_insert_plan(
                remaining + max(64, jingle_int * 2, ad_int * 2))
            seen = {e.get("kind") for e in
                    (getattr(self, "_queue_events", None) or [])}
            parts = []
            for kind, label in (("jingle", "jingle"), ("ad", "ad")):
                if kind in seen:
                    continue
                step = next((s for s, _secs, k in markers
                             if k == kind and s > remaining), None)
                if step is None:
                    continue
                n = step - remaining
                parts.append(f"{self._plan_icon(kind)} {label} after "
                             f"{n} more song{'s' if n != 1 else ''}")
            return ["⏭ later: " + " · ".join(parts)] if parts else []
        except Exception:
            return []

    def _queue_next_insert_text(self) -> str:
        """The header rundown: the next insert of each kind, one line.

        Ads / jingles / the anthem used to be folded into the song column
        invisibly (and a break with an unreadable file vanished completely);
        download and conversion were not shown at all. Each entry carries its
        own time and length — `length ?` when the file cannot be measured —
        and a transfer says how long it still needs.

        Only the FIRST insert of a kind is spelled out: a 50-song queue with a
        jingle every five songs used to repeat "🔔 jingle HH:MM:SS (00:06)"
        five times and wrap over three lines, hiding the ad and anthem behind
        "+47 more". The rest are counted once at the end.
        """
        events = [e for e in (getattr(self, "_queue_events", None) or [])
                  if e.get("kind") in ("jingle", "ad", "anthem")]
        first_by_kind: dict = {}
        for event in events:
            first_by_kind.setdefault(event["kind"], event)
        parts = []
        for event in first_by_kind.values():
            kind = event["kind"]
            secs = float(event.get("secs") or 0.0)
            length = self._fmt_dur(secs) if secs > 0 else "length ?"
            parts.append(f"{self._plan_icon(kind)} {kind} "
                         f"{self._clock_time(event['start'])} ({length})")
        lines = []
        if parts:
            hidden = len(events) - len(first_by_kind)
            if hidden > 0:
                parts.append(f"+{hidden} more")
            lines.append("⏭ next: " + " · ".join(parts))
        lines.extend(self._queue_beyond_text())
        transfer = self._queue_transfer_text()
        if transfer:
            lines.append(transfer)
        return "\n".join(lines)

    def _refresh_queue_next_label(self) -> None:
        """Paint the header's insert line (empty when nothing is scheduled)."""
        label = getattr(self, "queue_next_label", None)
        if label is None:
            return
        try:
            text = self._queue_next_insert_text()
            if text:
                # Two or three lines (events / later / transfer) must wrap to
                # the panel, not run under the scrollbar.
                width = 0
                try:
                    panel = getattr(self, "queue_panel_frame", None)
                    if panel is not None and panel.winfo_exists():
                        width = max(240, int(panel.winfo_width()) - 24)
                except Exception:
                    width = 0
                if width:
                    label.configure(text=text, wraplength=width)
                else:
                    label.configure(text=text)
                # `before` keeps it directly under the header: a plain pack()
                # would re-insert it BELOW the list it describes.
                before = getattr(self, "queue_search_frame", None)
                if before is not None and before.winfo_exists():
                    label.pack(fill="x", padx=12, pady=(0, 2), before=before)
                else:
                    label.pack(fill="x", padx=12, pady=(0, 2))
            else:
                # Drop the text too: a hidden label that still holds the last
                # rundown would show a stale clock time the moment anything
                # (a theme change, a re-pack) put it back on screen.
                label.configure(text="")
                label.pack_forget()
        except Exception:
            logging.getLogger("freq.queue").exception("queue next-line failed")

    def _queue_row_times(self, pos: int) -> str:
        """'HH:MM:SS–HH:MM:SS' for one row, or '' when no schedule is built."""
        sched = getattr(self, "_queue_times", None) or []
        if not 0 <= pos < len(sched):
            return ""
        start, stop = sched[pos]
        return f"{self._clock_time(start)}–{self._clock_time(stop)}"

    def _refresh_queue_times(self) -> None:
        """Re-stamp the clock column (the minute rolled) and repaint the rows
        that are on screen — the drawn window, never the whole list."""
        if not getattr(self, "_queue_view", None):
            return
        try:
            self._refresh_queue_clock()
        except Exception:
            logging.getLogger("freq.queue").exception(
                "queue time column failed")
            return
        for pos in list(getattr(self, "_queue_items", {})):
            self._queue_paint_row(pos)
        self._refresh_queue_next_label()

    def _draw_queue_row(self, canvas, pos: int):
        """Draw one row as canvas items; returns the item ids.

        Seven items per row instead of eight widgets (~one canvas each for
        CTk): the cost is per VISIBLE row, so the queue length stops
        mattering.
        """
        orig_i, song = self._queue_view[pos]
        top = pos * QUEUE_ROW_H
        left = QUEUE_CARD_PAD
        card_right = max(left + QUEUE_CARD_MIN_W,
                         self._queue_canvas_width() - QUEUE_CARD_PAD)
        mid = top + QUEUE_CARD_H // 2
        # A note line is a separator, not a song: it has no columns to fill.
        if self.rq.is_note(song):
            return self._draw_note_row(canvas, pos, song, top, left,
                                       card_right, mid)
        # The end-of-song mark owns the card's right edge; every text column
        # is measured from the space left of it, so nothing can overlap. The
        # skip flag sits between the two and is reserved the same way.
        mark_left, mark_right = self._queue_mark_span(card_right)
        right = mark_left - QUEUE_MARK_GAP - QUEUE_FLAG_W
        bottom = top + QUEUE_CARD_H
        is_current = orig_i == self.rq.current_index
        selected = (orig_i == self._queue_selected
                    or orig_i in (self._queue_selection or ()))
        hovered = pos == self._queue_hover_pos

        if is_current:
            bg, outline = COLORS["playing_bg"], COLORS["accent"]
        elif selected:
            bg, outline = COLORS["bg_selected"], COLORS["selected_border"]
        elif hovered:
            bg, outline = COLORS["bg_hover"], ""
        else:
            # Resting cards are the SAME tone as the panel: the list reads as
            # one flat surface (like the original OBS-style queue) and only
            # hover / selection / now-playing lift a card out of it.
            bg, outline = COLORS["bg_card"], ""
        items = [canvas.create_polygon(
            self._rounded_points(left, top, card_right, bottom, RADII["md"]),
            fill=bg, outline=outline, width=1 if outline else 0)]

        # Left rail: play marker (or position), then the source icon.
        status = (("▶️" if self._playing else "⏸️") if is_current
                  else f"{orig_i + 1}.")
        items.append(canvas.create_text(
            left + 12, mid, text=status, anchor="w",
            fill=COLORS["accent"] if is_current else COLORS["text3"],
            font=_queue_font(11)))
        items.append(canvas.create_text(
            left + 40, mid, text=self._queue_row_icon(song), anchor="w",
            fill=COLORS["text"], font=_queue_font(12)))
        # Right side of the card: the scheduled start–stop clock time
        # (HH:MM:SS–HH:MM:SS), then the length. A narrow card drops the clock
        # and hands the room back to the title instead of squeezing both
        # columns in.
        text_x = left + 62
        room = (right - QUEUE_SIDE_W) - text_x
        clock = self._queue_row_times(pos)
        if clock and room >= 40:
            items.append(canvas.create_text(
                right - QUEUE_DUR_INSET - QUEUE_DUR_W - 6, mid, text=clock,
                anchor="e", fill=COLORS["text3"], font=_queue_font(11)))
        else:
            room = (right - QUEUE_DUR_INSET - QUEUE_DUR_W - 8) - text_x
        room = max(40, room)
        # A live item has no length to count down: say so instead of 00:00.
        length_text = ("LIVE" if getattr(song, "source", "") == "stream"
                       else self._fmt_dur(song.duration))
        items.append(canvas.create_text(
            right - QUEUE_DUR_INSET, mid, text=length_text,
            anchor="e", fill=COLORS["text3"], font=_queue_font(11)))
        title = self._ellipsize(song.title or "Unknown", room, 13)
        skipped = bool(getattr(song, "skip", False))
        # A skipped song is still in the rundown, so the row must say so:
        # the flag plus a dimmed title is unmistakable at a glance.
        flag_left, flag_right = self._queue_flag_span(card_right)
        if skipped:
            items.append(canvas.create_text(
                (flag_left + flag_right) / 2, mid, text="⚑", anchor="center",
                fill=COLORS["orange"], font=_queue_font(13, True)))
        if is_current:
            title_fill = COLORS["accent"]
        elif skipped:
            title_fill = COLORS["text3"]
        else:
            title_fill = COLORS["text"]
        if song.artist:
            items.append(canvas.create_text(
                text_x, top + 20, text=title, anchor="w", fill=title_fill,
                font=_queue_font(13, True)))
            items.append(canvas.create_text(
                text_x, top + 47,
                text=self._ellipsize(song.artist, room, 11), anchor="w",
                fill=COLORS["text2"], font=_queue_font(11)))
        else:
            items.append(canvas.create_text(
                text_x, mid, text=title, anchor="w", fill=title_fill,
                font=_queue_font(13, True)))
        # What happens when this song ends — the only clickable spot in the
        # row's right column (see `_queue_mark_span`), drawn as vectors.
        items.extend(self._draw_end_mark(canvas, mark_right, mid, song))
        return items

    def _draw_note_row(self, canvas, pos: int, song, top: float, left: float,
                       card_right: float, mid: float) -> list:
        """Paint a note line — a coloured rule with its text and action.

        Three canvas items (a hover wash, the rule and the label) so a long
        rundown with many notes costs no more than its songs do; the text is
        the note's own title and the action sits at the right edge.
        """
        color = str(getattr(song, "note_color", "") or COLORS["accent"])
        items = []
        if pos == self._queue_hover_pos:
            items.append(canvas.create_polygon(
                self._rounded_points(left, top + 18, card_right,
                                     top + QUEUE_CARD_H - 18, RADII["md"]),
                fill=COLORS["bg_hover"], outline=""))
        items.append(canvas.create_line(left + 16, mid, card_right - 16, mid,
                                        fill=color, width=2))
        for cap_x in (left + 12, card_right - 16):
            items.append(canvas.create_oval(cap_x, mid - 4, cap_x + 8, mid + 4,
                                            fill=color, outline=""))
        action = str(getattr(song, "note_action", "none") or "none")
        badge = ""
        if action != "none":
            label = RadioQueue.NOTE_ACTION_LABELS.get(action, action)
            badge = f"⏸ {label}"
        text_right = card_right - 18
        if badge:
            badge_id = canvas.create_text(
                text_right, mid, text=badge, anchor="e", fill=color,
                font=_queue_font(10, True))
            items.append(badge_id)
            try:
                text_right = canvas.bbox(badge_id)[0] - 10
            except Exception:
                text_right = card_right - 150
        room = max(60, text_right - (left + 32))
        items.append(canvas.create_text(
            left + 32, mid, text=self._ellipsize("📝 " + (song.title or "Note"),
                                                 room, 12),
            anchor="w", fill=color, font=_queue_font(12, True)))
        return items

    def _end_mark_photo(self, mode: str):
        """The icon for one end mode — loaded once, reused by every row.

        Uses the operator's artwork from `icons/` (the PNGs baked from the
        SVGs by `make_end_icons.py`), resampled once to the row size. When a
        file is missing — or Pillow/ImageTk is unavailable — the same meaning
        is drawn from `_END_SHAPES` at `QUEUE_MARK_SS`× instead, so a broken
        asset can never leave a row without its mark.
        """
        # Cached per APP, not per module: a PhotoImage belongs to the Tk
        # interpreter that made it, and a second RadioApp in the same
        # process (tests, a restart) would draw dead images.
        cache = self._end_photos
        if mode in cache:
            return cache[mode]
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            cache[mode] = None
            return None
        photo = None
        png = _END_ICON_PNG.get(mode)
        if png:
            # The operator's own icon, resampled once to the row size.
            try:
                icon = Image.open(_END_ICON_DIR / png).convert("RGBA")
                icon = icon.resize((QUEUE_MARK_ICON_H, QUEUE_MARK_ICON_H),
                                   Image.LANCZOS)
                photo = ImageTk.PhotoImage(icon)
            except Exception:
                logging.getLogger("freq.queue").warning(
                    "end icon %s could not be loaded — drawing the shape instead",
                    png, exc_info=True)
        if photo is not None:
            cache[mode] = photo
            return photo
        shapes = _END_SHAPES.get(mode, ())
        if not shapes:
            cache[mode] = None
            return None
        s = QUEUE_MARK_SS
        w = (len(shapes) * QUEUE_MARK_ICON_W
             + (len(shapes) - 1) * QUEUE_MARK_ICON_GAP)
        h = QUEUE_MARK_ICON_H
        img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        x = 0
        try:
            for shape, color_key in shapes:
                color = COLORS[color_key]
                right = x + QUEUE_MARK_ICON_W
                if shape == "play":
                    draw.polygon([(x * s, 1 * s), (right * s, h * s / 2),
                                  (x * s, (h - 1) * s)], fill=color)
                elif shape == "stop":
                    draw.rounded_rectangle([x * s, 2 * s, right * s, (h - 2) * s],
                                           radius=2.5 * s, fill=color)
                else:                          # ring — normal, nothing extra
                    draw.ellipse([(x + 0.8) * s, 2.8 * s,
                                  (right - 0.8) * s, (h - 2.8) * s],
                                 outline=color, width=int(1.6 * s))
                x += QUEUE_MARK_ICON_W + QUEUE_MARK_ICON_GAP
            img = img.resize((w, h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
        except Exception:
            logging.getLogger("freq.queue").exception(
                "end mark %r could not be rendered", mode)
            cache[mode] = None
            return None
        cache[mode] = photo           # keep the reference or Tk drops the pixels
        return photo

    def _draw_end_mark(self, canvas, mark_right: float, mid: float, song):
        """Draw a row's end-of-song mark; returns the canvas items it made.

        Every item is tagged (`endmark`, `endmark-<mode>`) so callers — and
        the smoke suites — can find the mark without guessing its geometry.
        One item per VISIBLE row (two only on the path fallback), nothing per
        song in the queue.
        """
        mode = self._song_end_mode(song)
        shapes = _END_SHAPES.get(mode, ())
        if not shapes:
            return []
        tags = ("endmark", f"endmark-{mode}")
        photo = self._end_mark_photo(mode)
        if photo is not None:
            return [canvas.create_image(mark_right - photo.width(), mid,
                                        image=photo, anchor="w", tags=tags)]
        icon_w, icon_h = QUEUE_MARK_ICON_W, QUEUE_MARK_ICON_H
        total = (len(shapes) * icon_w
                 + (len(shapes) - 1) * QUEUE_MARK_ICON_GAP)
        x = mark_right - total
        items = []
        for shape, color_key in shapes:
            color = COLORS[color_key]
            try:
                if shape == "play":
                    items.append(canvas.create_polygon(
                        x, mid - icon_h / 2,
                        x + icon_w, mid,
                        x, mid + icon_h / 2,
                        fill=color, outline=color, width=0, tags=tags))
                elif shape == "stop":
                    items.append(canvas.create_polygon(
                        self._rounded_points(x, mid - icon_h / 2 + 1.5,
                                             x + icon_w, mid + icon_h / 2 - 1.5,
                                             2),
                        fill=color, outline=color, width=0, tags=tags))
                else:                       # "ring" — normal, nothing extra
                    items.append(canvas.create_oval(
                        x + 1, mid - icon_h / 2 + 1,
                        x + icon_w - 1, mid + icon_h / 2 - 1,
                        outline=color, width=1.4, tags=tags))
            except Exception:
                logging.getLogger("freq.queue").exception(
                    "end mark %r could not be drawn", mode)
            x += icon_w + QUEUE_MARK_ICON_GAP
        return items

    @staticmethod
    def _queue_mark_span(card_right: float):
        """(left, right) of a row's end-mark column, in canvas pixels.

        One function for both the painter and the hit test: a click that
        lands on the mark and the mark that is drawn must always agree.
        """
        right = card_right - QUEUE_MARK_INSET
        return right - QUEUE_MARK_W, right

    @classmethod
    def _queue_flag_span(cls, card_right: float):
        """(left, right) of the skip-flag zone — just left of the end mark."""
        mark_left, _mark_right = cls._queue_mark_span(card_right)
        right = mark_left - 4
        return right - QUEUE_FLAG_W, right

    @staticmethod
    def _song_end_mode(song) -> str:
        """A song's end-of-song behaviour, whatever the object holds."""
        mode = str(getattr(song, "end_mode", "auto") or "auto").lower()
        return mode if mode in END_MODES else "auto"

    @staticmethod
    def _rounded_points(x0: float, y0: float, x1: float, y1: float,
                        radius: float, steps: int = 4):
        """Polygon points for a rounded rectangle — ONE canvas item.

        Tk has no rounded rect: four arcs plus two rects would be six items
        per card. Sampling each corner arc into a single polygon keeps the
        card at one item while still looking curved at this radius.
        """
        r = max(0.0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
        if r <= 0:
            return [x0, y0, x1, y0, x1, y1, x0, y1]
        pts = []
        for cx, cy, start in ((x1 - r, y0 + r, -90.0),
                              (x1 - r, y1 - r, 0.0),
                              (x0 + r, y1 - r, 90.0),
                              (x0 + r, y0 + r, 180.0)):
            for i in range(steps + 1):
                angle = math.radians(start + 90.0 * i / steps)
                pts.extend((cx + r * math.cos(angle),
                            cy + r * math.sin(angle)))
        return pts

    @staticmethod
    def _ellipsize(text, max_px: int, size: int) -> str:
        """Clip text to the pixels available (canvas text cannot wrap)."""
        if not text:
            return ""
        text = str(text)          # ids/numbers from hand-edited data
        per_char = max(4.0, size * 0.55)     # rough advance of the UI font
        room = max(4, int(max_px // per_char))
        return text if len(text) <= room else text[:room - 1] + "…"

    @classmethod
    def _queue_row_icon(cls, song) -> str:
        """Source badge for a card — total, whatever the Song object holds."""
        source = getattr(song, "source", "") or ""
        try:
            if source == "youtube":
                return "⚡" if song_cache.has(getattr(song, "id", "")) else "🔗"
            if source == "mic":
                return "🎙"
            if source == "file":
                return "📁"
            if source == "stream":
                return "📡"
            if source == "note":
                return "📝"
        except Exception:
            pass
        return "🎵"

    # ── Scroll following ─────────────────────────────────────────

    def _start_queue_poll(self) -> None:
        """Follow the outer canvas's scroll position.

        CTkScrollableFrame scrolls its internal canvas and exposes no
        callback to children, so the visible window is refreshed on a cheap
        timer (one ``yview()`` call when nothing moved).
        """
        if self.__dict__.get("_queue_view_after"):
            return
        self._queue_view_after = self.after(QUEUE_POLL_MS,
                                            self._queue_view_tick)

    def _queue_view_tick(self) -> None:
        self._queue_view_after = None
        try:
            canvas = getattr(self, "_queue_canvas", None)
            if canvas is not None and canvas.winfo_exists():
                # One int compare per poll: when the wall clock rolls into
                # the next minute the start/stop column is re-stamped.
                minute = int(time.time() // 60)
                if minute != self.__dict__.get("_queue_clock_minute"):
                    self._queue_clock_minute = minute
                    self._refresh_queue_times()
                self._draw_queue_window()
        except Exception:
            # Log and keep the timer alive: a repaint bug must never stop
            # the list from following the scroll for the rest of the session.
            logging.getLogger("freq.queue").exception("queue repaint failed")
        try:
            self._queue_view_after = self.after(QUEUE_POLL_MS,
                                                self._queue_view_tick)
        except Exception:
            self._queue_view_after = None    # window torn down — stop quietly

    # ── Row events (bound once, on the canvas) ───────────────────

    def _on_queue_canvas_press(self, event):
        # Focus follows the click, so arrows start navigating rows; until
        # then Up/Down keep meaning volume, exactly as before.
        try:
            self._queue_canvas.focus_set()
        except Exception:
            pass
        pos = self._queue_pos_at(event)
        if pos is None:
            self._on_queue_empty_click(event)    # click below the last row
            return
        if self._over_queue_flag(pos, event.x):
            self._toggle_skip_flag(self._queue_view[pos][0])
            return
        if self._over_queue_mark(pos, event.x):
            self._cycle_song_end_mode(self._queue_view[pos][0])
            return
        self._on_queue_click(self._queue_view[pos][0], event)

    def _over_queue_mark(self, pos: int, x: float) -> bool:
        """Was this click/hover inside the row's end-of-song mark?

        The mark column is the card's right edge, so a press there cycles the
        mode instead of selecting (and never starts playback).
        """
        if not 0 <= pos < len(self._queue_view):
            return False
        card_right = max(QUEUE_CARD_PAD + QUEUE_CARD_MIN_W,
                         self._queue_canvas_width() - QUEUE_CARD_PAD)
        left, right = self._queue_mark_span(card_right)
        return left <= x <= right

    def _over_queue_flag(self, pos: int, x: float) -> bool:
        """Was this click inside the row's skip-flag zone?"""
        if not 0 <= pos < len(self._queue_view):
            return False
        card_right = max(QUEUE_CARD_PAD + QUEUE_CARD_MIN_W,
                         self._queue_canvas_width() - QUEUE_CARD_PAD)
        left, right = self._queue_flag_span(card_right)
        return left <= x <= right

    def _queue_gap_at(self, event):
        """Insertion index for a click in the free space between two cards."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None:
            return None
        try:
            y = canvas.canvasy(event.y)
        except Exception:
            y = event.y
        view = self._queue_view
        if not view:
            return 0
        pos = int(y // QUEUE_ROW_H)
        offset = y - pos * QUEUE_ROW_H
        if 0 <= pos < len(view):
            if offset < QUEUE_CARD_H:
                return None            # on a card, not in the gap
            return int(view[pos][0]) + 1
        if pos < 0:
            return int(view[0][0])
        return int(view[-1][0]) + 1

    # ── Note lines (separators) ────────────────────────────────────
    def _gap_context_menu(self, insert_at: int, event) -> None:
        """Right-click in the gap between two songs: add a note line there."""
        menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"], fg=COLORS["text"],
                       activebackground=COLORS["accent"], activeforeground="white")
        menu.add_command(label="📝  Add note line here",
                         command=lambda: self._open_note_editor(insert_at))
        if self._queue_undo:
            menu.add_command(label="↩  Undo", command=self._undo_queue)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_note_editor(self, position: int) -> None:
        """Create a note line at ``position``, or edit the one already there.

        A note carries a colour, a text and an action. The action is what
        gives the line meaning to the rundown: "Do nothing" is a label the
        operator reads, "Stop the rundown" ends playback where the line sits.
        """
        queue = self.rq.queue
        position = max(0, min(int(position), len(queue)))
        existing = (queue[position]
                    if 0 <= position < len(queue)
                    and self.rq.is_note(queue[position]) else None)
        colors = list(RadioQueue.NOTE_COLORS)
        actions = list(RadioQueue.NOTE_ACTIONS)
        current_color = str(getattr(existing, "note_color", "") or colors[0][1])
        color_label = next((name for name, value in colors
                            if value == current_color), colors[0][0])
        action_label = RadioQueue.NOTE_ACTION_LABELS.get(
            str(getattr(existing, "note_action", "none") or "none"),
            actions[0][1])

        dlg = ctk.CTkToplevel(self)
        dlg.title("📝 Note line")
        dlg.geometry("400x300")
        dlg.configure(fg_color=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        ctk.CTkLabel(dlg, text="📝 Edit note line" if existing else "📝 New note line",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(pady=(14, 8))

        ctk.CTkLabel(dlg, text="Text", text_color=COLORS["text2"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24)
        text_var = ctk.StringVar(value=str(getattr(existing, "title", "") or ""))
        text_entry = ctk.CTkEntry(dlg, textvariable=text_var, width=340)
        text_entry.pack(padx=24, pady=(2, 10))
        text_entry.focus_set()
        self._bind_clipboard_shortcuts(text_entry)

        ctk.CTkLabel(dlg, text="Colour", text_color=COLORS["text2"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24)
        color_var = ctk.StringVar(value=color_label)
        ctk.CTkOptionMenu(dlg, variable=color_var,
                          values=[name for name, _v in colors], width=180
                          ).pack(anchor="w", padx=24, pady=(2, 10))
        ctk.CTkLabel(dlg, text="Action when the rundown reaches this line",
                     text_color=COLORS["text2"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24)
        action_var = ctk.StringVar(value=action_label)
        ctk.CTkOptionMenu(dlg, variable=action_var,
                          values=[label for _k, label in actions], width=220
                          ).pack(anchor="w", padx=24, pady=(2, 12))

        def apply() -> None:
            text = text_var.get().strip()
            color = dict(colors)[color_var.get()]
            action = next((key for key, label in actions
                           if label == action_var.get()), "none")
            self._apply_note_edit(existing, position, text, color, action)
            dlg.destroy()

        def delete() -> None:
            self._push_queue_undo("delete note")
            try:
                self.rq.remove_song(self.rq.queue.index(existing))
            except (ValueError, AttributeError):
                pass
            dlg.destroy()
            self._refresh_queue(keep_scroll=True)
            self._show_toast("📝 Note line removed")

        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=18, pady=(6, 14))
        ctk.CTkButton(row, text="Cancel", width=80, height=30, corner_radius=8,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      text_color=COLORS["text"], command=dlg.destroy
                      ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(row, text="Save", width=90, height=30, corner_radius=8,
                      fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                      command=apply).pack(side="right")
        if existing is not None:
            ctk.CTkButton(row, text="🗑 Delete", width=92, height=30,
                          corner_radius=8, fg_color=COLORS["red"],
                          hover_color=COLORS["red_hover"], text_color="#ffffff",
                          command=delete).pack(side="left")
        dlg.bind("<Return>", lambda _e: apply())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)

    def _apply_note_edit(self, existing, position: int, text: str,
                         color: str, action: str) -> None:
        """Write a note line's text/colour/action (used by the dialog)."""
        if existing is not None:
            if (existing.title, existing.note_color,
                    existing.note_action) == (text or "Note", color, action):
                return
            self._push_queue_undo("edit note")
            existing.title = text or "Note"
            existing.note_color = color
            existing.note_action = action
        else:
            self._push_queue_undo("add note")
            self.rq.add_note(position, text, color, action)
        self._refresh_queue(keep_scroll=True)
        self._show_toast("📝 Note line saved")

    def _toggle_skip_flag(self, index: int) -> None:
        """Pin / clear the temporary skip flag on one queue row."""
        if not self.rq._valid_index(index):
            return
        song = self.rq.queue[index]
        if self.rq.is_note(song):
            return
        self._push_queue_undo("skip flag")
        song.skip = not bool(getattr(song, "skip", False))
        # A song already on air keeps playing: the flag applies the next time
        # the rundown reaches it.
        self._refresh_queue(keep_scroll=True)
        title = getattr(song, "title", "") or "song"
        self._show_toast((f"⚑ Skipping '{title}' — the rundown will step over it"
                          if song.skip else f"⚑ Back in the rundown: '{title}'"))

    # ── Undo / redo for queue edits ────────────────────────────────
    # A snapshot is the queue plus the position on air. Ops that destroy or
    # reorder rows push one BEFORE they touch the list, so Ctrl+Z always puts
    # back exactly what was there — including a song that was removed while
    # playing (its identity, not a rebuilt copy).
    QUEUE_UNDO_LIMIT = 40

    def _queue_state(self) -> tuple:
        import copy
        return (copy.deepcopy(self.rq.queue), int(self.rq.current_index))

    def _push_queue_undo(self, label: str = "queue change") -> None:
        queue, index = self._queue_state()
        self._queue_undo.append((label, queue, index))
        if len(self._queue_undo) > self.QUEUE_UNDO_LIMIT:
            del self._queue_undo[0]
        self._queue_redo = []

    def _undo_queue(self) -> None:
        if not self._queue_undo:
            self._show_toast("↩ Nothing to undo")
            return
        label, queue, index = self._queue_undo.pop()
        self._queue_redo.append((label, *self._queue_state()))
        if len(self._queue_redo) > self.QUEUE_UNDO_LIMIT:
            del self._queue_redo[0]
        self._restore_queue_state(queue, index)
        self._show_toast(f"↩ Undone: {label}")

    def _redo_queue(self) -> None:
        if not self._queue_redo:
            self._show_toast("↪ Nothing to redo")
            return
        label, queue, index = self._queue_redo.pop()
        self._queue_undo.append((label, *self._queue_state()))
        self._restore_queue_state(queue, index)
        self._show_toast(f"↪ Redone: {label}")

    def _restore_queue_state(self, queue, index: int) -> None:
        self.rq.queue = list(queue)
        self.rq.current_index = int(index)
        try:
            self._queue_selected = None
            self._queue_selection = []
            self._queue_shift_anchor = None
        except Exception:
            pass
        self._refresh_queue(keep_scroll=True)
        self._update_queue_filter_btn()
        self._update_now_playing()

    def _undo_key(self, _event=None):
        """Ctrl+Z for the queue — but never inside a text field."""
        widget = None
        try:
            widget = self.focus_get()
        except Exception:
            widget = None
        if widget is not None and widget.winfo_class() in ("Entry", "Text"):
            return None
        self._undo_queue()
        return "break"

    def _redo_key(self, _event=None):
        widget = None
        try:
            widget = self.focus_get()
        except Exception:
            widget = None
        if widget is not None and widget.winfo_class() in ("Entry", "Text"):
            return None
        self._redo_queue()
        return "break"

    def _queue_set_cursor(self, cursor: str) -> None:
        """Install a cursor on the list canvas — only when it changes."""
        if self.__dict__.get("_queue_cursor") == cursor:
            return
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        self._queue_cursor = cursor
        try:
            canvas.configure(cursor=cursor)
        except Exception:
            pass

    def _cycle_song_end_mode(self, index: int) -> None:
        """Cycle one song's end mode (normal → break → stop → break+stop).

        Only the song object changes, then ONE row is repainted in place:
        no rebuild, no re-layout, so a click costs the same at 6 songs and
        at 4,000.
        """
        try:
            song = self.rq.queue[index]
        except (IndexError, TypeError, KeyError):
            return
        mode = self._song_end_mode(song)
        mode = END_MODES[(END_MODES.index(mode) + 1) % len(END_MODES)]
        song.end_mode = mode
        pos = self._queue_pos_by_index.get(index)
        if pos is not None:
            self._queue_paint_row(pos)
        self._show_toast(f"🎯 {_END_LABEL[mode]}", 2500)

    def _on_queue_canvas_double(self, event):
        pos = self._queue_pos_at(event)
        # Two quick presses on the end mark are two mode changes, not a
        # request to play the song.
        if pos is not None and not self._over_queue_mark(pos, event.x):
            self._on_queue_double_click(self._queue_view[pos][0])

    def _on_queue_canvas_context(self, event):
        pos = self._queue_pos_at(event)
        if pos is not None:
            index = self._queue_view[pos][0]
            if self.rq.is_note(self.rq.queue[index]):
                self._note_context_menu(index, event)
            else:
                self._on_queue_right_click(index, event)
            return
        # Free space between two cards: that is where a note line goes.
        gap = self._queue_gap_at(event)
        if gap is not None:
            self._gap_context_menu(gap, event)

    def _note_context_menu(self, index: int, event) -> None:
        """Right-click on a note line: edit it, or remove it."""
        menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"], fg=COLORS["text"],
                       activebackground=COLORS["accent"], activeforeground="white")
        menu.add_command(label="📝  Edit note…",
                         command=lambda: self._open_note_editor(index))
        menu.add_command(label="📝  Add note line below",
                         command=lambda: self._open_note_editor(index + 1))
        menu.add_separator()
        menu.add_command(label="🗑  Delete note line",
                         command=lambda: self._delete_note_at(index))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_note_at(self, index: int) -> None:
        if not self.rq._valid_index(index) or not self.rq.is_note(self.rq.queue[index]):
            return
        self._push_queue_undo("delete note")
        self.rq.remove_song(index)
        self._refresh_queue(keep_scroll=True)
        self._show_toast("📝 Note line removed")

    def _on_queue_canvas_motion(self, event):
        pos = self._queue_pos_at(event)
        # The end mark and the skip flag are the two clickable spots that are
        # not the row itself: a hand cursor says so without a per-row widget.
        self._queue_set_cursor(
            "hand2" if pos is not None
            and (self._over_queue_mark(pos, event.x)
                 or self._over_queue_flag(pos, event.x))
            else "")
        prev = self._queue_hover_pos
        if pos == prev:
            return
        self._queue_hover_pos = pos
        for changed in (prev, pos):
            if changed is not None:
                self._queue_paint_row(changed)

    def _on_queue_canvas_leave(self, event=None):
        prev = self._queue_hover_pos
        self._queue_hover_pos = None
        if prev is not None:
            self._queue_paint_row(prev)

    # ── In-place row painting (no rebuild) ──

    def _queue_paint_row(self, pos: int) -> None:
        """Redraw ONE drawn row in place (hover / marker / selection)."""
        canvas = getattr(self, "_queue_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        for item in self._queue_items.pop(pos, ()):
            try:
                canvas.delete(item)
            except Exception:
                pass
        if 0 <= pos < len(self._queue_view):
            self._queue_items[pos] = self._draw_queue_row(canvas, pos)

    def _refresh_queue_markers(self) -> None:
        """Re-mark the playing/current row — repaint just those two rows.

        `_play_index` used to call `_refresh_queue()`, i.e. destroy and
        recreate every row on every track start (6.8 s with 100 songs).
        With the list virtualized the two changed rows are redrawn, and
        only if they are on screen at all.
        """
        if threading.current_thread() is not threading.main_thread():
            try:
                self.after(0, self._refresh_queue_markers)
            except Exception:
                pass
            return
        current = self.rq.current_index
        prev = self.__dict__.get("_queue_marked_index")
        mapped = getattr(self, "_queue_pos_by_index", {})
        for idx in {prev, current}:
            if idx is None:
                continue
            pos = mapped.get(idx)
            if pos is not None and pos in self._queue_items:
                self._queue_paint_row(pos)
        self._queue_marked_index = current
    def _update_queue_filter_btn(self) -> None:
        """Show the Filter / Edit / Preview buttons only with a selection."""
        btn = getattr(self, "btn_queue_filter", None)
        edit = getattr(self, "btn_queue_edit", None)
        preview = getattr(self, "btn_queue_preview", None)
        if btn is None:
            return
        if self._queue_selected is None:
            for widget in (btn, edit, preview):
                if widget is None:
                    continue
                try:
                    widget.pack_forget()
                except Exception:
                    pass
        else:
            try:
                btn.pack(side="right", padx=2)
            except Exception:
                pass
            if edit is not None:
                try:
                    edit.pack(side="right", padx=(2, 4))
                except Exception:
                    pass
            if preview is not None:
                try:
                    preview.pack(side="right", padx=2)
                except Exception:
                    pass

    # ── Selection helpers (deselect) ──
    def _bind_queue_empty_click(self) -> None:
        """Bind <Button-1> on the playlist BACKGROUND (empty space).

        ``CTkScrollableFrame`` renders its children into an internal frame
        on top of an internal canvas, so an empty-space click lands on one
        of those two widgets rather than on the scrollable frame itself.
        ``add="+"`` keeps CTk's own canvas bindings (scroll/focus) intact.
        """
        frame = getattr(self, "queue_frame", None)
        if frame is None:
            return
        for widget in (frame,
                       getattr(frame, "_parent_frame", None),
                       getattr(frame, "_parent_canvas", None)):
            if widget is None:
                continue
            try:
                widget.bind("<Button-1>", self._on_queue_empty_click,
                            add="+")
            except Exception:
                pass

    def _on_queue_empty_click(self, event=None) -> None:
        """Click on empty playlist space: drop the whole selection.

        Never returns "break": CTk's canvas keeps its own Button-1 work
        (focus/scroll) and must still run.
        """
        self._clear_queue_selection()

    def _clear_queue_selection(self) -> bool:
        """Drop the queue selection (Ctrl+D, or a background click).

        Returns True when something was actually selected, so callers/tests
        can tell a real deselect from a no-op. Only the selection paint is
        touched — the queue itself is never rebuilt here, so a deselect
        cannot disturb playback or the scroll position.
        """
        had_selection = bool(self._queue_selection) \
            or self._queue_selected is not None
        if not had_selection:
            return False
        # An audition belongs to the row it was started from; dropping the
        # selection must not leave it playing with no button to stop it.
        if getattr(self, "_preview_session", None) is not None \
                or getattr(self, "_preview_pending", False):
            self._stop_preview(quiet=True)
        token = self.__dict__.get("_queue_sel_after")
        if token:
            try:
                self.after_cancel(token)
            except Exception:
                pass
            self._queue_sel_after = None
        self._queue_selected = None
        self._queue_selection = []
        self._queue_shift_anchor = None
        self._update_queue_selection_paint()
        self._update_queue_filter_btn()
        return True

    def _selected_song(self):
        """The Song behind the current queue selection (None if invalid)."""
        idx = self._queue_selected
        if idx is None or not self.rq._valid_index(idx):
            return None
        try:
            return self.rq.queue[idx]
        except IndexError:
            return None

    def _open_selected_filters(self) -> None:
        """OBS-style: open the Filters dialog for the selected queue row —
        songs get EQ / auto-gain / crossfade, mic segments get the native
        4-stage mic filter chain. Read-only about playback: it only flips
        the SAME settings already wired elsewhere."""
        song = self._selected_song()
        if song is None:
            messagebox.showinfo("Filter", "Select a song or mic segment first.")
            return
        if getattr(song, "source", "") == "note":
            messagebox.showinfo("Filter",
                                "A note line carries no audio to filter.")
        elif getattr(song, "source", "") == "mic":
            self._open_mic_filters()
        else:
            self._open_song_filters(song)

    def _editable_audio_path(self, song) -> str:
        """The audio file this queue item can be edited from ("" if none).

        A YouTube item has no ``file_path`` — it plays from the download
        cache, so the cache entry for its song id is the real source. A mic
        segment is not a file of the operator's to rewrite, so it is refused.
        """
        if song is None or getattr(song, "source", "") in ("mic", "note"):
            return ""
        path = str(getattr(song, "file_path", "") or "")
        if path and os.path.isfile(path):
            return path
        try:
            cached = song_cache.get(song.id) if getattr(song, "id", "") else None
        except Exception:
            cached = None
        if cached and os.path.isfile(cached):
            return cached
        return ""

    def _open_selected_editor(self) -> None:
        """Open the native waveform editor for the selected queue item.

        Local files are edited in place; a YouTube item is edited from its
        cached audio, and the edited WAV is kept OUTSIDE the cache (clearing
        the cache must never delete a song the rundown now points at).
        """
        song = self._selected_song()
        if song is None:
            messagebox.showinfo("Edit audio", "Select a song first.")
            return
        if self.rq.is_note(song):
            messagebox.showinfo(
                "Edit audio",
                "A note line carries no audio — edit its text with a "
                "right-click instead.")
            return
        # The editor opens its own monitor session; two of them on one device
        # would fight over it, so the queue's audition stops first.
        self._stop_preview(quiet=True)
        if getattr(song, "source", "") == "mic":
            messagebox.showinfo(
                "Edit audio",
                "A live mic segment has no file to edit — record it to a file "
                "first, then add that file to the queue.")
            return

        path = self._editable_audio_path(song)
        if not path:
            # A YouTube row that was never played has no audio yet. Editing
            # one should not require airing it first: fetch it now, with the
            # Edit button showing the progress, and open the editor when the
            # file lands.
            if (getattr(song, "source", "") == "youtube"
                    and getattr(song, "url", "")):
                self._download_then_edit(song)
                return
            messagebox.showinfo(
                "Edit audio",
                "This item has no local audio file yet.\n"
                "Play it once so it is downloaded, then edit it.")
            return
        self._open_editor_for(song, path)

    def _open_editor_for(self, song, path: str) -> None:
        """Open the editor window on an existing audio file."""
        # Local sources keep their own folder; a cached download lands in an
        # `edits` folder beside the playlist instead.
        is_cached = not (getattr(song, "file_path", "") and
                         os.path.isfile(str(song.file_path)))
        save_dir = None
        if is_cached:
            try:
                save_dir = str(Path(self.rq._save_path).parent / "edits")
            except Exception:
                save_dir = str(Path.home() / "FreQ Edits")

        try:
            from editor_window import EditorWindow
        except Exception as err:      # native module missing / import failure
            messagebox.showerror("Edit audio", f"The audio editor is unavailable:\n\n{err}")
            return
        try:
            EditorWindow(self, path, song=song, on_save=self._on_song_edited,
                         display_name=song.title or os.path.basename(path),
                         save_dir=save_dir)
        except Exception as err:
            messagebox.showerror("Edit audio", f"Cannot open the editor:\n\n{err}")

    def _edit_button(self):
        """The queue's Edit button, or None (built after the panel exists)."""
        return getattr(self, "btn_queue_edit", None)

    def _set_edit_button_text(self, text: str) -> None:
        btn = self._edit_button()
        if btn is None:
            return
        try:
            btn.configure(text=text)
        except Exception:
            pass

    # ── Preview (audition) ────────────────────────────────────────
    # The Preview button auditions the selected song on the MONITOR device
    # through the same native engine the editor uses. `self.audio_player` is
    # never touched, so a preview started during a live broadcast is heard
    # only where the operator is listening.
    def _preview_button(self):
        """The queue's Preview button, or None (built after the panel)."""
        return getattr(self, "btn_queue_preview", None)

    def _set_preview_button_text(self, text: str) -> None:
        btn = self._preview_button()
        if btn is None:
            return
        try:
            btn.configure(text=text)
        except Exception:
            pass

    def _monitor_name_volume(self) -> tuple:
        """``(device_name, volume)`` from the editor's saved monitor prefs.

        The editor already lets the operator pick the device previews are
        heard on; every kind of audition reuses that choice so they all land
        in the same pair of headphones.
        """
        name, volume = "", 0.8
        try:
            from editor_window import load_prefs

            prefs = load_prefs()
            name = str(prefs.get("monitor_device") or "")
            volume = max(0.0, min(1.0,
                               float(prefs.get("monitor_volume", 80)) / 100.0))
        except Exception:
            pass
        return name, volume

    def _monitor_target(self) -> tuple:
        """``(device_index, volume)`` — the editor's monitor, by index."""
        index, volume = -1, 0.8
        try:
            from audio_editor import list_monitor_devices

            wanted, volume = self._monitor_name_volume()
            if wanted:
                for name, idx in list_monitor_devices():
                    if name == wanted:
                        index = int(idx)
                        break
        except Exception:
            pass
        return index, volume

    def _preview_active(self) -> bool:
        """True while any audition is running (file, live link or mic)."""
        audition = getattr(self, "_audition", None)
        return (getattr(self, "_preview_session", None) is not None
                or getattr(self, "_preview_pending", False)
                or (audition is not None and audition.running))

    def _toggle_preview(self) -> None:
        """Preview button: start the audition, or stop the one playing."""
        dialog = getattr(self, "_mic_preview_dialog", None)
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.focus_set()
                    return
            except Exception:
                pass
            self._mic_preview_dialog = None
        if self._preview_active():
            self._stop_preview()
            return
        song = self._selected_song()
        if song is None:
            messagebox.showinfo("Preview", "Select a song first.")
            return
        source = getattr(song, "source", "")
        if source == "note":
            messagebox.showinfo("Preview",
                                "A note line has no audio to audition.")
            return
        if source in ("mic", "stream"):
            self._start_live_preview(song)
            return
        path = self._editable_audio_path(song)
        if not path:
            messagebox.showinfo(
                "Preview",
                "This item has no local audio file yet.\n"
                "Press Edit to download it first, then preview it here.")
            return
        self._start_preview(song, path)

    def _start_live_preview(self, song) -> None:
        """Audition a live row — a microphone or a signal link — off air.

        Neither has a file, so the editor's decoder cannot help: a signal is
        demuxed by ffmpeg into a private render ring, and a mic is live-mixed
        into one by the native core. Both engines start on the MONITOR
        endpoint, so the program output keeps its own device and the operator
        hears the audition alone.
        """
        from audition import MonitorAudition

        if getattr(self, "_audition", None) is None:
            self._audition = MonitorAudition()
        name, volume = self._monitor_name_volume()
        source = getattr(song, "source", "")
        title = (getattr(song, "title", "") or source)[:48]
        self._preview_title = title

        if source == "mic":
            choices = self._mic_preview_choices(song)
            if len(choices) > 1:
                # Several inputs configured: let the operator pick which one
                # to hear (checking one cable at a time is the whole point).
                self._mic_preview_dialog = MicPreviewDialog(
                    self, choices,
                    on_pick=lambda choice: self._begin_mic_preview(choice, name, volume),
                    title=title)
                return
            self._begin_mic_preview(choices[0] if choices else None,
                                    name, volume)
            return

        # A signal link is probed first — a dead one must never look like a
        # live audition — and both the probe and the connect run off the UI
        # thread, because a slow CDN can take seconds.
        url = str(getattr(song, "url", "") or "").strip()
        if not url:
            messagebox.showinfo("Preview", "This stream has no link to audition.")
            return
        events: "queue.Queue" = queue.Queue()
        cancel = threading.Event()
        self._preview_events = events
        self._preview_cancel = cancel
        self._preview_pending = True
        self._set_preview_button_text("  ⏳ \u2026")

        def worker() -> None:
            try:
                ok, detail = self.audio_player.probe_stream(url, timeout=10)
                if cancel.is_set():
                    events.put(("cancelled",))
                    return
                if not ok:
                    events.put(("error", f"The link did not answer: {detail}"))
                    return
                started, why = self._audition.start_stream(url, name, volume)
                events.put(("live", started, why))
            except Exception as err:
                events.put(("error", str(err)))

        threading.Thread(target=worker, name="freq-preview-probe",
                         daemon=True).start()
        self._poll_preview(title)

    def _mic_preview_choices(self, song) -> list:
        """The microphones this segment is configured with, as pickable rows.

        A mic item carries the exact spec list it will air (up to four
        capture endpoints with their own gains), so preview offers those —
        plus the system default when the item does not pin one — instead of
        silently monitoring whatever Windows made default.
        """
        names = {}
        for dev in getattr(self, "_mic_input_devices", []) or []:
            endpoint = getattr(dev, "endpoint_id", None)
            if endpoint:
                names[endpoint] = str(getattr(dev, "name", "") or endpoint)
        specs = []
        try:
            specs = list(self._mic_specs(song) or [])
        except Exception:
            specs = []
        choices, seen = [], set()
        for position, (endpoint, gain) in enumerate(specs, start=1):
            key = (endpoint or "", round(float(gain), 2))
            if key in seen:
                continue
            seen.add(key)
            if endpoint:
                label = names.get(endpoint) or "Configured microphone"
            else:
                label = "System default microphone"
            if len(specs) > 1:
                label = f"{position}. {label}"
            gain = max(0.5, min(4.0, float(gain)))
            choices.append({
                "label": f"{label}   (gain {gain:g})",
                "endpoint": endpoint,
                "gain": gain,
                "name": label,
            })
        if not any(choice["endpoint"] is None for choice in choices):
            choices.append({"label": "System default microphone",
                            "endpoint": None, "gain": 1.0,
                            "name": "System default microphone"})
        return choices

    def _begin_mic_preview(self, choice, monitor_name: str,
                           volume: float) -> None:
        """Start (or refuse) the mic monitor for one configured input."""
        if not getattr(self, "_audition", None):
            return
        endpoint = (choice or {}).get("endpoint")
        gain = float((choice or {}).get("gain", 1.0) or 1.0)
        name = str((choice or {}).get("name", "") or "the microphone")
        ok, why = self._audition.start_mic(monitor_name, volume,
                                          [(endpoint, gain)])
        if not ok:
            self._set_preview_button_text("  ▶ Preview")
            messagebox.showerror(
                "Preview",
                "Cannot open this microphone for monitoring:\n\n" + str(why))
            return
        self._set_preview_button_text("  ■ MIC")
        self._show_toast(f"🎧 Monitoring {name} on the monitor device")

    def _start_preview(self, song, path: str) -> None:
        """Decode the file off the UI thread, then audition it on the monitor.

        Opening the native session means decoding the whole file, which on a
        long track takes a moment — the UI must keep painting, so it happens
        in a worker that hands the session back through a queue this thread
        polls. Nothing here touches the player or the rundown.
        """
        events: "queue.Queue" = queue.Queue()
        cancel = threading.Event()
        self._preview_events = events
        self._preview_cancel = cancel
        self._preview_pending = True
        self._preview_started = False
        self._set_preview_button_text("  ⏳ \u2026")
        title = (getattr(song, "title", "") or os.path.basename(path))[:48]
        index, volume = self._monitor_target()

        def worker() -> None:
            session = None
            try:
                from audio_editor import EditSession
                session = EditSession(path)
                if cancel.is_set():
                    session.close()
                    events.put(("cancelled",))
                    return
                events.put(("session", session, index, volume))
            except Exception as err:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
                events.put(("error", str(err)))

        threading.Thread(target=worker, name="freq-preview-load",
                         daemon=True).start()
        self._poll_preview(title)

    def _poll_preview(self, title: str = "") -> None:
        """UI thread: pick up the loaded session and drive the button label."""
        events = getattr(self, "_preview_events", None)
        audition = getattr(self, "_audition", None)
        if events is None and not (audition is not None and audition.running):
            return
        while events is not None:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "session":
                session, index, volume = event[1], event[2], event[3]
                self._preview_pending = False
                started = False
                try:
                    started = session.preview(0, session.frames, index, volume)
                except Exception:
                    started = False
                if (not started
                        or (self._preview_cancel is not None
                            and self._preview_cancel.is_set())):
                    try:
                        session.close()
                    except Exception:
                        pass
                    self._preview_events = None
                    self._set_preview_button_text("  ▶ Preview")
                    self._show_toast("⚠️ Nothing to preview in this item")
                    return
                self._preview_session = session
                self._preview_title = title
                self._set_preview_button_text("  ■ Stop")
                self._show_toast(f"🎧 Previewing '{title}' on the monitor")
            elif kind == "error":
                self._preview_pending = False
                self._preview_events = None
                self._set_preview_button_text("  ▶ Preview")
                messagebox.showerror(
                    "Preview",
                    f"Cannot preview this item:\n\n{event[1]}")
                return
            elif kind == "cancelled":
                self._preview_pending = False
                self._preview_events = None
                self._set_preview_button_text("  ▶ Preview")
                return
            elif kind == "live":
                self._preview_pending = False
                self._preview_events = None
                started = bool(event[1])
                why = str(event[2] or "")
                audition = getattr(self, "_audition", None)
                if not started or audition is None or not audition.running:
                    self._set_preview_button_text("  ▶ Preview")
                    messagebox.showerror(
                        "Preview",
                        "The signal could not be auditioned on the monitor:\n\n"
                        + (why or "the monitor device is not available"))
                    return
                self._set_preview_button_text("  ■ 0:00")
                self._show_toast(f"🎧 Auditioning '{title}' on the monitor")
        audition = getattr(self, "_audition", None)
        if audition is not None and audition.running:
            if audition.kind == "stream":
                if audition.ended:
                    # The feed stopped (or the link dropped): release the
                    # engine instead of leaving a silent render thread alive.
                    self._stop_preview(quiet=True)
                    self._show_toast("🎧 The monitored signal ended")
                    return
                seconds = int(audition.position)
                self._set_preview_button_text(
                    f"  ■ {seconds // 60}:{seconds % 60:02d}")
            else:
                # A microphone has no length: the label stays ■ MIC.
                self._set_preview_button_text("  ■ MIC")
        session = getattr(self, "_preview_session", None)
        if session is not None:
            playing, seconds = False, 0.0
            try:
                playing = bool(session.playing)
                seconds = session.position / max(1, session.sample_rate)
            except Exception:
                playing = False
            if playing:
                self._preview_started = True
                self._set_preview_button_text(
                    f"  ■ {int(seconds) // 60}:{int(seconds) % 60:02d}")
            elif getattr(self, "_preview_started", False):
                # The file reached its end on its own: release the session and
                # go back to "Preview", exactly like pressing Stop would.
                self._stop_preview(quiet=True)
                self._show_toast("🎧 Preview finished")
                return
        try:
            self._preview_after = self.after(150, lambda: self._poll_preview(title))
        except Exception:
            pass

    def _stop_preview(self, quiet: bool = False) -> None:
        """Stop the audition (and the loader, if it is still decoding)."""
        cancel = getattr(self, "_preview_cancel", None)
        if cancel is not None:
            cancel.set()
        token = getattr(self, "_preview_after", None)
        if token:
            try:
                self.after_cancel(token)
            except Exception:
                pass
        self._preview_after = None
        self._preview_events = None
        self._preview_pending = False
        session = getattr(self, "_preview_session", None)
        self._preview_session = None
        self._preview_started = False
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        dialog = getattr(self, "_mic_preview_dialog", None)
        if dialog is not None:
            self._mic_preview_dialog = None
            try:
                dialog._cancel()
            except Exception:
                pass
        audition = getattr(self, "_audition", None)
        live = bool(audition is not None and audition.running)
        if live:
            try:
                audition.stop()
            except Exception:
                pass
        self._set_preview_button_text("  ▶ Preview")
        if not quiet and (session is not None or live):
            self._show_toast("⏹ Preview stopped")

    def _download_then_edit(self, song) -> None:
        """Fetch a YouTube item's audio, then open the editor on it.

        The download runs off the UI thread through the cache's own worker
        path, so the queue, the player and the rundown keep running; the Edit
        button carries the percentage. The worker never touches Tk — it drops
        progress and the result into a queue that this thread polls — so a
        slow yt-dlp call cannot corrupt the UI. Two presses on the same row
        reuse the download already in flight.
        """
        if getattr(self, "_editor_fetching", None) is None:
            self._editor_fetching = {}
        song_id = getattr(song, "id", "") or ""
        if song_id in self._editor_fetching:
            self._show_toast("\u23f3 Already downloading this song for editing…")
            return
        events: "queue.Queue" = queue.Queue()
        self._editor_fetching[song_id] = {"events": events, "after": None}
        title = (getattr(song, "title", "") or "song")[:48]
        self._set_edit_button_text("  \u23f3 0%")
        self._show_toast(f"\u23f3 Downloading '{title}' to edit…")

        def on_progress(status: str, pct: float) -> None:
            # cache.py reports 0-100 here (not 0-1).
            try:
                value = max(0.0, min(100.0, float(pct or 0.0)))
            except Exception:
                value = 0.0
            events.put(("progress", value))

        def worker() -> None:
            path = None
            error = ""
            try:
                path = song_cache.cache_song(
                    song_id, getattr(song, "title", "") or "",
                    getattr(song, "url", "") or "",
                    float(getattr(song, "duration", 0.0) or 0.0),
                    on_progress=on_progress)
            except Exception as err:
                error = str(err)
            events.put(("done", path, error))

        threading.Thread(target=worker, name="freq-editor-fetch",
                         daemon=True).start()
        self._poll_editor_fetch(song)

    def _poll_editor_fetch(self, song) -> None:
        """UI thread: drain the download worker's events every 120 ms."""
        song_id = getattr(song, "id", "") or ""
        state = (getattr(self, "_editor_fetching", {}) or {}).get(song_id)
        if not state:
            return
        events = state["events"]
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "progress":
                self._set_edit_button_text(f"  \u23f3 {int(event[1])}%")
            elif event[0] == "done":
                self._edit_download_done(song, event[1], event[2])
                return
        try:
            state["after"] = self.after(120, lambda: self._poll_editor_fetch(song))
        except Exception:
            pass

    def _edit_download_done(self, song, path: Optional[str], error: str) -> None:
        """UI thread: the download for the editor finished (or failed)."""
        song_id = getattr(song, "id", "") or ""
        state = None
        try:
            state = (getattr(self, "_editor_fetching", {}) or {}).pop(song_id, None)
        except Exception:
            pass
        if state and state.get("after"):
            try:
                self.after_cancel(state["after"])
            except Exception:
                pass
        self._set_edit_button_text("  Edit")
        if path and os.path.isfile(path):
            self._show_toast("\u2705 Downloaded — opening the editor")
            self._open_editor_for(song, path)
            return
        messagebox.showerror(
            "Edit audio",
            "The download did not finish, so there is nothing to edit yet.\n\n"
            + (error or "No audio file was produced — check the URL and the "
                        "network, then try Edit again."))

    def _on_song_edited(self, song, new_path: str, duration: float) -> None:
        """Point the queue item at the edited WAV (the source is untouched)."""
        if song is None:
            return
        try:
            # Absolute: a relative path breaks the moment the app is started
            # from a different working directory (shortcut, other drive).
            song.file_path = os.path.abspath(str(new_path))
            song.duration = float(duration)
            # A YouTube item plays through play_youtube() (cache/download), so
            # a new file_path alone would be ignored — the item has to stop
            # being a YouTube item for the edited audio to reach the output.
            # The URL is kept as provenance.
            if getattr(song, "source", "") != "mic":
                song.source = "file"
            # A copy of the same song in the library keeps the same id, so it
            # must follow the edit too — otherwise adding it again would air
            # the unedited file.
            for other in list(getattr(self.rq, "library", []) or []):
                if getattr(other, "id", None) == getattr(song, "id", None):
                    other.file_path = song.file_path
                    other.duration = float(duration)
        except Exception:
            pass
        try:
            self._refresh_queue(keep_scroll=True)
        except Exception:
            pass
        try:
            self._show_toast(f"\u2702\ufe0f Edited audio linked: "
                             f"{os.path.basename(str(new_path))}")
        except Exception:
            pass

    def _open_song_filters(self, song) -> None:
        """"Filters for '<song>'" dialog: the app's real per-stream filters,
        presented OBS-style. Every control drives an existing API."""
        if not hasattr(self, "audio_player"):
            return
        title = song.title or "Unknown"
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Filters for '{title}'")
        dialog.geometry("440x470")
        dialog.minsize(390, 410)
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=f"Filters for '{title}'",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(14, 2))
        ctk.CTkLabel(dialog,
                     text="These apply to the whole music stream (same knobs\n"
                          "as the Mixer and Settings pages), OBS-style.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="center").pack()

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(8, 0))

        # ── EQ presets (one-click rows) ──
        ctk.CTkLabel(body, text="EQ Preset", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(
            fill="x", pady=(10, 2))
        preset_bar = ctk.CTkFrame(body, fg_color="transparent")
        preset_bar.pack(fill="x", pady=(0, 4))

        # ── EQ (3-band, live) ──
        ctk.CTkLabel(body, text="Equalizer (dB)", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(
            fill="x", pady=(10, 2))
        try:
            bass, mid, treble = self.audio_player.get_eq()
        except Exception:
            bass = mid = treble = 0.0
        eq_vars = {}
        for attr, label, val in (("bass", "Bass", bass),
                                 ("mid", "Mid", mid),
                                 ("treble", "Treble", treble)):
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=52, anchor="w",
                         font=ctk.CTkFont(size=10)).pack(side="left")
            var = ctk.DoubleVar(value=float(val))
            num = ctk.CTkLabel(row, text=f"{val:+.1f}", width=44,
                               font=ctk.CTkFont(size=10),
                               text_color=COLORS["text2"])
            ctk.CTkSlider(row, from_=-12, to=12, variable=var,
                          width=200,
                          command=lambda v, n=num: n.configure(
                              text=f"{float(v):+.1f}")).pack(
                side="left", padx=(0, 8))
            num.pack(side="left")
            eq_vars[attr] = (var, num)

        def apply_preset(name: str) -> None:
            """Fill the sliders from EQSettings.PRESETS (same table as the
            Settings page). Nothing reaches the engine until Apply."""
            try:
                p = self.eq.PRESETS.get(name, self.eq.PRESETS["flat"])
            except Exception:
                return
            for attr, _label, _default in (("bass", "Bass", 0.0),
                                           ("mid", "Mid", 0.0),
                                           ("treble", "Treble", 0.0)):
                var, num = eq_vars[attr]
                val = float(p.get(attr, 0.0))
                var.set(val)
                num.configure(text=f"{val:+.1f}")

        for name, label in (("vocal", "Vocal boost"),
                            ("bass_cut", "Bass cut"),
                            ("flat", "Flat")):
            ctk.CTkButton(preset_bar, text=label, height=26,
                          corner_radius=6,
                          fg_color=COLORS["bg_hover"],
                          hover_color=COLORS["border"],
                          text_color=COLORS["text"],
                          command=lambda n=name: apply_preset(n)).pack(
                side="left", padx=(0, 6))

        # ── Loudness Auto-Gain ──
        ag_row = ctk.CTkFrame(body, fg_color="transparent")
        ag_row.pack(fill="x", pady=(12, 0))
        ag_var = ctk.BooleanVar(value=bool(getattr(self, "_auto_gain_enabled", False)))
        ctk.CTkSwitch(ag_row, text="Loudness Auto-Gain (even volume across songs)",
                      variable=ag_var).pack(side="left")

        # ── Crossfade ──
        xf_row = ctk.CTkFrame(body, fg_color="transparent")
        xf_row.pack(fill="x", pady=(10, 0))
        xf_var = ctk.BooleanVar(value=bool(getattr(self.crossfade, "enabled", False)))
        ctk.CTkSwitch(xf_row, text="Crossfade between songs",
                      variable=xf_var).pack(side="left")

        def apply_changes() -> None:
            b = eq_vars["bass"][0].get()
            m = eq_vars["mid"][0].get()
            t = eq_vars["treble"][0].get()
            try:
                self.audio_player.set_eq(b, m, t)
            except Exception:
                pass
            self.eq.bass, self.eq.mid, self.eq.treble = b, m, t
            # No mirroring into Settings sliders: that section is gone (the
            # master chain is the only EQ), so the model above is the whole
            # record of this curve.
            try:
                self.audio_player.crossfade_enabled = bool(xf_var.get())
                self.crossfade.enabled = bool(xf_var.get())
                if hasattr(self, "btn_crossfade"):
                    self.btn_crossfade.configure(
                        fg_color=COLORS["accent"] if xf_var.get() else "transparent",
                        text_color="#fff" if xf_var.get() else COLORS["text"],
                    )
            except Exception:
                pass
            try:
                self._auto_gain_enabled = bool(ag_var.get())
                self.audio_player.set_auto_gain(
                    self._auto_gain_enabled, getattr(self, "_auto_gain_target", -14.0))
                if hasattr(self, "btn_auto_gain"):
                    self.btn_auto_gain.configure(
                        fg_color=COLORS["accent"] if self._auto_gain_enabled else "transparent",
                        text_color="#fff" if self._auto_gain_enabled else COLORS["text"],
                    )
            except Exception:
                pass
            self._mark_settings_dirty()
            dialog.destroy()

        btnbar = ctk.CTkFrame(dialog, fg_color="transparent")
        btnbar.pack(side="bottom", fill="x", padx=14, pady=10)
        ctk.CTkButton(btnbar, text="Close", width=88, height=30,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      command=dialog.destroy).pack(side="right")
        ctk.CTkButton(btnbar, text="Apply", width=96, height=30,
                      fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
                      text_color="#000000",
                      command=apply_changes).pack(side="right", padx=(0, 8))

    def _on_queue_click(self, index: int, event=None) -> None:
        if event is not None and (event.state & 0x0001):  # Shift held
            self._queue_shift_click(index)
            return
        self._queue_selected = index
        self._queue_selection = [index]
        self._queue_shift_anchor = None
        self._update_queue_filter_btn()
        # Defer the row-destroying re-render past the double-click window:
        # rebuilding instantly used to eat the second click, so
        # double-click-to-play almost never reached <Double-Button-1>.
        token = self.__dict__.get("_queue_sel_after")
        if token:
            try:
                self.after_cancel(token)
            except Exception:
                pass
        self._queue_sel_after = self.after(260, self._apply_queue_selection)

    def _apply_queue_selection(self) -> None:
        self._queue_sel_after = None
        # keep_scroll: re-rendering 260 ms after a click must not yank the
        # list back to the top when the user clicked a row further down.
        self._refresh_queue(keep_scroll=True)

    def _queue_shift_click(self, index: int) -> None:
        """Shift+click: extend the selection from the anchor to this row."""
        n = len(self.rq.queue)
        if n <= 0:
            return
        anchor = (self._queue_shift_anchor
                  if self._queue_shift_anchor is not None
                  else (self._queue_selected
                        if self._queue_selected is not None else 0))
        anchor = max(0, min(n - 1, anchor))
        lo, hi = min(anchor, index), max(anchor, index)
        self._queue_selection = list(range(lo, hi + 1))
        self._queue_selected = index
        self._queue_shift_anchor = anchor
        self._update_queue_selection_paint()

    def _on_queue_right_click(self, index: int, event=None) -> None:
        """Context menu: play, rename, flag, note line, remove."""
        menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"], fg=COLORS["text"],
                       activebackground=COLORS["accent"], activeforeground="white")
        song = self.rq.queue[index] if self.rq._valid_index(index) else None
        menu.add_command(label="▶  Play",
                         command=lambda: self._on_queue_double_click(index))
        menu.add_command(label="✏️  Rename…",
                         command=lambda: self._rename_queue_song(index))
        menu.add_separator()
        menu.add_command(
            label=("⚑  Clear skip flag" if getattr(song, "skip", False)
                   else "⚑  Skip this song (temporary)"),
            command=lambda: self._toggle_skip_flag(index))
        menu.add_command(label="📝  Add note line below",
                         command=lambda: self._open_note_editor(index + 1))
        menu.add_separator()
        menu.add_command(label="🗑  Remove",
                         command=self._remove_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _rename_queue_song(self, index: int) -> None:
        """Rename a queue song's title/artist via a small dialog.

        Edits go into the queue Song object AND the matching library
        entry (same id) so the library view stays in sync; the queue
        autosave persists both.
        """
        if not (0 <= index < len(self.rq.queue)):
            return
        song = self.rq.queue[index]
        dlg = ctk.CTkToplevel(self)
        dlg.title("✏️ Rename")
        dlg.geometry("360x200")
        dlg.configure(fg_color=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        ctk.CTkLabel(dlg, text=f"Edit song #{index + 1}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=COLORS["text"]).pack(pady=(14, 6))

        ctk.CTkLabel(dlg, text="Title", text_color=COLORS["text2"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24)
        title_var = ctk.StringVar(value=song.title or "")
        title_entry = ctk.CTkEntry(dlg, textvariable=title_var, width=300)
        title_entry.pack(padx=24, pady=(2, 8))
        title_entry.focus_set()
        title_entry.select_range(0, "end")
        self._bind_clipboard_shortcuts(title_entry)

        ctk.CTkLabel(dlg, text="Artist", text_color=COLORS["text2"],
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=24)
        artist_var = ctk.StringVar(value=song.artist or "")
        artist_entry = ctk.CTkEntry(dlg, textvariable=artist_var, width=300)
        artist_entry.pack(padx=24, pady=(2, 12))
        self._bind_clipboard_shortcuts(artist_entry)

        def _save_and_close() -> None:
            new_title = title_var.get().strip()
            new_artist = artist_var.get().strip()
            if new_title:
                song.title = new_title
                song.artist = new_artist
                # Library entries sharing this id keep the rename on reload.
                for lib_song in self.rq.library:
                    if lib_song.id == song.id:
                        lib_song.title = new_title
                        lib_song.artist = new_artist
                self._queue_selection_paint_one()
                self._refresh_library()
                try:
                    # Persist the rename immediately, quietly (the shared
                    # _save() pops a message box — not wanted per-rename).
                    self.rq.save_to_file(settings=self._collect_settings())
                except Exception:
                    pass
                self._show_toast(f"✏️ Renamed → {new_title[:40]}")
            dlg.destroy()

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(pady=(0, 14))
        ctk.CTkButton(btns, text="Save", width=100,
                      fg_color=COLORS["accent"],
                      command=_save_and_close).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Cancel", width=100,
                      fg_color="transparent", hover_color=COLORS["bg_hover"],
                      command=dlg.destroy).pack(side="left", padx=6)
        dlg.bind("<Return>", lambda e: _save_and_close())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _queue_selection_paint_one(self) -> None:
        """Repaint a single (possibly renamed) queue row cheaply."""
        try:
            if getattr(self, "_queue_sel_after", None):
                return  # a full re-render is already scheduled
            for pos in list(self._queue_items):
                self._queue_paint_row(pos)
        except Exception:
            pass

    def _on_queue_double_click(self, index: int) -> None:
        if self.rq._valid_index(index) and self.rq.is_note(self.rq.queue[index]):
            # A separator is not a song: double-click opens it for editing.
            self._open_note_editor(index)
            return
        token = self.__dict__.get("_queue_sel_after")
        if token:
            try:
                self.after_cancel(token)
            except Exception:
                pass
            self._queue_sel_after = None
        self._play_index(index)

    # ═══════════════════════════════════════
    # Playback
    # ═══════════════════════════════════════
    def _start_title_marquee(self, label) -> None:
        """Long now-playing titles slide left↔right inside the bar edge.

        A title wider than the widget clips at the edge line, so past
        that the displayed window slides back and forth (200 ms tick)
        until the whole title has been readable. Short titles never
        animate, the slide holds while playback is paused, and external
        text updates (now-playing refresh, anthem override) reset the
        slide automatically — the tick detects any text we did not write
        by comparing against the last displayed value.
        """
        self._marquee_last_displayed = ""
        self._marquee_full = ""
        self._marquee_phase = 0
        self._marquee_dir = 1
        self._marquee_tkfont = None

        def _tick():
            try:
                if not label.winfo_exists():
                    return
            except Exception:
                return
            try:
                displayed = str(label.cget("text") or "")
                if displayed != self._marquee_last_displayed:
                    # External update — treat as the new full title.
                    self._marquee_full = displayed
                    self._marquee_phase = 0
                    self._marquee_dir = 1
                    self._marquee_last_displayed = displayed
                width = max(10, label.winfo_width())
                if self._marquee_tkfont is None:
                    from tkinter import font as tkfont
                    cf = label.cget("font")
                    try:
                        fam = cf.cget("family")
                        size = cf.cget("size")
                        weight = cf.cget("weight")
                    except Exception:
                        fam, size, weight = "Segoe UI", 16, "bold"
                    self._marquee_tkfont = tkfont.Font(
                        family=fam, size=size, weight=weight)
                avg = max(1, self._marquee_tkfont.measure("x"))
                fit = max(1, int(width // avg))
                full = self._marquee_full
                overflow = len(full) - fit
                if overflow <= 0 or not getattr(self, "_playing", False):
                    self._marquee_phase = 0
                    self._marquee_dir = 1
                else:
                    gap = "      "
                    base = full + gap
                    ext = base + base
                    window = ext[self._marquee_phase:self._marquee_phase + fit]
                    if window != displayed:
                        label.configure(text=window)
                        self._marquee_last_displayed = window
                    self._marquee_phase += self._marquee_dir
                    if self._marquee_phase <= 0:
                        self._marquee_phase = 0
                        self._marquee_dir = 1
                    elif self._marquee_phase >= len(base):
                        self._marquee_phase = len(base)
                        self._marquee_dir = -1
            except Exception:
                pass
            self.after(200, _tick)

        self.after(200, _tick)

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
        if self.rq._valid_index(index) and self.rq.is_note(self.rq.queue[index]):
            self._show_toast("📝 A note line is not a song — play the row below it")
            return
        # Going on air ends any audition still running on the monitor.
        if (getattr(self, "_preview_session", None) is not None
                or getattr(self, "_preview_pending", False)):
            self._stop_preview(quiet=True)
        # A track actually reaching playback resets the missing-file cascade.
        self._missing_skip_count = 0
        # A manual/queue selection invalidates any pending anthem auto-resume.
        self._anthem_resume_token += 1
        self._anthem_resume_cancel.set()
        # Any manual/queue playback supersedes anthem/jingle playback — cancel
        # a pending anthem/jingle resume so it can't hijack the new selection.
        self._anthem_playing = False
        self._anthem_was_playing = False
        self._jingle_playing = False
        self._jingle_was_playing = False
        # Stop current playback
        self.audio_player.stop()
        # Music is the live mixer source again (ads/jingle/anthem are done —
        # their finish/resume paths all funnel through here).
        self._active_source_channel = "music"
        self._apply_mixer_volumes()
        self.rq.play(index)
        self._playing = True
        # Mark the new current row in place: a full `_refresh_queue()` here
        # rebuilt every row (seconds with a long queue) on every track start.
        self._refresh_queue_markers()
        self._update_now_playing()

        # Start real playback
        song = self.rq.queue[index]
        self._emit_plugins("on_track_started", song=self._song_info(song))
        # As-run (opt-in): this is the one point where an item reaches the
        # output — mic, YouTube and local file all funnel through here.
        if song.source == "mic":
            self._asrun_begin("mic", song, channel="mic",
                              reason="live mic from the queue")
        elif song.source == "youtube":
            self._asrun_begin("music", song, channel="music",
                              reason="queue · YouTube")
        elif song.source == "stream":
            self._asrun_begin("stream", song, channel="music",
                              reason="queue · live stream")
        else:
            self._asrun_begin("music", song, channel="music",
                              reason="queue · local file")
        if song.source == "mic":
            self._play_mic_segment(song, index)
            return
        if song.source == "stream" and song.url:
            self._play_stream_song(song, index)
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
                on_progress=lambda status, pct, idx=index: self.after(
                    0, self._on_download_progress, status, pct, idx
                ),
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
            else:
                # Arm the crossfade engine for the track that follows this one.
                self.after(250, lambda i=index: self._arm_crossfade_for_next(i))
            # Generate waveform
            self.after(100, lambda fp=song.file_path: self._load_waveform(fp))
            # Start streaming this file if streaming is active
            if streamer.is_streaming:
                streamer.on_track_change(song.title, song.artist, song.file_path)
                _native_mix.update_metadata(song.title, song.artist)
        else:
            # Missing local file — try the download cache before giving up.
            cached = song_cache.get(song.id) if song.id else None
            if cached and os.path.isfile(cached):
                self.np_time_total.configure(text=song.duration_str)
                self.np_status.configure(
                    text="⚠ original missing — playing cached copy",
                    text_color=COLORS["orange"])
                self._play_cached_fallback(song, index, cached)
            else:
                # File gone and no cache — skip to the next track, but cap the
                # cascade so an all-missing queue cannot loop forever.
                self.np_status.configure(
                    text=f"❌ File not found: {os.path.basename(song.file_path or '')} — skipped",
                    text_color=COLORS["red"])
                # As-run: an item that never reached the output is recorded
                # as missing — the gap in the log is evidence too.
                self._asrun_end_active("missing", kind="music")
                logging.getLogger("freq").warning(
                    "Track '%s' missing (%s) — skipped", song.title, song.file_path)
                self._missing_skip_count = getattr(self, "_missing_skip_count", 0) + 1
                n = len(self.rq.queue)
                if n and self._missing_skip_count < n:
                    self.after(300, self.rq.skip_next)
                    self.after(350, lambda: self._play_index(self.rq.current_index))
                else:
                    self._missing_skip_count = 0
                    self._playing = False
                    self.rq.current_index = -1
                    # Repaint the one row that was marked, not the whole
                    # list: this branch runs with a long queue too.
                    self._refresh_queue_markers()
                    self._update_now_playing()
                    self.np_status.configure(
                        text="❌ No playable tracks — all files missing",
                        text_color=COLORS["red"])

    def _play_stream_song(self, song, index: int) -> None:
        """Air a live stream item straight from its link.

        The link is probed first, off the UI thread: a dead or wrongly typed
        .m3u8 has to say so instead of showing "Live" and going quiet. A
        probe that finishes after the operator moved on is dropped.
        """
        self.np_time_total.configure(text="LIVE")
        self.np_status.configure(text="📡 Connecting…",
                                text_color=COLORS["orange"])
        # Live audio has no envelope to draw, and no file to read one from.
        self.waveform.set_waveform([])
        self.audio_player.volume = self.volume_var.get() / 100.0
        self._stream_token = getattr(self, "_stream_token", 0) + 1
        token = self._stream_token
        # Workers never touch Tk: the probe appends an event and the progress
        # tick applies it on the UI thread (`_drain_stream_events`).
        self._stream_events = getattr(self, "_stream_events", [])

        def _probe():
            ok, detail = self.audio_player.probe_stream(song.url)
            self._stream_events.append(("probe", token, index, song, ok, detail))

        threading.Thread(target=_probe, daemon=True).start()

    def _drain_stream_events(self) -> None:
        """Apply pending stream probes on the UI thread (cheap, no-op idle)."""
        events = getattr(self, "_stream_events", None)
        if not events:
            return
        pending, events[:] = list(events), []
        for kind, token, index, song, ok, detail in pending:
            if token != getattr(self, "_stream_token", 0):
                continue        # the operator moved on while it was probing
            if kind == "ended":
                if self._stream_still_current(index):
                    self._on_track_finished()
                continue
            if not ok:
                self._stream_failed(index, detail)
                continue
            started = self.audio_player.play_stream(
                song.url,
                on_finish=lambda t=token, i=index: self._stream_events.append(
                    ("ended", t, i, None, True, "")),
            )
            self._stream_on_air(song, index, started, detail)

    def _stream_still_current(self, index: int) -> bool:
        """True while the item at ``index`` is still the one on air."""
        return (self._playing and self.rq.current_index == index
                and 0 <= index < len(self.rq.queue))

    def _stream_failed(self, index: int, detail: str) -> None:
        """A stream item whose link produced no audio."""
        if not self._stream_still_current(index):
            return
        message = f"❌ Stream failed: {detail}"
        self.np_status.configure(text=message[:120], text_color=COLORS["red"])
        self._asrun_end_active("missing", kind="stream")
        self._playing = False
        self.rq.current_index = -1
        self._refresh_queue_markers()
        self._show_toast(message[:90], duration=6000)

    def _stream_on_air(self, song, index: int, started: bool, detail: str) -> None:
        """The probe passed — mark the item live (or say why it is not)."""
        if not self._stream_still_current(index):
            return
        if not started:
            self._stream_failed(index, "could not open the audio output")
            return
        self.np_status.configure(text=f"📡 Live stream · {detail}",
                                text_color=COLORS["green"])
        if streamer.is_streaming:
            # No file to forward — the broadcast keeps its own pull and only
            # the now-playing metadata changes.
            streamer.on_track_change(song.title, song.artist, "")
            _native_mix.update_metadata(song.title, song.artist)

    def _play_cached_fallback(self, song, index: int, cached_path: str) -> None:
        """Play a cache copy of a track whose original file is missing."""
        if self.normalizer.enabled:
            gain = self.normalizer.get_gain(song.id, cached_path)
            self.audio_player.volume = (self.volume_var.get() / 100.0) * gain
        self.audio_player.play_file(
            cached_path,
            duration=song.duration,
            on_finish=lambda: self.after(0, self._on_track_finished),
        )
        if self._saved_playback_position > 0:
            pos = self._saved_playback_position
            self._saved_playback_position = 0.0
            self.after(100, lambda p=pos: self.audio_player.seek(p))
        else:
            self.after(250, lambda i=index: self._arm_crossfade_for_next(i))
        self.after(100, lambda fp=cached_path: self._load_waveform(fp))
        if streamer.is_streaming:
            streamer.on_track_change(song.title, song.artist, cached_path)
        _native_mix.update_metadata(song.title, song.artist)

    def _mic_specs(self, song: Song) -> list:
        """[(endpoint_or_None, gain)] for every mic configured on a queue item.

        The GUI mic list carries native endpoint ids; the queue stores the
        list position (d.id). A missing entry falls back to the OS default
        endpoint (None) — never to PortAudio, which must never open a mic.
        """
        devices = getattr(self, "_mic_input_devices", [])

        def endpoint_for(device_id):
            if device_id is None:
                return None
            for d in devices:
                if d.id == device_id and getattr(d, "endpoint_id", None):
                    return d.endpoint_id
            return None

        indexes = [song.mic_device_index,
                   getattr(song, "mic_device_index2", None),
                   getattr(song, "mic_device_index3", None),
                   getattr(song, "mic_device_index4", None)]
        # Stable ids recorded at add time beat list positions: Windows
        # re-numbers capture devices on every reboot/unplug (virtual mics
        # reshuffle constantly), which used to send old queue items to the
        # WRONG — usually silent — microphone.
        known_endpoints = {getattr(d, "endpoint_id", None) for d in devices}
        stored_ids = list(getattr(song, "mic_endpoint_ids", []) or [])
        gains = list(getattr(song, "mic_gains", []) or [])
        specs = []
        for pos, idx in enumerate(indexes):
            if idx is None:
                continue
            try:
                gain = float(gains[pos]) if pos < len(gains) else 1.0
            except (TypeError, ValueError):
                gain = 1.0
            endpoint = None
            if pos < len(stored_ids) and stored_ids[pos] in known_endpoints:
                endpoint = stored_ids[pos]
            if endpoint is None:
                endpoint = endpoint_for(idx)
            specs.append((endpoint, max(0.5, min(4.0, gain))))
        return specs or [(None, 1.0)]

    def _play_mic_segment(self, song: Song, index: int) -> None:
        """Play a microphone queue item.

        Preferred path: a LIVE mic mixed into the output by the C++ engine
        — zero round trip, the announcer is heard as they speak. When the
        live path is unavailable (pygame engine, no capture endpoint) we
        fall back to record-then-play without blocking the GUI.
        """
        duration = song.mic_duration or song.duration or 30.0
        device_index = song.mic_device_index
        # Map the queue item's device to a WASAPI endpoint id (the live
        # path speaks endpoint ids, not PortAudio indices).
        endpoint = None
        if device_index is not None:
            for d in getattr(self, "_mic_input_devices", []):
                if d.id == device_index and getattr(d, "endpoint_id", None):
                    endpoint = d.endpoint_id
                    break
        mic_specs = self._mic_specs(song)
        opened = 0
        # TWO attempts: a freshly plugged / just-released endpoint often
        # refuses the FIRST open ("sometimes falls back to Recording…"
        # reports) — one short settle between tries absorbs it without
        # any perceptible delay when the first try succeeds.
        for attempt in (1, 2):
            try:
                opened = self.audio_player.play_mic_multi(
                    duration, mic_specs, duck_factor=self.mic.duck_volume,
                    on_finish=lambda: self.after(0, self._on_track_finished))
            except Exception:
                logging.getLogger("freq.gui").exception(
                    "multi-mic start failed (attempt %d)", attempt)
            if opened > 0:
                break
            if attempt == 1:
                logging.getLogger("freq.gui").warning(
                    "live mic open returned 0 — retrying once after settle")
                time.sleep(0.35)
        if opened > 0:
            self.np_status.configure(
                text=f"🎙 Microphone LIVE ×{opened}"
                if opened > 1 else "🎙 Microphone LIVE",
                text_color=COLORS["green"])
            return
        logging.getLogger("freq.gui").warning(
            "live mic path unavailable (opened=0) - falling back to record-then-play")
        output_path = str(Path(tempfile.gettempdir()) / f"freq_mic_{uuid.uuid4().hex}.wav")
        self._download_mode = True
        self.np_status.configure(text="🎙 Recording microphone...", text_color=COLORS["orange"])
        self.btn_play.configure(state="disabled")

        def _record() -> None:
            # Pass the stable endpoint id when the queue item has one —
            # the index alone can point at the wrong mic after a reboot.
            stored_ids = list(getattr(song, "mic_endpoint_ids", []) or [])
            ok = record_wav(
                output_path, duration, device_index=device_index,
                endpoint_id=(stored_ids[0] if stored_ids else ""))

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
                    self._step_queue()
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
                    self._step_queue()
                    return
                if streamer.is_streaming:
                    streamer.on_track_change(song.title, song.artist, output_path)
                _native_mix.update_metadata(song.title, song.artist)

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
        self._refresh_queue_markers()
        self._update_now_playing()

    def _step_queue(self) -> None:
        """Move the rundown on one entry, honouring skip flags and note lines.

        One choke point for every "next" (a song ending, an ad finishing, a
        manual Next): the queue decides what is next — stepped-over songs and
        plain note lines are passed, a note that asks to stop ends the
        rundown right there, and nothing playable ends it quietly.
        """
        verdict = self.rq.skip_next()
        if verdict == "playing":
            self._play_index(self.rq.current_index)
            return
        if verdict == "note":
            self._stop_at_note()
            return
        self._playing = False
        self._refresh_queue_markers()
        self._update_now_playing()

    def _stop_at_note(self) -> None:
        """Halt where a note line's action asked — the line stays selected."""
        note = self._current_song_or_none()
        text = str(getattr(note, "title", "") or "note")
        self._playing = False
        self._stop_after_break = False
        try:
            self.audio_player.stop()
        except Exception:
            logging.getLogger("freq.gui").exception("stop at note failed")
        try:
            self.np_status.configure(text=f"⏸ Stopped at note — {text}",
                                     text_color=COLORS["orange"])
        except Exception:
            pass
        self._refresh_queue_markers()
        self._update_now_playing()
        self._show_toast(f"⏸ Stopped at the note line: {text}")
        logging.getLogger("freq.gui").info("rundown stopped at note %r", text)

    def _on_track_finished(self) -> None:
        """Whatever just ended — honour the song's end mode, then move on.

        A song's end mode (the mark at the end of its queue row) overrides the
        interval counters: "break" forces an ad/jingle after THIS song, and
        "stop" / "break_stop" end the rundown instead of advancing.
        """
        self._emit_plugins("on_track_finished",
                           song=self._song_info(self._current_song_or_none()))
        # As-run: whatever was on air just ended naturally — song, ad,
        # jingle and mic all finish through this one callback.
        self._asrun_end_active("natural")
        # --- Ad just finished — go to next song (or end the rundown if the
        # break was the last thing the operator asked for) ---
        if self._ad_playing:
            self._ad_playing = False
            self._active_source_channel = "music"
            self._apply_mixer_volumes()
            if self._stop_after_break:
                self._stop_rundown("break_stop")
                return
            self._step_queue()
            return

        # --- Jingle just finished — go to next song (same end rule) ---
        if self._jingle_playing:
            self._jingle_playing = False
            self._active_source_channel = "music"
            self._apply_mixer_volumes()
            if self._stop_after_break:
                self._stop_rundown("break_stop")
                return
            self._step_queue()
            return

        # --- Normal song finished — what did this song ask for? ---
        mode = self._song_end_mode(self._current_song_or_none())
        if self._stop_after_break:
            # A break from an earlier song that never got to finish must not
            # stop the rundown later on.
            self._stop_after_break = False
        if mode == "stop":
            self._stop_rundown("stop")
            return
        # A forced break airs NOW even when the interval is not due. The
        # "then stop" half is only remembered once the break actually starts:
        # a break that cannot air must not silently end a later song.
        force_break = mode in ("break", "break_stop")

        # Commercial Break (checked first — higher priority)
        if self.commercial.enabled and self.commercial.ads:
            self.commercial.song_counter += 1
            if force_break or self.commercial.song_counter >= self.commercial.interval:
                self.commercial.song_counter = 0
                ad_file = self.commercial.ads[self.commercial.current_ad_index % len(self.commercial.ads)]
                self.commercial.current_ad_index += 1
                if os.path.isfile(ad_file) and self._channel_factor("ads") > 0.0:
                    try:
                        ok = self.audio_player.play_file(
                            ad_file, duration=0,
                            on_finish=lambda: self.after(0, self._on_track_finished),
                        )
                    except Exception:
                        logging.getLogger("freq.gui").exception("ad play_file raised for %s", ad_file)
                        ok = False
                    if ok:
                        self._ad_playing = True
                        self._stop_after_break = (mode == "break_stop")
                        self._active_source_channel = "ads"
                        self._apply_mixer_volumes()
                        self._asrun_begin(                             "ad", title=Path(ad_file).stem or "ad break",
                            path=ad_file, channel="ads",                             reason=("end mark: ad break after this song"
                                    if force_break else
                                    f"automatic ad break (every "
                                    f"{self.commercial.interval} songs)"))
                        self.np_status.configure(text="📢 Playing Ad...", text_color=COLORS["orange"])
                        return
                    # Ad failed — reset and fall through to the next song.
                    self._active_source_channel = "music"
                    self._apply_mixer_volumes()
                    self.np_status.configure(text="", text_color=COLORS["text2"])

        # Jingle
        if self.jingle.enabled and self.jingle.jingle_file:
            self.jingle.song_counter += 1
            if force_break or self.jingle.song_counter >= self.jingle.interval:
                self.jingle.song_counter = 0
                if os.path.isfile(self.jingle.jingle_file) and self._channel_factor("jingle") > 0.0:
                    try:
                        ok = self.audio_player.play_file(
                            self.jingle.jingle_file, duration=0,
                            on_finish=lambda: self.after(0, self._on_track_finished),
                        )
                    except Exception:
                        logging.getLogger("freq.gui").exception("jingle play_file raised for %s", self.jingle.jingle_file)
                        ok = False
                    if ok:
                        self._jingle_playing = True
                        self._stop_after_break = (mode == "break_stop")
                        self._active_source_channel = "jingle"
                        self._apply_mixer_volumes()
                        self._asrun_begin(
                            "jingle",
                            title=Path(self.jingle.jingle_file).stem or "jingle",
                            path=self.jingle.jingle_file, channel="jingle",                             reason=("end mark: jingle after this song"
                                    if force_break else
                                    f"automatic jingle (every "
                                    f"{self.jingle.interval} songs)"))
                        self.np_status.configure(text="🎵 Jingle...", text_color=COLORS["purple"])
                        return
                    # Jingle failed — reset and fall through to the next song.
                    self._active_source_channel = "music"
                    self._apply_mixer_volumes()
                    self.np_status.configure(text="", text_color=COLORS["text2"])

        if mode == "break_stop":
            # Nothing could air (no ad/jingle enabled, file missing, channel
            # muted) — the operator still asked the rundown to end here.
            self._stop_rundown("break_stop")
            return

        # --- Next song ---
        self._step_queue()

    def _stop_rundown(self, mode: str) -> None:
        """Halt the rundown where a song's end mode asked. Queue untouched.

        Stopping is not skipping: `current_index` stays on the song that just
        aired (the same end state as running out of queue), so the rundown
        does not silently swallow a song nobody heard.
        """
        self._stop_after_break = False
        self._playing = False
        try:
            self.audio_player.stop()
        except Exception:
            logging.getLogger("freq.gui").exception("stop after end mode failed")
        try:
            self.np_status.configure(text="⏹ Stopped — end of this song",
                                     text_color=COLORS["text3"])
        except Exception:
            pass
        self._refresh_queue_markers()
        self._update_now_playing()
        logging.getLogger("freq.gui").info(
            "rundown stopped after %r (end mode %s)",
            getattr(self._current_song_or_none(), "title", "?"), mode)

    def _next_track(self) -> None:
        self._asrun_end_active("skipped")
        self.audio_player.stop()
        self._playing = True
        self._step_queue()

    def _prev_track(self) -> None:
        self._asrun_end_active("skipped")
        self.audio_player.stop()
        self.rq.skip_previous()
        self._playing = True
        self._refresh_queue_markers()
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
        self._refresh_dual_device_list(names)

        # Restore selection — when the WASAPI engine is live, its route state
        # (FreQ-only endpoint, or "Default" after device loss) is the truth;
        # the pygame/PolicyConfig selection only applies to the pygame engine.
        if self.audio_player.engine == "wasapi" or getattr(self, "_wasapi_device", None):
            wasapi_ep = getattr(self, "_wasapi_device", None)
            if wasapi_ep:
                for d in output_devices:
                    if getattr(d, "endpoint_id", None) == wasapi_ep:
                        label = d.name if len(d.name) <= 40 else d.name[:37] + "..."
                        self.device_var.set(label)
                        return
            self.device_var.set("Default")
            return
        selected = self.audio_mgr.get_selected_device()
        if selected and self.audio_mgr.available:
            for n in names:
                if selected.name in n:
                    self.device_var.set(n)
                    return
        self.device_var.set("Default")

    def _on_device_lost(self, filepath: str = "") -> None:
        """The selected output device was unplugged (called on a player
        thread). Tk is single-threaded — 'after' from a worker thread is
        not delivered reliably, so only set a flag here; the main-loop
        progress poller notices it and runs the UI update on the GUI
        thread (see _update_progress)."""
        self._wasapi_device = None
        self._pending_device_lost = True

    def _drain_device_lost(self) -> None:
        if getattr(self, "_pending_device_lost", False):
            self._pending_device_lost = False
            self._on_device_lost_ui()

    def _on_device_lost_ui(self) -> None:
        try:
            if hasattr(self, "device_var"):
                self.device_var.set("Default")
                # CTkOptionMenu re-syncs its internal value back into the
                # variable during the next redraw, stomping the set above.
                # Re-assert after the widget settles, then re-render the
                # dropdown from engine state (OS default = "Default").
                self.after_idle(lambda: self.device_var.set("Default"))
                self.after(100, self._refresh_device_list)
        except Exception:
            pass
        if hasattr(self, "_show_toast"):
            try:
                self._show_toast("⚠️ The selected speaker was unplugged — moved to the system default")
            except Exception:
                pass

    def _on_device_selected(self, choice: str) -> None:
        """Route FreQ's output to the chosen speaker (WASAPI-first)."""
        if not self.audio_mgr.available:
            return
        if choice == "Default":
            self._wasapi_device = None
            if self.audio_player.engine == "wasapi":
                self.audio_player.reopen_output(None)
                self._show_toast("🔊 Output: system default (FreQ-only)")
            elif self.audio_mgr.restore_default():
                self.audio_player.reopen_output()
                self._show_toast("🔊 Output: system default")
            else:
                self._open_sound_settings()
            return
        # Search device by name
        for d in self.audio_mgr.get_output_devices():
            if d.name in choice or choice in d.name:
                # Preferred route: the C++ WASAPI engine plays on this
                # endpoint directly — the OS default stays untouched (works
                # even on Windows builds that cannot switch defaults).
                if d.endpoint_id and self.audio_player.move_to_endpoint(d.endpoint_id):
                    self._wasapi_device = d.endpoint_id
                    self._show_toast(f"🔊 Output: {d.name[:40]} (FreQ-only)")
                    break
                switched = (self.audio_mgr.switch_to_endpoint(d.endpoint_id)
                            if d.endpoint_id else self.audio_mgr.set_device(d.id))
                if switched:
                    self.audio_player.reopen_output()
                    self._show_toast(f"🔊 Output: {d.name[:40]}")
                else:
                    # Some Windows builds (trimmed images) lack the PolicyConfig
                    # class entirely — send the user to the Settings page.
                    self._show_toast("⚠️ This Windows build cannot switch the default speaker — Settings was opened")
                    self._open_sound_settings()
                break

    def _refresh_dual_device_list(self, names=None) -> None:
        """Fill the Dual Output dropdown (main device list + "Dual Off")."""
        menu = getattr(self, "dual_menu", None)
        if menu is None:
            return
        if names is None:
            # Derive the same labels _refresh_device_list builds.
            names = list(self.device_menu.cget("values") or ["Default"])
        values = ["Off"] + list(names)
        try:
            menu.configure(values=values)
        except Exception:
            pass
        if self.dual_var.get() not in values:
            self.dual_var.set("Off")

    def _shutdown_dual_output(self) -> None:
        """Release the Dual Output mirror (smoke-suite/close contract)."""
        try:
            self._dual_output.stop()
        except Exception:
            pass

    def _on_dual_device_selected(self, choice: str) -> None:
        """Smoke-suite alias for the dropdown command."""
        return self._on_dual_selected(choice)

    def _refresh_dual_list(self) -> None:
        """Smoke-suite alias for the device list refresh."""
        return self._refresh_dual_device_list()

    def _on_dual_selected(self, choice: str) -> None:
        """Start/stop the Dual Output mirror according to the dropdown."""
        endpoint = self._dual_endpoint_for(choice)
        if endpoint is None:
            self._dual_output.stop()
            self.dual_var.set("Off")
            return
        vol = self.dual_vol_var.get() / 100.0
        if self._dual_output.running and self._dual_output.endpoint != endpoint:
            ok = self._dual_output.change_endpoint(endpoint)
        else:
            ok = self._dual_output.start(endpoint, vol)
        if ok:
            self.dual_var.set(choice)
            self._show_toast(f"🙌 Dual Output → {choice[:32]} (mirror)")
        else:
            self.dual_var.set("Off")
            self._show_toast(f"⚠️ Dual Output failed: {self._dual_output.stats().get('error', 'unknown')[:60]}")

    def _dual_endpoint_for(self, label: str) -> Optional[str]:
        """Map the dual dropdown label to a WASAPI endpoint id."""
        if not label or label == "Off":
            return None
        for d in self.audio_mgr.get_output_devices():
            display = d.name if len(d.name) <= 40 else d.name[:37] + "..."
            if display == label:
                return getattr(d, "endpoint_id", None)
        # Fallback: prefix match (label might be the truncated form).
        for d in self.audio_mgr.get_output_devices():
            if d.name and (d.name in label or label in d.name):
                return getattr(d, "endpoint_id", None)
        return None

    def _on_dual_volume(self, value: float) -> None:
        """Per-output loudness for the mirrored speaker (0–200%), live.

        Slider-only: it must never touch the shell. The 1016.1 merge left
        the _open_sound_settings body inside this handler, so every drag
        step launched "ms-settings:sound" — the Windows Settings app
        popping up while moving the second-output volume.
        """
        pct = int(value)
        self.dual_vol_label.configure(text=f"{pct}%")
        self._dual_output.volume = value / 100.0

    def _open_sound_settings(self) -> None:
        """Open the Windows sound settings page (manual output picker)."""
        try:
            import os
            os.startfile("ms-settings:sound")  # noqa: S606 — user-initiated
        except Exception:
            pass

    # ═══════════════════════════════════════
    # Volume Control
    # ═══════════════════════════════════════
    # ── Mouse wheel on a volume bar ──
    WHEEL_VOLUME_STEP = 5.0   # percent per wheel notch

    def _bind_wheel_volume(self, slider, on_change, step=None,
                           low=0.0, high=100.0) -> None:
        """Let the mouse wheel nudge a volume slider (one notch = ±step).

        Windows/macOS send <MouseWheel> with ``delta`` a multiple of 120
        (some mice send partial/high-resolution deltas); X11 sends
        <Button-4>/<Button-5>. Returning "break" stops the notch from also
        scrolling the sidebar behind the fader.
        """
        step = self.WHEEL_VOLUME_STEP if step is None else step

        def _wheel(event):
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:                       # X11 wheel buttons
                delta = 120 if getattr(event, "num", 0) == 4 else -120
            notches = delta / 120.0
            if abs(notches) < 1:                 # high-res / tilt wheel
                notches = 1 if delta > 0 else -1
            try:
                current = float(slider.get())
            except Exception:
                return "break"
            nxt = min(high, max(low, current + step * notches))
            if nxt != current:
                slider.set(nxt)
                on_change(nxt)
            return "break"

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                # add="+" keeps CTk's own canvas/button bindings intact.
                slider.bind(sequence, _wheel, add="+")
            except Exception:
                pass

    def _on_volume_change(self, value: float) -> None:
        """Adjust volume when slider moves"""
        vol = int(value)
        self.volume_label.configure(text=f"{vol}%")
        self._apply_mixer_volumes()
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
    def _on_download_progress(self, status: str, pct: float, download_index: Optional[int] = None) -> None:
        """Update progress bar during YouTube download"""
        try:
            if status == "downloading":
                self._download_mode = True
                # Remember the transfer so the queue can push the clock times
                # after the row on air by whatever is left of it, and so the
                # operator can read the finish time instead of a bare %.
                state = self.__dict__.get("_dl_state") or {}
                if (state.get("index") != download_index
                        or state.get("status") != status):
                    state = {"index": download_index, "status": status,
                             "started": time.time(), "pct": 0.0}
                state["pct"] = float(pct)
                self._dl_state = state
                self._convert_started = None      # a new transfer, new timing
                eta = self._download_stall_seconds()
                suffix = (f" · ready ~{self._clock_time(time.time() + eta)}"
                          if eta > 0 else "")
                self.np_status.configure(text=f"📥 Downloading... {pct:.0f}%{suffix}",
                                          text_color=COLORS["orange"])
                self.np_progress.set(pct)
                self.np_time_cur.configure(text=f"{pct:.0f}%")
                self.np_time_total.configure(text="100%")
                self._maybe_refresh_download_plan()
            elif status == "processing":
                # yt-dlp reports no percentage while ffmpeg extracts the audio,
                # so the rundown shows the elapsed clock and — once a
                # conversion has actually been measured this session — an
                # estimated finish time. It is labelled "est.", never presented
                # as a fact.
                self._dl_state = {"index": download_index, "status": status,
                                  "started": time.time(), "pct": 0.0}
                self._convert_started = time.time()
                try:
                    idx = self.rq.current_index
                    song = self.rq.queue[idx] if 0 <= idx < len(self.rq.queue) else None
                    self._convert_song_secs = _safe_seconds(
                        getattr(song, "duration", 0.0))
                except Exception:
                    self._convert_song_secs = 0.0
                self.np_status.configure(text="🔄 Converting... (queue waits)",
                                          text_color=COLORS["purple"])
                self.np_progress.set(100)
                self._maybe_refresh_download_plan()
            elif status == "cached":
                self._finish_convert_measure()
                self._download_mode = False
                self._dl_state = {}
                self._maybe_refresh_download_plan()
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
                self._finish_convert_measure()
                self._download_mode = False
                self._maybe_refresh_download_plan()
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
                # A failed download should not block the queue. Ignore stale
                # callbacks if the user has already selected another song.
                if download_index is not None and self.rq.current_index != download_index:
                    return
                # As-run: the item never aired — record why, not just silence.
                self._asrun_end_active("error", kind="music")
                self._finish_convert_measure()
                self._download_mode = False
                self._dl_state = {}      # a dead transfer waits for nobody
                self._maybe_refresh_download_plan()
                self.np_status.configure(text="❌ Download failed — skipping",
                                          text_color=COLORS["red"])
                self.btn_play.configure(state="normal")
                self.np_progress.set(0)
                self.np_time_cur.configure(text="0:00")
                self.np_time_total.configure(text="0:00")
                failed_index = self.rq.current_index
                if not self.rq.queue or len(self.rq.queue) <= 1:
                    self._playing = False
                    self._refresh_queue()
                    self._update_now_playing()
                    return
                self.audio_player.stop()
                verdict = self.rq.skip_next()
                self._refresh_queue()
                self._update_now_playing()
                if verdict == "note":
                    self._stop_at_note()
                    return
                if verdict == "playing" and self.rq.current_index != failed_index:
                    self.after(150, lambda: self._play_index(self.rq.current_index))
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
        # A YouTube Mix / radio link (list=RD…, start_radio=1 — what the
        # share button gives you on a normal video page) carries a perfectly
        # good video id. yt-dlp refuses the LIST (“This playlist type is
        # unviewable”), so routing it into the playlist flow ended in
        # “No videos found in playlist” for a link that plainly works in a
        # browser. The user pasted a track: add that track.
        mix_note = ""
        if YouTubeManager.is_playlist_url(url):
            single = YouTubeManager.video_url_from(url)
            if single and YouTubeManager.is_mix_playlist(url):
                url = single
                mix_note = "🎧 YouTube Mix — added the video itself"
            else:
                # Auto-route to the playlist flow (watch?v=..&list=PL.. supported)
                self.yt_playlist_entry.delete(0, "end")
                self.yt_playlist_entry.insert(0, url)
                self._add_youtube_playlist()
                return

        self.btn_yt_add.configure(state="disabled", text="⏳ Loading...")

        def _worker():
            try:
                song = YouTubeManager.extract_video_info(url)
                if song:
                    self.after(0, lambda: self._on_yt_added(song))
                    if mix_note:
                        self.after(0, lambda: self._show_toast(mix_note, duration=5000))
                else:
                    self.after(0, lambda: messagebox.showerror("Error", "Cannot fetch video info"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self.btn_yt_add.configure(state="normal", text="➕ Add Video"))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_random_insert_toggle(self) -> None:
        """Mirror the 🎲 switch into the plain flag the add paths read."""
        try:
            self._queue_random_insert = bool(self._random_insert_var.get())
        except Exception:
            self._queue_random_insert = False
        self._show_toast(
            "\U0001f3b2 Random insert ON — new songs land somewhere in the "
            "upcoming queue" if self._queue_random_insert
            else "\u27a1 Random insert OFF — new songs go to the end",
            duration=3000)

    def _queue_random_insert_on(self) -> bool:
        """Is the Add tab set to scatter new songs through the queue?"""
        return bool(getattr(self, "_queue_random_insert", False))

    # ── Live stream (.m3u8) ──
    @staticmethod
    def _normalize_stream_url(text: str) -> str:
        """Clean a pasted stream link, or return "" when it is unusable.

        Accepts an http(s) address (an .m3u8 HLS playlist, or any feed ffmpeg
        can open) and an existing local ``.m3u8`` file. Quotes and angle
        brackets — added by "copy link" buttons and mail clients — are
        stripped so a pasted link is never silently rejected.
        """
        url = str(text or "").strip().strip("<>\"'").strip()
        if not url:
            return ""
        low = url.lower()
        if low.startswith(("http://", "https://")):
            # A scheme with no host ("https://") is not a link: without this
            # check it would be queued and only fail on air.
            from urllib.parse import urlsplit
            return url if urlsplit(url).netloc else ""
        if low.startswith("file://"):
            path = url[7:].lstrip("/")
            return path if os.path.isfile(path) else ""
        return url if os.path.isfile(url) else ""

    @staticmethod
    def _stream_default_name(url: str) -> str:
        """A readable label from a stream link: host plus playlist name."""
        text = str(url)
        if not text.lower().startswith(("http://", "https://")):
            # Local playlist: the file name is all the label worth having.
            name = Path(text).name
            return name[:-5] if name.lower().endswith(".m3u8") else (name or "Live stream")
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(text)
        except Exception:
            return "Live stream"
        host = parts.netloc or ""
        tail = (parts.path or "").rstrip("/").rsplit("/", 1)[-1]
        if tail.lower().endswith(".m3u8"):
            tail = tail[:-5]
        name = " · ".join(bit for bit in (host, tail) if bit)
        return name or "Live stream"

    def _add_stream(self) -> None:
        """Queue a live HLS (.m3u8) signal from its link."""
        raw = self.stream_url_entry.get()
        if not str(raw or "").strip():
            messagebox.showwarning("Add stream", "Paste an .m3u8 link first.")
            return
        url = self._normalize_stream_url(raw)
        if not url:
            messagebox.showerror(
                "Add stream",
                "That is not a usable stream link.\n\n"
                "Paste an http:// or https:// .m3u8 (HLS) address, or point \n"
                "at a local .m3u8 file.")
            return
        if not FFMPEG_AVAILABLE:
            messagebox.showerror(
                "FFmpeg required",
                "Live streams are pulled through FFmpeg.\n"
                "Install it (or drop ffmpeg.exe beside the app) and retry.")
            return
        title = (self.stream_name_entry.get().strip()
                 or self._stream_default_name(url))
        slot = self._add_to_queue(Song(
            title=title,
            artist="Live stream",
            source="stream",
            url=url,
            duration=0.0,          # live: no known length
        ))
        self.stream_url_entry.delete(0, "end")
        self.stream_name_entry.delete(0, "end")
        self._refresh_queue()
        self._update_now_playing()
        self._show_toast(f"📡 Live stream added (#{slot + 1})")

    def _add_to_queue(self, song: Song) -> int:
        """Add one song the way the Add tab is currently set up, and say where.

        Every add path (YouTube video, playlist batch, file, drag-and-drop,
        library, manual form) funnels through here, so the 🎲 switch means
        exactly the same thing wherever the song came from. Returns the slot
        the song landed in.
        """
        if self._queue_random_insert_on():
            return self.rq.insert_song_at_random(song)
        self.rq.add_song(song)
        return len(self.rq.queue) - 1

    def _on_yt_added(self, song: Song) -> None:
        self._add_to_queue(song)
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
                    # A Mix / radio list cannot be listed, but the video the
                    # link opens with is still playable — import that instead
                    # of failing on a link that works fine in a browser.
                    single = YouTubeManager.video_url_from(url)
                    if single:
                        song = YouTubeManager.extract_video_info(single)
                        if song:
                            self.after(0, lambda s=song: self._on_yt_added(s))
                            self.after(0, lambda: self.yt_playlist_entry.delete(0, "end"))
                            self.after(0, lambda: self._show_toast(
                                "🎧 This playlist can't be listed (YouTube Mix) "
                                "— added the video itself", duration=5000))
                            return
                    self.after(0, lambda: messagebox.showerror(
                        "Error",
                        "No videos found in playlist\n\n"
                        "YouTube Mix / radio links (list=RD…) cannot be "
                        "imported as a playlist — paste the video link "
                        "instead.\n\n"
                        "Otherwise the playlist may be empty, private or "
                        "deleted; check that the link opens in a browser "
                        "while logged out."))
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
        """Step 2: Ask what to import (main thread), then fetch that part.

        The picker opens for every playlist with something to choose: all N,
        the FIRST video only, or a range. A one-video playlist has nothing to
        ask about, so it imports directly as before.
        """
        first_title = ""
        if entries:
            first_title = str((entries[0] or {}).get("title") or "").strip()
        if total > 1:
            dialog = PlaylistRangeDialog(self, total, first_title=first_title)
            self.wait_window(dialog)
            result = dialog.result
            if result is None:
                self.btn_yt_playlist.configure(state="normal", text="📋 Add Playlist")
                return
            elif result == "all":
                start, end = 1, total
            elif result == "first":
                # "Add only first video in the playlist"
                start, end = 1, 1
            else:
                start, end = result
        else:
            start, end = 1, total

        count = end - start + 1
        self.btn_yt_playlist.configure(
            state="disabled",
            text=f"⏳ Loading {count} song{'s' if count != 1 else ''}...")

        def _fetch_worker():
            try:
                songs = YouTubeManager.extract_playlist_range(entries, start, end)
                if songs:
                    # ⚠️ worker thread: only queue state and the plain 🎲 flag
                    # are touched here, never a Tk widget.
                    for s in songs:
                        self._add_to_queue(s)
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
        where = ("  \U0001f3b2 scattered" if self._queue_random_insert_on() else "")
        self._show_toast(f"✅ Added {count} song{'s' if count != 1 else ''} from playlist{where}  (Queue: {total} songs)", duration=4000)
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

        self._add_to_queue(Song(title=title, artist=artist, duration=dur, source="local"))
        self.manual_title.delete(0, "end")
        self.manual_artist.delete(0, "end")
        self.manual_duration.delete(0, "end")
        self._refresh_queue()
        self._refresh_library()

    def _probe_duration(self, path: str) -> float:
        """Length of a local audio file in seconds (0.0 when unknown)."""
        try:
            from mutagen import File as MutagenFile
            mf = MutagenFile(path)
            # NB: mutagen's file objects are dict subclasses, so an untagged
            # file is FALSY — test None, not truthiness, or every wav reports 0.
            info = getattr(mf, "info", None)
            if info is not None and getattr(info, "length", 0):
                return float(info.length)
        except Exception:
            try:
                import pygame
                if pygame.mixer.get_init():
                    return float(pygame.mixer.Sound(path).get_length())
            except Exception:
                pass
        return 0.0

    def _pick_file(self) -> None:
        """Queue one or MANY local audio files picked in the file dialog.

        Ctrl/Shift-click in the dialog picks several at once; every file goes
        through ``_add_to_queue``, so the 🎲 switch scatters them exactly like
        any other add path. Nothing is written to disk.
        """
        from pathlib import Path

        filetypes = [
            ("Audio files", "*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.wma *.opus"),
            ("MP3", "*.mp3"),
            ("WAV", "*.wav"),
            ("OGG", "*.ogg"),
            ("FLAC", "*.flac"),
            ("All files", "*.*"),
        ]
        picked = filedialog.askopenfilenames(
            title="Select Audio Files (Ctrl/Shift-click for several)",
            filetypes=filetypes,
        )
        # Tk returns a string when nothing was selected; some old builds hand
        # back a bare string even for one file.
        if isinstance(picked, str):
            picked = (picked,) if picked else ()
        if not picked:
            return

        added: list[str] = []
        for raw in picked:
            path = str(raw).strip()
            if not path:
                continue
            title = Path(path).stem  # Filename without extension
            self._add_to_queue(Song(
                title=title,
                source="file",
                file_path=path,
                duration=self._probe_duration(path),
            ))
            added.append(title)
        if not added:
            return

        if len(added) == 1:
            self.file_label.configure(text=f"✅ {added[0]}")
        else:
            self.file_label.configure(
                text=f"✅ {len(added)} files: {added[0]} +{len(added) - 1}")
        self._refresh_queue()
        self._refresh_library()

        total = len(self.rq.queue)
        where = ("  \U0001f3b2 scattered" if self._queue_random_insert_on() else "")
        if len(added) == 1:
            self._show_toast(f"✅ Added: {added[0]}{where}  "
                             f"(Queue: {total} songs)", duration=3000)
        else:
            self._show_toast(f"✅ Added {len(added)} files{where}  "
                             f"(Queue: {total} songs)", duration=4000)

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
    def _apply_queue_shift_arrow(self, direction: int) -> str:
        """Shift + Up/Down inside the queue: extend the selection range

        The anchor is the row the user last Shift+clicked (or single-clicked).
        Shift+Down extends DOWN to the end of the list, Shift+Up extends UP
        to the first entry — the range keeps growing while the key repeats
        and the anchor never moves.
        """
        n = len(self.rq.queue)
        if n <= 0:
            return "break"
        anchor = (self._queue_shift_anchor
                  if self._queue_shift_anchor is not None
                  else (self._queue_selection[0]
                        if self._queue_selection
                        else self._queue_selected))
        if anchor is None:
            anchor = 0
        anchor = max(0, min(n - 1, anchor))
        last = (self._queue_selection[-1]
                if self._queue_selection else anchor)
        target = min(n - 1, last + direction) if direction > 0 else max(0, last + direction)
        if target == last:
            return "break"
        lo, hi = min(anchor, target), max(anchor, target)
        self._queue_selection = list(range(lo, hi + 1))
        self._queue_selected = target
        self._queue_shift_anchor = anchor
        self._update_queue_selection_paint()
        return "break"

    # ── Keyboard navigation ──────────────────────────────────────
    #
    # The list is virtualized: an off-screen row has no widget at all, so
    # "go to row 900" is a scroll-fraction move, never a widget lookup.
    # Movement works in POSITIONS (which follow the search filter) while the
    # selection keeps QUEUE INDEXES (Remove / Move Up / Move Down need them).

    def _on_queue_canvas_key(self, event):
        """Arrow / PageUp / PageDown / Home / End inside the focused list."""
        key = event.keysym
        if key == "Up":
            return self._queue_nav_rows(-1)
        if key == "Down":
            return self._queue_nav_rows(1)
        if key == "Prior":
            return self._queue_nav_page(-1)
        if key == "Next":
            return self._queue_nav_page(1)
        if key == "Home":
            return self._queue_nav_edge(-1)
        if key == "End":
            return self._queue_nav_edge(1)
        return None

    def _on_queue_canvas_enter(self, event=None):
        """Enter plays the selected row — the keyboard's double-click."""
        index = self._queue_selection_or_none()
        if index is not None:
            self._on_queue_double_click(index)
        return "break"

    def _queue_nav_rows(self, delta: int, extend: bool = False):
        """Move the selection ``delta`` rows through the VISIBLE list."""
        view = getattr(self, "_queue_view", [])
        if not view:
            return "break"
        pos = self._queue_pos_by_index.get(self._queue_selected)
        if pos is None:
            # Nothing selected yet: start at the playing row when the filter
            # keeps it visible, otherwise step in from the edge we came from.
            pos = self._queue_pos_by_index.get(self.rq.current_index)
            if pos is None:
                pos = -1 if delta > 0 else len(view)
        target = max(0, min(len(view) - 1, pos + delta))
        self._queue_select_index(view[target][0], extend=extend)
        return "break"

    def _queue_nav_page(self, direction: int):
        """PageUp/PageDown: a screenful, so a long queue is a few keys away."""
        rows = max(2, self._queue_viewport_rows() - 1)
        return self._queue_nav_rows(direction * rows)

    def _queue_nav_edge(self, which: int):
        """Home / End."""
        view = getattr(self, "_queue_view", [])
        if not view:
            return "break"
        self._queue_select_index(view[0][0] if which < 0 else view[-1][0])
        return "break"

    def _queue_select_index(self, index, extend: bool = False) -> bool:
        """Select a queue row, repaint it in place, and keep it on screen."""
        n = len(self.rq.queue)
        if n <= 0 or index is None:
            return False
        index = max(0, min(n - 1, int(index)))
        if extend:
            anchor = self._queue_shift_anchor
            if anchor is None:
                anchor = (self._queue_selected
                          if self._queue_selected is not None else index)
            anchor = max(0, min(n - 1, anchor))
            anchor_pos = self._queue_pos_by_index.get(anchor)
            target_pos = self._queue_pos_by_index.get(index)
            if anchor_pos is None or target_pos is None:
                self._queue_selection = [index]
            else:
                lo, hi = min(anchor_pos, target_pos), max(anchor_pos, target_pos)
                # A filtered list is not contiguous in queue indexes, so the
                # range is built from the rows actually on screen.
                self._queue_selection = [self._queue_view[p][0]
                                         for p in range(lo, hi + 1)]
            self._queue_shift_anchor = anchor
        else:
            self._queue_selection = [index]
            self._queue_shift_anchor = None
        self._queue_selected = index
        self._queue_scroll_into_view(index)
        self._update_queue_selection_paint()
        return True

    def _queue_scroll_into_view(self, index) -> bool:
        """Scroll so row ``index`` sits fully inside the viewport.

        One yview_moveto on the outer canvas instead of per-widget lookups,
        so it costs the same with 60 rows or 6,000 — the drawn window is
        repainted immediately rather than waiting for the 40 ms poll.
        """
        view = getattr(self, "_queue_view", [])
        n = len(view)
        pos = getattr(self, "_queue_pos_by_index", {}).get(index)
        if not n or pos is None:
            return False
        rows = self._queue_viewport_rows()
        top = self._queue_visible_top()
        if top <= pos < top + rows:
            return True                    # already on screen — do not jump
        new_top = pos if pos < top else pos - rows + 1
        new_top = max(0, min(n - 1, new_top))
        parent = getattr(self.queue_frame, "_parent_canvas", None)
        if parent is None:
            return False
        try:
            parent.yview_moveto(new_top / n)
        except Exception:
            return False
        self._draw_queue_window(force=True)
        return True

    def _queue_jump_to_playing(self):
        """Ctrl+J / the 🎯 button: select and reveal the song on air."""
        view = getattr(self, "_queue_view", [])
        if not view:
            self._show_toast("🎯 The queue is empty")
            return "break"
        index = getattr(self.rq, "current_index", None)
        if index is None or index < 0 or index >= len(self.rq.queue):
            self._show_toast("🎯 Nothing is playing right now")
            return "break"
        if index not in self._queue_pos_by_index:
            self._show_toast("🎯 The playing song is hidden by the search filter")
            return "break"
        self._queue_select_index(index)
        self._show_toast(f"🎯 #{index + 1} — the song on air")
        return "break"

    def _update_queue_selection_paint(self) -> None:
        """Repaint the selection without rebuilding the queue list.

        Only the drawn rows exist as canvas items, so a held-down arrow key
        costs one viewport repaint per step no matter how long the queue is;
        rows scrolled into view later read the selection as they are drawn.
        """
        for pos in list(getattr(self, "_queue_items", {})):
            self._queue_paint_row(pos)
        self._update_queue_filter_btn()

    def _queue_selection_or_none(self) -> Optional[int]:
        """Primary selection for legacy single-select call sites."""
        if self._queue_selection:
            return self._queue_selection[-1]
        return self._queue_selected

    def _remove_selected(self) -> None:
        if self._queue_selected is None:
            messagebox.showinfo("Info", "Please select a song from the queue first")
            return
        targets = self._queue_selection or [self._queue_selected]
        self._push_queue_undo("remove" if len(targets) == 1
                              else f"remove {len(targets)} rows")
        for i in sorted(targets, reverse=True):
            self.rq.remove_song(i)
        self._queue_selected = None
        self._queue_selection = []
        self._refresh_queue()
        self._update_now_playing()

    def _move_selected(self, direction: int) -> None:
        if self._queue_selected is None:
            messagebox.showinfo("Info", "Please select a song from the queue first")
            return
        sel = self._queue_selection or [self._queue_selected]
        # Move the block as one: quick sort/ids preserve visual order so the
        # moved songs keep their internal arrangement.
        sel_sorted = sorted(sel)
        start = sel_sorted[0] + direction
        if not self.rq._valid_index(start):
            return
        self._push_queue_undo("move")
        if direction > 0:
            # Moving down: rename top-down avoids non-overlapping indices.
            for old in reversed(sel_sorted):
                self.rq.move_song(old, old + direction)
        else:
            for old in sel_sorted:
                self.rq.move_song(old, old + direction)
        self._queue_selected = start
        self._queue_selection = sorted(
            start + (o - sel_sorted[0]) for o in sel_sorted)
        self._refresh_queue()

    def _clear_queue(self) -> None:
        if messagebox.askyesno("Confirm", "Clear entire queue?"):
            self._push_queue_undo("clear queue")
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
            ctk.CTkLabel(info, text=song.artist, font=ctk.CTkFont(size=10),
                          text_color=COLORS["text2"], anchor="w").pack(anchor="w", fill="x")

        ctk.CTkLabel(inner, text=self._fmt_dur(song.duration), width=45,
                      font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"], anchor="e").pack(side="right", pady=8)

        s = song
        ctk.CTkButton(inner, text="➕", width=28, height=24,
                       fg_color=COLORS["green"], text_color="#000",
                       hover_color=COLORS["green_hover"],
                       font=ctk.CTkFont(size=10),
                       command=lambda s=s: self._add_from_library(s)).pack(side="right", padx=(4, 0), pady=8)

    def _search_library(self) -> None:
        query = self.search_entry.get().strip()
        self._refresh_library(query)

    def _add_from_library(self, song: Song) -> None:
        """➕ in the library: goes through the Add tab's 🎲 setting too."""
        self._add_to_queue(song)             # same song object → same id
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
                           fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
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
        self.audio_player.crossfade_enabled = self.crossfade.enabled
        self.audio_player.crossfade_duration = max(1.0, self.crossfade.duration or 4.0)
        if not self.crossfade.enabled:
            self.audio_player.clear_next_track()
        self.btn_crossfade.configure(fg_color=COLORS["accent"] if self.crossfade.enabled else "transparent",
                                     text_color="#fff" if self.crossfade.enabled else COLORS["text"])
        if hasattr(self, "_toast"):
            try:
                self._toast("Crossfade ON — songs blend seamlessly" if self.crossfade.enabled
                            else "Crossfade OFF")
            except Exception:
                pass

    def _toggle_auto_gain(self) -> None:
        self._auto_gain_enabled = not self._auto_gain_enabled
        try:
            self.audio_player.set_auto_gain(self._auto_gain_enabled, self._auto_gain_target)
        except Exception:
            pass
        self.btn_auto_gain.configure(fg_color=COLORS["accent"] if self._auto_gain_enabled else "transparent",
                                     text_color="#fff" if self._auto_gain_enabled else COLORS["text"])
        if hasattr(self, "_toast"):
            try:
                self._toast("Auto-Gain ON — every track plays at the same loudness"
                            if self._auto_gain_enabled else "Auto-Gain OFF")
            except Exception:
                pass

    def _on_waveform_seek(self, fraction: float) -> None:
        """Click on the waveform jumps to that position in the track."""
        try:
            dur = self.audio_player.get_duration()
        except Exception:
            return
        if dur and dur > 0.5:
            self.audio_player.seek(max(0.0, min(dur - 0.1, fraction * dur)))

    def _queue_crossfade(self, song, index: int, on_finish: bool = True) -> None:
        """Hand the upcoming track to the player's crossfade engine.

        Only local files crossfade (YouTube/mic playback keeps the classic
        finish-callback path). ``handoff`` fires when the player seamlessly
        switches into this track, or via the normal finish callback when
        crossfade was off/too late to arm.
        """
        handoff = None
        if (self.crossfade.enabled and self.audio_player.crossfade_available
                and song.file_path and os.path.isfile(song.file_path)):
            handoff = self._advance_crossfaded
        if handoff is not None:
            self.audio_player.set_next_track(
                song.file_path, duration=song.duration or 0.0,
                index=index, handoff=handoff,
            )
        elif on_finish:
            # Keep the classic path: the player's monitor calls on_finish,
            # which lands in _on_track_finished via after(0).
            pass

    def _arm_crossfade_for_next(self, current_index: int) -> None:
        """Peek at the queue and arm the crossfade engine for the next song.

        Runs shortly after a track starts. Ads and jingles keep the classic
        finish-callback path — they only make sense as standalone segments.
        """
        try:
            if (not self.crossfade.enabled
                    or not self.audio_player.crossfade_available
                    or not self.audio_player.is_playing):
                return
            if (self.commercial.enabled and self.commercial.ads
                    and self.commercial.song_counter + 1 >= self.commercial.interval):
                return
            if (self.jingle.enabled and self.jingle.jingle_file
                    and self.jingle.song_counter + 1 >= self.jingle.interval):
                return
            # A row's end mark (break / stop / break+stop) ends the rundown at
            # THIS boundary — blending the next song in would air it anyway.
            if self._song_end_mode(self.rq.queue[current_index]) != "auto":
                return
            nxt_index = self.rq.modes.next_index(current_index, len(self.rq.queue))
            if nxt_index is None or nxt_index == current_index:
                return
            song = self.rq.queue[nxt_index]
            if song is None or song.source in ("youtube", "mic"):
                return
            self._queue_crossfade(song, nxt_index)
        except Exception as e:
            print(f"  ⚠️  Crossfade arm failed: {e}")

    def _advance_crossfaded(self, b_offset: float = 0.0, index: int = -1) -> None:
        """Called by the player the instant the crossfade mix becomes audible.

        Advances queue state + UI to the track that is already playing,
        mirroring what _play_index does after starting a song — but without
        touching the mixer (the audio never stops).
        """
        def _apply():
            try:
                if index >= 0 and index < len(self.rq.queue):
                    self.rq.play(index)
                else:
                    verdict = self.rq.skip_next()
                    if verdict == "note":
                        self._stop_at_note()
                        return
                    if verdict != "playing":
                        self._playing = False
                        self._refresh_queue_markers()
                        self._update_now_playing()
                        return
                self._playing = True
                self._jingle_playing = False
                self._ad_playing = False
                self._active_source_channel = "music"
                self._apply_mixer_volumes()
                self._refresh_queue()
                self._update_now_playing()
                current = self.rq.current
                if current:
                    self.np_time_total.configure(text=current.duration_str)
                    # (Normalizer gain already applied by the player's mixer chain.)
                    if streamer.is_streaming and current.file_path:
                        streamer.on_track_change(current.title, current.artist, current.file_path)
                    self.after(100, lambda fp=current.file_path: self._load_waveform(fp))
            except Exception as e:
                print(f"  ⚠️  Crossfade UI handoff failed: {e}")
        self.after(0, _apply)

    def _toggle_auto_cue(self) -> None:
        self._auto_cue = not getattr(self, "_auto_cue", False)
        self.audio_player.auto_cue = self._auto_cue
        self.btn_auto_cue.configure(
            fg_color=COLORS["accent"] if self._auto_cue else "transparent",
            text_color="#fff" if self._auto_cue else COLORS["text"],
        )
        if self._auto_cue:
            self._show_toast("⏱ Auto-cue on — silence at the start of each song is skipped")

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
            self.after(50, self._update_visualizer)
        else:
            if self.visualizer.canvas.winfo_viewable():
                self.visualizer.stop()
                self.visualizer.canvas.pack_forget()
            else:
                self.visualizer.canvas.pack(side="left", padx=10, pady=5)
                self.visualizer.start()
                self.after(50, self._update_visualizer)

    def _update_visualizer(self) -> None:
        """Update visualizer with live spectrum from the PCM tap."""
        if self.visualizer and self.visualizer.canvas.winfo_viewable():
            try:
                self.visualizer.update_pcm(audio_meter.latest_pcm(4096))
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
        ctk.CTkLabel(header, text="⏰  Scheduler", font=ctk.CTkFont(size=20, weight="bold"),
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

    def _new_playlist(self) -> None:
        """Start an empty playlist. Library, history and presets are kept.

        Nothing is written here: the open file keeps its bytes on disk until
        the operator saves, and ``new_playlist`` points the save target back
        at the default file so a following Save cannot overwrite the playlist
        that was being edited a moment ago.
        """
        if self.rq.queue:
            if not messagebox.askyesno(
                    "New playlist",
                    f"Start a new playlist?\n\n"
                    f"The {len(self.rq.queue)} song(s) in the queue will be cleared "
                    "(nothing is written to disk)\n"
                    "Library and history stay · the open file is not overwritten"):
                return
        if self._playing:
            try:
                self.audio_player.stop()
            except Exception:
                logging.getLogger("freq.gui").exception(
                    "stopping playback for a new playlist failed")
            self._playing = False
        self.rq.new_playlist()
        self._queue_selected = None
        self._refresh_queue()
        self._update_now_playing()
        self._refresh_library()
        self._show_toast("🆕 New playlist — the queue is empty "
                         "(library/history kept)")

    def _import_playlist(self) -> None:
        """Add the songs of one or more other .freq playlists to this queue.

        Import never replaces anything: the songs are appended after what is
        already queued (so the item on air keeps its slot), and duplicates are
        skipped by identity — the same file path, YouTube URL or mic segment —
        both against the queue and within the batch. A file that cannot be read
        is named instead of silently contributing zero songs.
        """
        paths = filedialog.askopenfilenames(
            title="Import FreQ playlist(s)",
            filetypes=[("FreQ Playlist", "*.freq"), ("All files", "*.*")],
        )
        if not paths:
            return
        songs: list = []
        unreadable: list[str] = []
        for raw in paths:
            try:
                found = self.rq.read_playlist(raw)
            except Exception:
                logging.getLogger("freq.gui").exception(
                    "cannot read playlist %r", raw)
                found = []
            if found:
                songs.extend(found)
            else:
                unreadable.append(Path(raw).name)
        if not songs:
            messagebox.showwarning(
                "Import playlist",
                "No songs could be read from the selected file(s):\n"
                + "\n".join(unreadable or ["(no files)"]))
            return
        try:
            result = self.rq.import_songs(songs)
        except Exception:
            logging.getLogger("freq.gui").exception("playlist import failed")
            messagebox.showerror("Import playlist",
                                 "Import failed — see the log for details")
            return
        self._queue_selected = None
        self._refresh_queue()
        self._refresh_library()
        self._refresh_presets()
        parts = [f"📥 Imported {result['added']} song(s)"]
        if result["skipped"]:
            parts.append(f"skipped {result['skipped']} duplicate(s)")
        if unreadable:
            parts.append(f"{len(unreadable)} unreadable file(s)")
        self._show_toast(" · ".join(parts))

    def _save(self) -> None:
        """Save .freq file (use existing path, or Save As)"""
        self._sync_anthem_times()
        settings = self._collect_settings()
        if self.rq._save_path.exists() or self.rq._save_path.name != "radio_queue.freq":
            self.rq.save_to_file(settings=settings)
            self._remember_freq_snapshot(settings, source="saved")
            self._refresh_freq_roundtrip()
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
        self._remember_freq_snapshot(settings, source="saved")
        self._refresh_freq_roundtrip()
        messagebox.showinfo("Success", f"💾 Saved: {Path(path).name}")

    # ── Settings save/restore ──
    def _collect_audio_output(self) -> dict:
        """Remember the selected output speaker for the .freq preset."""
        try:
            device = self.audio_mgr.get_selected_device()
        except Exception:
            device = None
        out = {
            "endpoint_id": getattr(device, "endpoint_id", None),
            "name": getattr(device, "name", None),
        }
        try:
            st = self._dual_output.stats()
            # Contract used by the smoke suite: dual_output.enabled always
            # present (False when the mirror is off), endpoint when running.
            out["dual_output"] = {
                "enabled": bool(st.get("running")),
                "endpoint": st.get("endpoint") or "",
                "volume_pct": int(self.dual_vol_var.get()) if st.get("running") else 100,
            }
        except Exception:
            pass
        return out

    def _apply_remembered_output(self, ao: dict) -> None:
        """Re-apply the output speaker remembered in a .freq preset."""
        if not ao:
            return
        endpoint_id = ao.get("endpoint_id")
        name = ao.get("name")
        if not endpoint_id and not name:
            return
        # Preferred: route the C++ WASAPI engine straight to the remembered
        # endpoint — works on every Windows build and leaves the OS default
        # device alone.
        if endpoint_id and self.audio_player.move_to_endpoint(endpoint_id):
            self._wasapi_device = endpoint_id
            try:
                self._refresh_device_list()
            except Exception:
                pass
            return
        # Fallbacks: pygame stays the engine — switch the OS default.
        try:
            current = self.audio_mgr.get_selected_device()
        except Exception:
            current = None
        if current and endpoint_id and getattr(current, "endpoint_id", None) == endpoint_id:
            return
        switched = False
        if endpoint_id and self.audio_mgr.wasapi_available:
            switched = self.audio_mgr.switch_to_endpoint(endpoint_id)
        if not switched and name:
            # Endpoint may have been replugged with a new id — match by name.
            switched = self.audio_mgr.switch_by_name(name)
        if switched:
            self.audio_player.reopen_output()
        # Keep the dropdown in sync with the (possibly new) default.
        try:
            self._refresh_device_list()
        except Exception:
            pass
        self._apply_remembered_dual(ao)

    def _apply_remembered_dual(self, ao: dict) -> None:
        """Restore Dual Output (device + per-output volume) from a .freq."""
        ep = (ao or {}).get("dual_endpoint_id")
        dcfg = (ao or {}).get("dual_output") or {}
        if not ep and not dcfg:
            return
        # Disabled entry = no-op, exactly as the mirror contract demands.
        if dcfg and not dcfg.get("enabled", False):
            return
        ep = ep or dcfg.get("endpoint") or ""
        if not ep:
            return
        vol = float(dcfg.get("volume_pct", (ao or {}).get("dual_volume_pct", 100)))
        try:
            self.dual_vol_var.set(vol)
            self.dual_vol_label.configure(text=f"{int(vol)}%")
        except Exception:
            pass
        try:
            self._dual_output.volume = vol / 100.0
        except Exception:
            pass
        # Match the endpoint to a current device label for the dropdown.
        for d in self.audio_mgr.get_output_devices():
            if getattr(d, "endpoint_id", None) == ep:
                label = d.name if len(d.name) <= 40 else d.name[:37] + "..."
                if self._dual_output.start(ep, vol / 100.0):
                    self.dual_var.set(label)
                return
        # Endpoint no longer present — device was unplugged since.
        self.dual_var.set("Off")

    def _collect_settings(self) -> dict:
        """Collect all current settings into a serializable dict."""
        # Playback position
        pos = 0.0
        if self._playing and self.audio_player.available:
            pos = self.audio_player.get_position()
        return {
            "volume": self.volume_var.get(),
            # Positions are never persisted — a .freq preset always
            # resumes (and every auto-queued track starts) at 0.00.
            "playback_position": 0.0,
            # Operator preferences, not song data: the live-apply switch
            # of the broadcast chain and the autosave watchdog. Both used
            # to reset to their factory defaults on every restart.
            "master_chain_immediate": bool(
                getattr(self, "_master_chain_immediate", False)),
            "autosave": {
                "enabled": bool(getattr(self, "_autosave_enabled", True)),
                "interval": int(getattr(self, "_autosave_interval", 60)),
            },
            "audio_output": self._collect_audio_output(),
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
            "auto_gain": {
                "enabled": getattr(self, "_auto_gain_enabled", False),
                "target": getattr(self, "_auto_gain_target", -14.0),
            },
            "mixer": self._collect_mixer(),
            "master_chain": {
                "mode": int(mc[0]), "bass": mc[1], "mid": mc[2],
                "treble": mc[3], "comp_thresh_db": mc[4],
                "comp_ratio_x10": int(mc[5]), "limit_ceiling_db": mc[6],
            } if (mc := getattr(self.audio_player, "_master_chain", None)) else {},
            "auto_cue": getattr(self, "_auto_cue", False),
            "anthem": self.anthem.config.to_dict(),
            "stream": {
                "name": self.stream_name.get() if hasattr(self, 'stream_name') else "FreQ Radio",
                "protocol": self.stream_protocol_var.get() if hasattr(self, 'stream_protocol_var') else "Icecast",
                "ssl": self.stream_ssl_var.get() if hasattr(self, 'stream_ssl_var') else False,
                "codec": self.stream_codec_var.get() if hasattr(self, 'stream_codec_var') else "mp3",
                "bitrate": self.stream_bitrate_var.get() if hasattr(self, 'stream_bitrate_var') else "128",
                "live_mix": bool(self.stream_native_var.get()) if hasattr(self, 'stream_native_var') else False,
                # Protocol-specific fields
                "shoutcast": self._get_stream_fields(self._shoutcast_fields) if hasattr(self, '_shoutcast_fields') else {},
                "icecast": self._get_stream_fields(self._icecast_fields) if hasattr(self, '_icecast_fields') else {},
                "webrtc": self._get_stream_fields(self._webrtc_fields) if hasattr(self, '_webrtc_fields') else {},
            },
            "keybindings": self._collect_keybindings() if hasattr(self, '_key_rows') else {},
            "focus_mode": {
                "sleep": bool(self.focus_sleep_var.get()),
                "updates": bool(self.focus_updates_var.get()),
                "notifications": bool(self.focus_notif_var.get()),
            },
            "aircheck": {
                "scheduled": bool(self.aircheck_enabled_var.get()),
                "start": self.aircheck_start_entry.get(),
                "end": self.aircheck_end_entry.get(),
                "folder": getattr(self, "aircheck_folder", ""),
                "source": self._aircheck_source(),
                "mp3": bool(self.aircheck_mp3_var.get()),
                "keep_days": self.aircheck_keep_entry.get(),
                "auto_onair": bool(self.aircheck_auto_var.get()),
            },
            "silence_sentinel": {
                "enabled": bool(self.silence_sentinel.enabled),
                "silence_seconds": self.silence_sentinel.snapshot()["silence_seconds"],
                "threshold": self.silence_sentinel.snapshot()["threshold"],
            },
            "plugin_enabled_map": self.plugin_manager.enabled_map(),
            "mic_segment_memory": dict(getattr(self, "_mic_segment_memory", {})),
            "mic_filters_immediate": bool(
                getattr(self, "_mic_filters_immediate", False)),
            "mic_filters": dict(getattr(self.audio_player, "mic_filters",
                                        lambda: {})())
            if hasattr(self, "audio_player") else {},
            # Dual Output at the TOP level — the restore path and the
            # original smoke contract both read settings["dual_output"].
            "dual_output": (self._collect_audio_output().get("dual_output")
                             if hasattr(self, "_dual_output") else {}),
            # As-run (P0): the operator's choice to keep the broadcast
            # record, where it lives and how long it is kept.
            "asrun": {
                "enabled": bool(getattr(self.asrun, "enabled", False)),
                "folder": str(getattr(self.asrun, "folder", "")),
                "keep_days": int(getattr(self, "asrun_keep_days",
                                         DEFAULT_KEEP_DAYS)),
            },
        }

    def _collect_mixer(self) -> dict:
        """Snapshot fader/gain/mute/solo of every mixer channel."""
        try:
            return {cid: ch.get_state() for cid, ch in self.mixer_channels.items()}
        except Exception:
            return {}

    def _get_stream_fields(self, fields: dict) -> dict:
        """Extract values from stream field widgets."""
        result = {}
        for key in ["host", "port", "password", "mount", "bearer", "whip_url", "ice_server"]:
            if key in fields and hasattr(fields[key], 'get'):
                result[key] = fields[key].get()
        return result

    def _restore_settings(self, settings: dict) -> None:
        """Restore settings from a dict loaded from .freq file."""
        # Record what the file actually contained BEFORE applying it: the
        # round-trip panel reports these values, and it must be able to say
        # "the file had no settings block" rather than guess.
        self._remember_freq_snapshot(settings, source="loaded")
        if not settings:
            self._refresh_freq_roundtrip()
            return
        self._restoring_settings = True
        try:
            self._restore_settings_impl(settings)
        finally:
            self._restoring_settings = False
            self._clear_settings_dirty()
            self._refresh_freq_roundtrip()

    def _restore_settings_impl(self, settings: dict) -> None:
        # Volume
        vol = settings.get("volume", 80.0)
        self.volume_var.set(vol)
        self.volume_slider.set(vol)
        self.volume_label.configure(text=f"{int(vol)}%")
        self._on_volume_change(vol)
        # Remembered output speaker
        try:
            self._apply_remembered_output(settings.get("audio_output", {}))
        except Exception:
            pass
        # Dual Output (top-level key, plus the nested legacy form)
        try:
            self._apply_remembered_dual(settings.get("dual_output",
                                        settings.get("audio_output", {}).get("dual_output", {})))
        except Exception:
            pass
        # As-run log (P0): opt-in switch, folder and retention. Restoring
        # only re-points the log and repaints the tab — it never writes.
        a = settings.get("asrun", {})
        if a:
            try:
                keep = int(a.get("keep_days", self.asrun_keep_days))
            except (TypeError, ValueError):
                keep = DEFAULT_KEEP_DAYS
            folder = a.get("folder") or None
            self.asrun.configure(enabled=bool(a.get("enabled", False)),
                                 folder=folder, keep_days=keep)
            self.asrun_keep_days = self.asrun.keep_days
            if hasattr(self, "asrun_switch_var"):
                self.asrun_switch_var.set(self.asrun.enabled)
                self.asrun_switch.configure(                     text="Record" if self.asrun.enabled else "Off")
            if hasattr(self, "asrun_keep_entry"):
                self.asrun_keep_entry.delete(0, "end")
                self.asrun_keep_entry.insert(0, str(self.asrun_keep_days))
            self._refresh_asrun(force=True)
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
            try:
                # Legacy .freq files only: the Settings sliders that used to
                # edit this are gone, and a real chain block restored below
                # wins over these values.
                self.audio_player.set_eq(self.eq.bass, self.eq.mid, self.eq.treble)
            except Exception:
                pass
        # Crossfade
        x = settings.get("crossfade", {})
        if x:
            self.crossfade.enabled = x.get("enabled", False)
            self.crossfade.duration = x.get("duration", 3.0)
            self.crossfade.fade_in = x.get("fade_in", True)
            self.crossfade.fade_out = x.get("fade_out", True)
            # Mirror the restored switch into the player's crossfade engine.
            try:
                self.audio_player.crossfade_enabled = self.crossfade.enabled
                self.audio_player.crossfade_duration = max(1.0, self.crossfade.duration or 4.0)
                if hasattr(self, "btn_crossfade"):
                    self.btn_crossfade.configure(
                        fg_color=COLORS["accent"] if self.crossfade.enabled else "transparent",
                        text_color="#fff" if self.crossfade.enabled else COLORS["text"],
                    )
            except Exception:
                pass
        # Loudness Auto-Gain
        ag = settings.get("auto_gain", {})
        if ag:
            self._auto_gain_enabled = bool(ag.get("enabled", False))
            try:
                self._auto_gain_target = float(ag.get("target", -14.0))
            except (TypeError, ValueError):
                self._auto_gain_target = -14.0
            try:
                self.audio_player.set_auto_gain(self._auto_gain_enabled, self._auto_gain_target)
            except Exception:
                pass
            if hasattr(self, "btn_auto_gain"):
                self.btn_auto_gain.configure(
                    fg_color=COLORS["accent"] if self._auto_gain_enabled else "transparent",
                    text_color="#fff" if self._auto_gain_enabled else COLORS["text"],
                )
        # Master Chain live-apply switch. The chain parameters themselves
        # are restored further down; this flag only decides whether edits
        # hit the air while the dialog is open, so it must come back
        # before the next dialog opens.
        self._master_chain_immediate = bool(
            settings.get("master_chain_immediate", False))
        # Autosave watchdog (on/off + interval). Validated the same way as
        # the Settings field so a hand-edited .freq cannot install a
        # 2-second save loop or a value that never fires.
        asv = settings.get("autosave", {})
        if isinstance(asv, dict) and asv:
            enabled = asv.get("enabled")
            if isinstance(enabled, bool):
                self._autosave_enabled = enabled
            self._autosave_interval = self._coerce_autosave_interval(
                asv.get("interval", self._autosave_interval))
            if hasattr(self, "autosave_var"):
                self.autosave_var.set(self._autosave_enabled)
            if hasattr(self, "autosave_interval_entry"):
                self.autosave_interval_entry.delete(0, "end")
                self.autosave_interval_entry.insert(
                    0, str(self._autosave_interval))
        # Auto-cue (skip leading silence)
        ac = bool(settings.get("auto_cue", False))
        self._auto_cue = ac
        self.audio_player.auto_cue = ac
        if hasattr(self, "btn_auto_cue"):
            self.btn_auto_cue.configure(
                fg_color=COLORS["accent"] if ac else "transparent",
                text_color="#fff" if ac else COLORS["text"],
            )
        # Mixer channels (fader/gain/mute/solo)
        mx = settings.get("mixer", {})
        # Master Broadcast Chain (0926.2 P1)
        mch = settings.get("master_chain", {})
        if mch and hasattr(self, "audio_player"):
            try:
                self.audio_player.set_master_chain(
                    int(mch.get("mode", 0)),
                    float(mch.get("bass", 0.0)),
                    float(mch.get("mid", 0.0)),
                    float(mch.get("treble", 0.0)),
                    float(mch.get("comp_thresh_db", -24.0)),
                    int(mch.get("comp_ratio_x10", 30)),
                    # Missing ceiling = limiter OFF (>= 0); a -1.0 default
                    # would silently engage it on every restored session.
                    float(mch.get("limit_ceiling_db", 0.0)))
            except Exception:
                pass
            # The Settings line must show the RESTORED state, not "OFF".
            if hasattr(self, "_refresh_master_chain_status"):
                self._refresh_master_chain_status()
        if mx and hasattr(self, "mixer_channels"):
            for cid, ch in self.mixer_channels.items():
                st = mx.get(cid)
                if st:
                    ch.set_state(st)
            try:
                self._apply_mixer_volumes()
            except Exception:
                pass
        # Focus Mode — apply through the real toggles so OS state follows
        fm = settings.get("focus_mode", {})
        if fm and hasattr(self, "focus_sleep_var"):
            if fm.get("sleep") and not self.focus_sleep_var.get():
                self.focus_sleep_var.set(True)
                self._on_focus_sleep_toggle()
            if fm.get("updates") and not self.focus_updates_var.get():
                self.focus_updates_var.set(True)
                self._on_focus_updates_toggle()
            if fm.get("notifications") and not self.focus_notif_var.get():
                self.focus_notif_var.set(True)
                self._on_focus_notif_toggle()
        # Air-Check Recorder schedule
        acr = settings.get("aircheck", {})
        if acr and hasattr(self, "aircheck_enabled_var"):
            self.aircheck_enabled_var.set(bool(acr.get("scheduled", False)))
            if hasattr(self, "aircheck_auto_var"):
                self.aircheck_auto_var.set(bool(acr.get("auto_onair", False)))
            for entry, key in ((self.aircheck_start_entry, "start"),
                               (self.aircheck_end_entry, "end")):
                entry.delete(0, "end")
                entry.insert(0, str(acr.get(key, "18:00" if key == "start" else "20:00")))
            if acr.get("folder"):
                self.aircheck_folder = acr["folder"]
                self.aircheck_folder_label.configure(text=acr["folder"],
                                                     text_color=COLORS["text2"])
            if hasattr(self, "aircheck_source_var"):
                src_map = {"master": "Master output", "mic": "Microphone",
                           "both": "Both (2 files)"}
                self.aircheck_source_var.set(src_map.get(acr.get("source", "master"),
                                                          "Master output"))
                self.aircheck_mp3_var.set(bool(acr.get("mp3", False)))
                self.aircheck_keep_entry.delete(0, "end")
                self.aircheck_keep_entry.insert(0, str(acr.get("keep_days", "14")))
        # Plugin enable/disable choices (U5 SDK)
        pem = settings.get("plugin_enabled_map", {})
        if pem and hasattr(self, "plugin_manager"):
            for name, on in pem.items():
                try:
                    self.plugin_manager.set_enabled(str(name), bool(on))
                except Exception:
                    pass
            if hasattr(self, "_plugin_rows_frame"):
                self._refresh_plugin_rows()
        # Mic filter chain (OBS-style, native): {stage: mode}.
        self._mic_filters_immediate = bool(
            settings.get("mic_filters_immediate", False))
        mf = settings.get("mic_filters", {})
        if mf and hasattr(self, "audio_player"):
            for stage in ("suppression", "compressor", "limiter", "deesser",
                          "miceq", "echo", "gainst", "upcomp", "expand"):
                try:
                    mode = int(mf.get(stage, 0))
                except (TypeError, ValueError):
                    mode = 0
                if 0 <= mode <= 2:
                    self.audio_player.set_mic_filter(stage, mode)
            if hasattr(self, "_refresh_mic_filters_status"):
                self._refresh_mic_filters_status()
        # Remembered "Add Mic Segment" defaults (device NAMES survive
        # Windows re-numbering better than device ids; garbage is ignored
        # by the dialog's whitelist check).
        mem = settings.get("mic_segment_memory", {})
        if isinstance(mem, dict):
            self._mic_segment_memory = dict(mem)
        # Silence Sentinel (dry-run watchdog)
        ss = settings.get("silence_sentinel", {})
        if ss and hasattr(self, "silence_sentinel"):
            try:
                self.silence_sentinel.configure(
                    enabled=bool(ss.get("enabled", False)),
                    silence_seconds=float(ss.get("silence_seconds", 10.0)),
                    threshold=float(ss.get("threshold", 0.004)),
                )
            except (TypeError, ValueError):
                pass
            if hasattr(self, "sentinel_enabled_var"):
                self.sentinel_enabled_var.set(bool(ss.get("enabled", False)))
            if hasattr(self, "sentinel_seconds_entry"):
                secs = float(ss.get("silence_seconds", 10.0))
                self.sentinel_seconds_entry.delete(0, "end")
                self.sentinel_seconds_entry.insert(
                    0, str(int(secs)) if secs == int(secs) else str(secs))
            if hasattr(self, "sentinel_threshold_slider"):
                thr = max(1e-9, float(ss.get("threshold", 0.004)))
                slider_v = max(1.0, min(5.0, -math.log10(thr)))
                self.sentinel_threshold_slider.set(slider_v)
                self.sentinel_threshold_label.configure(
                    text=f"-{round(-20 * math.log10(thr))} dB")
            self._refresh_sentinel_status()
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
            if hasattr(self, 'stream_native_var'):
                self.stream_native_var.set(bool(s.get("live_mix", False)))
            # Restore protocol-specific fields
            self._restore_stream_fields(self._shoutcast_fields, s.get("shoutcast", {}))
            self._restore_stream_fields(self._icecast_fields, s.get("icecast", {}))
            self._restore_stream_fields(self._webrtc_fields, s.get("webrtc", {}))
            # Trigger protocol change to show correct fields
            self.after(100, self._on_protocol_change)
        # Positions are never persisted — presets always start at 0.00.
        self._saved_playback_position = 0.0
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
    def _fmt_dur(secs) -> str:
        """mm:ss (or h:mm:ss) — TOTAL: never raises, never prints garbage.

        `Song.duration` is missing/None for stream entries, hand-edited
        .freq files and mic segments; this runs inside the queue painter and
        the now-playing bar, where one int(None) used to abort the whole
        draw. A non-number renders as 00:00 instead of taking the UI down.
        """
        total = int(_safe_seconds(secs))
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ─────────────────────────────────────
    # Progress Timer
    # ─────────────────────────────────────
    def _safe_poll(self, fn, name: str) -> None:
        """Run one poller step; a crashing step must never kill the timer
        that drives all of them. Failures are logged, never silent."""
        try:
            fn()
        except Exception:
            logging.getLogger("freq").exception("poller %s failed", name)

    def _start_progress_timer(self) -> None:
        """Periodically update progress bar, logs, streaming status, autosave.
        Always re-arms itself — even if every sub-task throws."""
        self._safe_poll(self._update_progress, "progress")
        self._safe_poll(self._refresh_log, "log")
        self._safe_poll(self._update_stream_status, "stream_status")
        self._safe_poll(self._check_autosave, "autosave")
        self._safe_poll(self._update_anthem_next, "anthem_next")
        self._safe_poll(self._update_mixer, "mixer")
        self._safe_poll(self._poll_aircheck_schedule, "aircheck")
        self._safe_poll(self._aircheck_auto_poll, "aircheck_auto")
        self._safe_poll(self._poll_asrun, "asrun")
        try:
            self.after(500, self._start_progress_timer)
        except Exception:
            # App is being torn down — no more ticks.
            pass

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
                # The window wrote the file, so the round-trip panel must
                # not keep reporting the previous one.
                self._remember_freq_snapshot(settings, source="saved")
                self._refresh_freq_roundtrip()
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
            toast.configure(bg=COLORS["green"])
            lbl = tk.Label(toast, text=text, bg=COLORS["green"], fg="#000",
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
            self._drain_device_lost()
            self._drain_stream_events()
            if not self.audio_player.available:
                return
            # Skip the PROGRESS BAR while downloading (it shows progress
            # elsewhere) — but NEVER skip the VU meter below: during the
            # record-then-play mic fallback _download_mode is True for a
            # long stretch, and bailing here used to freeze the meter so
            # the operator thought the mic was dead.
            if not self._download_mode:
                pos = self.audio_player.get_position()
                dur = self.audio_player.get_duration()
                if dur > 0:
                    pct = min(100.0, (pos / dur) * 100)
                    self.np_progress.set(pct)
                    # Update waveform playhead
                    self.waveform.set_position(pos / dur)
                    self.np_time_total.configure(text=self._fmt_dur(dur))
                self.np_time_cur.configure(text=self._fmt_dur(pos))
            # Update VU meter — real output levels from the loopback tap
            # (same source as the mixer meters), covering music on ANY
            # engine and live mic segments. No synthetic animation: when
            # nothing audible plays, the bars fall to zero.
            # "Audible now" = playing a track OR a live mic is mixed OR
            # the mic-fallback (record-then-play) holds the stage — in
            # every one of those the operator needs a live meter.
            active = (bool(self._playing)
                      or self.audio_player.emergency_mic_active()
                      or bool(self._download_mode))
            # Measure ONCE per tick: the tap PCM is drained by the read,
            # so a second tap in the same tick would always see zeros.
            tap_levels = self._tap_levels() if active else None
            # Cached for the Studio Display: the tap PCM is drained by the
            # read above, so a second reader in the same tick sees zeros.
            self._last_tap_levels = tap_levels
            self._last_meter_active = bool(active)
            self.vu_meter.update_levels(
                self.volume_var.get(), active, levels=tap_levels)
            # Feed the same measurement to the (dry-run) sentinel so the
            # meter and the watchdog always agree on what "silence" is.
            if getattr(self, "silence_sentinel", None) is not None:
                try:
                    self.silence_sentinel.observe(
                        tap_levels, bool(self._playing),
                        mic_active=self.audio_player.emergency_mic_active())
                except Exception:
                    pass
        except Exception:
            pass

    # -- Studio Display (read-only control-room screen) --

    def _studio_state(self) -> dict:
        """Snapshot for the Studio Display window.

        Read-only and cheap enough to call 8×/s: it copies what the progress
        tick already measured (the loopback tap is drained once per tick) and
        never touches the player, the queue or the rundown.
        """
        state = {"title": "", "artist": "", "position": 0.0,
                 "duration": 0.0, "live": False, "on_air": False,
                 "status": "", "next_title": "", "next_sub": "",
                 "levels": None}
        try:
            song = self._current_song_or_none()
            playing = bool(getattr(self, "_playing", False))
            mic_live = False
            try:
                mic_live = bool(self.audio_player.emergency_mic_active())
            except Exception:
                mic_live = False
            state["on_air"] = bool(playing or mic_live)
            # The tick's measurement — NOT a fresh read (see above).
            state["levels"] = getattr(self, "_last_tap_levels", None)
            if song is not None:
                state["title"] = str(getattr(song, "title", "") or "")
                state["artist"] = str(getattr(song, "artist", "") or "")
                state["live"] = getattr(song, "source", "") in ("mic", "stream")
                try:
                    state["duration"] = float(getattr(song, "duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    state["duration"] = 0.0
            if playing:
                try:
                    state["position"] = float(self.audio_player.get_position() or 0.0)
                    live_dur = float(self.audio_player.get_duration() or 0.0)
                    if live_dur > 0:
                        state["duration"] = live_dur
                except Exception:
                    pass
            if getattr(self, "_ad_playing", False):
                state["status"] = "AD BREAK"
            elif getattr(self, "_jingle_playing", False):
                state["status"] = "JINGLE"
            elif mic_live:
                state["status"] = "MIC LIVE"
            elif not state["on_air"]:
                state["status"] = "STANDBY"
            # Next up: the same walk the rundown takes (skips flags, stops
            # on a stop-note), so this screen never promises the wrong song.
            try:
                index, nxt, kind = self.rq.find_next(self.rq.current_index, 1)
            except Exception:
                index, nxt, kind = (None, None, "none")
            if kind == "play" and nxt is not None:
                state["next_title"] = str(getattr(nxt, "title", "") or "")
                bits = [str(getattr(nxt, "artist", "") or "")]
                try:
                    if float(getattr(nxt, "duration", 0.0) or 0.0) > 0:
                        bits.append(self._fmt_dur(float(nxt.duration)))
                except (TypeError, ValueError):
                    pass
                state["next_sub"] = "  ·  ".join(b for b in bits if b)
            elif kind == "note" and nxt is not None:
                state["next_title"] = "\u23f8  STOP AT NOTE"
                state["next_sub"] = str(getattr(nxt, "title", "") or "")
            else:
                state["next_title"] = "\u2014"
                state["next_sub"] = "end of rundown"
        except Exception:
            pass
        return state

    def _open_studio_display(self) -> None:
        """Open (or raise) the Studio Display for the control-room screen."""
        existing = getattr(self, "studio_display", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    return
            except Exception:
                pass
            self.studio_display = None
        try:
            from studio_display import StudioDisplay
            self.studio_display = StudioDisplay(self, self._studio_state)
        except Exception as exc:
            messagebox.showerror("Studio Display",
                                 f"Cannot open the studio display:\n{exc}")

    # -- Plugin SDK (U5, observe-only) --

    def _plugin_notify(self, message: str) -> None:
        """Plugin → status line (never blocks, never raises)."""
        try:
            status = getattr(self, "np_status", None)
            if status is not None:
                status.configure(text=f"\U0001f9e9 {message}")
        except Exception:
            pass

    def _plugin_snapshot(self) -> dict:
        """Read-only playback snapshot handed to plugins."""
        try:
            return {
                "playing": bool(self._playing),
                "queue_length": len(self.rq.queue),
                "current_index": self.rq.current_index,
                "volume": self.volume_var.get(),
            }
        except Exception:
            return {}

    def _emit_plugins(self, event: str, **data):
        """Fire a plugin event; returns the dispatcher thread (or None).
        Never raises — plugin failures are contained inside the manager."""
        try:
            return self.plugin_manager.emit(event, **data)
        except Exception:
            return None

    def _current_song_or_none(self):
        try:
            idx = self.rq.current_index
            if 0 <= idx < len(self.rq.queue):
                return self.rq.queue[idx]
        except Exception:
            pass
        return None

    @staticmethod
    def _song_info(song) -> dict:
        """Read-only snapshot of a song for plugin events.

        A live Song object would hand plugins a mutation path straight
        into the playback pipeline (e.g. rewriting file_path) — plugins
        get a plain dict copy instead, so the object stays private.
        """
        if song is None:
            return None
        try:
            return {
                "title": getattr(song, "title", ""),
                "artist": getattr(song, "artist", ""),
                "source": getattr(song, "source", ""),
                "duration": getattr(song, "duration", 0.0),
                "duration_str": getattr(song, "duration_str", ""),
                "id": getattr(song, "id", ""),
            }
        except Exception:
            return {}

    def _silence_on_silence(self, info: dict) -> None:
        """Silence Sentinel alert (dry-run): log + status line, nothing else.

        Deliberately does NOT touch the queue, the engine or playback —
        recovery actions belong to a later Ultimate build.
        """
        try:
            status = getattr(self, "np_status", None)
            if status is not None:
                status.configure(
                    text="⚠ Silence detected "
                         f"({info.get('silent_for', 0):.0f}s, dry-run)")
        except Exception:
            pass
        try:
            self._refresh_sentinel_status()
        except Exception:
            pass

    # -- Silence Sentinel settings (dry-run observer; opt-in) --

    def _on_sentinel_toggle(self) -> None:
        """Enable/disable the sentinel from the Settings switch."""
        try:
            self.silence_sentinel.configure(
                enabled=bool(self.sentinel_enabled_var.get()))
        except Exception:
            pass
        self._refresh_sentinel_status()

    def _on_sentinel_seconds(self) -> None:
        """Apply the silence-window seconds entry (clamped to ≥ 1 s)."""
        try:
            value = max(1.0, float(self.sentinel_seconds_entry.get()))
        except (TypeError, ValueError):
            value = 10.0
        self.silence_sentinel.configure(silence_seconds=value)
        self.sentinel_seconds_entry.delete(0, "end")
        self.sentinel_seconds_entry.insert(0, str(int(value)) if value == int(value)
                                           else str(value))
        self._refresh_sentinel_status()

    def _on_sentinel_threshold(self, slider_value: float) -> None:
        """Map the slider (1–5) to a linear peak threshold: 10^(-v).

        The label shows the true dBFS: 20·log10(10^-v) = -20·v.
        """
        v = float(slider_value)
        self.silence_sentinel.configure(threshold=10.0 ** (-v))
        self.sentinel_threshold_label.configure(text=f"-{round(v * 20)} dB")

    def _refresh_sentinel_status(self) -> None:
        """Sync the events counter + switch state from the sentinel."""
        try:
            snap = self.silence_sentinel.snapshot()
        except Exception:
            return
        if hasattr(self, "sentinel_enabled_var"):
            self.sentinel_enabled_var.set(bool(snap["enabled"]))
        if hasattr(self, "sentinel_events_label"):
            self.sentinel_events_label.configure(
                text=("Events: {events} · watching: {win}s / {db} dB"
                      .format(events=snap["silence_events"],
                              win=snap["silence_seconds"],
                              db=round(20 * math.log10(max(snap["threshold"], 1e-9))))
                    if snap["enabled"] else "Off — enable above to watch for dead air"))

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
                                          font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
        self.jingle_label.pack(anchor="w", padx=12)

        jingle_test_row = ctk.CTkFrame(scroll, fg_color="transparent")
        jingle_test_row.pack(fill="x", padx=8, pady=(6, 0))
        ctk.CTkButton(jingle_test_row, text="\u25b6  Play Now (Manual)", width=140, height=28,
                       fg_color=COLORS["purple"], hover_color=COLORS["purple"],
                       corner_radius=8, font=ctk.CTkFont(size=10, weight="bold"),
                       text_color="#ffffff",
                       command=self._play_jingle_manual).pack(side="left", padx=4)
        ctk.CTkButton(jingle_test_row, text="\u23f9  Stop", width=70, height=28,
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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
        ctk.CTkLabel(duck_row, text="when mic active", font=ctk.CTkFont(size=10),
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
        # -- National Anthem --
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
                       font=ctk.CTkFont(size=14),
                       command=lambda: self._open_time_picker(self.anthem_morning)).pack(side="left", padx=2)
        ctk.CTkLabel(morning_row, text="(Thailand standard)", font=ctk.CTkFont(size=10),
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
                       font=ctk.CTkFont(size=14),
                       command=lambda: self._open_time_picker(self.anthem_evening)).pack(side="left", padx=2)
        ctk.CTkLabel(evening_row, text="(Thailand standard)", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text3"]).pack(side="left")

        # Anthem file
        ctk.CTkButton(scroll, text="🎵 Select Anthem File", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._select_anthem).pack(fill="x", padx=8, pady=4)
        self.anthem_label = ctk.CTkLabel(scroll, text="No file selected",
                                          font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
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
                       fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
                       corner_radius=8, font=ctk.CTkFont(size=10, weight="bold"),
                       text_color="#ffffff",
                       command=self._test_anthem_play).pack(side="left", padx=4)
        ctk.CTkButton(anthem_test_row, text="\u23f9  Stop", width=70, height=28,
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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
                                                    font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
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
        # The EQ is NOT configurable here any more. These sliders and the
        # Master Broadcast Chain dialog pushed the SAME native 3-band stage
        # (set_eq wrote the very bands render_master_set owns), so the app
        # had two UIs fighting over one filter and two sets of values in the
        # .freq. The chain dialog is the single EQ now; `self.eq` stays only
        # so an older .freq that still carries an "eq" block restores into
        # those same bands instead of silently losing the operator's curve.
        # -- Audio Edit --
        ctk.CTkLabel(scroll, text="\u2702\ufe0f Audio Edit", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkButton(scroll, text="\U0001f4c2 Select Audio to Edit", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._select_edit_file).pack(fill="x", padx=8, pady=2)
        self.edit_file_label = ctk.CTkLabel(scroll, text="No file selected",
                                              font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
        self.edit_file_label.pack(anchor="w", padx=12)
        self._edit_file = ""

        edit_row = ctk.CTkFrame(scroll, fg_color="transparent")
        edit_row.pack(fill="x", padx=8, pady=4)
        for text, cmd in [("Trim", self._edit_trim), ("Fade In", self._edit_fadein),
                          ("Fade Out", self._edit_fadeout), ("Normalize", self._edit_normalize)]:
            ctk.CTkButton(edit_row, text=text, width=80, height=26,
                           fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                           font=ctk.CTkFont(size=10), command=cmd).pack(side="left", padx=2)

        self._settings_sep(scroll)
        # -- Cache --
        ctk.CTkLabel(scroll, text="\U0001f4e6 Cache", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkButton(scroll, text="\U0001f5d1 Clear Cache", height=28,
                       fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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

        self.autosave_status_label = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=10),
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
        # -- Focus Mode --
        ctk.CTkLabel(scroll, text="\U0001f3af Focus Mode", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        self.focus_sleep_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Prevent system sleep while running",
                      variable=self.focus_sleep_var, onvalue=True, offvalue=False,
                      command=self._on_focus_sleep_toggle).pack(anchor="w", padx=12, pady=2)
        self.focus_updates_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Block Windows Update service",
                      variable=self.focus_updates_var, onvalue=True, offvalue=False,
                      command=self._on_focus_updates_toggle).pack(anchor="w", padx=12, pady=2)
        self.focus_notif_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Suppress OS notifications",
                      variable=self.focus_notif_var, onvalue=True, offvalue=False,
                      command=self._on_focus_notif_toggle).pack(anchor="w", padx=12, pady=2)
        self.focus_status = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=9),
                                           text_color=COLORS["text3"])
        self.focus_status.pack(anchor="w", padx=16)

        self._settings_sep(scroll)
        # -- Air-Check Recorder --
        ctk.CTkLabel(scroll, text="\U0001f534 Air-Check Recorder", font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        air_row = ctk.CTkFrame(scroll, fg_color="transparent")
        air_row.pack(fill="x", padx=8, pady=2)
        self.aircheck_btn = ctk.CTkButton(air_row, text="\u23fa  Record Output", width=150, height=32,
                                            fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
                                            corner_radius=8, font=ctk.CTkFont(size=11, weight="bold"),
                                            text_color="#ffffff",
                                            command=self._toggle_aircheck)
        self.aircheck_btn.pack(side="left", padx=4)
        self.aircheck_status = ctk.CTkLabel(air_row, text="", font=ctk.CTkFont(size=10),
                                              text_color=COLORS["text3"])
        self.aircheck_status.pack(side="left", padx=6)
        ctk.CTkButton(scroll, text="\U0001f4c2  Choose Save Folder", height=28,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=8,
                       command=self._choose_aircheck_folder).pack(fill="x", padx=8, pady=2)
        self.aircheck_folder_label = ctk.CTkLabel(scroll, text="Saving next to the app",
                                                    font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
        self.aircheck_folder_label.pack(anchor="w", padx=12)
        src_row = ctk.CTkFrame(scroll, fg_color="transparent")
        src_row.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(src_row, text="Source:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.aircheck_source_var = ctk.StringVar(value="Master output")
        ctk.CTkOptionMenu(src_row, values=["Master output", "Microphone", "Both (2 files)"],
                          variable=self.aircheck_source_var, width=150, height=26,
                          fg_color=COLORS["bg_input"], button_color=COLORS["border"],
                          button_hover_color=COLORS["accent_hover"],
                          text_color=COLORS["text"]).pack(side="left", padx=6)
        self.aircheck_mp3_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(scroll, text="Export MP3 on stop (removes WAV)",
                        variable=self.aircheck_mp3_var, onvalue=True, offvalue=False,
                        font=ctk.CTkFont(size=10),
                        fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"]).pack(anchor="w", padx=12, pady=2)
        keep_row = ctk.CTkFrame(scroll, fg_color="transparent")
        keep_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(keep_row, text="Keep recordings (days):", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.aircheck_keep_entry = ctk.CTkEntry(keep_row, width=50, fg_color=COLORS["bg"],
                                                  border_color=COLORS["border"])
        self.aircheck_keep_entry.insert(0, "14")
        self.aircheck_keep_entry.pack(side="left", padx=6)
        ctk.CTkButton(keep_row, text="Clean Now", width=80, height=24,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"], corner_radius=6,
                       command=self._aircheck_clean_now).pack(side="left", padx=4)
        self.aircheck_files_label = ctk.CTkLabel(scroll, text="", justify="left",
                                                   font=ctk.CTkFont(size=9), text_color=COLORS["text3"])
        self.aircheck_files_label.pack(anchor="w", padx=12, pady=(2, 0))
        self.aircheck_enabled_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Scheduled recording (start/end time below)",
                      variable=self.aircheck_enabled_var, onvalue=True, offvalue=False,
                      command=self._on_aircheck_schedule_toggle).pack(anchor="w", padx=12, pady=(6, 2))
        sched_row = ctk.CTkFrame(scroll, fg_color="transparent")
        sched_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(sched_row, text="Start:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left")
        self.aircheck_start_entry = ctk.CTkEntry(sched_row, width=60, fg_color=COLORS["bg"],
                                                   border_color=COLORS["border"])
        self.aircheck_start_entry.insert(0, "18:00")
        self.aircheck_start_entry.pack(side="left", padx=4)
        ctk.CTkLabel(sched_row, text="End:", font=ctk.CTkFont(size=10),
                      text_color=COLORS["text2"]).pack(side="left", padx=(10, 0))
        self.aircheck_end_entry = ctk.CTkEntry(sched_row, width=60, fg_color=COLORS["bg"],
                                                 border_color=COLORS["border"])
        self.aircheck_end_entry.insert(0, "20:00")
        self.aircheck_end_entry.pack(side="left", padx=4)
        self.aircheck_last_path = ""
        # ── Automatic air-check when ON AIR (0926.2) ──
        self.aircheck_auto_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(scroll, text="Auto-record when ON AIR (live mix / mic)",
                      variable=self.aircheck_auto_var, onvalue=True, offvalue=False,
                      command=self._on_aircheck_auto_toggle).pack(anchor="w", padx=12, pady=(6, 2))
        ctk.CTkLabel(scroll,
                     text="Starts the native recorder whenever the show is on air\n"
                          "(Live mix stream or a live microphone) and saves when\n"
                          "it ends. Manual & scheduled recordings take priority.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        self.aircheck_auto_status = ctk.CTkLabel(scroll, text="", justify="left",
                                                   font=ctk.CTkFont(size=9),
                                                   text_color=COLORS["text3"])
        self.aircheck_auto_status.pack(anchor="w", padx=12, pady=(1, 0))

        self._settings_sep(scroll)
        # -- Silence Sentinel (Ultimate · U4, dry-run) --
        ctk.CTkLabel(scroll, text="\U0001f916 Silence Sentinel",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkLabel(scroll,
                     text="Warns when the broadcast output stays silent too long.\n"
                          "Dry-run: reports only — never touches playback.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        self.sentinel_enabled_var = ctk.BooleanVar(value=bool(_silence_sentinel_enabled))
        ctk.CTkSwitch(scroll, text="Watch for dead air (dry-run)",
                      variable=self.sentinel_enabled_var, onvalue=True, offvalue=False,
                      command=self._on_sentinel_toggle).pack(anchor="w", padx=12, pady=(6, 2))
        sent_row = ctk.CTkFrame(scroll, fg_color="transparent")
        sent_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(sent_row, text="Silent for:", font=ctk.CTkFont(size=10),
                     text_color=COLORS["text2"]).pack(side="left")
        self.sentinel_seconds_entry = ctk.CTkEntry(sent_row, width=50,
                                                   fg_color=COLORS["bg"],
                                                   border_color=COLORS["border"])
        self.sentinel_seconds_entry.insert(0, "10")
        self.sentinel_seconds_entry.pack(side="left", padx=4)
        ctk.CTkLabel(sent_row, text="sec", font=ctk.CTkFont(size=10),
                     text_color=COLORS["text2"]).pack(side="left")
        self.sentinel_seconds_entry.bind("<FocusOut>", lambda e: self._on_sentinel_seconds())
        self.sentinel_seconds_entry.bind("<Return>", lambda e: self._on_sentinel_seconds())
        self.sentinel_threshold_slider = ctk.CTkSlider(
            sent_row, from_=1.0, to=5.0, width=110, command=self._on_sentinel_threshold)
        self.sentinel_threshold_slider.set(2.4)   # 10^(-2.4) ≈ -48 dBFS default
        self.sentinel_threshold_slider.pack(side="left", padx=(12, 4))
        self.sentinel_threshold_label = ctk.CTkLabel(
            sent_row, text="-48 dB", width=52, font=ctk.CTkFont(size=10),
            text_color=COLORS["text2"])
        self.sentinel_threshold_label.pack(side="left")
        self.sentinel_events_label = ctk.CTkLabel(
            scroll, text="", justify="left", font=ctk.CTkFont(size=9),
            text_color=COLORS["text3"])
        self.sentinel_events_label.pack(anchor="w", padx=12, pady=(2, 0))
        self._refresh_sentinel_status()

        # Mic filters are deliberately NOT here: the chain belongs to a mic
        # ITEM, so it is opened from the queue row (Filter button / right
        # click → Filters) where the operator can see which mic they are
        # editing. A Settings copy edited the same global chain with no
        # context and made "which mic am I changing?" unanswerable.
        # ── Master Broadcast Chain (0926.2 P1) ──
        ctk.CTkLabel(scroll, text="\U0001f3a6 Master Broadcast Chain",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkLabel(scroll,
                     text="Broadcast processing on the ON-AIR mix (music + mics):\n"
                          "EQ -> Compressor -> Limiter. All OFF = untouched.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        ctk.CTkButton(scroll, text="⚙ Configure Master Chain…", height=30,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6,
                      command=self._open_master_chain).pack(fill="x", padx=8, pady=(4, 2))
        self.master_chain_status = ctk.CTkLabel(
            scroll, text="", justify="left", font=ctk.CTkFont(size=9),
            text_color=COLORS["text3"], wraplength=330)
        self.master_chain_status.pack(anchor="w", padx=12, pady=(0, 2))
        self._refresh_master_chain_status()

        # ── Broadcast Check + Mic Doctor (pre-show readiness tools) ──
        self._settings_sep(scroll)
        ctk.CTkLabel(scroll, text="\\U0001f4e1 Broadcast Check",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkLabel(scroll,
                     text="Pre-show readiness probe — read-only, changes nothing.\n"
                          "Checks queue, mics, output engine, sentinel, plugins, disk.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        ctk.CTkButton(scroll, text="▶ Run broadcast check", height=30,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6,
                      command=self._run_broadcast_check).pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkButton(scroll, text="🎙 Mic Doctor — test every mic…", height=30,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6,
                      command=self._open_mic_doctor).pack(fill="x", padx=8, pady=(0, 2))
        self.broadcast_check_status = ctk.CTkLabel(
            scroll, text="Not run yet.", justify="left", font=ctk.CTkFont(size=9),
            text_color=COLORS["text3"])
        self.broadcast_check_status.pack(anchor="w", padx=12, pady=(0, 2))

        # ── Plugins (U5 SDK — observe-only) ──
        self._settings_sep(scroll)
        ctk.CTkLabel(scroll, text="\U0001f9e9 Plugins",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkLabel(scroll,
                     text="Drop .py files into the plugins folder — they receive\n"
                          "track events only (observe-only, cannot play audio).",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        self._plugin_rows_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._plugin_rows_frame.pack(fill="x", padx=8, pady=2)
        self._plugin_status_label = ctk.CTkLabel(
            scroll, text="", justify="left", font=ctk.CTkFont(size=9),
            text_color=COLORS["text3"])
        self._plugin_status_label.pack(anchor="w", padx=12)
        plugin_btns = ctk.CTkFrame(scroll, fg_color="transparent")
        plugin_btns.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkButton(plugin_btns, text="\U0001f504 Reload", width=100, height=26,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6,
                      command=self._reload_plugins).pack(side="left", padx=(4, 6))
        ctk.CTkButton(plugin_btns, text="\U0001f4c2 Open folder", width=110, height=26,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6,
                      command=self._open_plugin_folder).pack(side="left")
        self._refresh_plugin_rows()

        self.aircheck_recorder = None
        self._aircheck_auto_active = False
        self._last_aircheck_poll = 0.0
        self._last_aircheck_files_refresh = 0.0

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
                                                    font=ctk.CTkFont(size=10), text_color=COLORS["text3"])
        self._save_settings_status.pack(anchor="w", padx=12)
        ctk.CTkButton(scroll, text="\U0001f4c2  Load Settings from File", height=32,
                       fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                       corner_radius=8, font=ctk.CTkFont(size=11),
                       command=self._load_settings_from_freq).pack(fill="x", padx=8, pady=(2, 8))

        self._settings_sep(scroll)
        # -- .freq round-trip (what is saved vs. what was restored) --
        ctk.CTkLabel(scroll, text="\U0001f9fe .freq Round-Trip",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(12, 4))
        ctk.CTkLabel(scroll,
                     text="Read-only. Left = what THIS session will write on\n"
                          "Save / autosave / quit. Right = what the last file gave us.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="left").pack(anchor="w", padx=12)
        # wrap="word": the Settings column is narrow, and a clipped report
        # is exactly the kind of half-truth this panel exists to remove.
        self.freq_roundtrip_box = ctk.CTkTextbox(
            scroll, height=200, wrap="word", corner_radius=6, border_width=1,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=COLORS["text"])
        self.freq_roundtrip_box.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkButton(scroll, text="\U0001f504  Refresh preview", height=26,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      corner_radius=6, font=ctk.CTkFont(size=10),
                      command=self._refresh_freq_roundtrip).pack(fill="x", padx=8,
                                                                 pady=(0, 8))
        self._refresh_freq_roundtrip()

    # ── Settings: .freq round-trip preview ──
    def _remember_freq_snapshot(self, settings: dict, source: str = "loaded") -> None:
        """Remember the settings block that belongs to the .freq file now.

        `source` is "loaded" when the file was read or "saved" when this
        session just wrote it. The panel compares live values against this
        snapshot, so both paths must update it — otherwise a fresh save
        would keep reporting "changed since load".
        """
        path = getattr(getattr(self, "rq", None), "_save_path", None)
        try:
            self._freq_restored_from = str(path) if path is not None else ""
            self._freq_restored_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._freq_restored_source = source
            self._freq_restored_settings = {
                k: v for k, v in (settings or {}).items()}
        except Exception:
            self._freq_restored_settings = {}

    def _freq_roundtrip_text(self) -> str:
        """Build the round-trip report as text (no widget access).

        Lives apart from the widget so the report can be checked directly
        against the file on disk — the panel is only trusted if the text
        it shows matches what a save really writes.
        """
        live: dict = {}
        try:
            live = self._collect_settings()
        except Exception:
            live = {}

        live_mc = bool(live.get("master_chain_immediate",
                                getattr(self, "_master_chain_immediate", False)))
        asv = live.get("autosave")
        if not isinstance(asv, dict):
            asv = {"enabled": bool(getattr(self, "_autosave_enabled", True)),
                   "interval": int(getattr(self, "_autosave_interval", 60))}
        live_as_on = bool(asv.get("enabled", True))
        live_as_every = self._coerce_autosave_interval(asv.get("interval", 60))
        try:
            vol = float(live.get("volume", self.volume_var.get()))
        except Exception:
            vol = 0.0

        path = getattr(getattr(self, "rq", None), "_save_path", None)
        name = path.name if path is not None else "radio_queue.freq"
        exists = bool(path is not None and path.exists())
        if exists:
            try:
                st = path.stat()
                file_line = (f"FILE     {name}  (exists, {st.st_size / 1024:.1f} KB, "
                             f"modified {time.strftime('%H:%M:%S', time.localtime(st.st_mtime))})")
            except OSError:
                file_line = f"FILE     {name}  (exists)"
        else:
            file_line = f"FILE     {name}  (not written yet)"

        snap = getattr(self, "_freq_restored_settings", None) or {}
        src = getattr(self, "_freq_restored_source", "loaded")
        when = getattr(self, "_freq_restored_at", "") or "-"

        if snap:
            head = ("RESTORED FROM THE LAST FILE" if src == "loaded"
                    else "LAST WRITTEN BY THIS SESSION")
            restore_line = (f"{head}  {name}  at {when}  "
                            f"({len(snap)} settings groups)")
            has_mc = "master_chain_immediate" in snap
            snap_mc = bool(snap.get("master_chain_immediate", False))
            snap_as = snap.get("autosave")
            has_as = isinstance(snap_as, dict)
            snap_as_text = "-"
            if has_as:
                snap_as_on = bool(snap_as.get("enabled", True))
                snap_as_every = self._coerce_autosave_interval(
                    snap_as.get("interval", 60))
                snap_as_text = (f"{'ON' if snap_as_on else 'OFF'} / "
                                f"{snap_as_every} s")
        else:
            restore_line = (f"RESTORED FROM THE LAST FILE  {name}  "
                            f"(no settings block)")
            has_mc = has_as = False
            snap_mc = False
            snap_as_text = "-"

        def row(label: str, live_text: str, file_text: str, present: bool) -> str:
            if not present:
                return f"  {label} = {live_text}  (file: not stored -> default)"
            mark = "ok" if live_text == file_text else "CHANGED - save to keep"
            return f"  {label} = {live_text}  (file: {file_text})  {mark}"

        live_mc_text = "ON" if live_mc else "OFF"
        live_as_text = f"{'ON' if live_as_on else 'OFF'} / {live_as_every} s"

        if not snap:
            verdict = ("the last file had no settings - a restart would start "
                       "from factory defaults")
        else:
            # A key the file never stored restores to its factory default,
            # so compare against that rather than reporting a phantom edit.
            eff_mc_text = ("ON" if snap_mc else "OFF") if has_mc else "OFF"
            eff_as_text = snap_as_text if has_as else "ON / 60 s"
            if live_mc_text == eff_mc_text and live_as_text == eff_as_text:
                verdict = "unchanged since load - a restart would keep these values"
            else:
                verdict = ("changed since load - save to keep them across "
                           "a restart")

        return "\n".join([
            file_line,
            restore_line,
            row("master chain immediate", live_mc_text,
                "ON" if snap_mc else "OFF", has_mc),
            row("autosave", live_as_text, snap_as_text, has_as),
            "WILL WRITE  (Save / autosave / quit)",
            f"  master chain immediate = {live_mc_text}",
            f"  autosave = {live_as_text}",
            f"  volume = {vol:.0f} %",
            f"  settings groups = {len(live)}",
            f"VERDICT: {verdict}",
        ])

    def _refresh_freq_roundtrip(self) -> None:
        """Re-render the read-only round-trip box (safe before it exists)."""
        box = getattr(self, "freq_roundtrip_box", None)
        if box is None:
            return
        try:
            text = self._freq_roundtrip_text()
        except Exception as exc:              # never take the tab down
            text = f"Could not build the .freq report: {exc}"
        try:
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
            box.configure(state="disabled")
        except Exception:
            pass

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
                                                font=ctk.CTkFont(size=10),
                                                text_color=COLORS["text3"])
        self.stream_info_label.pack(anchor="w", padx=8)

        # ── Control Buttons ──
        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(fill="x", padx=8, pady=8)

        self.btn_stream_start = ctk.CTkButton(btn_frame, text="Start Stream", height=36,
                                                fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
                                                command=self._start_stream)
        self.btn_stream_start.pack(side="left", padx=4)
        ToolTip(self.btn_stream_start, text="Start Streaming", description="Begin broadcasting to server")

        self.btn_stream_stop = ctk.CTkButton(btn_frame, text="Stop", height=36,
                                               fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
                                               command=self._stop_stream, state="disabled")
        self.btn_stream_stop.pack(side="left", padx=4)
        ToolTip(self.btn_stream_stop, text="Stop Streaming", description="Stop broadcasting to server")

        # ── Live mix source (0926.2): broadcast what the app PLAYS ──
        # Uses the native C++ sender (native_stream.cpp): WASAPI loopback
        # → ffmpeg → server in one native thread. With this switch ON,
        # listeners hear live mics + music + the whole filter chain.
        # Default OFF = legacy file-streaming behaviour, byte-identical.
        self.stream_native_var = ctk.BooleanVar(value=False)
        native_row = ctk.CTkFrame(scroll, fg_color="transparent")
        native_row.pack(fill="x", padx=8, pady=(0, 4))
        self.chk_native_mix = ctk.CTkCheckBox(
            native_row, text="🔴 Live mix — broadcast mics + music as heard",
            variable=self.stream_native_var,
            font=ctk.CTkFont(size=11), text_color=COLORS["text2"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"])
        self.chk_native_mix.pack(side="left")
        ToolTip(self.chk_native_mix, text="Live mix source",
                description="Broadcast the real-time master mix (live mics, "
                            "music, filters) instead of single files. "
                            "Uses the native C++ sender.")
        self.stream_native_state = ctk.CTkLabel(native_row, text="",
                                                 font=ctk.CTkFont(size=10),
                                                 text_color=COLORS["text3"])
        self.stream_native_state.pack(side="left", padx=(12, 0))

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
            ctk.CTkLabel(scroll, text=req, font=ctk.CTkFont(size=10),
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
            fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
            corner_radius=8, font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ffffff",
            command=self._start_relay)
        self.btn_relay_start.pack(side="left", padx=4)
        self.btn_relay_stop = ctk.CTkButton(
            relay_btn_frame, text="⏹ Stop", height=32,
            fg_color=COLORS["red"], hover_color=COLORS["red_hover"],
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
            self._asrun_begin("relay", title="external stream relay",
                              path=filepath, source_type="relay",
                              channel="relay",
                              reason="playing a fetched relay segment")
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
        self._asrun_end_active("stopped", kind="relay")
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
        if self.stream_native_var.get():
            self._start_native_stream()
            return
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
            self._asrun_begin(
                "stream", title=self.stream_name.get() or "FreQ Radio",
                url=f"{proto_name}://{host}:{port}{mount}",
                source_type=proto_name.lower(), channel="stream",
                reason="broadcast stream started")
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
                eta = getattr(streamer, "reconnect_eta", 0.0)
                attempt = getattr(streamer, "reconnect_attempt", 0)
                remaining = max(0, int(round(eta - time.time()))) if eta else 0
                self.stream_info_label.configure(
                    text=f"Icecast connection lost; retrying in {remaining}s "
                         f"(attempt {attempt})...")
            elif streamer.state == StreamState.ERROR:
                self.stream_status_label.configure(text="❌ Stream error", text_color=COLORS["red"])
                self.stream_info_label.configure(text="Connection failed; check Icecast settings and logs")
            elif hasattr(self, '_relay_playing') and self._relay_playing:
                self.stream_status_label.configure(text="🔗 Relay active", text_color=COLORS["green"])
        except Exception:
            pass

    # ═══ Native live-mix streaming (native_stream.cpp) ═══
    def _start_native_stream(self) -> None:
        """Broadcast the LIVE master mix through the native C++ sender."""
        if not _native_mix.available():
            self.stream_status_label.configure(
                text="❌ native_stream module missing", text_color=COLORS["red"])
            return
        try:
            if self.stream_protocol_var.get() == "Shoutcast":
                fields = self._shoutcast_fields
                host = fields["host"].get() or "localhost"
                port = int(fields["port"].get() or "8000")
                username = "source"
                password = fields["password"].get() or "hackme"
                mount = "/"
            else:
                fields = self._icecast_fields
                host = fields["host"].get() or "localhost"
                port = int(fields["port"].get() or "8000")
                username = fields["user"].get() or "source"
                password = fields["password"].get() or "hackme"
                mount = fields["mount"].get() or "/stream"
        except (ValueError, KeyError) as e:
            messagebox.showerror("Invalid input", f"Check your settings: {e}")
            return
        if _native_mix.start(host, port, mount, username, password,
                             self.stream_name.get() or "FreQ Radio",
                             bitrate=int(self.stream_bitrate_var.get())):
            self.stream_status_label.configure(text="🔴 Live mix on air",
                                                text_color=COLORS["red"])
            self.stream_info_label.configure(
                text=f"native @ {host}:{port}{mount} — mics + music as heard")
            self.btn_stream_start.configure(state="disabled")
            self.btn_stream_stop.configure(state="normal")
            self._asrun_begin(
                "stream", title=self.stream_name.get() or "FreQ Radio",
                url=f"native://{host}:{port}{mount}",
                source_type="native", channel="stream",
                reason="broadcast stream started (live mix)")
            self._push_native_metadata()
        else:
            err = _native_mix.status()["error"] or "Connection failed — check server"
            self.stream_status_label.configure(
                text=f"❌ {err}", text_color=COLORS["red"])

    def _push_native_metadata(self) -> None:
        """Send the current queue item to listeners as ICY metadata."""
        try:
            idx = self.rq.current_index
            if 0 <= idx < len(self.rq.queue):
                song = self.rq.queue[idx]
                if getattr(song, "is_microphone", False):
                    _native_mix.update_metadata("Live voice", "Microphone")
                else:
                    _native_mix.update_metadata(song.title, song.artist)
        except Exception:
            pass

    def _update_stream_status(self) -> None:
        """Update streaming status label periodically."""
        if getattr(self, "stream_native_var", None) and self.stream_native_var.get() \
                and _native_mix.active:
            st = _native_mix.status()
            icon = {"Streaming": "🔴", "Reconnecting": "⚠",
                    "Error": "❌"}.get(st["state"], "●")
            color = {"Streaming": COLORS["red"],
                     "Reconnecting": COLORS["orange"],
                     "Error": COLORS["red"]}.get(st["state"], COLORS["text3"])
            self.stream_status_label.configure(
                text=f"{icon} Live mix: {st['state']}", text_color=color)
            sent_mb = st["sent"] / (1024.0 * 1024.0)
            extra = f" · {st['attempts']} attempts" if st["attempts"] > 1 else ""
            self.stream_info_label.configure(
                text=f"{sent_mb:.1f} MB sent{extra}"
                     + (f" · {st['error']}" if st["error"] else ""))
            return
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

    def _stop_stream(self) -> None:
        """Stop streaming."""
        if _native_mix.active:
            _native_mix.stop()
        streamer.stop()
        self._asrun_end_active("stopped", kind="stream")
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
            ("jingle",   "Jingle",   COLORS["cat_jingle"]),
            ("ads",      "Ads",      COLORS["cat_ads"]),
            ("anthem",   "Anthem",   COLORS["red"]),
            ("mic",      "Mic",      COLORS["green"]),
        ]
        for cid, label, color in channel_defs:
            ch = MixerChannelWidget(tab, label=label, color=color, channel_id=cid,
                                       on_solo=self._on_solo_change,
                                       on_change=self._on_mixer_channel_change)
            ch.pack(fill="x", padx=6, pady=2)
            self._bind_wheel_volume(ch.fader, ch._on_fader_change)
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
        self._bind_wheel_volume(self.mixer_master_slider,
                                self._on_mixer_master_change)
        self.mixer_master_label = ctk.CTkLabel(fader_frame, text="80%",
                                                font=ctk.CTkFont(size=10),
                                                text_color=COLORS["text3"], width=36)
        self.mixer_master_label.pack(side="left")

    def _channel_factor(self, cid: str) -> float:
        """Mute/solo-aware linear factor for a mixer channel (0.0 – 2.0).

        Solo wins over mute: when any channel is soloed, every other
        channel is silenced regardless of its own mute state.
        """
        ch = self.mixer_channels.get(cid)
        if ch is None:
            return 1.0
        if any(c.is_solo for c in self.mixer_channels.values()) and not ch.is_solo:
            return 0.0
        if ch.is_muted:
            return 0.0
        lin = 10 ** (ch._gain_db / 20.0)
        return max(0.0, min(2.0, (ch._volume / 100.0) * lin))

    def _apply_mixer_volumes(self) -> None:
        """Route master × active-channel factor into the playback engine.

        Music, jingle, ads, and anthem all play through the single
        audio_player (sequentially — one source at a time), so the mixer
        gate applies the factor of whichever source is live right now.
        The mic channel's fader/gain drive the recording gain instead.
        """
        if not hasattr(self, "volume_var"):
            return
        master = self.volume_var.get() / 100.0
        factor = self._channel_factor(getattr(self, "_active_source_channel", "music"))
        if self.audio_player.available:
            self.audio_player.volume = max(0.0, min(1.0, master * factor))
        mic = self.mixer_channels.get("mic")
        if mic is not None and hasattr(self, "mic"):
            if self._channel_factor("mic") == 0.0:
                gain = 0.0
            else:
                gain = (mic._volume / 100.0) * (10 ** (mic._gain_db / 20.0))
            self.mic.gain = round(max(0.0, min(3.0, gain)), 2)
            if hasattr(self, "mic_gain_slider"):
                self.mic_gain_slider.set(self.mic.gain)
            if hasattr(self, "mic_gain_label"):
                self.mic_gain_label.configure(text=f"{self.mic.gain:.1f}x")

    def _on_mixer_channel_change(self, channel_id: str) -> None:
        """Fader/gain/mute/solo changed on a channel — re-apply to audio."""
        self._apply_mixer_volumes()

    def _on_solo_change(self, channel_id: str, solo: bool) -> None:
        """Handle solo toggle — mute all other channels when any solo is active."""
        any_solo = any(ch.is_solo for ch in self.mixer_channels.values())
        for cid, ch in self.mixer_channels.items():
            if any_solo:
                # When any channel is soloed, unmuted non-solo channels become muted
                if ch.is_solo:
                    ch.mute_btn.configure(fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])
                else:
                    ch.mute_btn.configure(fg_color=COLORS["red_muted"], text_color=COLORS["red"])
            else:
                # No solo active — restore normal mute state
                if ch.is_muted:
                    ch.mute_btn.configure(fg_color=COLORS["red"], text_color="#fff")
                else:
                    ch.mute_btn.configure(fg_color=COLORS["bg_hover"], text_color=COLORS["text2"])
        self._apply_mixer_volumes()

    def _on_mixer_master_change(self, val: float) -> None:
        """Handle master fader change in mixer."""
        vol = float(val)
        if hasattr(self, 'volume_label'):
            self.volume_label.configure(text=f"{int(vol)}%")
        if hasattr(self, 'mixer_master_label'):
            self.mixer_master_label.configure(text=f"{int(vol)}%")
        self.volume_var.set(vol)
        self._apply_mixer_volumes()
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
        """Unbind what we bound last time, then bind the current keys."""
        # Track the exact sequences we bound, never the current key strings:
        # _on_key_change rewrites _keybindings BEFORE calling us, so unbinding
        # "the current key" only ever removed the NEW one and left the old
        # shortcut alive forever (rebinding Save to Ctrl+P gave you both).
        for sequence in getattr(self, "_applied_key_seqs", ()):
            try:
                self.unbind(sequence)
            except Exception:
                pass
        self._applied_key_seqs = set()

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
            "toggle_crossfade": lambda e: self._toggle_crossfade(),
            "deselect":    lambda e: self._clear_queue_selection(),
        }

        # Bind new keys
        for action_id, (_, key, _) in self._keybindings.items():
            handler = handler_map.get(action_id)
            if not handler:
                continue
            if key == "Space":
                # Space uses special handler (skip if typing in entry)
                sequence = "<space>"
                handler = self._no_typing(self._on_global_space)
            elif "+" in key:
                # Modifier combos (Ctrl+S, Ctrl+D …) don't collide with typing —
                # bind them raw so they also work from inside a settings field.
                sequence = key_event_sequence(key)
                if sequence is None:
                    continue
            else:
                # Plain keys (n, p, m …) fire while typing otherwise.
                sequence = f"<{key.lower()}>"
                handler = self._no_typing(handler)
            try:
                self.bind(sequence, handler)
                self._applied_key_seqs.add(sequence)
            except Exception as err:
                # Never swallow this again: a bad pattern means the shortcut
                # the Keys tab promises simply does not work.
                logging.getLogger("freq.gui").warning(
                    "keybinding %s → %s rejected by Tk: %s",
                    action_id, sequence, err)

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

    def _tap_levels(self):
        """Real (left, right) linear peaks of the PROGRAMME audio.

        Two sources, in order of trust:
        1. the native render engine's own meter — measured in the render
           thread after the master chain and after the volume scaler, so it
           shows what FreQ plays and nothing else (a browser in the
           background cannot move the VU, and the level is not attenuated
           twice);
        2. the WASAPI render loopback, kept as the fallback for the pygame
           engine (no native meter) — that tap is "what leaves the speaker",
           which includes every other app by definition.

        Returns None when neither source is available. The loopback PCM is
        drained here so its 5 s ring never overflows.
        """
        try:
            engine_levels = self.audio_player.output_levels()
        except Exception:
            engine_levels = None
        if engine_levels is not None:
            return engine_levels
        cap = getattr(self, "_loopback_tap", None)
        if cap is None:
            try:
                import native_audio_capture as nac
                cap = nac.Capture(loopback=True)
                cap.start()
            except Exception:
                cap = False   # sentinel: unavailable for this session
            self._loopback_tap = cap
        if cap is False:
            return None
        try:
            lvl = cap.levels()
            cap.read()
            return lvl
        except Exception:
            return None

    def _stop_loopback_tap(self) -> None:
        cap = getattr(self, "_loopback_tap", None)
        if cap and cap is not False:
            try:
                cap.stop()
            except Exception:
                pass
        self._loopback_tap = None

    def _update_mixer(self) -> None:
        """Update mixer channel meters with real output levels.

        Levels come from the native loopback tap (what actually leaves the
        default output device). When the tap is unavailable the meters fall
        back to a volume-scaled steady level — never random values.
        """
        try:
            vol = self.volume_var.get()
            playing = self._playing and not self._download_mode
            tap = self._tap_levels() if playing else None

            # Active source: whichever stream currently owns the engine.
            source_active = {
                "music": playing,
                "jingle": self._jingle_playing,
                "ads": self._ad_playing,
                "anthem": getattr(self, "_anthem_playing", False),
            }
            # Any source that is on gets the true tapped level (scaled by its
            # mixer gate); everything else is silent.
            for cid in ("music", "jingle", "ads", "anthem"):
                ch = self.mixer_channels.get(cid)
                if not ch:
                    continue
                active = source_active.get(cid, False)
                if active:
                    f = self._channel_factor(cid)
                    if tap is not None:
                        # This channel owns the output right now → real level.
                        if any(source_active.values()) and sum(source_active.values()) == 1:
                            l, r = tap
                        else:   # several flags at once — divide fairly
                            l, r = (tap[0] / 2, tap[1] / 2)
                    else:
                        l = r = 0.8 * (vol / 100.0)   # steady estimate, no tap
                    ch.update_levels(max(0.0, min(1.0, l * f)),
                                     max(0.0, min(1.0, r * f)), True)
                else:
                    ch.update_levels(0.0, 0.0, False)

            # Mic channel — real level while a native capture is recording.
            mic_ch = self.mixer_channels.get("mic")
            if mic_ch:
                mic_lvl = getattr(self, "_mic_capture_levels", None)
                if callable(mic_lvl):
                    try:
                        ml, mr = mic_lvl()
                    except Exception:
                        ml = mr = 0.0
                    f = self._channel_factor("mic")
                    mic_ch.update_levels(max(0.0, min(1.0, ml * f)),
                                         max(0.0, min(1.0, mr * f)),
                                         (ml > 0.0 or mr > 0.0))
                else:
                    mic_ch.update_levels(0.0, 0.0, False)

            # Master meter — the tapped output as-is (what listeners hear).
            if hasattr(self, 'master_meter'):
                if tap is not None:
                    mml = max(0.0, min(1.0, tap[0]))
                    mmr = max(0.0, min(1.0, tap[1]))
                else:
                    base = (vol / 100.0) if playing else 0.0
                    mml = mmr = base * 0.8
                self.master_meter.update_levels(mml, mmr, playing)
        except Exception:
            pass

    # -- Focus Mode --
    def _focus(self):
        if getattr(self, "focus_mode", None) is None:
            from focus_mode import FocusMode
            self.focus_mode = FocusMode()
        return self.focus_mode

    def _focus_report(self, ok: bool, label: str, on: bool = True) -> None:
        f = self._focus()
        if ok:
            self.focus_status.configure(text=f"{label}: {'on' if on else 'off'}",
                                        text_color=COLORS["text3"])
        else:
            self.focus_status.configure(text=f"{label} failed: {f.last_error}",
                                        text_color=COLORS["red"])

    def _on_focus_sleep_toggle(self) -> None:
        on = bool(self.focus_sleep_var.get())
        ok = self._focus().set_prevent_sleep(on)
        if not ok:
            self.focus_sleep_var.set(False)
        self._focus_report(ok, "Sleep guard", on)

    def _on_focus_updates_toggle(self) -> None:
        on = bool(self.focus_updates_var.get())
        ok = self._focus().set_block_updates(on)
        if not ok:
            self.focus_updates_var.set(False)
        if on and ok:
            self._toast("Windows Update disabled (restored on exit)") if hasattr(self, "_toast") else None
        self._focus_report(ok, "Update block", on)

    def _on_focus_notif_toggle(self) -> None:
        on = bool(self.focus_notif_var.get())
        ok = self._focus().set_suppress_notifications(on)
        if not ok:
            self.focus_notif_var.set(False)
        self._focus_report(ok, "Notification suppression", on)

    # -- Air-Check Recorder --
    def _aircheck(self):
        from aircheck import AirCheckRecorder
        if self.aircheck_recorder is None:
            self.aircheck_recorder = AirCheckRecorder()
        return self.aircheck_recorder

    # ═══ Automatic Air-Check on air (native engine) ═══
    def _aircheck_native_recording(self) -> bool:
        import native_aircheck as na
        return na.aircheck_state() == 1

    def _aircheck_native_start(self) -> str:
        """Start a native air-check; returns the destination path."""
        import native_aircheck as na
        from datetime import datetime
        folder = getattr(self, "aircheck_folder", "") or os.getcwd()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(
            folder, f"aircheck_{datetime.now():%Y%m%d_%H%M}.wav")
        na.aircheck_start(path, "", True)   # loopback = the full mix
        self._aircheck_native_path = path
        self._aircheck_native_started_at = time.time()
        return path

    def _aircheck_native_stop(self) -> Optional[str]:
        """Stop the native air-check; optionally transcode to MP3.
        Returns the saved path (or None)."""
        import native_aircheck as na
        path = getattr(self, "_aircheck_native_path", None)
        if na.aircheck_state() == 1:
            na.aircheck_stop()
        if not path:
            return None
        if self.aircheck_mp3_var.get():
            try:
                from aircheck import AirCheckRecorder
                mp3 = AirCheckRecorder.export_mp3(path, delete_wav=True)
                if mp3:
                    self._aircheck_native_path = mp3
                    path = mp3
            except Exception:
                pass
        try:
            keep = int(self.aircheck_keep_entry.get())
            if keep >= 1:
                from aircheck import AirCheckRecorder
                AirCheckRecorder.clean_old(
                    os.path.dirname(path), keep_days=keep)
        except (ValueError, Exception):
            pass
        return path

    def _aircheck_auto_wanted(self) -> bool:
        """True when auto air-check should be recording right now.

        Sources (any one is enough):
          · Live mix stream active (native or legacy)
          · An emergency mic session is live
        """
        if getattr(self, "stream_native_var", None) is not None \
                and self.stream_native_var.get() and _native_mix.active:
            return True
        try:
            if streamer.is_streaming:
                return True
        except Exception:
            pass
        try:
            if self.audio_player.emergency_mic_active():
                return True
        except Exception:
            pass
        return getattr(self, "_mic_session_open", False)

    def _aircheck_auto_poll(self) -> None:
        """Called from the 500 ms tick: starts/stops the automatic
        air-check as the show goes on/off the air. Manual and scheduled
        recordings always win — auto never touches their files."""
        if not (getattr(self, "aircheck_auto_var", None)
                and self.aircheck_auto_var.get()):
            return
        rec = getattr(self, "aircheck_recorder", None)
        if rec is not None and rec.recording:
            return                      # manual/scheduled owns the slot
        want = self._aircheck_auto_wanted()
        active = self._aircheck_native_recording()
        if want and not active:
            try:
                p = self._aircheck_native_start()
                self.aircheck_auto_status.configure(
                    text="auto air-check: recording", text_color=COLORS["red"])
                self._toast("🔴 On air — auto air-check started") if hasattr(self, "_toast") else None
            except Exception as e:
                self.aircheck_auto_status.configure(
                    text=f"auto air-check error: {e}",
                    text_color=COLORS["red"])
        elif not want and active:
            # Save + MP3-export + prune run on a WORKER thread: a long
            # recording means a multi-second ffmpeg transcode, and doing
            # that on the UI thread froze the whole app ("Not Responding")
            # at exactly the moment the show ended.
            self.aircheck_auto_status.configure(text="saving air-check…",
                                                  text_color=COLORS["orange"])
            threading.Thread(target=self._aircheck_auto_finish_worker,
                             daemon=True, name="freq-aircheck-save").start()

    def _aircheck_auto_finish_worker(self) -> None:
        """Background: stop the native recorder, export MP3, prune, then
        hop back to the UI thread for the status label."""
        import native_aircheck as na
        path = getattr(self, "_aircheck_native_path", None)
        if na.aircheck_state() == 1:
            na.aircheck_stop()
        if not path:
            return
        saved = path
        try:
            if self.aircheck_mp3_var.get():
                from aircheck import AirCheckRecorder
                mp3 = AirCheckRecorder.export_mp3(path, delete_wav=True)
                if mp3:
                    self._aircheck_native_path = mp3
                    saved = mp3
        except Exception:
            pass
        try:
            keep = int(self.aircheck_keep_entry.get())
            if keep >= 1:
                from aircheck import AirCheckRecorder
                AirCheckRecorder.clean_old(os.path.dirname(saved),
                                           keep_days=keep)
        except (ValueError, Exception):
            pass
        name = os.path.basename(saved)

        def _announce() -> None:
            self.aircheck_auto_status.configure(
                text="auto saved: " + name, text_color=COLORS["text3"])
            self._refresh_aircheck_files(force=True)

        try:
            self.after(0, _announce)   # normal path: back to the UI thread
        except Exception:
            _announce()                # headless/test: no mainloop to hop to

    def _aircheck_source(self) -> str:
        mapping = {"Master output": "master", "Microphone": "mic",
                   "Both (2 files)": "both"}
        return mapping.get(self.aircheck_source_var.get(), "master")

    def _aircheck_maybe_export_mp3(self) -> None:
        """After a manual stop, transcode to MP3 when the checkbox is set."""
        if not self.aircheck_mp3_var.get():
            return
        rec = self.aircheck_recorder
        if rec is None or not self.aircheck_last_path:
            return
        base = os.path.splitext(self.aircheck_last_path)[0]
        candidates = [self.aircheck_last_path]
        if self.aircheck_last_path.endswith(".wav"):
            stem = os.path.splitext(base)[0]
            if stem.endswith("_mic") or base != stem:
                candidates.append(base)  # paired master/mic file
        mp3s = []
        for wav in set(candidates):
            if os.path.isfile(wav):
                mp3 = rec.export_mp3(wav, delete_wav=True)
                if mp3:
                    mp3s.append(mp3)
        if mp3s:
            self.aircheck_last_path = mp3s[0]

    def _aircheck_clean_now(self) -> None:
        try:
            keep = int(self.aircheck_keep_entry.get())
            if keep < 1:
                keep = 1
        except ValueError:
            keep = 14
        from aircheck import AirCheckRecorder
        folder = getattr(self, "aircheck_folder", "") or os.getcwd()
        n = AirCheckRecorder.clean_old(folder, keep_days=keep)
        self._toast(f"Cleaned {n} old recording(s)") if hasattr(self, "_toast") else None
        self._refresh_aircheck_files(force=True)

    def _refresh_aircheck_files(self, force: bool = False) -> None:
        if not hasattr(self, "aircheck_files_label"):
            return
        now = time.time()
        if not force and now - self._last_aircheck_files_refresh < 15.0:
            return
        self._last_aircheck_files_refresh = now
        from aircheck import AirCheckRecorder
        folder = getattr(self, "aircheck_folder", "") or os.getcwd()
        try:
            files = AirCheckRecorder.list_files(folder)
        except Exception:
            files = []
        if not files:
            self.aircheck_files_label.configure(text="No recordings yet")
            return
        lines = []
        for p, size, _mt in files[:5]:
            mb = size / (1024 * 1024)
            lines.append(f"{os.path.basename(p)}  ({mb:.1f} MB)")
        if len(files) > 5:
            lines.append(f"… and {len(files) - 5} more")
        self.aircheck_files_label.configure(text="\n".join(lines))

    def _toggle_aircheck(self) -> None:
        rec = self._aircheck()
        try:
            if rec.recording:
                rec.stop()
                self.aircheck_btn.configure(text="\u23fa  Record Output",
                                            fg_color=COLORS["red"],
                                            hover_color=COLORS["red_hover"])
                p = self.aircheck_last_path
                self.aircheck_status.configure(
                    text=("saved: " + os.path.basename(p)) if p else "stopped",
                    text_color=COLORS["text3"])
                try:
                    self._aircheck_maybe_export_mp3()
                    if self.aircheck_mp3_var.get() and self.aircheck_last_path.endswith(".mp3"):
                        self.aircheck_status.configure(
                            text="saved: " + os.path.basename(self.aircheck_last_path))
                except Exception:
                    pass
                self._refresh_aircheck_files(force=True)
                self._toast("Air-check saved") if hasattr(self, "_toast") else None
            else:
                folder = getattr(self, "aircheck_folder", "") or None
                self.aircheck_last_path = rec.start(folder=folder,
                                                    source=self._aircheck_source())
                self.aircheck_btn.configure(text="\u23f9  Stop Recording",
                                            fg_color=COLORS["red_hover"],
                                            hover_color=COLORS["red"])
                self.aircheck_status.configure(text="recording… 0:00",
                                               text_color=COLORS["red"])
        except Exception as e:
            self.aircheck_status.configure(text=f"error: {e}",
                                           text_color=COLORS["red"])

    def _choose_aircheck_folder(self) -> None:
        from tkinter import filedialog
        d = filedialog.askdirectory(title="Choose Air-Check save folder")
        if d:
            self.aircheck_folder = d
            self.aircheck_folder_label.configure(text=d, text_color=COLORS["text2"])

    def _on_aircheck_schedule_toggle(self) -> None:
        self._aircheck_auto_active = False   # force re-evaluation on next tick

    def _on_aircheck_auto_toggle(self) -> None:
        """Auto air-check switched: if it was mid-recording and the show is
        no longer on air, close the file cleanly right away."""
        if hasattr(self, "_toast"):
            self._toast("🟢 Auto air-check enabled — records while on air"
                        if self.aircheck_auto_var.get()
                        else "Auto air-check disabled")

    def _poll_aircheck_schedule(self) -> None:
        """Called every 500 ms tick: drives manual status text and the
        scheduled recording window (start/stop automatically)."""
        rec = getattr(self, "aircheck_recorder", None)
        now = time.time()
        if rec is not None and rec.recording and not self._aircheck_auto_active:
            # Manual recording — update the elapsed label
            m, s = divmod(int(rec.elapsed()), 60)
            self.aircheck_status.configure(text=f"recording… {m}:{s:02d}",
                                           text_color=COLORS["red"])
        self._refresh_aircheck_files()
        # Scheduled recording (at most every 5 s of housekeeping)
        if now - self._last_aircheck_poll < 5.0:
            return
        self._last_aircheck_poll = now
        if not self.aircheck_enabled_var.get():
            return
        try:
            from aircheck import AirCheckScheduler
            sched = AirCheckScheduler()
            sched.add(self.aircheck_start_entry.get().strip() or "00:00",
                      self.aircheck_end_entry.get().strip() or "23:59",
                      folder=getattr(self, "aircheck_folder", ""))
            due = sched.due_window()
            if due and not (rec and rec.recording):
                folder = due.get("folder") or None
                self.aircheck_last_path = rec.start(folder=folder) if rec else None
                if self.aircheck_last_path:
                    self._aircheck_auto_active = True
                    self.aircheck_btn.configure(text="\u23f9  Stop Recording",
                                                fg_color=COLORS["red_hover"],
                                                hover_color=COLORS["red"])
                    self.aircheck_status.configure(text="scheduled recording…",
                                                   text_color=COLORS["red"])
            elif not due and rec and rec.recording and self._aircheck_auto_active:
                rec.stop()
                self._aircheck_auto_active = False
                self.aircheck_btn.configure(text="\u23fa  Record Output",
                                            fg_color=COLORS["red"],
                                            hover_color=COLORS["red_hover"])
                self.aircheck_status.configure(
                    text="saved: " + os.path.basename(self.aircheck_last_path or ""),
                    text_color=COLORS["text3"])
                self._refresh_aircheck_files(force=True)
            elif not due and self._aircheck_auto_active:
                self._aircheck_auto_active = False
        except Exception as e:
            if rec is None or not rec.recording:
                self.aircheck_status.configure(text=f"schedule error: {e}",
                                               text_color=COLORS["red"])

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
        """Manually play the jingle now.

        Architecture (shared with the anthem): STOP the current track —
        never pause. The old pause/resume design deadlocked the WASAPI
        engine (a paused stream swallowed the next play_file) and left
        the interrupted song resuming at the wrong position.
        """
        if self._jingle_playing or self._anthem_playing:
            self._show_toast("🔔 A jingle/anthem is already playing")
            return
        if not self.jingle.jingle_file:
            messagebox.showwarning("No jingle file", "Select a jingle file first.")
            return
        if not os.path.isfile(self.jingle.jingle_file):
            messagebox.showwarning("File not found", f"Jingle file not found:\n{self.jingle.jingle_file}")
            return
        if self._channel_factor("jingle") == 0.0:
            self._show_toast("🔔 Jingle channel is muted in the mixer")
            return
        # As-run: the song on air is cut for this jingle. Close it with the
        # honest reason FIRST (the jingle's own entry opens below).
        self._asrun_end_active("jingle-cut")
        # Snapshot the interrupted track BEFORE stopping anything.
        self._jingle_was_playing = self._playing
        self._jingle_saved_index = self.rq.current_index
        self._jingle_saved_position = 0.0
        if self._playing and self.audio_player.available:
            self._jingle_saved_position = self.audio_player.get_position()
        # Full stop — this also invalidates crossfade/decoder state cleanly.
        self._playing = False
        self.audio_player.stop()
        self._jingle_playing = True
        self._active_source_channel = "jingle"
        self._apply_mixer_volumes()
        self.np_status.configure(text="🔔 Jingle (manual)...", text_color=COLORS["purple"])
        try:
            ok = self.audio_player.play_file(
                self.jingle.jingle_file, duration=0,
                on_finish=lambda: self.after(0, self._jingle_resume))
        except Exception as e:
            logging.getLogger("freq.gui").exception("jingle play_file raised for %s", self.jingle.jingle_file)
            ok = False
        if not ok:
            self._jingle_playing = False
            self._active_source_channel = "music"
            self._apply_mixer_volumes()
            self.np_status.configure(text="", text_color=COLORS["text2"])
            self._show_toast("⚠️ Cannot play jingle file")
            self._jingle_resume()  # restore whatever was playing before
            return
        self._playing = True
        self.btn_play.configure(text="⏸")
        self._asrun_begin(
            "jingle", title=Path(self.jingle.jingle_file).stem or "jingle",
            path=self.jingle.jingle_file, channel="jingle",
            reason="jingle (pressed by the operator)")

    def _jingle_stop(self) -> None:
        """Stop jingle playback and resume the previous song."""
        self.audio_player.stop()
        self._jingle_resume()

    def _jingle_resume(self) -> None:
        """Restore the interrupted song after a manual jingle.

        Re-plays the saved track at the saved position (play_file + seek —
        a fresh start, never a stale resume of a paused stream).
        """
        def _do_resume():
            self._jingle_playing = False
            self._active_source_channel = "music"
            self._apply_mixer_volumes()
            # Only restore if something was actually playing before the
            # jingle and the user has not started something else since.
            if not self._jingle_was_playing or self._jingle_playing or self._anthem_playing:
                return
            self._jingle_was_playing = False
            idx = getattr(self, '_jingle_saved_index', self.rq.current_index)
            pos = getattr(self, '_jingle_saved_position', 0.0)
            if idx is None or not (0 <= idx < len(self.rq.queue)):
                return
            song = self.rq.queue[idx]
            self._saved_playback_position = pos
            self._playing = True
            self._asrun_begin("music", song, channel="music",
                              reason="resumed the song after the jingle")
            if song.source == "youtube" and song.url:
                self.audio_player.play_youtube(
                    song.url, duration=song.duration, song_id=song.id,
                    title=song.title,
                    on_finish=lambda: self.after(0, self._on_track_finished),
                    start_position=pos,
                )
            elif song.file_path and os.path.isfile(song.file_path):
                self.audio_player.play_file(
                    song.file_path, duration=song.duration,
                    on_finish=lambda: self.after(0, self._on_track_finished),
                )
                if pos > 0.3:
                    self.after(100, lambda p=pos: self.audio_player.seek(p))
            else:
                # Original file vanished — fall back to cache or skip.
                self._play_index(idx)
        self.after(0, _do_resume)

    def _select_jingle(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.ogg *.flac")])
        if path:
            self.jingle.jingle_file = path
            self.jingle_label.configure(text=Path(path).name)

    # ── Plugin manager panel (Settings) ──────────────────────────────

    def _refresh_plugin_rows(self) -> None:
        """Rebuild per-plugin rows from manager status (UI-only, cheap)."""
        frame = getattr(self, "_plugin_rows_frame", None)
        label = getattr(self, "_plugin_status_label", None)
        manager = getattr(self, "plugin_manager", None)
        if frame is None or manager is None:
            return
        for child in frame.winfo_children():
            child.destroy()
        rows = manager.status()
        if not rows:
            if label is not None:
                label.configure(text="No plugins yet — drop a .py file into the folder.")
            return
        failed = 0
        for row in rows:
            line = ctk.CTkFrame(frame, fg_color="transparent")
            line.pack(fill="x", pady=1)
            if row.get("load_error"):
                failed += 1
                ctk.CTkLabel(line, text="\u274c", width=24,
                             font=ctk.CTkFont(size=11)).pack(side="left")
                ctk.CTkLabel(line, text=f"{row.get('file', '?')} — failed to load",
                             anchor="w", font=ctk.CTkFont(size=10),
                             text_color=COLORS["red"]).pack(side="left", fill="x", expand=True)
                continue
            name = str(row.get("name", "?"))
            var = ctk.BooleanVar(value=bool(row.get("enabled", False)))

            def _toggle(chosen=name, chosen_var=var):
                self._on_plugin_toggle(chosen, chosen_var)

            ctk.CTkSwitch(line, text="", variable=var, onvalue=True,
                          offvalue=False, command=_toggle, width=40).pack(side="left")
            ctk.CTkLabel(line, text=name, anchor="w",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=(8, 0))
            consec = int(row.get("consecutive_errors", 0) or 0)
            total = int(row.get("total_errors", 0) or 0)
            badge = f"v{row.get('version', '0')} · {'on' if row.get('enabled') else 'off'}"
            if consec:
                badge += f" · \u26a0 {consec}/3"
            elif total:
                badge += f" · {total} err"
            ctk.CTkLabel(line, text=badge, font=ctk.CTkFont(size=9),
                         text_color=COLORS["text3"]).pack(side="right")
        if label is not None:
            loaded = len(rows) - failed
            text = f"{loaded} plugin(s) loaded"
            if failed:
                text += f" · {failed} failed to load"
            label.configure(text=text)

    def _on_plugin_toggle(self, name: str, var) -> None:
        """Flip one plugin; persists via plugin_enabled_map on autosave/save."""
        manager = getattr(self, "plugin_manager", None)
        if manager is None:
            return
        manager.set_enabled(name, bool(var.get()))
        self._refresh_plugin_rows()

    def _reload_plugins(self) -> None:
        """Scan the folder again — picks up NEW .py files only (safe)."""
        manager = getattr(self, "plugin_manager", None)
        if manager is None:
            return
        loaded, errors = manager.load_all()
        self._refresh_plugin_rows()
        label = getattr(self, "_plugin_status_label", None)
        if label is not None:
            total = len(manager.status()) - len(errors)
            parts = [f"Scanned — {loaded} new" if loaded else "Scanned",
                     f"{total} available", f"{len(errors)} failed"]
            label.configure(text=" · ".join(p for p in parts if p))

    # ── Broadcast Check + Mic Doctor (pre-show readiness) ───────────

    def _broadcast_check_rows(self):
        """Readiness rows: (label, ok, detail). STRICTLY read-only — the
        check never opens capture endpoints, moves the queue, or changes
        any setting; it only reports what is already true."""
        rows = []
        # 1. Queue
        try:
            q = list(getattr(self, "rq").queue)
            mic_items = sum(1 for s in q if getattr(s, "is_mic_segment", False))
            missing = 0
            for s in q:
                p = getattr(s, "file_path", "") or ""
                if p and not Path(p).exists():
                    missing += 1
            ok = bool(q) and missing == 0
            detail = (f"{len(q)} items, {mic_items} mic segments"
                      + (f", {missing} missing file(s)!" if missing else ""))
            rows.append(("Queue", ok, detail))
        except Exception as err:
            rows.append(("Queue", False, f"unreadable: {err}"))
        # 2. Microphones detected
        try:
            mics = list(getattr(self, "_mic_input_devices", []) or [])
            rows.append(("Microphones", bool(mics),
                         f"{len(mics)} input device(s) detected"
                         if mics else "none detected — plug a mic in"))
        except Exception as err:
            rows.append(("Microphones", False, f"error: {err}"))
        # 3. Output engine (no device I/O — just process state)
        try:
            ap = getattr(self, "audio_player", None)
            available = bool(ap is not None and getattr(ap, "available", False))
            rows.append(("Output engine", available,
                         "WASAPI render engine running" if available
                         else "engine not ready — reopen output device"))
        except Exception as err:
            rows.append(("Output engine", False, f"error: {err}"))
        # 4. Loopback tap (what the broadcast actually hears)
        try:
            lvl = self._tap_levels()
            ok = lvl is not None
            detail = "native loopback tap OK" if ok else "tap unavailable"
            if ok and self._playing:
                detail += f" — live peak {max(lvl):.2f}"
            rows.append(("Loopback tap", ok, detail))
        except Exception as err:
            rows.append(("Loopback tap", False, f"error: {err}"))
        # 5. Mic filters (report-only; OFF is a valid choice)
        try:
            modes = getattr(self, "audio_player").mic_filters()
            names = ("Off", "Gentle", "Strong")
            detail = " · ".join(
                f"{stage.title()}: {names[modes.get(stage, 0)]}"
                for stage in ("suppression", "compressor", "limiter", "deesser",
                              "miceq", "echo", "gainst", "upcomp", "expand"))
            rows.append(("Mic filters", True, detail))
        except Exception as err:
            rows.append(("Mic filters", False, f"error: {err}"))
        # 6. Plugins
        try:
            status = getattr(self, "plugin_manager").status()
            bad = [r for r in status if r.get("load_error")]
            rows.append(("Plugins", not bad,
                         f"{len(status)} loaded" + (f", {len(bad)} failed to load!" if bad else "")
                         if status else "none installed (ok)"))
        except Exception as err:
            rows.append(("Plugins", False, f"error: {err}"))
        # 7. Disk space for recordings
        try:
            free = shutil.disk_usage(Path.cwd()).free
            gb = free / (1024 ** 3)
            rows.append(("Disk space", gb >= 2.0, f"{gb:.1f} GB free"))
        except Exception as err:
            rows.append(("Disk space", False, f"error: {err}"))
        return rows

    def _run_broadcast_check(self) -> None:
        """Run the readiness probe now and paint the result lines."""
        label = getattr(self, "broadcast_check_status", None)
        if label is None:
            return
        fails = 0
        lines = []
        for name, ok, detail in self._broadcast_check_rows():
            fails += 0 if ok else 1
            mark = "✔" if ok else "✘"
            lines.append(f"{mark} {name}: {detail}")
        if fails:
            lines.append(f"{fails} check(s) need attention before going live.")
        else:
            lines.append("All clear — ready for broadcast.")
        label.configure(text="\n".join(lines),
                        text_color=COLORS["red"] if fails else COLORS["green"])

    def _open_mic_doctor(self) -> None:
        """Test every capture endpoint through the SAME native engine path
        the app uses, and report which mic actually carries signal.
        Opens mics one at a time for ~2.5 s and closes them again — it
        never touches a live session (play_mic_multi supersessions and
        the queue timer are left alone)."""
        if not hasattr(self, "audio_player"):
            return
        try:
            import native_audio_capture as nac
        except Exception as err:
            from tkinter import messagebox
            messagebox.showerror("Mic Doctor",
                                 f"Native capture module unavailable:\n{err}")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Mic Doctor")
        dialog.geometry("520x420")
        dialog.minsize(440, 340)
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Which microphone actually has signal?",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(14, 2))
        ctk.CTkLabel(dialog,
                     text="Each mic is opened alone for ~2.5 s and closed again.\n"
                          "Speak into one mic at a time; watch its peak move.",
                     font=ctk.CTkFont(size=9), text_color=COLORS["text3"],
                     justify="center").pack()
        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(8, 4))
        rows = {}
        try:
            mics = [("", "Default (OS)")] + [
                (eid, name) for eid, name, _flag in
                (nac.enum_microphones() or [])]
        except Exception:
            mics = [("", "Default (OS)")]

        def set_row(eid, text, color):
            lab = rows.get(eid)
            if lab is not None and lab.winfo_exists():
                lab.configure(text=text, text_color=color)

        # All probing runs on the MAIN thread via after() ticks — Tk
        # widgets are never touched from a worker thread, and the UI
        # stays responsive while each mic is measured for ~2.5 s.
        state = {"cap": None, "eid": None, "peak": 0.0, "ticks": 0}
        mic_iter = iter(mics)

        def close_cap():
            cap = state.get("cap")
            if cap not in (None, False):
                try:
                    cap.stop()
                except Exception:
                    pass
            state["cap"] = None

        def finish_mic():
            close_cap()
            peak = state["peak"]
            if peak >= 0.02:
                set_row(state["eid"], f"✔ SIGNAL  peak {peak:.2f}",
                        COLORS["green"])
            else:
                set_row(state["eid"], "— silent (no signal)",
                        COLORS["text3"])

        def tick():
            if not dialog.winfo_exists():
                close_cap()
                return
            if state["cap"] is None:
                try:
                    eid, _name = next(mic_iter)      # (endpoint, label) row
                except StopIteration:
                    return
                state["eid"] = eid
                state["peak"] = 0.0
                state["ticks"] = 0
                set_row(eid, "listening…", COLORS["accent"])
                try:
                    cap = nac.Capture(endpoint=eid or None)
                    cap.start()
                    state["cap"] = cap
                except Exception as err:
                    set_row(eid, f"✘ failed to open: {err}", COLORS["red"])
                    state["cap"] = False
                    dialog.after(60, tick)
                    return
            cap = state["cap"]
            if cap is not False:
                try:
                    lvl = cap.levels()
                    if lvl:
                        state["peak"] = max(state["peak"], float(max(lvl)))
                    cap.read(4096)          # drain so the ring never overflows
                except Exception:
                    pass
                set_row(state["eid"],
                        f"listening… peak {state['peak']:.2f}",
                        COLORS["accent"])
            state["ticks"] += 1
            if state["ticks"] >= 25:        # ~2.5 s per mic at 100 ms
                finish_mic()
                dialog.after(150, tick)
            else:
                dialog.after(100, tick)

        def run_all():
            for eid, _name in mics:
                set_row(eid, "queued…", COLORS["text3"])
            dialog.after(10, tick)

        for eid, name in mics:
            line = ctk.CTkFrame(body, fg_color="transparent")
            line.pack(fill="x", pady=2)
            ctk.CTkLabel(line, text=name, anchor="w",
                         font=ctk.CTkFont(size=11)).pack(side="left")
            lab = ctk.CTkLabel(line, text="—", anchor="e",
                               font=ctk.CTkFont(size=10))
            lab.pack(side="right")
            rows[eid] = lab
        btnbar = ctk.CTkFrame(dialog, fg_color="transparent")
        btnbar.pack(side="bottom", fill="x", padx=14, pady=10)
        ctk.CTkButton(btnbar, text="Close", width=90, height=30,
                      command=dialog.destroy).pack(side="right")
        ctk.CTkButton(btnbar, text="▶ Test all mics", width=140, height=30,
                      fg_color=COLORS["green_dark"],
                      hover_color=COLORS["green_hover"],
                      command=run_all).pack(side="right", padx=(0, 8))
        run_all()

    # ── Mic Filters dialog (OBS-style chain, all native C++) ─────────

    _MIC_FILTER_LABELS = (("suppression", "Noise Suppression"),
                          ("gainst", "Gain (dB)"),
                          ("compressor", "Compressor"),
                          ("limiter", "Limiter"),
                          ("deesser", "De-esser"),
                          ("miceq", "Mic EQ"),
                          ("echo", "Echo"),
                          ("upcomp", "Upward Compressor"),
                          ("expand", "Expander"))

    # Fine-parameter definitions for the Mic Filters dialog (OBS-style
    # sliders). (key, label, min, max, unit, step) — values are INTs in
    # the native store; step is the slider resolution.
    _MIC_STAGE_PARAMS = {
        "suppression": (
            ("sup_hp_hz", "High-pass cutoff", 40, 200, "Hz", 2),
            ("sup_gate_db", "Gate threshold", -60, -20, "dB", 1),
            ("gate_floor_pct", "Gate floor", 0, 60, "%", 5),
        ),
        "gainst": (
            ("gain_db", "Input gain", 0, 24, "dB", 1),
        ),
        "compressor": (
            ("comp_db", "Threshold", -48, -6, "dB", 1),
            ("comp_ratio10", "Ratio", 10, 80, ":1 x0.1", 5),
        ),
        "limiter": (
            ("lim_db", "Ceiling", -6, 0, "dB", 1),
        ),
        "deesser": (
            ("de_hz", "Split frequency", 2500, 8000, "Hz", 100),
            ("de_db", "Threshold", -36, -10, "dB", 1),
        ),
        "miceq": (
            ("eq_low_db", "Low shelf", -12, 6, "dB", 1),
            ("eq_high_db", "Presence shelf", -6, 12, "dB", 1),
        ),
        "echo": (
            ("echo_ms", "Delay", 30, 600, "ms", 10),
            ("echo_fb_pct", "Feedback", 0, 70, "%", 5),
            ("echo_wet_pct", "Wet mix", 0, 60, "%", 5),
        ),
        "upcomp": (
            ("up_db", "Threshold", -60, -20, "dB", 1),
            ("up_ratio10", "Ratio", 10, 40, ":1 x0.1", 5),
        ),
        "expand": (
            ("ex_db", "Threshold", -70, -30, "dB", 1),
            ("ex_depth_pct", "Depth", 20, 98, "%", 5),
        ),
    }

    # Factory defaults of every fine parameter (exactly the Gentle sound
    # of the mode-only releases) — used by the per-stage Defaults button.
    _MIC_PARAM_DEFAULTS = {
        "sup_hp_hz": 90, "sup_gate_db": -45, "gate_floor_pct": 25,
        "gain_db": 6, "comp_db": -24, "comp_ratio10": 35, "lim_db": -1,
        "de_hz": 3500, "de_db": -20, "eq_low_db": -3, "eq_high_db": 3,
        "echo_ms": 180, "echo_fb_pct": 30, "echo_wet_pct": 28,
        "up_db": -40, "up_ratio10": 15, "ex_db": -45, "ex_depth_pct": 75,
    }

    # ═══ Master Broadcast Chain (0926.2 P1) ═══
    _MASTER_CHAIN_LABELS = (("eq", "3-Band EQ"),
                            ("comp", "Compressor"),
                            ("limit", "Limiter"))
    _MASTER_CHAIN_DESC = {
        "eq": "Tone of the whole station: bass shelf 120 Hz, mid peak 1 kHz, treble shelf 3.2 kHz",
        "comp": "Evens loud/soft passages of the whole broadcast (music + mics)",
        "limit": "Safety ceiling - stops the on-air signal from clipping",
    }

    def _open_master_chain(self) -> None:
        """OBS-style dialog for the MASTER chain: EQ -> Compressor ->
        Limiter on the final broadcast mix (music + live mics).

        Each stage owns a pane and the stage list raises exactly one of
        them. The old version looked the pane host up by walking widget
        parents (``btn.master.master.master``) and comparing against the
        stage row frame, so it never found it: clicking a stage in the list
        did nothing and all three panes stayed stacked in one column. The
        panes live in a dict now and are switched directly — no layout
        archaeology. The dialog also says plainly when the native engine is
        idle (pygame path) instead of silently storing values nobody hears.
        """
        if not hasattr(self, "audio_player"):
            return
        mode, bass, mid, treb, comp_t, comp_r, ceil_db = \
            self.audio_player.get_master_chain()
        lim_on = ceil_db < 0
        if not lim_on:
            ceil_db = -1.0          # keep a sane slider value while switched off

        dialog = ctk.CTkToplevel(self)
        dialog.title("Master Broadcast Chain")
        dialog.geometry("660x580")
        dialog.minsize(600, 520)
        dialog.resizable(True, True)
        dialog.transient(self)

        top = ctk.CTkFrame(dialog, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 2))
        ctk.CTkLabel(top, text="Master broadcast chain",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(top, text="EQ -> Compressor -> Limiter on the ON-AIR mix",
                     font=ctk.CTkFont(size=9),
                     text_color=COLORS["text3"]).pack(side="left", padx=(10, 0))
        # Honest status: the chain only runs inside the native WASAPI
        # renderer, so on the pygame fallback nothing would be audible.
        engine_note = ctk.CTkLabel(dialog, text="", anchor="w", justify="left",
                                   font=ctk.CTkFont(size=9),
                                   text_color=COLORS["orange"])
        engine_note.pack(fill="x", padx=16)

        immediate_var = ctk.BooleanVar(
            value=bool(getattr(self, "_master_chain_immediate", False)))

        stage_vars = {
            "eq": ctk.BooleanVar(value=(bass != 0 or mid != 0 or treb != 0)),
            "comp": ctk.BooleanVar(value=mode != 0),
            "limit": ctk.BooleanVar(value=lim_on),
        }
        strength_var = ctk.StringVar(value="Strong" if mode == 2 else "Gentle")

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(2, 0))

        left = ctk.CTkScrollableFrame(body, width=190,
                                      fg_color=COLORS["bg_input"],
                                      corner_radius=8)
        left.pack(side="left", fill="y", padx=(4, 6), pady=4)
        # Plain frame for the pane host: the panes are switched with
        # pack/pack_forget, and a CTkScrollableFrame renders its children
        # into a canvas-owned inner frame (exactly the layout the old
        # parent-walking lookup could not find).
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, pady=4)

        panes = {key: ctk.CTkFrame(right, fg_color="transparent")
                 for key, _ in self._MASTER_CHAIN_LABELS}
        stage_buttons = {}
        for key, label in self._MASTER_CHAIN_LABELS:
            row = ctk.CTkFrame(left, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=3)
            dot = ctk.CTkLabel(row, text="\u25cf", width=14,
                               font=ctk.CTkFont(size=11),
                               text_color=COLORS["text3"])
            dot.pack(side="left")
            btn = ctk.CTkButton(row, text=label, anchor="w", height=30,
                                fg_color="transparent",
                                text_color=COLORS["text"],
                                hover_color=COLORS["bg_hover"],
                                corner_radius=6,
                                command=lambda k=key: show_stage(k))
            btn.pack(side="left", fill="x", expand=True)
            stage_buttons[key] = (dot, btn)

        sliders = {}
        slider_widgets = {}

        def set_dot(key, on):
            stage_buttons[key][0].configure(
                text_color=COLORS["green"] if on else COLORS["text3"])

        def refresh_dots():
            for key, var in stage_vars.items():
                set_dot(key, bool(var.get()))

        def show_stage(key):
            """Raise ONE stage pane and highlight its row."""
            for k, pane in panes.items():
                if k == key:
                    pane.pack(fill="both", expand=True)
                else:
                    pane.pack_forget()
            for k, (dot, btn) in stage_buttons.items():
                btn.configure(fg_color=COLORS["bg_hover"] if k == key
                              else "transparent")

        def set_slider(key, value):
            """Move the WIDGET, not just the dict.

            Defaults used to reset only the dict, so the old slider
            positions stayed on screen and the next commit pushed them
            straight back into the engine.
            """
            s, val, fmt = slider_widgets[key]
            s.set(value)
            sliders[key] = float(value)
            val.configure(text=fmt(float(value)))

        def chain_params():
            """The exact 7 values the engine takes for the current UI state."""
            eq_on = bool(stage_vars["eq"].get())
            b = float(sliders.get("eq_bass", 0.0)) if eq_on else 0.0
            mi = float(sliders.get("eq_mid", 0.0)) if eq_on else 0.0
            t = float(sliders.get("eq_treb", 0.0)) if eq_on else 0.0
            comp_on = bool(stage_vars["comp"].get())
            m = 0 if not comp_on else (
                2 if strength_var.get() == "Strong" else 1)
            thr = float(sliders.get("comp_thr", -24.0))
            ratio = int(round(float(sliders.get("comp_ratio", 30.0))))
            # The native limiter is ON when its ceiling is < 0 dBFS, so an
            # OFF switch must send 0.0 — passing -1.0 left it engaged and
            # the switch did nothing audible.
            ceil = (min(float(sliders.get("limit_ceil", -1.0)), -0.1)
                    if stage_vars["limit"].get() else 0.0)
            return m, b, mi, t, thr, ratio, ceil

        def commit(_=None):
            self.audio_player.set_master_chain(*chain_params())
            self._mark_settings_dirty()
            refresh_dots()
            refresh_status()

        def release_commit(_=None):
            if immediate_var.get():
                commit()

        # ── per-stage widgets ──
        def slider(parent, key, label, frm, to, init, fmt, res=1.0):
            fr = ctk.CTkFrame(parent, fg_color="transparent")
            fr.pack(fill="x", padx=10, pady=4)
            ctk.CTkLabel(fr, text=label, width=120, anchor="w",
                         font=ctk.CTkFont(size=11),
                         text_color=COLORS["text2"]).pack(side="left")
            val = ctk.CTkLabel(fr, width=76, anchor="e",
                               font=ctk.CTkFont(size=10),
                               text_color=COLORS["text"])
            val.pack(side="right")
            s = ctk.CTkSlider(
                fr, from_=frm, to=to,
                number_of_steps=max(1, int(round((to - frm) / res))),
                command=lambda v, k=key, l=val, f=fmt: (
                    sliders.__setitem__(k, float(v)),
                    l.configure(text=f(float(v)))))
            s.set(init)
            sliders[key] = float(init)
            slider_widgets[key] = (s, val, fmt)
            val.configure(text=fmt(float(init)))
            # Push once per gesture, on release — never a per-tick flood.
            s.bind("<ButtonRelease-1>", release_commit)
            s.pack(side="left", fill="x", expand=True, padx=8)

        def stage_switch(parent, key):
            return ctk.CTkSwitch(
                parent, text="Enable", variable=stage_vars[key],
                onvalue=True, offvalue=False,
                command=lambda: (commit() if immediate_var.get()
                                 else refresh_dots()))

        eq_pane = panes["eq"]
        stage_switch(eq_pane, "eq").pack(anchor="w", padx=10, pady=(6, 2))
        ctk.CTkLabel(eq_pane, text=self._MASTER_CHAIN_DESC["eq"],
                     font=ctk.CTkFont(size=9), justify="left", wraplength=380,
                     text_color=COLORS["text3"]).pack(anchor="w", padx=10)
        slider(eq_pane, "eq_bass", "Bass (120 Hz)", -12, 12, bass,
               lambda v: f"{v:+.0f} dB")
        slider(eq_pane, "eq_mid", "Mid (1 kHz)", -12, 12, mid,
               lambda v: f"{v:+.0f} dB")
        slider(eq_pane, "eq_treb", "Treble (3.2 kHz)", -12, 12, treb,
               lambda v: f"{v:+.0f} dB")

        comp_pane = panes["comp"]
        stage_switch(comp_pane, "comp").pack(anchor="w", padx=10, pady=(6, 2))
        prow = ctk.CTkFrame(comp_pane, fg_color="transparent")
        prow.pack(fill="x", padx=10, pady=(0, 2))
        ctk.CTkLabel(prow, text="Character", anchor="w",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text2"]).pack(side="left")
        ctk.CTkSegmentedButton(
            prow, values=["Gentle", "Strong"], variable=strength_var,
            command=lambda _=None: (commit() if immediate_var.get()
                                    else refresh_dots())
        ).pack(side="right")
        ctk.CTkLabel(comp_pane, text=self._MASTER_CHAIN_DESC["comp"],
                     font=ctk.CTkFont(size=9), justify="left", wraplength=380,
                     text_color=COLORS["text3"]).pack(anchor="w", padx=10)
        slider(comp_pane, "comp_thr", "Threshold", -48, -6, comp_t,
               lambda v: f"{v:.0f} dB")
        slider(comp_pane, "comp_ratio", "Ratio", 10, 80, comp_r,
               lambda v: f"{v / 10.0:.1f}:1")

        lim_pane = panes["limit"]
        stage_switch(lim_pane, "limit").pack(anchor="w", padx=10, pady=(6, 2))
        ctk.CTkLabel(lim_pane, text=self._MASTER_CHAIN_DESC["limit"],
                     font=ctk.CTkFont(size=9), justify="left", wraplength=380,
                     text_color=COLORS["text3"]).pack(anchor="w", padx=10)
        slider(lim_pane, "limit_ceil", "Ceiling", -6, 0, ceil_db,
               lambda v: f"{v:.1f} dB", res=0.1)

        # ── live readout: what the ON-AIR mix is actually going through ──
        status_frame = ctk.CTkFrame(dialog, fg_color=COLORS["bg_input"],
                                    corner_radius=6)
        status_frame.pack(fill="x", padx=14, pady=(6, 2))
        status_lbl = ctk.CTkLabel(status_frame, text="", anchor="w",
                                  justify="left", wraplength=600,
                                  font=ctk.CTkFont(size=10),
                                  text_color=COLORS["text"])
        status_lbl.pack(fill="x", padx=10, pady=(4, 0))
        probe_lbl = ctk.CTkLabel(status_frame, text="", anchor="w",
                                 justify="left", wraplength=600,
                                 font=ctk.CTkFont(size=9),
                                 text_color=COLORS["text3"])
        probe_lbl.pack(fill="x", padx=10, pady=(0, 4))

        def refresh_status():
            try:
                params = self.audio_player.get_master_chain()
            except Exception:
                params = chain_params()
            status_lbl.configure(text=self._master_chain_summary(params))
            if getattr(self.audio_player, "_engine", "pygame") == "wasapi":
                engine_note.configure(text="")
            else:
                engine_note.configure(
                    text="\u26a0 Native output is idle (pygame) \u2014 these "
                         "stages apply as soon as the WASAPI output plays.")
            self._refresh_master_chain_status()

        def apply_and_close():
            commit()
            self._master_chain_immediate = immediate_var.get()
            self._refresh_freq_roundtrip()
            dialog.destroy()

        def defaults():
            """Factory state: every stage OFF, parameters at their defaults.

            Unlike Mic Filters (where Defaults keeps the Enabled switch,
            because a mic filter is usually tuned, not silenced), the
            broadcast chain's factory state IS "off" — the documented raw,
            bit-identical passthrough.
            """
            strength_var.set("Gentle")
            for var in stage_vars.values():
                var.set(False)
            for key, value in (("eq_bass", 0.0), ("eq_mid", 0.0),
                               ("eq_treb", 0.0), ("comp_thr", -24.0),
                               ("comp_ratio", 30.0), ("limit_ceil", -1.0)):
                set_slider(key, value)
            probe_lbl.configure(text="")
            commit()

        def run_selftest():
            ok, msg = self._master_chain_selftest()
            probe_lbl.configure(
                text=("\u2705 Self-test: " if ok else "\u274c Self-test: ") + msg,
                text_color=COLORS["green"] if ok else COLORS["red"])

        bottom = ctk.CTkFrame(dialog, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(2, 10))
        ctk.CTkSwitch(bottom, text="Immediate mode (apply while you move)",
                      variable=immediate_var, onvalue=True, offvalue=False
                      ).pack(side="left")
        ctk.CTkButton(bottom, text="Close", width=80, height=30,
                      fg_color=COLORS["accent"],
                      hover_color=COLORS["accent_hover"],
                      command=apply_and_close).pack(side="right")
        ctk.CTkButton(bottom, text="Apply", width=80, height=30,
                      fg_color=COLORS["bg_hover"],
                      hover_color=COLORS["border"],
                      command=commit).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bottom, text="Defaults", width=90, height=30,
                      fg_color=COLORS["bg_hover"],
                      hover_color=COLORS["border"],
                      command=defaults).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bottom, text="Test chain", width=100, height=30,
                      fg_color=COLORS["bg_hover"],
                      hover_color=COLORS["border"],
                      command=run_selftest).pack(side="right", padx=(6, 0))

        # Closing the window must apply too — with Immediate off, the X used
        # to throw the edits away without a hint.
        dialog.protocol("WM_DELETE_WINDOW", apply_and_close)

        show_stage("eq")
        refresh_dots()
        refresh_status()

        # Handles the smoke test drives (same contract as the public API).
        dialog._mc = {
            "panes": panes, "show": show_stage, "sliders": slider_widgets,
            "set": set_slider, "stage_vars": stage_vars,
            "strength": strength_var, "params": chain_params,
            "commit": commit, "defaults": defaults, "apply": apply_and_close,
            "status": status_lbl, "probe": probe_lbl,
            "note": engine_note, "selftest": self._master_chain_selftest,
        }

    @staticmethod
    def _master_chain_summary(params) -> str:
        """One-line description of a master-chain state (dialog + Settings)."""
        try:
            mode, b, m, t, thr, ratio, ceil = params
            mode, b, m, t = int(mode), float(b), float(m), float(t)
            thr, ratio, ceil = float(thr), float(ratio), float(ceil)
        except (TypeError, ValueError):
            return "Unknown state"
        parts = []
        if b or m or t:
            parts.append(f"EQ {b:+.0f}/{m:+.0f}/{t:+.0f} dB")
        if mode:
            parts.append(f"Comp {ratio / 10.0:.1f}:1 @ {thr:.0f} dB"
                         + (" (strong)" if mode == 2 else " (gentle)"))
        if ceil < 0:
            parts.append(f"Limiter {ceil:.1f} dB")
        return " \u2192 ".join(parts) if parts else \
            "OFF \u2014 raw mix, bit-identical passthrough"

    @staticmethod
    def _master_chain_selftest():
        """Run the native DSP self-test (no audio device needed).

        Returns (ok, message). The C++ test proves the stages do real work:
        OFF is byte-identical, EQ lifts a 60 Hz sine, the compressor pulls a
        loud 1 kHz peak toward the threshold and the limiter clips at the
        ceiling. Running it from the dialog separates "the parameters are
        wired" from "the DSP is broken".
        """
        try:
            import native_audio_render as nar
            code = int(nar.render_master_selftest())
        except Exception as exc:
            return False, f"native engine unavailable ({exc})"
        if code == 0:
            return True, "EQ, compressor and limiter all pass"
        return False, f"stage {code} failed — the native engine needs a rebuild"
    def _open_mic_filters(self) -> None:
        """OBS-style two-pane filter dialog: the chain on the left, the
        selected stage's parameters on the right. Every stage has an
        on/off switch, Gentle/Strong starting points and fine sliders.
        "Immediate mode" (persisted) applies every edit to the native
        engine the moment it is made; otherwise nothing changes until
        Apply. Slider pushes happen on BUTTON RELEASE (not per tick)
        so the engine receives a handful of atomic stores, never a flood."""
        if not hasattr(self, "audio_player"):
            return
        current = self.audio_player.mic_filters()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Mic Filters")
        dialog.geometry("760x620")
        dialog.minsize(640, 540)
        dialog.resizable(True, True)
        dialog.transient(self)

        # Top bar: title + the Immediate-mode toggle.
        top = ctk.CTkFrame(dialog, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(top, text="Mic filter chain",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left")
        ctk.CTkLabel(top, text="9 native stages · order fixed · sliders are live",
                     font=ctk.CTkFont(size=9),
                     text_color=COLORS["text3"]).pack(side="left", padx=(10, 0))
        immediate_var = ctk.BooleanVar(
            value=bool(getattr(self, "_mic_filters_immediate", False)))

        body = ctk.CTkFrame(dialog, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=(2, 0))

        # ── Left pane: the stage list (OBS filter list) ──
        left = ctk.CTkScrollableFrame(
            body, width=190, fg_color=COLORS["bg_input"],
            corner_radius=8)
        left.pack(side="left", fill="y", padx=(4, 6), pady=4)

        # ── Right pane: the selected stage's parameters ──
        right = ctk.CTkScrollableFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, pady=4)

        state = {"stage": None, "rows": [], "editor": None,
                 "apply_btn": None}

        def push_stage(stage, var, strength_var, params) -> None:
            """Push one stage (mode + fine params) into the engine NOW."""
            mode = 0 if not var.get() else (
                2 if strength_var.get() == "Strong" else 1)
            self.audio_player.set_mic_filter(stage, mode)
            current[stage] = mode        # keep the snapshot LIVE — the
                                         # dots read from it
            for key, var_v, _unit, step in params:
                self.audio_player.mic_param_set(
                    key, int(var_v.get()) // step * step)
            self._mark_settings_dirty()

        def build_editor(stage) -> None:
            """(Re)build the right pane for the selected stage."""
            for w in right.winfo_children():
                w.destroy()
            state["stage"] = stage
            label = dict(self._MIC_FILTER_LABELS)[stage]
            ctk.CTkLabel(right, text=label, anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(
                fill="x", padx=6, pady=(6, 0))
            var = ctk.BooleanVar(value=bool(current.get(stage, 0)))
            strength = ctk.StringVar(
                value="Strong" if current.get(stage, 0) == 2 else "Gentle")
            prow = ctk.CTkFrame(right, fg_color="transparent")
            prow.pack(fill="x", padx=6, pady=(6, 0))

            params = []
            editor = {"stage": stage, "var": var, "strength": strength,
                      "params": params}

            def mode_now(_e=None) -> None:
                # The dot mirrors the switch in BOTH modes — when Immediate
                # is off it shows what Apply will commit.
                if immediate_var.get():
                    push_stage(stage, var, strength, params)
                refresh_dots()

            ctk.CTkSwitch(prow, text="Enabled", variable=var,
                          onvalue=True, offvalue=False,
                          command=mode_now).pack(side="left")
            ctk.CTkSegmentedButton(
                prow, values=["Gentle", "Strong"], variable=strength,
                command=mode_now).pack(side="right")
            ctk.CTkLabel(right,
                         text={"suppression":
                               "High-pass + noise gate (rumble, hiss, room, fans)",
                               "compressor":
                               "Evens loud/soft phrases (broadcast voice balance)",
                               "limiter":
                               "Safety ceiling — stops accidental clipping",
                               "deesser":
                               "Tames sharp S sounds (~5 kHz band only)",
                               "miceq":
                               "Voice tilt: trims boom, lifts presence (radio shape)",
                               "echo":
                               "Station-ID style echo repeats on the mic",
                               "gainst": "Input trim in dB",
                               "upcomp":
                               "Lifts QUIET parts up (whispers stay audible)",
                               "expand":
                               "Pushes the noise floor down between words",
                               }[stage],
                         font=ctk.CTkFont(size=9),
                         text_color=COLORS["text3"], anchor="w",
                         justify="left").pack(fill="x", padx=6)

            for key, plabel, lo, hi, unit, step in self._MIC_STAGE_PARAMS.get(stage, ()):
                srow = ctk.CTkFrame(right, fg_color="transparent")
                srow.pack(fill="x", padx=6, pady=(8, 0))
                ctk.CTkLabel(srow, text=plabel, width=120, anchor="w",
                             font=ctk.CTkFont(size=10)).pack(side="left")
                val = self.audio_player.mic_param_get(key)
                if val is None or val < 0:
                    val = self._MIC_PARAM_DEFAULTS.get(key, lo)
                val = max(lo, min(hi, int(val)))
                var_v = ctk.IntVar(value=val)
                num = ctk.CTkLabel(srow, text=f"{val} {unit}", width=84,
                                   anchor="e", font=ctk.CTkFont(size=10),
                                   text_color=COLORS["text2"])

                def on_slide(v, n=num, u=unit, s=step):
                    n.configure(text=f"{int(v) // s * s} {u}")

                sl = ctk.CTkSlider(srow, from_=lo, to=hi, variable=var_v,
                                    number_of_steps=max(1, (hi - lo) // step),
                                    width=260, command=on_slide)
                sl.pack(side="left", padx=(0, 10))
                num.pack(side="left")

                # STABILITY: push once per gesture, on button release —
                # the engine gets atomic stores, not a per-tick flood.
                def on_release(_e=None, k=key, vv=var_v, s=step):
                    if immediate_var.get():
                        self.audio_player.mic_param_set(
                            k, int(vv.get()) // s * s)
                        self._mark_settings_dirty()

                sl.bind("<ButtonRelease-1>", on_release)
                params.append((key, var_v, unit, step))

            def reset_stage() -> None:
                """OBS "Defaults": parameters back to the factory gentle
                sound. The Enabled switch is PRESERVED — Defaults must
                never silently re-enable a filter you switched off."""
                strength.set("Gentle")
                for key, var_v, unit, step in params:
                    dflt = self._MIC_PARAM_DEFAULTS.get(key, 0)
                    var_v.set(dflt)
                build_editor(stage)      # redraw labels from the vars
                if immediate_var.get():
                    push_stage(stage, var, strength, params)
                refresh_dots()           # dots follow the real state

            editor["reset"] = reset_stage
            state["editor"] = editor
            refresh_dots()               # dots follow the real state

        def refresh_dots() -> None:
            """Dots mirror the TRUE state: the stage being edited follows
            its own switch; other stages follow the live snapshot."""
            for stage, _roww, dot in state["rows"]:
                if state["stage"] == stage and state["editor"]:
                    on = bool(state["editor"]["var"].get())
                else:
                    on = bool(current.get(stage, 0))
                dot.configure(text_color=COLORS["green"] if on
                              else COLORS["text3"])

        def select(stage) -> None:
            for s, roww, _dot in state["rows"]:
                roww.configure(
                    fg_color=COLORS["bg_selected"] if s == stage
                    else "transparent")
            build_editor(stage)

        for stage, label in self._MIC_FILTER_LABELS:
            roww = ctk.CTkButton(
                left, text=label, anchor="w", height=30,
                corner_radius=6, fg_color="transparent",
                hover_color=COLORS["bg_hover"],
                text_color=COLORS["text"],
                command=lambda s=stage: select(s))
            roww.pack(fill="x", padx=4, pady=2)
            dot = ctk.CTkLabel(roww, text="●", width=16,
                               font=ctk.CTkFont(size=10),
                               text_color=COLORS["text3"])
            dot.place(relx=1.0, rely=0.5, anchor="e", x=-8)
            state["rows"].append((stage, roww, dot))

        # ── Bottom bar: Immediate-mode toggle + Defaults/Apply/Close ──
        btnbar = ctk.CTkFrame(dialog, fg_color="transparent")
        btnbar.pack(side="bottom", fill="x", padx=14, pady=10)

        def toggle_immediate() -> None:
            self._mic_filters_immediate = bool(immediate_var.get())
            if self._mic_filters_immediate and state["editor"]:
                # Nothing staged is lost: push the visible editor once.
                e = state["editor"]
                push_stage(e["stage"], e["var"], e["strength"],
                           e["params"])
                refresh_dots()
            if self._mic_filters_immediate:
                state["apply_btn"].pack_forget()
            else:
                state["apply_btn"].pack(side="right", padx=(0, 8))

        ctk.CTkSwitch(btnbar,
                      text="Immediate mode (apply while you move)",
                      variable=immediate_var,
                      command=toggle_immediate).pack(side="left")

        def apply_filters() -> None:
            if state["editor"] is None:
                return
            # Push the stage being edited, then every other stage keeps
            # its remembered values (they were applied when edited).
            e = state["editor"]
            push_stage(e["stage"], e["var"], e["strength"], e["params"])
            refresh_dots()
            self._refresh_mic_filters_status()
            dialog.destroy()

        apply_btn = ctk.CTkButton(btnbar, text="Apply", width=96,
                                  height=30, command=apply_filters,
                                  fg_color=COLORS["green"],
                                  hover_color=COLORS["green_hover"],
                                  text_color="#000000")
        apply_btn.pack(side="right", padx=(0, 8))
        state["apply_btn"] = apply_btn

        def defaults_clicked() -> None:
            if state["editor"]:
                state["editor"]["reset"]()

        ctk.CTkButton(btnbar, text="Defaults", width=96, height=30,
                      fg_color=COLORS["bg_hover"],
                      hover_color=COLORS["border"],
                      command=defaults_clicked).pack(side="right",
                                                     padx=(0, 8))
        ctk.CTkButton(btnbar, text="Close", width=88, height=30,
                      command=dialog.destroy).pack(side="right")

        if immediate_var.get():
            apply_btn.pack_forget()   # everything already applies live
        select("suppression")         # open on the first stage, OBS-style
        refresh_dots()

    def _refresh_mic_filters_status(self) -> None:
        """One-line summary under the Settings button."""
        label = getattr(self, "mic_filters_status", None)
        if label is None or not hasattr(self, "audio_player"):
            return
        try:
            modes = self.audio_player.mic_filters()
        except Exception:
            return
        names = {"suppression": "Suppression", "compressor": "Comp",
                 "limiter": "Limiter", "deesser": "De-ess",
                 "miceq": "EQ", "echo": "Echo", "gainst": "Gain",
                 "upcomp": "Up-Comp", "expand": "Expand"}
        active = [f"{names[s]}: {['Off', 'Gentle', 'Strong'][m]}"
                  for s, m in modes.items() if m]
        label.configure(text=" · ".join(active) if active
                        else "All filters off — raw mic sound")

    def _refresh_master_chain_status(self) -> None:
        """One-line summary under the Master Chain button.

        Without it the section only had a Configure button: nothing on the
        Settings page said whether the ON-AIR mix was being processed.
        """
        label = getattr(self, "master_chain_status", None)
        if label is None or not hasattr(self, "audio_player"):
            return
        try:
            params = self.audio_player.get_master_chain()
        except Exception:
            return
        text = self._master_chain_summary(params)
        label.configure(text=text if text.startswith("OFF") else f"Chain: {text}")

    def _open_plugin_folder(self) -> None:
        manager = getattr(self, "plugin_manager", None)
        folder = str(getattr(manager, "folder", "") or "") if manager else ""
        if folder:
            try:
                os.startfile(folder)
            except OSError:
                pass

    def _save_settings_to_freq(self) -> None:
        """Save all settings (including queue) to .freq file."""
        self._sync_anthem_times()
        settings = self._collect_settings()
        try:
            self.rq.save_to_file(settings=settings)
            self._remember_freq_snapshot(settings, source="saved")
            self._refresh_freq_roundtrip()
            self._save_settings_status.configure(
                text=f"\u2705 Saved to {self.rq._save_path.name}",
                text_color=COLORS["green"],
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
            text_color=COLORS["green"],
        )

    # ── Settings Dirty State ──
    def _build_settings_dirty_banner(self) -> None:
        """Create floating unsaved-changes banner on the settings tab."""
        self._dirty_banner = ctk.CTkFrame(
            self.tab_settings, fg_color=COLORS["bg_selected"], corner_radius=12,
            border_color=COLORS["accent"], border_width=1,
        )
        # Pack at top — initially hidden via pack_forget
        ctk.CTkLabel(
            self._dirty_banner,
            text="\u26a0\ufe0f  Settings changed",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text"],
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
            fg_color="transparent", hover_color=COLORS["bg_hover"],
            corner_radius=6, font=ctk.CTkFont(size=10),
            text_color=COLORS["text2"], border_width=1, border_color=COLORS["border"],
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
        """Stop current playback before the anthem (scheduler thread).

        Kept for callback compatibility with the scheduler — but it STOPS
        (with a position snapshot), never pauses. A paused WASAPI stream
        swallowed the anthem's play_file and the anthem played silently.
        """
        def _do_pause():
            self._anthem_was_playing = self._playing
            self._anthem_saved_index = self.rq.current_index
            self._anthem_saved_position = 0.0
            if self._playing and self.audio_player.available:
                self._anthem_saved_position = self.audio_player.get_position()
            self._playing = False
            self.audio_player.stop()
        self.after(0, _do_pause)

    def _anthem_play(self, filepath: str) -> None:
        """Play the anthem file (called from scheduler thread).

        The WHOLE cycle runs as one step on the main thread — snapshot,
        stop, start. Splitting stop and start across two ``after`` jobs
        let them interleave with jingle/queue handlers and race.
        """
        def _do_play():
            if self._jingle_playing or self._anthem_playing:
                self.anthem.confirm_played(False)
                return  # jingle owns the stream; its resume path continues
            # Snapshot the interrupted track BEFORE stopping anything.
            self._anthem_was_playing = self._playing
            self._anthem_saved_index = self.rq.current_index
            self._anthem_saved_position = 0.0
            if self._playing and self.audio_player.available:
                self._anthem_saved_position = self.audio_player.get_position()
            # As-run: the anthem is about to cut whatever is playing. That
            # cut is the reason the inspector will want to see on the song.
            self._asrun_end_active("anthem-cut")
            self._playing = False
            self.audio_player.stop()
            self._anthem_playing = True
            dur = self.anthem.config.duration
            if dur <= 0:
                dur = self._detect_anthem_duration(filepath)
                self.anthem.config.duration = dur
            if self.audio_player.available:
                ok = self.audio_player.play_file(
                    filepath, duration=dur,
                    on_finish=lambda: self.after(0, self._anthem_resume))
                if not ok:
                    self._anthem_playing = False
                    self._show_toast("⚠️ Cannot play anthem file")
                    self._anthem_resume()
                    self.anthem.confirm_played(False)
                    return
                self._playing = True
                self._active_source_channel = "anthem"
                self._apply_mixer_volumes()
                self.btn_play.configure(text="⏸")
                self.np_title.configure(text="🇹🇭 National Anthem")
                self.np_artist.configure(text="Now playing the national anthem")
                self._asrun_begin("anthem", title="national anthem", path=filepath,
                                  duration=dur, source_type="file",
                                  channel="anthem",
                                  reason="anthem time (automatic)")
                if streamer.is_streaming:
                    streamer.on_track_change("National Anthem", "", filepath)
                # Confirmed audible — only now does the scheduler mark
                # this period as played for today.
                self.anthem.confirm_played(True)
            else:
                self._anthem_playing = False
                self.anthem.confirm_played(False)
        self.after(0, _do_play)

    def _anthem_resume(self) -> None:
        """Restore the interrupted song after the anthem."""
        def _do_resume():
            was_playing = self._anthem_was_playing
            # Consume the saved state — makes a second resume harmless.
            self._anthem_was_playing = False
            self._anthem_playing = False
            self._active_source_channel = "music"
            self._apply_mixer_volumes()
            # Resume only when the anthem truly owned the stream (EOF is
            # the finish signal) and nothing newer has taken over — a
            # jingle or manual track that started meanwhile wins.
            if not was_playing or self._jingle_playing:
                return
            cur = self.audio_player._current_file
            if cur and os.path.abspath(cur) != os.path.abspath(
                    self.anthem.config.anthem_file or ""):
                return  # something else started — do not clobber it
            self._asrun_end_active("natural", kind="anthem")
            idx = getattr(self, '_anthem_saved_index', self.rq.current_index)
            pos = getattr(self, '_anthem_saved_position', 0.0)
            if idx is None or not (0 <= idx < len(self.rq.queue)):
                return
            song = self.rq.queue[idx]
            self._saved_playback_position = pos
            self._playing = True
            self._asrun_begin("music", song, channel="music",
                              reason="resumed the song after the anthem")
            if song.source == "youtube" and song.url:
                self.audio_player.play_youtube(
                    song.url, duration=song.duration, song_id=song.id,
                    title=song.title,
                    on_finish=lambda: self.after(0, self._on_track_finished),
                    start_position=pos,
                )
            elif song.file_path and os.path.isfile(song.file_path):
                self.audio_player.play_file(
                    song.file_path, duration=song.duration,
                    on_finish=lambda: self.after(0, self._on_track_finished),
                )
                if pos > 0.3:
                    self.after(100, lambda p=pos: self.audio_player.seek(p))
            else:
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

    def _anthem_confirm(self, ok: bool) -> None:
        """Forward play confirmation to the scheduler (main thread)."""
        try:
            self.anthem.confirm_played(ok)
        except Exception:
            pass

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
        if self._channel_factor("anthem") == 0.0:
            self._show_toast("🇹🇭 Anthem channel is muted in the mixer")
            return
        # Snapshot the interrupted track BEFORE stopping anything.
        self._anthem_was_playing = self._playing
        self._anthem_saved_index = self.rq.current_index
        self._anthem_saved_position = 0.0
        if self._playing and self.audio_player.available:
            self._anthem_saved_position = self.audio_player.get_position()
        self._playing = False
        self.audio_player.stop()
        # Play anthem — finishes through the standard on_finish path.
        self._anthem_playing = True
        self._active_source_channel = "anthem"
        self._apply_mixer_volumes()
        duration = self.anthem.config.duration
        if duration <= 0:
            duration = self._detect_anthem_duration()
            self.anthem.config.duration = duration
        if self.audio_player.available:
            ok = self.audio_player.play_file(
                self.anthem.config.anthem_file, duration=duration,
                on_finish=lambda: self.after(0, self._anthem_resume))
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
        """Emergency Mic — open a LIVE microphone mixed into the output.

        The mic lives inside the C++ WASAPI engine: voice is mixed into the
        same device buffers as the music (~20 ms path, real-time). On the
        pygame engine (or a dead capture endpoint) we still duck the music
        and report honestly instead of pretending the mic is on.
        """
        log = logging.getLogger("freq.gui")
        if not self.mic.emergency:
            self.mic.emergency = True
            # Save current volume and duck it
            self._pre_duck_volume = self.volume_var.get()
            duck_vol = self.mic.duck_volume * 100.0
            self.volume_var.set(duck_vol)
            self.volume_slider.set(duck_vol)
            self._on_volume_change(duck_vol)
            self.btn_mute.configure(text="🎤")
            started = False
            try:
                started = self.audio_player.play_mic_live(
                    0.0,                      # sticky: open until toggled off
                    mic_gain=max(0.5, self.mic.gain),
                    duck_factor=0.0)          # GUI already ducked via slider
            except Exception:
                log.exception("emergency mic start failed")
                started = False
            if started:
                self.np_status.configure(
                    text="🎤 Emergency mic LIVE", text_color=COLORS["green"])
            else:
                self.np_status.configure(
                    text="⚠ mic unavailable on this engine — music ducked only",
                    text_color=COLORS["orange"])
                log.warning("emergency mic could not open a capture endpoint")
        else:
            self.mic.emergency = False
            # Restore original volume
            restore_vol = getattr(self, '_pre_duck_volume', 80.0)
            self.volume_var.set(restore_vol)
            self.volume_slider.set(restore_vol)
            self._on_volume_change(restore_vol)
            self.btn_mute.configure(text="🔊")
            try:
                self.audio_player.stop_emergency_mic()
            except Exception:
                log.exception("emergency mic stop failed")
            self.np_status.configure("")

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
        """Apply a named curve to the legacy EQ model.

        There are no Settings sliders to mirror any more — the Master
        Broadcast Chain dialog is the only EQ surface and it reads the real
        bands back from the player after every push.
        """
        self.eq.apply_preset(name)
        self._push_eq()

    def _on_eq_slider(self, attr: str, value: float) -> None:
        setattr(self.eq, attr, value)
        self._push_eq()

    def _push_eq(self) -> None:
        """Send the current EQ gains to the audio engine."""
        try:
            self.audio_player.set_eq(self.eq.bass, self.eq.mid, self.eq.treble)
        except Exception:
            pass

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
        self._refresh_freq_roundtrip()

    @staticmethod
    def _coerce_autosave_interval(value) -> int:
        """Clamp an autosave interval to something that can actually run.

        Floor of 5 s keeps a stray keystroke from hammering the disk, and
        the 24 h ceiling keeps a hand-edited .freq from silently turning
        autosave off (a huge interval simply never elapses). Garbage falls
        back to the 60 s default instead of raising during restore.
        """
        try:
            val = int(value)
        except (TypeError, ValueError):
            return 60
        return max(5, min(val, 86400))

    def _on_autosave_interval_change(self) -> None:
        val = self._coerce_autosave_interval(
            self.autosave_interval_entry.get())
        self.autosave_interval_entry.delete(0, "end")
        self.autosave_interval_entry.insert(0, str(val))
        self._autosave_interval = val
        self._refresh_freq_roundtrip()

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
    # ── Crash containment ────────────────────────────────────────

    def report_callback_exception(self, exc, val, tb) -> None:
        """Contain an exception raised inside ANY Tk callback.

        Tk's default handler prints the traceback and keeps going, but a
        packaged (windowed) build has no stderr, so a callback that raises on
        every tick — a poller, the queue painter, a button — would fail
        invisibly. Here it is counted, logged with its traceback, surfaced to
        the user at most once every 30 s, and never re-raised: the event loop
        stays alive and playback continues.
        """
        try:
            self._callback_errors = getattr(self, "_callback_errors", 0) + 1
            logging.getLogger("freq").error(
                "contained UI error #%d in a Tk callback: %r",
                self._callback_errors, val, exc_info=(exc, val, tb))
        except Exception:
            pass
        try:
            now = time.monotonic()
            if now - getattr(self, "_last_error_toast", 0.0) > 30.0:
                toast = getattr(self, "_show_toast", None)
                if toast is not None:
                    self._last_error_toast = now
                    toast("⚠️ Internal error contained — playback continues",
                          duration=4000)
        except Exception:
            pass

    def _on_close(self) -> None:
        """Save and shutdown cleanly"""
        # Release the monitor engine before the process goes away.
        try:
            self._stop_preview(quiet=True)
        except Exception:
            pass
        self._sync_anthem_times()
        # As-run: close whatever was on air (so no item is left open) and
        # seal today's file, so the day is complete even if the app is
        # reopened later that night.
        try:
            self._asrun_end_active("stopped")
            self.asrun.write_manifest()
        except Exception:
            logging.getLogger("freq.asrun").exception("as-run close failed")
        self._anthem_resume_token += 1
        self._anthem_resume_cancel.set()
        # Tell the supervisor this is a CLEAN stop before anything else —
        # it must never restart over the user closing the window.
        try:
            if getattr(self, "_ui_watchdog", None) is not None:
                self._ui_watchdog.stop()
        except Exception:
            pass
        self.anthem.stop()
        settings = self._collect_settings()
        self.audio_player.shutdown()
        self.rq.save_to_file(settings=settings)
        # A scheduled/manual recording must not keep a WASAPI capture
        # handle after the app is gone.
        try:
            rec = getattr(self, "aircheck_recorder", None)
            if rec is not None and rec.recording:
                rec.stop()
        except Exception:
            pass
        # Dual Output: release the second engine and the loopback tap.
        try:
            self._dual_output.stop()
        except Exception:
            pass
        # Undo Focus Mode (sleep guard, update block, toast suppression)
        try:
            if getattr(self, "focus_mode", None) is not None:
                self.focus_mode.release_all()
        except Exception:
            pass
        self.destroy()

    def destroy(self) -> None:
        # Stop the queue's scroll poller first: it re-arms itself every
        # 40 ms, and an `after` on a dying window is a traceback per tick.
        token = self.__dict__.get("_queue_view_after")
        if token:
            try:
                self.after_cancel(token)
            except Exception:
                pass
            self._queue_view_after = None
        # Let plugins say goodbye, but never let one hang the exit.
        try:
            _shutdown_thread = self._emit_plugins("on_shutdown")
            if _shutdown_thread is not None:
                _shutdown_thread.join(timeout=1.5)
        except Exception:
            pass
        try:
            self.audio_player.shutdown()
        except Exception:
            pass
        super().destroy()


# ═══════════════════════════════════════════════════════════════
# Splash Screen
# ═══════════════════════════════════════════════════════════════

def _splash_version() -> str:
    """The installer's version, however we can get it.

    Order: version.txt (stamped from installer.iss by build.py — the
    packaged app has no installer.iss beside it), then installer.iss
    directly (running from source), else the PyInstaller _MEIPASS data
    dir, else "dev".
    """
    import re
    candidates = [Path(__file__).with_name("version.txt")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "version.txt")
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            pass
    try:
        content = Path(__file__).with_name("installer.iss").read_text(encoding="utf-8")
        match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^"]+)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "dev"


class FreQSplash(ctk.CTkToplevel):
    """Broadcast-style startup splash.

    Layout (720x540 — the same 4:3 aspect as the 800x600 splash.jpg):
      - The photo fills the whole window; a left-to-right gradient scrim
        fades it into the panel color under the (narrow) text column so
        the text stays readable while the photo stays the hero — it is
        visible from ~45% of the width rightward.
      - Overlaid left: animated equalizer logo, Ultimate Series kicker,
        product wordmark, tagline and the release id (accent-colored for
        Ultimate builds, plain for legacy), STARTING UP status line,
        thin progress bar and the license note.
    """

    SPLASH_W = 720
    SPLASH_H = 540

    BG = COLORS["splash_panel"]
    PANEL = COLORS["splash_bg"]
    ACCENT = COLORS["accent"]
    TEXT = COLORS["text"]
    TEXT_DIM = COLORS["splash_text_dim"]

    STATUS_LINES = (
        "STARTING UP",
        "Initializing audio engine",
        "Loading playlist cache",
        "Detecting audio devices",
        "Preparing stream modules",
        "Almost ready",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.overrideredirect(True)  # borderless
        self.configure(fg_color=self.BG)
        self.attributes("-topmost", True)

        w, h = self.SPLASH_W, self.SPLASH_H
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # ── Full-bleed product photo + left gradient scrim ──
        # splash.jpg is 800x600 and the window is 720x540: the same 4:3
        # aspect, so the photo covers the window with zero distortion.
        # A panel-colored gradient fades the photo out under the text
        # column (DaVinci-style) instead of squeezing it into a strip.
        self._photo = None  # keep a reference alive or Tk drops the image
        image_path = Path(__file__).with_name("splash.jpg")
        if image_path.is_file():
            try:
                from PIL import Image, ImageTk

                img = Image.open(image_path).convert("RGBA")
                img = img.resize((w, h), Image.LANCZOS)
                # Light uniform tint so a busy photo never fights the text.
                # NOTE: BG-based — the transparent CTkFrame over this image
                # paints the toplevel's BG color, so the scrim must melt
                # into the SAME color or the frame edge shows as a line.
                base = Image.alpha_composite(
                    img, Image.new("RGBA", (w, h), (20, 24, 31, 35))
                )
                # Horizontal scrim: near-solid panel color only over the
                # narrow text column (left_w = 42%), easing into a light
                # veil by 82% so the photo is the hero from mid-window
                # rightward. The flat region extends PAST the text frame's
                # right edge so the frame's paint and the composite are
                # identical where the frame ends — otherwise the frame
                # edge shows as a vertical line where the photo suddenly
                # appears. A smoothstep curve (zero slope at both ends of
                # the ramp) keeps the transition itself invisible.
                a_start, a_end = 255, 30
                ramp0, ramp1 = 0.44, 0.82
                grad = Image.new("L", (w, 1))
                for x in range(w):
                    t = x / max(1, w - 1)
                    if t <= ramp0:
                        a = a_start
                    elif t >= ramp1:
                        a = a_end
                    else:
                        u = (t - ramp0) / (ramp1 - ramp0)
                        u = u * u * (3.0 - 2.0 * u)   # smoothstep
                        a = int(a_start + (a_end - a_start) * u)
                    grad.putpixel((x, 0), a)

                # Vertical edge melt: the photo fades into the panel color as
                # it meets the window's top/bottom/right edges, so no edge
                # reads as a cut line (full-bleed look). Combined with the
                # horizontal scrim via max() — whichever veil is stronger
                # wins at each pixel.
                def _smooth(u: float) -> float:
                    u = max(0.0, min(1.0, u))
                    return u * u * (3.0 - 2.0 * u)

                vgrad = Image.new("L", (1, h))
                top_fade, bottom_fade, right_fade = 24, 40, 20
                for y in range(h):
                    s = 0
                    if y < top_fade:
                        s = int(200 * (1.0 - _smooth(y / top_fade)))
                    elif y >= h - bottom_fade:
                        s = int(200 * (1.0 - _smooth((h - 1 - y) / bottom_fade)))
                    vgrad.putpixel((0, y), s)
                rgrad = Image.new("L", (w, 1))
                for x in range(w):
                    d = w - 1 - x
                    rgrad.putpixel((x, 0),
                                   int(140 * (1.0 - _smooth(d / right_fade)))
                                   if d < right_fade else 0)
                from PIL import ImageChops
                alpha = ImageChops.lighter(
                    grad.resize((w, h)),
                    ImageChops.lighter(vgrad.resize((w, h)), rgrad.resize((w, h))),
                )
                scrim = Image.new("RGBA", (w, h), (20, 24, 31, 0))
                scrim.putalpha(alpha)
                base = Image.alpha_composite(base, scrim)
                self._photo = ImageTk.PhotoImage(
                    base.convert("RGB"), master=self
                )
                tk.Label(
                    self, image=self._photo, bg=self.BG, borderwidth=0
                ).place(x=0, y=0)
            except Exception:
                self._photo = None

        # ── Left column (over the photo's scrimmed area) ──
        # Narrow on purpose: the photo is the hero, the text column only
        # frames the brand + status (keep the scrim ramp0 = 0.44 in sync).
        left_w = int(w * 0.42)
        left = ctk.CTkFrame(
            self,
            fg_color="transparent" if self._photo else self.PANEL,
            width=left_w, height=self.SPLASH_H, corner_radius=0,
        )
        left.place(x=0, y=0)
        left.pack_propagate(False)

        # Animated logo: equalizer bars on a canvas (cheap and smooth).
        # Canvas bg = BG so it melts into the transparent frame's paint
        # (a different shade here shows up as a box around the logo).
        self._logo = FreQLogoCanvas(left, width=120, height=64,
                                    bar_color=self.ACCENT, bg=self.BG)
        self._logo.pack(pady=(96, 18))

        # Brand column — Ultimate identity: letterspaced series kicker
        # above the wordmark, then tagline and release id on separate
        # lines (accent + bold for the Ultimate scheme; legacy versions
        # keep the plain dim look with their historical v-prefix).
        ver_raw = _splash_version().strip()
        ver_disp = format_display_version(ver_raw)
        is_ultimate = ver_disp != ver_raw

        if is_ultimate:
            ctk.CTkLabel(
                left, text="U L T I M A T E   S E R I E S",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=self.ACCENT,
            ).pack(pady=(0, 2))

        ctk.CTkLabel(
            left, text="FreQ",
            font=ctk.CTkFont(size=44, weight="bold"),
            text_color=self.TEXT,
        ).pack()

        ctk.CTkLabel(
            left, text="Radio Playlist Manager",
            font=ctk.CTkFont(size=12),
            text_color=self.TEXT_DIM,
        ).pack(pady=(2, 0))

        ctk.CTkLabel(
            left,
            text=ver_disp if is_ultimate else "v" + ver_raw,
            font=ctk.CTkFont(
                size=12, weight="bold" if is_ultimate else "normal"),
            text_color=self.ACCENT if is_ultimate else self.TEXT_DIM,
        ).pack(pady=(2, 0))

        # Bottom block: status + progress, aligned like the reference art.
        status_block = ctk.CTkFrame(left, fg_color="transparent")
        status_block.pack(side="bottom", fill="x", padx=36, pady=(0, 44))

        self._status_label = ctk.CTkLabel(
            status_block,
            text=self.STATUS_LINES[0].upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self.TEXT_DIM,
            anchor="w",
        )
        self._status_label.pack(fill="x", pady=(0, 8))

        self._progress = 0.0
        self._progress_bar = ctk.CTkProgressBar(
            status_block, width=left_w - 72, height=3,
            fg_color=COLORS["splash_track"],
            progress_color=self.ACCENT,
            corner_radius=1,
        )
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            status_block,
            text="FREE & OPEN-SOURCE SOFTWARE · GPL-3.0",
            font=ctk.CTkFont(size=10),
            text_color=self.TEXT_DIM,
            anchor="w",
        ).pack(fill="x")

        # Start animations
        self._animate_bars()
        self._tick()

    # ── Animation ──

    def _animate_bars(self) -> None:
        self._logo.step()
        self._bar_job = self.after(120, self._animate_bars)

    # The window is ALREADY usable when this splash appears: ``RadioApp()``
    # finishes constructing before the splash is shown, so the animation was
    # pure delay on the critical path — 0.014 per 30 ms tick meant ~2.2 s
    # every launch spent watching a progress bar reach a READY state the app
    # had reached long ago. 0.05 × 30 ms reaches READY in ~0.6 s, which keeps
    # the broadcast-style intro without holding the window hostage.
    SPLASH_STEP = 0.05

    def _tick(self) -> None:
        """Advance the startup progress and status line."""
        self._progress += self.SPLASH_STEP
        steps = len(self.STATUS_LINES)
        if self._progress >= 1.0:
            self._progress_bar.set(1.0)
            self._status_label.configure(text="READY")
            self.after(300, self._finish)
            return
        self._progress_bar.set(self._progress)
        idx = min(steps - 1, int(self._progress * steps))
        self._status_label.configure(text=self.STATUS_LINES[idx].upper())
        self._tick_job = self.after(30, self._tick)

    def _finish(self) -> None:
        """Fade out splash then call the completion callback."""
        self._fading = True
        self._fade_step(0.9)

    def _fade_step(self, alpha: float) -> None:
        if alpha <= 0.1:
            try:
                self.after_cancel(self._bar_job)
            except Exception:
                pass
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            cb = getattr(self, '_on_done', None)
            if cb:
                cb()
            return
        self.attributes("-alpha", alpha)
        self.after(30, lambda: self._fade_step(alpha - 0.15))

    def on_complete(self, callback) -> None:
        """Set callback for when splash finishes."""
        self._on_done = callback


class FreQLogoCanvas(tk.Canvas):
    """Animated FreQ logo mark: equalizer bars with a travelling wave."""

    def __init__(self, parent, width=120, height=64,
                 bar_color=COLORS["accent"], bg=COLORS["splash_bg"]):
        super().__init__(parent, width=width, height=height,
                         bg=bg, highlightthickness=0)
        self._h = height
        self._phase = 0.0
        n = 9
        gap = 4
        bar_w = 5
        total = n * bar_w + (n - 1) * gap
        x0 = (width - total) // 2
        mid = height // 2
        self._base = [0.35, 0.55, 0.8, 1.0, 0.7, 1.0, 0.8, 0.55, 0.35]
        self._bars = []
        for i in range(n):
            x = x0 + i * (bar_w + gap)
            rect = self.create_rectangle(
                x, mid - 4, x + bar_w, mid + 4,
                fill=bar_color, outline="", width=0,
            )
            self._bars.append((rect, x, x + bar_w, self._base[i]))

    def step(self) -> None:
        """Advance one animation frame: a travelling wave over the bars."""
        self._phase += 0.45
        mid = self._h // 2
        for i, (rect, x0, x1, base) in enumerate(self._bars):
            wave = 0.5 + 0.5 * math.sin(self._phase - i * 0.55)
            amp = base * (0.25 + 0.75 * wave)
            half = max(2, int(amp * (mid - 4)))
            self.coords(rect, x0, mid - half, x1, mid + half)
            # Blue that brightens toward white with the wave.
            r = int(0x58 + wave * (0xE8 - 0x58))
            g = int(0xA6 + wave * (0xF0 - 0xA6))
            self.itemconfigure(rect, fill=f"#{r:02x}{g:02x}ff")


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1) Create the main app — withdrawn so it's invisible
    app = RadioApp()
    app.withdraw()

    # 0) UI freeze watchdog: catches a hung UI thread in the logs AND
    #    feeds the out-of-process supervisor's heartbeat (only when
    #    FREQ_SUPERVISED is set — running gui.py directly stays undirected).
    _ui_watchdog = UiFreezeWatchdog(app)
    _ui_watchdog.start()
    app._ui_watchdog = _ui_watchdog

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
        ctk.CTkLabel(header, text=f"FreQ {format_display_version(update_info.version)} is now available",
                     font=ctk.CTkFont(size=12),
                     text_color=COLORS["text"]).pack(pady=(4, 0))
        
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
        
        ctk.CTkLabel(info_grid, text=f"Current: {version_label(CURRENT_VERSION)}",
                     font=ctk.CTkFont(size=11),
                     text_color=COLORS["text2"]).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ctk.CTkLabel(info_grid, text=f"Latest: {version_label(update_info.version)}",
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
                     fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
                     text_color="#ffffff", font=ctk.CTkFont(size=12, weight="bold"),
                     command=_open_download).pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        ctk.CTkButton(btn_frame, text="Maybe Later", height=36,
                     fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                     text_color=COLORS["text2"], font=ctk.CTkFont(size=12),
                     command=_skip).pack(side="left", expand=True, fill="x", padx=(5, 0))
    
    splash.on_complete(_on_splash_done)

    app.mainloop()
