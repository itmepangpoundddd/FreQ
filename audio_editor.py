#!/usr/bin/env python3
"""Undoable editing session over the native editor core.

`native_editor.Editor` owns the decoded PCM and does the sample work in C++;
this module adds what the UI needs on top of it: a bounded undo/redo history
that stores only the bytes an operation touched (a fade on a 4-second
selection keeps 4 seconds, not the whole track), selection helpers, WAV
export and the monitor preview wrapper.

Every edit is expressed in FRAMES, not seconds, so repeated operations never
drift by a rounding error. The UI converts at the edges.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

try:  # The .pyd ships with the app; a source checkout may not have built it.
    import native_editor as _native

    NATIVE_AVAILABLE = True
    NATIVE_ERROR = ""
except Exception as _err:  # pragma: no cover - exercised only without a build
    _native = None
    NATIVE_AVAILABLE = False
    NATIVE_ERROR = str(_err)


# Fade shapes, in the native module's numbering (Audacity's names).
FADE_CURVES = (
    ("Linear", 0),
    ("Logarithmic", 1),
    ("Exponential", 2),
    ("S-curve", 3),
)
FADE_CURVE_INDEX = {name: idx for name, idx in FADE_CURVES}


class EditSession:
    """A decoded track plus a bounded undo history."""

    MAX_UNDO = 40
    MAX_UNDO_BYTES = 96 * 1024 * 1024      # keep the editor lean on RAM

    def __init__(self, path: str) -> None:
        if not NATIVE_AVAILABLE:
            raise RuntimeError(
                "native_editor is not built - run build_native_meter.py")
        self.path = str(path)
        self._temp_dir: Optional[str] = None
        self._ed = _native.Editor(self._native_readable(self.path))
        self._undo: list = []
        self._redo: list = []
        self._undo_bytes = 0
        self.dirty = False
        self._clipboard_frames = 0

    def _native_readable(self, path: str) -> str:
        """A path the native decoder can open.

        miniaudio's narrow file API goes through the Windows ANSI code page,
        so a Thai (or any non-ANSI) name opens as mojibake and fails. Hand the
        decoder an ASCII hard link (or copy) instead; the operator's file is
        only read, so linking to it changes nothing on disk.
        """
        if path.isascii():
            return path
        folder = tempfile.mkdtemp(prefix="freq_edit_src_")
        ext = os.path.splitext(path)[1] or ".audio"
        target = os.path.join(folder, "source" + ext)
        try:
            os.link(path, target)          # same volume: free and instant
        except OSError:
            shutil.copy2(path, target)     # other volume / no link support
        self._temp_dir = folder
        return target

    # ── Read-only state ────────────────────────────────────────────
    @property
    def frames(self) -> int:
        return int(self._ed.frames)

    @property
    def sample_rate(self) -> int:
        return int(self._ed.sample_rate)

    @property
    def channels(self) -> int:
        return int(self._ed.channels)

    @property
    def duration(self) -> float:
        return float(self._ed.duration)

    @property
    def has_clipboard(self) -> bool:
        return self._clipboard_frames > 0

    def peaks(self, start: int, end: int, buckets: int) -> list:
        """Waveform envelope for a range: [min, max, min, max, ...]."""
        start = max(0, min(int(start), self.frames))
        end = max(start, min(int(end), self.frames))
        buckets = max(1, int(buckets))
        if end <= start:
            return [0.0] * (buckets * 2)
        return list(self._ed.peaks(start, end, buckets))

    def stats(self, start: int, end: int) -> dict:
        """Peak/RMS in dBFS for a range (never raises on an empty range)."""
        start = max(0, min(int(start), self.frames))
        end = max(start, min(int(end), self.frames))
        return dict(self._ed.stats(start, end))

    # ── Undo bookkeeping ───────────────────────────────────────────
    # An entry stores the action that UNDOES the edit; the action that redoes
    # it is captured from the track at the moment of the undo, so a paste or
    # a fade never has to be held in memory twice.
    def _frame_bytes(self) -> int:
        return max(1, self.channels) * 4           # float32 samples per frame

    def _run(self, action: tuple) -> None:
        kind, pos, payload = action
        if kind == "delete":
            self._ed.delete(pos, pos + int(payload))
        elif kind == "insert":
            self._ed.insert_data(pos, payload)
        elif kind == "set":
            self._ed.set_range(pos, payload)

    def _inverse(self, action: tuple) -> tuple:
        """The action that undoes ``action``, with its bytes read now."""
        kind, pos, payload = action
        if kind == "delete":
            frames = int(payload)
            return ("insert", pos, self._ed.get_range(pos, pos + frames))
        if kind == "insert":
            frames = len(payload) // self._frame_bytes()
            return ("delete", pos, frames)
        frames = len(payload) // self._frame_bytes()
        return ("set", pos, self._ed.get_range(pos, pos + frames))

    def _push(self, undo_action: tuple, label: str,
              redo_action: Optional[tuple] = None) -> None:
        entry = {"undo": undo_action, "label": label, "redo": redo_action}
        self._undo.append(entry)
        self._undo_bytes += self._entry_bytes(entry)
        self._redo.clear()
        while (len(self._undo) > self.MAX_UNDO
               or self._undo_bytes > self.MAX_UNDO_BYTES):
            dropped = self._undo.pop(0)
            self._undo_bytes -= self._entry_bytes(dropped)
        self.dirty = True

    @staticmethod
    def _entry_bytes(entry: dict) -> int:
        total = 0
        for key in ("undo", "redo"):
            action = entry.get(key)
            if action and isinstance(action[2], bytes):
                total += len(action[2])
        return total

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1]["label"] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1]["label"] if self._redo else ""

    def undo(self) -> bool:
        if not self._undo:
            return False
        entry = self._undo.pop()
        self._undo_bytes -= self._entry_bytes(entry)
        # Capture the redo bytes BEFORE the track changes.
        entry["redo"] = self._inverse(entry["undo"])
        self._run(entry["undo"])
        self._redo.append(entry)
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        entry = self._redo.pop()
        action = entry["redo"]
        # And the undo bytes before the redo lands.
        entry["undo"] = self._inverse(action)
        self._run(action)
        self._undo.append(entry)
        self._undo_bytes += self._entry_bytes(entry)
        self.dirty = True
        return True

    # ── Edits ──────────────────────────────────────────────────────
    def _clamp(self, start: int, end: int) -> tuple:
        total = self.frames
        s = max(0, min(int(start), total))
        e = max(0, min(int(end), total))
        if e < s:
            s, e = e, s
        return s, e

    def delete(self, start: int, end: int) -> int:
        """Remove a range. Returns the number of frames removed."""
        s, e = self._clamp(start, end)
        if e <= s:
            return 0
        data = self._ed.get_range(s, e)
        self._ed.delete(s, e)
        self._push(("insert", s, data), "delete")
        return e - s

    def trim_to(self, start: int, end: int) -> None:
        """Keep only the selection (Audacity's "Trim Audio")."""
        s, e = self._clamp(start, end)
        if e <= s:
            return
        total = self.frames
        if e < total:
            self.delete(e, total)        # tail first: head offsets stay valid
        if s > 0:
            self.delete(0, s)

    def copy(self, start: int, end: int) -> int:
        s, e = self._clamp(start, end)
        if e <= s:
            return 0
        self._ed.copy(s, e)
        self._clipboard_frames = e - s
        return e - s

    def cut(self, start: int, end: int) -> int:
        removed = self.copy(start, end)
        if removed:
            self.delete(start, end)
        return removed

    def paste(self, pos: int) -> int:
        if not self._clipboard_frames:
            return 0
        total = self.frames
        p = max(0, min(int(pos), total))
        frames = self._clipboard_frames
        self._ed.paste(p)
        self._push(("delete", p, frames), "paste")
        return frames

    def insert_silence(self, pos: int, frames: int) -> None:
        total = self.frames
        p = max(0, min(int(pos), total))
        n = max(0, int(frames))
        if n == 0:
            return
        self._ed.insert_silence(p, n)
        self._push(("delete", p, n), "silence")

    def _record_region(self, start: int, end: int) -> tuple:
        s, e = self._clamp(start, end)
        if e <= s:
            return 0, 0, b""
        return s, e, self._ed.get_range(s, e)

    def fade(self, start: int, end: int, curve: int = 0, fade_in: bool = True) -> None:
        s, e, before = self._record_region(start, end)
        if e - s < 2:
            return
        self._ed.fade(s, e, int(curve), 1 if fade_in else 0)
        self._push(("set", s, before), "fade")

    def gain(self, start: int, end: int, db: float) -> None:
        s, e, before = self._record_region(start, end)
        if e <= s:
            return
        self._ed.gain(s, e, float(db))
        self._push(("set", s, before), "gain")

    def normalize(self, start: int, end: int, target_db: float = -1.0) -> float:
        s, e, before = self._record_region(start, end)
        if e <= s:
            return 0.0
        applied = float(self._ed.normalize(s, e, float(target_db)))
        self._push(("set", s, before), "normalize")
        return applied

    def silence(self, start: int, end: int) -> None:
        s, e, before = self._record_region(start, end)
        if e <= s:
            return
        self._ed.silence(s, e)
        self._push(("set", s, before), "silence")

    def reverse(self, start: int, end: int) -> None:
        s, e, before = self._record_region(start, end)
        if e - s < 2:
            return
        self._ed.reverse(s, e)
        self._push(("set", s, before), "reverse")

    # ── Export ─────────────────────────────────────────────────────
    def save(self, path: str) -> None:
        """Write a 16-bit WAV. The source file is never touched.

        The native writer uses an ANSI fopen(), so a Thai file name would be
        written as mojibake that the queue can never find again. Non-ASCII
        targets are therefore written to a temporary ASCII name in the same
        folder and then renamed with os.replace (atomic, Unicode-safe).
        """
        target = os.path.abspath(str(path))
        folder = os.path.dirname(target)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if target.isascii():
            self._ed.save_wav(target)
        else:
            fd, temp_path = tempfile.mkstemp(suffix=".wav", dir=folder or None)
            os.close(fd)
            try:
                self._ed.save_wav(temp_path)
                os.replace(temp_path, target)
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
        self.dirty = False

    # ── Monitor preview (device is chosen by the caller) ───────────
    def preview(self, start: int, end: int, device_index: int = -1,
                volume: float = 0.8) -> bool:
        s, e = self._clamp(start, end)
        if e <= s:
            return False
        return bool(self._ed.preview_start(s, e, int(device_index), float(volume)))

    def stop(self) -> None:
        self._ed.preview_stop()

    @property
    def playing(self) -> bool:
        return bool(self._ed.playing)

    @property
    def position(self) -> int:
        """Frames played since the current preview started."""
        return int(self._ed.position_frames)

    def set_volume(self, value: float) -> None:
        self._ed.preview_set_volume(float(value))

    def close(self) -> None:
        try:
            self._ed.preview_stop()
        except Exception:
            pass
        self._ed = None
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


def list_monitor_devices() -> list:
    """[(display_name, index)] — index -1 means "follow the system default"."""
    if not NATIVE_AVAILABLE:
        return [("System default", -1)]
    out = [("System default (Windows)", -1)]
    try:
        for dev in _native.list_devices():
            name = str(dev.get("name") or f"Device {dev.get('index')}")
            out.append((name, int(dev.get("index", -1))))
    except Exception:
        pass
    return out


def format_time(seconds: float, millis: bool = True) -> str:
    """mm:ss.mmm (or hh:mm:ss.mmm) — the editor's time readouts."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:06.3f}" if millis else \
               f"{hours:d}:{minutes:02d}:{int(secs):02d}"
    return f"{minutes:02d}:{secs:06.3f}" if millis else f"{minutes:02d}:{int(secs):02d}"
