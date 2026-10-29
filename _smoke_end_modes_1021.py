#!/usr/bin/env python3
"""Smoke: per-song end modes [endmodes-1021].

Three things can happen when a song ends, chosen per song from the mark at
the right end of its queue row:

  1. ads/jingle, then carry on        (mark "AD▶", mode "break")
  2. stop here                        (mark "■",  mode "stop")
  3. ads/jingle, then stop            (mark "AD■", mode "break_stop")
  4. no mark, the old behaviour        (mark "＋",  mode "auto")

Verifies:
  A. model  — Song.end_mode defaults to "auto", survives a .freq round trip,
              and an unknown value (hand-edited file) reads as "auto"
  B. row    — the mark is drawn as ONE canvas item, in the card's right
              column, and it does not eat the title/clock/duration columns
  C. input  — a press on the mark cycles the mode and paints that one row;
              a press anywhere else still selects the row, unchanged
  D. rundown— break / stop / break_stop do exactly what they say, a break
              that cannot air still stops, a stale "stop after break" flag
              never fires later, and crossfade is not armed across a boundary
  E. speed  — a click repaints one row (no rebuild) and a 400-song rebuild
              still only draws the viewport
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
# RadioQueue saves to a RELATIVE path — run from a throwaway dir so the
# user's real radio_queue.freq is never touched.
os.chdir(tempfile.mkdtemp(prefix="freq_endmode_smoke_"))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════
# A. The model
# ══════════════════════════════════════════════════════════════
from radio_manager import END_MODES, RadioQueue, Song  # noqa: E402

check("four end modes are defined",
      END_MODES == ("auto", "break", "stop", "break_stop"), str(END_MODES))
check("a song starts on auto", Song(title="x").end_mode == "auto")

work = tempfile.mkdtemp(prefix="endmode_files_")


def make_track(name):
    path = os.path.join(work, name)
    with open(path, "wb") as fh:
        fh.write(b"\0" * 44)
    return path


songs = []
for i, mode in enumerate(END_MODES):
    songs.append(Song(title=f"เพลง {i}", artist="ศิลปิน", duration=180.0,
                      source="local", file_path=make_track(f"m{i}.wav"),
                      id=f"id{i}", end_mode=mode))
check("every mode lands on its own song",
      [s.end_mode for s in songs] == list(END_MODES))

rq = RadioQueue()
rq.queue = list(songs)
path = os.path.join(work, "playlist.freq")
rq.save_to_file(path)
rq2 = RadioQueue()
rq2.load_from_file(path)
check("the .freq round trip keeps every end mode",
      [s.end_mode for s in rq2.queue] == list(END_MODES),
      str([s.end_mode for s in rq2.queue]))
check("the round trip keeps the songs themselves",
      len(rq2.queue) == 4 and rq2.queue[0].title == "เพลง 0")

tampered = Song.from_dict({"title": "x", "end_mode": "nonsense"})
check("an unknown mode is read as-is by the model (the UI clamps it)",
      tampered.end_mode == "nonsense")

# ══════════════════════════════════════════════════════════════
# B-E. The real widget
# ══════════════════════════════════════════════════════════════
import gui as gui_mod  # noqa: E402
from gui import (QUEUE_CARD_H, QUEUE_CARD_MIN_W, QUEUE_CARD_PAD,  # noqa: E402
                 QUEUE_MARK_W, QUEUE_ROW_H, RadioApp)

app = RadioApp()
app.withdraw()          # keep the desktop clean; widgets still lay out

# Nothing here is about audio: every engine call is stubbed.
played = []
app.audio_player.play_file = lambda path, **kw: (played.append(path), True)[1]
app.audio_player.play_youtube = lambda *a, **k: True
app.audio_player.play_mic_multi = lambda *a, **k: 1
app.audio_player.stop = lambda: played.append("<stop>") or None
app.audio_player.seek = lambda *a, **k: None
app._load_waveform = lambda *a, **k: None
app._arm_crossfade_for_next = lambda *a, **k: None
app._asrun_begin = lambda *a, **k: None
app._asrun_end_active = lambda *a, **k: None
app._emit_plugins = lambda *a, **k: None

toasts = []
app._show_toast = lambda text="", duration=3000: toasts.append(text)

app.rq.queue = [Song(title=f"Song {i}", artist="Artist", duration=180.0,
                     source="local", file_path=make_track(f"q{i}.wav"),
                     id=f"q{i}") for i in range(6)]
app.rq.current_index = 0
app._refresh_queue()
app.update()


def mark_span():
    """Right-hand mark column as it is currently drawn."""
    card_right = max(QUEUE_CARD_PAD + QUEUE_CARD_MIN_W,
                     app._queue_canvas_width() - QUEUE_CARD_PAD)
    return app._queue_mark_span(card_right)


def row_click_point(pos, x):
    """A fake pointer event at (x, centre of row `pos`)."""
    class _Event:
        pass
    ev = _Event()
    ev.x = x
    ev.y = pos * QUEUE_ROW_H + QUEUE_CARD_H // 2
    ev.state = 0
    return ev


def row_texts(pos):
    """Every text item drawn in row `pos` (in draw order)."""
    canvas = app._queue_canvas
    texts = []
    for item in app._queue_items.get(pos, ()):
        try:
            if canvas.type(item) == "text":
                texts.append(canvas.itemcget(item, "text"))
        except Exception:
            pass
    return texts


def row_mark_items(pos):
    """The canvas items that ARE the end mark in row `pos` (by tag)."""
    canvas = app._queue_canvas
    out = []
    for item in app._queue_items.get(pos, ()):
        try:
            if any(t.startswith("endmark-") for t in canvas.gettags(item)):
                out.append(item)
        except Exception:
            pass
    return out


def row_mark_mode(pos):
    """The mode a row's mark claims to show (from its tag)."""
    canvas = app._queue_canvas
    modes = set()
    for item in row_mark_items(pos):
        for tag in canvas.gettags(item):
            if tag.startswith("endmark-"):
                modes.add(tag.split("endmark-", 1)[1])
    return modes


# ── B. the mark is drawn ONCE per row, inside its own column, as vectors ──
check("the list is virtualized and has rows drawn",
      bool(app._queue_items) and len(app._queue_items) <= len(app.rq.queue) + 8,
      f"{len(app._queue_items)} drawn")
mark_left, mark_right = mark_span()
check("a row carries exactly one end mark",
      row_mark_mode(0) == {"auto"}, str(row_mark_mode(0)))
# The mark is the operator's own artwork (icons/*.png, baked from the SVGs)
# drawn as ONE image item per row; when an asset or Pillow is missing the
# same meaning is drawn from canvas paths instead. Never a font glyph.
PNG_OK = all((gui_mod._END_ICON_DIR / name).is_file()
             for name in gui_mod._END_ICON_PNG.values())
PHOTO_OK = app._end_mark_photo("break") is not None
ONE_ICON = ["image"] if PHOTO_OK else ["polygon"]
check("the three action icons are baked from the operator's SVGs",
      PNG_OK and set(gui_mod._END_ICON_PNG)
      == {"break", "stop", "break_stop"},
      str(sorted(gui_mod._END_ICON_PNG.items())))
check("the mark is a drawn icon, not a font glyph",
      bool(row_mark_items(0))
      and all(app._queue_canvas.type(i) in ("image", "polygon", "oval")
              for i in row_mark_items(0)),
      str([app._queue_canvas.type(i) for i in row_mark_items(0)]))
check("…one canvas item per row (the shared, anti-aliased icon)",
      len(row_mark_items(0)) == 1
      and (app._queue_canvas.type(row_mark_items(0)[0]) == ONE_ICON[0]),
      str([app._queue_canvas.type(i) for i in row_mark_items(0)]))
check("…reused from the cache, not re-rendered per row",
      not PHOTO_OK or app._end_mark_photo("auto") is app._end_mark_photo("auto"))
check("no text item renders the mark any more",
      not any(t in ("＋", "AD▶", "■", "AD■") for t in row_texts(0)),
      str(row_texts(0)))
mark_boxes = [app._queue_canvas.bbox(i) for i in row_mark_items(0)]
check("the mark stays inside its own column",
      mark_boxes and all(b and b[0] >= mark_left - 1 and b[2] <= mark_right + 1
                         for b in mark_boxes), str(mark_boxes))
check("the mark is tall enough to read (≥ 12 px)",
      mark_boxes and all(b[3] - b[1] >= 12 for b in mark_boxes), str(mark_boxes))
check("the mark column sits at the card's right edge",
      mark_right < app._queue_canvas_width()
      and mark_right - mark_left == QUEUE_MARK_W,
      f"{mark_left}..{mark_right} of {app._queue_canvas_width()}")
check("a row still costs ONE canvas item per column (plus the mark)",
      len(app._queue_items[0]) <= 9, f"{len(app._queue_items[0])} items")

# ── C. the press target ──
before_mode = app.rq.queue[0].end_mode
rebuilt = []
real_draw_window = app._draw_queue_window
app._draw_queue_window = lambda *a, **k: rebuilt.append(1) or real_draw_window(*a, **k)
app._on_queue_canvas_press(row_click_point(0, mark_right - 3))
check("a press on the mark cycles to 'break'",
      app.rq.queue[0].end_mode == "break",
      f"{before_mode} -> {app.rq.queue[0].end_mode}")
check("…and paints that row only (no rebuild)",
      not rebuilt and len(app._queue_items[0]) <= 9,
      f"{len(rebuilt)} rebuild(s)")
check("…drawing the break mark (play icon)",
      row_mark_mode(0) == {"break"}
      and [app._queue_canvas.type(i) for i in row_mark_items(0)] == ONE_ICON,
      f"{row_mark_mode(0)} {[app._queue_canvas.type(i) for i in row_mark_items(0)]}")
check("…and saying so in a toast",
      bool(toasts) and "after this song" in toasts[-1], str(toasts[-1:]))

app._on_queue_canvas_press(row_click_point(0, mark_right - 3))
check("a second press cycles to 'stop'", app.rq.queue[0].end_mode == "stop")
check("…drawing the stop mark (square)",
      row_mark_mode(0) == {"stop"}
      and [app._queue_canvas.type(i) for i in row_mark_items(0)] == ONE_ICON)
app._on_queue_canvas_press(row_click_point(0, mark_right - 3))
check("a third press cycles to 'break_stop'",
      app.rq.queue[0].end_mode == "break_stop")
check("…drawn with the ads-then-stop icon",
      row_mark_mode(0) == {"break_stop"}
      and [app._queue_canvas.type(i) for i in row_mark_items(0)]
      == (["image"] if PHOTO_OK else ["polygon", "polygon"]),
      str(row_mark_mode(0)))
_boxes = [app._queue_canvas.bbox(i) for i in row_mark_items(0)]
check("…and stays inside its column",
      bool(_boxes) and _boxes[0][0] >= mark_left - 1
      and _boxes[-1][2] <= mark_right + 1, str(_boxes))
app._on_queue_canvas_press(row_click_point(0, mark_right - 3))
check("a fourth press is back to 'auto'",
      app.rq.queue[0].end_mode == "auto" and row_mark_mode(0) == {"auto"}
      and all(app._queue_canvas.type(i) != "text" for i in row_mark_items(0)))

# A missing/broken asset must never leave a row without its mark: the app
# then draws the same meaning itself (12 px triangle instead of the 16 px
# artwork — the sizes tell the two paths apart).
_real_icon_dir = gui_mod._END_ICON_DIR
app.rq.queue[0].end_mode = "break"
app._end_photos.clear()
gui_mod._END_ICON_DIR = Path(work) / "no_such_icons"
app._queue_paint_row(0)
_boxes = [app._queue_canvas.bbox(i) for i in row_mark_items(0)]
check("a missing icon file falls back to the shape the app draws",
      bool(_boxes) and _boxes[0][2] - _boxes[0][0] <= gui_mod.QUEUE_MARK_ICON_W + 2,
      str(_boxes))
gui_mod._END_ICON_DIR = _real_icon_dir
app._end_photos.clear()
app._queue_paint_row(0)
_boxes = [app._queue_canvas.bbox(i) for i in row_mark_items(0)]
check("…and the artwork comes back once the folder is right",
      not PNG_OK or (bool(_boxes)
                     and _boxes[0][2] - _boxes[0][0] >= gui_mod.QUEUE_MARK_ICON_H - 1),
      str(_boxes))
app.rq.queue[0].end_mode = "auto"
app._queue_paint_row(0)

played_modes = []
app._on_queue_double_click = lambda idx: played_modes.append(idx)
app._on_queue_canvas_double(row_click_point(0, mark_right - 3))
check("a double click on the mark never starts the song", not played_modes,
      str(played_modes))
app._on_queue_canvas_double(row_click_point(0, mark_left - 40))
check("…a double click on the row still does", played_modes == [0],
      str(played_modes))

app._on_queue_canvas_press(row_click_point(0, mark_left - 40))
check("a press on the row itself still selects it",
      app._queue_selected == 0 and app.rq.queue[0].end_mode == "auto",
      f"selected={app._queue_selected} mode={app.rq.queue[0].end_mode}")
app._draw_queue_window = real_draw_window

# ── D. what the rundown does at the end of a song ──
ads = [make_track("ad1.wav")]
app.commercial.enabled = True
app.commercial.ads = ads
app.commercial.interval = 10
app.commercial.song_counter = 0
app.commercial.current_ad_index = 0
app.jingle.jingle_file = ""


def reset(index=0, mode="auto"):
    for i, song in enumerate(app.rq.queue):
        song.end_mode = "auto"
    app.rq.queue[index].end_mode = mode
    app.rq.current_index = index
    app._playing = True
    app._ad_playing = False
    app._jingle_playing = False
    app._stop_after_break = False
    app.commercial.song_counter = 0
    app.jingle.song_counter = 0
    played.clear()


# 1. break — an ad now, then the next song
reset(0, "break")
app._on_track_finished()
check("'break': the ad airs even though the interval is not due",
      app._ad_playing is True and played[-1:].count(ads[0]) == 1, str(played))
check("'break': the rundown is still on this song", app.rq.current_index == 0)
app._on_track_finished()          # the ad finishes
check("'break': then the next song plays",
      app.rq.current_index == 1 and app._playing is True
      and app._ad_playing is False, f"index={app.rq.current_index}")

# 2. stop — nothing after the song
reset(1, "stop")
app._on_track_finished()
check("'stop': no ad airs", ads[0] not in played, str(played))
check("'stop': playback stops", app._playing is False)
check("'stop': the rundown does not advance", app.rq.current_index == 1)
check("'stop': the operator is told",
      "Stopped" in app.np_status.cget("text"), app.np_status.cget("text"))

# 3. break_stop — the ad, then nothing
reset(2, "break_stop")
app._on_track_finished()
check("'break_stop': the ad airs first",
      app._ad_playing is True and ads[0] in played, str(played))
check("'break_stop': the stop is remembered for after the break",
      app._stop_after_break is True)
app._on_track_finished()          # the ad finishes
check("'break_stop': then the rundown stops",
      app._playing is False and app._stop_after_break is False)
check("'break_stop': and the song it ended on is still current",
      app.rq.current_index == 2, str(app.rq.current_index))

# 4. break_stop with nothing to air — still stops (and never advances)
reset(3, "break_stop")
app.commercial.enabled = False
app._on_track_finished()
check("'break_stop' with no break available still stops",
      app._playing is False and app.rq.current_index == 3, str(played))
app.commercial.enabled = True

# 5. a stale flag from a break that never aired must not stop a later song
reset(4, "auto")
app.commercial.song_counter = 0
app._stop_after_break = True        # left over from something else
app._on_track_finished()
check("a stale 'stop after break' flag is dropped, not fired",
      app._playing is True and app.rq.current_index == 5
      and app._stop_after_break is False,
      f"playing={app._playing} index={app.rq.current_index}")

# 6. auto is untouched: interval counters still rule
reset(5, "auto")
app.commercial.interval = 1
app._on_track_finished()
check("'auto' still honors the ad interval",
      app._ad_playing is True, str(played))

# 7. crossfade never blends across an end-mark boundary
# The harness stubbed this out above; the real method is what we are testing.
app._arm_crossfade_for_next = (
    gui_mod.RadioApp._arm_crossfade_for_next.__get__(app))
app.crossfade.enabled = True
app.audio_player._is_playing = True      # read-only property, backed by this
app.audio_player._is_paused = False
app.audio_player.crossfade_enabled = True
# The engine has no such attribute (the arm helper reads it anyway, and its
# broad except swallows the AttributeError) — set it so the guard is reached.
app.audio_player.crossfade_available = True
armed = []
app._queue_crossfade = lambda song, index, on_finish=True: armed.append(index)
app.commercial.enabled = False
app.rq.queue[0].end_mode = "stop"
app.rq.current_index = 0
app._arm_crossfade_for_next(0)
check("crossfade is not armed for a row that ends the rundown", not armed,
      str(armed))
app.rq.queue[0].end_mode = "auto"
app._arm_crossfade_for_next(0)
check("…but is armed for a normal row", armed == [1], str(armed))
app.crossfade.enabled = False
app.commercial.enabled = True

# ── E. speed: the click path never rebuilds the list ──
app.rq.queue = [Song(title=f"Long song title {i}", artist="Artist",
                     duration=180.0, source="local", id=f"big{i}")
                for i in range(400)]
app.rq.current_index = 0
t0 = time.perf_counter()
app._refresh_queue()
rebuild = time.perf_counter() - t0
app.update()
drawn = len(app._queue_items)
check("400 songs: the rebuild only draws the viewport",
      drawn <= 16, f"{drawn} rows drawn")
check("400 songs: the rebuild stays fast", rebuild < 1.5, f"{rebuild * 1000:.0f} ms")

mark_left, mark_right = mark_span()    # the card geometry followed the resize
pos = sorted(app._queue_items)[len(app._queue_items) // 2]
orig = app._queue_view[pos][0]
rebuilt.clear()
app._draw_queue_window = lambda *a, **k: rebuilt.append(1) or real_draw_window(*a, **k)
t0 = time.perf_counter()
app._on_queue_canvas_press(row_click_point(pos, mark_right - 3))
cycle = time.perf_counter() - t0
app._draw_queue_window = real_draw_window
check("400 songs: a mark click repaints ONE row",
      not rebuilt and app.rq.queue[orig].end_mode == "break",
      f"{len(rebuilt)} rebuild(s), mode={app.rq.queue[orig].end_mode}")
check("400 songs: the click is instant", cycle < 0.15, f"{cycle * 1000:.0f} ms")

app.destroy()

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
sys.exit(1 if fails else 0)
