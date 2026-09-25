"""Record short microphone segments to WAV files for the radio queue.

On Windows, microphones are captured exclusively through the native
WASAPI engine (``native_audio_capture``): several old USB webcam
microphones crash the whole process inside PortAudio's DLL
(0xC0000005 in libportaudio64bit.dll), which no Python exception
handler can intercept — so PortAudio never opens a capture device
there. On other platforms (no native module) the legacy sounddevice
path remains as a fallback.

A device that fails to open is blacklisted for a cooldown window so a
malformed driver is not hammered every few seconds (each failed open
can leave the driver in a worse state and stalls the caller).
"""
from __future__ import annotations

import logging
import sys
import time
import wave
from pathlib import Path
from typing import Optional

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

try:
    import native_audio_capture as _nac
    NATIVE_CAPTURE_AVAILABLE = True
except ImportError:
    _nac = None
    NATIVE_CAPTURE_AVAILABLE = False

# PortAudio capture is banned on the platform whose old webcam drivers
# crash inside its DLL; elsewhere it is the only available backend.
_ALLOW_PORTAUDIO_CAPTURE = sys.platform != "win32"


# Failed endpoints may not be retried before this many seconds.
_BLACKLIST_TTL = 600.0
_blacklist: dict[str, float] = {}


def _blacklisted(endpoint_id: str) -> bool:
    until = _blacklist.get(endpoint_id)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _blacklist[endpoint_id]
        return False
    return True


def _resolve_endpoint(device_index: Optional[int], endpoint_hint: str = "") -> str:
    """Endpoint id for a queue item's device reference.

    The queue stores the index into the GUI's mic list (``d.id``), not a
    PortAudio index. We cannot read that list here (importing the GUI
    would be circular), so the mapping is rebuilt natively:
    ``enum_microphones()`` returns capture endpoints in the same order
    the GUI's device manager lists them, and an endpoint id / name hint
    is matched first when given.
    """
    if endpoint_hint:
        return endpoint_hint
    if device_index is None:
        return ""   # OS default capture endpoint
    if _nac is None:
        return ""
    try:
        mics = _nac.enum_microphones()
    except Exception:
        return ""
    if 0 <= device_index < len(mics):
        return str(mics[device_index][0] or "")
    return ""


def _record_wav_native(
    filepath: str,
    duration: float,
    endpoint_id: str = "",
) -> bool:
    """Record a capture endpoint ("" = OS default) via native WASAPI."""
    if _nac is None or duration <= 0:
        return False
    if endpoint_id and _blacklisted(endpoint_id):
        return False
    cap = None
    wav = None
    wrote_any = False
    try:
        cap = _nac.Capture(endpoint=endpoint_id or None)
        cap.start()
        cap.clear()
        out_rate, out_ch = cap.samplerate, min(2, cap.channels)
        path = Path(filepath)
        # The WAV file is created lazily on the first delivered bytes —
        # a silent attempt must not leave an empty file behind.
        chunk = max(4096, cap.samplerate * cap.channels * 2 // 8)  # ~125 ms
        deadline = time.time() + duration
        while time.time() < deadline:
            data = cap.read(chunk)
            if data:
                if wav is None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    wav = wave.open(str(path), "wb")
                    wav.setnchannels(out_ch)
                    wav.setsampwidth(2)
                    wav.setframerate(out_rate)
                wav.writeframes(data)
                wrote_any = True
            else:
                time.sleep(0.02)
        if not wrote_any:
            # Endpoint opened but delivered nothing — treat as a
            # failure so the queue moves on instead of airing silence.
            if endpoint_id:
                _blacklist[endpoint_id] = time.monotonic() + _BLACKLIST_TTL
            return False
        return True
    except Exception:
        if endpoint_id:
            _blacklist[endpoint_id] = time.monotonic() + _BLACKLIST_TTL
        return False
    finally:
        if wav is not None:
            try:
                wav.close()
            except Exception:
                pass
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass


def _record_wav_sounddevice(
    filepath: str,
    duration: float,
    device_index: Optional[int] = None,
    sample_rate: int = 44100,
    channels: int = 1,
) -> bool:
    """Legacy sounddevice path — non-Windows fallback only."""
    if not SOUNDDEVICE_AVAILABLE or sd is None or duration <= 0:
        return False
    try:
        frames = int(duration * sample_rate)
        recording = sd.rec(
            frames, samplerate=sample_rate, channels=channels,
            dtype="int16", device=device_index,
        )
        sd.wait()
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(recording.tobytes())
        return True
    except Exception:
        return False


def record_wav(
    filepath: str,
    duration: float,
    device_index: Optional[int] = None,
    sample_rate: int = 44100,
    channels: int = 1,
    endpoint_id: str = "",
) -> bool:
    """Record a microphone segment and save it as PCM WAV.

    Windows: always the native WASAPI capture — the ``device_index`` is
    the position in the native endpoint enumeration (as stored by the
    GUI), ``endpoint_id`` (when given) selects the device directly.
    Elsewhere: legacy sounddevice fallback when the native module is
    missing. Returns False when the endpoint cannot be opened or stays
    silent; the queue then skips the item instead of freezing (and a
    flaky endpoint is not retried for ten minutes).
    """
    if duration <= 0:
        return False
    if NATIVE_CAPTURE_AVAILABLE:
        endpoint = _resolve_endpoint(device_index, endpoint_id)
        # Two attempts: USB capture endpoints can take a moment to prime
        # their buffers, and an endpoint only just released by another
        # (or a just-closed) process can refuse one open transiently —
        # a short pause before retrying absorbs both.
        for attempt in (1, 2):
            if _record_wav_native(filepath, duration, endpoint):
                return True
            if attempt == 1 and not _blacklisted(endpoint):
                time.sleep(0.25)
                continue
            break
        logging.getLogger("freq.mic").warning(
            "microphone capture failed (endpoint=%r, index=%r)",
            endpoint or "<default>", device_index)
        return False
    if _ALLOW_PORTAUDIO_CAPTURE and SOUNDDEVICE_AVAILABLE and sd is not None:
        return _record_wav_sounddevice(filepath, duration, device_index,
                                       sample_rate, channels)
    return False


def mic_blacklist_active() -> bool:
    """True while at least one endpoint is serving its failure cooldown."""
    now = time.monotonic()
    for endpoint in list(_blacklist):
        if now >= _blacklist[endpoint]:
            del _blacklist[endpoint]
    return bool(_blacklist)
