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
- Persistent stream metadata and Icecast source streaming through FFmpeg.
- Native C++ PCM analysis and processing with a Python fallback.
- Desktop GUI, Flask Web GUI, and command-line queue tools.

## Requirements

- Windows, macOS, or Linux
- Python 3.10 or newer
- FFmpeg on `PATH`, or the optional bundled copy in `deps/ffmpeg-essentials`
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

The optional C++ extension calculates stereo RMS and Peak levels and applies
per-channel gain and limiting to PCM audio. Python automatically falls back to
the portable implementation if the native extension is unavailable.

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

## Project Layout

```text
SFM/
├── gui.py                 Desktop GUI
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
