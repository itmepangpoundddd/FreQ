# FreQ — Release Notes

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
