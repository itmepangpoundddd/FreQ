#!/usr/bin/env python3
"""Audacity-style clip editor opened from a queue row.

The window is deliberately non-destructive towards the broadcast: it edits a
decoded copy in the native module and only ever WRITES a new WAV, so a song
that is on air keeps playing from a file nobody is rewriting. Preview plays
through the native monitor engine on the device chosen at the top of the
window — separate from the program output — so an operator can audition a cut
on headphones while the station keeps airing.

Editing model: everything is frames. The SVG-free waveform is painted from a
native envelope (one call per repaint, two floats per pixel), the selection is
dragged on the canvas, and every operation goes through EditSession so undo
covers each step.
"""

from __future__ import annotations

import json
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

from audio_editor import (
    FADE_CURVES,
    NATIVE_AVAILABLE,
    NATIVE_ERROR,
    EditSession,
    format_time,
    list_monitor_devices,
)
from theme import COLORS
from tooltips import ToolTip

WAVE_BG = "#0d1219"
WAVE_COLOR = COLORS["accent"]
WAVE_SEL = COLORS["accent_hover"]
GRID = "#1c2431"
PLAYHEAD = "#f0883e"


def prefs_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "FreQ" / "editor_prefs.json"


def load_prefs() -> dict:
    try:
        data = json.loads(prefs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(data: dict) -> None:
    try:
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def safe_stem(name: str, fallback: str = "edited audio") -> str:
    """A Windows-safe file name stem for the artwork-ish song titles."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:90] or fallback).rstrip(" .")


def unique_path(path: Path) -> Path:
    """`song (edited).wav`, then `song (edited 2).wav`, … — never overwrite."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} {n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class EditorWindow(ctk.CTkToplevel):
    """Waveform editor for one local file."""

    MIN_VIEW = 256            # frames; deepest zoom

    def __init__(self, master, path: str, song=None,
                 on_save: Optional[Callable] = None,
                 display_name: str = "",
                 save_dir: Optional[str] = None) -> None:
        super().__init__(master)
        self.withdraw()
        self.song = song
        self._on_save = on_save
        self._source_path = path
        self._title_name = display_name or Path(path).name
        # Where `Save (copy)` lands. The caller decides: next to a local file,
        # or in an `edits` folder for a cached download — never inside the
        # cache, which "Clear Cache" would empty.
        self._save_dir = Path(save_dir) if save_dir else Path(path).parent

        if not NATIVE_AVAILABLE:
            messagebox.showerror(
                "Audio editor",
                "The native editor module is not available:\n\n"
                f"{NATIVE_ERROR}\n\nRun build_native_meter.py to build it.",
                parent=master)
            self.destroy()
            return

        try:
            self.session = EditSession(path)
        except Exception as err:
            messagebox.showerror("Audio editor", f"Cannot open this file:\n\n{err}",
                                 parent=master)
            self.destroy()
            return

        self.title(f"Edit — {self._title_name}")
        self.geometry("1120x680")
        self.minsize(760, 500)
        self.configure(fg_color=COLORS["bg"])
        self.transient(master)

        total = self.session.frames
        self.view_start = 0
        self.view_frames = max(1, total)
        self.sel_start: Optional[int] = None
        self.sel_end: Optional[int] = None
        self.playhead = 0
        self._drag_anchor: Optional[int] = None
        self._drag_moved = False
        self._play_origin = 0
        self._tick_job = None
        self._wave_items: list = []
        self._prefs = load_prefs()
        # Level analysis is cached: scanning a 4-minute track takes ~30 ms, and
        # doing that on every playhead tick held the audio buffer locked half
        # the time, which the monitor heard as stutter.
        self._stats_key = None
        self._stats_value = {"peak_db": -120.0, "rms_db": -120.0}
        self._edit_seq = 0

        self._devices = list_monitor_devices()
        self._device_index = -1
        self._device_name = str(self._prefs.get("monitor_device") or "")

        self._build()
        self._select_device_from_prefs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda e: self._on_close())
        self.deiconify()
        self.after(60, self._redraw_all)
        self.after(80, self.focus_force)

    # ──────────────────────────────────────────────────────────────
    # Layout
    # ──────────────────────────────────────────────────────────────
    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        header.pack(fill="x", padx=10, pady=(10, 6))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkLabel(left, text=self._title_name, anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(fill="x")
        ctk.CTkLabel(left, text=Path(self._source_path).name, anchor="w",
                     font=ctk.CTkFont(size=10),
                     text_color=COLORS["text3"]).pack(fill="x")

        monitor = ctk.CTkFrame(header, fg_color="transparent")
        monitor.pack(side="right", padx=10, pady=8)
        ctk.CTkLabel(monitor, text="Monitor", font=ctk.CTkFont(size=10),
                     text_color=COLORS["text2"]).pack(side="left", padx=(0, 6))
        self.device_box = ctk.CTkComboBox(
            monitor, values=[name for name, _i in self._devices],
            width=250, height=28, corner_radius=6,
            fg_color=COLORS["bg_input"], border_color=COLORS["border"],
            button_color=COLORS["border"], dropdown_fg_color=COLORS["bg_card"],
            command=self._on_device_change)
        self.device_box.pack(side="left")
        ToolTip(self.device_box, text="Monitor device",
                description="Preview plays on this device — the program "
                            "output keeps its own device")
        ctk.CTkButton(monitor, text="↻", width=30, height=28, corner_radius=6,
                      fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
                      command=self._refresh_devices).pack(side="left", padx=(4, 8))
        ctk.CTkLabel(monitor, text="Vol", font=ctk.CTkFont(size=10),
                     text_color=COLORS["text2"]).pack(side="left")
        self.vol_var = ctk.DoubleVar(value=float(self._prefs.get("monitor_volume", 80)))
        ctk.CTkSlider(monitor, from_=0, to=100, width=110, variable=self.vol_var,
                      command=self._on_volume).pack(side="left", padx=(6, 0))

        # Transport
        transport = ctk.CTkFrame(self, fg_color="transparent")
        transport.pack(fill="x", padx=12, pady=(0, 4))
        self.btn_play = ctk.CTkButton(
            transport, text="▶  Play", width=104, height=30, corner_radius=6,
            fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
            command=self._toggle_play)
        self.btn_play.pack(side="left")
        ToolTip(self.btn_play, text="Play / Pause (Space)",
                description="Plays the selection, or from the cursor to the end")
        self.btn_stop = ctk.CTkButton(
            transport, text="■  Stop", width=86, height=30, corner_radius=6,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            command=self._stop)
        self.btn_stop.pack(side="left", padx=(6, 12))
        self.time_label = ctk.CTkLabel(transport, text="00:00.000 / 00:00.000",
                                       font=ctk.CTkFont(size=12, family="Consolas"),
                                       text_color=COLORS["text"])
        self.time_label.pack(side="left")
        self.sel_label = ctk.CTkLabel(transport, text="", font=ctk.CTkFont(size=10),
                                      text_color=COLORS["accent"])
        self.sel_label.pack(side="left", padx=(14, 0))

        # Toolbars
        tools = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        tools.pack(fill="x", padx=10, pady=(4, 4))

        row1 = ctk.CTkFrame(tools, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(8, 2))
        self.btn_undo = self._tool(row1, "↶  Undo", self._undo, "Undo (Ctrl+Z)")
        self.btn_redo = self._tool(row1, "↷  Redo", self._redo, "Redo (Ctrl+Y)")
        self._gap(row1)
        self._tool(row1, "✂  Cut", self._cut, "Cut the selection (Ctrl+X)")
        self._tool(row1, "⧉  Copy", self._copy, "Copy the selection (Ctrl+C)")
        self._tool(row1, "📋  Paste", self._paste, "Paste at the cursor (Ctrl+V)")
        self._tool(row1, "✂  Trim", self._trim,
                   "Keep only the selection, drop the rest")
        self._tool(row1, "🗑  Delete", self._delete, "Delete the selection (Del)")
        self._tool(row1, "🔇  Mute", self._mute, "Replace the selection with silence")

        row2 = ctk.CTkFrame(tools, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=(2, 8))
        self._tool(row2, "Fade In", self._fade_in, "Fade in across the selection")
        self._tool(row2, "Fade Out", self._fade_out, "Fade out across the selection")
        self.curve_box = ctk.CTkComboBox(
            row2, values=[name for name, _i in FADE_CURVES], width=120, height=28,
            corner_radius=6, fg_color=COLORS["bg_input"],
            border_color=COLORS["border"], button_color=COLORS["border"],
            dropdown_fg_color=COLORS["bg_card"])
        self.curve_box.set(str(self._prefs.get("fade_curve") or FADE_CURVES[0][0]))
        self.curve_box.pack(side="left", padx=(2, 8))
        self._tool(row2, "Normalize", self._normalize, "Normalize the selection peak")
        self._tool(row2, "Gain…", self._ask_gain, "Amplify the selection by dB")
        self._tool(row2, "Reverse", self._reverse, "Reverse the selection")
        self._tool(row2, "Insert silence…", self._ask_insert,
                   "Insert silence at the cursor")
        self._gap(row2)
        self._tool(row2, "Zoom −", lambda: self._zoom(0.5), "Zoom out (Ctrl+wheel)")
        self._tool(row2, "Zoom +", lambda: self._zoom(2.0), "Zoom in (Ctrl+wheel)")
        self._tool(row2, "Fit", self._fit, "Show the whole file")

        # Waveform
        wave = ctk.CTkFrame(self, fg_color=COLORS["bg_card"], corner_radius=10)
        wave.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.canvas = tk.Canvas(wave, bg=WAVE_BG, highlightthickness=0, bd=0,
                                height=300)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.scroll = tk.Scrollbar(wave, orient="horizontal",
                                   command=self._on_scroll)
        self.scroll.pack(fill="x", padx=6, pady=(0, 6))
        for seq, handler in (
            ("<Button-1>", self._on_press),
            ("<B1-Motion>", self._on_drag),
            ("<ButtonRelease-1>", self._on_release),
            ("<Double-Button-1>", lambda e: self._select_all()),
            ("<Button-3>", self._on_context),
            ("<MouseWheel>", self._on_wheel),
            ("<Shift-MouseWheel>", self._on_wheel),
            ("<Control-MouseWheel>", self._on_ctrl_wheel),
            ("<Configure>", lambda e: self._redraw_all()),
        ):
            self.canvas.bind(seq, handler)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=12, pady=(0, 10))
        self.status = ctk.CTkLabel(footer, text="", anchor="w",
                                   font=ctk.CTkFont(size=10),
                                   text_color=COLORS["text2"])
        self.status.pack(side="left", fill="x", expand=True)
        self.btn_save_as = ctk.CTkButton(
            footer, text="Save As…", width=100, height=32, corner_radius=6,
            fg_color=COLORS["bg_hover"], hover_color=COLORS["border"],
            command=self._save_as)
        self.btn_save_as.pack(side="right", padx=(6, 0))
        ToolTip(self.btn_save_as, text="Save As",
                description="Write a WAV anywhere; the queue item follows it")
        self.btn_save = ctk.CTkButton(
            footer, text="Save (copy)", width=120, height=32, corner_radius=6,
            fg_color=COLORS["green"], hover_color=COLORS["green_hover"],
            command=self._save_copy)
        self.btn_save.pack(side="right")
        ToolTip(self.btn_save, text="Save a copy",
                description="Writes '<title> (edited).wav' and points the queue "
                            "item at it; the original file is never modified")

        self._bind_keys()
        self._update_buttons()

    def _tool(self, parent, text: str, command, tip: str = ""):
        btn = ctk.CTkButton(parent, text=text, width=92, height=28,
                            corner_radius=6, fg_color=COLORS["bg_hover"],
                            hover_color=COLORS["border"],
                            text_color=COLORS["text"], font=ctk.CTkFont(size=11),
                            command=command)
        btn.pack(side="left", padx=2)
        if tip:
            ToolTip(btn, text=text.split("  ")[-1].strip("… "), description=tip)
        return btn

    @staticmethod
    def _gap(parent) -> None:
        ctk.CTkFrame(parent, width=10, height=1,
                     fg_color="transparent").pack(side="left")

    def _bind_keys(self) -> None:
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        self.bind("<Control-Z>", lambda e: self._redo())
        self.bind("<Control-x>", lambda e: self._cut())
        self.bind("<Control-c>", lambda e: self._copy())
        self.bind("<Control-v>", lambda e: self._paste())
        self.bind("<Control-a>", lambda e: self._select_all())
        self.bind("<Control-s>", lambda e: self._save_copy())
        self.bind("<Delete>", lambda e: self._delete())
        self.bind("<space>", lambda e: self._toggle_play())
        self.bind("<Home>", lambda e: self._set_playhead(0))
        self.bind("<End>", lambda e: self._set_playhead(self.session.frames))

    # ──────────────────────────────────────────────────────────────
    # Geometry helpers
    # ──────────────────────────────────────────────────────────────
    def _canvas_width(self) -> int:
        return max(1, self.canvas.winfo_width() or 1)

    def _x_to_frame(self, x: int) -> int:
        w = self._canvas_width()
        total = self.session.frames
        frame = self.view_start + int((x / w) * self.view_frames)
        return max(0, min(frame, total))

    def _frame_to_x(self, frame: int) -> float:
        w = self._canvas_width()
        return (frame - self.view_start) / max(1, self.view_frames) * w

    def _selection(self):
        if self.sel_start is None or self.sel_end is None:
            return None
        s, e = sorted((self.sel_start, self.sel_end))
        if e <= s:
            return None
        return s, e

    def _selection_or_all(self):
        sel = self._selection()
        if sel:
            return sel
        return 0, self.session.frames

    # ──────────────────────────────────────────────────────────────
    # Painting
    # ──────────────────────────────────────────────────────────────
    def _redraw_all(self) -> None:
        if not self.winfo_exists() or not hasattr(self, "canvas"):
            return
        w = self._canvas_width()
        h = max(40, self.canvas.winfo_height() or 300)
        self.canvas.delete("all")
        self._wave_items = []
        total = self.session.frames
        self._known_frames = total
        self._clamp_view()

        # Ruler
        self._draw_ruler(w)
        mid = h / 2.0
        amp = max(8.0, mid - 22)
        # ONE native call per repaint: two numbers per pixel column. The
        # envelope is min/max per bucket, so a dense track still reads as a
        # solid shape instead of a hairline.
        peaks = self.session.peaks(self.view_start,
                                   self.view_start + self.view_frames, w)
        for x in range(w):
            lo = peaks[2 * x]
            hi = peaks[2 * x + 1]
            if lo == 0.0 and hi == 0.0:
                continue
            self.canvas.create_line(x, mid - hi * amp, x, mid - lo * amp,
                                    fill=WAVE_COLOR, width=1, tags="wave")
        self.canvas.create_line(0, mid, w, mid, fill=GRID)

        self._draw_selection(w, h)
        self._draw_playhead(h)
        self._update_labels()
        self._refresh_scroll()

    def _draw_ruler(self, width: int) -> None:
        rate = self.session.sample_rate or 44100
        span = self.view_frames / rate
        steps = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
        step = next((s for s in steps if span / s <= 12), steps[-1])
        start_t = self.view_start / rate
        first = int(start_t / step) * step
        t = first
        while t <= start_t + span + step:
            x = (t * rate - self.view_start) / max(1, self.view_frames) * width
            if 0 <= x <= width:
                self.canvas.create_line(x, 0, x, 6, fill=GRID)
                self.canvas.create_text(x + 3, 8, anchor="nw",
                                        text=format_time(t, False),
                                        fill=COLORS["text3"],
                                        font=("Consolas", 8))
            t += step

    def _draw_selection(self, width: int, height: int) -> None:
        sel = self._selection()
        if not sel:
            return
        x0 = max(0, self._frame_to_x(sel[0]))
        x1 = min(width, self._frame_to_x(sel[1]))
        if x1 <= x0:
            return
        self.canvas.create_rectangle(x0, 18, x1, height, fill=WAVE_SEL,
                                     stipple="gray25", outline="")
        self.canvas.create_line(x0, 18, x0, height, fill=WAVE_SEL, width=2)
        self.canvas.create_line(x1, 18, x1, height, fill=WAVE_SEL, width=2)

    def _draw_playhead(self, height: int) -> None:
        x = self._frame_to_x(self.playhead)
        if 0 <= x <= self._canvas_width():
            self.canvas.create_line(x, 0, x, height, fill=PLAYHEAD,
                                    width=2, tags="playhead")

    def _update_time_label(self) -> None:
        """The cheap readout the playhead tick needs (no buffer scan)."""
        rate = self.session.sample_rate or 44100
        self.time_label.configure(
            text=f"{format_time(self.playhead / rate)} / "
                 f"{format_time(self.session.duration)}")

    def _levels(self, sel, frames: int) -> dict:
        """Peak/RMS, recomputed only when the range or the edit changes."""
        key = (sel, frames, self._edit_seq)
        if key != self._stats_key:
            self._stats_key = key
            self._stats_value = self.session.stats(*(sel or (0, frames)))
        return self._stats_value

    def _update_labels(self) -> None:
        rate = self.session.sample_rate or 44100
        self._update_time_label()
        sel = self._selection()
        if sel:
            self.sel_label.configure(
                text=f"selection {format_time((sel[1] - sel[0]) / rate)} "
                     f"· {format_time(sel[0] / rate)} → {format_time(sel[1] / rate)}")
        else:
            self.sel_label.configure(text="no selection")
        stats = self._levels(sel, self.session.frames)
        kind = {1: "mono", 2: "stereo"}.get(self.session.channels,
                                            f"{self.session.channels} ch")
        self.status.configure(
            text=f"{format_time(self.session.duration)} · "
                 f"{rate / 1000:.1f} kHz · {kind} · "
                 f"peak {stats['peak_db']:+.1f} dBFS · "
                 f"RMS {stats['rms_db']:+.1f} dBFS"
                 + ("  ·  edited" if self.session.dirty else ""))
        self._update_buttons()

    def _update_buttons(self) -> None:
        for btn, enabled in ((getattr(self, "btn_undo", None), self.session.can_undo),
                             (getattr(self, "btn_redo", None), self.session.can_redo)):
            if btn is None:
                continue
            try:
                btn.configure(state="normal" if enabled else "disabled",
                              fg_color=COLORS["bg_hover"] if enabled
                              else COLORS["bg_input"])
            except Exception:
                pass

    def _refresh_scroll(self) -> None:
        total = max(1, self.session.frames)
        span = min(self.view_frames, total)
        try:
            self.scroll.set(self.view_start / total,
                            min(1.0, (self.view_start + span) / total))
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # View / selection / transport
    # ──────────────────────────────────────────────────────────────
    def _clamp_view(self) -> None:
        total = max(1, self.session.frames)
        self.view_frames = max(self.MIN_VIEW, min(self.view_frames, total))
        self.view_start = max(0, min(self.view_start, total - self.view_frames))
        self.playhead = max(0, min(self.playhead, self.session.frames))

    def _on_scroll(self, *args) -> None:
        # tk.Scrollbar passes ("moveto", frac) or ("scroll", n, "units").
        total = max(1, self.session.frames)
        if args and args[0] == "moveto":
            frac = float(args[1])
            self.view_start = int(frac * total)
        elif args and args[0] == "scroll":
            amount = int(args[1])
            unit = "pages" if len(args) > 2 and args[2] == "pages" else "units"
            step = self.view_frames // (4 if unit == "units" else 1)
            self.view_start += amount * step
        self._clamp_view()
        self._redraw_all()

    def _on_wheel(self, event) -> None:
        if event.state & 0x0001:            # Shift → horizontal scroll
            self.view_start -= int(event.delta / 120) * (self.view_frames // 8)
        else:
            self.view_start -= int(event.delta / 120) * (self.view_frames // 12)
        self._clamp_view()
        self._redraw_all()

    def _on_ctrl_wheel(self, event) -> None:
        anchor = self._x_to_frame(event.x)
        factor = 1.25 if event.delta > 0 else 0.8
        total = max(1, self.session.frames)
        new_span = int(self.view_frames / factor) if factor > 1 else \
            int(self.view_frames * (1 / factor))
        new_span = max(self.MIN_VIEW, min(new_span, total))
        ratio = (anchor - self.view_start) / max(1, self.view_frames)
        self.view_frames = new_span
        self.view_start = int(anchor - ratio * new_span)
        self._clamp_view()
        self._redraw_all()

    def _zoom(self, factor: float) -> None:
        total = max(1, self.session.frames)
        centre = self.playhead
        new_span = int(self.view_frames / factor)
        new_span = max(self.MIN_VIEW, min(new_span, total))
        self.view_frames = new_span
        self.view_start = int(centre - new_span / 2)
        self._clamp_view()
        self._redraw_all()

    def _fit(self) -> None:
        self.view_start = 0
        self.view_frames = max(1, self.session.frames)
        self._redraw_all()

    def _on_press(self, event) -> None:
        self._stop()
        frame = self._x_to_frame(event.x)
        self._drag_anchor = frame
        self._drag_moved = False
        if event.state & 0x0001:            # Shift extends the current selection
            start = self.sel_start if self.sel_start is not None else frame
            self.sel_start, self.sel_end = start, frame
            self._drag_moved = True
        else:
            self.sel_start = self.sel_end = frame
        self._redraw_all()

    def _on_drag(self, event) -> None:
        if self._drag_anchor is None:
            return
        frame = self._x_to_frame(event.x)
        if abs(frame - self._drag_anchor) > 2:
            self._drag_moved = True
        self.sel_start, self.sel_end = self._drag_anchor, frame
        self._redraw_all()

    def _on_release(self, event) -> None:
        if self._drag_anchor is not None and not self._drag_moved:
            # A plain click is a cursor move, like Audacity.
            self.sel_start = self.sel_end = None
            self.playhead = self._drag_anchor
        self._drag_anchor = None
        self._redraw_all()

    def _select_all(self) -> None:
        self.sel_start, self.sel_end = 0, self.session.frames
        self._redraw_all()
        return "break"

    def _set_playhead(self, frame: int) -> None:
        self.playhead = max(0, min(int(frame), self.session.frames))
        self._visible_around(self.playhead)
        self._redraw_all()

    def _visible_around(self, frame: int) -> None:
        if not (self.view_start <= frame < self.view_start + self.view_frames):
            self.view_start = int(frame - self.view_frames / 2)
            self._clamp_view()

    def _on_context(self, event) -> None:
        frame = self._x_to_frame(event.x)
        if self._selection() is None:
            self.playhead = frame
            self._redraw_all()
        menu = tk.Menu(self, tearoff=0, bg=COLORS["bg_card"],
                       fg=COLORS["text"], activebackground=COLORS["accent"],
                       activeforeground="#fff")
        menu.add_command(label="Cut", command=self._cut)
        menu.add_command(label="Copy", command=self._copy)
        menu.add_command(label="Paste here", command=self._paste)
        menu.add_separator()
        menu.add_command(label="Trim to selection", command=self._trim)
        menu.add_command(label="Delete", command=self._delete)
        menu.add_command(label="Mute", command=self._mute)
        menu.add_separator()
        menu.add_command(label="Fade in", command=self._fade_in)
        menu.add_command(label="Fade out", command=self._fade_out)
        menu.add_command(label="Normalize", command=self._normalize)
        menu.add_command(label="Reverse", command=self._reverse)
        menu.add_separator()
        menu.add_command(label="Select all", command=self._select_all)
        menu.add_command(label="Insert silence…", command=self._ask_insert)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ──────────────────────────────────────────────────────────────
    # Transport
    # ──────────────────────────────────────────────────────────────
    def _toggle_play(self) -> None:
        if self.session.playing:
            self._stop()
            return
        sel = self._selection()
        start, end = sel if sel else (self.playhead, self.session.frames)
        if end - start < 2:
            start, end = 0, self.session.frames
        self._play_origin = start
        self.session.set_volume(self.vol_var.get() / 100.0)
        try:
            ok = self.session.preview(start, end, self._device_index,
                                      self.vol_var.get() / 100.0)
        except Exception as err:
            messagebox.showerror("Monitor", f"Cannot play on this device:\n\n{err}",
                                 parent=self)
            return
        if not ok:
            messagebox.showwarning("Monitor", "Nothing to play in that range.",
                                   parent=self)
            return
        self.btn_play.configure(text="⏸  Pause")
        self._tick()

    def _stop(self) -> None:
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        try:
            self.session.stop()
        except Exception:
            pass
        if hasattr(self, "btn_play"):
            self.btn_play.configure(text="▶  Play")

    def _tick(self) -> None:
        if not self.session.playing:
            self.playhead = self._play_origin + self.session.position
            self._stop()
            self._redraw_all()
            return
        previous = self.playhead
        self.playhead = self._play_origin + self.session.position
        if not (self.view_start <= self.playhead
                < self.view_start + self.view_frames):
            # Follow the cursor off-screen, but repaint only when it moves a
            # whole page — otherwise every tick would redraw the waveform.
            self._visible_around(self.playhead)
            self._redraw_all()
        else:
            self._paint_playhead()
        self._tick_job = self.after(60, self._tick)

    def _paint_playhead(self) -> None:
        h = max(40, self.canvas.winfo_height() or 300)
        self.canvas.delete("playhead")
        self._draw_playhead(h)
        # Only the clock moves during playback: no stats scan, no waveform
        # repaint — the audio callback must never wait for the UI.
        self._update_time_label()

    # ──────────────────────────────────────────────────────────────
    # Edits
    # ──────────────────────────────────────────────────────────────
    def _after_edit(self) -> None:
        """Repaint after an operation, keeping the selection when it survives.

        A fade or a gain leaves the length alone, so the same range stays
        selected for the next step (fade, then normalize, then fade out); a
        delete or a trim moves frames around, so the selection is dropped
        rather than silently pointing at different audio.
        """
        self._stop()
        total = self.session.frames
        self._edit_seq += 1          # invalidates the cached peak/RMS
        same_length = total == getattr(self, "_known_frames", total)
        if same_length:
            if self.sel_start is not None:
                self.sel_start = min(self.sel_start, total)
            if self.sel_end is not None:
                self.sel_end = min(self.sel_end, total)
        else:
            self.sel_start = self.sel_end = None
        self.view_frames = min(max(self.view_frames, self.MIN_VIEW), max(1, total))
        if self.playhead > total:
            self.playhead = total
        self._clamp_view()
        self._redraw_all()

    def _undo(self) -> None:
        if self.session.undo():
            self._after_edit()

    def _redo(self) -> None:
        if self.session.redo():
            self._after_edit()

    def _copy(self) -> None:
        sel = self._selection()
        if not sel:
            self.status.configure(text="Select a range first — nothing copied.")
            return
        frames = self.session.copy(*sel)
        self.status.configure(text=f"Copied {format_time(frames / self.session.sample_rate)}.")

    def _cut(self) -> None:
        sel = self._selection()
        if not sel:
            self.status.configure(text="Select a range first — nothing cut.")
            return
        self.session.cut(*sel)
        self._after_edit()

    def _paste(self) -> None:
        if not self.session.has_clipboard:
            self.status.configure(text="Nothing on the clipboard.")
            return
        self.session.paste(self.playhead)
        self._after_edit()

    def _trim(self) -> None:
        sel = self._selection()
        if not sel:
            self.status.configure(text="Select the part to keep, then Trim.")
            return
        self.session.trim_to(*sel)
        self.playhead = 0
        self._after_edit()

    def _delete(self) -> None:
        sel = self._selection()
        if not sel:
            self.status.configure(text="Select a range first — nothing deleted.")
            return
        self.session.delete(*sel)
        self._after_edit()

    def _mute(self) -> None:
        start, end = self._selection_or_all()
        self.session.silence(start, end)
        self._after_edit()

    def _curve(self) -> int:
        name = self.curve_box.get()
        for label, idx in FADE_CURVES:
            if label == name:
                return idx
        return 0

    def _fade_in(self) -> None:
        start, end = self._selection_or_all()
        self.session.fade(start, end, self._curve(), True)
        self._after_edit()

    def _fade_out(self) -> None:
        start, end = self._selection_or_all()
        self.session.fade(start, end, self._curve(), False)
        self._after_edit()

    def _normalize(self) -> None:
        start, end = self._selection_or_all()
        applied = self.session.normalize(start, end, -1.0)
        self._after_edit()
        self.status.configure(
            text=f"Normalized to -1.0 dBFS (applied {applied:+.1f} dB).")

    def _reverse(self) -> None:
        start, end = self._selection_or_all()
        self.session.reverse(start, end)
        self._after_edit()

    def _ask_gain(self) -> None:
        dialog = ctk.CTkInputDialog(text="Amplify the selection by (dB):",
                                    title="Gain")
        raw = dialog.get_input()
        if raw is None:
            return
        try:
            db = float(str(raw).strip())
        except ValueError:
            messagebox.showinfo("Gain", "Enter a number, e.g. -3 or 2.5.",
                                parent=self)
            return
        start, end = self._selection_or_all()
        self.session.gain(start, end, db)
        self._after_edit()

    def _ask_insert(self) -> None:
        dialog = ctk.CTkInputDialog(text="Insert silence at the cursor (seconds):",
                                    title="Insert silence")
        raw = dialog.get_input()
        if raw is None:
            return
        try:
            seconds = float(str(raw).strip())
        except ValueError:
            messagebox.showinfo("Insert silence", "Enter seconds, e.g. 0.5.",
                                parent=self)
            return
        frames = int(seconds * self.session.sample_rate)
        if frames <= 0:
            return
        self.session.insert_silence(self.playhead, frames)
        self._after_edit()

    # ──────────────────────────────────────────────────────────────
    # Monitor device
    # ──────────────────────────────────────────────────────────────
    def _select_device_from_prefs(self) -> None:
        wanted = self._device_name
        names = [name for name, _i in self._devices]
        index = -1
        for i, name in enumerate(names):
            if wanted and name == wanted:
                index = i
                break
        if index < 0:
            index = 0
        self.device_box.set(names[index] if names else "System default")
        self._device_index = self._devices[index][1] if self._devices else -1
        self._device_name = names[index] if names else ""

    def _on_device_change(self, choice: str) -> None:
        for name, index in self._devices:
            if name == choice:
                self._device_index = index
                self._device_name = name
                break
        self._prefs["monitor_device"] = self._device_name
        save_prefs(self._prefs)
        was_playing = self.session.playing
        if was_playing:
            self._stop()
            self._toggle_play()

    def _refresh_devices(self) -> None:
        self._devices = list_monitor_devices()
        names = [name for name, _i in self._devices]
        self.device_box.configure(values=names)
        self._select_device_from_prefs()
        self.status.configure(text=f"{len(names) - 1} output device(s) found.")

    def _on_volume(self, _value=None) -> None:
        self.session.set_volume(self.vol_var.get() / 100.0)
        self._prefs["monitor_volume"] = float(self.vol_var.get())
        save_prefs(self._prefs)

    # ──────────────────────────────────────────────────────────────
    # Save / close
    # ──────────────────────────────────────────────────────────────
    def _save_copy(self) -> None:
        base = safe_stem(self._title_name, Path(self._source_path).stem)
        target = unique_path(self._save_dir / f"{base} (edited).wav")
        self._write(target)

    def _save_as(self) -> None:
        source = Path(self._source_path)
        base = safe_stem(self._title_name, source.stem)
        chosen = filedialog.asksaveasfilename(
            parent=self, title="Save edited audio",
            initialdir=str(self._save_dir if self._save_dir.is_dir()
                           else source.parent),
            initialfile=f"{base} (edited).wav",
            defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
        if not chosen:
            return
        self._write(Path(chosen))

    def _write(self, target: Path) -> None:
        target = Path(os.path.abspath(str(target)))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self.session.save(str(target))
        except Exception as err:
            messagebox.showerror("Save", f"Cannot write this file:\n\n{err}",
                                 parent=self)
            return
        if not target.is_file():
            # Never hand the queue a path that is not there: that is exactly
            # how a row ends up saying "original missing".
            messagebox.showerror(
                "Save", f"The file was not written:\n\n{target}", parent=self)
            return
        self._prefs["fade_curve"] = self.curve_box.get()
        save_prefs(self._prefs)
        if self._on_save is not None:
            try:
                self._on_save(self.song, str(target), self.session.duration)
            except Exception:
                pass
        self.status.configure(text=f"Saved → {target.name} (original untouched)")
        self._redraw_all()

    def _on_close(self) -> None:
        if self.session.dirty:
            answer = messagebox.askyesnocancel(
                "Close editor",
                "Save the edit before closing?\n\nThe original file is never "
                "modified — saving writes a new WAV.",
                parent=self)
            if answer is None:
                return
            if answer:
                self._save_copy()
        self._stop()
        try:
            self.session.close()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
