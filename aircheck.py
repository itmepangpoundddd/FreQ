"""Air-Check Recorder — record the master output and/or microphone to WAV
via the native WASAPI capture (native_audio_capture).

The recorder runs its own puller thread per source: it drains the capture
ring and appends PCM to a WAV file opened with the stdlib ``wave`` module.
A caller can poll ``levels()`` for a live meter or ``elapsed()`` for the
counter. After stopping, ``export_mp3()`` can transcode the WAV with the
bundled ffmpeg (encode only — never on the live path). Helpers
``list_files()`` and ``clean_old()`` manage the recordings folder.
"""
from __future__ import annotations

import glob
import os
import subprocess
import threading
import time
import wave
from datetime import datetime
from typing import Optional

_CAPTURE = None
try:
    import native_audio_capture as _CAPTURE
except ImportError:
    _CAPTURE = None

SOURCES = ("master", "mic", "both")


def _ffmpeg_exe() -> Optional[str]:
    """Locate ffmpeg (same order as the player's lookup)."""
    candidates = []
    here = os.path.dirname(os.path.abspath(__file__))
    # bundled copy next to the app (installer layout)
    for base in (os.path.dirname(here), here, os.getcwd()):
        candidates.append(os.path.join(base, "ffmpeg", "ffmpeg.exe"))
        candidates.append(os.path.join(base, "deps", "ffmpeg-essentials",
                                       "bin", "ffmpeg.exe"))
    from shutil import which
    exe = which("ffmpeg")
    if exe:
        candidates.append(exe)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


class _SourceWriter:
    """One capture endpoint -> one WAV file (a puller thread + writer)."""

    def __init__(self, path: str, loopback: bool, endpoint: Optional[str]) -> None:
        self.path = path
        if endpoint:
            self.cap = _CAPTURE.Capture(loopback=loopback, endpoint=endpoint)
        else:
            self.cap = _CAPTURE.Capture(loopback=loopback)
        self.cap.start()
        self.cap.clear()
        self.wav = wave.open(path, "wb")
        self.wav.setnchannels(self.cap.channels)
        self.wav.setsampwidth(2)
        self.wav.setframerate(self.cap.samplerate)
        self.stop_ev = threading.Event()
        self.error: Optional[str] = None
        self.thread = threading.Thread(target=self._pull, daemon=True)
        self.thread.start()

    def _pull(self) -> None:
        rate, ch = self.cap.samplerate, self.cap.channels
        chunk = max(4096, rate * ch * 2 // 4)   # ~0.25 s
        while not self.stop_ev.is_set():
            try:
                data = self.cap.read(chunk)
            except Exception as e:
                self.error = str(e)
                break
            if data:
                try:
                    self.wav.writeframes(data)
                except Exception as e:
                    self.error = str(e)
                    break
            else:
                time.sleep(0.05)
        try:
            self.cap.stop()
        except Exception:
            pass
        try:
            self.wav.close()
        except Exception:
            pass

    def stop(self) -> None:
        self.stop_ev.set()
        self.thread.join(timeout=5)


class AirCheckRecorder:
    """Records the master output and/or microphone to WAV file(s)."""

    def __init__(self) -> None:
        self._writers: list[_SourceWriter] = []
        self._paths: list[str] = []
        self._master_cap = None      # kept for levels()/elapsed of primary
        self._error: Optional[str] = None
        self._started_at = 0.0
        self._source = "master"

    # -- state ----------------------------------------------------------
    @property
    def available(self) -> bool:
        return _CAPTURE is not None

    @property
    def recording(self) -> bool:
        return bool(self._writers)

    @property
    def paths(self) -> list:
        return list(self._paths)

    @property
    def path(self) -> Optional[str]:      # primary (master) file, compat
        return self._paths[0] if self._paths else None

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def source(self) -> str:
        return self._source

    def elapsed(self) -> float:
        if not self.recording:
            return 0.0
        return max(0.0, time.time() - self._started_at)

    def levels(self):
        """(l, r) linear peaks of the primary source being recorded."""
        if self._master_cap is None:
            return (0.0, 0.0)
        try:
            return self._master_cap.levels()
        except Exception:
            return (0.0, 0.0)

    # -- control ----------------------------------------------------------
    def start(self, folder: Optional[str] = None,
              name: Optional[str] = None,
              source: str = "master") -> str:
        """Start recording; returns the primary file path."""
        if self.recording:
            raise RuntimeError("already recording")
        if _CAPTURE is None:
            raise RuntimeError("native_audio_capture module not available")
        if source not in SOURCES:
            source = "master"
        self._error = None
        self._source = source

        base = name or f"aircheck_{datetime.now():%Y%m%d_%H%M}"
        stem, ext = os.path.splitext(base)
        if ext and ext.lower() != ".wav":
            base = stem + ".wav"
        elif not ext:
            base = stem + ".wav"

        def path_for(tag: str) -> str:
            # Suffix only the secondary file of a dual recording; a solo
            # mic recording keeps the clean base name.
            if source == "both" and tag != "master":
                fname = f"{stem}_{tag}.wav"
            else:
                fname = base
            p = os.path.join(folder, fname) if folder else fname
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            return p

        want_master = source in ("master", "both")
        want_mic = source in ("mic", "both")
        if not want_master and not want_mic:
            raise ValueError(f"unknown source: {source}")

        try:
            if want_master:
                w = _SourceWriter(path_for("master"), loopback=True,
                                  endpoint=None)
                self._writers.append(w)
                self._master_cap = w.cap
            if want_mic:
                w = _SourceWriter(path_for("mic"), loopback=False,
                                  endpoint=None)   # default input device
                self._writers.append(w)
                if self._master_cap is None:
                    self._master_cap = w.cap
        except Exception:
            # roll back partial starts
            for w in self._writers:
                try:
                    w.stop()
                except Exception:
                    pass
            self._writers = []
            self._master_cap = None
            raise

        self._paths = [w.path for w in self._writers]
        self._started_at = time.time()
        return self._paths[0]

    def stop(self) -> Optional[str]:
        """Stop recording and finalize the WAV file(s). Returns primary."""
        if not self.recording:
            return None
        errors = []
        for w in self._writers:
            w.stop()
            if w.error:
                errors.append(w.error)
        if errors:
            self._error = "; ".join(errors)
        self._writers = []
        self._master_cap = None
        primary = self._paths[0] if self._paths else None
        self._paths = []
        return primary

    # -- MP3 export (offline, after stop) -----------------------------------
    @staticmethod
    def export_mp3(wav_path: str, quality: str = "2",
                   delete_wav: bool = False) -> Optional[str]:
        """Transcode a finished WAV to MP3 with ffmpeg. Returns the MP3
        path or None when ffmpeg is unavailable/encoding failed."""
        exe = _ffmpeg_exe()
        if not exe or not os.path.isfile(wav_path):
            return None
        mp3 = os.path.splitext(wav_path)[0] + ".mp3"
        try:
            r = subprocess.run(
                [exe, "-y", "-v", "error", "-i", wav_path,
                 "-codec:a", "libmp3lame", "-q:a", quality, mp3],
                capture_output=True, timeout=600,
            )
        except Exception:
            return None
        if r.returncode != 0 or not os.path.isfile(mp3):
            return None
        if delete_wav:
            try:
                os.remove(wav_path)
            except OSError:
                pass
        return mp3

    # -- recordings folder helpers -------------------------------------------
    @staticmethod
    def list_files(folder: str) -> list:
        """All air-check recordings in ``folder``, newest first:
        [(path, size_bytes, mtime_epoch)]."""
        if not folder or not os.path.isdir(folder):
            return []
        out = []
        for pat in ("aircheck_*.wav", "aircheck_*.mp3"):
            for p in glob.glob(os.path.join(folder, pat)):
                try:
                    st = os.stat(p)
                    out.append((p, st.st_size, st.st_mtime))
                except OSError:
                    pass
        out.sort(key=lambda t: t[2], reverse=True)
        return out

    @staticmethod
    def clean_old(folder: str, keep_days: int = 14) -> int:
        """Delete recordings older than ``keep_days``. Returns count."""
        cutoff = time.time() - keep_days * 86400
        removed = 0
        for p, _size, mtime in AirCheckRecorder.list_files(folder):
            if mtime < cutoff:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
        return removed


class AirCheckScheduler:
    """Time-of-day schedule table: e.g. every day 18:00-20:00.

    Pure bookkeeping (list of entries + .freq serialization); the GUI
    timer decides when to start/stop a recording.
    """

    def __init__(self) -> None:
        self.entries: list[dict] = []   # {"days": "daily"|"1234560", "start": "18:00", "end": "20:00", "folder": str}

    def add(self, start: str, end: str, days: str = "daily",
            folder: str = "") -> dict:
        e = {"days": days, "start": start, "end": end, "folder": folder}
        self.entries.append(e)
        return e

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self.entries.pop(index)

    def to_data(self) -> list:
        return [dict(e) for e in self.entries]

    def restore(self, data) -> None:
        self.entries = []
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("start") and e.get("end"):
                    self.entries.append({
                        "days": e.get("days", "daily"),
                        "start": str(e["start"]),
                        "end": str(e["end"]),
                        "folder": e.get("folder", ""),
                    })

    def due_window(self, now: Optional[datetime] = None):
        """Return the entry whose window contains ``now``, else None."""
        now = now or datetime.now()
        day = str(now.isoweekday())           # Mon=1..Sun=7
        hm = f"{now:%H:%M}"
        for e in self.entries:
            days = e["days"]
            if days != "daily" and day not in days:
                continue
            if e["start"] <= hm < e["end"]:
                return e
        return None
