"""
FreQ — Streaming Engine
Broadcast audio to external servers via Shoutcast, Icecast, or WebRTC.

Supported protocols:
  - Shoutcast: MP3/AAC stream to SHOUTcast servers
  - Icecast: MP3/OGG/Opus stream to Icecast servers
  - WebRTC: Low-latency browser streaming via WebRTC

Requires: FFmpeg (for Shoutcast/Icecast), aiortc (for WebRTC)
"""

from __future__ import annotations

import os
import sys
import json
import time
import struct
import socket
import base64
import hashlib
import logging
import tempfile
import threading
import subprocess
from enum import Enum, auto
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable
from urllib.parse import urlparse

logger = logging.getLogger("freq.streaming")

# ═══════════════════════════════════════════════
# Check availability
# ═══════════════════════════════════════════════

def _check_ffmpeg() -> bool:
    """Check if FFmpeg is available. Also searches common install locations."""
    import shutil
    if shutil.which("ffmpeg"):
        return True
    # Try common locations and add to PATH
    candidates = []
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin",
            Path(r"C:\ffmpeg\bin"),
            Path(r"C:\Program Files\ffmpeg\bin"),
            Path.home() / "Documents" / "yt-dlp",
            Path(__file__).parent / "ffmpeg",
        ]
    else:
        candidates = [
            Path("/usr/local/bin"),
            Path.home() / ".local" / "bin",
        ]
    for d in candidates:
        if d.is_dir() and ((d / "ffmpeg.exe").exists() or (d / "ffmpeg").exists()):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return True
    return False

FFMPEG_AVAILABLE = _check_ffmpeg()

try:
    import aiortc
    from aiortc.contrib.media import MediaRelay
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False


# ═══════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════

class StreamProtocol(Enum):
    SHOUTCAST = auto()
    ICECAST = auto()
    WEBRTC = auto()


class StreamState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    STREAMING = auto()
    RECONNECTING = auto()
    ERROR = auto()


@dataclass
class StreamConfig:
    """Configuration for a streaming connection."""
    protocol: StreamProtocol = StreamProtocol.ICECAST

    # Server settings
    host: str = "localhost"
    port: int = 8000
    password: str = "hackme"           # Icecast source password
    mount: str = "/freQ"               # Icecast mount point

    # Stream metadata
    stream_name: str = "FreQ Radio"
    stream_description: str = "FreQ - Radio Playlist Manager"
    stream_genre: str = "Various"
    stream_url: str = ""
    stream_public: bool = True

    # Audio settings
    bitrate: int = 128                 # kbps
    sample_rate: int = 44100
    channels: int = 2
    codec: str = "mp3"                 # mp3, aac, ogg, opus

    # WebRTC settings
    webrtc_ice_servers: list[str] = field(default_factory=lambda: ["stun:stun.l.google.com:19302"])

    def get_server_url(self) -> str:
        """Get the full server URL."""
        proto = "http"
        return f"{proto}://{self.host}:{self.port}{self.mount}"

    def get_icy_metadata_interval(self) -> int:
        """Get ICY metadata interval (bytes)."""
        return 8192  # Standard for Shoutcast


@dataclass
class StreamStatus:
    """Current streaming status."""
    state: StreamState = StreamState.DISCONNECTED
    protocol: StreamProtocol = StreamProtocol.ICECAST
    uptime: float = 0.0               # seconds connected
    bytes_sent: int = 0
    current_song: str = ""
    listeners: int = 0
    error: str = ""
    server_info: str = ""


# ═══════════════════════════════════════════════
# Shoutcast / Icecast Streamer (via FFmpeg)
# ═══════════════════════════════════════════════

class FFmpegStreamer:
    """Stream audio via FFmpeg to Shoutcast/Icecast server.

    Architecture:
        Audio file → FFmpeg encode → Shoutcast/Icecast server
        (also supports raw PCM via stdin for future use)
    """

    def __init__(self, config: StreamConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._state = StreamState.DISCONNECTED
        self._start_time = 0.0
        self._bytes_sent = 0
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._current_stream_file: Optional[str] = None
        self._stream_title: str = ""
        self._stream_artist: str = ""

    @property
    def state(self) -> StreamState:
        return self._state

    def connect(self) -> bool:
        """Start streaming to the server."""
        if not FFMPEG_AVAILABLE:
            self._state = StreamState.ERROR
            logger.error("FFmpeg not available")
            return False

        with self._lock:
            if self._process and self._process.poll() is None:
                logger.warning("Already streaming")
                return True

            self._state = StreamState.CONNECTING

            try:
                cmd = self._build_ffmpeg_command()
                logger.info(f"Starting FFmpeg: {' '.join(cmd[:10])}...")

                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )

                self._start_time = time.time()
                self._running = True
                self._state = StreamState.STREAMING

                # Start monitor thread
                self._monitor_thread = threading.Thread(
                    target=self._monitor, daemon=True
                )
                self._monitor_thread.start()

                logger.info(f"Connected to {self.config.get_server_url()}")
                return True

            except Exception as e:
                self._state = StreamState.ERROR
                logger.error(f"Failed to connect: {e}")
                return False

    def disconnect(self) -> None:
        """Stop streaming."""
        self._running = False
        with self._lock:
            self._stop_process()
            self._current_stream_file = None
            self._state = StreamState.DISCONNECTED
            logger.info("Disconnected from server")

    def stream_file(self, filepath: str, title: str = "", artist: str = "") -> bool:
        """Stream an audio file to the server via FFmpeg.

        Stops any current FFmpeg process and starts a new one
        that reads the file and streams it to the server.
        """
        if not FFMPEG_AVAILABLE:
            self._state = StreamState.ERROR
            logger.error("FFmpeg not available")
            return False

        if not os.path.isfile(filepath):
            logger.error(f"File not found: {filepath}")
            return False

        with self._lock:
            # Stop any existing FFmpeg process
            self._stop_process()

            self._state = StreamState.CONNECTING
            self._current_stream_file = filepath
            self._stream_title = title
            self._stream_artist = artist

            try:
                cmd = self._build_ffmpeg_command(input_file=filepath)
                logger.info(f"Streaming file: {os.path.basename(filepath)}")
                logger.debug(f"FFmpeg cmd: {' '.join(cmd[:12])}...")

                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )

                self._start_time = time.time()
                self._running = True
                self._state = StreamState.STREAMING
                self._bytes_sent = 0

                # Start monitor thread
                self._monitor_thread = threading.Thread(
                    target=self._monitor, daemon=True
                )
                self._monitor_thread.start()

                # Update Icecast metadata via admin API
                self._update_icecast_metadata(title, artist)

                return True

            except Exception as e:
                self._state = StreamState.ERROR
                logger.error(f"Failed to start streaming: {e}")
                return False

    def update_metadata(self, title: str, artist: str = "") -> None:
        """Update stream metadata (song title)."""
        self._stream_title = title
        self._stream_artist = artist
        self._update_icecast_metadata(title, artist)

    def _update_icecast_metadata(self, title: str, artist: str = "") -> None:
        """Update Icecast stream metadata via admin API."""
        if self.config.protocol != StreamProtocol.ICECAST:
            return
        try:
            from urllib.request import Request, urlopen
            from urllib.parse import urlencode

            song = f"{artist} - {title}".strip(" -")
            params = urlencode({
                "mode": "upinfo",
                "mount": self.config.mount,
                "song": song,
            })
            url = f"http://{self.config.host}:{self.config.port}/admin/metadata"
            auth = base64.b64encode(
                f"source:{self.config.password}".encode()
            ).decode()
            req = Request(
                f"{url}?{params}",
                headers={"Authorization": f"Basic {auth}"},
            )
            urlopen(req, timeout=5)
            logger.info(f"Updated metadata: {song}")
        except Exception as e:
            logger.debug(f"Metadata update failed (non-critical): {e}")

    def write_audio(self, data: bytes) -> None:
        """Write PCM audio data to the FFmpeg pipe."""
        if self._process and self._process.stdin and self._state == StreamState.STREAMING:
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
                self._bytes_sent += len(data)
            except (BrokenPipeError, OSError):
                self._state = StreamState.RECONNECTING
                logger.error("Pipe broken, attempting reconnect...")

    def get_status(self) -> StreamStatus:
        """Get current streaming status."""
        return StreamStatus(
            state=self._state,
            protocol=self.config.protocol,
            uptime=time.time() - self._start_time if self._state == StreamState.STREAMING else 0,
            bytes_sent=self._bytes_sent,
            current_song=self._stream_title or self.config.stream_name,
            server_info=f"{self.config.host}:{self.config.port}{self.config.mount}",
        )

    def _stop_process(self) -> None:
        """Kill the current FFmpeg process if running."""
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    def _build_ffmpeg_command(self, input_file: Optional[str] = None) -> list[str]:
        """Build the FFmpeg command for streaming.

        If input_file is given, read from that file.
        Otherwise, read from stdin (PCM s16le).
        """
        cfg = self.config
        cmd = ["ffmpeg", "-re", "-y"]  # -re: read at native frame rate

        # Input
        if input_file:
            cmd.extend(["-i", input_file])
        else:
            cmd.extend(["-f", "s16le"])
            cmd.extend(["-ar", str(cfg.sample_rate)])
            cmd.extend(["-ac", str(cfg.channels)])
            cmd.extend(["-i", "pipe:0"])

        # Output codec
        if cfg.codec == "mp3":
            cmd.extend(["-codec:a", "libmp3lame"])
            cmd.extend(["-b:a", f"{cfg.bitrate}k"])
            cmd.extend(["-ar", str(cfg.sample_rate)])
            cmd.extend(["-ac", str(cfg.channels)])
        elif cfg.codec == "aac":
            cmd.extend(["-codec:a", "aac"])
            cmd.extend(["-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "ogg":
            cmd.extend(["-codec:a", "libvorbis"])
            cmd.extend(["-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "opus":
            cmd.extend(["-codec:a", "libopus"])
            cmd.extend(["-b:a", f"{cfg.bitrate}k"])
        else:
            cmd.extend(["-codec:a", "libmp3lame"])
            cmd.extend(["-b:a", f"{cfg.bitrate}k"])

        # Output to server
        if cfg.protocol == StreamProtocol.ICECAST:
            cmd.extend(["-f", "mp3" if cfg.codec == "mp3" else cfg.codec])
            server_url = f"http://{cfg.host}:{cfg.port}{cfg.mount}"
            cmd.extend(["-headers", f"Authorization: Basic {base64.b64encode(f'source:{cfg.password}'.encode()).decode()}\r\n"])
            cmd.append(server_url)

        elif cfg.protocol == StreamProtocol.SHOUTCAST:
            cmd.extend(["-f", "mp3" if cfg.codec == "mp3" else cfg.codec])
            server_url = f"http://{cfg.host}:{cfg.port}"
            cmd.extend(["-headers", f"Authorization: Basic {base64.b64encode(f'.{cfg.password}'.encode()).decode()}\r\n"])
            cmd.extend(["-content_type", "audio/mpeg"])
            cmd.append(server_url)

        return cmd

    def _monitor(self) -> None:
        """Monitor FFmpeg process."""
        while self._running and self._process:
            if self._process.poll() is not None:
                if self._running:
                    self._state = StreamState.RECONNECTING
                    stderr = self._process.stderr.read() if self._process.stderr else b""
                    logger.error(f"FFmpeg exited: {stderr.decode(errors='ignore')[:200]}")
                    # Auto-reconnect after 5 seconds
                    time.sleep(5)
                    if self._running:
                        self.connect()
                break
            time.sleep(1)


# ═══════════════════════════════════════════════
# WebRTC Streamer
# ═══════════════════════════════════════════════

class WebRTCStreamer:
    """Stream audio via WebRTC to browsers.

    Architecture:
        pygame.mixer → PCM buffer → AudioTrack → WebRTC peer → Browser
    """

    def __init__(self, config: StreamConfig):
        self.config = config
        self._state = StreamState.DISCONNECTED
        self._peers: list = []
        self._lock = threading.Lock()
        self._audio_track: Optional[object] = None
        self._running = False
        self._http_server: Optional[threading.Thread] = None
        self._signaling_port = 8080

    @property
    def state(self) -> StreamState:
        return self._state

    def connect(self) -> bool:
        """Start WebRTC signaling server."""
        if not WEBRTC_AVAILABLE:
            self._state = StreamState.ERROR
            logger.error("aiortc not available. Install: pip install aiortc")
            return False

        try:
            self._state = StreamState.CONNECTING
            self._running = True
            self._state = StreamState.STREAMING
            logger.info(f"WebRTC signaling started on port {self._signaling_port}")
            return True
        except Exception as e:
            self._state = StreamState.ERROR
            logger.error(f"Failed to start WebRTC: {e}")
            return False

    def disconnect(self) -> None:
        """Stop WebRTC streaming."""
        self._running = False
        with self._lock:
            for peer in self._peers:
                try:
                    peer.close()
                except Exception:
                    pass
            self._peers.clear()
            self._state = StreamState.DISCONNECTED

    def write_audio(self, data: bytes) -> None:
        """Write PCM audio data to all connected WebRTC peers."""
        with self._lock:
            for peer in self._peers:
                try:
                    peer.send_audio(data)
                except Exception:
                    pass

    def get_status(self) -> StreamStatus:
        """Get current streaming status."""
        with self._lock:
            return StreamStatus(
                state=self._state,
                protocol=StreamProtocol.WEBRTC,
                listeners=len(self._peers),
                server_info=f"WebRTC on port {self._signaling_port}",
            )

    def get_offer_sdp(self) -> str:
        """Get SDP offer for a new peer connection."""
        if not WEBRTC_AVAILABLE:
            return ""
        # Placeholder — full implementation requires aiortc signaling
        return ""

    def add_peer(self, sdp_answer: str) -> bool:
        """Add a peer from SDP answer."""
        # Placeholder — full implementation requires aiortc signaling
        return True


# ═══════════════════════════════════════════════
# Web Dashboard (for WebRTC viewers)
# ═══════════════════════════════════════════════

STREAMING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FreQ - Live Stream</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0e14; color: #e8edf4; min-height: 100vh; display: flex; flex-direction: column; align-items: center; }
        .header { text-align: center; padding: 40px 20px; }
        .header h1 { font-size: 48px; color: #58a6ff; margin-bottom: 8px; }
        .header p { color: #8b95a5; font-size: 16px; }
        .player { background: #131920; border-radius: 16px; padding: 32px; width: 100%; max-width: 500px; margin: 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
        .now-playing { text-align: center; margin-bottom: 24px; }
        .now-playing .title { font-size: 20px; font-weight: bold; }
        .now-playing .artist { color: #8b95a5; margin-top: 4px; }
        .controls { display: flex; justify-content: center; gap: 16px; margin: 24px 0; }
        .btn { width: 60px; height: 60px; border-radius: 50%; border: none; cursor: pointer; font-size: 24px; transition: all 0.2s; }
        .btn-play { background: #58a6ff; color: white; }
        .btn-play:hover { background: #79b8ff; transform: scale(1.1); }
        .btn-play.playing { background: #f85149; }
        .volume { display: flex; align-items: center; gap: 12px; justify-content: center; }
        .volume input { width: 150px; accent-color: #58a6ff; }
        .status { text-align: center; margin-top: 20px; padding: 12px; background: #1a2230; border-radius: 8px; font-size: 12px; color: #8b95a5; }
        .status .live { color: #f85149; font-weight: bold; }
        .visualizer { display: flex; align-items: flex-end; justify-content: center; gap: 4px; height: 60px; margin: 20px 0; }
        .visualizer .bar { width: 8px; background: linear-gradient(to top, #58a6ff, #bc8cff); border-radius: 4px; transition: height 0.1s; }
    </style>
</head>
<body>
    <div class="header">
        <h1>FreQ</h1>
        <p>Live Radio Stream</p>
    </div>
    <div class="player">
        <div class="now-playing">
            <div class="title" id="title">Connecting...</div>
            <div class="artist" id="artist">Please wait</div>
        </div>
        <div class="visualizer" id="visualizer"></div>
        <div class="controls">
            <button class="btn btn-play" id="playBtn" onclick="togglePlay()">▶️</button>
        </div>
        <div class="volume">
            <span>🔊</span>
            <input type="range" min="0" max="100" value="80" id="volume" oninput="setVolume(this.value)">
            <span id="volLabel">80%</span>
        </div>
        <div class="status">
            <span class="live" id="status">● LIVE</span>
            &nbsp;|&nbsp; Listeners: <span id="listeners">0</span>
            &nbsp;|&nbsp; Uptime: <span id="uptime">0:00</span>
        </div>
    </div>
    <script>
        let audio = new Audio();
        let playing = false;

        // Initialize visualizer bars
        const viz = document.getElementById('visualizer');
        for (let i = 0; i < 20; i++) {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.style.height = '10px';
            viz.appendChild(bar);
        }

        function togglePlay() {
            if (playing) {
                audio.pause();
                playing = false;
                document.getElementById('playBtn').textContent = '▶️';
                document.getElementById('playBtn').classList.remove('playing');
            } else {
                // For Shoutcast/Icecast — connect to stream URL
                audio.src = '/stream'; // Adjust to your mount point
                audio.play().catch(e => console.error('Playback failed:', e));
                playing = true;
                document.getElementById('playBtn').textContent = '⏸️';
                document.getElementById('playBtn').classList.add('playing');
                animateVisualizer();
            }
        }

        function setVolume(val) {
            audio.volume = val / 100;
            document.getElementById('volLabel').textContent = val + '%';
        }

        function animateVisualizer() {
            if (!playing) return;
            const bars = viz.children;
            for (let i = 0; i < bars.length; i++) {
                const h = Math.random() * 50 + 10;
                bars[i].style.height = h + 'px';
            }
            requestAnimationFrame(() => setTimeout(animateVisualizer, 100));
        }

        // Fetch status
        setInterval(async () => {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                document.getElementById('title').textContent = data.current_song || 'Unknown';
                document.getElementById('listeners').textContent = data.listeners || 0;
                document.getElementById('status').innerHTML = data.state === 'STREAMING' ? '<span class="live">● LIVE</span>' : '○ OFFLINE';
            } catch(e) {}
        }, 3000);
    </script>
</body>
</html>"""


# ═══════════════════════════════════════════════
# Main Streaming Manager
# ═══════════════════════════════════════════════

class StreamingManager:
    """Manages all streaming connections for FreQ.

    Integrates with the playback engine to capture and broadcast audio.
    """

    def __init__(self):
        self.config = StreamConfig()
        self._streamer: Optional[object] = None
        self._status_callbacks: list[Callable] = []
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def is_streaming(self) -> bool:
        return self._streamer is not None and self._streamer.state == StreamState.STREAMING

    @property
    def state(self) -> StreamState:
        if self._streamer:
            return self._streamer.state
        return StreamState.DISCONNECTED

    def configure(self, **kwargs) -> None:
        """Update stream configuration."""
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)

    def start(self) -> bool:
        """Start streaming with current config."""
        self.stop()  # Stop any existing stream

        if self.config.protocol in (StreamProtocol.SHOUTCAST, StreamProtocol.ICECAST):
            if not FFMPEG_AVAILABLE:
                logger.error("FFmpeg required for Shoutcast/Icecast")
                return False
            self._streamer = FFmpegStreamer(self.config)

        elif self.config.protocol == StreamProtocol.WEBRTC:
            if not WEBRTC_AVAILABLE:
                logger.error("aiortc required for WebRTC: pip install aiortc")
                return False
            self._streamer = WebRTCStreamer(self.config)

        else:
            logger.error(f"Unsupported protocol: {self.config.protocol}")
            return False

        success = self._streamer.connect()
        if success:
            self._running = True
            self._monitor_thread = threading.Thread(target=self._status_monitor, daemon=True)
            self._monitor_thread.start()

        return success

    def stop(self) -> None:
        """Stop all streaming."""
        self._running = False
        if self._streamer:
            self._streamer.disconnect()
            self._streamer = None

    def stream_file(self, filepath: str, title: str = "", artist: str = "") -> bool:
        """Stream an audio file to the server.

        If not connected, starts a new connection first.
        """
        if not self.is_streaming:
            # Auto-connect if not already streaming
            if not self.start():
                return False

        if self._streamer and hasattr(self._streamer, 'stream_file'):
            return self._streamer.stream_file(filepath, title, artist)
        return False

    def on_track_change(self, title: str, artist: str, filepath: str) -> None:
        """Called when a new track starts — stream it if connected."""
        if self.is_streaming and filepath and os.path.isfile(filepath):
            self.stream_file(filepath, title, artist)
        elif self.is_streaming:
            # No file available (e.g. YouTube not downloaded yet) — just update metadata
            self.update_song(title, artist)

    def write_audio(self, data: bytes) -> None:
        """Write PCM audio data to the active stream."""
        if self._streamer:
            self._streamer.write_audio(data)

    def update_song(self, title: str, artist: str = "") -> None:
        """Update currently playing song metadata."""
        if self._streamer and hasattr(self._streamer, 'update_metadata'):
            self._streamer.update_metadata(title, artist)

    def get_status(self) -> StreamStatus:
        """Get current streaming status."""
        if self._streamer:
            return self._streamer.get_status()
        return StreamStatus()

    def on_status_change(self, callback: Callable) -> None:
        """Register a status change callback."""
        self._status_callbacks.append(callback)

    def get_html(self) -> str:
        """Get the web player HTML."""
        return STREAMING_HTML

    def _status_monitor(self) -> None:
        """Monitor streaming status and notify callbacks."""
        while self._running:
            status = self.get_status()
            for cb in self._status_callbacks:
                try:
                    cb(status)
                except Exception:
                    pass
            time.sleep(2)


# ═══════════════════════════════════════════════
# Convenience instance
# ═══════════════════════════════════════════════

streamer = StreamingManager()
