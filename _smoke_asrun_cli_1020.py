#!/usr/bin/env python3
"""Smoke: as-run command line + folder-wide sealing [asrun-cli-1020].

Runs the REAL command line (`python asrun.py …`) in a subprocess and checks
its exit code, so the contract a cron job or an inspector depends on is
tested through the interface they will use.

Proves, on a folder built through the real module:
  1. a clean folder exits 0 and says so (the check is not vacuous)
  2. every kind of forgery is caught and NAMED:
       E  an edited payload (chain hash mismatch)          → exit 1
       F  a deleted row (seq gap + broken link)            → exit 1
       D  a truncated day file                             → exit 1
       A  a fully re-chained file, same line count         → exit 1
       B  a re-chained file that also GREW (slips past a   → exit 1
          line-count-only rule — caught by the sealed prefix)
       G  a deleted day file whose manifest survives       → exit 1
       H  a missing manifest on an earlier day             → exit 1 (3 with
                                                              --allow-unsealed)
       C  a post-seal append with a valid chain            → exit 3 (never 0)
  3. `--seal-all` re-seals case C with the audit trail (previous hash +
     reseal_count) and then verifies clean again
  4. `--seal-all` REFUSES case A/B and leaves the old manifest byte-identical
     (sealing can never launder a rewritten file)
  5. the rest of the interface: --list / --json / --export / --prune / bad
     dates / a folder that does not exist yet
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime as real_datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import asrun  # noqa: E402
from asrun import AsRunLog  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


ASRUN = HERE / "asrun.py"


CRASHES = []


def cli(folder, *args):
    """Run the real command line. Returns (exit_code, stdout, stderr)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ASRUN), "--folder", str(folder), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(HERE), env=env, timeout=120)
    # An exit code of 1 must mean "evidence problem", never a crash: a
    # traceback would otherwise make every tampering check pass for the
    # wrong reason.
    if "Traceback (most recent call last)" in proc.stderr:
        CRASHES.append((args, proc.stderr.strip().splitlines()[-1]))
    return proc.returncode, proc.stdout, proc.stderr


# ── the folder under test, built through the real module ────────────

REAL_DT = asrun.datetime


class _Clock(REAL_DT):
    """A clock we can set, so days and hours are deterministic."""

    t = None

    @classmethod
    def now(cls, tz=None):
        return cls.t


def build_folder(past_days=2, items_per_day=3):
    """A folder with `past_days` sealed days plus today, still airing."""
    folder = tempfile.mkdtemp(prefix="asrun_cli_base_")
    today = REAL_DT.now().replace(hour=9, minute=0, second=0, microsecond=0)
    days = [(today - timedelta(days=n)) for n in range(past_days, 0, -1)]
    asrun.datetime = _Clock
    try:
        log = AsRunLog(folder, enabled=True, app_version="smoke-cli")
        for offset, day_start in enumerate(days):
            _Clock.t = day_start
            for i in range(items_per_day):
                # Thai titles are a deliberate UTF-8 fixture (CSV round-trip).
                item = log.begin("music", title=f"เพลง {offset + 1}-{i + 1}",
                                 artist="ศิลปิน", duration=200.0,
                                 path=f"C:/music/m{offset}{i}.mp3")
                _Clock.t = _Clock.t + timedelta(seconds=201)
                log.end(item, "natural", actual_duration=200.0)
        # today: one item still open, no manifest (a day still airing)
        _Clock.t = REAL_DT.now().replace(hour=8, minute=30, second=0,
                                         microsecond=0)
        log.begin("music", title="วันนี้ยังออกอากาศ", duration=180.0,
                  path="C:/music/today.mp3")
        today_str = _Clock.t.strftime("%Y-%m-%d")
        log.seal_stale(today=today_str, force_rescan=True)
    finally:
        asrun.datetime = REAL_DT
    return folder, [d.strftime("%Y-%m-%d") for d in days], today_str


def copy_of(folder, tag):
    target = tempfile.mkdtemp(prefix=f"asrun_cli_{tag}_")
    shutil.rmtree(target)
    shutil.copytree(folder, target)
    return target


def day_path(folder, day):
    return Path(folder) / f"{day}.jsonl"


def manifest_path(folder, day):
    return Path(folder) / f"{day}.manifest.json"


def manifest_sha(folder, day):
    return hashlib.sha256(manifest_path(folder, day).read_bytes()).hexdigest()


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def rows_of(folder, day):
    text = day_path(folder, day).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_rows(folder, day, rows, *, rechain):
    if rechain:
        prev = "0" * 40
        for index, row in enumerate(rows, start=1):
            row.pop("hash", None)
            row["seq"] = index
            row["prev_hash"] = prev
            row["hash"] = _sha1(_canonical(row))
            prev = row["hash"]
    day_path(folder, day).write_text(
        "\n".join(_canonical(row) for row in rows) + "\n", encoding="utf-8")


BASE, PAST, TODAY = build_folder()
check("the fixture folder really has sealed past days",
      len(PAST) == 2 and all(manifest_path(BASE, d).exists() for d in PAST),
      f"days={PAST} today={TODAY}")

# ── 1. clean control ────────────────────────────────────────────────
rc, out, err = cli(BASE, "--verify-all")
check("clean folder exits 0", rc == 0, f"rc={rc} err={err.strip()[:120]}")
check("clean folder says so", "all days clean" in out, out.strip()[:200])
check("clean folder lists every day", all(d in out for d in PAST + [TODAY]),
      out.strip()[:200])
rc, out, _ = cli(BASE, "--verify-all", "--json")
report = json.loads(out)
check("--json reports ok + counts",
      rc == 0 and report["ok"] is True and report["counts"]["days"] == 3
      and report["counts"]["tampered"] == 0, json.dumps(report["counts"]))

# ── 2. forgeries ────────────────────────────────────────────────────
VICTIM = PAST[0]

# E: edit a payload without re-chaining (the naive tamper)
copy = copy_of(BASE, "edit")
rows = rows_of(copy, VICTIM)
rows[0]["item"]["title"] = "แก้ชื่อเพลง"
write_rows(copy, VICTIM, rows, rechain=False)
rc, out, err = cli(copy, "--verify-all")
check("E: an edited payload is caught (exit 1)", rc == 1, f"rc={rc}")
check("E: the CLI names the day and the reason",
      VICTIM in err and "line edited" in out, out.strip()[:200])
check("E: it is never reported clean", "all days clean" not in out)

# F: delete a row (seq gap + broken link)
copy = copy_of(BASE, "delete")
rows = rows_of(copy, VICTIM)
write_rows(copy, VICTIM, rows[:1] + rows[2:], rechain=False)
rc, out, err = cli(copy, "--verify-all")
check("F: a deleted row is caught (exit 1)", rc == 1, f"rc={rc}")
check("F: the day is named as a problem", VICTIM in err, err.strip()[:160])

# D: truncate the file (chain still valid, but shorter than the seal)
copy = copy_of(BASE, "truncate")
rows = rows_of(copy, VICTIM)
write_rows(copy, VICTIM, rows[:-1], rechain=False)
rc, out, err = cli(copy, "--verify-all")
check("D: a truncated day is caught (exit 1)", rc == 1, f"rc={rc}")
check("D: reported as truncated (not merely unsealed)",
      "truncated after sealing" in out, out.strip()[:200])

# A: a fully re-chained file with the SAME line count (a skilled forgery)
copy = copy_of(BASE, "rechain")
rows = rows_of(copy, VICTIM)
rows[0]["item"]["title"] = "เขียนใหม่ทั้งไฟล์"
write_rows(copy, VICTIM, rows, rechain=True)
rc, out, err = cli(copy, "--verify-all")
check("A: a re-chained file with the same line count is caught (exit 1)",
      rc == 1, f"rc={rc} err={err.strip()[:120]}")
check("A: reported against the sealed manifest",
      "manifest" in out and VICTIM in err, out.strip()[:200])
tampered_copy = copy

# B: re-chained AND grown — the case a line-count rule would wave through
copy = copy_of(BASE, "rechain_grow")
rows = rows_of(copy, VICTIM)
rows[0]["item"]["title"] = "เขียนใหม่แล้วเพิ่มแถว"
extra = dict(rows[-1])
extra["event"] = "end"
extra["item_id"] = f"{VICTIM}#1"
write_rows(copy, VICTIM, rows + [extra], rechain=True)
rc, out, err = cli(copy, "--verify-all")
check("B: a re-chained and GROWN file is still caught (exit 1)", rc == 1,
      f"rc={rc}")
check("B: the sealed prefix is named as the reason",
      "prefix" in out or "prefix" in err, (out + err).strip()[:220])
grown_copy = copy

# C: a post-seal append with a valid chain — never a clean pass
copy = copy_of(BASE, "late_append")
sealed_sha = manifest_sha(copy, VICTIM)
asrun.datetime = _Clock
try:
    log = AsRunLog(copy, enabled=True, app_version="smoke-cli")
    base = real_datetime.strptime(VICTIM, "%Y-%m-%d").replace(
        hour=23, minute=50, second=0, microsecond=0)
    _Clock.t = base
    item = log.begin("music", title="เพลงข้ามคืน", duration=120.0,
                     path="C:/music/late.mp3")
    _Clock.t = base + timedelta(minutes=2)
    log.end(item, "natural", actual_duration=120.0)
finally:
    asrun.datetime = REAL_DT
rc, out, err = cli(copy, "--verify-all")
check("C: a post-seal append never passes as clean (exit 3)", rc == 3,
      f"rc={rc} out={out.strip()[:160]}")
check("C: reported as stale with the remedy",
      "STALE" in out and "seal-all" in out, out.strip()[:220])
check("C: the appended row is still readable in the day's log",
      any(r.get("item", {}).get("title") == "เพลงข้ามคืน"
          for r in rows_of(copy, VICTIM)))
stale_copy = copy

# G: the day file is gone but its manifest survived
copy = copy_of(BASE, "missing_file")
day_path(copy, VICTIM).unlink()
rc, out, err = cli(copy, "--verify-all")
check("G: a deleted day file is caught (exit 1)", rc == 1, f"rc={rc}")
check("G: reported as missing evidence",
      "MISSING" in out and VICTIM in err, (out + err).strip()[:200])

# H: an earlier day with no manifest at all
copy = copy_of(BASE, "unsealed")
manifest_path(copy, VICTIM).unlink()
rc, out, err = cli(copy, "--verify-all")
check("H: an unsealed earlier day fails by default (exit 1)", rc == 1,
      f"rc={rc}")
check("H: it is reported as UNSEALED", "UNSEALED" in out, out.strip()[:200])
rc, out, _ = cli(copy, "--verify-all", "--allow-unsealed")
check("H: --allow-unsealed downgrades it to maintenance (exit 3)", rc == 3,
      f"rc={rc}")
check("H: even downgraded it is never 'clean'", "all days clean" not in out)
unsealed_copy = copy

# ── 3. seal-all re-seals legitimately, with the audit trail ─────────
old_manifest = json.loads(manifest_path(stale_copy, VICTIM).read_text(
    encoding="utf-8"))
rc, out, err = cli(stale_copy, "--seal-all")
check("seal-all re-seals a grown day (exit 0)", rc == 0,
      f"rc={rc} err={err.strip()[:120]}")
new_manifest = json.loads(manifest_path(stale_copy, VICTIM).read_text(
    encoding="utf-8"))
check("seal-all kept the superseded hash (audit trail)",
      new_manifest.get("previous_file_sha256") == old_manifest.get("file_sha256")
      and new_manifest.get("reseal_count") == 1,
      json.dumps({k: new_manifest.get(k) for k in
                  ("previous_file_sha256", "previous_lines", "reseal_count")}))
check("seal-all recorded the new chain tail",
      new_manifest.get("tail_hash") and new_manifest.get("manifest_version") == 2)
rc, out, _ = cli(stale_copy, "--verify-all")
check("the re-sealed day verifies clean (exit 0)", rc == 0 and
      "all days clean" in out, f"rc={rc} {out.strip()[:160]}")
rc, out, _ = cli(stale_copy, "--seal-all")
check("a second seal-all is a no-op (exit 0)", rc == 0,
      out.strip()[:160])

# ── 4. seal-all refuses to launder a rewritten day ──────────────────
for tag, copy in (("A", tampered_copy), ("B", grown_copy)):
    before = manifest_sha(copy, VICTIM)
    rc, out, err = cli(copy, "--seal-all")
    check(f"{tag}: seal-all refuses the rewritten day (exit 1)", rc == 1,
          f"rc={rc}")
    check(f"{tag}: refusal is explained on stderr", "REFUSED" in err,
          err.strip()[:160])
    check(f"{tag}: the sealed manifest is byte-identical after the refusal",
          manifest_sha(copy, VICTIM) == before)
    rc, _, _ = cli(copy, "--verify-all")
    check(f"{tag}: the day still fails verification afterwards (exit 1)",
          rc == 1, f"rc={rc}")

# 4b. seal-all skips today on purpose — but a tampered today must still fail
copy = copy_of(BASE, "today_tampered")
rows = rows_of(copy, TODAY)
rows[0]["item"]["title"] = "แก้วันนี้"
write_rows(copy, TODAY, rows, rechain=False)
rc, out, err = cli(copy, "--seal-all")
check("seal-all still fails when the tampered day is today (exit 1)",
      rc == 1, f"rc={rc} out={out.strip()[:120]}")
check("seal-all names that day on stderr",
      TODAY in err or TODAY in out, (out + err).strip()[:200])
check("seal-all says out loud that today was skipped, not sealed",
      "skipped" in out, out.strip()[:200])

# ── 5. the rest of the interface ────────────────────────────────────
rc, out, _ = cli(BASE, "--list")
check("--list exits 0 and prints one line per day",
      rc == 0 and all(d in out for d in PAST + [TODAY]), out.strip()[:160])

out_csv = Path(tempfile.mkdtemp(prefix="asrun_cli_csv_")) / "day.csv"
rc, out, err = cli(BASE, "--export", VICTIM, "--out", str(out_csv))
text = out_csv.read_text(encoding="utf-8-sig") if out_csv.exists() else ""
check("--export writes the day as CSV (exit 0)",
      rc == 0 and text.startswith("seq,start,end,kind"), f"rc={rc} {err[:80]}")
check("--export keeps the Thai titles", "ศิลปิน" in text)
rc, out, err = cli(BASE, "--export", "1999-01-01")
check("--export of a day with no file fails (exit 1)", rc == 1, f"rc={rc}")

rc, out, err = cli(BASE, "--prune", "--keep-days", "5")
check("--prune under the legal floor is a usage error (exit 2)", rc == 2,
      f"rc={rc}")
check("--prune under the floor deleted nothing",
      day_path(BASE, VICTIM).exists())

rc, out, err = cli(BASE, "--seal", "2026-13-99")
check("--seal with a bad date is a usage error (exit 2)", rc == 2, f"rc={rc}")
rc, out, err = cli(BASE, "--today", "not-a-date")
check("--today with a bad date is a usage error (exit 2)", rc == 2, f"rc={rc}")

rc, out, err = cli(BASE, "--verify-all", "--today", TODAY)
check("--today lets a past day be re-verified as 'today' (exit 0)", rc == 0,
      f"rc={rc} {out.strip()[:120]}")

empty = tempfile.mkdtemp(prefix="asrun_cli_empty_")
rc, out, err = cli(empty, "--verify-all")
check("an empty folder exits 0 with no days", rc == 0 and "0 day(s)" in out,
      f"rc={rc} {out.strip()[:120]}")
missing = Path(tempfile.mkdtemp(prefix="asrun_cli_none_")) / "never"
rc, out, err = cli(missing, "--verify-all")
check("a folder that does not exist yet is a note, not a failure",
      rc == 0 and ("does not exist" in err or "0 day(s)" in out),
      f"rc={rc} err={err.strip()[:120]}")

rc, out, err = cli(BASE, "--seal", TODAY)
today_manifest = json.loads(manifest_path(BASE, TODAY).read_text(
    encoding="utf-8"))
check("--seal seals today on request",
      rc == 0 and today_manifest.get("lines", 0) >= 1
      and today_manifest.get("open_items") == 1,
      json.dumps({k: today_manifest.get(k)
                  for k in ("lines", "items", "open_items")}))
rc, out, err = cli(BASE, "--seal", TODAY)
check("--seal twice is a no-op, not an error", rc == 0 and "already current" in out,
      out.strip()[:120])

# --prune: an old day goes, a recent day and today stay
old_folder = tempfile.mkdtemp(prefix="asrun_cli_prune_")
asrun.datetime = _Clock
try:
    log = AsRunLog(old_folder, enabled=True, app_version="smoke-cli")
    old_day = (real_datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_day = (real_datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    for day in (old_day, recent_day):
        _Clock.t = real_datetime.strptime(day, "%Y-%m-%d").replace(
            hour=10, minute=0, second=0, microsecond=0)
        item = log.begin("music", title=f"เพลง {day}", duration=100.0,
                         path="C:/music/old.mp3")
        _Clock.t = _Clock.t + timedelta(seconds=100)
        log.end(item, "natural", actual_duration=100.0)
        log.write_manifest(day)
finally:
    asrun.datetime = REAL_DT
rc, out, err = cli(old_folder, "--prune", "--keep-days", "30")
check("--prune past retention removes the old day (exit 0)", rc == 0,
      f"rc={rc} {out.strip()[:120]}")
check("--prune removed both the log and its manifest",
      not day_path(old_folder, old_day).exists()
      and not manifest_path(old_folder, old_day).exists())
check("--prune keeps days inside the retention",
      day_path(old_folder, recent_day).exists()
      and manifest_path(old_folder, recent_day).exists())
rc, out, _ = cli(old_folder, "--verify-all")
check("the pruned folder still verifies clean (exit 0)", rc == 0,
      f"rc={rc} {out.strip()[:140]}")

check("no CLI invocation crashed (an exit 1 is a verdict, not a traceback)",
      not CRASHES, str(CRASHES[:2]))

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
