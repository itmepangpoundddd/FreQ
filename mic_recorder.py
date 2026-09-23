"""Record short microphone segments to WAV files for the radio queue.

Primary path is the native WASAPI capture (``native_audio_capture``) —
non-blocking driver, no extra dependency. ``sounddevice`` remains a
fallback when a specific device index is requested or the native module
is unavailable.
"""
from __future__ import annotations

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


def _record_wav_native(
    filepath: str,
    duration: float,
) -> bool:
    """Record the default microphone via native WASAPI capture."""
    if _nac is None or duration <= 0:
        return False
    cap = None
    wav = None
    try:
        cap = _nac.Capture()   # default input endpoint
        cap.start()
        cap.clear()
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        wav = wave.open(str(path), "wb")
        wav.setnchannels(cap.channels)
        wav.setsampwidth(2)
        wav.setframerate(cap.samplerate)

        chunk = max(4096, cap.samplerate * cap.channels * 2 // 8)  # ~125 ms
        deadline = time.time() + duration
        while time.time() < deadline:
            data = cap.read(chunk)
            if data:
                wav.writeframes(data)
            else:
                time.sleep(0.02)
        return True
    except Exception:
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
    """Legacy sounddevice path (also used for explicit device indices)."""
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
) -> bool:
    """Record a microphone segment and save it as PCM WAV.

    Uses the native WASAPI capture for the default microphone; explicit
    device indices go through sounddevice (index spaces are incompatible).
    """
    if device_index is None and NATIVE_CAPTURE_AVAILABLE:
        if _record_wav_native(filepath, duration):
            return True
    return _record_wav_sounddevice(filepath, duration, device_index,
                                   sample_rate, channels)
