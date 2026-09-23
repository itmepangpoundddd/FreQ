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
import queue
from enum import Enum, auto
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable
from urllib.parse import quote, urlparse

from audio_meter import LoudnessNormalizer, meter as audio_meter

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
            Path(__file__).parent / "deps" / "ffmpeg-essentials",
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
    username: str = "source"
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

    # Loudness normalization (BS.1770-4, measured on the PCM tap)
    loudness_enabled: bool = False
    loudness_target_lufs: float = -16.0
    loudness_max_gain_db: float = 12.0

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

# Reconnect backoff ladder for live streams (seconds). The first failed
# connection retries after 5s; consecutive failures escalate, capped at 60s.
RECONNECT_DELAYS: tuple = (5, 15, 30, 60)


def reconnect_delay_for(attempt: int) -> int:
    """Backoff delay (s) for the Nth consecutive reconnect attempt."""
    return RECONNECT_DELAYS[min(max(int(attempt), 0), len(RECONNECT_DELAYS) - 1)]


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
        self._audio_thread: Optional[threading.Thread] = None
        self._input_process: Optional[subprocess.Popen] = None
        self._input_thread: Optional[threading.Thread] = None
        self._pcm_writer_thread: Optional[threading.Thread] = None
        self._input_stop_event = threading.Event()
        self._pcm_queue: queue.Queue[bytes] = queue.Queue(maxsize=160)
        self._icecast_socket: Optional[socket.socket] = None
        self._running = False
        self._reconnect_attempt = 0
        self._reconnect_eta = 0.0
        self._current_stream_file: Optional[str] = None
        self._stream_title: str = ""
        self._stream_artist: str = ""
        self._loudness = LoudnessNormalizer(
            sample_rate=config.sample_rate,
            target_lufs=config.loudness_target_lufs,
            max_gain_db=config.loudness_max_gain_db,
        )

    @property
    def state(self) -> StreamState:
        return self._state

    @property
    def reconnect_attempt(self) -> int:
        """Consecutive reconnect attempts since the last healthy stream."""
        return self._reconnect_attempt

    @property
    def reconnect_eta(self) -> float:
        """Unix timestamp of the next reconnect attempt (0 = not waiting)."""
        return self._reconnect_eta

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
                if self.config.protocol == StreamProtocol.ICECAST:
                    self._start_icecast_stream()
                    return True

                cmd = self._build_ffmpeg_command()
                logger.info(f"Starting FFmpeg: {' '.join(cmd[:10])}...")

                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                time.sleep(0.1)
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read() if self._process.stderr else b""
                    raise RuntimeError(
                        f"FFmpeg exited: {stderr.decode(errors='ignore')[:300]}"
                    )

                self._start_time = time.time()
                self._running = True
                self._state = StreamState.STREAMING

                # Start monitor thread
                self._monitor_thread = threading.Thread(
                    target=self._monitor, args=(self._process, False), daemon=True
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
        self._reconnect_attempt = 0
        self._reconnect_eta = 0.0
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
            if self.config.protocol == StreamProtocol.ICECAST and self._state == StreamState.STREAMING:
                # Keep the Icecast connection and encoder alive between tracks.
                self._current_stream_file = filepath
                self._stream_title = title
                self._stream_artist = artist
                self._start_pcm_input(filepath)
                self._update_icecast_metadata(title, artist)
                return True

            # Stop any existing FFmpeg process
            self._stop_process()

            self._state = StreamState.CONNECTING
            self._current_stream_file = filepath
            self._stream_title = title
            self._stream_artist = artist

            try:
                if self.config.protocol == StreamProtocol.ICECAST:
                    self._start_icecast_stream(filepath)
                    self._update_icecast_metadata(title, artist)
                    return True

                cmd = self._build_ffmpeg_command(input_file=filepath)
                logger.info(f"Streaming file: {os.path.basename(filepath)}")
                logger.debug(f"FFmpeg cmd: {' '.join(cmd[:12])}...")

                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                time.sleep(0.1)
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read() if self._process.stderr else b""
                    raise RuntimeError(
                        f"FFmpeg exited: {stderr.decode(errors='ignore')[:300]}"
                    )

                self._start_time = time.time()
                self._running = True
                self._state = StreamState.STREAMING
                self._bytes_sent = 0

                # Start monitor thread
                self._monitor_thread = threading.Thread(
                    target=self._monitor, args=(self._process, True), daemon=True
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
        if self._loudness is not None:
            # Per-track loudness history would bias the measurement once the
            # program changes; start fresh for each new track.
            self._loudness.reset()

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
        if self._state != StreamState.STREAMING or not data:
            return
        if self.config.channels == 2:
            audio_meter.publish_s16le_stereo(data)
        if (
            self.config.channels == 2
            and self.config.loudness_enabled
            and self._loudness is not None
        ):
            gain = self._loudness.write(data)
            if gain != 1.0:
                data = process_s16le_stereo(data, gain, gain)
        try:
            self._pcm_queue.put_nowait(data)
        except queue.Full:
            # Drop the oldest audio block instead of blocking the playback thread.
            try:
                self._pcm_queue.get_nowait()
                self._pcm_queue.put_nowait(data)
            except queue.Empty:
                pass

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
        self._input_stop_event.set()
        if self._input_process:
            try:
                self._input_process.terminate()
            except Exception:
                pass
            self._input_process = None
        self._input_thread = None
        if self._icecast_socket:
            try:
                self._icecast_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._icecast_socket.close()
            except OSError:
                pass
            self._icecast_socket = None
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
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                break

    def _start_icecast_stream(self, input_file: Optional[str] = None) -> None:
        """Connect using Icecast's source protocol and feed it FFmpeg output."""
        self._stop_process()
        self._icecast_socket = self._connect_icecast()

        cmd = self._build_ffmpeg_pcm_command()
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        time.sleep(0.1)
        if self._process.poll() is not None:
            stderr = self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(f"FFmpeg exited: {stderr.decode(errors='ignore')[:300]}")

        self._start_time = time.time()
        self._bytes_sent = 0
        self._running = True
        self._state = StreamState.STREAMING
        process = self._process
        self._audio_thread = threading.Thread(
            target=self._send_ffmpeg_output, args=(process, self._icecast_socket), daemon=True
        )
        self._audio_thread.start()
        self._pcm_writer_thread = threading.Thread(
            target=self._write_pcm_input, args=(process,), daemon=True
        )
        self._pcm_writer_thread.start()
        self._input_stop_event.clear()
        if input_file and os.path.isfile(input_file):
            self._start_pcm_input(input_file)

    def _start_pcm_input(self, filepath: str) -> None:
        """Decode one file to PCM and feed the persistent encoder."""
        self._input_stop_event.set()
        if self._input_process:
            try:
                self._input_process.terminate()
            except Exception:
                pass

        # Do not let buffered samples from the previous track leak into this one.
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                break

        cmd = [
            "ffmpeg", "-v", "error", "-re", "-i", filepath,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(self.config.sample_rate), "-ac", str(self.config.channels),
            "pipe:1",
        ]
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        self._input_process = process
        stop_event = threading.Event()
        self._input_stop_event = stop_event

        def _feed() -> None:
            try:
                while not stop_event.is_set() and process.stdout:
                    data = process.stdout.read(16384)
                    if not data:
                        break
                    if stop_event.is_set():
                        break
                    self.write_audio(data)
            finally:
                if self._input_process is process:
                    self._input_process = None

        self._input_thread = threading.Thread(target=_feed, daemon=True)
        self._input_thread.start()

    def _write_pcm_input(self, process: subprocess.Popen) -> None:
        """Move queued PCM into the long-lived FFmpeg encoder."""
        try:
            while self._running and self._process is process and process.stdin:
                try:
                    data = self._pcm_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                process.stdin.write(data)
                process.stdin.flush()
                self._bytes_sent += len(data)
        except (BrokenPipeError, OSError):
            if self._running and self._process is process:
                self._state = StreamState.RECONNECTING
                logger.error("FFmpeg PCM input pipe closed")

    def _build_ffmpeg_pcm_command(self) -> list[str]:
        """Build one persistent PCM-to-Icecast encoder command."""
        cfg = self.config
        output_format = "mp3" if cfg.codec == "mp3" else cfg.codec
        cmd = [
            "ffmpeg", "-v", "error", "-f", "s16le",
            "-ar", str(cfg.sample_rate), "-ac", str(cfg.channels),
            "-i", "pipe:0", "-codec:a",
        ]
        if cfg.codec == "mp3":
            cmd.extend(["libmp3lame", "-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "aac":
            cmd.extend(["aac", "-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "ogg":
            cmd.extend(["libvorbis", "-b:a", f"{cfg.bitrate}k"])
        else:
            cmd.extend(["libopus", "-b:a", f"{cfg.bitrate}k"])
        cmd.extend([
            "-ar", str(cfg.sample_rate), "-ac", str(cfg.channels),
            "-f", output_format, "pipe:1",
        ])
        return cmd

    def _connect_icecast(self) -> socket.socket:
        """Perform BUTT-compatible PUT/SOURCE handshake with Icecast."""
        cfg = self.config
        mount = cfg.mount if cfg.mount.startswith("/") else f"/{cfg.mount}"
        auth = base64.b64encode(
            f"{cfg.username or 'source'}:{cfg.password}".encode()
        ).decode()
        content_type = "audio/mpeg" if cfg.codec == "mp3" else f"audio/{cfg.codec}"
        audio_info = (
            f"ice-bitrate={cfg.bitrate};ice-channels={cfg.channels};"
            f"ice-samplerate={48000 if cfg.codec == 'opus' else cfg.sample_rate}"
        )

        for method in ("PUT", "SOURCE"):
            sock = socket.create_connection((cfg.host, cfg.port), timeout=10)
            request = (
                f"{method} {mount} HTTP/{'1.1' if method == 'PUT' else '1.0'}\r\n"
                f"Authorization: Basic {auth}\r\n"
                f"Host: {cfg.host}:{cfg.port}\r\n"
                "User-Agent: FreQ\r\n"
                f"Content-Type: {content_type}\r\n"
                f"ice-name: {cfg.stream_name}\r\n"
                f"ice-description: {cfg.stream_description}\r\n"
                f"ice-genre: {cfg.stream_genre}\r\n"
                f"ice-public: {1 if cfg.stream_public else 0}\r\n"
                f"ice-audio-info: {audio_info}\r\n\r\n"
            ).encode("utf-8")
            try:
                sock.sendall(request)
                response = self._read_icecast_response(sock)
                status = int(response.split()[1]) if len(response.split()) > 1 else 0
                if status == 200:
                    logger.info("Icecast source connected using %s", method)
                    # A blocking socket with no timeout can hang sendall()
                    # forever when the server stalls (full TCP buffer, no
                    # ACK) — killing the whole stream thread. A generous
                    # send timeout turns that into a reconnect instead.
                    sock.settimeout(30)
                    return sock
                sock.close()
                if status == 401:
                    raise RuntimeError("Icecast rejected username or password (401)")
                if status == 403:
                    raise RuntimeError("Icecast mountpoint is already in use (403)")
                if status == 0 and method == "PUT":
                    logger.warning("Icecast PUT returned no response; trying SOURCE")
                    continue
                if status != 404:
                    raise RuntimeError(f"Icecast rejected source connection ({status})")
            except (socket.timeout, ConnectionError, OSError) as exc:
                sock.close()
                if method == "PUT":
                    logger.warning("Icecast PUT handshake did not respond; trying SOURCE")
                    continue
                raise RuntimeError(f"Icecast handshake failed: {exc}") from exc

        raise RuntimeError("Icecast does not support PUT or SOURCE for this mountpoint")

    @staticmethod
    def _read_icecast_response(sock: socket.socket) -> str:
        response = bytearray()
        sock.settimeout(10)
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        return response.decode("iso-8859-1", errors="replace")

    def _send_ffmpeg_output(self, process: subprocess.Popen, sock: socket.socket) -> None:
        """Forward encoded FFmpeg bytes to the active Icecast socket.

        Native pump first: the C++ module reads the encoder's pipe and
        send()s to the socket itself — Python is off the audio path. The
        Python loop below is the fallback (module missing / handoff
        rejected).
        """
        try:
            import native_audio_stream as nas
        except ImportError:
            nas = None
        if nas is not None:
            try:
                import msvcrt
                handle = int(msvcrt.get_osfhandle(process.stdout.fileno()))
                ok = nas.stream_pump_start(handle, sock.fileno(), b"")
                if ok:
                    logger.info("Native stream pump active (C++ send loop)")
                    last = -1
                    while self._running and self._process is process:
                        state = nas.stream_pump_state()
                        if state == 3:   # send error
                            raise ConnectionError(
                                "native pump: icecast send failed")
                        if state == 2:   # encoder closed its stdout
                            break
                        sent = nas.stream_pump_sent()
                        if sent != last:
                            self._bytes_sent = sent
                            last = sent
                        time.sleep(0.1)
                    nas.stream_pump_stop()
                    if self._process is process and process.poll() is not None:
                        self._state = StreamState.DISCONNECTED
                    return
            except (ConnectionError, OSError):
                raise
            except Exception:
                logger.exception("native stream pump unavailable — Python fallback")
        try:
            while self._running and self._process is process and process.stdout:
                data = process.stdout.read(16384)
                if not data:
                    break
                sock.sendall(data)
                self._bytes_sent += len(data)
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            if self._running and self._process is process:
                self._state = StreamState.RECONNECTING
                logger.error("Icecast connection lost: %s", exc)
                # The monitor thread only reacts to the FFmpeg process
                # exiting — a dead socket alone used to leave the stream
                # stuck in RECONNECTING forever. Terminate the encoder so
                # the monitor sees the exit and runs the reconnect ladder.
                try:
                    process.terminate()
                except OSError:
                    try:
                        process.kill()
                    except OSError:
                        pass
        finally:
            if self._process is process and process.poll() is not None:
                self._state = StreamState.DISCONNECTED

    def _build_ffmpeg_audio_command(self, input_file: Optional[str] = None) -> list[str]:
        """Build FFmpeg command that encodes audio to stdout for Icecast."""
        cfg = self.config
        cmd = ["ffmpeg", "-re", "-y"]
        if input_file:
            cmd.extend(["-i", input_file])
        else:
            channel_layout = "mono" if cfg.channels == 1 else "stereo"
            cmd.extend(["-f", "lavfi", "-i", f"anullsrc=r={cfg.sample_rate}:cl={channel_layout}"])
        if cfg.codec == "mp3":
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "aac":
            cmd.extend(["-codec:a", "aac", "-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "ogg":
            cmd.extend(["-codec:a", "libvorbis", "-b:a", f"{cfg.bitrate}k"])
        elif cfg.codec == "opus":
            cmd.extend(["-codec:a", "libopus", "-b:a", f"{cfg.bitrate}k"])
        else:
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", f"{cfg.bitrate}k"])
        cmd.extend(["-ar", str(cfg.sample_rate), "-ac", str(cfg.channels), "-f", "mp3" if cfg.codec == "mp3" else cfg.codec, "pipe:1"])
        return cmd

    def _build_ffmpeg_command(self, input_file: Optional[str] = None) -> list[str]:
        """Build the FFmpeg command for streaming.

        If input_file is given, read from that file.
        Otherwise, generate silence so Icecast receives the stream headers
        immediately, even before a playable track is available.
        """
        cfg = self.config
        cmd = ["ffmpeg", "-re", "-y"]  # -re: read at native frame rate

        # Input
        if input_file:
            cmd.extend(["-i", input_file])
        else:
            channel_layout = "mono" if cfg.channels == 1 else "stereo"
            cmd.extend([
                "-f", "lavfi",
                "-i", f"anullsrc=r={cfg.sample_rate}:cl={channel_layout}",
            ])

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
            output_format = "mp3" if cfg.codec == "mp3" else cfg.codec
            cmd.extend(["-f", output_format])
            cmd.extend([
                "-content_type",
                "audio/mpeg" if output_format == "mp3" else f"audio/{output_format}",
                "-ice_name", cfg.stream_name,
                "-ice_description", cfg.stream_description,
                "-ice_genre", cfg.stream_genre,
                "-ice_public", "1" if cfg.stream_public else "0",
            ])
            mount = cfg.mount if cfg.mount.startswith("/") else f"/{cfg.mount}"
            password = quote(str(cfg.password), safe="")
            username = quote(str(cfg.username or "source"), safe="")
            server_url = f"icecast://{username}:{password}@{cfg.host}:{cfg.port}{mount}"
            cmd.append(server_url)

        elif cfg.protocol == StreamProtocol.SHOUTCAST:
            cmd.extend(["-f", "mp3" if cfg.codec == "mp3" else cfg.codec])
            server_url = f"http://{cfg.host}:{cfg.port}"
            cmd.extend(["-headers", f"Authorization: Basic {base64.b64encode(f'.{cfg.password}'.encode()).decode()}\r\n"])
            cmd.extend(["-content_type", "audio/mpeg"])
            cmd.append(server_url)

        return cmd

    def _monitor(self, process: subprocess.Popen, reconnect: bool) -> None:
        """Monitor FFmpeg process with exponential reconnect backoff."""
        alive_secs = 0
        while self._running and self._process is process:
            if process.poll() is not None:
                if self._running and self._process is process and reconnect:
                    self._state = StreamState.RECONNECTING
                    stderr = process.stderr.read() if process.stderr else b""
                    logger.error(f"FFmpeg exited: {stderr.decode(errors='ignore')[:200]}")
                    # A connection that stayed healthy for a while resets the
                    # backoff ladder; repeated failures escalate 5→15→30→60s.
                    if alive_secs >= 120:
                        self._reconnect_attempt = 0
                    delay = reconnect_delay_for(self._reconnect_attempt)
                    self._reconnect_attempt += 1
                    self._reconnect_eta = time.time() + delay
                    logger.info(f"Reconnecting in {delay}s "
                                f"(attempt {self._reconnect_attempt})")
                    # Sleep in 1s ticks so a user stop cancels the wait.
                    for _ in range(delay):
                        if not (self._running and self._process is process):
                            return
                        time.sleep(1)
                    self._reconnect_eta = 0.0
                    if self._running and self._process is process:
                        self.connect()
                        return     # connect() spawns a fresh monitor thread
                elif self._process is process:
                    self._state = StreamState.DISCONNECTED
                break
            alive_secs += 1
            if alive_secs >= 120:
                self._reconnect_attempt = 0
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
            self._state = StreamState.ERROR
            logger.error("WebRTC signaling is not implemented yet")
            return False
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
        # Do not claim success until signaling and peer creation exist.
        return False


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
