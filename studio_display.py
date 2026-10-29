#!/usr/bin/env python3
"""FreQ Studio Display — the control-room screen.

A second window built for a screen the whole studio can read from across the
room: the song on air, what comes next, a stereo VU meter, an ON AIR lamp and
the wall clock. It is **read-only**: it polls a snapshot the main window
already computed and never touches the player, the queue or the rundown, so
opening (or closing) it mid-broadcast cannot disturb what is airing.

`F11` toggles fullscreen (the point of a second monitor), `Esc` leaves
fullscreen, `Ctrl+W` closes.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Callable, Optional

import customtkinter as ctk

from theme import COLORS

# Big type: this window is read from across a control room, not at 60 cm.
FONT_FAMILY_FALLBACK = "Segoe UI"
CLOCK_FMT = "%H:%M:%S"
DB_FLOOR = -60.0          # bottom of the meter scale
DB_TOP = 0.0


def _db(level: Optional[float]) -> float:
    """Linear peak → dBFS, floored at the meter's bottom."""
    try:
        value = float(level)
    except (TypeError, ValueError):
        return DB_FLOOR
    if value <= 0.0:
        return DB_FLOOR
    return max(DB_FLOOR, min(DB_TOP, 20.0 * math.log10(value)))


def _meter_color(db: float) -> str:
    if db >= -3.0:
        return COLORS["red"]
    if db >= -12.0:
        return COLORS["orange"]
    return COLORS["green"]


def _fmt(seconds: float) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "0:00"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes}:{secs:02d}")


class StudioDisplay(ctk.CTkToplevel):
    """The studio's own readout: now playing, next up, VU, ON AIR, clock."""

    TICK_MS = 120             # fast enough for a meter, cheap enough to idle

    def __init__(self, master, state_provider: Callable[[], dict]):
        super().__init__(master)
        self._state_provider = state_provider
        self._after_id = None
        self._fullscreen = False
        self._geometry = "1180x680"
        self._last = {}
        self._peak_l = DB_FLOOR
        self._peak_r = DB_FLOOR
        self._peak_at = 0.0
        self._on_air = False
        self._blink = 0

        self.title("FreQ Studio Display")
        self.geometry(self._geometry)
        self.minsize(760, 420)
        self.configure(fg_color="#05070b")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<F11>", lambda _e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda _e: self._leave_fullscreen())
        self.bind("<Control-w>", lambda _e: self.close())
        self.bind("<Control-W>", lambda _e: self.close())

        self._build()
        self.after(80, self._tick)

    # ── Layout ────────────────────────────────────────────────────
    def _build(self) -> None:
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=26, pady=18)

        # Top bar: station mark, clock, ON AIR lamp.
        top = ctk.CTkFrame(root, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="FreQ", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=COLORS["accent"]).pack(side="left")
        self.station_label = ctk.CTkLabel(
            top, text="STUDIO DISPLAY", font=ctk.CTkFont(size=12),
            text_color=COLORS["text3"])
        self.station_label.pack(side="left", padx=(10, 0), pady=(8, 0))

        self.on_air_frame = ctk.CTkFrame(top, fg_color="#3a1013", corner_radius=10,
                                         border_width=2, border_color="#5a1a1f")
        self.on_air_frame.pack(side="right")
        self.on_air_dot = ctk.CTkLabel(self.on_air_frame, text="●",
                                       font=ctk.CTkFont(size=18),
                                       text_color="#7a2a30")
        self.on_air_dot.pack(side="left", padx=(14, 6), pady=6)
        self.on_air_label = ctk.CTkLabel(
            self.on_air_frame, text="OFF AIR",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text3"])
        self.on_air_label.pack(side="left", padx=(0, 16), pady=6)

        self.clock_label = ctk.CTkLabel(
            top, text="00:00:00", font=ctk.CTkFont(size=44, weight="bold"),
            text_color=COLORS["text"])
        self.clock_label.pack(side="right", padx=(0, 22))
        self.date_label = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12), text_color=COLORS["text3"])
        self.date_label.pack(side="right", padx=(0, 8), pady=(14, 0))

        # Now playing card.
        card = ctk.CTkFrame(root, fg_color=COLORS["bg_card"], corner_radius=16)
        card.pack(fill="both", expand=True, pady=(20, 12))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=26, pady=20)

        # Equal spacers above and below centre the block on a tall screen
        # (height=1: a plain CTkFrame would ask for 200 px and squeeze the
        # next-up/meter row off the window).
        ctk.CTkFrame(inner, fg_color="transparent", height=1).pack(fill="both", expand=True)
        ctk.CTkLabel(inner, text="NOW PLAYING", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=COLORS["text3"]).pack(anchor="w")
        self.title_label = ctk.CTkLabel(
            inner, text="—", font=ctk.CTkFont(size=52, weight="bold"),
            text_color=COLORS["text"], anchor="w", justify="left")
        self.title_label.pack(fill="x", pady=(4, 0))
        self.artist_label = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(size=22),
            text_color=COLORS["text2"], anchor="w")
        self.artist_label.pack(fill="x")

        self.progress = ctk.CTkProgressBar(inner, height=10, corner_radius=5,
                                           progress_color=COLORS["accent"])
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(18, 6))
        times = ctk.CTkFrame(inner, fg_color="transparent")
        times.pack(fill="x")
        self.elapsed_label = ctk.CTkLabel(
            times, text="0:00", font=ctk.CTkFont(size=20),
            text_color=COLORS["text2"])
        self.elapsed_label.pack(side="left")
        self.remaining_label = ctk.CTkLabel(
            times, text="—", font=ctk.CTkFont(size=20),
            text_color=COLORS["text2"])
        self.remaining_label.pack(side="right")
        ctk.CTkFrame(inner, fg_color="transparent", height=1).pack(fill="both", expand=True)

        # Bottom row: next up (left) + VU meters (right).
        bottom = ctk.CTkFrame(root, fg_color="transparent")
        bottom.pack(fill="x")
        next_card = ctk.CTkFrame(bottom, fg_color=COLORS["bg_card"], corner_radius=14)
        next_card.pack(side="left", fill="both", expand=True, padx=(0, 12))
        next_inner = ctk.CTkFrame(next_card, fg_color="transparent")
        next_inner.pack(fill="both", expand=True, padx=20, pady=14)
        ctk.CTkLabel(next_inner, text="NEXT UP",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLORS["text3"]).pack(anchor="w")
        self.next_label = ctk.CTkLabel(
            next_inner, text="—", font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text"], anchor="w")
        self.next_label.pack(fill="x")
        self.next_sub_label = ctk.CTkLabel(
            next_inner, text="", font=ctk.CTkFont(size=14),
            text_color=COLORS["text2"], anchor="w")
        self.next_sub_label.pack(fill="x")

        meter_card = ctk.CTkFrame(bottom, fg_color=COLORS["bg_card"], corner_radius=14,
                                  width=430, height=132)
        meter_card.pack(side="right")
        meter_card.pack_propagate(False)
        self.meter = _StudioMeter(meter_card)
        self.meter.pack(fill="both", expand=True, padx=16, pady=12)

    # ── Fullscreen ────────────────────────────────────────────────
    def toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._leave_fullscreen()
            return
        try:
            self._geometry = self.geometry()
        except Exception:
            pass
        self._fullscreen = True
        try:
            self.attributes("-fullscreen", True)
        except Exception:
            pass

    def _leave_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        try:
            self.attributes("-fullscreen", False)
            self.geometry(self._geometry)
        except Exception:
            pass

    # ── Ticking ───────────────────────────────────────────────────
    def _tick(self) -> None:
        self._after_id = None
        try:
            self._render_clock()
            state = self._state_provider() or {}
            self._render_state(state)
        except Exception:
            # A readout must never take the broadcast down: swallow, and the
            # next tick will try again.
            pass
        try:
            self._after_id = self.after(self.TICK_MS, self._tick)
        except Exception:
            self._after_id = None

    def _render_clock(self) -> None:
        now = datetime.now()
        text = now.strftime(CLOCK_FMT)
        if text != self._last.get("clock"):
            self.clock_label.configure(text=text)
            self._last["clock"] = text
        date_text = now.strftime("%a %d %b %Y")
        if date_text != self._last.get("date"):
            self.date_label.configure(text=date_text)
            self._last["date"] = date_text

    def _render_state(self, state: dict) -> None:
        title = str(state.get("title") or "—")
        artist = str(state.get("artist") or "")
        if title != self._last.get("title"):
            self.title_label.configure(text=title[:64])
            self._last["title"] = title
        if artist != self._last.get("artist"):
            self.artist_label.configure(text=artist[:80])
            self._last["artist"] = artist

        position = float(state.get("position") or 0.0)
        duration = float(state.get("duration") or 0.0)
        live = bool(state.get("live"))
        if live:
            self.elapsed_label.configure(text=_fmt(position))
            self.remaining_label.configure(text="LIVE")
            self.progress.set(0)
        else:
            self.elapsed_label.configure(text=_fmt(position))
            self.remaining_label.configure(
                text=("—" if duration <= 0 else f"-{_fmt(max(0.0, duration - position))}"))
            self.progress.set(0.0 if duration <= 0
                              else min(1.0, max(0.0, position / duration)))

        next_title = str(state.get("next_title") or "—")
        next_sub = str(state.get("next_sub") or "")
        if next_title != self._last.get("next_title"):
            self.next_label.configure(text=next_title[:64])
            self._last["next_title"] = next_title
        if next_sub != self._last.get("next_sub"):
            self.next_sub_label.configure(text=next_sub[:80])
            self._last["next_sub"] = next_sub

        self._set_on_air(bool(state.get("on_air")), str(state.get("status") or ""))
        levels = state.get("levels")
        self.meter.set_levels(levels)

    def _set_on_air(self, on_air: bool, status: str) -> None:
        self._blink = (self._blink + 1) % 8
        if on_air != self._on_air:
            self._on_air = on_air
            if on_air:
                self.on_air_frame.configure(fg_color="#5c1116", border_color="#ff2d3f")
                self.on_air_label.configure(text="ON AIR", text_color="#ffffff")
            else:
                self.on_air_frame.configure(fg_color="#3a1013", border_color="#5a1a1f")
                self.on_air_label.configure(text="OFF AIR",
                                            text_color=COLORS["text3"])
        # A live lamp pulses only while something is actually airing.
        dot_color = "#ff2d3f" if (on_air and self._blink < 5) else (
            "#7a2a30" if on_air else "#7a2a30")
        try:
            self.on_air_dot.configure(text_color=dot_color)
        except Exception:
            pass
        suffix = f"  ·  {status}" if status else ""
        self.station_label.configure(text="STUDIO DISPLAY" + suffix[:48])

    # ── Lifecycle ─────────────────────────────────────────────────
    def close(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class _StudioMeter(ctk.CTkFrame):
    """Stereo horizontal VU with a dB scale and peak hold — one canvas."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.canvas = ctk.CTkCanvas(self, bg=COLORS["bg_card"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.levels = (0.0, 0.0)
        self._peak = [DB_FLOOR, DB_FLOOR]
        self._peak_hold = [0.0, 0.0]

    def set_levels(self, levels) -> None:
        try:
            left, right = (float(levels[0]), float(levels[1]))
        except (TypeError, ValueError, IndexError):
            left, right = 0.0, 0.0
        self.levels = (left, right)
        self.redraw()

    def redraw(self) -> None:
        canvas = self.canvas
        try:
            width = max(120, canvas.winfo_width())
            height = max(60, canvas.winfo_height())
        except Exception:
            return
        canvas.delete("all")
        left_margin, right_margin = 20, 54
        bar_left = left_margin
        bar_right = width - right_margin
        bar_w = max(20, bar_right - bar_left)
        is_live = any(value > 0.0001 for value in self.levels)
        rows = (height * 0.32, height * 0.66)
        bar_h = max(10, int(height * 0.24))
        canvas.create_text(6, rows[0], text="L", anchor="w",
                           fill=COLORS["text3"], font=(FONT_FAMILY_FALLBACK, 10))
        canvas.create_text(6, rows[1], text="R", anchor="w",
                           fill=COLORS["text3"], font=(FONT_FAMILY_FALLBACK, 10))

        now = time.monotonic()
        for index, level in enumerate(self.levels):
            y = rows[index]
            db = _db(level)
            fill_w = int(bar_w * ((db - DB_FLOOR) / (DB_TOP - DB_FLOOR)))
            canvas.create_rectangle(bar_left, y - bar_h / 2, bar_right, y + bar_h / 2,
                                    fill="#10151d", outline="")
            if fill_w > 0:
                canvas.create_rectangle(bar_left, y - bar_h / 2,
                                        bar_left + fill_w, y + bar_h / 2,
                                        fill=_meter_color(db), outline="")
            if db > self._peak[index]:
                self._peak[index] = db
                self._peak_hold[index] = now + 1.2
            elif now > self._peak_hold[index]:
                self._peak[index] = max(DB_FLOOR, self._peak[index] - 1.5)
            peak_x = bar_left + int(bar_w * ((self._peak[index] - DB_FLOOR)
                                             / (DB_TOP - DB_FLOOR)))
            canvas.create_rectangle(peak_x - 2, y - bar_h / 2,
                                    peak_x + 2, y + bar_h / 2,
                                    fill="#e6edf5", outline="")
            canvas.create_text(bar_right + 8, y, anchor="w",
                               text=("--" if db <= DB_FLOOR + 0.5
                                     else f"{db:.1f}"),
                               fill=COLORS["text2"],
                               font=(FONT_FAMILY_FALLBACK, 10))
        # dB scale along the bottom edge, under both bars (labels sit above
        # the edge so the canvas never clips them)
        scale_y = height - 2
        for tick in (0, -6, -12, -24, -48):
            x = bar_left + int(bar_w * ((tick - DB_FLOOR) / (DB_TOP - DB_FLOOR)))
            anchor = "se" if tick == 0 else ("sw" if tick == -48 else "s")
            canvas.create_text(x, scale_y - 5, text=str(tick), anchor=anchor,
                               fill=COLORS["text3"],
                               font=(FONT_FAMILY_FALLBACK, 8))
        if not is_live:
            canvas.create_text(bar_left + bar_w / 2, rows[0] - bar_h * 0.9,
                               text="SILENCE", fill=COLORS["text3"],
                               font=(FONT_FAMILY_FALLBACK, 9))
