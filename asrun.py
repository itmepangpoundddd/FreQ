"""As-run log — what actually went to air, in an append-only file.

The queue, the air-check recording and the application log all answer part
of the question "what did we broadcast?", but none of them can be handed to
an inspector: the queue holds intentions, the recording holds sound with no
index, and the log is in memory. This module is the missing evidence:
one JSONL file per day, written forward only, each line chained to the one
before it by SHA-1 so a later edit is visible.

Layout (default folder ``logs/asrun``):

    logs/asrun/2026-10-20.jsonl            one line per write, append-only
    logs/asrun/2026-10-20.manifest.json    daily seal: counts + file hash

Each aired item is written **twice** — a ``start`` line when it reaches the
output and an ``end`` line when it stops. A power cut mid-track therefore
still leaves the start line behind (``ended_why`` absent, item shows as
still open), which a single end-of-item write could never do.

The module never raises on the playback path: a failed write is recorded in
``last_error`` and reported to the UI, because losing the log is bad and
losing the broadcast is worse.

Nothing here knows about Tk or the audio engine — the GUI calls ``begin``
and ``end``; tests drive the same public API.

Command line (run from the folder that holds ``logs/asrun``):

    python asrun.py --verify-all          verify every day in the folder
    python asrun.py --seal-all            seal every day that needs it
    python asrun.py --seal 2026-10-18     seal one day
    python asrun.py --list                day by day summary
    python asrun.py --export 2026-10-18   CSV for one day
    python asrun.py --prune [--keep-days N] [--force]

Exit codes: 0 clean · 1 evidence broken or missing · 3 maintenance due
(stale seal / unsealed day) · 2 the command itself was wrong.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sys
import threading
import time
from datetime import date as _date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

__all__ = ["AsRunLog", "SCHEMA", "KINDS", "END_REASONS",
           "LEGAL_MIN_DAYS", "DEFAULT_KEEP_DAYS", "END_REASON_LABELS",
           "EXIT_OK", "EXIT_PROBLEM", "EXIT_ATTENTION", "EXIT_USAGE",
           "main"]

SCHEMA = "freq.asrun/1"
MANIFEST_VERSION = 2

# Exit codes of the command line — a monitoring contract, not a detail:
#   0  every day is clean
#   1  evidence is broken or missing (tampered chain, rewritten file,
#      deleted log, unsealed day unless --allow-unsealed)
#   3  nothing is broken, but maintenance is due (stale seal / unsealed day
#      with --allow-unsealed) — fix with --seal-all
#   2  the command itself was wrong (bad date, retention under the floor)
EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_ATTENTION = 3
EXIT_USAGE = 2

# What kind of thing went to air. `stream` is the outgoing Icecast/Shoutcast
# feed itself (an event of the broadcast, not a track) and `relay` an
# incoming stream played back.
KINDS = ("music", "mic", "jingle", "ad", "anthem", "stream", "relay")

# Why an item stopped. Kept to a fixed vocabulary so reports can count them.
END_REASONS = ("natural", "skipped", "stopped", "interrupted", "anthem-cut",
               "jingle-cut", "missing", "error", "superseded")

END_REASON_LABELS = {
    "natural": "ended",
    "skipped": "skipped",
    "stopped": "stopped by operator",
    "interrupted": "interrupted",
    "anthem-cut": "cut by anthem",
    "jingle-cut": "cut by jingle",
    "missing": "file missing",
    "error": "error",
    "superseded": "superseded",
    # Synthesised when reading, never written: a start row whose end never
    # arrived (the app was killed, or a second copy was running) is shown as
    # closed instead of pretending to be on air forever.
    "unclosed": "unclosed (no end recorded)",
}

# The National Broadcasting Act requires the as-run record to be kept long
# enough to be inspected; the station's own licence can demand more, so the
# retention is a setting — 30 days is the floor this system will not go
# under without an explicit force.
LEGAL_MIN_DAYS = 30
DEFAULT_KEEP_DAYS = 400

_GENESIS_HASH = "0" * 40


def _canonical(payload: dict) -> str:
    """Deterministic JSON for hashing (stable key order, no ASCII escaping)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


def _clean_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


class AsRunLog:
    """Append-only, hash-chained record of what went to air."""

    def __init__(self, folder: Optional[str] = None, *,
                 enabled: bool = False, keep_days: int = DEFAULT_KEEP_DAYS,
                 app_version: str = "", logger: Optional[logging.Logger] = None,
                 durable: bool = True) -> None:
        self.folder = Path(folder) if folder else Path("logs") / "asrun"
        self.enabled = bool(enabled)
        self.keep_days = self._clamp_keep(keep_days)
        self.app_version = _clean_text(app_version, 40)
        self.durable = bool(durable)
        self.log = logger or logging.getLogger("freq.asrun")
        self.last_error = ""
        self._lock = threading.RLock()
        # date -> [next seq, last hash]
        self._state: dict[str, tuple[int, str]] = {}
        self._open: dict[str, dict] = {}
        self._sealed: set[str] = set()

    # ── configuration ───────────────────────────────────────────────

    @staticmethod
    def _clamp_keep(keep_days: int) -> int:
        try:
            n = int(keep_days)
        except (TypeError, ValueError):
            return DEFAULT_KEEP_DAYS
        return max(LEGAL_MIN_DAYS, n)

    def configure(self, *, enabled: Optional[bool] = None,
                  folder: Optional[str] = None,
                  keep_days: Optional[int] = None) -> None:
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
            if folder:
                new_folder = Path(folder)
                if new_folder != self.folder:
                    self.folder = new_folder
                    self._state.clear()
            if keep_days is not None:
                self.keep_days = self._clamp_keep(keep_days)

    # ── paths ───────────────────────────────────────────────────────

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d")

    @staticmethod
    def _normalize_date(day: Optional[str]) -> str:
        if not day:
            return AsRunLog._today()
        text = str(day).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"invalid as-run date: {day!r}")

    def day_file(self, day: Optional[str] = None) -> Path:
        return self.folder / f"{self._normalize_date(day)}.jsonl"

    def manifest_file(self, day: Optional[str] = None) -> Path:
        return self.folder / f"{self._normalize_date(day)}.manifest.json"

    def dates(self) -> list[str]:
        """Every day that has a log file, oldest first."""
        try:
            return sorted(p.stem for p in self.folder.glob("*.jsonl"))
        except OSError:
            return []

    # ── writing ─────────────────────────────────────────────────────

    def _state_for(self, day: str) -> tuple[int, str]:
        """(next seq, last hash) for a day — read once, then kept in memory."""
        if day in self._state:
            return self._state[day]
        seq, last = 1, _GENESIS_HASH
        path = self.day_file(day)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            # A half-written tail is evidence too: stop here,
                            # the chain restart marker below keeps verify loud.
                            break
                        if isinstance(row.get("seq"), int):
                            seq = row["seq"] + 1
                        if isinstance(row.get("hash"), str) and row["hash"]:
                            last = row["hash"]
            except OSError as err:
                self._fail(f"cannot read {path.name}: {err}")
        self._state[day] = (seq, last)
        return seq, last

    def _fail(self, message: str) -> None:
        self.last_error = message
        try:
            self.log.warning("as-run: %s", message)
        except Exception:
            pass

    def _write_row(self, day: str, payload: dict) -> Optional[dict]:
        """Chain + append one row. Never raises; returns the stored row."""
        with self._lock:
            seq, prev = self._state_for(day)
            row = dict(payload)
            row["schema"] = SCHEMA
            row["date"] = day
            row["seq"] = seq
            row["prev_hash"] = prev
            row["hash"] = _sha1(_canonical(row))
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                with open(self.day_file(day), "a", encoding="utf-8",
                          newline="\n") as fh:
                    fh.write(_canonical(row) + "\n")
                    fh.flush()
                    if self.durable:
                        os.fsync(fh.fileno())
            except OSError as err:
                self._fail(f"cannot append to {self.day_file(day).name}: {err}")
                return None
            self._state[day] = (seq + 1, row["hash"])
            self.last_error = ""
            return row

    # ── playback-facing API ─────────────────────────────────────────

    @property
    def active(self) -> Optional[dict]:
        """The currently open item, or None."""
        with self._lock:
            for entry in self._open.values():
                return dict(entry)
            return None

    @property
    def active_id(self) -> Optional[str]:
        with self._lock:
            for item_id in self._open:
                return item_id
            return None

    def begin(self, kind: str, *, title: str = "", artist: str = "",
              song_id: str = "", duration: Any = None,
              source_type: str = "", path: str = "", url: str = "",
              channel: str = "", device: str = "",
              planned: Optional[dict] = None, plan_revision: Any = None,
              reason: str = "", recording: Optional[dict] = None,
              extra: Optional[dict] = None) -> Optional[str]:
        """Record an item reaching the output. Returns its id (or None).

        Any item already open is closed first as ``superseded`` — the caller
        that forgot to end it must not corrupt the pairing.
        """
        if not self.enabled:
            return None
        if kind not in KINDS:
            self._fail(f"unknown as-run kind: {kind!r}")
            return None
        self.end_active("superseded")
        now = datetime.now().astimezone()
        day = now.strftime("%Y-%m-%d")
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        wall_ms = int((now - midnight).total_seconds() * 1000)
        item = {
            "song_id": _clean_text(song_id, 80),
            "title": _clean_text(title),
            "artist": _clean_text(artist),
            "duration": _clean_number(duration),
        }
        if extra:
            for key, value in extra.items():
                if key not in item:
                    item[_clean_text(key, 40)] = value
        payload = {
            "event": "start",
            "kind": kind,
            "ts_start": now.isoformat(timespec="milliseconds"),
            "wall_offset_ms": wall_ms,
            "item": item,
            "source": {
                "type": _clean_text(source_type or ("file" if path else ""), 20),
                "path": _clean_text(path, 400),
                "url": _clean_text(url, 400),
            },
            "channel": _clean_text(channel or kind, 20),
            "device": _clean_text(device, 120),
            "planned": planned or {},
            "plan_revision": plan_revision,
            "reason": _clean_text(reason, 400),
            "recording": recording or None,
            "app": {"name": "FreQ", "version": self.app_version},
        }
        row = self._write_row(day, payload)
        if row is None:
            return None
        item_id = f"{day}#{row['seq']}"
        with self._lock:
            self._open[item_id] = {
                "id": item_id, "date": day, "kind": kind,
                "title": item["title"], "artist": item["artist"],
                "channel": payload["channel"],
                "monotonic": time.monotonic(),
                "started": now.isoformat(timespec="milliseconds"),
            }
        # Auto-seal: the first write of a new day seals the day before it,
        # so a station that never closes the app still gets its manifests.
        self.seal_stale(today=day)
        return item_id

    def end(self, item_id: Optional[str], ended_why: str = "natural",
            *, actual_duration: Any = None) -> bool:
        """Close one open item. Returns True when an ``end`` row was written."""
        if not item_id:
            return False
        with self._lock:
            entry = self._open.pop(item_id, None)
        if entry is None:
            return False
        reason = ended_why if ended_why in END_REASONS else "stopped"
        elapsed = _clean_number(actual_duration)
        if elapsed is None:
            elapsed = round(max(0.0, time.monotonic() - entry["monotonic"]), 3)
        now = datetime.now().astimezone()
        payload = {
            "event": "end",
            "kind": entry["kind"],
            "item_id": item_id,
            "ts_end": now.isoformat(timespec="milliseconds"),
            "actual_duration": elapsed,
            "ended_why": reason,
            "item": {"title": entry["title"], "artist": entry["artist"]},
        }
        # The end row goes in the file of the day the item STARTED: an item
        # that crosses midnight belongs to the day it aired from.
        return self._write_row(entry["date"], payload) is not None

    def end_active(self, ended_why: str = "natural",
                   *, kind: Optional[str] = None) -> bool:
        """Close the open item (optionally only when it is of ``kind``)."""
        with self._lock:
            item_id = None
            for candidate, entry in self._open.items():
                if kind is None or entry["kind"] == kind:
                    item_id = candidate
                    break
        if item_id is None:
            return False
        return self.end(item_id, ended_why)

    # ── reading / reporting ─────────────────────────────────────────

    def _rows(self, day: str) -> list[dict]:
        path = self.day_file(day)
        rows: list[dict] = []
        if not path.exists():
            return rows
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def read_day(self, day: Optional[str] = None) -> list[dict]:
        """Items of a day, start rows merged with their end row, oldest first.

        Unparsable lines are skipped (verify() is the place that reports
        them) so the table keeps drawing the rest of the day.
        """
        day = self._normalize_date(day)
        try:
            rows = self._rows(day)
        except (OSError, ValueError) as err:
            self._fail(f"cannot read day {day}: {err}")
            return []
        items: list[dict] = []
        by_id: dict[str, dict] = {}
        for row in rows:
            if row.get("event") == "start":
                item_id = f"{row.get('date')}#{row.get('seq')}"
                entry = {
                    "id": item_id, "seq": row.get("seq"),
                    "kind": row.get("kind", ""),
                    "title": (row.get("item") or {}).get("title", ""),
                    "artist": (row.get("item") or {}).get("artist", ""),
                    "song_id": (row.get("item") or {}).get("song_id", ""),
                    "planned_duration": (row.get("item") or {}).get("duration"),
                    "ts_start": row.get("ts_start", ""),
                    "ts_end": "",
                    "actual_duration": None,
                    "ended_why": "",
                    "reason": row.get("reason", ""),
                    "plan_revision": row.get("plan_revision"),
                    "source": row.get("source") or {},
                    "channel": row.get("channel", ""),
                    "recording": row.get("recording"),
                    "open": True,
                }
                items.append(entry)
                by_id[item_id] = entry
            elif row.get("event") == "end":
                entry = by_id.get(row.get("item_id") or "")
                if entry is None:
                    # An end with no start in this file (start survived in a
                    # deleted file) — keep it visible rather than dropping it.
                    entry = {
                        "id": row.get("item_id") or f"orphan-{row.get('seq')}",
                        "seq": row.get("seq"), "kind": row.get("kind", ""),
                        "title": (row.get("item") or {}).get("title", ""),
                        "artist": (row.get("item") or {}).get("artist", ""),
                        "song_id": "", "planned_duration": None,
                        "ts_start": "", "ts_end": "", "actual_duration": None,
                        "ended_why": "", "reason": "", "plan_revision": None,
                        "source": {}, "channel": "", "recording": None,
                        "open": False, "orphan": True,
                    }
                    items.append(entry)
                    by_id[entry["id"]] = entry
                entry["ts_end"] = row.get("ts_end", "")
                entry["actual_duration"] = row.get("actual_duration")
                entry["ended_why"] = row.get("ended_why", "")
                entry["open"] = False
        # Only the NEWEST unclosed item can still be on air. A crash (or two
        # copies of the app writing the same folder) leaves older starts with
        # no end row; showing them all as "on air now" is a lie, so they are
        # read back as closed-but-unrecorded and named as such.
        unclosed = [i for i in items if i["open"]]
        for stale in unclosed[:-1]:
            stale["open"] = False
            stale["stale"] = True
            stale["ended_why"] = "unclosed"
        return items

    def stats(self, day: Optional[str] = None) -> dict:
        """Counts and totals for a day — used by the UI and the manifest."""
        day = self._normalize_date(day)
        items = self.read_day(day)
        by_kind: dict[str, int] = {}
        aired = 0.0
        for item in items:
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
            if item["actual_duration"]:
                aired += float(item["actual_duration"])
        path = self.day_file(day)
        return {
            "date": day,
            "items": len(items),
            "by_kind": by_kind,
            "open": sum(1 for i in items if i["open"]),
            "aired_seconds": round(aired, 3),
            "lines": self._count_lines(path),
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else 0,
        }

    @staticmethod
    def _count_lines(path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

    def verify(self, day: Optional[str] = None, *,
               check_manifest: bool = True,
               today: Optional[str] = None) -> dict:
        """Walk the chain of a day and report every break it finds.

        Chain checks, in order: JSON parses, ``seq`` counts up from 1 with no
        gap, ``prev_hash`` links to the row before it, and the row's own hash
        matches its content.

        Manifest checks: the sealed SHA-256 must match the file, or — when
        the day legitimately grew after sealing — the sealed PREFIX must be
        provably untouched. That prefix proof is what stops a rebuilt file:
        the chain is hash-linked from genesis, so reproducing the sealed tail
        hash at the sealed line count means reproducing the sealed rows.

        ``state`` is the one-word answer:
          clean     — chain intact and matching its manifest (or no manifest
                      for a day that is still airing)
          stale     — sealed, then extended; seal it again
          unsealed  — an earlier day with no manifest: nothing to compare to
          tampered  — chain broken, file rewritten/truncated after sealing,
                      or manifest unreadable
          missing   — no log file for that day at all

        ``ok`` is about the CHAIN and the sealed evidence only; whether an
        unsealed day blocks a run is decided by ``verify_all``.
        """
        day = self._normalize_date(day)
        ref_today = self._normalize_date(today) if today else self._today()
        path = self.day_file(day)
        result: dict[str, Any] = {
            "date": day, "exists": path.exists(), "lines": 0,
            "items": 0, "ok": True, "errors": [], "manifest": "missing",
            "state": "clean", "manifest_detail": None,
            "head_seq": None, "tail_hash": None, "prefix_ok": None,
            "row_hashes": [],
        }
        if not path.exists():
            result["state"] = "missing"
            return result
        prev = _GENESIS_HASH
        expected_seq = 1
        hashes: list[str] = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for number, raw in enumerate(fh, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    result["lines"] += 1
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        result["errors"].append(
                            f"line {number}: unreadable JSON (damaged file)")
                        result["ok"] = False
                        # Count the damaged row as consumed so the rows after
                        # it are not ALSO reported as seq mismatches.
                        expected_seq += 1
                        continue
                    stored = row.pop("hash", None)
                    if result["head_seq"] is None:
                        result["head_seq"] = row.get("seq")
                    if row.get("seq") != expected_seq:
                        result["errors"].append(
                            f"line {number}: seq {row.get('seq')} "
                            f"(expected {expected_seq})")
                        result["ok"] = False
                    if row.get("prev_hash") != prev:
                        result["errors"].append(
                            f"line {number}: prev_hash does not link to the line before")
                        result["ok"] = False
                    if stored != _sha1(_canonical(row)):
                        result["errors"].append(
                            f"line {number}: hash does not match the content "
                            "(line edited)")
                        result["ok"] = False
                    if row.get("event") == "start":
                        result["items"] += 1
                    if isinstance(stored, str) and stored:
                        prev = stored
                        hashes.append(stored)
                    expected_seq += 1
        except OSError as err:
            result["errors"].append(f"cannot read the file: {err}")
            result["ok"] = False
            result["state"] = "tampered"
            return result
        result["row_hashes"] = hashes
        if hashes:
            result["tail_hash"] = hashes[-1]

        if not check_manifest:
            result["state"] = "clean" if result["ok"] else "tampered"
            return result

        manifest_path = self.manifest_file(day)
        if not manifest_path.exists():
            result["manifest"] = "missing"
            if result["ok"] and day < ref_today:
                result["state"] = "unsealed"
            else:
                result["state"] = "clean" if result["ok"] else "tampered"
            return result
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            result["manifest"] = "unreadable"
            result["manifest_detail"] = {"error": str(err)}
            result["errors"].append(f"cannot read the manifest: {err}")
            result["ok"] = False
            result["state"] = "tampered"
            return result

        sealed_lines = int(data.get("lines") or 0)
        sealed_tail = data.get("tail_hash")
        result["manifest_detail"] = {
            "lines": sealed_lines,
            "items": data.get("items"),
            "file_sha256": data.get("file_sha256"),
            "tail_hash": sealed_tail,
            "generated_at": data.get("generated_at"),
            "reseal_count": data.get("reseal_count"),
            "manifest_version": data.get("manifest_version", 1),
        }
        if data.get("file_sha256") == _sha256_file(path):
            result["manifest"] = "ok"
        elif result["lines"] > sealed_lines > 0:
            # The day grew after sealing. Allowed ONLY when the sealed
            # prefix is provably untouched: the hash at the sealed line
            # count must be exactly the one the manifest recorded.
            if isinstance(sealed_tail, str) and sealed_tail:
                if sealed_lines <= len(hashes):
                    intact: Optional[bool] = hashes[sealed_lines - 1] == sealed_tail
                else:
                    intact = False
            else:
                intact = None      # manifest v1: cannot prove the prefix
            result["prefix_ok"] = intact
            if intact is False:
                result["manifest"] = "mismatch"
                result["errors"].append(
                    f"the sealed prefix was rewritten: the hash at line "
                    f"{sealed_lines} is not the one that was sealed")
                result["ok"] = False
                result["state"] = "tampered"
                return result
            result["manifest"] = "stale"
        else:
            result["manifest"] = "mismatch"
            why = ("the file was truncated after sealing"
                   if result["lines"] < sealed_lines
                   else "the file was rewritten after sealing (sha256 differs)")
            result["errors"].append(
                f"manifest: {sealed_lines} lines / "
                f"{str(data.get('file_sha256'))[:12]}… "
                f"but the file has {result['lines']} lines — {why}")
            result["ok"] = False

        if not result["ok"]:
            result["state"] = "tampered"
        elif result["manifest"] == "stale":
            result["state"] = "stale"
        else:
            result["state"] = "clean"
        return result

    # ── manifest ────────────────────────────────────────────────────

    def write_manifest(self, day: Optional[str] = None, *,
                       force: bool = False) -> Optional[dict]:
        """Seal a day: counts, chain tail, file size/hash — written atomically.

        Sealing must never be a way to launder a modified file. When a
        manifest already exists and the file no longer matches it:

        * the file only GREW and the sealed prefix is provably intact
          (the chain hash at the sealed line count is the recorded
          ``tail_hash``) → re-seal, carrying ``previous_file_sha256``,
          ``previous_lines`` and ``reseal_count`` as the audit trail
        * the chain is broken, the file shrank, or the prefix changed →
          **refuse** (``last_error`` explains) so the old manifest survives
          as the record of what was sealed
        * ``force=True`` — an explicit operator decision — overrides the
          refusal; the superseded hashes are kept in the new manifest

        Returns the manifest dict (``unchanged: True`` when it was already
        current) or None when nothing could be sealed.
        """
        day = self._normalize_date(day)
        path = self.day_file(day)
        if not path.exists():
            return None
        existing: Optional[dict] = None
        manifest_path = self.manifest_file(day)
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as err:
                self._fail(f"{day}: the existing manifest cannot be read ({err})")
                if not force:
                    return None
        verify = self.verify(day, check_manifest=False)
        if not verify["ok"] and not force:
            self._fail(f"{day}: the hash chain is broken — refusing to seal "
                       "over it (the old evidence is kept; "
                       "check with --verify-all)")
            return None
        if existing and not force:
            if existing.get("file_sha256") == _sha256_file(path):
                out = dict(existing)
                out["unchanged"] = True
                return out
            sealed_lines = int(existing.get("lines") or 0)
            if verify["lines"] < sealed_lines:
                self._fail(f"{day}: the file is shorter than the seal "
                           f"({verify['lines']} < {sealed_lines} lines) — "
                           "refusing (use --force if that is intended)")
                return None
            sealed_tail = existing.get("tail_hash")
            row_hashes = verify.get("row_hashes") or []
            if sealed_tail and sealed_lines <= len(row_hashes):
                if row_hashes[sealed_lines - 1] != sealed_tail:
                    self._fail(f"{day}: the sealed prefix was rewritten — "
                               "refusing to seal over it")
                    return None
        stats = self.stats(day)
        manifest = {
            "schema": SCHEMA,
            "manifest_version": MANIFEST_VERSION,
            "kind": "asrun-daily-manifest",
            "date": day,
            "generated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "app": {"name": "FreQ", "version": self.app_version},
            "items": stats["items"],
            "by_kind": stats["by_kind"],
            "open_items": stats["open"],
            "aired_seconds": stats["aired_seconds"],
            "lines": stats["lines"],
            "head_seq": verify.get("head_seq"),
            "tail_hash": verify.get("tail_hash"),
            "file": path.name,
            "file_bytes": stats["bytes"],
            "file_sha256": _sha256_file(path),
            "chain_ok": verify["ok"],
            "chain_errors": verify["errors"],
            "keep_days": self.keep_days,
        }
        if existing and existing.get("file_sha256") != manifest["file_sha256"]:
            manifest["previous_file_sha256"] = existing.get("file_sha256")
            manifest["previous_lines"] = existing.get("lines")
            manifest["reseal_count"] = int(existing.get("reseal_count") or 0) + 1
        manifest["manifest_hash"] = _sha1(_canonical(manifest))
        target = self.manifest_file(day)
        tmp = target.with_suffix(".json.tmp")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(manifest, ensure_ascii=False,
                                      indent=2, sort_keys=True),
                           encoding="utf-8")
            os.replace(tmp, target)
        except OSError as err:
            self._fail(f"cannot write manifest {target.name}: {err}")
            return None
        with self._lock:
            self._sealed.add(day)
        return manifest

    def seal_stale(self, *, today: Optional[str] = None,
                   window_days: int = 14,
                   force_rescan: bool = False) -> list[str]:
        """Auto-seal: every earlier day that has no manifest yet.

        Called by the app on the first write of a new day (so a station that
        runs 24/7 seals yesterday by itself) and by the GUI poller. Bounded
        to the last ``window_days`` on purpose: a folder holding years of
        logs must never stall a track change. The once-per-day guard keeps
        the repeated GUI call free — ``force_rescan`` bypasses it for the
        command line.
        """
        ref = self._normalize_date(today) if today else self._today()
        with self._lock:
            if not force_rescan and ref in self._sealed:
                return []
            self._sealed.add(ref)
        cutoff = datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=window_days)
        sealed: list[str] = []
        for day in self.dates():
            if day >= ref:
                continue
            try:
                if datetime.strptime(day, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                continue
            if self.manifest_file(day).exists():
                continue
            if self.write_manifest(day) is not None:
                sealed.append(day)
        return sealed

    def gaps(self, *, today: Optional[str] = None,
             window_days: int = 60) -> list[str]:
        """Earlier days with no manifest (newest first) — the evidence gaps."""
        ref = self._normalize_date(today) if today else self._today()
        cutoff = datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=window_days)
        out: list[str] = []
        for day in self.dates():
            if day >= ref:
                continue
            try:
                if datetime.strptime(day, "%Y-%m-%d") < cutoff:
                    continue
            except ValueError:
                continue
            if not self.manifest_file(day).exists():
                out.append(day)
        return sorted(out, reverse=True)

    def seal_all(self, *, today: Optional[str] = None, window_days: int = 400,
                 force: bool = False, include_today: bool = False) -> dict:
        """Seal every day that needs it. Returns what happened, day by day.

        A day whose chain is broken (or that was truncated after sealing) is
        REPORTED as refused and left alone — ``--seal-all`` is maintenance,
        not a way to overwrite the evidence of tampering.
        """
        ref = self._normalize_date(today) if today else self._today()
        cutoff = datetime.strptime(ref, "%Y-%m-%d") - timedelta(days=window_days)
        report: dict[str, Any] = {"folder": str(self.folder), "today": ref,
                                  "sealed": [], "unchanged": [],
                                  "refused": [], "skipped": []}
        for day in self.dates():
            if day == ref and not include_today:
                report["skipped"].append(day)
                continue
            try:
                if datetime.strptime(day, "%Y-%m-%d") < cutoff:
                    report["skipped"].append(day)
                    continue
            except ValueError:
                report["skipped"].append(day)
                continue
            manifest = self.write_manifest(day, force=force)
            if manifest is None:
                report["refused"].append({"day": day, "reason": self.last_error})
            elif manifest.get("unchanged"):
                report["unchanged"].append(day)
            else:
                report["sealed"].append(day)
        return report

    def verify_all(self, *, today: Optional[str] = None,
                   allow_unsealed: bool = False,
                   window_days: int = 400) -> dict:
        """Verify the whole folder — the nightly / inspection command.

        ``problems`` are things an inspector must see: a broken chain, a file
        rewritten or truncated after sealing, an unreadable manifest, a
        deleted log that still has a manifest, and (unless
        ``allow_unsealed``) an earlier day with no manifest at all, because
        nothing can then be proven about it.

        ``attention`` is maintenance: a sealed day that was extended after
        sealing (run ``seal_all``), or an unsealed day when the caller
        accepts that.
        """
        ref = self._normalize_date(today) if today else self._today()
        days = self.dates()
        reports: list[dict] = []
        problems: list[str] = []
        attention: list[str] = []
        for day in sorted(days, reverse=True):
            report = self.verify(day, today=ref)
            reports.append(report)
            if report["state"] == "tampered":
                problems.append(day)
            elif report["state"] == "stale":
                attention.append(day)
            elif report["state"] == "unsealed":
                (attention if allow_unsealed else problems).append(day)
        # A manifest with no log file at all: the day was deleted after it
        # was sealed — that is missing evidence, not a missing day.
        known = set(days)
        try:
            orphans = sorted(p.name for p in self.folder.glob(
                "*.manifest.json"))
        except OSError:
            orphans = []
        for name in orphans:
            day = name[:-len(".manifest.json")]
            if day in known:
                continue
            problems.append(day)
            reports.append({
                "date": day, "exists": False, "lines": 0, "items": 0,
                "ok": False, "state": "missing", "manifest": "orphan",
                "errors": ["a manifest exists but the day's log file is gone"],
                "row_hashes": [], "manifest_detail": None,
            })
        counts = {"days": len(reports), "lines": 0, "items": 0,
                  "clean": 0, "stale": 0, "unsealed": 0, "tampered": 0,
                  "missing": 0}
        for report in reports:
            counts["lines"] += int(report.get("lines") or 0)
            counts["items"] += int(report.get("items") or 0)
            state = report.get("state", "clean")
            counts[state] = counts.get(state, 0) + 1
        return {
            "folder": str(self.folder),
            "today": ref,
            "ok": not problems,
            "problems": problems,
            "attention": attention,
            "days": reports,
            "counts": counts,
            "allow_unsealed": bool(allow_unsealed),
            "checked_at": datetime.now().astimezone().isoformat(
                timespec="seconds"),
        }

    # ── export / retention ──────────────────────────────────────────

    CSV_FIELDS = ("seq", "start", "end", "kind", "title", "artist",
                  "duration_s", "ended_why", "channel", "source", "path",
                  "plan_revision", "reason")

    def export_csv(self, day: Optional[str] = None,
                   path: Optional[str] = None) -> Optional[str]:
        """Write the day as a spreadsheet (UTF-8 BOM so Excel shows Thai)."""
        day = self._normalize_date(day)
        target = Path(path) if path else self.folder / f"{day}.csv"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(self.CSV_FIELDS))
                writer.writeheader()
                for item in self.read_day(day):
                    source = item.get("source") or {}
                    writer.writerow({
                        "seq": item.get("seq"),
                        "start": item.get("ts_start"),
                        "end": item.get("ts_end"),
                        "kind": item.get("kind"),
                        "title": item.get("title"),
                        "artist": item.get("artist"),
                        "duration_s": item.get("actual_duration") or "",
                        "ended_why": item.get("ended_why") or "",
                        "channel": item.get("channel"),
                        "source": source.get("type", ""),
                        "path": source.get("path", ""),
                        "plan_revision": item.get("plan_revision") or "",
                        "reason": item.get("reason"),
                    })
        except OSError as err:
            self._fail(f"cannot export CSV {target.name}: {err}")
            return None
        return str(target)

    def prune(self, keep_days: Optional[int] = None, *,
              today: Optional[str] = None, force: bool = False) -> list[str]:
        """Delete days older than the retention. Returns the removed dates.

        Refuses (ValueError) to go under the legal floor unless ``force``.
        Today is never touched.
        """
        days = self.keep_days if keep_days is None else int(keep_days)
        if days < LEGAL_MIN_DAYS and not force:
            raise ValueError(
                f"retention {days} days is below the legal minimum "
                f"({LEGAL_MIN_DAYS} days) — confirm with --force")
        today = self._normalize_date(today) if today else self._today()
        cutoff = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)
        removed: list[str] = []
        for day in self.dates():
            if day == today:
                continue
            try:
                when = datetime.strptime(day, "%Y-%m-%d")
            except ValueError:
                continue
            if when >= cutoff:
                continue
            for path in (self.day_file(day), self.manifest_file(day)):
                try:
                    if path.exists():
                        path.unlink()
                except OSError as err:
                    self._fail(f"cannot prune {path.name}: {err}")
                    continue
            removed.append(day)
        return removed

    # ── UI conveniences ─────────────────────────────────────────────

    def gap_warning(self, *, today: Optional[str] = None,
                    window_days: int = 60) -> str:
        """Short Thai warning for the tab when earlier days are unsealed."""
        gaps = self.gaps(today=today, window_days=window_days)
        if not gaps:
            return ""
        head = ", ".join(gaps[:3]) + (" …" if len(gaps) > 3 else "")
        return (f"⚠️ {len(gaps)} day(s) never sealed ({head}) — "
                "press Seal or run asrun.py --seal-all")

    def summary_line(self, day: Optional[str] = None) -> str:
        """One-line status for the As-run tab header."""
        day = self._normalize_date(day)
        stats = self.stats(day)
        if not stats["exists"]:
            return f"{day}: no log yet"
        verify = self.verify(day)
        chain = "🔗 chain intact" if verify["ok"] else "⚠️ chain broken"
        hours, rem = divmod(int(stats["aired_seconds"]), 3600)
        minutes, seconds = divmod(rem, 60)
        return (f"{day}: {stats['items']} items · "
                f"{hours}:{minutes:02d}:{seconds:02d} on air · {chain}"
                + ("" if stats["open"] == 0 else
                   f" · {stats['open']} still open"))

# ═══════════════════════════════════════════════════════════════════
# Command line — the nightly / inspection interface
# ═══════════════════════════════════════════════════════════════════

_STATE_TEXT = {
    "clean": "clean",
    "stale": "STALE (seal again)",
    "unsealed": "UNSEALED",
    "tampered": "TAMPERED",
    "missing": "MISSING",
}


def _version_hint() -> str:
    """The app version stamped into manifests written by the CLI."""
    try:
        text = (Path(__file__).with_name("version.txt")
                .read_text(encoding="utf-8").strip())
        return text or "cli"
    except OSError:
        return "cli"


def _parse_day_arg(text: str) -> Optional[str]:
    """Validate a date given on the command line (None when unusable)."""
    try:
        return AsRunLog._normalize_date(text)
    except (ValueError, TypeError):
        return None


def _print_day_line(report: dict) -> None:
    detail = ""
    if report.get("errors"):
        detail = f" — {report['errors'][0]}"
    elif report.get("manifest") == "stale":
        detail = " — extended after sealing (sealed prefix intact)"
    elif report.get("manifest") == "missing":
        detail = " — no manifest"
    print(f"  {report.get('date', '?')}  "
          f"{_STATE_TEXT.get(report.get('state', ''), report.get('state', '')):16s}"
          f"{int(report.get('items') or 0):6d} items "
          f"{int(report.get('lines') or 0):6d} lines"
          f"   manifest: {report.get('manifest', '?')}{detail}")


def main(argv: Optional[list[str]] = None) -> int:
    """Run the as-run command line. Returns one of the EXIT_* codes."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="asrun.py",
        description="FreQ as-run log — seal, verify and export the record of "
                    "what actually aired.",
        epilog="exit codes: 0 clean · 1 evidence broken/missing · "
               "3 maintenance due · 2 usage error")
    parser.add_argument("--folder", default=str(Path("logs") / "asrun"),
                        help="as-run folder (default: logs/asrun)")
    parser.add_argument("--today", default="",
                        help="treat this date (YYYY-MM-DD) as today, for "
                             "re-verifying a past day")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--allow-unsealed", action="store_true",
                        help="an earlier day without a manifest is maintenance, "
                             "not a problem")
    parser.add_argument("--keep-days", type=int, default=None,
                        help=f"retention in days (floor {LEGAL_MIN_DAYS})")
    parser.add_argument("--force", action="store_true",
                        help="prune under the legal floor / re-seal over a "
                             "refusal (explicit operator decision)")
    parser.add_argument("--include-today", action="store_true",
                        help="--seal-all also seals today (still airing)")
    parser.add_argument("--out", default="", help="CSV output path")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify-all", action="store_true",
                       help="verify every day in the folder (default action)")
    group.add_argument("--seal-all", action="store_true",
                       help="seal every day that needs it")
    group.add_argument("--seal", metavar="DATE", help="seal one day")
    group.add_argument("--list", action="store_true", dest="list_days",
                       help="day-by-day summary")
    group.add_argument("--export", metavar="DATE", help="export one day as CSV")
    group.add_argument("--prune", action="store_true",
                       help="delete days older than the retention")
    args = parser.parse_args(argv)

    try:
        log = AsRunLog(args.folder, enabled=True, app_version=_version_hint(),
                       keep_days=args.keep_days if args.keep_days
                       else DEFAULT_KEEP_DAYS)
    except OSError as err:                     # pragma: no cover - defensive
        print(f"error: cannot use folder {args.folder!r}: {err}",
              file=sys.stderr)
        return EXIT_USAGE
    today = None
    if args.today:
        today = _parse_day_arg(args.today)
        if today is None:
            print(f"error: --today {args.today!r} is not a date "
                  "(YYYY-MM-DD)", file=sys.stderr)
            return EXIT_USAGE
    for flag, value in (("--seal", args.seal), ("--export", args.export)):
        if value and _parse_day_arg(value) is None:
            print(f"error: {flag} {value!r} is not a date (YYYY-MM-DD)",
                  file=sys.stderr)
            return EXIT_USAGE
    if not log.folder.exists() and not args.quiet:
        print(f"note: {log.folder} does not exist yet — nothing has been "
              "recorded here", file=sys.stderr)

    if args.prune:
        try:
            removed = log.prune(args.keep_days, today=today, force=args.force)
        except ValueError as err:
            print(f"error: {err}", file=sys.stderr)
            return EXIT_USAGE
        if args.as_json:
            print(json.dumps({"removed": removed}, ensure_ascii=False))
        elif not args.quiet:
            print(f"pruned {len(removed)} day(s): "
                  f"{', '.join(removed) if removed else '-'}")
        return EXIT_OK

    if args.seal:
        manifest = log.write_manifest(args.seal, force=args.force)
        if manifest is None:
            print(f"error: {args.seal}: {log.last_error or 'seal failed'}",
                  file=sys.stderr)
            return EXIT_PROBLEM
        if args.as_json:
            print(json.dumps(manifest, ensure_ascii=False, default=str))
        elif not args.quiet:
            state = ("already current" if manifest.get("unchanged")
                     else "sealed")
            print(f"{args.seal}: {state} — {manifest.get('items')} items, "
                  f"{manifest.get('lines')} lines, "
                  f"chain_ok={manifest.get('chain_ok')}")
        return EXIT_OK

    if args.export:
        if not log.stats(args.export)["exists"]:
            print(f"error: no as-run file for {args.export}", file=sys.stderr)
            return EXIT_PROBLEM
        path = log.export_csv(args.export, args.out or None)
        if not path:
            print(f"error: export {args.export} failed: {log.last_error}",
                  file=sys.stderr)
            return EXIT_PROBLEM
        if not args.quiet:
            print(path)
        return EXIT_OK

    if args.list_days:
        days = sorted(log.dates(), reverse=True)
        if args.as_json:
            print(json.dumps({"folder": str(log.folder),
                              "days": [log.stats(d) for d in days]},
                             ensure_ascii=False, default=str))
            return EXIT_OK
        print(f"as-run — {log.folder}  ({len(days)} day(s))")
        for day in days:
            _print_day_line(log.verify(day, today=today))
        return EXIT_OK

    if args.seal_all:
        report = log.seal_all(today=today, force=args.force,
                              include_today=args.include_today)
        # Sealing is maintenance, but the exit code must still tell the truth
        # about the folder: today is deliberately NOT sealed (it is still
        # airing), so a day that was skipped is never a reason to report
        # success while a tampered file is sitting there.
        audit = log.verify_all(today=today, allow_unsealed=True)
        report["audit"] = {"ok": audit["ok"],
                           "problems": audit["problems"],
                           "attention": audit["attention"]}
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, default=str))
        else:
            print(f"as-run seal-all — {report['folder']}")
            print(f"  sealed    : {', '.join(report['sealed']) or '-'}")
            print(f"  unchanged : {', '.join(report['unchanged']) or '-'}")
            if report["skipped"] and not args.quiet:
                print(f"  skipped   : {', '.join(report['skipped'])}"
                      "  (today is still airing — seal it with --seal)")
            for refused in report["refused"]:
                print(f"  REFUSED   : {refused['day']} — {refused['reason']}",
                      file=sys.stderr)
        if report["refused"]:
            print("at least one day refused sealing — inspect it with "
                  "--verify-all before forcing", file=sys.stderr)
            return EXIT_PROBLEM
        if not audit["ok"]:
            print(f"problems ({len(audit['problems'])}): "
                  f"{', '.join(audit['problems'])}", file=sys.stderr)
            print("sealing did not fix those days — inspect them; do NOT "
                  "force a seal over them", file=sys.stderr)
            return EXIT_PROBLEM
        return EXIT_OK

    # Default action (and --verify-all): the whole folder.
    report = log.verify_all(today=today, allow_unsealed=args.allow_unsealed)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return (EXIT_PROBLEM if not report["ok"]
                else EXIT_ATTENTION if report["attention"] else EXIT_OK)
    counts = report["counts"]
    if not args.quiet:
        print(f"as-run verify — {report['folder']}   "
              f"(today {report['today']})")
        for day_report in report["days"]:
            _print_day_line(day_report)
        print(f"  {counts['days']} day(s) · {counts['items']} items · "
              f"{counts['lines']} lines · clean {counts['clean']} · "
              f"stale {counts['stale']} · unsealed {counts['unsealed']} · "
              f"tampered {counts['tampered']}"
              + (f" · missing {counts['missing']}" if counts.get("missing")
                 else ""))
    if not report["ok"]:
        print(f"problems ({len(report['problems'])}): "
              f"{', '.join(report['problems'])}", file=sys.stderr)
        print("  → inspect those days; do NOT seal over them before the "
              "cause is understood", file=sys.stderr)
        return EXIT_PROBLEM
    if report["attention"]:
        print(f"maintenance due ({len(report['attention'])}): "
              f"{', '.join(report['attention'])} → run --seal-all")
        return EXIT_ATTENTION
    if not args.quiet:
        print("all days clean")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
