"""
FreQ Advanced Features:
  - Shuffle / Repeat (queue/single)
  - Crossfade (fade in/out)
  - Jingle system with timer
  - Emergency Mic + Timer Mic
  - Commercial/Ads break
  - Volume Normalization
  - EQ / Audio Effects
  - Audio trim/edit
  - Dark/Light theme toggle
"""

from __future__ import annotations

import io
import os
import random
import threading
import time
import wave
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

# Keep feature tools working in packaged builds where FFmpeg is bundled
# beside the application instead of being present on PATH.
from player import _ffmpeg_exe, _ffprobe_exe


# ═══════════════════════════════════════════════
# Playback Modes
# ═══════════════════════════════════════════════

class RepeatMode(Enum):
    OFF = auto()
    REPEAT_ALL = auto()     # Repeat entire queue
    REPEAT_ONE = auto()     # Repeat single song


class PlaybackModes:
    """Manage playback modes: shuffle, repeat"""

    def __init__(self) -> None:
        self._shuffle = False
        self._repeat = RepeatMode.OFF
        self._shuffle_order: list[int] = []
        self._shuffle_index: int = 0

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @shuffle.setter
    def shuffle(self, v: bool) -> None:
        self._shuffle = v
        if not v:
            self._shuffle_order.clear()
            self._shuffle_index = 0

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    @repeat.setter
    def repeat(self, v: RepeatMode) -> None:
        self._repeat = v

    def toggle_shuffle(self) -> bool:
        self._shuffle = not self._shuffle
        if not self._shuffle:
            self._shuffle_order.clear()
            self._shuffle_index = 0
        return self._shuffle

    def toggle_repeat(self) -> RepeatMode:
        modes = [RepeatMode.OFF, RepeatMode.REPEAT_ALL, RepeatMode.REPEAT_ONE]
        idx = modes.index(self._repeat)
        self._repeat = modes[(idx + 1) % len(modes)]
        return self._repeat

    def next_index(self, current: int, queue_len: int) -> Optional[int]:
        """Calculate next index"""
        if queue_len == 0:
            return None

        if self._repeat == RepeatMode.REPEAT_ONE:
            return current

        if self._shuffle:
            return self._next_shuffle_index(queue_len)

        nxt = current + 1
        if nxt >= queue_len:
            if self._repeat == RepeatMode.REPEAT_ALL:
                return 0
            return None
        return nxt

    def prev_index(self, current: int, queue_len: int) -> Optional[int]:
        if queue_len == 0:
            return None
        if self._repeat == RepeatMode.REPEAT_ONE:
            return current
        if self._shuffle:
            return self._prev_shuffle_index(queue_len)
        prev = current - 1
        if prev < 0:
            if self._repeat == RepeatMode.REPEAT_ALL:
                return queue_len - 1
            return 0
        return prev

    def _next_shuffle_index(self, queue_len: int) -> int:
        if not self._shuffle_order or len(self._shuffle_order) != queue_len:
            self._shuffle_order = list(range(queue_len))
            random.shuffle(self._shuffle_order)
            self._shuffle_index = 0
        else:
            self._shuffle_index = (self._shuffle_index + 1) % queue_len
        return self._shuffle_order[self._shuffle_index]

    def _prev_shuffle_index(self, queue_len: int) -> int:
        if not self._shuffle_order or len(self._shuffle_order) != queue_len:
            self._shuffle_order = list(range(queue_len))
            random.shuffle(self._shuffle_order)
            self._shuffle_index = 0
        else:
            self._shuffle_index = (self._shuffle_index - 1) % queue_len
        return self._shuffle_order[self._shuffle_index]

    def reshuffle(self, queue_len: int) -> None:
        self._shuffle_order = list(range(queue_len))
        random.shuffle(self._shuffle_order)
        self._shuffle_index = 0


# ═══════════════════════════════════════════════
# Crossfade
# ═══════════════════════════════════════════════

@dataclass
class CrossfadeSettings:
    enabled: bool = False
    duration: float = 3.0       # seconds
    fade_in: bool = True
    fade_out: bool = True


# ═══════════════════════════════════════════════
# Jingle System
# ═══════════════════════════════════════════════

@dataclass
class JingleConfig:
    enabled: bool = False
    jingle_file: str = ""       # path to jingle audio file
    interval: int = 5           # play jingle every N songs
    song_counter: int = 0       # internal counter
    fade_duration: float = 1.0  # fade in/out seconds


# ═══════════════════════════════════════════════
# Mic System
# ═══════════════════════════════════════════════

@dataclass
class MicConfig:
    enabled: bool = False
    timer_enabled: bool = False
    timer_duration: float = 30.0    # seconds to keep mic open
    timer_remaining: float = 0.0
    emergency: bool = False
    device_index: Optional[int] = None
    gain: float = 1.0
    duck_volume: float = 0.3         # volume level when mic is active (0.0-1.0)
    _timer_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _stop_event: Optional[threading.Event] = field(default=None, repr=False)


# ═══════════════════════════════════════════════
# Commercial / Ads Break
# ═══════════════════════════════════════════════

@dataclass
class CommercialBreak:
    enabled: bool = False
    ads: list = field(default_factory=list)   # list of file paths
    interval: int = 10            # play ad every N songs
    song_counter: int = 0
    current_ad_index: int = 0     # round-robin


# ═══════════════════════════════════════════════
# Volume Normalization
# ═══════════════════════════════════════════════

class VolumeNormalizer:
    """Normalize volume across all songs"""

    def __init__(self) -> None:
        self.enabled = False
        self.target_lufs: float = -14.0   # target loudness (LUFS)
        self._gains: dict[str, float] = {}  # song_id -> gain

    def analyze_file(self, filepath: str) -> float:
        """Analyze file loudness, return required gain"""
        # Note: analysis shells out to ffmpeg's loudnorm filter; numpy was
        # never used here, so the old NUMPY_AVAILABLE gate only disabled the
        # feature for no reason on machines without numpy.
        if not os.path.isfile(filepath):
            return 1.0

        try:
            import subprocess
            result = subprocess.run(
                [_ffmpeg_exe(), "-i", filepath, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30
            )
            import json
            # Parse output for integrated loudness
            output = result.stderr
            start = output.rfind("{")
            end = output.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(output[start:end])
                current_lufs = float(data.get("input_i", -14))
                gain_db = self.target_lufs - current_lufs
                return 10 ** (gain_db / 20.0)
        except Exception:
            pass
        return 1.0

    def get_gain(self, song_id: str, filepath: str) -> float:
        if not self.enabled:
            return 1.0
        if song_id in self._gains:
            return self._gains[song_id]
        gain = self.analyze_file(filepath)
        self._gains[song_id] = gain
        return gain


# ═══════════════════════════════════════════════
# EQ / Audio Effects
# ═══════════════════════════════════════════════

@dataclass
class EQSettings:
    enabled: bool = False
    preset: str = "flat"
    bass: float = 0.0       # -12 to +12 dB
    mid: float = 0.0
    treble: float = 0.0
    reverb: float = 0.0     # 0 to 1
    delay: float = 0.0      # seconds
    speed: float = 1.0      # 0.5 to 2.0

    PRESETS: dict = field(default_factory=lambda: {
        "flat":      {"bass": 0, "mid": 0, "treble": 0},
        "bass_boost": {"bass": 8, "mid": 2, "treble": -2},
        "bass_cut":  {"bass": -6, "mid": 1, "treble": 0},
        "treble_boost": {"bass": -2, "mid": 0, "treble": 8},
        "vocal":     {"bass": -3, "mid": 6, "treble": 2},
        "rock":      {"bass": 6, "mid": -1, "treble": 4},
        "pop":       {"bass": 2, "mid": 4, "treble": 3},
        "jazz":      {"bass": 4, "mid": 2, "treble": 1},
        "electronic": {"bass": 8, "mid": -2, "treble": 6},
    })

    def apply_preset(self, name: str) -> None:
        p = self.PRESETS.get(name, self.PRESETS["flat"])
        self.bass = p["bass"]
        self.mid = p["mid"]
        self.treble = p["treble"]
        self.preset = name

    def get_ffmpeg_filter(self) -> str:
        """Generate ffmpeg filter string from EQ settings"""
        filters = []
        if self.bass != 0 or self.mid != 0 or self.treble != 0:
            bass_g = self.bass
            mid_g = self.mid
            treble_g = self.treble
            filters.append(
                f"equalizer=f=100:t=h:w=100:g={bass_g},"
                f"equalizer=f=1000:t=h:w=1000:g={mid_g},"
                f"equalizer=f=8000:t=h:w=3000:g={treble_g}"
            )
        if self.speed != 1.0:
            filters.append(f"atempo={self.speed}")
        if self.reverb > 0:
            level = int(self.reverb * 80)
            filters.append(f"aecho=0.8:0.88:{level}:0.4")
        return ",".join(filters) if filters else ""


# ═══════════════════════════════════════════════
# Audio Edit
# ═══════════════════════════════════════════════

@dataclass
class EditResult:
    success: bool = False
    output_path: str = ""
    error: str = ""


class AudioEditor:
    """Basic audio editing"""

    @staticmethod
    def trim(filepath: str, start: float, end: float, output: str = "") -> EditResult:
        """Trim audio from start to end (seconds)"""
        if not os.path.isfile(filepath):
            return EditResult(error="File not found")
        if not output:
            stem = Path(filepath).stem
            output = str(Path(filepath).parent / f"{stem}_trimmed.mp3")
        try:
            import subprocess
            cmd = [
                _ffmpeg_exe(), "-y", "-i", filepath,
                "-ss", str(start), "-to", str(end),
                "-c:a", "libmp3lame", "-q:a", "2",
                output
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return EditResult(success=True, output_path=output)
            return EditResult(error=result.stderr.decode("utf-8", errors="replace")[:200])
        except Exception as e:
            return EditResult(error=str(e))

    @staticmethod
    def fade_in(filepath: str, duration: float = 3.0, output: str = "") -> EditResult:
        if not os.path.isfile(filepath):
            return EditResult(error="File not found")
        if not output:
            stem = Path(filepath).stem
            output = str(Path(filepath).parent / f"{stem}_fadein.mp3")
        try:
            import subprocess
            cmd = [
                _ffmpeg_exe(), "-y", "-i", filepath,
                "-af", f"afade=t=in:ss=0:d={duration}",
                "-c:a", "libmp3lame", "-q:a", "2", output
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return EditResult(success=True, output_path=output)
            return EditResult(error=result.stderr.decode("utf-8", errors="replace")[:200])
        except Exception as e:
            return EditResult(error=str(e))

    @staticmethod
    def fade_out(filepath: str, duration: float = 3.0, output: str = "") -> EditResult:
        if not os.path.isfile(filepath):
            return EditResult(error="File not found")
        if not output:
            stem = Path(filepath).stem
            output = str(Path(filepath).parent / f"{stem}_fadeout.mp3")
        try:
            import subprocess
            # Get duration first
            probe = subprocess.run(
                [_ffprobe_exe(), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=10
            )
            total = float(probe.stdout.strip() or "0")
            fade_start = max(0, total - duration)
            cmd = [
                _ffmpeg_exe(), "-y", "-i", filepath,
                "-af", f"afade=t=out:st={fade_start}:d={duration}",
                "-c:a", "libmp3lame", "-q:a", "2", output
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return EditResult(success=True, output_path=output)
            return EditResult(error=result.stderr.decode("utf-8", errors="replace")[:200])
        except Exception as e:
            return EditResult(error=str(e))

    @staticmethod
    def normalize(filepath: str, output: str = "") -> EditResult:
        if not os.path.isfile(filepath):
            return EditResult(error="File not found")
        if not output:
            stem = Path(filepath).stem
            output = str(Path(filepath).parent / f"{stem}_norm.mp3")
        try:
            import subprocess
            cmd = [
                _ffmpeg_exe(), "-y", "-i", filepath,
                "-af", "loudnorm=I=-14:TP=-1:LRA=11",
                "-c:a", "libmp3lame", "-q:a", "2", output
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return EditResult(success=True, output_path=output)
            return EditResult(error=result.stderr.decode("utf-8", errors="replace")[:200])
        except Exception as e:
            return EditResult(error=str(e))

    @staticmethod
    def change_speed(filepath: str, speed: float = 1.0, output: str = "") -> EditResult:
        if not os.path.isfile(filepath):
            return EditResult(error="File not found")
        if not output:
            stem = Path(filepath).stem
            output = str(Path(filepath).parent / f"{stem}_speed{speed}.mp3")
        try:
            import subprocess
            cmd = [
                _ffmpeg_exe(), "-y", "-i", filepath,
                "-af", f"atempo={speed}",
                "-c:a", "libmp3lame", "-q:a", "2", output
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode == 0:
                return EditResult(success=True, output_path=output)
            return EditResult(error=result.stderr.decode("utf-8", errors="replace")[:200])
        except Exception as e:
            return EditResult(error=str(e))


# ═══════════════════════════════════════════════
# Theme
# ═══════════════════════════════════════════════

class ThemeManager:
    """Dark / Light theme toggle"""

    DARK = {
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
    }

    LIGHT = {
        "bg":           "#ffffff",
        "bg_card":      "#f6f8fa",
        "bg_hover":     "#eaeef2",
        "bg_selected":  "#dbe9f7",
        "bg_input":     "#ffffff",
        "border":       "#d0d7de",
        "border_focus": "#0969da",
        "text":         "#1f2328",
        "text2":        "#656d76",
        "text3":        "#8b949e",
        "accent":       "#0969da",
        "accent_hover": "#0550ae",
        "green":        "#1a7f37",
        "green_dark":   "#116329",
        "red":          "#cf222e",
        "red_dark":     "#a40e26",
        "orange":       "#bf8700",
        "purple":       "#8250df",
        "playing_bg":   "#ddf4ff",
        "playing_border": "#0969da",
        "selected_border": "#0969da",
    }

    def __init__(self) -> None:
        self.is_dark: bool = True

    @property
    def colors(self) -> dict:
        return self.DARK.copy() if self.is_dark else self.LIGHT.copy()

    def toggle(self) -> dict:
        self.is_dark = not self.is_dark
        return self.colors

    def set_dark(self) -> dict:
        self.is_dark = True
        return self.colors

    def set_light(self) -> dict:
        self.is_dark = False
        return self.colors


# ═══════════════════════════════════════════════
# National Anthem Scheduler (เพลงชาติ)
# ═══════════════════════════════════════════════

@dataclass
class AnthemSchedule:
    """Configuration for national anthem playback."""
    enabled: bool = True
    morning_time: str = "08:00"       # เวลาเพลงชาติตอนเช้า
    evening_time: str = "18:00"       # เวลาเพลงชาติตอนเย็น
    anthem_file: str = ""             # path ไฟล์เพลงชาติ
    duration: float = 0.0              # ความยาวเพลงชาติ (วินาที)
    fade_in: float = 2.0               # fade in (วินาที)
    fade_out: float = 2.0              # fade out (วินาที)
    pause_before: float = 1.0          # หยุดเพลงก่อนเล่นเพลงชาติ
    resume_after: bool = True          # เล่นเพลงต่อหลังเพลงชาติจบ
    play_daily: bool = True            # เล่นทุกวัน
    days: list[str] = field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "morning_time": self.morning_time,
            "evening_time": self.evening_time,
            "anthem_file": self.anthem_file,
            "duration": self.duration,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "pause_before": self.pause_before,
            "resume_after": self.resume_after,
            "play_daily": self.play_daily,
            "days": self.days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AnthemSchedule:
        return cls(
            enabled=data.get("enabled", True),
            morning_time=data.get("morning_time", "08:00"),
            evening_time=data.get("evening_time", "18:00"),
            anthem_file=data.get("anthem_file", ""),
            duration=data.get("duration", 0.0),
            fade_in=data.get("fade_in", 2.0),
            fade_out=data.get("fade_out", 2.0),
            pause_before=data.get("pause_before", 1.0),
            resume_after=data.get("resume_after", True),
            play_daily=data.get("play_daily", True),
            days=data.get("days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]),
        )


class NationalAnthem:
    """Scheduler for playing national anthem (เพลงชาติ) at specific times.
    
    Typical Thai schedule:
      - 08:00 (เช้า)
      - 18:00 (เย็น)
    """

    def __init__(self):
        self.config = AnthemSchedule()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._play_callback: Optional[Callable] = None
        self._pause_callback: Optional[Callable] = None
        self._resume_callback: Optional[Callable] = None
        self._status_callback: Optional[Callable] = None
        self._play_confirm: Optional[Callable] = None
        self._last_played: dict[str, str] = {}  # {"morning": "2024-01-15", "evening": "2024-01-15"}
        self._last_check_minute: int = -1       # minute-of-day of the last check (-1 = never)
        self._play_attempts: dict[str, int] = {}  # failed starts per period today
        self._playing_period: str = "manual"      # period currently being played

    def set_callbacks(self, play: Callable = None, pause: Callable = None,
                     resume: Callable = None, status: Callable = None,
                     confirm: Callable = None):
        """Set callbacks for anthem actions.

        ``confirm`` (optional) is called by the GUI once the anthem is
        actually audible — the scheduler uses it to mark the period as
        played for today, instead of assuming every fire succeeds.
        """
        self._play_callback = play
        self._pause_callback = pause
        self._resume_callback = resume
        self._status_callback = status
        self._play_confirm = confirm

    def confirm_played(self, ok: bool = True) -> None:
        """Report from the GUI whether the anthem really started.

        Marks the current period as played on success, or clears the
        playing period on failure so the catch-up window can retry.
        """
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        period = self._playing_period
        if ok:
            if period and period != "manual":
                self._last_played[period] = today
                self._log(f"Anthem confirmed audible ({period})")
        else:
            self._log("Anthem start failed — will retry within the catch-up window")
        self._playing_period = "manual"

    def get_force_marker(self) -> str:
        """Period label for manual plays (never auto-marks anything)."""
        return self._playing_period or "manual"

    def start(self) -> None:
        """Start the anthem scheduler."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._log("Anthem scheduler started")

    def stop(self) -> None:
        """Stop the anthem scheduler."""
        self._running = False
        self._stop_event.set()
        self._log("Anthem scheduler stopped")

    def _run_loop(self) -> None:
        """Main scheduler loop — checks continuously once per second.

        A crashed check must never kill the thread: the anthem would
        never fire again for the rest of the session.
        """
        while self._running:
            try:
                self._check_times()
            except Exception:
                import traceback
                traceback.print_exc()
            self._stop_event.wait(1.0)

    @staticmethod
    def _normalize_time(t: str) -> str:
        """Normalize time string to HH:MM format (e.g. '8:0' -> '08:00', '8:00' -> '08:00')."""
        t = t.strip()
        if not t:
            return ""
        try:
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return f"{h:02d}:{m:02d}"
        except (ValueError, IndexError):
            return t

    @staticmethod
    def _time_to_minutes(t: str) -> Optional[int]:
        """Convert 'HH:MM' to minutes-of-day, or None if invalid."""
        if not t:
            return None
        try:
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return None
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h * 60 + m

    def _check_times(self) -> None:
        """Check if it's time to play the anthem.

        The loop ticks once per second, so an exact-minute match is
        reliable — but a machine waking from sleep, a blocked main thread,
        or a failed start can skip past the minute. A short catch-up
        window (up to ``kCatchUpMinutes``) re-fires the anthem instead of
        silently moving on. ``_last_played`` prevents double-firing for
        starts that were actually delivered; ``_play_attempts`` caps
        retries when starts keep failing.
        """
        if not self.config.enabled or not self.config.anthem_file:
            return
        if not os.path.isfile(self.config.anthem_file):
            return

        import datetime
        now = datetime.datetime.now()
        now_minute = now.hour * 60 + now.minute
        prev_minute = self._last_check_minute
        self._last_check_minute = now_minute
        current_day = now.strftime("%a").lower()[:3]
        today = now.strftime("%Y-%m-%d")

        # Check day
        if current_day not in self.config.days:
            return

        # Normalize config times for comparison (handles '8:00' vs '08:00')
        morning = self._normalize_time(self.config.morning_time)
        evening = self._normalize_time(self.config.evening_time)

        # Check morning anthem
        if self._last_played.get("morning") != today:
            target = self._time_to_minutes(morning)
            if target is not None and self._in_fire_window(
                    now_minute, prev_minute, target,
                    self._play_attempts.get("morning", 0)):
                self._trigger_anthem("morning")

        # Check evening anthem
        if self._last_played.get("evening") != today:
            target = self._time_to_minutes(evening)
            if target is not None and self._in_fire_window(
                    now_minute, prev_minute, target,
                    self._play_attempts.get("evening", 0)):
                self._trigger_anthem("evening")

    kCatchUpMinutes = 3

    def _in_fire_window(self, now_minute: int, prev_minute: int,
                        target: int, attempts: int) -> bool:
        """True when ``target`` minute should fire right now.

        Fires exactly on the minute, or during the catch-up window when
        the tick jumped over it (sleep wake / stalled thread) — retries
        are capped so a permanently failing start cannot loop all day.
        """
        if attempts >= self.kCatchUpMinutes:
            return False
        if now_minute == target:
            return True
        # Crossed the target between two ticks (jumped minutes) — capped
        # at the catch-up window so a tick that landed long after the
        # hour (sleep wake, stalled machine) does not fire an anthem
        # minutes (or hours) late.
        if (prev_minute < target <= now_minute
                and now_minute <= target + self.kCatchUpMinutes):
            return True
        # Late start: app launched after the target, still within window.
        if (prev_minute == -1 and target < now_minute
                and now_minute <= target + self.kCatchUpMinutes):
            return True
        return False

    def _trigger_anthem(self, period: str) -> None:
        """Hand the anthem to the GUI without blocking the time checker.

        The "played today" mark is set by the GUI only after playback is
        confirmed audible (``confirm_played``) — a failed or swallowed
        start stays unmarked so the catch-up window can retry.
        """
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if self._last_played.get(period) == today:
            return
        self._play_attempts[period] = self._play_attempts.get(period, 0) + 1
        self._playing_period = period
        threading.Thread(
            target=self._play_anthem, args=(period,), daemon=True
        ).start()

    def _play_anthem(self, period: str) -> None:
        """Play the national anthem (scheduler thread).

        The GUI owns the whole snapshot → stop → play → resume cycle;
        this only forwards the request and reports status. There is NO
        timer-based resume here: the previous design resumed after
        ``duration`` seconds and cut the anthem off / raced the start.
        """
        self._log(f"Playing national anthem ({period})")
        self._status("playing", period)

        if self._play_callback:
            try:
                self._play_callback(self.config.anthem_file)
            except Exception:
                import traceback
                traceback.print_exc()
                self._status("finished", period)
                return

        # If the GUI has no resume wiring it finishes via its own EOF
        # callback; the legacy timer path only runs when a resume
        # callback exists but no play confirmation is wired.
        if self._resume_callback and not self._play_confirm:
            duration = self.config.duration or self._detect_duration()
            if self._stop_event.wait(max(0.0, duration)):
                return
            if self.config.resume_after and self._resume_callback:
                self._resume_callback()
            self._log(f"Anthem finished ({period})")
            self._status("finished", period)

    def _detect_duration(self) -> float:
        """Read the configured anthem duration when it was not saved yet."""
        if not self.config.anthem_file or not os.path.isfile(self.config.anthem_file):
            return 80.0
        try:
            from mutagen import File as MutagenFile
            media = MutagenFile(self.config.anthem_file)
            if media and media.info and media.info.length > 0:
                self.config.duration = float(media.info.length)
                return self.config.duration
        except Exception:
            pass
        return 80.0

    def _log(self, msg: str) -> None:
        """Log a message."""
        if self._status_callback:
            self._status_callback("log", msg)

    def _status(self, state: str, period: str = "") -> None:
        """Report status."""
        if self._status_callback:
            self._status_callback(state, period)

    def get_next_play_time(self) -> Optional[str]:
        """Get the next anthem play time."""
        if not self.config.enabled:
            return None

        import datetime
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%a").lower()[:3]
        today = now.strftime("%Y-%m-%d")

        if current_day not in self.config.days:
            return "No anthem scheduled today"

        # Normalize config times so '8:00' compares correctly against '08:00'
        morning = self._normalize_time(self.config.morning_time)
        evening = self._normalize_time(self.config.evening_time)

        # Check if morning already played
        morning_played = self._last_played.get("morning") == today
        evening_played = self._last_played.get("evening") == today

        if not morning_played and current_time < morning:
            return f"Morning: {morning}"
        elif not evening_played and current_time < evening:
            return f"Evening: {evening}"
        elif not morning_played:
            return f"Morning: {morning} (pending)"
        elif not evening_played:
            return f"Evening: {evening} (pending)"
        else:
            return "All done for today"

    def force_play(self, period: str = "manual") -> bool:
        """Force play the anthem now (for testing)."""
        if not self.config.anthem_file or not os.path.isfile(self.config.anthem_file):
            self._log("No anthem file selected")
            return False

        threading.Thread(target=self._play_anthem, args=(period,), daemon=True).start()
        return True
