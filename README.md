# FreQ Radio Playlist Manager

FreQ is a desktop radio playlist manager with local audio playback, YouTube
integration, scheduled jingles and live streaming to Icecast or Shoutcast.

## Features

- Playlist management: add, remove, insert, reorder and search tracks.
- Local audio files and YouTube downloads.
- Play, pause, resume, seek, skip and volume control.
- Play history and saved JSON playlists.
- Scheduled and manual jingles.
- Timed microphone segments that can be placed and reordered in the queue.
- Commercial break support.
- Icecast, Shoutcast and WebRTC streaming modes.
- Stream metadata updates for the current song.
- Desktop, web and command-line interfaces.

## Streaming Architecture

For Icecast, FreQ acts as a source client. It does not connect to BUTT. Both
FreQ and BUTT are source clients that send audio to the same Icecast server.

The current Icecast pipeline is:

```text
Current track or jingle
        |
        v
FFmpeg decodes the file to PCM
        |
        v
PCM buffer
        |
        v
One persistent FFmpeg encoder
        |
        v
Icecast source connection and mountpoint
```

The Icecast socket and encoder stay open while tracks change. FreQ does not
reconnect the mountpoint for every song. This avoids unnecessary interruptions
when a song changes or a jingle is inserted.

The source handshake is compatible with BUTT and supports both methods:

1. `PUT /mountpoint HTTP/1.1`
2. `SOURCE /mountpoint HTTP/1.0` as a fallback

Authentication uses the Icecast source username and source password. The
Icecast administrator password is not used for streaming.

## Requirements

- Windows, macOS or Linux
- Python 3.10 or newer
- FFmpeg available on `PATH`, or bundled in `deps/ffmpeg-essentials`
- Python packages listed in `requirements.txt`

## Installation

```bash
python -m pip install -r requirements.txt
```

For a minimal desktop setup:

```bash
python -m pip install customtkinter pygame yt-dlp sounddevice
```

## Running FreQ

### Desktop GUI

```bash
python gui.py
```

### Web GUI

```bash
python app.py
```

Open `http://localhost:5000` in a browser.

### Command line

```bash
python radio_manager.py
```

## Icecast Setup

1. Start the Icecast server.
2. Open the FreQ Streaming settings.
3. Select `Icecast`.
4. Enter the Icecast server address and port.
5. Enter the source username and source password.
6. Enter a unique mountpoint such as `/test`.
7. Select the codec and bitrate.
8. Start the stream.

The mountpoint should appear in the Icecast status page after FreQ has sent
audio data. A mountpoint that is already being used normally returns HTTP 403.
Invalid source credentials normally return HTTP 401.

## Microphone Queue

Use `Add Mic Segment to Queue` in the Add tab to create a timed microphone
item. Choose the input device and recording duration. The item is stored in
the same queue as music and can be moved, removed and played in any order.

When the item becomes current, FreQ records the microphone in the background,
plays the resulting WAV file, and sends that recording to the active stream.
The GUI remains responsive during recording.

## Project Structure

```text
SFM/
├── gui.py                         Desktop GUI and playback controls
├── app.py                         Flask web interface
├── radio_manager.py               Playlist model and CLI
├── player.py                      Local playback and playback callbacks
├── streaming.py                   FFmpeg, Icecast, Shoutcast and WebRTC
├── audio.py                       Audio device discovery
├── templates/                     Web UI templates
├── deps/ffmpeg-essentials/        Optional bundled FFmpeg binaries
├── requirements.txt               Python dependencies
├── build.py                       PyInstaller build script
└── README.md                     Project documentation
```

## Build a Windows Executable

Build the desktop application with PyInstaller:

```bash
python build.py
```

The output is created at:

```text
dist/FreQ/FreQ.exe
```

To create a portable archive after building, run `build_portable.bat`.

For troubleshooting, build with a console window:

```bash
python build.py --debug
```

## Troubleshooting

### HTTP 401: rejected username or password

Use the Icecast source credentials, normally the source username and source
password. Do not use the Icecast web administration password unless the server
configuration explicitly uses the same value.

### HTTP 403: mountpoint already in use

Stop BUTT or another source using the same mountpoint, or choose another
mountpoint.

### Stream connects but no mountpoint appears

Check that FFmpeg is available, the selected audio file exists, and FreQ is
actually playing a track. A successful connection without audio data may not
create a visible active mountpoint on every Icecast configuration.

### A short pause occurs during a transition

The local player and stream encoder use separate audio paths. The Icecast
connection remains persistent, but the next file still needs to be decoded and
buffered. The long-term architecture can share one PCM bus between local audio
output and the Stream process to make both paths sample-identical.

## Related Source

The BUTT source tree used for protocol comparison is kept separately at:

```text
C:\Users\SocieticsTv\Documents\SFM\butt-master
```

The main BUTT Icecast implementation is `src/icecast.cpp`. FreQ implements
the compatible source handshake independently in `streaming.py`.

## License

FreQ is released under the **GNU General Public License v3.0 (GPL-3.0)**.

See `LICENSE` for the complete license terms.
