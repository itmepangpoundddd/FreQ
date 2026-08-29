"""
╔══════════════════════════════════════════════════════════════╗
║              FreQ — Song Cache Manager                       ║
║     Song Cache System — Pre-download for instant playback              ║
╚══════════════════════════════════════════════════════════════╝

Features:
  - Auto-cache YouTube songs in background
  - Play from cache instantly (no download wait)
  - LRU eviction when cache is full
  - Persistent cache across sessions
  - Thread-safe operations

dependencies:
    pip install yt-dlp
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


def find_ffmpeg() -> Optional[str]:
    """Find ffmpeg location — checks PATH, common install dirs, and bundled location."""
    import shutil as _shutil
    import sys as _sys
    if _shutil.which("ffmpeg"):
        return None
    candidates = []
    if _sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
            Path(r"C:\Program Files\ffmpeg\bin"),
            Path.home() / "Documents" / "yt-dlp",
            Path(__file__).parent / "ffmpeg",
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


# Cache metadata file
CACHE_META = "cache_index.json"
DEFAULT_CACHE_DIR = Path.home() / ".freq_cache"
DEFAULT_MAX_FILES = 50          # max cached songs
DEFAULT_MAX_SIZE_MB = 500       # max total cache size in MB


@dataclass
class CacheEntry:
    """Metadata for a cached file"""
    song_id: str
    title: str
    url: str
    filepath: str               # absolute path to cached .mp3
    duration: float = 0.0
    cached_at: float = 0.0      # timestamp
    access_count: int = 0       # LRU: more access = less likely to be evicted
    last_accessed: float = 0.0

    def touch(self) -> None:
        self.access_count += 1
        self.last_accessed = time.time()


class SongCache:
    """YouTube song cache — pre-download for instant playback"""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_size_mb: int = DEFAULT_MAX_SIZE_MB,
    ) -> None:
        self._dir = cache_dir or DEFAULT_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._max_files = max_files
        self._max_size_mb = max_size_mb
        self._lock = threading.Lock()
        self._entries: dict[str, CacheEntry] = {}
        self._downloading: set[str] = set()  # song_ids currently being downloaded
        self._semaphore = threading.Semaphore(3)  # max 3 concurrent downloads
        self._download_queue: list[tuple] = []     # pending downloads
        self._queue_lock = threading.Lock()
        self._on_status: Optional[Callable[[str, str, float], None]] = None  # callback(song_id, status, pct)
        self._load_index()

    @property
    def cache_dir(self) -> Path:
        return self._dir

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def size_mb(self) -> float:
        total = 0
        for entry in self._entries.values():
            p = Path(entry.filepath)
            if p.exists():
                total += p.stat().st_size
        return total / (1024 * 1024)

    def set_status_callback(self, cb: Callable[[str, str, float], None]) -> None:
        """Set callback: cb(song_id, status, percent)"""
        self._on_status = cb

    # ─────────────────────────────────────
    # Public API
    # ─────────────────────────────────────

    def has(self, song_id: str) -> bool:
        """Check if a song is in the cache"""
        with self._lock:
            entry = self._entries.get(song_id)
            if not entry:
                return False
            # Verify the file still exists
            if not Path(entry.filepath).exists():
                del self._entries[song_id]
                self._save_index()
                return False
            return True

    def get(self, song_id: str) -> Optional[str]:
        """Get path of cached file (if available)"""
        with self._lock:
            entry = self._entries.get(song_id)
            if not entry:
                return None
            if not Path(entry.filepath).exists():
                del self._entries[song_id]
                self._save_index()
                return None
            entry.touch()
            self._save_index()
            return entry.filepath

    def is_downloading(self, song_id: str) -> bool:
        return song_id in self._downloading

    def cache_song(
        self,
        song_id: str,
        title: str,
        url: str,
        duration: float = 0.0,
        on_progress: Optional[Callable[[str, float], None]] = None,
    ) -> Optional[str]:
        """
        Download and cache a song
        Returns filepath on success, None on failure or already downloading
        """
        if not YT_DLP_AVAILABLE:
            return None

        # Already in cache
        cached = self.get(song_id)
        if cached:
            return cached

        # Currently downloading
        if song_id in self._downloading:
            return None

        return self._do_download(song_id, title, url, duration, on_progress)

    def cache_song_background(
        self,
        song_id: str,
        title: str,
        url: str,
        duration: float = 0.0,
    ) -> None:
        """Download to cache in background thread (max 3 concurrent)"""
        if not YT_DLP_AVAILABLE:
            return
        if self.has(song_id) or song_id in self._downloading:
            return

        def _worker():
            self._semaphore.acquire()
            try:
                self._do_download(song_id, title, url, duration, None)
            finally:
                self._semaphore.release()
                # Process next in queue
                self._process_queue()

        threading.Thread(target=_worker, daemon=True).start()

    def _process_queue(self) -> None:
        """Process next download in queue"""
        pass  # Semaphore-based: threads auto-start

    def remove(self, song_id: str) -> bool:
        """Remove a song from the cache"""
        with self._lock:
            entry = self._entries.pop(song_id, None)
            if entry:
                try:
                    Path(entry.filepath).unlink(missing_ok=True)
                except Exception:
                    pass
                self._save_index()
                return True
            return False

    def clear(self) -> int:
        """Clear all cache — returns number of files removed"""
        count = len(self._entries)
        with self._lock:
            for entry in self._entries.values():
                try:
                    Path(entry.filepath).unlink(missing_ok=True)
                except Exception:
                    pass
            self._entries.clear()
            self._save_index()
        return count

    def get_stats(self) -> str:
        """Show cache statistics"""
        count = self.count
        size = self.size_mb
        downloading = len(self._downloading)
        lines = [
            f"📦 Cache: {count}/{self._max_files} files ({size:.1f} MB / {self._max_size_mb} MB)",
        ]
        if downloading:
            lines.append(f"⏳ Downloading: {downloading}")
        return "\n".join(lines)

    # ─────────────────────────────────────
    # Internal
    # ─────────────────────────────────────

    def _do_download(
        self,
        song_id: str,
        title: str,
        url: str,
        duration: float,
        on_progress: Optional[Callable[[str, float], None]],
    ) -> Optional[str]:
        """Download YouTube audio to cache"""
        self._downloading.add(song_id)

        def _notify(status: str, pct: float) -> None:
            if on_progress:
                on_progress(status, pct)
            if self._on_status:
                self._on_status(song_id, status, pct)

        try:
            _notify("downloading", 0)

            outtmpl = str(self._dir / f"{song_id}.%(ext)s")

            def _progress_hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes") or 0
                    pct = (downloaded / total * 100) if total else 0
                    _notify("downloading", pct)
                elif d.get("status") == "finished":
                    _notify("processing", 100)

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
                    if not os.path.exists(filename):
                        _notify("error", 0)
                        return None

            actual_duration = duration
            if not actual_duration and info:
                actual_duration = float(info.get("duration") or 0)

            # Save entry
            entry = CacheEntry(
                song_id=song_id,
                title=title,
                url=url,
                filepath=filename,
                duration=actual_duration,
                cached_at=time.time(),
                access_count=0,
                last_accessed=time.time(),
            )

            with self._lock:
                self._entries[song_id] = entry
                self._evict_if_needed()
                self._save_index()

            _notify("cached", 100)
            return filename

        except Exception as e:
            print(f"  ❌  Cache download failed: {e}")
            _notify("error", 0)
            return None
        finally:
            self._downloading.discard(song_id)

    def _evict_if_needed(self) -> None:
        """Evict old files if over max_files or max_size_mb (LRU)"""
        # Evict by count
        while len(self._entries) > self._max_files:
            oldest = min(self._entries.values(), key=lambda e: (e.access_count, e.last_accessed))
            try:
                Path(oldest.filepath).unlink(missing_ok=True)
            except Exception:
                pass
            self._entries.pop(oldest.song_id, None)

        # Evict by size
        while self.size_mb > self._max_size_mb and self._entries:
            oldest = min(self._entries.values(), key=lambda e: (e.access_count, e.last_accessed))
            try:
                Path(oldest.filepath).unlink(missing_ok=True)
            except Exception:
                pass
            self._entries.pop(oldest.song_id, None)

    # ─────────────────────────────────────
    # Index persistence
    # ─────────────────────────────────────

    def _index_path(self) -> Path:
        return self._dir / CACHE_META

    def _save_index(self) -> None:
        try:
            data = {}
            for sid, entry in self._entries.items():
                data[sid] = {
                    "song_id": entry.song_id,
                    "title": entry.title,
                    "url": entry.url,
                    "filepath": entry.filepath,
                    "duration": entry.duration,
                    "cached_at": entry.cached_at,
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed,
                }
            self._index_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_index(self) -> None:
        try:
            if self._index_path().exists():
                data = json.loads(self._index_path().read_text(encoding="utf-8"))
                for sid, d in data.items():
                    # Check if file still exists
                    if Path(d.get("filepath", "")).exists():
                        self._entries[sid] = CacheEntry(**d)
                    else:
                        # File gone — skip loading
                        pass
        except Exception:
            pass


# Global cache instance
cache = SongCache()
