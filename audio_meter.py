"""Thread-safe PCM level meter with an optional C++ implementation."""
from __future__ import annotations

import math
import struct
import threading
import time

try:
    from native_audio_meter import analyze_s16le_stereo, process_s16le_stereo
    NATIVE_AVAILABLE = True
except ImportError:
    NATIVE_AVAILABLE = False

    def analyze_s16le_stereo(data: bytes) -> tuple[float, float, float, float]:
        frames = len(data) // 4
        if not frames:
            return 0.0, 0.0, 0.0, 0.0
        sum_l = sum_r = peak_l = peak_r = 0.0
        for left, right in struct.iter_unpack("<hh", memoryview(data)[:frames * 4]):
            l, r = left / 32768.0, right / 32768.0
            sum_l += l * l; sum_r += r * r
            peak_l = max(peak_l, abs(l)); peak_r = max(peak_r, abs(r))
        return math.sqrt(sum_l / frames), math.sqrt(sum_r / frames), peak_l, peak_r

    def process_s16le_stereo(
        data: bytes, gain_left: float = 1.0, gain_right: float = 1.0,
        limiter: float = 0.98,
    ) -> bytes:
        """Apply per-channel gain and limiting without requiring the C++ module."""
        if not 0.0 < limiter <= 1.0:
            raise ValueError("limiter must be greater than 0 and no greater than 1")
        output = bytearray(len(data) - len(data) % 4)
        for offset, (left, right) in zip(
            range(0, len(output), 4),
            struct.iter_unpack("<hh", memoryview(data)[:len(output)]),
        ):
            left = max(-limiter, min(limiter, left / 32768.0 * gain_left))
            right = max(-limiter, min(limiter, right / 32768.0 * gain_right))
            output[offset:offset + 4] = struct.pack(
                "<hh", round(left * 32767.0), round(right * 32767.0)
            )
        return bytes(output)


class AudioMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._levels = (0.0, 0.0, 0.0, 0.0)
        self._updated = 0.0

    def publish_s16le_stereo(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._levels = analyze_s16le_stereo(data)
            self._updated = time.monotonic()

    def levels(self, max_age: float = 0.25) -> tuple[float, float, float, float]:
        with self._lock:
            return self._levels if time.monotonic() - self._updated <= max_age else (0.0,) * 4


meter = AudioMeter()
