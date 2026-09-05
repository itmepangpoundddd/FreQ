#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║                 🎙️  FreQ — Radio Playlist Manager          ║
║     Radio Playlist Manager with YouTube Integration     ║
╚══════════════════════════════════════════════════════════════╝

Features:
  ✅ Add / Remove / Insert songs in queue
  ✅ Drag/reorder songs in queue
  ✅ Add songs from YouTube video links
  ✅ Add songs from YouTube Playlist (full playlist)
  ✅ Search songs in library
  ✅ Save / Load queue (JSON)
  ✅ Manage playlist presets
  ✅ Play history
  ✅ Playback simulation (Play / Pause / Skip / Previous)

dependencies:
    pip install yt-dlp
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from features import PlaybackModes, RepeatMode

# Safe print: wraps stdout to handle Unicode errors in --noconsole mode
import builtins as _builtins
_original_print = _builtins.print

def _safe_print(*args, **kwargs):
    try:
        _original_print(*args, **kwargs)
    except (UnicodeEncodeError, ValueError, OSError):
        pass

print = _safe_print

# ---------------------------------------------------------------------------
# yt-dlp import (graceful fallback if not installed)
# ---------------------------------------------------------------------------
try:
    import yt_dlp

    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


# ===========================================================================
# Data Models
# ===========================================================================

class SongStatus(Enum):
    QUEUED = auto()
    PLAYING = auto()
    PAUSED = auto()
    COMPLETED = auto()


@dataclass
class Song:
    """Represents a single song in the system"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    artist: str = ""
    duration: float = 0.0            # seconds
    source: str = "local"            # "local" | "youtube" | "file" | "mic"
    url: str = ""                    # YouTube URL (if any)
    file_path: str = ""              # Local file path (mp3/wav/etc)
    mic_duration: float = 0.0        # Recording duration for mic queue items
    mic_device_index: Optional[int] = None
    thumbnail: str = ""
    status: SongStatus = SongStatus.QUEUED

    @property
    def duration_str(self) -> str:
        td = timedelta(seconds=int(self.duration))
        total = int(td.total_seconds())
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @property
    def display_name(self) -> str:
        name = f"{self.title}"
        if self.artist:
            name += f" — {self.artist}"
        return name

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.name
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Song:
        d["status"] = SongStatus[d.get("status", "QUEUED")]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ===========================================================================
# YouTube Integration
# ===========================================================================

class YouTubeManager:
    """Fetch data from YouTube via yt-dlp"""

    _BASE_OPTS: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    @staticmethod
    def is_available() -> bool:
        return YT_DLP_AVAILABLE

    @staticmethod
    def _ydl() -> yt_dlp.YoutubeDL:
        return yt_dlp.YoutubeDL(YouTubeManager._BASE_OPTS)

    @classmethod
    def extract_video_info(cls, url: str) -> Optional[Song]:
        """Fetch info for a single YouTube video"""
        if not cls.is_available():
            raise RuntimeError(
                "yt-dlp not installed. Run: pip install yt-dlp"
            )
        try:
            with cls._ydl() as ydl:
                info = ydl.extract_info(url, download=False)
            return Song(
                title=info.get("title", "Unknown"),
                artist=info.get("uploader", ""),
                duration=float(info.get("duration") or 0),
                source="youtube",
                url=info.get("webpage_url", url),
                thumbnail=info.get("thumbnail", ""),
            )
        except Exception as exc:
            print(f"  ⚠️  Cannot fetch video info: {exc}")
            return None

    @classmethod
    def extract_playlist(cls, url: str) -> list[Song]:
        """Fetch all songs from a YouTube playlist"""
        if not cls.is_available():
            raise RuntimeError(
                "yt-dlp not installed. Run: pip install yt-dlp"
            )
        songs: list[Song] = []
        try:
            with yt_dlp.YoutubeDL({
                **cls._BASE_OPTS,
                "extract_flat": True,       # Fetch names only first
                "playlistend": None,
            }) as ydl:
                info = ydl.extract_info(url, download=False)

            entries = info.get("entries") or []
            total = len(entries)
            if total == 0:
                print("  ⚠️  No videos found in this playlist")
                return songs

            print(f"  📋  Found {total} videos in playlist. Fetching details...")

            # Fetch details for each video (to get duration)
            with cls._ydl() as ydl_detail:
                for idx, entry in enumerate(entries, 1):
                    video_url = entry.get("url") or entry.get("id", "")
                    if not video_url.startswith("http"):
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    try:
                        vinfo = ydl_detail.extract_info(video_url, download=False)
                        songs.append(Song(
                            title=vinfo.get("title", entry.get("title", f"Video {idx}")),
                            artist=vinfo.get("uploader", ""),
                            duration=float(vinfo.get("duration") or 0),
                            source="youtube",
                            url=vinfo.get("webpage_url", video_url),
                            thumbnail=vinfo.get("thumbnail", ""),
                        ))
                        pct = int(idx / total * 100)
                        print(f"  ✅  [{idx}/{total}] {songs[-1].title[:50]}  ({pct}%)")
                    except Exception as e:
                        print(f"  ❌  [{idx}/{total}] Skipping video: {e}")

        except Exception as exc:
            print(f"  ⚠️  Cannot fetch playlist: {exc}")

        return songs

    @classmethod
    def count_playlist(cls, url: str) -> tuple[int, list[dict]]:
        """Fast: count songs and return flat entries (no detail fetch). Returns (count, entries)"""
        if not cls.is_available():
            raise RuntimeError("yt-dlp not installed")
        try:
            with yt_dlp.YoutubeDL({
                **cls._BASE_OPTS,
                "extract_flat": True,
                "playlistend": None,
            }) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = list(info.get("entries") or [])
            return (len(entries), entries)
        except Exception as exc:
            print(f"  ⚠️  Cannot count playlist: {exc}")
            return (0, [])

    @classmethod
    def extract_playlist_range(cls, entries: list[dict], start: int, end: int) -> list[Song]:
        """Fetch details only for entries[start:end] (1-indexed)."""
        if not cls.is_available():
            raise RuntimeError("yt-dlp not installed")
        songs: list[Song] = []
        sliced = entries[start-1:end]
        total = len(sliced)
        print(f"  📥  Fetching {total} songs (#{start}-#{end})...")
        try:
            with cls._ydl() as ydl_detail:
                for idx, entry in enumerate(sliced, 1):
                    video_url = entry.get("url") or entry.get("id", "")
                    if not video_url.startswith("http"):
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    try:
                        vinfo = ydl_detail.extract_info(video_url, download=False)
                        songs.append(Song(
                            title=vinfo.get("title", entry.get("title", f"Video {idx}")),
                            artist=vinfo.get("uploader", ""),
                            duration=float(vinfo.get("duration") or 0),
                            source="youtube",
                            url=vinfo.get("webpage_url", video_url),
                            thumbnail=vinfo.get("thumbnail", ""),
                        ))
                        print(f"  ✅  [{idx}/{total}] {songs[-1].title[:50]}")
                    except Exception as e:
                        print(f"  ❌  [{idx}/{total}] Skipping: {e}")
        except Exception as exc:
            print(f"  ⚠️  Cannot fetch range: {exc}")
        print(f"  📊  Got {len(songs)} songs from range")
        return songs

    @classmethod
    def is_playlist_url(cls, url: str) -> bool:
        """Check if URL is a playlist"""
        return "list=" in url.lower()

    @classmethod
    def is_youtube_url(cls, url: str) -> bool:
        """Check if URL is a YouTube link"""
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        return host in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
        }


# ===========================================================================
# Radio Queue — Main class for managing the queue
# ===========================================================================

class RadioQueue:
    """Manage all songs in the radio queue"""

    def __init__(self) -> None:
        self.queue: list[Song] = []
        self.library: list[Song] = []       # All songs (for search)
        self.history: list[Song] = []       # Play history
        self.presets: dict[str, list[str]] = {}   # preset_name -> [song_ids]
        self.current_index: int = -1
        self.last_settings: dict = {}       # settings restored from .freq file
        self.modes = PlaybackModes()
        self._save_path = Path("radio_queue.freq")

    # ---- Core queue operations ----

    def add_song(self, song: Song) -> None:
        self.queue.append(song)
        if song not in self.library:
            self.library.append(song)
        print(f"  ✅  Added: {song.display_name}")

    def add_songs(self, songs: list[Song]) -> None:
        for s in songs:
            self.add_song(s)
        print(f"  📊  Total added: {len(songs)} songs")

    def insert_song(self, position: int, song: Song) -> bool:
        """Insert a song at a specific position"""
        if position < 0 or position > len(self.queue):
            print(f"  ❌  Invalid position (0-{len(self.queue)})")
            return False
        self.queue.insert(position, song)
        if self.current_index >= position:
            self.current_index += 1
        if song not in self.library:
            self.library.append(song)
        print(f"  ✅  Inserted at position {position}: {song.display_name}")
        return True

    def remove_song(self, index: int) -> Optional[Song]:
        """Remove a song from the queue by index"""
        if not self._valid_index(index):
            print(f"  ❌  Invalid position (0-{len(self.queue) - 1})")
            return None
        old_current = self.current_index
        removed = self.queue.pop(index)
        if not self.queue:
            self.current_index = -1
        elif old_current == index:
            self.current_index = min(index, len(self.queue) - 1)
        elif old_current > index:
            self.current_index -= 1
        print(f"  🗑️  Removed: {removed.display_name}")
        return removed

    def remove_by_title(self, title: str) -> int:
        """Remove all songs matching title (case-insensitive)"""
        old_current = self.current_index
        current_id = (
            self.queue[old_current].id
            if self._valid_index(old_current) else None
        )
        before = len(self.queue)
        self.queue = [s for s in self.queue if title.lower() not in s.title.lower()]
        if not self.queue:
            self.current_index = -1
        elif current_id:
            current_pos = next(
                (i for i, song in enumerate(self.queue) if song.id == current_id),
                None,
            )
            self.current_index = (
                current_pos if current_pos is not None
                else min(old_current, len(self.queue) - 1)
            )
        removed = before - len(self.queue)
        if removed:
            print(f"  🗑️  Removed {removed} songs matching '{title}'")
        else:
            print(f"  ⚠️  No songs matching '{title}'")
        return removed

    def move_song(self, from_idx: int, to_idx: int) -> None:
        """Move a song from from_idx to to_idx"""
        if not self._valid_index(from_idx) or not self._valid_index(to_idx):
            print(f"  ❌  Invalid position (0-{len(self.queue) - 1})")
            return
        current_id = (
            self.queue[self.current_index].id
            if self._valid_index(self.current_index) else None
        )
        song = self.queue.pop(from_idx)
        self.queue.insert(to_idx, song)
        if current_id:
            self.current_index = next(
                i for i, queued in enumerate(self.queue) if queued.id == current_id
            )
        print(f"  🔀  Moved '{song.display_name}' from {from_idx} → {to_idx}")

    def swap(self, idx1: int, idx2: int) -> None:
        """Swap two songs"""
        if not self._valid_index(idx1) or not self._valid_index(idx2):
            print(f"  ❌  Invalid position (0-{len(self.queue) - 1})")
            return
        current_id = (
            self.queue[self.current_index].id
            if self._valid_index(self.current_index) else None
        )
        self.queue[idx1], self.queue[idx2] = self.queue[idx2], self.queue[idx1]
        if current_id:
            self.current_index = next(
                i for i, queued in enumerate(self.queue) if queued.id == current_id
            )
        print(f"  🔀  Swapped positions {idx1} ↔ {idx2}")

    def clear_queue(self) -> None:
        self.queue.clear()
        self.current_index = -1
        print("  🧹  Queue cleared")

    # ---- Playback simulation ----

    def play(self, index: Optional[int] = None) -> None:
        if not self.queue:
            print("  ⚠️  Queue is empty — nothing to play")
            return
        if index is not None:
            if self._valid_index(index):
                self.current_index = index
            else:
                print(f"  ❌  Invalid position (0-{len(self.queue) - 1})")
                return
        elif self.current_index < 0:
            self.current_index = 0

        # Reset all statuses
        for s in self.queue:
            s.status = SongStatus.QUEUED

        current = self.queue[self.current_index]
        current.status = SongStatus.PLAYING
        self.history.append(current)
        print(f"\n  🎵  Now playing: {current.display_name}")
        print(f"     ⏱️  Duration: {current.duration_str}")
        print(f"     📍  Position: {self.current_index + 1}/{len(self.queue)}")

    def pause(self) -> None:
        if self.current_index < 0:
            print("  ⚠️  No song is playing")
            return
        current = self.queue[self.current_index]
        if current.status == SongStatus.PLAYING:
            current.status = SongStatus.PAUSED
            print(f"  ⏸️  Paused: {current.display_name}")
        elif current.status == SongStatus.PAUSED:
            current.status = SongStatus.PLAYING
            print(f"  ▶️  Resumed: {current.display_name}")

    def skip_next(self) -> None:
        if not self.queue:
            print("  ⚠️  Queue is empty")
            return
        if self.current_index < 0:
            self.current_index = -1
        # Mark current as completed
        if 0 <= self.current_index < len(self.queue):
            self.queue[self.current_index].status = SongStatus.COMPLETED
        self.current_index += 1
        if self.current_index >= len(self.queue):
            self.current_index = 0  # wrap around
        self.play()

    def skip_previous(self) -> None:
        if not self.queue:
            print("  \u26a0\ufe0f  Queue is empty")
            return
        if 0 <= self.current_index < len(self.queue):
            self.queue[self.current_index].status = SongStatus.COMPLETED
        prev = self.modes.prev_index(self.current_index, len(self.queue))
        if prev is None:
            return
        self.current_index = prev
        self.play()

    def search(self, query: str) -> list[Song]:
        q = query.lower()
        results = [
            s for s in self.library
            if q in s.title.lower() or q in s.artist.lower()
        ]
        if results:
            print(f"\n  🔍  Found {len(results)} results:")
            for i, s in enumerate(results, 1):
                print(f"      {i}. {s.display_name}  [{s.duration_str}] [{s.source}]")
        else:
            print(f"  🔍  No results for '{query}'")
        return results

    # ---- Presets ----

    def save_preset(self, name: str) -> None:
        self.presets[name] = [s.id for s in self.queue]
        print(f"  💾  Saved preset '{name}' ({len(self.queue)} songs)")

    def load_preset(self, name: str) -> None:
        if name not in self.presets:
            print(f"  ❌  Preset '{name}' not found")
            return
        ids = self.presets[name]
        song_map = {s.id: s for s in self.library}
        loaded = [song_map[sid] for sid in ids if sid in song_map]
        self.queue = loaded
        self.current_index = -1
        print(f"  📂  Loaded preset '{name}' ({len(loaded)} songs)")

    def delete_preset(self, name: str) -> bool:
        """Delete a preset by name. Returns True if deleted."""
        if name not in self.presets:
            print(f"  ❌  Preset '{name}' not found")
            return False
        del self.presets[name]
        print(f"  🗑️  Deleted preset '{name}'")
        return True

    def list_presets(self) -> None:
        if not self.presets:
            print("  📂  No presets")
            return
        print("\n  📂 Presets:")
        for name, ids in self.presets.items():
            print(f"      • {name} ({len(ids)} songs)")

    # ---- Persistence ----

    def save_to_file(self, path: Optional[str] = None, settings: Optional[dict] = None) -> None:
        fp = Path(path) if path else self._save_path
        if fp.suffix.lower() != ".freq":
            fp = fp.with_suffix(".freq")
        data = {
            "format": "freq",
            "version": 2,
            "queue": [s.to_dict() for s in self.queue],
            "library": [s.to_dict() for s in self.library],
            "history": [s.to_dict() for s in self.history],
            "presets": self.presets,
            "current_index": self.current_index,
        }
        if settings:
            data["settings"] = settings
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._save_path = fp
        print(f"  💾  Saved → {fp}")

    def load_from_file(self, path: Optional[str] = None) -> None:
        fp = Path(path) if path else self._save_path
        if fp.suffix.lower() != ".freq":
            fp = fp.with_suffix(".freq")
        if not fp.exists():
            print(f"  ⚠️  File not found: {fp}")
            return
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Try loading as .json (legacy format)
            fp_json = fp.with_suffix(".json")
            if fp_json.exists():
                data = json.loads(fp_json.read_text(encoding="utf-8"))
            else:
                print(f"  ❌  Corrupt file: {fp}")
                return
        self.queue = [Song.from_dict(d) for d in data.get("queue", [])]
        self.library = [Song.from_dict(d) for d in data.get("library", [])]
        self.history = [Song.from_dict(d) for d in data.get("history", [])]
        self.presets = data.get("presets", {})
        self.current_index = data.get("current_index", -1)
        self.last_settings = data.get("settings", {})
        self._save_path = fp
        print(f"  📂  Loaded from {fp} ({len(self.queue)} songs in queue, {len(self.library)} in library)")

    # ---- Display ----

    def display_queue(self) -> None:
        if not self.queue:
            print("\n  📋  Queue is empty")
            return
        total_duration = sum(s.duration for s in self.queue)
        td = timedelta(seconds=int(total_duration))
        print(f"\n  ╔{'═' * 62}╗")
        print(f"  ║  📋  Queue ({len(self.queue)} songs) — Total duration: {td}  ║")
        print(f"  ╠{'═' * 62}╣")
        for i, song in enumerate(self.queue):
            status_icon = {
                SongStatus.QUEUED: "  ",
                SongStatus.PLAYING: "▶️",
                SongStatus.PAUSED: "⏸️",
                SongStatus.COMPLETED: "✅",
            }.get(song.status, "  ")
            marker = " ◀  NOW" if i == self.current_index else ""
            source_icon = "🔗" if song.source == "youtube" else "🎵"
            num = f"{i:>3}."
            print(
                f"  ║ {status_icon} {num} {source_icon} {song.display_name[:40]:<40} "
                f"[{song.duration_str:>7}]{marker:<10} ║"
            )
        print(f"  ╚{'═' * 62}╝")

    def display_history(self) -> None:
        if not self.history:
            print("  📜  No history")
            return
        print("\n  📜  Play history:")
        for i, s in enumerate(reversed(self.history[-20:]), 1):
            print(f"      {i}. {s.display_name}")

    def _valid_index(self, idx: int) -> bool:
        return 0 <= idx < len(self.queue)


# ===========================================================================
# Demo data generator
# ===========================================================================

def generate_demo_songs() -> list[Song]:
    """Create sample songs for demo"""
    demos = [
        Song(title="Bohemian Rhapsody", artist="Queen", duration=354, source="local"),
        Song(title="Stairway to Heaven", artist="Led Zeppelin", duration=482, source="local"),
        Song(title="Hotel California", artist="Eagles", duration=391, source="local"),
        Song(title="Imagine", artist="John Lennon", duration=187, source="local"),
        Song(title="Smells Like Teen Spirit", artist="Nirvana", duration=301, source="local"),
        Song(title="Billie Jean", artist="Michael Jackson", duration=294, source="local"),
        Song(title="Hey Jude", artist="The Beatles", duration=431, source="local"),
        Song(title="Sweet Child O' Mine", artist="Guns N' Roses", duration=356, source="local"),
        Song(title="Wonderwall", artist="Oasis", duration=258, source="local"),
        Song(title="Lose Yourself", artist="Eminem", duration=326, source="local"),
    ]
    return demos


# ===========================================================================
# CLI Interface
# ===========================================================================

def print_banner() -> None:
    print(r"""
  ╔══════════════════════════════════════════════════════════════╗
  ║                 🎙️  FreQ — Radio Playlist Manager          ║
  ║     Radio Playlist Manager with YouTube Integration     ║
  ╚══════════════════════════════════════════════════════════════╝
    """)


def print_help() -> None:
    print("""
  ╔══════════════════════════════════════════════════════════╗
  ║                     📖  All Commands                       ║
  ╠══════════════════════════════════════════════════════════╣
  ║  === Add Songs ===                                      ║
  ║  add <title> [artist]    Add song manually               ║
  ║  add-yt <url>            Add song from YouTube link      ║
  ║  add-playlist <url>      Add full YouTube playlist       ║
  ║  demo                    Add 10 demo songs               ║
  ║                                                          ║
  ║  === Manage Queue ===                                   ║
  ║  list                    Show queue                      ║
  ║  remove <index>          Remove song by position         ║
  ║  remove-all <title>      Remove songs by title           ║
  ║  insert <index> <title>  Insert song at position         ║
  ║  move <from> <to>        Move song                       ║
  ║  swap <a> <b>            Swap two songs                  ║
  ║  clear                   Clear entire queue              ║
  ║                                                          ║
  ║  === Playback ===                                      ║
  ║  play [index]            Play song                       ║
  ║  pause                   Pause / Resume                  ║
  ║  next                    Next song                       ║
  ║  prev                    Previous song                   ║
  ║                                                          ║
  ║  === Search & Library ===                               ║
  ║  search <query>          Search songs in library                  ║
  ║  library                 Show all songs in library       ║
  ║  history                 Show play history               ║
  ║                                                          ║
  ║  === Save & Load ===                                    ║
  ║  save [filename]         Save queue to file              ║
  ║  load [filename]         Load queue from file            ║
  ║  preset-save <name>      Save queue as preset            ║
  ║  preset-load <name>      Load preset                     ║
  ║  preset-delete <name>    Delete a preset                 ║
  ║  preset-list             Show all presets                ║
  ║                                                          ║
  ║  === Audio Devices ===                                  ║
  ║  devices                 Show all output devices         ║
  ║  device <id>             Select output device            ║
  ║                                                          ║
  ║  === Other ===                                         ║
  ║  help                    Show this help                  ║
  ║  quit / exit             Quit program                    ║
  ╚══════════════════════════════════════════════════════════╝
    """)


def parse_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except ValueError:
        return None


def main() -> None:
    print_banner()

    from audio import AudioDeviceManager
    audio_mgr = AudioDeviceManager()
    if audio_mgr.available:
        out_devs = audio_mgr.get_output_devices()
        print(f"  🔊  Found {len(out_devs)} output devices")
    else:
        print("  🔊  sounddevice not installed (pip install sounddevice)")

    rq = RadioQueue()

    # Try loading existing file
    if rq._save_path.exists():
        rq.load_from_file()
        print()

    while True:
        try:
            raw = input("  🎙️  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  👋  Goodbye!")
            break

        if not raw:
            continue

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # ── Quit ──
        if cmd in ("quit", "exit", "q"):
            print("  👋  Goodbye!")
            break

        # ── Help ──
        elif cmd == "help":
            print_help()

        # ── Add local song ──
        elif cmd == "add":
            if not arg:
                print("  ❌  Usage: add <title> [artist]")
                continue
            arg_parts = arg.split("|")
            title = arg_parts[0].strip()
            artist = arg_parts[1].strip() if len(arg_parts) > 1 else ""
            dur_str = input("  ⏱️  Duration (seconds): ").strip()
            dur = float(dur_str) if dur_str else 0
            rq.add_song(Song(title=title, artist=artist, duration=dur, source="local"))

        # ── Add from YouTube ──
        elif cmd == "add-yt":
            if not arg:
                print("  ❌  Usage: add-yt <youtube-url>")
                continue
            if not YouTubeManager.is_available():
                print("  ❌  yt-dlp not installed — pip install yt-dlp")
                continue
            if YouTubeManager.is_playlist_url(arg):
                print("  ℹ️  Looks like a playlist — use add-playlist instead")
                continue
            print("  ⏳  Fetching from YouTube...")
            song = YouTubeManager.extract_video_info(arg)
            if song:
                rq.add_song(song)
            else:
                print("  ❌  Cannot add song")

        # ── Add YouTube playlist ──
        elif cmd == "add-playlist":
            if not arg:
                print("  ❌  Usage: add-playlist <youtube-playlist-url>")
                continue
            if not YouTubeManager.is_available():
                print("  ❌  yt-dlp not installed — pip install yt-dlp")
                continue
            print("  ⏳  Fetching playlist from YouTube...")
            songs = YouTubeManager.extract_playlist(arg)
            if songs:
                rq.add_songs(songs)
            else:
                print("  ❌  No videos found in playlist")

        # ── Demo data ──
        elif cmd == "demo":
            demos = generate_demo_songs()
            rq.add_songs(demos)

        # ── List queue ──
        elif cmd in ("list", "ls", "queue", "q"):
            rq.display_queue()

        # ── Remove by index ──
        elif cmd == "remove":
            idx = parse_int(arg)
            if idx is None:
                print("  ❌  Usage: remove <index>")
            else:
                rq.remove_song(idx)

        # ── Remove by title ──
        elif cmd == "remove-all":
            if not arg:
                print("  ❌  Usage: remove-all <title>")
            else:
                rq.remove_by_title(arg)

        # ── Insert ──
        elif cmd == "insert":
            if not arg:
                print("  ❌  Usage: insert <index> <title]")
                continue
            ins_parts = arg.split(maxsplit=1)
            if len(ins_parts) < 2:
                print("  ❌  Usage: insert <index> <title]")
                continue
            idx = parse_int(ins_parts[0])
            if idx is None:
                print("  ❌  Index must be a number")
                continue
            title = ins_parts[1].strip()
            artist_in = input("  🎤  Artist (optional): ").strip()
            dur_in = input("  ⏱️  Duration (seconds): ").strip()
            dur_f = float(dur_in) if dur_in else 0
            rq.insert_song(idx, Song(title=title, artist=artist_in, duration=dur_f))

        # ── Move ──
        elif cmd == "move":
            m_parts = arg.split()
            if len(m_parts) < 2:
                print("  ❌  Usage: move <from> <to>")
                continue
            f, t = parse_int(m_parts[0]), parse_int(m_parts[1])
            if f is None or t is None:
                print("  ❌  Position must be a number")
            else:
                rq.move_song(f, t)

        # ── Swap ──
        elif cmd == "swap":
            s_parts = arg.split()
            if len(s_parts) < 2:
                print("  ❌  Usage: swap <a> <b>")
                continue
            a, b = parse_int(s_parts[0]), parse_int(s_parts[1])
            if a is None or b is None:
                print("  ❌  Position must be a number")
            else:
                rq.swap(a, b)

        # ── Clear ──
        elif cmd == "clear":
            confirm = input("  ⚠️  Clear entire queue? (y/n): ").strip().lower()
            if confirm == "y":
                rq.clear_queue()

        # ── Play ──
        elif cmd == "play":
            idx = parse_int(arg) if arg else None
            rq.play(idx)

        # ── Pause ──
        elif cmd == "pause":
            rq.pause()

        # ── Next ──
        elif cmd == "next":
            rq.skip_next()

        # ── Previous ──
        elif cmd == "prev":
            rq.skip_previous()

        # ── Search ──
        elif cmd == "search":
            if not arg:
                print("  ❌  Usage: search <query>")
            else:
                rq.search(arg)

        # ── Library ──
        elif cmd == "library":
            if not rq.library:
                print("  📚  Library is empty")
            else:
                print(f"\n  📚  Library ({len(rq.library)} songs):")
                for i, s in enumerate(rq.library, 1):
                    src = "🔗" if s.source == "youtube" else "🎵"
                    print(f"      {i:>3}. {src} {s.display_name}  [{s.duration_str}]")

        # ── History ──
        elif cmd == "history":
            rq.display_history()

        # ── Save ──
        elif cmd == "save":
            rq.save_to_file(arg if arg else None)

        # ── Load ──
        elif cmd == "load":
            rq.load_from_file(arg if arg else None)

        # ── Preset Save ──
        elif cmd == "preset-save":
            if not arg:
                print("  ❌  Usage: preset-save <name>")
            else:
                rq.save_preset(arg)

        # ── Preset Load ──
        elif cmd == "preset-load":
            if not arg:
                print("  ❌  Usage: preset-load <name>")
            else:
                rq.load_preset(arg)

        # ── Preset Delete ──
        elif cmd == "preset-delete":
            if not arg:
                print("  ❌  Usage: preset-delete <name>")
            else:
                rq.delete_preset(arg)

        # ── Preset List ──
        elif cmd == "preset-list":
            rq.list_presets()

        # ── Devices ──
        elif cmd == "devices":
            audio_mgr.refresh()
            print()
            print(audio_mgr.get_device_summary())

        # ── Select Device ──
        elif cmd == "device":
            if not arg:
                print("  ❌  Usage: device <id>")
                print("      Type 'devices' to see the list")
            else:
                did = parse_int(arg)
                if did is None:
                    # Try searching by name
                    if audio_mgr.set_device_by_name(arg):
                        dev = audio_mgr.get_selected_device()
                        print(f"  🔊  Selected: {dev}")
                    else:
                        print(f"  ❌  No device matching '{arg}'")
                else:
                    if audio_mgr.set_device(did):
                        dev = audio_mgr.get_selected_device()
                        print(f"  🔊  Selected: {dev}")
                    else:
                        print(f"  ❌  Device id {did} not found")

        # ── Unknown ──
        else:
            print(f"  ❌  Unknown command '{cmd}' — type 'help' for all commands")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    main()
