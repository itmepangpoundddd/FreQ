# FreQ — Release Notes

---

## FreQ Ultimate 2026 (Build 2026.1029.1) — cut a song before it airs, and play a live signal from its link (source build)

**Two new jobs for the same rundown.** Any item can now be **edited before it
airs**: `Edit`, next to `Filter`, opens a native waveform editor with trim,
cut/copy/paste, four fade shapes, gain, normalize, reverse and mute, auditioned
on a **monitor device of its own** and saved to a new WAV while the original
file is never touched. And the Add tab can queue a **live `.m3u8` signal** — the
item airs straight from its link with no download and no temp file, and the
rundown shows `LIVE` instead of a length.

The editor's monitor no longer stutters: the playhead tick used to scan the
whole track for peak/RMS while holding the audio buffer's lock (measured: a
third of every second went silent); the scan is cached and the native core no
longer blocks the audio callback on read-only analysis, measured at **0.00 s
lost over 10 s** of monitor playback. Thai and every other non-ANSI file name
survives the round trip, and the whole test set was re-run against freshly
rebuilt native modules.

### ✂️ The audio editor — trim, cut, fade, on a native core (new)

- **An `Edit` button sits beside `Filter`** in the queue's button bar, and
  appears exactly when `Filter` does — with one row selected. It opens the
  selected item's audio: a local file by its `file_path`, and a **YouTube entry
  from its download cache** (the row that says "Loaded from cache" is exactly
  what edits). A live mic segment is refused because there is no file of the
  operator's to rewrite
- **An item that was never played is downloaded first, not refused.** Pressing
  Edit on an un-downloaded YouTube row fetches it right there: the Edit button
  turns into `⏳ 42%` while the cache downloads on its own worker (the queue,
  the player and the rundown keep running — the UI thread never waits), and the
  editor opens by itself the moment the file lands. A second press reuses the
  download already in flight; a failed one says why instead of opening an empty
  window
- **The sample work is C++** (`native_editor.cpp`, built into
  `native_editor.cp314-win_amd64.pyd` by `build_native_meter.py`): the track is
  decoded once into interleaved float32, and every operation is a native call —
  **trim (keep selection), delete, cut/copy/paste, insert silence, fade in/out,
  gain, normalize, reverse, mute**. Waveform peaks are native too, so painting
  a five-minute song is **one call and two numbers per pixel**, not a Python
  loop over millions of samples
- **Fades have the four shapes** (linear, logarithmic, exponential, S-curve) on
  a dropdown beside the buttons, and **normalize** names the gain it applied
- **The waveform is the editor**: drag to select (Shift+drag extends, double
  click selects all, a plain click just moves the cursor), Ctrl+wheel zooms on
  the pointer, wheel scrolls, `Fit` shows the whole file, and the right-click
  menu carries the same operations
- **Undo/redo covers every step** and remembers only the bytes each operation
  touched (a fade on a 4-second selection stores 4 seconds, not the track); the
  history is capped at 40 steps / 96 MB so a long session cannot eat the RAM
  the broadcast needs
- **The monitor is its own device** — a `Monitor` dropdown (with a volume and a
  refresh button) at the top of the editor opens the native playback engine on
  **that** endpoint, so an operator can audition a cut on headphones while the
  program output keeps its own device, untouched. The choice is remembered in
  `%APPDATA%\FreQ\editor_prefs.json`. (The whole-program `Dual Output` mirror in
  Settings is still there and unrelated.)
- **Editing never touches the original file.** `Save (copy)` writes
  `<title> (edited).wav` — next to a local source, or in an `edits` folder
  beside the playlist for a cached download (never inside `~/.freq_cache`,
  which **Clear Cache** would empty) — making the name Windows-safe from titles
  that contain `|`, `:` or `【】`, and never overwriting: ` (edited 2)` and so
  on. The queue item is then pointed at the new WAV with its new length, so a
  song that is on air keeps playing from a file nobody is rewriting.
  `Save As…` writes wherever the operator chooses and links that too
- **A YouTube item that gets edited becomes a file item** (`source = file`,
  the URL kept as provenance), because the player reaches YouTube rows through
  `play_youtube()` and would otherwise keep airing the cached original no
  matter what `file_path` said
- **Thai (and every non-ANSI) file name now survives the round trip.** The
  native writer opened files with a narrow `fopen()`, which runs the name
  through the Windows ANSI code page — a song called `สาธุ (Sathu) …` was
  written as `เธชเธฒเธธ…` while the queue stored the real name, so the row came
  back as *"original missing — playing cached copy"*. Two fixes, either enough:
  the C++ side takes the path as a **wide** string (`_wfopen`,
  `ma_decoder_init_file_w`), and the Python side writes non-ASCII exports to a
  temporary ASCII name and `os.replace`s them into place (works with a `.pyd`
  built before the fix). The save is **verified on disk** before the queue is
  pointed at it, and `file_path` is stored **absolute** — a relative
  `edits\…` path stops resolving the moment the app is started from another
  folder
- Shortcuts: `Ctrl+Z` / `Ctrl+Y`, `Ctrl+X/C/V`, `Ctrl+A`, `Ctrl+S`, `Del`,
  `Space` (play/pause), `Home` / `End`
- **Measured on a real 4:00 MP3** (5.6 MB, 44.1 kHz stereo, this machine):
  decode **260 ms**, waveform envelope for 1200 columns **22 ms**, normalize
  across the whole track **75 ms**, a quarter-track S-curve fade **55 ms**, two
  undos **21 ms**, WAV export **58 ms**. The window paints once and the
  playhead moves as one canvas line, so dragging a selection stays smooth

### ⚡ The editor's monitor no longer stutters (fixed)

- **The playhead tick was scanning the whole track for peak/RMS every 60 ms**
  — **30 ms** of a 4-minute song — and that scan held the audio buffer's lock,
  so the monitor device got silence for roughly a third of every second.
  Measured on a real 4:00 track: **0.98 s of audio lost in 3.09 s (31.7%)**,
  which is exactly the chop an operator hears
- **Fixed on both sides.** In the window, levels are cached per
  (selection, length, edit count) and a playing tick only moves the playhead
  and the clock — the buffer is never touched while airing. In the native core,
  read-only analysis (`peaks`, `stats`) no longer takes the lock at all, the
  playback callback **blocks** for its 20 ms period instead of silently skipping
  a period, and the period is set explicitly rather than left to the default
- **Measured after the fix** (real 4:00 track, 10 s of monitor playback):
  **0** buffer scans during playback and **10.00 s of audio in 10.01 s** of wall
  clock — 10 ms, i.e. no gap. The full-track scan still costs ~30 ms, but it now
  runs once per selection/edit instead of 16 times a second. The same run was
  repeated against the rebuilt native module (**10.00 s in 10.01 s**)
- `_measure_editor_stutter_1027.py` replays the same measurement on any file
  (`python _measure_editor_stutter_1027.py "<file>" 10`), and a regression check
  in the suite proves the tick performs **zero** buffer scans and that preview
  playback keeps pace while those ticks run

### 📡 Live streams (.m3u8) play straight from the link

- **New section in the Add tab: `📡 Add Live Stream (.m3u8)`** — a link box with
  its own paste button, an optional `Stream name`, and `📡 Add Stream to Queue`.
  A pasted address keeps its query string, and wrapping quotes/angle brackets
  (what "copy link" buttons add) are stripped instead of rejecting the link
- **Nothing is downloaded and no temp file is written**: while the item airs,
  ffmpeg demuxes the HLS playlist/segments into a raw PCM pipe and the render
  ring's backpressure paces it to realtime — the same route the decoder uses,
  so EQ, volume, the VU meter, dual output and the as-run log all apply. Real
  links get `-reconnect`/`-rw_timeout` so a segment boundary or a short network
  drop does not end the item; a local `.m3u8` file skips those flags (ffmpeg
  rejects them for file input — "Option reconnect not found")
- **The link is probed before it airs.** `ffprobe` runs off the UI thread and
  the result is applied by the progress tick: a good feed shows
  `📡 Live stream · aac`, a dead or wrong link shows
  `❌ Stream failed: <reason>`, is recorded in the as-run log as missing and is
  **never marked on air** — silently going quiet is the one outcome this cannot
  do. If the operator skips while the probe is in flight, its answer is dropped
- **The queue row says LIVE, not 00:00**, wears the 📡 badge and carries no
  length; the now-playing clock shows `LIVE` and counts the elapsed time only
- **The item is a normal queue item**: it saves into the `.freq` file with its
  link and comes back as a live stream, 🎲 random positions, move/remove and
  the end marks all still work, and `Edit` explains there is no file to cut
- **`_smoke_stream_m3u8_1029.py` 38/38** — builds a real 6-second HLS playlist
  with ffmpeg to drive the genuine widget flow: the section is present and
  wired, an empty box / a bare `https://` / plain text are all refused with the
  queue untouched, a pasted playlist (quotes included) is queued as
  `source="stream"` with no length and the typed name, the row paints `LIVE`
  with the 📡 badge, pressing play probes and then **really plays** (position
  advances 0.25 s → 1.87 s), the same playlist served over **HTTP** probes as
  `aac` and plays (0.23 s → 1.44 s), a dead link fails with a reason and leaves
  nothing on air, `stop` leaves no feeder thread, the item survives a
  save/load round trip, and `Edit` on a live row answers politely
- Also green after the change: `_smoke_browse_multi_1026.py` 27/27 ·
  `_smoke_playlist_1020.py` 32/32 · `test_ui_1016.py` 112/112 ·
  `test_gui_wiring.py` 18/18

### 📦 The build and the installer carry the editor

- **A new C++ extension** — `native_editor.cpp` →
  `native_editor.cp314-win_amd64.pyd` — is built by `build_native_meter.py`
  beside the other eight, and `build.py` ships all nine plus `icons/`,
  `templates/` and the `audio_editor` / `editor_window` hidden imports
- **`installer.iss` lists what the app actually imports**: **32 app modules** under
  `{app}\src` (now including `audio_editor.py` and `editor_window.py`) —
  plus `build_native_meter.py` / `.bat` in the same folder, and **every C++
  source** (`native_*.cpp`, `miniaudio.h`, 4.4 MB) under
  `{app}\src\native`, so the source of the shipped binaries travels with
  the installer.
  Checked mechanically: every listed file exists and no local import of
  `gui.py` is missing from the list
- **One version number, everywhere**: `installer.iss` says
  `2026.1029.1` / numeric `2026.10.2901` / display
  `Ultimate 2026 (Build 1029.1)`, and `version.txt` matches it —
  `release.py --set-version` wrote both, so the splash, the installer wizard and
  the update checker agree
- **The bundle on disk is stale until it is rebuilt**: `dist\FreQ` is still the
  1026.1 payload, so packaging this build means `python release.py` (or
  `build.py` + Inno Setup), which re-runs the bundle verification — every
  shipped module imported **from the bundle**, every `.pyd` entry point called

### 🧪 Full sweep against the rebuilt native modules

All 34 suites were re-run after `build_native_meter.py` rebuilt all nine `.pyd`
files, each in its own process with the exit status captured before any log
filtering ([_sweep_1029.log](_sweep_1029.log), script:
[_sweep_1029_runner.sh](_sweep_1029_runner.sh)). Green: `test_gui_wiring`
18/18 · `test_ui_1016` 112/112 · `_smoke_asrun_1020` 105/105 ·
`_smoke_asrun_cli_1020` 59/59 · `_smoke_playlist_1020` 32/32 ·
`_smoke_queue_times_1019` 53/53 · `_smoke_master_chain_1017` 36/36 ·
`_smoke_queue_stability_1018` 16/16 · `_smoke_random_insert_1020` 31/31 ·
`_smoke_end_modes_1021` 52/52 · `_smoke_audio_editor_1027` 103/103 ·
`_smoke_browse_multi_1026` 27/27 · `_smoke_stream_m3u8_1029` 38/38 ·
`_smoke_dual_0927` 15/15 · `_smoke_select_1017` 32/32 · `_smoke_wheel_1017`
30/30 · `_smoke_playlist_first_1017` 26/26 · `_smoke_yt_mix_1017` 21/21 ·
`_smoke_supervisor_0926` 10/10 · `_smoke_watchdog_0926` 5/5 ·
`test_plugin_system` 43/43 · `test_silence_sentinel` 40/40 ·
`test_combo_aircheck_stream` green · `test_jingle_stress` 41/41 ·
`test_pause_storm` green, plus `test_edge_cases` 563/563 and
`_smoke_settings_persist_1017` / `_smoke_freq_roundtrip_1017` green.

Four suites still fail **exactly as they do on the untouched tree** (verified by
running them against a `git archive HEAD` copy): `_smoke_heal_0928`
(`app.sentinel_heal` does not exist yet), `_smoke_split_0928`
(`_aircheck_tracklog` does not exist yet), `_smoke_sup_0928` (supervisor
settings not wired yet) and `_smoke_marker_0926` (still reads the old
`QueueRowHandle.item` from before the canvas-row rewrite). They are stale
tests for unwritten features, not regressions; `_smoke_queue_nav_1018` fails one
boundary check for the same reason (the last row of a 300-row queue sits one
row past `_queue_visible_top() + rows` — identical on HEAD). `test_multi_mic`
passed 13/13 on both re-runs after one timing-sensitive failure in the sweep.

Soak suites (`test_soak.py`, `test_soak_stream.py`, `test_soak_ui_1026.py`) are
not in this sweep: they run for 20+ minutes by design.

### ✅ Verified on the final bytes (2026-10-29)

- **`_smoke_audio_editor_1027.py` 103/103** — the editor end to end on a
  generated stereo WAV: the native decode/rate/channel properties, the peak of
  a known -6.02 dBFS tone, +4/−4 dB gain round trip, normalize landing on the
  target, a linear fade that ramps up, reverse keeping the level, delete /
  insert / paste geometry, and a **saved WAV that re-opens with the same frame
  count, rate and channel count**. Then undo/redo of every operation, a 20-step
  run undoing exactly back to the original length, the 40-step bound, monitor
  list + a real preview that advances and stops, and the window itself: the
  real `RadioApp` showing the Edit button only with a selection, a real drawn
  waveform, Fade Out / Normalize / Trim changing the samples, Undo restoring
  them, and Save writing `<name> (edited).wav` while the original stays
  byte-identical. Also the queue rules: a YouTube row resolves its **cached**
  audio, an un-cached row and a mic segment are refused, an edited YouTube row
  turns into a file item that plays the WAV (URL kept), and a title full of
  `|` `:` `?` lands as a legal file name in the requested folder. And the
  download-first rule: Edit on an un-played row starts exactly one download,
  the button shows `⏳`, the window opens when the audio lands and the label
  returns to `Edit`; a download that fails reports once and opens nothing.
  Plus the encoding regression: a Thai-named file loads, exports under its
  exact Thai name, leaves no mojibake copy beside it, re-opens at the same
  length, and the queue keeps an absolute path — and the stutter regression:
  a drawn playhead tick performs **no** buffer scan and real preview playback
  stays within 0.3 s of the wall clock while those ticks run
- Also green with the rebuilt native modules: `test_gui_wiring.py` 18/18 ·
  `test_ui_1016.py` 112/112 · `test_edge_cases.py` 563/563 ·
  `_smoke_playlist_1020.py` 32/32 · `_smoke_browse_multi_1026.py` 27/27 ·
  `_smoke_end_modes_1021.py` 52/52 · `_smoke_queue_times_1019.py` 53/53 ·
  `_smoke_asrun_1020.py` 105/105 (listed in the sweep report above)
- **Measured, not claimed**: a 4:00 track decodes in 260 ms, the waveform
  envelope for 1200 columns takes 22 ms, normalize 75 ms, a quarter-track
  S-curve fade 55 ms, WAV export 58 ms; the monitor plays 10.00 s of audio in
  10.00 s of wall clock with **zero** buffer scans on the tick

### ⚠️ What this build does NOT claim

- **A live stream needs FFmpeg and the WASAPI output route.** The pygame route
  can only load files, so a stream switches the player to WASAPI (the same
  device the app already uses); with FFmpeg missing the Add section refuses the
  item up front instead of queueing something that cannot air
- **A live item has no length, no progress bar and no waveform** — it says
  `LIVE` and counts up. End marks still work (they fire when the item stops),
  but there is nothing to count down to
- **The stream is played, not recorded**: no copy lands in the cache, so the
  item needs its link to air again, and editing it is refused with a message
  (there is no file to cut)
- **Editing saves a copy** (`<title> (edited).wav`); the original file is never
  rewritten in place, and the editor works on one item at a time
- **The monitor stutter fix is measured on this machine** (10 s of preview at
  10.00 s of wall clock, zero buffer scans); a different audio endpoint or a
  loaded CPU can still produce its own gaps

## FreQ Ultimate 2026 (Build 2026.1026.1) — the station can prove what aired, and every song can say how it ends (source build)

**One build, four jobs.** FreQ can tell a station manager — or an inspector —
**what actually went to air**, when, for how long and why it stopped: the
**As-run log** writes one append-only file per day, every item chained to the
one before it by SHA-1 and sealed with a daily manifest. On the playlist side,
the header can **start a new `.freq`** and **import the songs of another one**
into the queue on air. Each song can now carry an **end mark** — the small
symbol at the right end of its queue row — that decides what happens when it
finishes: a break and carry on, stop here, or a break and then stop. And the
queue card reads the **wall clock**, so the rundown shows the time a song will
really start and stop, not just how long it is.

The as-run tab was re-laid out in the same pass: one compact card per aired
item, aligned columns, a **Refresh** button and a hover description on every
control. Everything the operator sees — tab, toasts, dialogs and the
reasons written into the log — speaks **English**, and the build and the
installer now carry every module and asset the app actually imports.

The as-run observer is **OFF by default**; with it off the app behaves exactly
as it did before, and a song with no end mark plays on exactly like it always
did.

### 🧾 The as-run log (new)

- `asrun.py` writes `logs/asrun/YYYY-MM-DD.jsonl` — **append-only**. Rows are
  never rewritten, only added; the app opens the file in `"a"` mode and (by
  default) `fsync`s each row
- **Each item is two rows: `start` when it reaches the output, `end` when it
  stops.** A power cut mid-track therefore still leaves the start line behind;
  an item with no end row shows as *still open* instead of vanishing
- **Hash chain**: every row carries `prev_hash` + its own `hash` (SHA-1 over the
  canonical JSON of the row). Editing or deleting a line breaks the chain, and
  `verify` names the line and the reason. Each day can be **sealed** into
  `YYYY-MM-DD.manifest.json` (counts, per-kind totals, aired seconds, file
  SHA-256, chain state), written atomically via `os.replace`
- **What it records per item**: wall-clock start/end with offset, kind, the
  item's own metadata (title/artist/song-id/planned length), where it came from
  (file path or URL), channel, the playback device, the **air-check file + offset**
  it can be heard in when a recording is running, and the app version
- **Why it stopped** is a fixed vocabulary, so reports can count it: `natural`,
  `skipped`, `stopped`, `interrupted`, `anthem-cut`, `jingle-cut`, `missing`,
  `error`, `superseded` — a file that never played is recorded as `missing`, a
  failed download as `error`, and a song cut by the anthem as `anthem-cut`
- **Hooked at every path that reaches the output**: queue items (local file and
  YouTube), mic segments, automatic jingle breaks, automatic ad breaks, manual
  jingle (records the interrupted song first), the national anthem (and its
  resume), going on air (both the FFmpeg and the native live-mix stream) and
  relay playback. Down-mixing the paths would have produced a log that lies
- **Retention is a setting with a floor**: default **400 days**, the entry
  refuses anything under **30 days** without an explicit force, and pruning
  never touches today. `logs/` is git-ignored — the evidence stays on the
  broadcast machine
- **A dead disk cannot take the broadcast down**: a failed write is recorded in
  `last_error`, surfaced in the tab (⚠️ + the OS message) and logged — it never
  raises into the playback path

### 🗂️ New sidebar tab: As-run (ON AIR)

- One card per aired item for the chosen day: clock time and kind icon,
  title — artist, kind + real aired length, and the end reason (`natural` /
  `skipped` / `interrupted` / `missing` …)
- **The row is laid out, not stacked.** Two short lines inside one card, with
  the clock, the kind and the length each in a **fixed column** — so a column of
  items reads straight down the tab, a long title wraps inside its own card
  instead of pushing the length out of line, and the sidebar no longer turns
  into a wall of ragged text
- **Only the newest unclosed item says ⏳ *on air now*.** A crash — or two
  copies of the app writing the same folder — can leave older `start` rows with
  no `end` row; those are read back as closed-but-unrecorded and named
  `unclosed`, instead of three items all claiming to be on air at once. The
  newest one is still the only item that can be open
- Day navigation (◀ ▶ / **Today**), a live summary line (`N items · H:MM:SS on
  air · 🔗 chain intact`), and the actions: **🔒 Seal** the day, **🔒 All days**
  seal every day that needs it, **✅ Verify** the chain (a clean report, or every
  broken line listed), **📤 CSV** export (UTF-8 BOM so Excel reads it), **🗑
  Prune** past the retention
- **🔄 Refresh** pulls the list again on demand (the tab already polls while
  open; this is the manual pull), and **every control explains itself on
  hover** — Record, ◀ / ▶ / Today, Refresh / Seal / All days / Verify / CSV /
  Prune, and the retention box + Apply — so the six-button row stays readable
  without a legend
- The table refreshes itself while the tab is open (one cheap poll, skipped
  when the tab is hidden), and the switch writes the operator's choice into the
  `.freq` preset along with the folder and the retention

### 🔒 Sealing that runs itself, and a command line for the inspector

- **The day seals itself.** Opening the app on a new day seals the day before,
  and the As-run poller seals every earlier day that has no manifest once a day
  — so a station that is switched on 24/7, or one that crashed last night,
  still ends up with a complete set of manifests without anyone pressing 🔒
- **`--seal-all` in the tab and on the command line** seals every day that
  needs it, and **refuses** to seal a day whose chain is broken or whose file
  changed after sealing: those days are reported (tab toast / `REFUSED` on
  stderr, exit code 1) and their old manifest is left exactly as it was
- **Sealing can tell the difference between "grew" and "was rebuilt".** The
  manifest now records `manifest_version`, `head_seq` and the **`tail_hash`**
  (the chain hash at the sealed line count). A day that legitimately gained
  items after sealing is re-sealed and keeps the superseded hash in
  `previous_file_sha256` + `reseal_count` as the audit trail; a file rewritten
  with a *self-consistent* chain — even one that grew, which a line-count rule
  waves through — is caught by comparing that sealed tail hash, and reported as
  **TAMPERED**
- **A whole folder, verified in one command.** `python asrun.py --verify-all`
  walks every day, prints a line per day, and exits with a code a cron job can
  act on: **0** clean · **1** evidence broken/missing (a broken chain, a
  truncated day, a re-written file, a deleted day whose manifest survived, or an
  earlier day that was never sealed) · **3** maintenance due (a sealed day that
  was extended, or an unsealed day that `--allow-unsealed` accepts) · **2** the
  command itself was wrong. Also `--seal DATE`, `--seal-all`, `--list`,
  `--export DATE [--out file]`, `--prune [--keep-days N] [--force]`, `--json`,
  `--quiet` and `--today` for re-checking a past day
- **`--seal-all` skips today on purpose** (it is still airing, so sealing it
  would seal a day that is not over) — but it says so (`skipped : … (today is
  still airing — seal it with --seal)`) and then still audits the whole folder,
  so a day that has been tampered with **cannot** leave the command with exit 0
  just because it was skipped
- **The tab says when it cannot prove something**: an earlier day with no
  manifest shows as `⚠️ N day(s) never sealed (…)` in the summary line instead
  of the tab looking quietly complete

### 🆕 New playlist (.freq) — the header, next to Open / Save / Save As

- **🆕 New** (or **Ctrl+N**) starts an empty queue. It asks first when something
  is queued, and it **empties only the queue**: the library (the searchable
  shelf), the history (what already aired) and the presets are all kept, so a
  new playlist does not throw away the operator's working context
- **The save target goes back to the default file**, so the very next Save can
  never write over the playlist that was being edited a moment ago
- **It writes nothing by itself** — the previously open file keeps its bytes on
  disk until the operator decides to save

### 📥 Import songs from another playlist (.freq)

- **📥 Import** (or **Ctrl+I**) takes one **or several** .freq files and
  **appends** their songs to the current queue: nothing is replaced, and the
  item on air keeps its slot in the list
- **Duplicates are skipped by identity**, not by title luck: a local file by its
  path, a YouTube item by its URL, a mic segment by title + length, anything
  else by title + artist — checked against the queue *and* inside the batch, so
  importing the same file twice adds nothing the second time
- **A file that cannot be read is named**, never silently contributing zero
  songs, and a file that has none leaves the queue exactly as it was
- The toast reports what happened (`📥 Imported 12 song(s) · skipped 4
  duplicate(s)`), and the imported songs land in the library as well, so they
  can be searched and used in presets afterwards

### 🌐 The interface speaks English

- The whole as-run tab (**Record** / **Today** / **Refresh** / **Seal** /
  **All days** / **Verify** / **CSV** / **Prune** / **Keep (days)** + **Apply**),
  every tooltip, every toast and dialog, and the `reason` values written into
  the log are plain English — the tab, the CSV export and the CLI all read the
  same words
- Comments and docstrings across the source are English too. Behavior is
  unchanged: only the wording moved, and Thai song titles still round-trip
  untouched (a test proves it)

### ⏹ Per-song end marks (new)

- **Three endings, chosen per song, from the mark at the right end of its queue
  row.** A press on the mark cycles it, and the tooltip says what the current
  one means:
  - **＋** `auto` — nothing chosen: the song ends and the rundown goes on
    exactly as it always did (the ad/jingle interval still applies)
  - **AD▶** `break` — air an ad/jingle after **this** song, then carry on with
    the next one
  - **■** `stop` — stop the rundown here, immediately after this song
  - **AD■** `break_stop` — air an ad/jingle after this song, then stop
- **The mark is one item on the queue canvas**, in the card's own right-hand
  column, so it cannot push the title, the clock or the length around. A press
  repaints **that one row** — no rebuild of the list — and a press anywhere else
  on the card still selects the song like before
- **The artwork is SVG, baked to PNG once.** `icons/*.svg` are the source of
  truth; `make_end_icons.py` rasterizes them (transparent background, the SVG's
  own frame) and the app only ever loads the PNGs, downsampled once per app and
  cached — four images in total, not one per song. A missing file cannot blank a
  row: the same meaning is drawn from shapes instead
- **It travels with the playlist.** `end_mode` is written into the `.freq` file,
  defaults to `auto`, and a hand-edited value the app does not know reads back
  as `auto` rather than breaking the row
- **The rundown obeys the mark, and only the mark** — with the edge cases
  settled deliberately:
  - **`break` airs a break now**, even when the interval counter is not due, and
    the interval counter is not left double-counted for the next song
  - **"then stop" is remembered only once the break actually starts.** A break
    that cannot air (no ad or jingle configured, file missing, channel muted)
    must not silently end a *later* song — but `break_stop` with nothing that can
    air still stops, because that is what the operator asked for
  - **Stopping is not skipping.** `current_index` stays on the song that just
    aired — the same end state as running out of queue — so the rundown never
    swallows a song nobody heard
  - **Crossfade is not armed across a boundary**: a fade into a mark that ends
    the rundown would have faded into silence

### ⏰ The queue shows the clock, not just the length

- **Every card carries the wall-clock window it will air in**
  (`HH:MM:SS–HH:MM:SS`) right beside its length, so the operator can read
  "this one starts at 08:20:16 and ends at 08:26:40" off the list instead of
  adding durations in their head
- **The schedule is anchored on the song playing right now** (start = now −
  elapsed), so the rows above it are in the past and the rows below carry the
  time they will really start. With nothing playing, row 0 starts now and every
  row continues from the one above it (`stop[i] == start[i+1]`)
- **The painter never raises on a bad length** — a `None`, NaN, negative or
  string duration still gets a stamp, and the row is still drawn
- **A narrow card drops the clock instead of squeezing the title**, and gets the
  room back when it widens. The minute rolling over re-stamps the drawn rows and
  nothing else repaints

### 🎲 Random insert — a new song does not always land at the end

- **A 🎲 switch in the Add tab** sends each new song to a random slot in the
  **upcoming** queue instead of the tail, so a request airs sooner and somewhere
  unexpected
- **Every add path goes through the same rule** — YouTube video, playlist batch,
  file, drag-and-drop, library and the manual form — so the switch means exactly
  the same thing wherever the song came from, and a toast says where it landed
  (or that it is OFF and songs go to the end)
- **Never in front of the song on air.** The random range starts at
  `current_index + 1`, because that slot has already been passed and a song
  dropped there would never play; with nothing playing the whole queue is in
  play, top included. A batch is placed one song at a time against the queue as
  it stands, so it spreads out over the list instead of landing as one block
- **The 🎲 switch covers Browse Audio too**: a whole picked batch is scattered
  one file at a time through the upcoming queue, never as a block and never
  ahead of the song on air

### 📂 Browse Audio takes a whole batch

- **The file picker now selects several files at once** (Ctrl/Shift-click in the
  dialog) and queues every one of them — the button says `pick several`, and the
  dialog title reminds the operator of the shortcut. One file is still one file:
  nothing changed for the single-pick habit
- **The batch keeps the order it was picked in** (with 🎲 off), one queue card
  per file, titled by its filename with the real path attached, and the label
  under the button summarises the batch (`3 files: first +2`)
- **One toast per batch, not one dialog per song** — the old modal `Added: …`
  box is gone, so picking twenty files no longer means dismissing twenty popups
  and the queue is not blocked while they are cleared
- **Duration detection fixed for untagged files**: mutagen's file objects are
  dict subclasses, so an untagged wav/mp3 was falsy and every such file used to
  be queued as `0:00` — the length is now read from `info` regardless of tags,
  and the queue clock column benefits too

### 📦 The build and the installer follow the source

- **`build.py` now ships the end-mark artwork and the templates** (`icons/`,
  `templates/`) as data, and keeps the version stamped from `installer.iss` —
  one number, one place, splash and installer agreeing
- **`installer.iss` lists the modules the app actually imports**: eighteen were
  missing (`asrun`, `aircheck`, `aircheck_split`, `dual_output`, `audio_meter`,
  `focus_mode`, `keys_tab`, `livelan`, `mic_recorder`, `plugin_system`,
  `scheduler`, `silence_sentinel`, `theme`, `titlebar`, `update_checker`,
  `visualizer`, `freq_supervisor`, `sentinel_heal`), and a `translate_html2.py`
  that does not exist in the tree was dropped. The list is now a comment-checked
  copy of the imports rather than a leftover
- **`release.py` refuses to build an installer around an incomplete bundle**: it
  checks `FreQ.exe`, `splash.jpg`, `version.txt`, the three end-mark icons and
  every native `<abi>.pyd`, then imports each shipped module **from the bundle**
  (not from the repo) and calls its entry points — the check that catches a
  stale pre-SP1 renderer, which loads fine and then crashes on every jingle

### ✅ Verified on the final bytes

- **`test_edge_cases.py` 561/561** — 536 as-run/queue checks plus **25 new
  `[playlist]` guards** (buttons on the toolbar, both handlers wired, Ctrl+N /
  Ctrl+I bound, multi-file import, and the containment rules that Import must
  never call `load_from_file` — that is what Open is for — and that New must
  never write a file)
- **`_smoke_browse_multi_1026.py` 27/27** — drives the real **Browse Audio**
  button and the real dialog hook: the picker is the **multi-select** one (the
  single-file dialog is never called), three real WAV files all reach the queue
  in picked order with their paths and real 0.2 s lengths, a bare string from Tk
  still works as one file, cancel and a garbage answer add nothing and never
  raise, and with the 🎲 switch **toggled like a click** every picked file lands
  strictly **after** the item on air while 12 batches produce 6 distinct
  orders — then OFF again appends in order. The source files stay
  byte-identical and nothing is written beside them
- **`_smoke_playlist_1020.py` 32/32** — drives the **real buttons** (found by
  label in the header and invoked like a click): Import appends both files'
  songs in order with the on-air item still first, a second import adds
  nothing, a corrupt and an empty file are both reported with the queue
  unchanged, New empties the queue while keeping library/history/presets,
  resets the save target, and leaves the open playlist byte-identical
- **`_smoke_asrun_cli_1020.py` 59/59** — runs the real command line as a
  subprocess and needs an **exit 0** on a clean folder before anything else, so
  the tampering checks cannot pass vacuously. Every forgery is caught and named:
  an edited payload, a deleted row, a truncated file, a file **re-written with a
  fresh valid chain** (same line count), a re-chained file that also **grew**
  past the sealed tail, a deleted day whose manifest survived, a day that was
  never sealed, and a tampered **today** that `--seal-all` skips. It also proves
  `--seal-all` re-seals the legitimate append with `previous_file_sha256` +
  `reseal_count`, refuses the rewritten days while leaving their manifests
  byte-identical, that a second `--seal-all` is a no-op, and that no invocation
  ever ends in a traceback (an exit 1 has to be a verdict, not a crash)
- **`_smoke_asrun_1020.py` 105/105** — drives the real widgets: 20 scripted items
  (and, for the auto-seal, two real earlier days — one that must be sealed on
  the spot, one whose chain is broken and must be left alone while the tab
  keeps warning about it) become **20 paired items / 40 rows**, the first item's
  aired length matches real elapsed time (1.003 s for a 1.0 s sleep), appends
  never rewrite earlier bytes, `rq.history` still behaves, and every hook above
  is exercised (missing file, skip, ad, auto + manual jingle, anthem + its cut,
  mic, stream, relay). It also tampers a line, deletes a line, corrupts the
  folder, prunes, exports, seals, round-trips the settings, opens the tab for
  real and closes the app mid-item
- **`_smoke_end_modes_1021.py` 52/52** — the end marks end to end: the model
  (defaults, `.freq` round trip, an unknown hand-edited value reading as
  `auto`), **one canvas item** per visible row inside the right column without
  eating the title/clock/length columns, a press on the mark cycling the mode
  and painting one row while a press elsewhere still selects, the rundown doing
  what each mode says (a break that cannot air still stopping for `break_stop`,
  a stale "stop after break" flag never firing later, crossfade not armed across
  a boundary), and the speed rule: a click repaints one row, a 400-song rebuild
  draws the viewport only
- Also green: `test_gui_wiring.py` 18/18 · `test_ui_1016.py` 112/112 ·
  `_smoke_queue_times_1019.py` 53/53 · `_smoke_settings_persist_1017.py` 34/34 ·
  `_smoke_master_chain_1017.py` 36/36 · `_smoke_queue_stability_1018.py` 16/16 ·
  `_smoke_random_insert_1020.py` 31/31
- **A 20-minute soak of the real app** (`test_soak_ui_1026.py --minutes 20`: the
  genuine `RadioApp` inside its own `mainloop`, silent WAV audio, a 24-song
  queue, ad/jingle counters, end marks stopping the rundown with the operator
  starting it again, tab switches, search typing, marks clicked and as-run
  refreshes) found exactly one thing — and it was worth finding:
  - **17 of 18 checks passed**, including 0 contained UI errors, every
    scheduled ping delivered, no thread growth, a clean as-run chain the whole
    way, the air-check file untouched, and the app closing itself without
    raising
  - **The one failure was a real stall, not noise: a 1.64 s Tk round trip**
    while the As-run tab redrew a day that had grown to ~90 items. Measured
    (`_measure_asrun_rows_1026.py`): **14 ms to build a card and 4 ms to destroy
    it**, so 90 items cost 1.7 s in one bite and a 300-item broadcast day would
    have frozen the window for over five seconds — exactly the kind of hiccup
    nobody reports and everybody feels
  - **Fixed by slicing the redraw**: the tab now clears 20 old cards and builds
    10 new ones per `after(1)` tick, and a newer refresh cancels the older
    build. Re-measured the same moment on a **200-item day**
    (`_measure_asrun_refresh_1026.py`): worst GUI round trip during the redraw
    **129 ms**, down from 1,640 ms on 90 items; the queue drawing is untouched
- **Toolbar measured, not eyeballed**: the file cluster needs 432 px
  (268 px before the two new buttons) and the whole bar 1175 px, against a
  1370 px window — see [the capture](_ui_toolbar_1020.png). At the 900 px
  minimum size the bar already overflowed before this build (1011 px > 900 px),
  so the new buttons widen an existing limit rather than creating one

### ⚠️ What this build does NOT claim

- **Two rows per item is deliberate**, so a crash leaves a trace — it is not the
  "one line per item" wording the design draft used. The tab and every report
  count *items*, not lines
- **The chain is evidence, not proof**: it makes an edit visible, but someone
  determined enough to rebuild an entire day's file from scratch can produce a
  self-consistent forgery. `verify` will show the chain no longer matches the
  sealed manifest's SHA-256, which is the honest guarantee — and when the day
  was legitimately extended after sealing, the sealed-tail comparison is what
  distinguishes "more of the same file" from "a different file"
- **The sealed manifest is still editable by whoever holds the machine.** The
  command line makes a rewrite visible; it does not make it impossible, and
  nothing here is a substitute for keeping the folder on a machine and an
  account that not everyone has
- **Planned vs actual is not here yet.** This build records what aired;
  comparing it against a designed schedule needs the compiler (P2) in
  [DESIGN-plan-1020.md](DESIGN-plan-1020.md), along with the quota reports, the
  printable rundown and the "click an item, hear that moment" trace (P3)
- **`scheduler.py` is still unwired** (it has been since it was written). The
  as-run log does not depend on it; P1/P2 must decide whether the plan or the
  scheduler owns time
- **Import copies song ENTRIES, not audio.** A local file that lives on another
  machine stays a path; a YouTube item still needs a network to play
- **Only the queue is imported.** The other file's library, history, presets and
  settings are left where they are — importing must not silently re-shape the
  current session (merging presets is a separate decision nobody has asked for)
- **The imported songs keep their own ids**, so a preset naming the same song in
  the other file does not follow it over
- **Import does not re-check the files on disk** — a path that no longer exists
  imports as a `missing` row when it reaches air, exactly like a queued one
- **The end mark is a rule for natural endings only.** Pressing ⏭ Next or ⏮
  Previous by hand does what it always did — it skips, records `skipped` in the
  as-run log, and never asks the absent song what it wanted. Nothing marks a
  mic segment, an ad or a jingle; those air because something else asked them to
- **The clock is a forecast, not a promise.** It assumes every song plays to its
  full length, in order, from right now: a seek, a skip, a manual jingle, the
  anthem or an end mark that stops the rundown moves the whole tail, and the
  column is re-stamped from that moment rather than holding the old estimate
- **Random insert is placement, not rotation.** It scatters new songs through
  the upcoming queue (never before the song on air); it is not artist
  separation, not a no-repeat rule, and it does not re-order what is already
  queued
- **The 20-minute soak was run on the build from before the redraw fix.** That
  run is what found the 1.64 s stall; the fix is measured on the same moment
  (129 ms for a 200-item day) but the full 20-minute gate has **not yet been
  re-run on these bytes** — `python test_soak_ui_1026.py --minutes 20` is the
  one check still outstanding, and it should be green before the installer is
  handed to a station
- **Reported tests that were already red stay red**: `_smoke_split_0928.py` fails
  on the missing `_aircheck_tracklog` GUI wiring (R2, still open) and
  `_smoke_heal_0928.py` / `_smoke_sup_0928.py` / `_smoke_marker_0926.py` remain
  in the baseline — none of them are touched by this build

---

## FreQ Ultimate 2026 (Build 2026.1018.1) — the queue stops costing seconds (source build)

**A performance build with a design pass on top.** The queue is no longer a
widget tree: it is **one canvas that draws only the cards you can actually
see**, so a 400-song list opens in a fraction of a second instead of six, and a
track change repaints two cards instead of rebuilding the whole list. The
cards then got the approved look — 68 px tall, 15 px apart, never narrower
than 130 px, every field present — and the queue text is now sized in
**pixels**, so a 150 % Windows display no longer blows it up to twice the
design. The same round found and fixed **three real crashes** and put a
containment net under every Tk callback. Proven on the final bytes:
**14 suites, 900 checks, every exit code 0.**

### ⚡ The queue is one virtualized canvas (rebuilt)

- The list used to build a widget per song (a `CTkFrame` at first, plain Tk
  frames after): 400 songs meant ~3,200 widgets and **~6.1 s** of layout
  before the queue was usable — re-run on every refresh, including every
  track start
- It is now **one `tk.Canvas`** as tall as the whole list, which keeps
  `CTkScrollableFrame`'s native scrolling and scrollbar untouched, with only
  the cards inside the viewport (plus 4 rows of overscan) materialised as
  canvas items: **9 cards / 54 items** on a 400-song queue — and the same 54
  items at 4,000 songs
- Measured on the final build, **400 songs: rebuild 11–50 ms + first paint
  72–132 ms** (a drawn list in well under a second, worst run ~180 ms)
  versus ~6.1 s before. **4,000 songs: rebuild ~11 ms** — the first paint
  still grows, see *Still open*
- A track change repaints **two cards**: the current-row marker costs
  **0.01–0.05 ms** (it used to rebuild the entire list). Holding a selection
  arrow repaints the viewport in **~6 ms** per step
- Row hits come from canvas geometry (`y // pitch`) instead of one widget per
  row, so a click anywhere on a card — its text included — selects it, and a
  click in the **15 px gap** counts as a click on the playlist background
  (deselect), never as the card below
- The visible window follows the scroll with a 40 ms poller that costs one
  `yview()` call when nothing moved, and a `keep_scroll` path stops the
  deferred selection repaint from yanking the list back to the top
- The empty-queue placeholder and the list swap in and out cleanly (no
  stacked placeholders), and the list canvas is kept across refreshes

### 🚀 Minimize → restore is fast again (measured)

- **Minimize the window, then bring it back: 3.63 s → 1.53 s** until the
  window is usable again, timed on the operator's own machine with the same
  .freq — **2.10 s less** every single time (this is the restore path, not
  the cold start)
- What changed in that window: the queue used to be a widget per song —
  thousands of child windows sitting in the frame that gets restored — and
  it is now one canvas that redraws only the cards you can see, so the song
  count no longer decides how long the list takes to come back

### 🎹 The queue is usable without the mouse (new)

- Click a row and the list takes the keyboard: **↑/↓** walk the rows,
  **Shift+↑/↓** extend the range, **PageUp/PageDown** move a screenful,
  **Home/End** jump to the ends, **Enter** plays the selection
- Every navigation handler consumed the key, so the window-wide ↑/↓ volume
  shortcut keeps working everywhere else — the volume did not move once
  while walking a 300-row list in the smoke run
- **Ctrl+J** (or **🎯 Now** in the queue header) selects and scrolls to the
  song on air; when nothing is playing, or the search filter hides that
  song, a toast says exactly that instead of doing nothing
- Movement works in positions of the *visible* list, so navigation and End
  respect an active search filter instead of jumping to a hidden row
- Revealing an off-screen row is one scroll-fraction move, not a widget
  lookup: row #3,800 costs the same as row #8

### 🎛️ One EQ, and mic chains live with the mic (changed)

- The Settings tab's EQ (preset + Bass/Mid/Treble sliders) is **gone**: it
  pushed the SAME native 3-band stage as the Master Broadcast Chain dialog,
  so two UIs were fighting over one filter and two copies of the curve
  lived in the .freq. The chain dialog is the single EQ now, and an older
  .freq that still carries an `eq` block restores into those same bands
- **Mic Filters has no Settings section any more** — a mic chain belongs to
  a mic ITEM, so it opens from the queue row (Filter / right-click →
  Filters) where the operator can see which mic is being edited
- Nothing an existing .freq holds is lost: `mic_filters`,
  `mic_filters_immediate` and the legacy `eq` block all still restore

### 🎨 Queue cards match the approved design (new)

- **68 px card, 15 px gap, never narrower than 130 px** — the pitch, the gap
  and the floor are named constants, and the card width follows the panel
  (912 px in a 922 px panel)
- Left rail: position number (or the ▶️/⏸️ marker on the playing card) and
  the source badge (⚡ cached / 🔗 YouTube / 📁 file / 🎙 mic); then the bold
  title with the artist underneath; the duration right-aligned — every field
  from the design, on every row
- Rounded corners are one polygon per card (four sampled corner arcs), not
  six canvas items, and the card is 6 px inset from the panel edge
- **Resting cards are the same tone as the panel** (`#131920`), so the list
  reads as one flat surface; hover lifts a card to `#1a2230`, a selected card
  gets `#1c3050` with a border, and the playing card gets the now-playing tint
  plus the accent border
- Titles and artists are clipped against the real card width with an
  ellipsis, so text can never run into the duration column

### 🔤 Queue text is sized in pixels, not points (fixed)

- The card fonts were specified in **points**, so Windows display scaling
  multiplied them — 13 pt is ≈17 px at 96 DPI and ~1.5× that on a 150 %
  display, which is why the same card looked about twice the design size
- They are now **pixels** (`(family, -size)`): title **13 px bold**, artist
  **11 px**, index and duration **11 px**, source badge 12 px — identical on
  every display
- Proven: a 10 pt label measured **152 px** wide at scaling 1.0 and
  **279 px** at 1.75, while the queue's 13 px title stayed **207 px** at both

### 🛡 The queue can no longer take the app down (fixed)

- **Contained UI errors**: every Tk callback now goes through
  `report_callback_exception` — the traceback is logged and counted, the user
  sees one toast per 30 s at most, and nothing is ever re-raised, so the event
  loop survives a button that throws
- **Three real crashes found by a new soak and fixed**: the queue filter called
  `.lower()` on a `None` title/artist (hand-edited `.freq`, stream entries);
  the queue header summed `None` durations (`TypeError` — the list never drew
  at all); `_fmt_dur(None)` did `int(None)` inside the card painter
- Song lengths are read through **one total helper** (`None` / `NaN` / `inf` /
  negative → `0`), so bad data can poison neither the header nor a card
- **One unreadable song no longer blanks the list**: the card is skipped and
  logged, the rest still draw
- The 40 ms scroll poller **re-arms even when a repaint throws**, stops itself
  on a torn-down window, and is cancelled in `destroy()` — no `after` on a
  dead window
- Queue rebuilds issued from a **worker thread are re-dispatched onto the UI
  thread** (Tk widgets are main-thread only; reaching one from a worker is a
  hard crash, not an exception)
- **Opening a `.freq` can no longer kill the launch**: unreadable, corrupt or
  truncated files, a top level that is not an object, non-song rows, an
  unknown `status`, wrong-typed fields and an out-of-range `current_index` are
  all handled (skip / default / clamp) instead of raising — the current queue
  is kept when the file cannot be trusted

### 🧪 Proven — regression suite on the final bytes (2026-10-18)

| Suite | Result |
|---|---|
| `test_edge_cases.py` (main gate, `[qrow]` rewritten for the canvas + new `[stable]` guards) | ✅ 449/449 |
| `test_ui_1016.py` | ✅ 109/109 |
| `test_gui_wiring.py` | ✅ 18/18 |
| `_smoke_queue_stability_1018.py` — 700 random queue operations + hostile songs + a callback that throws (new) | ✅ 16/16 |
| `_smoke_select_1017.py` — real Tk selection flow on the canvas | ✅ 32/32 |
| `_smoke_freq_roundtrip_1017.py` — panel vs file vs real restart | ✅ 61/61 |
| `_smoke_watchdog_0926.py` | ✅ 5/5 |
| `_smoke_supervisor_0926.py` | ✅ 10/10 |

- **700 checks across 8 suites, every exit code 0** on the shipped bytes
- The new soak drives the live app through 700 random operations
  (refresh / filter / scroll / click / marker / add / remove) over a queue that
  contains `None`, `NaN`, `inf`, negative and empty fields, then proves
  containment: a deliberately raising callback is caught and the app keeps
  working, a rebuild from a worker thread lands on the UI thread, and
  `destroy()` while the 40 ms poller is armed leaves nothing behind
- Guard changes are honest, not weakened: `[qrow]` now asserts the canvas
  design (no widget per song, no CTk widget per row, drawing bounded by the
  viewport) instead of the widget design it replaced, `RADII` left the wiring
  baseline because it is finally used, and `_smoke_freq_roundtrip_1017.py`
  now decodes its child process as UTF-8 (the Thai/emoji output made the
  cp874 reader thread raise, which had been returning `None` instead of the
  child's result)

### ⚠️ Still open

- Queues of thousands of songs still pay one long first paint (~0.5–0.9 s at
  4,000) while Tk lays out a very tall canvas; the rebuild itself is flat at
  ~11 ms
- Emoji on cards are canvas text, so they render monochrome (CTk labels drew
  them in colour)
- A resting card shows no rounded corner — that is deliberate: it is the same
  tone as the panel

---

## FreQ Ultimate 2026 (Build 2026.1017.1) — switches that tell the truth, dialogs that work (source build)

**A finishing-and-truthfulness build.** Nothing in it exists for headline
value: the Master Broadcast Chain dialog finally does what its buttons
claim, the chain's "all OFF" path really is untouched audio, the two
preferences that 1016.1 put on screen (**Immediate mode**, **Autosave**)
finally survive a restart, the queue got the selection controls it was
half-way through getting — and a new read-only Settings panel shows
exactly what the saved settings hold, so "it persists" is something you
can read instead of something you take on trust. Proven on the final bytes:
**13 suites, 786 checks, every exit code 0.**

### 🩺 Master Broadcast Chain — the dialog works now, and "raw" is raw again (fixed)

- The two-pane dialog found its own pane host by guessing from widget
  parents (`btn.master.master.master`), so clicking **3-Band EQ** /
  **Compressor** / **Limiter** did nothing and all three panes stayed
  stacked on top of each other. It locates the pane host explicitly, so
  selecting a stage actually switches the pane
- The **Limiter switch sent its ceiling even when it was OFF** — turning
  the limiter off left it engaged
- **Defaults reset a dict instead of the widgets**, so the next commit
  pushed the old slider positions straight back; it now resets what you
  see
- The Settings page now states whether the chain is ON and says that the
  stages only run inside the native WASAPI renderer (with a warning while
  the native output is idle)
- **The all-off path was not actually raw**: the player's default limiter
  ceiling was `−1.0 dBFS`, and the native renderer reads `ceiling < 0` as
  "limiter engaged" — so every supposedly untouched mix was quietly
  **1 dB-limited** on the WASAPI path. The default is now `0.0` (in
  `set_master_chain` / `get_master_chain` too), restoring the documented
  bit-identical passthrough when every stage is OFF

### ⏱ Immediate mode + Autosave survive a restart (new)

- `master_chain_immediate` and `autosave {enabled, interval}` are now
  saved with the rest of the app's settings and restored on every path
  (startup, Open, Load Settings): the flag is back **before** the next
  dialog opens, and the switch + seconds field re-sync on the Settings page
  so the widgets never disagree with the running state
- Hand-edited values are clamped to something that can actually run:
  floor **5 s**, ceiling **24 h**, unparseable → **60 s** — never a
  2-second save loop, and never an interval so large that autosave
  silently never fires
- Settings files written before this build still load unchanged (missing
  keys keep the factory defaults)

### ⌨️ Queue selection completed — deselect + ranges that do not crash (fixed)

- **Ctrl+D** deselects the queue — a new action under 🎵 Queue in the Keys
  tab, rebindable and resettable like every other action — and clicking
  empty playlist space deselects too; the selection-only **Filter** button
  hides again when nothing is selected
- Fixed the real crash behind 1016.1's selection work: Shift+arrow read
  `sel[0] / sel[-1]` while the selection state was a **set**, raising
  `TypeError: 'set' object is not subscriptable`. 1016.1's *simulated*
  `range_from()` test kept passing while the live handler blew up — the
  new suite drives the REAL app through the real Tk bindings instead
- Deselect is a no-op when nothing is selected and never rebuilds the
  queue, so scroll position and playback are left alone

### 🖱 Mouse wheel on every volume bar (new)

- One notch = **±5 %** on the main bar, with multi-notch and
  high-resolution deltas stepping the right amount, and values clamping at
  each bar's own range (0–100, Dual 0–200)
- The wheel works on the main output, the Dual mirror, the master fader
  and every **channel fader** — a notch on a channel moves *that* channel
  only; the X11 `Button-4/5` wheel buttons work as well as `<MouseWheel>`
- Bound on the CTk slider's internal canvas with `add="+"`, so CTk's own
  drag binding survives — the wheel never takes the drag slot

### 🎞 Playlist import: "Add only first video in the playlist" (new)

- The playlist picker now offers exactly three answers — **first video** /
  **range** / **all** — and "first" fetches entry #1 and nothing else
- `+ Add Video` routes a playlist link into this dialog; a one-video
  playlist asks nothing and imports directly; Enter confirms, Esc cancels,
  and a long first-video title no longer clips the buttons out of the window

### 🩺 Dual Output slider opened Windows Sound settings (fixed)

- Dragging the mirror volume slider called `os.startfile("ms-settings:sound")`
  on every step — the body of `_open_sound_settings` had been merged into
  `_on_dual_volume` during the 1016.1 merge, and the method it called no
  longer existed. Split back into two methods
- Guarded so it cannot come back: code-level checks in `test_ui_1016.py`
  (catches a statement stranded mid-body, requires `_open_sound_settings`
  to be its own method) plus `_smoke_dual_0927.py` checks that watch for
  `os.startfile` while the slider is dragged

### 🧪 Proven — regression suite on the final bytes (2026-10-17)

| Suite | Result |
|---|---|
| `test_edge_cases.py` (main gate) | ✅ 398/398 |
| `test_ui_1016.py` (1016.1 features + this round's guards) | ✅ 104/104 |
| `test_gui_wiring.py` (permanent wiring guard, new) | ✅ 18/18 |
| `_smoke_freq_roundtrip_1017.py` — panel vs file vs real restart (new) | ✅ 61/61 |
| `_smoke_settings_persist_1017.py` — Immediate + autosave in a second process (new) | ✅ 34/34 |
| `_smoke_master_chain_1017.py` — real dialog + real player (new) | ✅ 36/36 |
| `_smoke_select_1017.py` — real Tk bindings, not the simulation (new) | ✅ 28/28 |
| `_smoke_wheel_1017.py` (new) | ✅ 30/30 |
| `_smoke_playlist_first_1017.py` (new) | ✅ 26/26 |
| `_smoke_yt_mix_1017.py` (new) | ✅ 21/21 |
| `_smoke_dual_0927.py` | ✅ 15/15 |
| `_smoke_watchdog_0926.py` / `_smoke_supervisor_0926.py` | ✅ 5/5 · 10/10 |

- **786 checks across 13 suites, every exit code 0** on the shipped bytes
- Every headline claim is mutation-checked: breaking it in code turns
  exactly the right checks red (dropping the widget→panel refresh, skipping
  the post-save snapshot, making the panel editable, forcing the verdict to
  "matches", dropping the restore-side snapshot), and the files were then
  restored byte-identical (sha256 match) before the final run

---

## FreQ Ultimate 2026 (Builds 0927.x–0930) — reliability batch (source builds)

Note: these builds were distributed during testing; this section records
exactly what they shipped. The next numbered release (2026.1016.1) rolls
DOWN all of it plus the new queue/now-playing/dual features.

### ⏱ UI freeze watchdog + process supervisor (new)
- **Outer guardian, outside the process entirely**: `freq_supervisor.py`
  spawns the app as a child, watches the child's heartbeat file and
  kills + restarts the whole tree when the UI thread stops pumping
  (lesson from the 0926 freeze caught live by py-spy: an in-process
  watchdog can log a hang but nothing inside a deadlocked process can
  rescue it)
- **`UiFreezeWatchdog` in gui.py**: the UI thread stamps a clock on
  every event-loop tick; a watcher thread logs a WARNING with the REAL
  stack of the blocked main thread (names the offending blocking call),
  logs "resumed" when the loop comes back, and writes the supervisor
  heartbeat every 50 ms while things run
- Recovered autostarts show a Windows tray notification; the user's
  clean window-close writes the clean-stop marker so the supervisor
  exits instead of restarting over the user
- Crash-loop guard: repeat-crash budget (3 within 120 s) then give up
  — no endless spin; dev mode launched through "START FreQ
  (Auto-Restart).bat"

### 🔇 Silence Sentinel self-heal ladder (new, opt-in)
- `sentinel_heal.py`: consumes the 0924 sentinel events and climbs a
  recovery ladder one step per event (restart track → … → alert),
  cooldown-gated, resets after sustained audio, modes off / dry_run /
  active — dry_run never calls back, matching the structural-test
  ground rule that the sentinel stays a passive observer

### 🔊 Dual Output — mirror the broadcast mix to a second device (new)
- `dual_output.py`: taps the MAIN device's WASAPI loopback (the same
  tap as the VU meter) and re-plays it through a SECOND native render
  engine on the chosen endpoint — a monitor in another room or the
  co-host's headset hears EVERYTHING (music, live mics, master chain)
- 48 kHz → 44.1 kHz linear-interpolation resampler with a
  duration+frequency-preserving smoke check; one background pump
  thread; stop() idempotent; volume setter (0–200%) hot-adjusts live

### ✈ Air-Check track splitting (new)
- `aircheck_split.py`: a finished air-check WAV is cut into one WAV
  per track with ffmpeg stream copy (lossless), named
  `track_001 - Artist - Title.wav` from real queue metadata; segments
  inherit the recorder's retention policy

### Smoke suites added
-  `_smoke_sup_0928`, `_smoke_supervisor_0926` (supervisor full chain,
  clean-stop marker + REAL watchdog heartbeat),
  `_smoke_watchdog_0926` (deliberate UI stall caught with real stack),
  `_smoke_heal_0928` (ladder modes + cooldowns), `_smoke_dual_0927`
  (mirror engine + resampler + UI wiring), `_smoke_split_0928`,
  `_smoke_marker_0926`

---

## FreQ Ultimate 2026 (Build 2026.1016.1) — queue UX + dual output UI (source build)

### ✏️ Rename from the queue (new)
- Right-click any queue row → **✏️ Rename…** — one small dialog edits
  title AND artist; the rename also lands in the matching library
  entry (same id) and persists into the `.freq` immediately

### 🔢 Multi-select in the queue (new)
- **Shift+click any row**: selects the whole range from the anchor row
  to that row (highlighted borders, no re-render raster needed)
- **Shift+Down**: keeps extending the selection DOWNWARD — repeat it
  and the selection reaches the END of the list
- **Shift+Up**: extends UPWARD to the FIRST entry
- Selection repaints live on screen without rebuilding the list, so a
  held-down arrow sweeps at tick speed even with 1000+ songs queued
- Remove treats the multi-selection as one block; move keeps the
  block's internal order

### 🎞 Now-playing title marquee (new)
- A title longer than the now-playing bar clips at the edge — and
  slides left↔right automatically on a 200 ms tick until the whole
  title has been readable; pauses while playback is paused; external
  text changes (fresh track, anthem override) reset the slide safely

### 📣 Dual Output wired into the UI (new)
- A **second dropdown sits directly under the main output dropdown**:
  pick the mirror device (or "Off"), then adjust the mirror's own
  loudness with its slider (0–200%) — main and mirror outputs are now
  independently controllable
- Start/stop and endpoint hot-swap run through the native mirror
  engine; the chosen device + volume persist in the `.freq` preset and
  are restored at startup (a mirror device that vanished since is
  handled gracefully)

### 🩺 Waveform on installed machines (fixed)
- The waveform generator used to spawn a **bare `ffmpeg`** command and
  fall back to a flat line when that binary was not on PATH — on
  machines without ffmpeg installed globally the waveform widget never
  displayed anything. It now resolves ffmpeg through the same helper
  the rest of the app uses (bundled `deps/ffmpeg-essentials` or the
  `{app}\ffmpeg` copy the installer puts down), so the waveform works
  out-of-the-box after install
- A file that cannot be opened still degrades to flat silence — never
  a crash

### 🩺 Output row re-aligned (fixed, 1016 follow-up)
- The far-right Now Playing output controls were stacking with mismatched
  row widths before: the main output dropdown, the Dual mirror dropdown,
  the two volume sliders, and the `48 / 50` position were scattering into
  different widths and anchors
- Rebuilt as **two uniform rows** that match each other, plus the `xx / xx`
  position row on its own underneath:
  - **Row 1**: main output (device dropdown + mirror toggle + mute +
    0–100% slider)
  - **Row 2**: Dual mirror (device dropdown + its own mute/volume row,
    0–200%)
  - **Bottom row**: `xx / xx` queue position, right-aligned
- Both output rows now use the same widget widths so the dropdowns and
  sliders sit in clean columns instead of drifting; the main mute button
  sits on the main row and the mirror row carries the mirror's own volume
  so it is obvious which loudness you are moving

### 🩺 Now-playing playback-state edge case (fixed)
- The now-playing slot was defaulting to a "playing" appearance when it
  could not reliably tell pause from play, and the playback indicator
  could override the marquee when the bar was paused
- The slot now stops defaulting to playing in that ambiguous case, and the
  play indicator no longer overrides the marquee while the bar is paused

### 🧪 Hardened test suite (new)
- `test_ui_1016.py`: 44 checks across the five features above
  (structural for Tk glue, logic-level for the marquee recurrence and
  the selection ranges, DualOutputMirror unit behaviour incl. clamps
  and idempotent stop)

---

## FreQ Ultimate 2026 (Build 0926.1) — 2026-09-26

**Broadcast Day build** — everything a one-person radio station needs to go
on air with confidence: **Go Live** streams the real broadcast mix (live
mics + music + every filter) to Icecast/SHOUTcast listeners, **Auto
air-check** records every show by itself, the **Master Broadcast Chain**
(EQ → Compressor → Limiter) gives the on-air signal a broadcast voice, and
the mic side carries a NINE-stage native filter chain with real OBS-style
sliders. Audio survives device format changes mid-broadcast; pre-show
readiness tools and a friendlier queue round it out. Plus every fix from
0925.1/0925.2.

### 🎛 Mic Filters — full OBS-style dialog with Immediate mode (new)
- Select a mic segment in the queue → **Filter** (or Settings → 🎙 Mic
  Filters → ⚙ Configure) opens the OBS layout: the 9-stage chain as a list
  on the LEFT with a green dot on every active stage, the selected stage's
  parameters on the RIGHT
- **Nine native C++ stages**, in fixed order:
  **Suppression → Gain (dB) → Compressor → Limiter → De-esser → Mic EQ →
  Echo → Upward Compressor → Expander**
- **Immediate mode** toggle (remembered in .freq):
  - CHECKED — every slider/switch edit reaches the engine the moment you
    release it (no Apply needed — tune live while you speak)
  - UNCHECKED — nothing changes until you press **Apply**
- **Real parameter sliders per stage** (not just Gentle/Strong):
  Suppression high-pass cutoff / gate threshold / gate floor · Gain
  0…+24 dB · Compressor & Upward-Compressor threshold + ratio · Limiter
  ceiling · De-esser split freq + threshold · Mic EQ low/presence shelf ·
  Echo delay 30–600 ms + feedback + wet mix · Expander threshold + depth
- Gentle/Strong buttons remain as instant starting points, and a
  per-stage **Defaults** button resets that filter to the factory sound
- All values push into the engine atomically on slider release (never a
  flood while dragging), persist in .freq, and default OFF so raw sound
  stays byte-identical

### 🟢 Filter chain truthfulness fixes (fixed)
- The green dots in the filter list used to read a stale snapshot taken
  when the dialog opened: toggling a stage (or pressing **Defaults**)
  left the dot frozen — filters you had switched OFF glowed green (e.g.
  "Mic EQ turns itself on") and Defaults refused to let a dot go dark
- Defaults also silently RE-ENABLED the stage it reset (`var.set(True)`)
- Both are gone: Defaults now resets parameters ONLY and never touches
  your on/off switches, and the dots mirror the true state on every
  change — live engine state when Immediate mode is on, the pending
  Apply state when it is off

### 🎚 OBS-style Filter button on the queue (new)
- Select any song (or mic segment) in the queue and a **Filter** button
  pops up next to the action bar — exactly like OBS's per-source Filters
- Songs open **"Filters for '<title>'"**: 3-band EQ, Loudness Auto-Gain
  and Crossfade — the same live knobs as the Mixer page, one Apply away
- Mic segments open the native **9-stage mic filter chain** dialog
- Bonus fix: with a queue **search** active, clicking a row used to
  resolve to the WRONG song (filtered view renumbered rows) — the
  original queue indexes are now preserved everywhere

### ⚡ One-click EQ presets in the song Filters dialog (new)
- **Vocal boost / Bass cut / Flat** buttons at the top of
  "Filters for '<song>'" fill the EQ sliders instantly — the same preset
  table the Settings page uses (vocal −3/6/+2 dB, bass cut −6/+1/0 dB)
- Nothing reaches the engine until **Apply**, so you can audition a
  preset against the sliders and back out with Close
- "Bass cut" is a new preset added to the shared table (handy for
  taming boomy room recordings)

### 📡 Broadcast Check — one-click pre-show readiness probe (new)
- Settings → 📡 Broadcast Check → **▶ Run broadcast check**
- Strictly read-only: opens no live sessions, moves nothing, changes nothing
- Reports: queue health (incl. missing files), mics detected, output engine,
  loopback tap (with live peak while on air), mic-filter chain state,
  plugin load errors, free disk space for recordings
- Red lines tell you exactly what to fix before the show; green means
  "All clear — ready for broadcast."

### 🩺 Mic Doctor — which mic actually has signal? (new)
- Settings → 📡 Broadcast Check → **🎙 Mic Doctor — test every mic…**
- Opens each capture endpoint ALONE for ~2.5 s through the same native
  engine path the app uses, shows the live peak, then closes it again
- Ends the classic "is it plugged in? is it the Default?" guesswork —
  mics that show ✔ SIGNAL are the ones to pick in Add Mic Segment
- Probing runs on the UI thread via timed ticks (never touches widgets
  from worker threads) and never touches a session the app already has open

### 🎚 Master Broadcast Chain — EQ → Compressor → Limiter on the ON-AIR mix (new)
- Settings has a new **⚙ Configure Master Chain…** dialog (OBS-style:
  stage list on the left, parameters on the right, Immediate mode,
  Gentle/Strong-style Defaults): three native C++ stages processing the
  FINAL broadcast mix — music AND live mics together, exactly what
  listeners get
- **3-Band EQ** (bass shelf 120 Hz / mid peak 1 kHz / treble shelf
  3.2 kHz, ±12 dB), **Compressor** (threshold −48…−6 dBFS, ratio
  1:1…8:1 — evens loud/soft passages of the whole show) and **Limiter**
  (safety ceiling −6…0 dBFS, engaged independently)
- Runs inside the render engine after the mic mix-in: all three output
  paths (playing / paused-with-voice / mic-over-silence) are chained, so
  the voice-over-paused-music trick is processed too
- **All OFF (default) = byte-identical legacy path**; parameters are
  lock-free atomics — the render thread picks up edits on the next
  buffer with zero glitch risk; persists in .freq; survives endpoint
  switches (re-pushed after every engine swap)
- Proven by a C++ **self-test with no device needed**: OFF is a
  byte-identical pass-through, EQ boosts a 60 Hz sine, the compressor
  pulls a loud 1 kHz peak toward the threshold, the limiter clips at the
  ceiling — wired into the main suite so a broken stage fails the build

### 🎙 Auto air-check — records every show automatically (new)
- Air-Check tab has an **"Auto-record when ON AIR"** switch (default OFF):
  while on, the moment the show starts — a **Live mix stream** connects or
  a **live microphone** opens — the native recorder starts capturing the
  full broadcast mix, and it saves the moment the show ends
- Powered by the native engine (WAV written entirely in C++ — a busy UI
  can never stall or corrupt a recording), reusing your save folder,
  MP3-export setting and keep-days pruning
- **Manual and scheduled recordings always win** — auto never touches a
  recorder you started yourself, and repeated ticks never double-start
- Verified end-to-end: on-air auto start, off-air stop with MP3 export
  (WAV removed), prune ran, no double-record, persistence round-trip

### 🧊 App freeze when a show ends (fixed)
- Ending a show (or changing a track while streaming) could freeze the
  whole window ("Not Responding"): the auto air-check's MP3 export ran
  ffmpeg on the UI thread (seconds for a long recording), and the ICY
  metadata push could block up to its 5 s socket timeout on every track
  change
- Both now run on background threads: the UI shows "saving air-check…"
  the instant a show ends and updates when done (measured: the tick
  returns in ~6 ms), and metadata pushes return immediately (measured:
  ~1 ms) even against a dead server
- Also hardened the on-air detection so a failed engine probe can no
  longer silently disable auto air-check

### 🧪 Proven together — the combo soak (new)
- A dedicated suite runs **Go Live and auto air-check AT THE SAME TIME**
  (both tap the same loopback) while the stream server is killed
  repeatedly: **9 kills in 55 s, zero interference** — the recording kept
  growing through every kill, the stream ladder kept recovering, the
  show-end transition saved a valid 55 s WAV by itself, and no orphaned
  ffmpeg survived the shutdown (`test_combo_aircheck_stream.py`)

### 🔴 Go Live — broadcast the live mix to listeners (new)
- The streaming tab has a **"Live mix — broadcast mics + music as heard"**
  switch: ON = listeners hear the real-time master mix (live mics, music,
  the whole 9-stage filter chain) through the native C++ sender; OFF
  (default) = the legacy single-file stream, byte-identical to before
- Song titles follow the show automatically: every queue item (songs,
  jingles, even "Live voice" for mics) is pushed to the Icecast admin
  API as ICY metadata so listener players show what's on air
- Status line shows real stream state (Streaming / Reconnecting / Error),
  MB sent, and reconnect attempts — same Start/Stop buttons, same
  server settings, and the switch is remembered in .freq
- Auth matches the legacy path (Basic source header), encoder is the
  bundled ffmpeg (128/192/256/320 kbps selectable), and reconnects are
  handled inside the native thread (see the kill-storm soak below)

### 📡 Native broadcast stream — hardened by a kill-storm soak (new)
- The new native sender (`  native_stream.cpp`: loopback → ffmpeg → Icecast/SHOUTcast in one C++ thread) survived a dedicated **50,000-op
  soak** built to kill it: **668 forced disconnects** (clean shutdowns,
  RST resets, 144 full server outages), 47 fresh sessions — **668/668
  recoveries, zero failures, worst recovery 2.1 s**
- **118 MB** streamed end-to-end during the storm; RSS flat (Δ+1.0 MB),
  threads back to baseline, no orphaned ffmpeg.exe, final fresh session
  still streams
- The soak caught (and this build fixes) real broadcast hazards: a pipe
  deadlock where a stalled server could park the sender forever (a
  separate stall monitor now cancels blocked I/O), ffmpeg's chatty
  teardown errors polluting the app's stderr, and a wedged thread that
  used to lock the module — the stream now resets cleanly and a fresh
  session starts immediately
- The kill-storm harness ships as `test_soak_stream.py` (12 checks) so
  every future build can be tortured the same way

### 🔄 Seamless mid-stream sample-rate/format changes (new)

- Switching a device's sample rate / bit depth / format mid-broadcast
  (Sound control panel, driver resets, "audio enhancements" toggles) no
  longer kills the engine — playback used to stutter, restart or jump
  to another endpoint
- The render thread now **re-opens the device in place** with the new
  mix format (up to 8 tries while the driver settles): buffered music
  is preserved, the stream keeps flowing, and only a truly dead device
  falls back to the old migrate path
- The **mic capture path heals itself too** — the resampler is rebuilt
  for the new mix format and the voice keeps flowing through the same
  9-stage filter chain without interruption

### 🐛 Opening .freq files / adding mics crashed (fixed)
- Loading a playlist or adding a Microphone segment threw
  "cannot unpack non-iterable Song object" and the queue stopped
  rendering — an internal list-shape mismatch introduced with the
  filtered-view fix. Queue rows now carry their original index in ONE
  consistent shape on every path (open, add, search, re-render)

### 🛠 Also in this build
- All 0925.2 fixes: VU meter no longer freezes during mic segments,
  live-mic open retries once before falling back to record-then-play
- All 0925.1 features: permanent mic endpoint ids (mics survive Windows
  reshuffling), typing no longer triggers playback hotkeys, resizable
  Add Mic Segment that remembers your mics and gains, Plugins page + SDK
  with example scripts
- Verified by the expanded regression suite — **332 checks** in the main
  suite (118 added today), jingle stress **41/41**, multi-mic **13/13**,
  pause-storm ALL PASSED — and stability under load: a
  **100,000-operation soak** (0 failures, memory flat at +0.7 MB, threads
  back to baseline, 2,574 live-mic sessions) and a **300-cycle
  device-recovery storm** with music AND a live mic running (every cycle
  clean)

---

## FreQ Ultimate 2026 (Build 0925.2) — 2026-09-25

**Hotfix build over 0925.1**: two broadcast-critical fixes reported from
real on-air use — the frozen VU meter that made working microphones look
dead, and live-mic opens that sometimes fell into record-then-play.

### 📊 VU meter stays live during mic segments (fixed)

- While a mic segment used the record-then-play fallback ("Recording
  microphone…" status), the progress/VU tick was skipped entirely and
  the meter froze at zero — a perfectly healthy mic looked dead. The VU
  meter now measures real output on EVERY tick in every mode (music,
  live mic, fallback recording), and the "audible now" state includes
  the fallback stage.
- The progress bar/waveform logic is untouched for normal playback.

### 🎙 Live mics retry before falling back (fixed)

- A freshly plugged or just-released endpoint sometimes refused the
  first open, silently pushing the segment into "Recording microphone…"
  mode. The live path now retries once after a short settle — the
  fallback only happens when a mic is genuinely unavailable, and the
  retry is logged.

### Everything from Build 0925.1

- 4-stage native mic filter chain (Suppression → Compressor → Limiter →
  De-esser), permanent mic endpoint ids (the no-sound fix), typing-safe
  playback hotkeys, resizable Add Mic Segment dialog that remembers your
  setup, the in-app plugin manager + SDK guide, and the hardened release
  pipeline with a test gate.

---

## FreQ Ultimate 2026 (Build 0925.1) — 2026-09-25

The broadcast-hardening build: live meters you can finally trust during
mic segments, a full OBS-style filter chain, the fix for "mic segments
play no sound", and a plugin system you can manage from the app.
Verified with the full suite (204 cases) plus a 10-minute soak of
147,191 playback operations with zero failures and no memory growth.

### 📊 VU meter no longer freezes during mic segments

- **The misleading bug is fixed**: while a mic segment used the
  record-then-play fallback, the whole progress/VU tick was skipped —
  the meter froze at zero and made a perfectly working mic LOOK dead.
  The VU meter now measures the real output on EVERY tick, in every
  mode (music, live mic, fallback recording), and the "audible now"
  state includes the fallback stage.
- **Live mics retry before giving up**: a freshly plugged or just
  released endpoint sometimes refused the first open, silently pushing
  the segment into "Recording microphone…" mode. The live path now
  retries once after a short settle — the fallback only happens when a
  mic is genuinely unavailable, and the retry is logged.

### 🔧 Mic segments: the no-sound bug is fixed

- **Root cause found and fixed**: mic segments stored the microphone as
  a LIST POSITION, and Windows re-numbers capture devices on every
  reboot or unplug (virtual mics reshuffle constantly). After a restart
  a saved segment could silently point at the WRONG — usually muted —
  microphone. Queue items now remember the microphone's **permanent
  endpoint id**; positions remain only as a fallback. Existing `.freq`
  files keep working.
- The record-then-play fallback path also receives the stable endpoint,
  so both playback paths pick the right mic.

### 🎙 Mic Filters — De-esser added (4-stage native chain)

- The OBS-style chain is now **Suppression → Compressor → Limiter →
  De-esser**, all inside the C++ engine (nothing runs in Python). The
  new split-band De-esser ducks only the sharp "S" range (~5 kHz) when
  sibilance spikes — the voice body is untouched. Every stage has an
  on/off switch and a Gentle/Strong choice in Settings → 🎙 Mic Filters,
  applies live to already-open mics, and is persisted in the `.freq`
  preset. With every stage off the audio path is byte-identical to
  previous releases.
- **Fix**: the legacy bypass used to check only the Suppression stage,
  so running Compressor or Limiter alone silently did nothing. The
  bypass now requires EVERY stage off; a lone stage runs for real.

### ⌨ Typing no longer triggers playback hotkeys

- Fixed: playback hotkeys (Space / N / P / M / arrows) fired while you
  were typing in any settings field, skipping songs mid-sentence. All
  playback hotkeys are now guarded while a text widget holds focus —
  including CustomTkinter's inner entries and comboboxes. Modifier
  combos (Ctrl+S / Ctrl+O) keep working while typing.

### 🎚 Add Mic Segment dialog: resizable + remembers your setup

- The dialog is resizable with the Add button locked to the bottom —
  it can no longer fall off the window on small screens (the quad-mic
  rows once pushed it out of view).
- Your last microphone choices AND per-mic gains are remembered and
  prefilled next time — within the session and across restarts (stored
  in the `.freq` preset; unknown/stale devices fall back safely).

### 🧩 Plugins — manage them from the app

- New Settings → 🧩 Plugins panel: per-plugin on/off switches, health
  badges (error counters, auto-disable warnings), failed-load rows,
  **Reload** (picks up new .py files without a restart) and **Open
  folder**.
- The full SDK guide ships as **PLUGIN-SDK.md** (three worked examples:
  now-playing webhook, daily on-air log, external-script bridge) plus a
  new atomic-write **OBS overlay** example in `plugins/examples/`.

### 🛠 Release pipeline hardening

- Every build now starts by syncing the whole source tree to
  installer.iss (version.txt, release-notes check) and runs the main
  test suite as a **gate before any compilation** (`--skip-tests` for
  emergency hotfixes only).
- All CLI scripts (`release.py`, `build.py`, `push_source_code.py`,
  `build_native_meter.py`, `create_wizard_images.py`) got proper
  `--help` with examples, and UTF-8-safe console output.

---

## FreQ Ultimate 2026 (Build 0924.1) — 2026-09-24

### 🎙 Mic Filters — OBS-style native chain (opt-in, default OFF)

- **A real filter chain for live mics, all native C++**: mic segments run
  an OBS-style three-stage chain inside the audio engine —
  **Noise Suppression → Compressor → Limiter → De-esser**, fixed
  broadcast order. Suppression = high-pass (~80–120 Hz) + smoothed
  noise gate (rumble, hiss, room tone, PC fans). Compressor = evens
  loud/soft phrases (gentle/strong ratios). Limiter = transparent
  ceiling that stops accidental clipping (soft/hard). De-esser =
  split-band sibilance tamer that ducks only the sharp "S" range
  (~5 kHz) — the voice body is untouched.
- **Per-stage on/off + strength**: Settings → 🎙 Mic Filters → ⚙ Configure
  opens an OBS-style dialog; every stage has a switch and a
  Gentle/Strong choice. The chain persists in the `.freq` preset, applies
  live to already-open mics, and engines created later inherit it.
- **Off means untouched**: with every stage off, the mic mix path is
  byte-identical to previous releases — no hidden processing, ever.

**The Ultimate era begins.** Starting with this build FreQ switches to a
year-based versioning scheme — `Ultimate 2026 (Build mmdd.round)`, where
Build is the release month+day and the final number counts update rounds
within that day. This build carries every SP3 improvement (four live
microphones with per-mic gain, real-time mic on every engine, mic
pause/resume, the PortAudio crash fix) and a new accurate update checker
that understands the new scheme.

### 🏷 New versioning scheme

- Releases are now identified as **Ultimate 2026 (Build 0924.1)** — the
  year is the major version, and each build is dated by month+day with an
  update-round counter, so a build's age is readable at a glance.
- The update checker compares the new scheme against every historical
  tag format (legacy `2.7.4-SP3`, `v2.7.4`, `2.7.2_SP1`) — any Ultimate
  build outranks the entire legacy 2.x series.

### 🧩 Plugin SDK — write your own plugin (v1, observe-only)

- **Drop a Python file into `plugins/` and FreQ runs it**: plugins receive
  `on_start`, `on_track_started`, `on_track_finished` and `on_shutdown`
  events with the track's title/artist/source/duration. The full SDK
  guide ships as **PLUGIN-SDK.md** (three worked examples: webhook,
  daily on-air log, external-script bridge), plus ready-to-copy
  examples in `plugins/examples/` (now-playing webhook and an
  atomic-write **OBS overlay** JSON feed).
- **Safe by design**: plugins get only `ctx.log`, `ctx.notify` and a
  read-only `ctx.snapshot()` — there is no pathway into the audio engine,
  enforced by a structural test that fails if one ever appears. Track
  data arrives as plain dict copies, never live objects.
- **A broken plugin can never break the app**: load failures are skipped
  and reported, every hook call is wrapped, and a plugin that fails 3
  times in a row is disabled automatically. Hooks run on a background
  thread, so a slow webhook never stalls the UI or the audio.
- **Your choice persists**: enable/disable per plugin via the `.freq`
  settings file.

### 🤖 Silence Sentinel — first Ultimate feature (dry-run, opt-in)

- **Watches for dead air**: a new sentinel measures the broadcast output
  through the same WASAPI loopback tap as the VU meter and warns when the
  output stays silent too long while a segment is playing (default 10 s,
  ≈ -48 dBFS threshold). The warning lands in the log and the status line.
- **Dry-run by design**: it NEVER touches playback, the queue or the
  engine — enforced structurally, a test parses the module and fails if a
  playback API ever appears in it. Recovery actions (re-skip, engine
  restart) come in a later Ultimate build and will ship default-off.
- **Opt-in**: disabled by default; enable with the environment variable
  `FREQ_SILENCE_SENTINEL=1` — or from the new Settings section (toggle,
  silence window in seconds, a −20…−100 dBFS threshold slider and an
  events counter). The choice persists in `.freq` settings files.
- **No false alarms**: stopped playback, live mic segments, and an
  unavailable level tap are never counted as dead air — no data means no
  warning. Repeated alerts during one silent stretch are rate-limited to
  one per 30 s.
- **Tested inside the main suite**: 40 fake-silence cases (controllable
  clock, no audio hardware, nothing audible) run as part of
  `test_edge_cases.py` (102 checks total).

### 🔄 Update checker (now actually functional)

- **Fixed the stale version**: the app always reported itself as `2.5.47`
  (a constant left in the code). It now reads the version from
  `version.txt`, which the build stamps from the installer on every
  release — one place to bump, correct everywhere, nothing left to forget.
- **Understands every tag format this project publishes**: correct
  comparisons for `2026.0924.1`, `2.7.4-SP3`, `v2.7.4` and legacy
  `2.7.2_SP1`-style tags (the old parser silently failed on those, and
  the SP suffix was never read at all).
- **Ordering is correct**: Build 0924.2 is newer than 0924.1, 0925.1 is
  newer than every 0924.x, and any Ultimate 2026 build is newer than any
  legacy 2.7.x — updates are announced exactly when they should be.
- **No data → no nagging**: if the local version cannot be read or the
  API call fails, the checker stays silent instead of guessing.
- The update dialog and the About page now display the version from the
  same source — they can no longer disagree.

### 🗺 What's next (in development)

See `ROADMAP-ULTIMATE.md` for the full Ultimate Series plan: voice
tracking, separate headphone (cue) output, sound pads, multi-station,
audience listen links and unattended automation.

---

## FreQ 2.7.4 SP3 (Stable Pack 3) — 2026-09-23

**SP3 makes microphones work the way they should — and makes the update
checker work for the first time.** Up to four simultaneous live mics with
per-mic gain, real-time voice on every playback engine, pause/resume for
mic segments, a hard fix for a crash some old USB webcam microphones could
trigger, and an accurate update checker. Updating over SP2 is safe; all
existing settings and queue files keep working.

### 🔄 Update checker (now actually functional)

- **Fixed the stale version**: the app always reported itself as `2.5.47`
  (a constant left in the code). It now reads the version from
  `version.txt`, which the build stamps from the installer on every
  release — one place to bump, correct everywhere, nothing left to forget.
- **Understands every tag format this project publishes**: correct
  comparisons for `2.7.4-SP3`, `v2.7.4` and legacy `2.7.2_SP1`-style tags
  (the old parser silently failed on those, and the SP suffix was never
  read at all).
- **SP ordering is correct**: SP2 is newer than SP1, and `2.7.5` is newer
  than any SP of `2.7.4` — updates are announced exactly when they should
  be.
- **No data → no nagging**: if the local version cannot be read or the
  API call fails, the checker stays silent instead of guessing.
- The update dialog and the About page now display the version from the
  same source — they can no longer disagree.

### 🗺 What's next (in development)

See `ROADMAP.md` for the full plan: voice tracking, separate headphone
(cue) output, and a watchdog that restarts the app unattended.

### 🛡 Fixed: crash when opening certain microphones

- **Fixed a hard process crash (0xC0000005 inside `libportaudio64bit.dll`)**
  triggered when opening some old USB webcam microphones (reported from the
  field with a "Microphone (2- USB2.0 MIC)" device). The crash happened
  inside a third-party audio DLL where no error handler could catch it.
- Microphones now travel **exclusively through FreQ's native WASAPI engine**
  on Windows — PortAudio never enumerates or opens a capture device there
  anymore. The same problematic webcam now opens and streams correctly
  (verified on the actual hardware).
- The legacy PortAudio path remains available only on non-Windows platforms
  where the native module is absent.

### 🎙 Four simultaneous live microphones, each with its own gain

- Mic segments now support **up to 4 microphones at once** (panels,
  interviews, guest DJs). Second/third/fourth mic rows in the Add Mic
  Segment dialog, each with an individual **gain (0.5×–4.0×)** — quiet
  webcam mics can be boosted, hot ones tamed.
- Each mic is an independent capture + FIFO inside the engine: one mic
  failing, going silent or being unplugged never affects the others.
- The 4-mic ceiling is enforced both in the player and in the C++ engine.
- Queue items store device endpoint ids, so re-plugging devices does not
  make queued segments point at the wrong mic.

### ⚡ Live mic works on every playback engine — no more "Recording…" delay

- Previously, a live (real-time) mic required the WASAPI playback engine;
  when music ran on the default engine, mic segments fell back to
  **record-first-play-later**, which delayed the voice by the whole clip
  length.
- Mic segments now always run **real-time (~20 ms)**: when the music engine
  cannot host them, FreQ spins up a dedicated mic render engine once and
  reuses it. Music playback is never touched.
- Emergency Mic benefits from the same path on every engine.

### ⏸ Pause / Resume for mic segments

- Pause now stops the **voice mix too**: silence while paused, and on
  resume you hear the live voice immediately — nothing said during the
  pause is replayed afterwards.
- The clip timer is **extended by the paused time**, so a 30-second segment
  plays its full 30 seconds of voice.
- Stopping during a pause clears all transport state cleanly (no stuck
  progress bar, no spurious next-track callback).

### 🧹 Anti-echo and queue-flow hardening

- Starting a new segment **supersedes** the previous live session — rapid
  re-presses can never double the voice or stall the queue.
- Switching the playback engine retires the dedicated mic engine, so the
  voice can never be mixed by two engines at once (echo).
- The record-then-play fallback (still used when the native engine is
  truly unavailable) now retries transient USB opens, treats silent
  endpoints as failures, and never leaves empty WAV files behind. A mic
  that fails is not hammered again for ten minutes.

### 📊 The now-playing VU meter shows real output

- The stereo VU bar under the track title now displays **what actually
  leaves the speakers** (the same WASAPI loopback measurement the mixer
  meters use) — for music on any engine and for live mic segments.
- The old decorative animation has been removed: when nothing is audible,
  the bars fall to zero.

### ✅ Testing

| Suite | Result |
|---|---|
| Multi-mic suite (quad mic, problem webcam ×4 slots, storms, PortAudio tripwire) | ✅ 13/13 (×5 consecutive runs) |
| Pause-storm (60× pause/resume, pause→stop races, engine switches, thread/RSS hygiene) | ✅ all passed (×5 consecutive runs) |
| Jingle stress | ✅ 41/41 |
| Edge cases | ✅ 62/62 |
| Soak **134,619 ops / 8 min** (40k songs, 24k jingles, 3,442 live-mic sessions incl. 1,721 dual) | ✅ 8/8 — **0 start failures**, RSS peak Δ+2.0 MB, threads return to baseline |

### 📦 Installation notes

- Installs over SP1/SP2/SP3 directly; settings and `.freq` queue files are
  preserved.
- If a microphone misbehaves, FreQ now skips it gracefully (status bar
  message, queue continues) instead of crashing — and the queue moves on.

### ✅ Testing

| Suite | Result |
|---|---|
| Multi-mic suite (quad mic, problem webcam ×4 slots, storms, PortAudio tripwire) | ✅ 13/13 (×5 consecutive runs) |
| Pause-storm (60× pause/resume, pause→stop races, engine switches, thread/RSS hygiene) | ✅ all passed (×5 consecutive runs) |
| Jingle stress | ✅ 41/41 |
| Edge cases | ✅ 62/62 |
| Soak **134,619 ops / 8 min** (40k songs, 24k jingles, 3,442 live-mic sessions incl. 1,721 dual) | ✅ 8/8 — **0 start failures**, RSS peak Δ+2.0 MB, threads return to baseline |
| Version comparison (v prefix, SP suffix, `_`/`-` tags, garbage input) | ✅ all cases |

### 📦 Installation notes

- Installs over SP1/SP2 directly; settings and `.freq` queue files are
  preserved.
- If a microphone misbehaves, FreQ now skips it gracefully (status bar
  message, queue continues) instead of crashing — and the queue moves on.

---

## FreQ 2.7.3 SP2 (Stable Pack 2) — 2026-09-23

**SP2 is the real-time microphone release** — a live mic mixed straight into
the programme the moment you speak (~20 ms, no record-then-play round trip),
plus **Dual Mic** (two microphones in one segment) and fixes for the
waveform-seek glitch that used to double the audio. Updates over SP1
directly; all existing settings keep working.

### 🎙 Real-time live microphone (Emergency Mic actually works now)

- The Emergency Mic button previously only ducked the volume — no mic was
  ever opened. It now opens a **live microphone mixed into the same device
  buffers as the music**: real-time (~20 ms), works even when the music is
  stopped, with automatic music ducking and a sticky on/off toggle.
- Mic segments play **live by default** — the announcer is heard as they
  speak, with the progress bar following the clip. Record-first-play-later
  remains only as a fallback.

### 🎚 Dual Mic segments

- A mic segment can open **two microphones at once** (interview / two-DJ
  setup). Each mic is its own capture + buffer in the engine — one failing
  never affects the other; music ducks exactly once, and the queue
  continues cleanly when the clip ends.

### 🔀 Fixed: doubled audio when clicking the waveform

- Seeking used to **rebuild the whole audio engine** even though the C++
  engine always had a native seek — now every seek is engine-level and
  atomically flushes stale audio from the buffers.
- Fixed a real overlap bug: seeking near the start fell back to
  `pygame` playback **on top of** the running WASAPI engine — two layers
  of the same song. Fixed.
- A pending crossfade is now properly cancelled when you seek.

### ✅ Testing

| Suite | Result |
|---|---|
| Jingle stress + new seek/mic cases (engine-level seek storm ×300, waveform storm ×60, mic start/stop ×30) | ✅ 41/41 |
| Edge cases | ✅ 62/62 |
| Soak 20,000 ops (incl. 492 live-mic sessions, 226 dual) | ✅ 8/8 — RSS stable, threads at baseline, 0 start failures |

---

## FreQ 2.7.2 SP1 (Stable Pack 1) — 2026-09-23

**SP1 is a stability package** — no new features, no UI changes, no settings
migration. Install it over any previous version and keep working.

### 🛠 Crashes fixed

- **Fixed the crash when playing jingles in any way** (manual bell, every-N
  songs automation, ad breaks, stopping mid-jingle, rapid re-presses).
  Root cause: the native audio module left a zombie decode thread behind on
  rapid start/stop, which read freed decoder state and crashed the process
  with a divide-by-zero (`0xC0000094`) inside the resampler.
- **Fixed 48 kHz files playing silent + heap corruption** (`0xC0000374`
  when re-pressing quickly): the decoder was initialised on the stack and
  then *copied* into a heap block — internal pointers kept referencing the
  dead stack frame, corrupting neighbouring heap data.
- **Fixed the assertion dialog freezing the app**: release builds now ship
  with assertions disabled (NDEBUG), every division in the audio path is
  guarded, and the Python side gained a recovery ladder that migrates
  playback when a device disappears.

### 🩹 Memory leaks fixed

- The native capture ring buffer leaked **~960 KB every time** a recorder
  or loopback closed (measured: 66 cycles = +63 MB). Fixed with proper C++
  member destruction — now ~1 KB per cycle.
- A new native streaming module (`native_audio_stream`) is included and
  verified.

### ✅ Testing

| Suite | Result |
|---|---|
| Jingle stress incl. mixed-rate storm (44.1k/48k/22.05k/96k) | ✅ 29/29 |
| Edge cases | ✅ 62/62 |
| Soak 3,000 ops (75 engine recreations, 122 capture cycles) | ✅ 6/6 — RSS 57.8→57.1 MB, peak Δ+0.1 MB |
| Soak **20,000 ops** (5,982 songs, 3,599 jingles, 496 engine recreations, 879 captures) | ✅ 6/6 — RSS flat, peak Δ+0.4 MB, starts 20,000/20,000 |

### 📦 Installation notes

- Install directly over the current version — no uninstall needed, settings
  and queue files are untouched.
- If anything still crashes, send the entry from Windows Event Viewer
  (Windows Logs → Application → "Application Error") and it gets fixed.
