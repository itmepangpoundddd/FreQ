# FreQ — Radio Playlist Manager

FreQ is a desktop radio workspace for building playlists, playing local and
YouTube audio, scheduling station inserts, recording microphone segments, and
streaming to Icecast, Shoutcast, or WebRTC.

## Highlights

- Playlist queue with search, reorder, history, presets, and `.freq` files.
- Local audio playback and YouTube video/playlist importing.
- Automatic skip to the next track when a YouTube download fails.
- OBS-style five-channel mixer with mute, solo, gain, and VU meters.
- Scheduled jingles, commercial breaks, and national anthem playback.
- Timed microphone segments recorded directly into the queue.
- Playlist files: start a new `.freq`, or import the songs of other `.freq`
  playlists into the open queue (duplicates skipped) — `Ctrl+N` / `Ctrl+I`.
- Persistent stream metadata and Icecast source streaming through FFmpeg.
- Native C++ PCM analysis and processing with a Python fallback.
- Desktop GUI, Flask Web GUI, and command-line queue tools.
- As-run log: an append-only, hash-chained record of what aired, sealed daily
  and verifiable for the whole folder from the command line.

## Requirements

- Windows (WASAPI audio), macOS, or Linux
- Python 3.10 or newer
- FFmpeg **optional** for playback: MP3/WAV/FLAC/Ogg decode through the embedded miniaudio decoder (`native_audio_decode`), no FFmpeg needed. FFmpeg is still used for AAC/M4A playback, audio editing, normalization, and streaming (bundled copy in `deps/ffmpeg-essentials` or on `PATH`)
- Packages listed in `requirements.txt`

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run FreQ

Desktop application:

```bash
python gui.py
```

Web radio manager and public landing page:

```bash
python app.py
```

Then open:

- `http://localhost:5000/` — Web Radio Manager
- `http://localhost:5000/landing` — FreQ product website

Command-line queue manager:

```bash
python radio_manager.py
```

## Native Audio Meter

The optional C++ extension calculates stereo RMS and Peak levels, applies
per-channel gain and limiting to PCM audio, computes 32 log-frequency spectrum
bars for the visualizer (iterative radix-2 FFT, no numpy needed), aggregates
waveform peaks for the waveform display, converts legacy sample lists to PCM
(`samples_to_stereo_pcm`), detects leading/trailing silence for auto-cue
(`find_silence_end`), measures BS.1770-4 K-weighted
loudness with histogram gating for real-time stream normalization, and mixes
two PCM streams with equal-power cosine/sine curves for gapless crossfades
(`crossfade_mix`). A second
module (`native_audio_output`) manages output devices through WASAPI: it
enumerates render endpoints and switches the OS default speaker, so the whole
system (pygame playback included) follows the dropdown selection. A third
module (`native_audio_render`) is a full WASAPI render stream: an event-driven
C++ playback engine with a ~2 s ring buffer that plays decoded PCM directly on
the chosen endpoint — selecting a speaker routes FreQ's audio there without
touching the OS default (works even on Windows builds that cannot switch
defaults) — and mixes crossfade chunks live on one output clock. Python
automatically falls back to the portable implementation (same algorithms) if
the native extension is unavailable; on Windows builds without the
PolicyConfig class, the app opens the system sound settings instead.

Auto-cue skips silent heads so every track starts right at the music. Toggle
it with the ⏱ button in the playback controls (the choice is saved to the
`.freq` preset); detection is measured once per file and cached, then the
head is trimmed with the same ffmpeg mechanism seek uses.

Crossfade (🔄 button, saved to the `.freq` preset) blends the end of each
song into the beginning of the next: near the end of the current track the
player renders an equal-power overlap of the two (loudness-matched with the
BS.1770-4 meter, ±6 dB clamp), queues it with SDL so the switch between
buffers is sample-gapless, and updates the queue/UI exactly when the next
track becomes audible. YouTube and mic segments keep the classic
stop-between-tracks behavior, and everything falls back to sequential
playback when the native mixer is unavailable.

Loudness Auto-Gain (📢 button, saved to the `.freq` preset) plays every
track at the same perceived loudness: each file is measured once with the
BS.1770-4 meter (cached), and a ±6 dB gain is applied on the output path so
quiet songs never disappear next to loud ones. The WASAPI engine applies the
full boost; on the pygame fallback a boost is capped by the master volume
headroom. Crossfade's loudness match and Auto-Gain compose cleanly — both
sides of a fade land on the same target.

Playback runs through the embedded C++ decoder (`native_audio_decode`,
miniaudio) — no FFmpeg subprocess is spawned for supported formats, track
start is faster, and FreQ plays MP3/WAV/FLAC/Ogg even on machines without
FFmpeg at all. The microphone recorder uses native WASAPI capture as well.

The **Air-Check Recorder** (Settings tab) records everything the default
output device plays to a WAV file via native WASAPI loopback — manually
with one click, or automatically on a daily time window (saved to the
`.freq` preset). A live peak meter shows what is being captured, and the
master/mixer meters display real output levels from the same loopback tap.

Air-Check can capture the **master output, the microphone, or both at once**
(two separate files). On stop it can automatically export to **MP3**, and old
recordings can be pruned automatically by age (`Keep recordings` + Clean Now)
with the newest files listed right in the Settings tab.

The waveform display is clickable — jump anywhere in the track by clicking
the bar — and `Toggle Crossfade` has a rebindable hotkey (default `C`) in
the Keys tab. Icecast/Shoutcast relays reconnect on an exponential backoff
ladder (5 → 15 → 30 → 60 s) that resets after a healthy connection, with
the remaining time and attempt count shown in the stream status.

When `loudness_enabled` is set on the streaming configuration, FreQ measures
the integrated loudness of the program being streamed and smoothly retunes a
gain stage so the broadcast lands on the target LUFS — no per-file
`ffmpeg loudnorm` passes are needed. Enable it in code with:

```python
streamer.configure(loudness_enabled=True, loudness_target_lufs=-16.0)
```

## Plugins (Ultimate Platform)

FreQ has a plugin SDK: single-file Python programs in the `plugins\` folder
(next to `FreQ.exe`) that receive broadcast events — track started/finished,
app start/shutdown — and can report to the status line, write their own files,
call webhooks or drive external scripts. Plugins are **observe-only**: there
is no API to control playback, failures are fully contained, and each plugin
has an on/off switch in Settings → 🧩 Plugins (persisted in the `.freq`
preset). See **[PLUGIN-SDK.md](PLUGIN-SDK.md)** for the full guide with three
ready-to-copy examples, and `plugins/examples/` for a now-playing webhook.

On Windows, install Visual Studio Build Tools with a Windows SDK, then run:

```bash
python build_native_meter.py
```

The generated `.pyd` file is a local build artifact and is intentionally not
tracked by Git.

## Build Windows Installer

The one-command build pipeline reads the canonical version from `installer.iss`
and performs the native build, PyInstaller build, wizard image generation, and
Inno Setup compilation:

```bash
python release.py
```

The installer is written to the sibling `_final` directory. Inno Setup 6 must
be installed. If it is installed in a custom location, set:

```powershell
$env:INNO_SETUP_PATH="C:\Path\To\Inno Setup 6"
```

To push a prepared source commit and publish a GitHub Release, use:

```bash
python release.py --publish
```

Publishing requires Git, GitHub CLI, and an authenticated `gh` session. For
source-only commits without building a release:

```bash
python push_source_code.py --dry-run
python push_source_code.py
```

## Icecast Setup

1. Start Icecast.
2. Open FreQ Streaming settings and select `Icecast`.
3. Enter the server, port, source username, source password, and mountpoint.
4. Choose codec and bitrate, then start the stream.

FreQ keeps one encoder and source connection alive while tracks change. Use
the Icecast source credentials, not the web administrator password.

## Playlist files: New and Import

The header carries the file actions for the current queue:

- **🆕 New** (`Ctrl+N`) — start an empty playlist. It empties the **queue**
  only: the library, the history and the presets stay, and the save target goes
  back to `radio_queue.freq` so a later Save cannot overwrite the playlist you
  were editing. Nothing is written until you save.
- **📂 Open** (`Ctrl+O`) — replace everything with another `.freq` file.
- **📥 Import** (`Ctrl+I`) — pick one or more `.freq` files and **append** their
  songs to the open queue. The item on air keeps its slot, and a song already
  queued is skipped by identity (file path, YouTube URL, mic title+length), so
  importing the same file twice adds nothing. A file that cannot be read is
  named in the warning instead of silently contributing nothing.
- **💾 Save** (`Ctrl+S`) / **💾 Save As** — write the queue to disk.

Import copies song **entries**, not audio: a local path that does not exist
here still needs its file, and the other file's presets/settings are not merged.

## As-run log (what actually aired)

The **As-run** sidebar tab records every item that reached the output into
`logs/asrun/YYYY-MM-DD.jsonl` — append-only, one `start` row and one `end` row
per item, each row hash-chained to the one before it. It is **off by default**
and never raises into the playback path.

Each day is **sealed** into `YYYY-MM-DD.manifest.json` (counts, aired seconds,
file SHA-256 and the chain's `tail_hash`, which is what proves the sealed part
of a day that later grew was not rewritten). Sealing happens by itself: on the
first item of a new day, from the app while it runs, and from the 🔒 **ทั้งหมด**
button — a seal is **refused** (never overwritten) on a day whose chain is
broken or whose file shrank.

The same checks run from a command line, which is what a nightly task or an
inspector should use:

```bash
cd SFM
python asrun.py --verify-all          # the whole folder (default action)
python asrun.py --verify-all --json    # machine-readable, per day
python asrun.py --list                 # day-by-day summary
python asrun.py --seal-all             # seal every day that needs it
python asrun.py --seal 2026-10-18      # one day
python asrun.py --export 2026-10-18 --out day.csv
python asrun.py --prune --keep-days 400
```

Exit codes: **0** clean · **1** evidence broken or missing (a broken chain, a
truncated or re-written file, a deleted day whose manifest survived, an earlier
day that was never sealed) · **3** maintenance due (a sealed day that was
extended — run `--seal-all`) · **2** the command itself was wrong. `--today`
re-checks a past day as if it were today, and `--allow-unsealed` downgrades a
never-sealed day from a problem to maintenance.

`--seal-all` never seals **today** (the day is still airing — use
`--seal DATE`) and never seals over a broken chain, but it still audits the
folder afterwards, so it exits **1** when any day is broken rather than
reporting success.

## Project Layout

```text
SFM/
├── gui.py                 Desktop GUI
├── asrun.py               As-run log, daily seal and the verify command line
├── app.py                 Flask web interface and landing page
├── radio_manager.py       Queue model and CLI
├── player.py              Local playback and YouTube downloads
├── streaming.py           Icecast, Shoutcast, and WebRTC
├── audio_meter.py         Native/fallback PCM meter API
├── native_audio_meter.cpp C++ audio processing extension
├── templates/             Web UI templates
├── installer.iss          Inno Setup configuration and version source
├── release.py             Build and release pipeline
└── requirements.txt       Python dependencies
```

## License

FreQ is released under the GNU General Public License v3.0. See `LICENSE` for
the complete license text. Commercial licensing information is in
`COMMERCIAL_LICENSE.md`.
