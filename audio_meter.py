"""Thread-safe PCM level meter with an optional C++ implementation."""
from __future__ import annotations

import math
import struct
import threading
import time
from typing import Optional

try:
    from native_audio_meter import (
        analyze_s16le_stereo,
        compute_spectrum as _native_compute_spectrum,
        find_silence_end as _native_find_silence_end,
        loudness_new as _native_loudness_new,
        loudness_process as _native_loudness_process,
        loudness_reset as _native_loudness_reset,
        process_s16le_stereo,
        samples_to_stereo_pcm as _native_samples_to_stereo_pcm,
        waveform_peaks as _native_waveform_peaks,
    )
    NATIVE_AVAILABLE = True
    # Canonical names that exist whether or not the native module loaded.
    compute_spectrum = _native_compute_spectrum
    waveform_peaks = _native_waveform_peaks
    loudness_new = _native_loudness_new
    loudness_process = _native_loudness_process
    loudness_reset = _native_loudness_reset
    samples_to_stereo_pcm = _native_samples_to_stereo_pcm
    find_silence_end = _native_find_silence_end
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

    # ── Pure-Python fallbacks for the spectrum / waveform helpers ──

    _FFT_SIZE = 512
    _BARS = 32
    _BAND_LO_HZ = (
        30, 45, 68, 100, 150, 220, 330, 480, 700, 1000, 1400, 2000,
        2800, 3800, 5000, 6300, 7800, 9300, 10800, 12300, 13800,
        15300, 16800, 18300, 19800, 20800, 21800, 22300, 22800,
        23200, 23500, 23800, 24000,
    )
    _BAND_BINS = tuple(
        (max(0, int(_BAND_LO_HZ[i] / (48000.0 / _FFT_SIZE))),
         min(_FFT_SIZE // 2, max(int(_BAND_LO_HZ[i] / (48000.0 / _FFT_SIZE)) + 1,
                                 int(_BAND_LO_HZ[i + 1] / (48000.0 / _FFT_SIZE)))))
        for i in range(_BARS)
    )
    _HANN = tuple(0.5 * (1 - math.cos(2 * math.pi * i / _FFT_SIZE)) for i in range(_FFT_SIZE))

    def _fft_magnitudes(re: list[float], im: list[float]) -> None:
        """In-place iterative radix-2 FFT; magnitudes end up in ``re``."""
        n = len(re)
        j = 0
        for i in range(1, n):
            bit = n >> 1
            while j & bit:
                j ^= bit
                bit >>= 1
            j ^= bit
            if i < j:
                re[i], re[j] = re[j], re[i]
                im[i], im[j] = im[j], im[i]
        length = 2
        while length <= n:
            ang = -2.0 * math.pi / length
            w_re, w_im = math.cos(ang), math.sin(ang)
            half = length // 2
            for start in range(0, n, length):
                cur_re, cur_im = 1.0, 0.0
                for k in range(half):
                    a = start + k
                    b = a + half
                    t_re = re[b] * cur_re - im[b] * cur_im
                    t_im = re[b] * cur_im + im[b] * cur_re
                    re[b] = re[a] - t_re
                    im[b] = im[a] - t_im
                    re[a] += t_re
                    im[a] += t_im
                    cur_re, cur_im = (
                        cur_re * w_re - cur_im * w_im,
                        cur_re * w_im + cur_im * w_re,
                    )
            length <<= 1

    def compute_spectrum(data: bytes) -> list[float]:
        """Return 32 log-frequency spectrum bars (0.0-1.0) — mirrors the C++ code."""
        frames = len(data) // 4
        if frames < 2:
            return [0.0] * _BARS
        mono = [
            (left + right) / 65536.0
            for left, right in struct.iter_unpack("<hh", memoryview(data)[:frames * 4])
        ]
        tail = mono[-_FFT_SIZE:]
        windowed = tail + [0.0] * (_FFT_SIZE - len(tail))
        windowed = [v * w for v, w in zip(windowed, _HANN)]
        if max(abs(v) for v in windowed) < 1e-4:
            return [0.0] * _BARS
        im = [0.0] * _FFT_SIZE
        _fft_magnitudes(windowed, im)
        bars: list[float] = []
        for lo_bin, hi_bin in _BAND_BINS:
            if lo_bin >= hi_bin:
                bars.append(0.0)
                continue
            sum_sq = 0.0
            max_mag = 0.0
            for k in range(lo_bin, hi_bin):
                mag = math.hypot(windowed[k], im[k])
                sum_sq += mag * mag
                if mag > max_mag:
                    max_mag = mag
            mean = math.sqrt(sum_sq / (hi_bin - lo_bin))
            v = (mean * 0.6 + max_mag * 0.4) / (_FFT_SIZE / 4.0)
            bars.append(min(1.0, math.log10(1.0 + v * 9.0)))
        return bars

    def waveform_peaks(data, num_samples: int = 200) -> list[float]:
        """Return blended RMS/peak values per bucket for mono s16le PCM."""
        num_samples = max(1, min(4096, num_samples))
        total = len(data) // 2
        if total <= 0:
            return [0.0] * num_samples
        chunk, remainder = divmod(total, num_samples)
        out: list[float] = []
        offset = 0
        samples = struct.iter_unpack("<h", memoryview(data)[: total * 2])
        for i in range(num_samples):
            count = chunk + (1 if i < remainder else 0)
            sum_sq = 0.0
            peak = 0.0
            for _ in range(count):
                v = next(samples)[0] / 32768.0
                sum_sq += v * v
                peak = max(peak, abs(v))
            rms = math.sqrt(sum_sq / count) if count else 0.0
            out.append(rms * 0.7 + peak * 0.3)
            offset += count
        return out

    # ── Pure-Python BS.1770-4 loudness meter (same algorithm as the C++ code) ──

    class _KWeightingStage:
        """Transposed direct-form-II biquad; mirrors Biquad in the C++ module."""

        __slots__ = ("b0", "b1", "b2", "a1", "a2", "z1", "z2")

        def __init__(self) -> None:
            self.b0 = self.b1 = self.b2 = 1.0
            self.a1 = self.a2 = 0.0
            self.z1 = self.z2 = 0.0

        def process(self, x: float) -> float:
            y = self.b0 * x + self.z1
            self.z1 = self.b1 * x - self.a1 * y + self.z2
            self.z2 = self.b2 * x - self.a2 * y
            return y

        def reset(self) -> None:
            self.z1 = self.z2 = 0.0

    def _design_highpass_stage(f0: float, q: float, fs: float) -> _KWeightingStage:
        k = math.tan(math.pi * f0 / fs)
        a0 = 1.0 + k / q + k * k
        stage = _KWeightingStage()
        stage.b0, stage.b1, stage.b2 = 1.0, -2.0, 1.0
        stage.a1 = 2.0 * (k * k - 1.0) / a0
        stage.a2 = (1.0 - k / q + k * k) / a0
        return stage

    class _KWeighting:
        """Two-stage K-weighting cascade per channel (BS.1770-4)."""

        __slots__ = ("shelf", "highpass")

        def __init__(self, fs: float) -> None:
            # Stage 1: high shelf (+4 dB above ~1.68 kHz)
            f0 = 1681.974450955533
            g = 3.999843853973347
            q = 0.7071752369554196
            k = math.tan(math.pi * f0 / fs)
            vh = 10.0 ** (g / 20.0)
            vb = vh ** 0.4996667741545416
            a0 = 1.0 + k / q + k * k
            self.shelf = _KWeightingStage()
            self.shelf.b0 = (vh + vb * k / q + k * k) / a0
            self.shelf.b1 = 2.0 * (k * k - vh) / a0
            self.shelf.b2 = (vh - vb * k / q + k * k) / a0
            self.shelf.a1 = 2.0 * (k * k - 1.0) / a0
            self.shelf.a2 = (1.0 - k / q + k * k) / a0
            # Stage 2: high pass (~38 Hz)
            self.highpass = _design_highpass_stage(38.13547087602444, 0.5003270373238773, fs)

        def process(self, x: float) -> float:
            return self.highpass.process(self.shelf.process(x))

        def reset(self) -> None:
            self.shelf.reset()
            self.highpass.reset()

    class _PyLoudnessMeter:
        """400 ms blocks with 100 ms hop and histogram gating (BS.1770-4)."""

        __slots__ = ("k_l", "k_r", "window", "hop", "ring_l", "ring_r",
                     "ring_pos", "ring_fill", "since_hop", "bin_count", "bin_energy")

        _BIN_WIDTH = 0.1
        _HIST_MIN = -80.0
        _BINS = 1000

        def __init__(self, fs: float) -> None:
            self.k_l = _KWeighting(fs)
            self.k_r = _KWeighting(fs)
            self.window = max(1, int(0.4 * fs))
            self.hop = max(1, int(0.1 * fs))
            self.ring_l = [0.0] * self.window
            self.ring_r = [0.0] * self.window
            self.ring_pos = 0
            self.ring_fill = 0
            self.since_hop = 0
            self.bin_count = [0] * self._BINS
            self.bin_energy = [0.0] * self._BINS

        def process(self, data: bytes) -> float:
            frames = len(data) // 4
            window, hop = self.window, self.hop
            ring_l, ring_r = self.ring_l, self.ring_r
            unpack_from = struct.unpack_from
            for i in range(frames):
                left, right = unpack_from("<hh", data, i * 4)
                fl = self.k_l.process(left / 32768.0)
                fr = self.k_r.process(right / 32768.0)
                pos = self.ring_pos
                ring_l[pos] = fl
                ring_r[pos] = fr
                self.ring_pos = pos = (pos + 1) % window
                if self.ring_fill < window:
                    self.ring_fill += 1
                self.since_hop += 1
                if self.ring_fill == window and self.since_hop >= hop:
                    self.since_hop = 0
                    self._emit_block()
            return self.integrated()

        def _emit_block(self) -> None:
            window = self.window
            ring_l, ring_r = self.ring_l, self.ring_r
            pos = self.ring_pos
            sum_sq = 0.0
            for i in range(window):
                idx = (pos + i) % window
                sum_sq += ring_l[idx] * ring_l[idx] + ring_r[idx] * ring_r[idx]
            z = sum_sq / window
            if z <= 0.0:
                return
            loudness = -0.691 + 10.0 * math.log10(z)
            bin_idx = int((loudness - self._HIST_MIN) / self._BIN_WIDTH)
            if 0 <= bin_idx < self._BINS:
                self.bin_count[bin_idx] += 1
                self.bin_energy[bin_idx] += z

        def integrated(self) -> float:
            bins = self._BINS
            counts = self.bin_count
            energies = self.bin_energy
            hist_min, bin_width = self._HIST_MIN, self._BIN_WIDTH
            total = 0.0
            n = 0
            for i in range(bins):
                c = counts[i]
                if not c:
                    continue
                mean_loud = -0.691 + 10.0 * math.log10(energies[i] / c)
                if mean_loud > -70.0:
                    total += energies[i]
                    n += c
            if not n:
                return -math.inf
            gamma_a = -0.691 + 10.0 * math.log10(total / n)
            threshold = gamma_a - 10.0
            total = 0.0
            n = 0
            for i in range(bins):
                c = counts[i]
                if not c:
                    continue
                mean_loud = -0.691 + 10.0 * math.log10(energies[i] / c)
                if mean_loud >= threshold:
                    total += energies[i]
                    n += c
            if not n:
                return -math.inf
            return -0.691 + 10.0 * math.log10(total / n)

        def reset(self) -> None:
            self.k_l.reset()
            self.k_r.reset()
            self.ring_pos = self.ring_fill = self.since_hop = 0
            self.bin_count = [0] * self._BINS
            self.bin_energy = [0.0] * self._BINS

    def loudness_new(sample_rate: float = 48000.0):
        """Create a stateful BS.1770-4 loudness meter (pure-Python fallback)."""
        if not 8000.0 <= sample_rate <= 192000.0:
            raise ValueError("sample_rate must be between 8000 and 192000")
        return _PyLoudnessMeter(sample_rate)

    def loudness_process(handle, data: bytes) -> float:
        return handle.process(data)

    def loudness_reset(handle) -> None:
        handle.reset()

    def samples_to_stereo_pcm(samples) -> bytes:
        """Convert an iterable of float samples (-1..1) to s16le stereo PCM."""
        mono = [
            struct.pack("<h", max(-32768, min(32767, int(v * 32767))))
            for v in list(samples)
        ]
        return b"".join(frame + frame for frame in mono)

    def find_silence_end(
        data: bytes, threshold: float = 0.005, min_ms: float = 120.0,
        sample_rate: float = 11025.0,
    ) -> tuple[float, float]:
        """Leading/trailing silence (seconds) in mono s16le PCM."""
        frames = len(data) // 2
        if frames <= 0:
            return 0.0, 0.0
        q = int(threshold * 32768.0)
        samples = struct.iter_unpack("<h", memoryview(data)[: frames * 2])
        all_samples = [s[0] for s in samples]
        start = 0
        while start < frames and abs(all_samples[start]) <= q:
            start += 1
        end = frames
        while end > start and abs(all_samples[end - 1]) <= q:
            end -= 1
        lead = start / sample_rate
        trail = (frames - end) / sample_rate
        return (
            lead * 1000.0 >= min_ms and lead or 0.0,
            trail * 1000.0 >= min_ms and trail or 0.0,
        )


class LoudnessNormalizer:
    """Stream-loudness normalizer.

    Measures the gated integrated loudness of the program being streamed
    (BS.1770-4, computed in the native module when available) and applies a
    smoothly moving gain so the broadcast lands on the target LUFS. Gain
    changes are slew-rate limited so no pumping artifacts are introduced.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        target_lufs: float = -16.0,
        max_gain_db: float = 12.0,
        settle_seconds: float = 8.0,
        slew_db_per_sec: float = 6.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.target_lufs = target_lufs
        self.max_gain = 10.0 ** (max_gain_db / 20.0)
        self.min_gain = 10.0 ** (-max_gain_db / 20.0)
        self.settle_seconds = settle_seconds
        self.slew_db_per_sec = slew_db_per_sec
        self._handle = None
        self._reset()

    def _reset(self) -> None:
        self._handle = loudness_new(self.sample_rate)
        self._gain = 1.0
        self._last_update = 0.0
        self._last_loudness: Optional[float] = None

    def reset(self) -> None:
        """Forget all history — call when the streamed program changes."""
        self._reset()

    @property
    def current_gain(self) -> float:
        return self._gain

    @property
    def measured_lufs(self) -> Optional[float]:
        """Latest gated integrated loudness, or None before the first window."""
        return self._last_loudness

    def _retune_gain(self, loudness: float, now: float) -> None:
        self._last_loudness = loudness
        if not math.isfinite(loudness):
            return
        error_db = self.target_lufs - loudness
        if now - self._last_update < self.settle_seconds:
            return
        target_gain = self._gain * 10.0 ** (error_db / 20.0)
        target_gain = max(self.min_gain, min(self.max_gain, target_gain))
        # Slew-rate limit: how far may the gain move since the last retune?
        elapsed = max(0.001, now - self._last_update)
        max_move = self.slew_db_per_sec * elapsed
        current_db = 20.0 * math.log10(max(1e-9, self._gain))
        target_db = 20.0 * math.log10(max(1e-9, target_gain))
        target_db = max(current_db - max_move, min(current_db + max_move, target_db))
        self._gain = 10.0 ** (target_db / 20.0)
        self._last_update = now

    def write(self, data: bytes) -> float:
        """Measure a chunk and return the gain (linear) to apply to it."""
        if not data:
            return self._gain
        now = time.monotonic()
        loudness = loudness_process(self._handle, data)
        if loudness != 0.0:  # skip the -inf initial state
            self._retune_gain(loudness, now)
        return self._gain


class AudioMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._levels = (0.0, 0.0, 0.0, 0.0)
        self._updated = 0.0
        # Rolling window of the most recent PCM so widgets (spectrum
        # visualizer) can pull a fresh slice on their own timer.
        self._pcm_lock = threading.Lock()
        self._pcm = bytearray()
        self._pcm_cap = 65536

    def publish_s16le_stereo(self, data: bytes) -> None:
        if not data:
            return
        self.record_pcm(data)
        with self._lock:
            self._levels = analyze_s16le_stereo(data)
            self._updated = time.monotonic()

    def record_pcm(self, data: bytes) -> None:
        """Keep the tail of the PCM stream for downstream visual widgets."""
        with self._pcm_lock:
            self._pcm.extend(data)
            overflow = len(self._pcm) - self._pcm_cap
            if overflow > 0:
                del self._pcm[:overflow]

    def latest_pcm(self, size: int) -> bytes:
        """Return up to ``size`` bytes of the most recent PCM (may be empty)."""
        with self._pcm_lock:
            if not self._pcm:
                return b""
            return bytes(self._pcm[-size:]) if size < len(self._pcm) else bytes(self._pcm)

    def spectrum(self, size: int = 4096) -> list[float]:
        """Spectrum bars (0.0-1.0) computed from the latest PCM window."""
        return compute_spectrum(self.latest_pcm(size))

    def levels(self, max_age: float = 0.25) -> tuple[float, float, float, float]:
        with self._lock:
            return self._levels if time.monotonic() - self._updated <= max_age else (0.0,) * 4


# ── Auto-cue: leading/trailing silence detection for playback ──

_SILENCE_CACHE: dict[str, tuple[float, float]] = {}

def detect_leading_silence(
    filepath: str,
    threshold: float = 0.005,
    max_lead: float = 1.0,
    min_ms: float = 120.0,
) -> float:
    """Return how much leading silence (seconds) to skip for a file.

    Decodes up to ``max_lead`` seconds with ffmpeg (s16le mono 11 kHz) and
    measures with the native ``find_silence_end`` (or the Python fallback).
    Results are cached per path — the same file is never decoded twice.
    """
    if filepath in _SILENCE_CACHE:
        return _SILENCE_CACHE[filepath][0]
    lead = 0.0
    try:
        import subprocess
        from player import _ffmpeg_exe
        result = subprocess.run(
            [
                _ffmpeg_exe(), "-v", "quiet", "-i", filepath,
                "-t", str(max_lead), "-ac", "1", "-ar", "11025",
                "-f", "s16le", "-y", "pipe:1",
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            # NOTE: positional args — the native module does not accept keywords.
            lead_s, _trail = find_silence_end(
                result.stdout, threshold, min_ms, 11025.0,
            )
            # Only trust a measurement from a full window; if the decode was
            # cut short mid-silence the true cue point may lie beyond it.
            frames = len(result.stdout) // 2
            full_window = frames >= int(max_lead * 11025 * 0.95)
            lead = lead_s if (lead_s > 0 or full_window) else 0.0
        _SILENCE_CACHE[filepath] = (lead, 0.0)
    except Exception:
        _SILENCE_CACHE[filepath] = (0.0, 0.0)
    return _SILENCE_CACHE[filepath][0]


meter = AudioMeter()
