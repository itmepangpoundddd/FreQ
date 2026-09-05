"""Record short microphone segments to WAV files for the radio queue."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False


def record_wav(
    filepath: str,
    duration: float,
    device_index: Optional[int] = None,
    sample_rate: int = 44100,
    channels: int = 1,
) -> bool:
    """Record a microphone segment and save it as PCM WAV."""
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
