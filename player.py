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
        self._progress_thread: Optional[threading.Thread] = None
        # Each playback monitor gets its own cancellation event. A shared
        # boolean can be reset by a new track before the old monitor exits.
        self._progress_stop_event = threading.Event()
        self._callback_queue: list[tuple[Callable, tuple]] = []
        self._callback_lock = threading.Lock()
        # Seek state
        self._seek_offset: float = 0.0      # seconds offset from original file start
        self._original_file: Optional[str] = None  # full original file (before seek trim)
        self._seek_trimmed_files: list[str] = []   # temp files created by seek
        # YouTube download state
        self._yt_ready_file: Optional[str] = None  # resolved file path after YT download

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
            pygame.mixer.music.set_volume(self._volume)
            self._temp_dir = Path(tempfile.mkdtemp(prefix="freq_"))
            self._initialized = True
            return True
        except Exception as e:
            print(f"  ⚠️  Cannot init pygame mixer: {e}")
            return False

    def shutdown(self) -> None:
        """Shutdown mixer and cleanup"""
        self.stop()
        if self._initialized:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
            self._initialized = False
        # Cleanup temp files
        self._cleanup_seek_files()
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
        if self._initialized:
            try:
                pygame.mixer.music.set_volume(self._volume)
            except Exception:
                pass

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

        with self._lock:
            try:
                # Track the original full file for seek operations.
                # If this is a fresh play (not a seek-trimmed file), reset seek state.
                if not filepath.startswith(str(self._temp_dir or "")):
                    self._original_file = filepath
                elif self._original_file is None:
                    self._original_file = filepath
                # else: seek-trimmed file, keep existing _original_file

                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                self._current_file = filepath
                self._is_playing = True
                self._is_paused = False
                self._play_start_time = time.time()
                self._pause_offset = 0.0
                # Only reset seek offset for fresh plays (not seeked files)
                if self._current_file == self._original_file:
                    self._seek_offset = 0.0
                self._duration = duration
                self._on_finish = on_finish
                self._start_progress_monitor()
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
                try:
                    pygame.mixer.music.unpause()
                    self._is_paused = False
                    self._play_start_time = time.time() - self._pause_offset
                except Exception:
                    pass

    def stop(self) -> None:
        if not self.available:
            return
        with self._lock:
            self._progress_stop_event.set()
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
            self._cleanup_seek_files()

    def get_position(self) -> float:
        """Current position in seconds (accounting for seek offset)"""
        if not self.available:
            return 0.0
        if not self._is_playing:
            return 0.0
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
        if not self._ffmpeg_available():
            print("  ⚠️  ffmpeg not installed — seek unavailable")
            return

        # Clamp to valid range
        total = self._duration if self._duration > 0 else self._get_file_duration()
        seconds = max(0.0, min(seconds, max(0.0, total - 0.1)))

        # Remember the file we were playing from (original, not previously trimmed)
        source = self._original_file or self._current_file

        # If seeking to the very start, just restart from original file
        if seconds <= 0.1:
            self._do_seek(source, 0.0, total)
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
        """Probe file duration via ffprobe (fallback 0)"""
        if not self._current_file or not os.path.isfile(self._current_file):
            return 0.0
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
        try:
            result = subprocess.run(
                [_ffmpeg_exe(), "-version"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
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

    @property
    def is_playing(self) -> bool:
        return self._is_playing and not self._is_paused

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
            # pygame can briefly report False from get_busy() while a newly
            # loaded file is being decoded. This matters most for short jingles.
            started_at = time.monotonic()
            while not stop_event.wait(0.25):
                if not self._is_playing or self._is_paused:
                    continue
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
