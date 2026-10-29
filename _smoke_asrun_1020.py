#!/usr/bin/env python3
"""Smoke: As-run broadcast log [asrun-1020].

P0 of DESIGN-plan-1020.md — "what actually aired", append-only.

Verifies:
  1. the module: hash chain, pairing, verify (incl. tamper + seq gap),
     daily manifest, CSV export, retention floor, cross-midnight rule,
     and that a broken folder never raises on the playback path
  2. the real widget: 20 scripted queue items land in the file as 20 paired
     items, actual durations match real elapsed time, nothing is overwritten
  3. every hook: music / missing file / skip / ad / jingle (auto + manual) /
     anthem (with its cut) / mic / stream / relay
  4. the As-run tab: day table rows, summary, prev-day nav, CSV export,
     seal, verify dialog, prune confirm, settings round-trip, close seals
"""

import sys
import os
import hashlib
import tempfile
import time
from datetime import datetime as _real_datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — run from a throwaway dir so the
# user's real radio_queue.freq is never touched.
os.chdir(tempfile.mkdtemp(prefix="freq_asrun_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════
# 1. Module
# ══════════════════════════════════════════════════════════════
import asrun  # noqa: E402
from asrun import AsRunLog, LEGAL_MIN_DAYS  # noqa: E402

mod_dir = tempfile.mkdtemp(prefix="asrun_mod_")
log = AsRunLog(mod_dir, enabled=False, app_version="smoke")

check("disabled: begin writes nothing", log.begin("music", title="X") is None)
check("disabled: no file created", not Path(log.day_file()).exists())

log.configure(enabled=True, keep_days=45)
check("keep_days is kept above the legal floor", log.keep_days == 45)
log.configure(keep_days=1)
check("keep_days clamps up to the floor", log.keep_days == LEGAL_MIN_DAYS)
# Thai song titles are deliberate UTF-8 fixtures (the station airs them).
first = log.begin("music", title="เพลงไทย", artist="ศิลปิน",
                  duration=240.0, path="C:/music/a.mp3", reason="queue · local file")
check("begin returns an id", bool(first), str(first))
check("begin opens exactly one item as active",
      log.active_id == first and log.active is not None)
check("a second begin supersedes the first (no lost pairing)",
      (second := log.begin("mic", title="พูดสด")) is not None
      and log.read_day()[0]["ended_why"] == "superseded")
check("end closes the active item", log.end(second, "natural"))
check("end of an unknown id is a no-op", log.end("nope") is False)

items = log.read_day()
check("two items paired", len(items) == 2, str(len(items)))
check("start/end timestamps + totals present",
      all(i["ts_start"] and i["ts_end"] and i["actual_duration"] is not None
          for i in items), str(items))
check("stats count items and kinds",
      log.stats()["items"] == 2 and log.stats()["by_kind"] == {"music": 1, "mic": 1},
      str(log.stats()))

# ── hash chain ──
raw = Path(log.day_file()).read_text(encoding="utf-8").splitlines()
import json as _json  # noqa: E402
rows = [_json.loads(line) for line in raw]
check("every row carries prev_hash + hash",
      all(len(r["prev_hash"]) == 40 and len(r["hash"]) == 40 for r in rows))
check("genesis row links to the zero hash", rows[0]["prev_hash"] == "0" * 40)
check("each row links to the row before it",
      all(rows[i]["prev_hash"] == rows[i - 1]["hash"] for i in range(1, len(rows))))
check("seq counts up", [r["seq"] for r in rows] == list(range(1, len(rows) + 1)))

verify = log.verify()
check("verify passes on an untouched day",
      verify["ok"] and verify["items"] == 2 and not verify["errors"], str(verify))

manifest = log.write_manifest()
check("manifest sealed with counts + file hash",
      manifest and manifest["items"] == 2 and len(manifest["file_sha256"]) == 64)
check("verify accepts a matching manifest",
      log.verify()["manifest"] == "ok")

# tamper: rewrite one line's content (hash no longer matches)
lines = Path(log.day_file()).read_text(encoding="utf-8").splitlines()
row = _json.loads(lines[0])
row["item"]["title"] = "เพลงที่ถูกแก้"
lines[0] = _json.dumps(row, ensure_ascii=False)
Path(log.day_file()).write_text("\n".join(lines) + "\n", encoding="utf-8")
tampered = log.verify()
check("verify catches an edited line",
      not tampered["ok"] and any("hash" in e for e in tampered["errors"]),
      str(tampered["errors"][:2]))
check("verify reports the manifest no longer matches",
      tampered["manifest"] == "mismatch")

# seq gap: drop a middle line
good_lines = [l for l in lines if l.strip()]
Path(log.day_file()).write_text("\n".join([good_lines[0], good_lines[2], good_lines[3]]) + "\n",
                                encoding="utf-8")
gap = log.verify()
check("verify catches a deleted line (seq gap)",
      not gap["ok"] and any("seq" in e for e in gap["errors"]),
      str(gap["errors"][:2]))

# ── CSV export ──
csv_dir = tempfile.mkdtemp(prefix="asrun_csv_")
fresh = AsRunLog(csv_dir, enabled=True, app_version="smoke")
i1 = fresh.begin("music", title="ไทย", artist="ศิลปิน", duration=200, path="C:/a.mp3")
fresh.end(i1, "natural")
i2 = fresh.begin("ad", title="โฆษณา", path="C:/ad.mp3")
fresh.end(i2, "skipped")
csv_path = fresh.export_csv()
csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
check("CSV exported", csv_path is not None and Path(csv_path).exists(), str(csv_path))
check("CSV has BOM + header + one row per item",
      csv_text.startswith("seq,start,end,kind")
      and len([l for l in csv_text.splitlines() if l.strip()]) == 3,
      str(csv_text.splitlines()[:2]))
check("CSV keeps Thai title + end reason",
      "ไทย" in csv_text and "natural" in csv_text)

# ── retention ──
old_day = "2020-01-01"
for day in (old_day, "2020-01-02"):
    Path(fresh.day_file(day)).write_text("{}\n", encoding="utf-8")
try:
    fresh.prune(5)
    check("prune refuses under the legal floor", False, "no ValueError raised")
except ValueError:
    check("prune refuses under the legal floor", True)
check("refused prune deleted nothing", Path(fresh.day_file(old_day)).exists())
removed = fresh.prune(LEGAL_MIN_DAYS)
check("prune removes days past retention", set(removed) == {old_day, "2020-01-02"},
      str(removed))
check("prune never removes today",
      Path(fresh.day_file()).exists() and fresh._today() not in removed)
check("forced prune can go lower", fresh.prune(1, force=True) == [])

# ── cross-midnight: the end row belongs to the day the item started ──
import datetime as _dt  # noqa: E402
roll = AsRunLog(tempfile.mkdtemp(prefix="asrun_roll_"), enabled=True)
real_dt = asrun.datetime


class _FakeDT(real_dt):
    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


asrun.datetime = _FakeDT
try:
    _FakeDT.fixed = real_dt(2026, 10, 20, 23, 59, 30).astimezone()
    late = roll.begin("music", title="เพลงข้ามคืน")
    _FakeDT.fixed = real_dt(2026, 10, 21, 0, 3, 0).astimezone()
    roll.end(late, "natural")
finally:
    asrun.datetime = real_dt
check("an item that crosses midnight stays in its start day's file",
      len(roll.read_day("2026-10-20")) == 1
      and not Path(roll.day_file("2026-10-21")).exists(),
      str(sorted(roll.dates())))

# ── a broken folder must never raise on the playback path ──
blocker = Path(tempfile.mkdtemp(prefix="asrun_bad_")) / "not_a_dir"
blocker.write_text("x", encoding="utf-8")
broken = AsRunLog(str(blocker), enabled=True)
bad_id = broken.begin("music", title="X")
check("IO failure: begin returns None instead of raising", bad_id is None)
check("IO failure: last_error is set for the UI", bool(broken.last_error),
      broken.last_error)
check("IO failure: end on a failed begin is safe", broken.end(bad_id) is False)

# ══════════════════════════════════════════════════════════════
# 2-4. The real widget
# ══════════════════════════════════════════════════════════════
import gui as gui_mod  # noqa: E402
from gui import RadioApp  # noqa: E402
from radio_manager import Song  # noqa: E402

app = RadioApp()
work = tempfile.mkdtemp(prefix="asrun_gui_")


def make_track(name, seconds=0):
    path = os.path.join(work, name)
    with open(path, "wb") as fh:
        fh.write(b"\0" * 44)
    return path


def stub_player():
    app.audio_player.play_file = lambda *a, **k: True
    app.audio_player.play_youtube = lambda *a, **k: True
    app.audio_player.play_mic_multi = lambda *a, **k: 1
    app.audio_player.stop = lambda: None
    app.audio_player.seek = lambda *a, **k: None
    # Cosmetic deferred jobs (waveform draw, crossfade arming) are not part
    # of what this suite measures — stubbing them keeps every after(...)
    # callback in the test about the as-run log.
    app._load_waveform = lambda *a, **k: None
    app._arm_crossfade_for_next = lambda *a, **k: None


def drain(seconds=0.45):
    """Run the deferred Tk callbacks a playback path scheduled."""
    time.sleep(seconds)
    app.update()


try:
    check("As-run tab exists in the sidebar",
          "As-run" in app._tab_names and app._sidebar_panels.get("As-run") is app.tab_asrun)
    check("as-run is OFF by default", app.asrun.enabled is False)
    check("tab shows day nav + switch + rows frame",
          all(hasattr(app, n) for n in
              ("asrun_switch_var", "asrun_day_label", "asrun_summary_label",
               "asrun_rows_frame", "asrun_keep_entry")))

    app.asrun.configure(enabled=True, folder=os.path.join(work, "logs", "asrun"))
    app.asrun_switch_var.set(True)
    app.asrun_switch.configure(text="Record")
    stub_player()
    day = app.asrun._today()

    # ── 20 scripted items ──
    files = [make_track(f"s{i}.wav") for i in range(20)]
    for i, path in enumerate(files):
        app.rq.queue.append(Song(title=f"เพลง {i + 1}", artist="ศิลปิน",
                                 duration=180.0, source="local",
                                 file_path=path, id=f"id{i}"))
    app.rq.current_index = 0
    app._play_index(0)
    check("first item opens on air", app.asrun.stats()["items"] == 1
          and app.asrun.active is not None, app.asrun.active_id or "")
    time.sleep(1.0)
    for _ in range(19):
        app._on_track_finished()
    app._asrun_end_active("stopped")
    stats20 = app.asrun.stats()
    items20 = app.asrun.read_day()
    check("20 scripted items -> 20 as-run items", stats20["items"] == 20,
          str(stats20))
    check("every item is closed", stats20["open"] == 0)
    check("two lines per item (start + end)", stats20["lines"] == 40,
          str(stats20["lines"]))
    check("all 20 are music",
          stats20["by_kind"].get("music") == 20, str(stats20["by_kind"]))
    check("first item's aired length matches real elapsed (~1.0 s)",
          0.9 <= (items20[0]["actual_duration"] or 0) <= 1.8,
          str(items20[0]["actual_duration"]))
    check("items 2-20 were near-instant (no sleeps)",
          all((i["actual_duration"] or 0) < 0.5 for i in items20[1:]))
    check("titles/artists kept per item",
          items20[3]["title"] == "เพลง 4" and items20[3]["artist"] == "ศิลปิน")
    check("verify passes after a scripted run",
          app.asrun.verify(day)["ok"], str(app.asrun.verify(day)["errors"][:2]))

    before = Path(app.asrun.day_file(day)).read_text(encoding="utf-8")
    app._asrun_begin("music", title="เพิ่มอีก")
    app._asrun_end_active("natural")
    after = Path(app.asrun.day_file(day)).read_text(encoding="utf-8")
    check("appending never rewrites existing lines",
          after.startswith(before) and len(after) > len(before))
    check("rq.history still records airings",
          len(app.rq.history) > 0, str(len(app.rq.history)))

    # ── missing file ──
    app.rq.queue.append(Song(title="ไฟล์หาย", duration=100, source="local",
                             file_path=os.path.join(work, "nope.mp3"), id="gone"))
    idx = len(app.rq.queue) - 1
    app.rq.current_index = idx
    app._play_index(idx)          # no cache -> the skip path
    check("missing file is recorded as 'missing', not silently dropped",
          any(i["title"] == "ไฟล์หาย" and i["ended_why"] == "missing"
              for i in app.asrun.read_day(day)),
          str(sorted({i["ended_why"] for i in app.asrun.read_day(day)})))
    check("a missing item is never left open",
          not any(i["title"] == "ไฟล์หาย" and i["open"]
                  for i in app.asrun.read_day(day)))
    # The skip cascade is scheduled with after(300/350) — drain it here so
    # no stale timer can run in the middle of a later step of this suite.
    drain()
    app._asrun_end_active("stopped")

    # ── manual skip ──
    app._play_index(0)
    app._next_track()
    check("a manual skip is recorded as 'skipped'",
          any(i["ended_why"] == "skipped" for i in app.asrun.read_day(day)))

    # ── ad break ──
    ad_file = make_track("ad1.mp3")
    app.commercial.enabled = True
    app.commercial.ads = [ad_file]
    app.commercial.interval = 1
    app.commercial.song_counter = 0
    app.jingle.enabled = False
    app.commercial_var.set(True)
    app._play_index(0)
    app._on_track_finished()
    check("an automatic ad break opens an 'ad' item",
          app.asrun.active is not None and app.asrun.active["kind"] == "ad",
          str(app.asrun.active))
    app._on_track_finished()
    ads = [i for i in app.asrun.read_day(day) if i["kind"] == "ad"]
    check("the ad closed naturally and music resumed",
          ads and ads[-1]["ended_why"] == "natural"
          and app.asrun.active is not None and app.asrun.active["kind"] == "music",
          str([(a["title"], a["ended_why"]) for a in ads]))
    app.commercial.enabled = False
    app.commercial_var.set(False)

    # ── automatic jingle ──
    jingle_file = make_track("jingle.mp3")
    app.jingle.enabled = True
    app.jingle.jingle_file = jingle_file
    app.jingle.interval = 1
    app.jingle.song_counter = 0
    app.jingle_var.set(True)
    app._play_index(0)
    app._on_track_finished()
    check("an automatic jingle opens a 'jingle' item",
          app.asrun.active is not None and app.asrun.active["kind"] == "jingle",
          str(app.asrun.active))
    app._on_track_finished()
    js = [i for i in app.asrun.read_day(day) if i["kind"] == "jingle"]
    check("auto jingle closed naturally + music resumed",
          js and js[-1]["ended_why"] == "natural"
          and app.asrun.active is not None
          and app.asrun.active["kind"] == "music",
          str([(j["title"], j["ended_why"]) for j in js]))
    app.jingle.enabled = False
    app.jingle_var.set(False)

    # ── manual jingle cuts the song ──
    app._play_index(0)
    app._play_jingle_manual()
    cut = [i for i in app.asrun.read_day(day) if i["ended_why"] == "jingle-cut"]
    check("manual jingle records the interrupted song as 'jingle-cut'",
          len(cut) >= 1 and app.asrun.active is not None
          and app.asrun.active["kind"] == "jingle")
    try:
        # Exactly what the jingle's on_finish does — run its after(0) hop.
        app._jingle_resume()
        app.update()
    except Exception as err:
        check("manual jingle resume drains", False, str(err))
    check("after a manual jingle the song resumes on air",
          app.asrun.active is not None and app.asrun.active["kind"] == "music",
          str(app.asrun.active))
    check("the resumed song carries the jingle-resume reason",
          any(i["reason"] == "resumed the song after the jingle"
              for i in app.asrun.read_day(day)))

    # ── anthem cuts the song, then it resumes ──
    anthem_file = make_track("anthem.mp3")
    app.anthem.config.duration = 30.0
    app.anthem.config.anthem_file = anthem_file
    try:
        app.anthem.stop()
    except Exception:
        pass
    real_available = type(app.audio_player).available
    type(app.audio_player).available = property(lambda self: True)
    try:
        app._play_index(0)
        app._anthem_play(anthem_file)
        try:
            app.update()
        except Exception as err:
            check("anthem drains", False, str(err))
        anthem_items = [i for i in app.asrun.read_day(day) if i["kind"] == "anthem"]
        check("the anthem opened its own item",
              bool(anthem_items) and anthem_items[-1]["title"] == "national anthem",
              str(anthem_items[-1:]))
        check("the song under the anthem is marked 'anthem-cut'",
              any(i["ended_why"] == "anthem-cut" for i in app.asrun.read_day(day)))
        app._anthem_resume()
        try:
            app.update()
        except Exception as err:
            check("anthem resume drains", False, str(err))
        check("anthem closed natural + the song re-entered",
              any(i["kind"] == "anthem" and i["ended_why"] == "natural"
                  for i in app.asrun.read_day(day))
              and app.asrun.active is not None
              and app.asrun.active["kind"] == "music",
              str(app.asrun.active))
    finally:
        type(app.audio_player).available = real_available

    # ── mic segment ──
    mic_song = Song(title="ช่วงพูดสด", duration=30.0, source="mic")
    mic_song.mic_duration = 30.0
    app.rq.queue.insert(0, mic_song)
    app.rq.current_index = 0
    app._play_index(0)
    check("a mic queue item opens kind='mic'",
          app.asrun.active is not None and app.asrun.active["kind"] == "mic",
          str(app.asrun.active))
    app._on_track_finished()
    mic_items = [i for i in app.asrun.read_day(day) if i["kind"] == "mic"]
    check("mic item closed with a reason", bool(mic_items) and mic_items[-1]["ended_why"])

    # ── outgoing stream ──
    real_streamer = gui_mod.streamer

    class FakeStreamer:
        started = False
        is_streaming = False
        protocol = None

        def configure(self, **kwargs):
            self.protocol = kwargs.get("protocol")

        def start(self):
            self.started = True
            return True

        def stop(self):
            self.started = False

        def on_track_change(self, *a, **k):
            pass

    gui_mod.streamer = FakeStreamer()
    try:
        app.stream_native_var.set(False)
        app._start_stream()
        check("going on air opens a 'stream' item",
              app.asrun.active is not None and app.asrun.active["kind"] == "stream",
              str(app.asrun.active))
        app._stop_stream()
        streams = [i for i in app.asrun.read_day(day) if i["kind"] == "stream"]
        check("stopping the stream closes it as 'stopped'",
              streams and streams[-1]["ended_why"] == "stopped",
              str([(s["title"], s["ended_why"]) for s in streams]))
    finally:
        gui_mod.streamer = real_streamer

    # ── relay in ──
    relay_ok = hasattr(app, "relay_status_label")
    if relay_ok:
        real_available = type(app.audio_player).available
        type(app.audio_player).available = property(lambda self: True)
        try:
            app._play_relay_file(make_track("relay.mp3"))
            check("a relay opens kind='relay'",
                  app.asrun.active is not None and app.asrun.active["kind"] == "relay",
                  str(app.asrun.active))
            app._stop_relay()
            relays = [i for i in app.asrun.read_day(day) if i["kind"] == "relay"]
            check("relay closed as 'stopped'",
                  relays and relays[-1]["ended_why"] == "stopped")
        finally:
            type(app.audio_player).available = real_available
    else:
        check("relay widgets present in this build", False, "relay_status_label missing")

    # ── air-check reference ──
    class _FakeRec:
        recording = True
        path = os.path.join(work, "aircheck_1200.wav")

        def elapsed(self):
            return 12.5

    app.aircheck_recorder = _FakeRec()
    app._asrun_begin("music", title="มีเสียงอ้างอิง")
    app._asrun_end_active("natural")
    ref = app.asrun.read_day(day)[-1]["recording"]
    check("an item links to the air-check file + offset",
          ref == {"file": _FakeRec.path, "offset": 12.5}, str(ref))
    app.aircheck_recorder = None
    app._asrun_begin("music", title="ไม่มีอัดเสียง")
    app._asrun_end_active("natural")
    check("no recording -> no reference (never invented)",
          app.asrun.read_day(day)[-1]["recording"] is None)

    # ── the broken folder again, now through the GUI path ──
    good_folder = app.asrun.folder
    app.asrun.configure(folder=str(blocker))
    started = app._asrun_begin("music", title="ระหว่างดิสก์เต็ม")
    check("GUI begin is fail-soft on IO error", started is None)
    check("GUI shows the error in the tab (no silent loss)",
          bool(app.asrun.last_error) and app._asrun_begin is not None)
    app._refresh_asrun(force=True)
    check("tab summary reports the write failure",
          "⚠️" in app.asrun_summary_label.cget("text"))
    app.asrun.configure(folder=str(good_folder))
    app.asrun.last_error = ""

    # ── the tab itself ──
    # The tab builds its cards in slices (a whole day in one go blocked the
    # event loop for over a second), so the check pumps the loop until the
    # build it asked for has finished — then asserts the SAME full count.
    def pump_rows(expected: int, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            app.update()
            if len(app.asrun_rows_frame.winfo_children()) >= expected:
                break
            time.sleep(0.01)

    app._refresh_asrun(force=True)
    pump_rows(app.asrun.stats(day)["items"])
    rows = [w for w in app.asrun_rows_frame.winfo_children()]
    check("tab draws one row per item",
          len(rows) == app.asrun.stats(day)["items"],
          f"{len(rows)} rows / {app.asrun.stats(day)['items']} items")
    check("summary line carries items + chain state",
          "items" in app.asrun_summary_label.cget("text")
          and "chain intact" in app.asrun_summary_label.cget("text"),
          app.asrun_summary_label.cget("text"))
    # The tab must actually become the visible panel with laid-out rows.
    tab_index = app._tab_names.index("As-run")
    app._on_sidebar_tab_switch(tab_index, "As-run")
    app.update()
    check("the As-run tab becomes the shown panel",
          app._current_sidebar_tab == "As-run"
          and bool(app.tab_asrun.winfo_manager()))
    laid_out = app.asrun_rows_frame.winfo_children()
    check("rows request a real size (not collapsed widgets)",
          bool(laid_out) and all(w.winfo_reqwidth() > 100
                                 and w.winfo_reqheight() > 20
                                 for w in laid_out[:10]),
          f"{laid_out[0].winfo_reqwidth()}x{laid_out[0].winfo_reqheight()}"
          if laid_out else "no rows")
    check("day label shows the selected day",
          day in app.asrun_day_label.cget("text"),
          app.asrun_day_label.cget("text"))

    # ── the action row: a manual refresh, and a tooltip on every button ──
    action_row = app.asrun_refresh_btn.master
    buttons = list(action_row.winfo_children())
    labels = [b.cget("text") for b in buttons]
    check("the action row has a Refresh button first and five ops",
          labels[0] == "🔄 Refresh" and len(buttons) == 6, str(labels))
    tips = []
    for b in buttons:
        target = getattr(b, "_canvas", None) or b
        tips.append(bool(target.bind("<Enter>")) or bool(b.bind("<Enter>")))
    check("every action button explains itself on hover", all(tips),
          f"{dict(zip(labels, tips))}")
    check("the row still fits the sidebar",
          sum(b.winfo_width() for b in buttons) <= action_row.winfo_width(),
          f"{sum(b.winfo_width() for b in buttons)}px / {action_row.winfo_width()}px")
    app._asrun_last_store = ("stale", -1, -1)
    app.asrun_refresh_btn.invoke()
    app.update()
    check("Refresh pulls the list again (cache is dropped)",
          app._asrun_last_store != ("stale", -1, -1),
          str(app._asrun_last_store))

    app._asrun_step_day(-1)
    from datetime import datetime as _dnow, timedelta as _td
    yesterday = (_dnow.now() - _td(days=1)).strftime("%Y-%m-%d")
    check("day nav moves to yesterday", app._asrun_day == yesterday, app._asrun_day)
    app._asrun_goto_today()
    check("day nav returns to today", app._asrun_day == "")

    # ── export / seal / verify / prune through the real buttons ──
    out_csv = os.path.join(work, "export.csv")
    real_asksave = gui_mod.filedialog.asksaveasfilename
    gui_mod.filedialog.asksaveasfilename = lambda **k: out_csv
    try:
        app._asrun_export_csv()
    finally:
        gui_mod.filedialog.asksaveasfilename = real_asksave
    check("CSV button writes the day file",
          os.path.isfile(out_csv) and Path(out_csv).read_text(
              encoding="utf-8-sig").count("\n") > 1)
    app._asrun_seal_day()
    check("Seal button writes the daily manifest",
          Path(app.asrun.manifest_file(day)).exists())

    shown = {}
    real_info, real_warn = gui_mod.messagebox.showinfo, gui_mod.messagebox.showwarning
    gui_mod.messagebox.showinfo = lambda title, body="", **k: shown.update(
        {"t": title, "b": body})
    gui_mod.messagebox.showwarning = lambda title, body="", **k: shown.update(
        {"t": title, "b": body})
    try:
        app._asrun_verify_now()
        check("Verify button reports a clean chain",
              "✅" in shown.get("t", ""), str(shown))
        # make it dirty, then verify again
        line_path = Path(app.asrun.day_file(day))
        text = line_path.read_text(encoding="utf-8").splitlines()
        line_path.write_text("\n".join(text[:-1]) + "\n", encoding="utf-8")
        shown.clear()
        app._asrun_verify_now()
        check("Verify button reports a broken chain",
              "⚠️" in shown.get("t", "") and "manifest" in shown.get("b", ""),
              str(shown.get("t")) + " · " + str(shown.get("b", ""))[:80])
    finally:
        gui_mod.messagebox.showinfo, gui_mod.messagebox.showwarning = real_info, real_warn

    real_ask = gui_mod.messagebox.askyesno
    gui_mod.messagebox.askyesno = lambda *a, **k: False
    try:
        app._asrun_prune()
        check("Prune asks first and does nothing when declined",
              Path(app.asrun.day_file(day)).exists())
    finally:
        gui_mod.messagebox.askyesno = real_ask

    # ── retention floor through the entry ──
    app.asrun_keep_entry.delete(0, "end")
    app.asrun_keep_entry.insert(0, "5")
    app._asrun_apply_keep()
    check("keep-days entry refuses to go under the legal floor",
          app.asrun.keep_days == LEGAL_MIN_DAYS
          and app.asrun_keep_entry.get() == str(LEGAL_MIN_DAYS))

    # ── settings round-trip ──
    app.asrun_keep_entry.delete(0, "end")
    app.asrun_keep_entry.insert(0, "120")
    app._asrun_apply_keep()
    data = app._collect_settings()
    check("settings carry the as-run block",
          data.get("asrun", {}).get("enabled") is True
          and data["asrun"]["keep_days"] == 120, str(data.get("asrun")))
finally:
    try:
        app.destroy()
    except Exception:
        pass

# ── auto-seal: the station seals itself, without anyone pressing 🔒 ──
class _Clock(_real_datetime):
    """A settable clock, so "yesterday" can be written on purpose."""

    t = None

    @classmethod
    def now(cls, tz=None):
        return cls.t


auto_dir = os.path.join(work, "logs", "asrun_auto")
past_day = (seed_now := _real_datetime.now() - timedelta(days=1))
broken_day = seed_now - timedelta(days=2)
_real_module_datetime = asrun.datetime
asrun.datetime = _Clock
try:
    seed = AsRunLog(auto_dir, enabled=True, app_version="smoke")
    for offset, day in enumerate((broken_day, past_day)):
        _Clock.t = day.replace(hour=9, minute=0, second=0, microsecond=0)
        item = seed.begin("music", title=f"วันก่อน {offset}", duration=100.0,
                          path="C:/music/old.mp3")
        _Clock.t = _Clock.t + timedelta(seconds=100)
        seed.end(item, "natural", actual_duration=100.0)
finally:
    asrun.datetime = _real_module_datetime
past = past_day.strftime("%Y-%m-%d")
broken = broken_day.strftime("%Y-%m-%d")
# The broken day: one row edited, so its chain no longer matches anything.
broken_rows = Path(seed.day_file(broken)).read_text(
    encoding="utf-8").splitlines()
import json as _j  # noqa: E402
first_row = _j.loads(broken_rows[0])
first_row["item"]["title"] = "ถูกแก้หลัง Seal"
broken_rows[0] = _j.dumps(first_row, ensure_ascii=False)
Path(seed.day_file(broken)).write_text("\n".join(broken_rows) + "\n",
                                      encoding="utf-8")
# Writing yesterday teed off the module's own auto-seal, so the older day
# already has a manifest while yesterday does not — exactly the two states
# the app has to handle: one to seal, one it must never seal over.
broken_manifest_sha = hashlib.sha256(
    Path(seed.manifest_file(broken)).read_bytes()).hexdigest()
check("auto-seal fixture: yesterday is unsealed, the older day is sealed",
      not Path(seed.manifest_file(past)).exists()
      and Path(seed.manifest_file(broken)).exists(), f"{broken} {past}")
check("auto-seal fixture: the older day's chain is broken",
      seed.verify(broken, today=past)["state"] == "tampered",
      str(seed.verify(broken, today=past)["state"]))

app3 = RadioApp()
try:
    app3.asrun.configure(enabled=True, folder=auto_dir)
    app3._asrun_selected_day = lambda: past
    app3._refresh_asrun(force=True)
    warning = app3.asrun_summary_label.cget("text")
    check("the tab warns about days that were never sealed",
          "never sealed" in warning and past in warning,
          warning.replace("\n", " | ")[:160])
    app3._asrun_auto_seal()
    check("auto-seal seals the earlier day with no manifest",
          Path(app3.asrun.manifest_file(past)).exists(), past)
    check("auto-seal remembers it already ran today (the poller will not repeat it)",
          app3._asrun_seal_poll_day == _real_datetime.now().strftime("%Y-%m-%d"),
          str(getattr(app3, "_asrun_seal_poll_day", None)))
    app3._refresh_asrun(force=True)
    after = app3.asrun_summary_label.cget("text")
    check("once sealed, the warning is gone",
          "never sealed" not in after, after.replace("\n", " | ")[:160])
    check("the sealed day verifies clean",
          app3.asrun.verify(past, today=past)["state"] == "clean",
          str(app3.asrun.verify(past, today=past)["state"]))
    toasts = []
    app3._show_toast = lambda text="", **kw: toasts.append(text)
    app3._asrun_seal_all_days()
    check("the Seal-all button refuses the broken day",
          hashlib.sha256(Path(app3.asrun.manifest_file(broken))
                         .read_bytes()).hexdigest() == broken_manifest_sha)
    check("…and the toast names the day it refused",
          any(broken in t for t in toasts), str(toasts)[:160])
    check("the broken day is still reported as tampered",
          app3.asrun.verify(broken, today=past)["state"] == "tampered",
          str(app3.asrun.verify(broken, today=past)["state"]))
except Exception as err:
    check("auto-seal path ran without raising", False, repr(err))
finally:
    try:
        app3.destroy()
    except Exception:
        pass

app2 = RadioApp()
try:
    app2._restore_settings({"asrun": {"enabled": True, "keep_days": 200,
                                      "folder": os.path.join(work, "logs", "asrun")}})
    check("restore re-points the log and the switch",
          app2.asrun.enabled is True and app2.asrun.keep_days == 200
          and app2.asrun_switch_var.get() is True
          and app2.asrun_keep_entry.get() == "200",
          f"{app2.asrun.enabled} {app2.asrun.keep_days} {app2.asrun_switch_var.get()}")
    check("restore leaves the switch OFF when the file says so",
          (app2._restore_settings({"asrun": {"enabled": False}})
           or app2.asrun.enabled is False))
    app2._restore_settings({"asrun": {"enabled": True}})
    app2._asrun_begin("music", title="ค้างไว้ตอนปิด")
    check("an item can be left open", app2.asrun.active is not None)
    app2._on_close()
    items2 = app2.asrun.read_day()
    check("closing the app closes the open item as 'stopped'",
          items2 and items2[-1]["ended_why"] == "stopped",
          str([(i["title"], i["ended_why"]) for i in items2[-1:]]))
    check("closing the app seals today's manifest",
          Path(app2.asrun.manifest_file()).exists())
except Exception as err:
    check("close path ran without raising", False, repr(err))
    try:
        app2.destroy()
    except Exception:
        pass

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
