"""Air-Check Track Splitting — cut a finished air-check WAV into one
file per track, named from real queue metadata.

During a recording the GUI appends a TrackLog event every time a queue
track starts (kind, title, artist, offset seconds from recording start).
When the recording finishes, ``split_recording`` validates the events,
pairs them into segments (a segment runs from its own offset to the next
event's offset; the last one runs to end-of-file) and cuts each segment
out of the master WAV with ffmpeg stream copy — fast and lossless.

Naming: ``track_001 - Artist - Title.wav`` (index keeps duplicates
unique). Segments inherit the recorder's retention via
``AirCheckRecorder.list_files`` (which also matches ``track_*``).

This module is offline-only: it never touches the live audio path and
is meant to run inside a background worker thread.
"""
from __future__ import annotations

import os
import re
import subprocess
import wave
from typing import Callable, Optional

from aircheck import _ffmpeg_exe

MIN_SEGMENT_SEC = 2.0        # shorter candidates are dropped
MAX_SEGMENTS = 500           # hard cap: a broken event stream cannot
                             # spawn thousands of ffmpeg processes
NAME_MAXLEN = 80

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")


# ── naming helpers ─────────────────────────────────────────────────────
def sanitize_name(text: str, maxlen: int = NAME_MAXLEN) -> str:
    """Make a Windows-safe, single-line file-name fragment."""
    if not text:
        return ""
    # Newlines/tabs become word separators FIRST (the illegal-char class
    # also eats control chars, which would glue words together).
    out = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    out = _ILLEGAL.sub("", out)
    out = _WS.sub(" ", out).strip().strip(".").strip()
    if len(out) > maxlen:
        out = out[:maxlen].rstrip()
    return out


def build_segment_name(index: int, artist: str, title: str,
                       ext: str = ".wav") -> str:
    """``track_001 - Artist - Title.wav`` (artist omitted when empty)."""
    title = sanitize_name(title) or "Untitled"
    artist = sanitize_name(artist)
    label = f"{artist} - {title}" if artist else title
    return f"track_{index:03d} - {label}{ext}"


# ── event log (filled live by the GUI) ─────────────────────────────────
class TrackLog:
    """Track-start events captured during one recording.

    Offsets are seconds from recording start. ``add`` keeps the list
    sorted by offset and rejects degenerate entries so a messy GUI
    stream can never poison the splitter.
    """

    def __init__(self) -> None:
        self._events: list[dict] = []

    def clear(self) -> None:
        self._events = []

    def add(self, kind: str = "music", title: str = "",
            artist: str = "", duration: float = 0.0,
            offset: Optional[float] = None) -> Optional[dict]:
        try:
            offset = float(offset)
        except (TypeError, ValueError):
            return None
        if offset is None or offset < 0:
            return None
        ev = {
            "kind": str(kind or "music"),
            "title": str(title or "Untitled"),
            "artist": str(artist or ""),
            "duration": max(0.0, float(duration or 0.0)),
            "offset": offset,
        }
        self._events.append(ev)
        self._events.sort(key=lambda e: e["offset"])
        return ev

    def events(self) -> list:
        return [dict(e) for e in self._events]

    def __len__(self) -> int:
        return len(self._events)


# ── validation ─────────────────────────────────────────────────────────
def _validate_events(events, file_duration: float) -> list:
    """Clamp/sort events into the file; drop ones at or past the end."""
    out = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        try:
            off = min(max(0.0, float(e.get("offset", -1.0))), file_duration)
        except (TypeError, ValueError):
            continue
        if off >= file_duration:
            continue
        out.append({
            "kind": str(e.get("kind", "music")),
            "title": str(e.get("title", "Untitled")),
            "artist": str(e.get("artist", "")),
            "duration": max(0.0, float(e.get("duration", 0.0) or 0.0)),
            "offset": off,
        })
    out.sort(key=lambda e: e["offset"])
    return out


# ── the splitter ───────────────────────────────────────────────────────
def wav_duration(path: str) -> float:
    """Duration of a WAV file in seconds (header-based)."""
    with wave.open(path, "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate() or 1
    return frames / float(rate)


def split_recording(wav_path: str, events,
                    out_folder: Optional[str] = None,
                    progress: Optional[Callable[[int, int], None]] = None
                    ) -> list:
    """Cut ``wav_path`` into per-track WAVs. Returns segment dicts:
    ``{"path","start","end","duration","title","artist","kind","bytes"}``.

    - events are clamped, sorted and merged into contiguous segments
    - segments shorter than MIN_SEGMENT_SEC are dropped
    - the last segment always runs to end-of-file
    - any ffmpeg failure records ``"error"`` on that segment entry
      (caller can filter on ``"error" not in seg``)
    """
    if not wav_path or not os.path.isfile(wav_path):
        return []
    file_dur = wav_duration(wav_path)
    evs = _validate_events(events, file_dur)
    if not evs:
        return []
    # Merge duplicates at the same offset (two starts at the same instant).
    merged: list[dict] = []
    for e in evs:
        if merged and abs(e["offset"] - merged[-1]["offset"]) < 0.05:
            continue
        merged.append(e)
    if len(merged) > MAX_SEGMENTS:
        merged = merged[:MAX_SEGMENTS]

    out_folder = out_folder or os.path.dirname(wav_path)
    os.makedirs(out_folder, exist_ok=True)
    exe = _ffmpeg_exe()
    if not exe:
        return []

    segments = []
    total = len(merged)
    for i, ev in enumerate(merged):
        start = ev["offset"]
        end = merged[i + 1]["offset"] if i + 1 < total else file_dur
        if end - start < MIN_SEGMENT_SEC:
            if progress:
                progress(i + 1, total)
            continue
        name = build_segment_name(i + 1, ev["artist"], ev["title"], ".wav")
        out_path = os.path.join(out_folder, name)
        cmd = [exe, "-y", "-v", "error",
               "-ss", f"{start:.3f}", "-i", wav_path,
               "-t", f"{end - start:.3f}", "-c", "copy", out_path]
        entry = {
            "path": out_path, "start": round(start, 3),
            "end": round(end, 3), "duration": round(end - start, 3),
            "title": ev["title"], "artist": ev["artist"],
            "kind": ev["kind"],
        }
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600)
            if r.returncode == 0 and os.path.isfile(out_path) \
                    and os.path.getsize(out_path) > 44:
                entry["bytes"] = os.path.getsize(out_path)
            else:
                entry["error"] = (r.stderr or b"").decode(
                    "utf-8", "replace").strip()[-200:] or "ffmpeg failed"
        except Exception as exc:                 # noqa: BLE001
            entry["error"] = str(exc)
        segments.append(entry)
        if progress:
            progress(i + 1, total)
    return segments
