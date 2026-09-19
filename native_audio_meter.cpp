// Native PCM level meter for FreQ. Build with build_native_meter.py.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

// ═══════════════════════════════════════════════
// FFT (iterative radix-2 Cooley-Tukey, real input via complex FFT)
// ═══════════════════════════════════════════════

static void fft_inplace(std::vector<double>& re, std::vector<double>& im) {
    const size_t n = re.size();
    // Bit-reversal permutation
    for (size_t i = 1, j = 0; i < n; ++i) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            std::swap(re[i], re[j]);
            std::swap(im[i], im[j]);
        }
    }
    // Butterflies
    for (size_t len = 2; len <= n; len <<= 1) {
        const double angle = -2.0 * 3.14159265358979323846 / static_cast<double>(len);
        const double w_re = std::cos(angle);
        const double w_im = std::sin(angle);
        for (size_t i = 0; i < n; i += len) {
            double cur_re = 1.0;
            double cur_im = 0.0;
            for (size_t k = 0; k < len / 2; ++k) {
                const size_t a = i + k;
                const size_t b = i + k + len / 2;
                const double t_re = re[b] * cur_re - im[b] * cur_im;
                const double t_im = re[b] * cur_im + im[b] * cur_re;
                re[b] = re[a] - t_re;
                im[b] = im[a] - t_im;
                re[a] += t_re;
                im[a] += t_im;
                const double next_re = cur_re * w_re - cur_im * w_im;
                cur_im = cur_re * w_im + cur_im * w_re;
                cur_re = next_re;
            }
        }
    }
}

static void compute_spectrum_32(const int16_t* samples, Py_ssize_t count, double* out_bars) {
    const int BARS = 32;
    const int FFT_SIZE = 512;
    // Log-spaced band edges (Hz) — covers the musically useful range while
    // keeping low-end detail that linear bins would smear into one bar.
    static const double lo_hz[BARS + 1] = {
        30, 45, 68, 100, 150, 220, 330, 480, 700, 1000, 1400, 2000,
        2800, 3800, 5000, 6300, 7800, 9300, 10800, 12300, 13800,
        15300, 16800, 18300, 19800, 20800, 21800, 22300, 22800,
        23200, 23500, 23800, 24000
    };
    static double window[FFT_SIZE];
    static bool window_ready = false;
    if (!window_ready) {
        for (int i = 0; i < FFT_SIZE; ++i) {
            window[i] = 0.5 * (1.0 - std::cos(2.0 * 3.14159265358979323846 * i / FFT_SIZE));
        }
        window_ready = true;
    }

    // Mix to mono and take the most recent FFT_SIZE samples.
    std::vector<double> re(FFT_SIZE, 0.0);
    std::vector<double> im(FFT_SIZE, 0.0);
    const Py_ssize_t mono_frames = count / 2;
    const Py_ssize_t use_frames = mono_frames < FFT_SIZE ? mono_frames : FFT_SIZE;
    const Py_ssize_t start_frame = mono_frames - use_frames;  // tail of the buffer
    double peak = 1e-9;
    for (int i = 0; i < FFT_SIZE; ++i) {
        // Zero-pad the tail up to FFT_SIZE.
        const double mono = (i < use_frames)
            ? (samples[(start_frame + i) * 2] + samples[(start_frame + i) * 2 + 1]) / 65536.0
            : 0.0;
        re[i] = mono * window[i];
        const double mag = std::fabs(mono);
        if (mag > peak) {
            peak = mag;
        }
    }
    if (peak < 1e-4) {  // digital silence — skip the transform entirely
        for (int i = 0; i < BARS; ++i) {
            out_bars[i] = 0.0;
        }
        return;
    }

    fft_inplace(re, im);

    const double bin_hz = 48000.0 / FFT_SIZE;
    for (int b = 0; b < BARS; ++b) {
        int lo_bin = static_cast<int>(lo_hz[b] / bin_hz);
        int hi_bin = static_cast<int>(lo_hz[b + 1] / bin_hz);
        if (hi_bin <= lo_bin) {
            hi_bin = lo_bin + 1;  // narrow low bands still get at least one bin
        }
        if (hi_bin > FFT_SIZE / 2) {
            hi_bin = FFT_SIZE / 2;
        }
        if (lo_bin >= hi_bin) {
            out_bars[b] = 0.0;
            continue;
        }
        double sum_sq = 0.0;
        double max_mag = 0.0;
        for (int k = lo_bin; k < hi_bin; ++k) {
            const double mag = std::sqrt(re[k] * re[k] + im[k] * im[k]);
            sum_sq += mag * mag;
            if (mag > max_mag) {
                max_mag = mag;
            }
        }
        // Mix of mean power (body) and max (sparkle) per band, log-scaled so
        // the bars resemble a typical broadcast spectrum display.
        const double mean = std::sqrt(sum_sq / (hi_bin - lo_bin));
        const double v = (mean * 0.6 + max_mag * 0.4) / (FFT_SIZE / 4.0);
        out_bars[b] = std::min(1.0, std::log10(1.0 + v * 9.0));
    }
}

// ═══════════════════════════════════════════════
// Python-callable functions
// ═══════════════════════════════════════════════

static PyObject* analyze_s16le_stereo(PyObject*, PyObject* args) {
    Py_buffer buffer{};
    if (!PyArg_ParseTuple(args, "y*", &buffer)) return nullptr;
    const auto* data = static_cast<const unsigned char*>(buffer.buf);
    const Py_ssize_t frames = buffer.len / 4;
    if (!frames) {
        PyBuffer_Release(&buffer);
        return Py_BuildValue("(dddd)", 0.0, 0.0, 0.0, 0.0);
    }
    double sum_l = 0.0, sum_r = 0.0, peak_l = 0.0, peak_r = 0.0;
    for (Py_ssize_t frame = 0; frame < frames; ++frame) {
        const auto offset = frame * 4;
        const auto left = static_cast<int16_t>(static_cast<uint16_t>(data[offset]) |
                                                (static_cast<uint16_t>(data[offset + 1]) << 8));
        const auto right = static_cast<int16_t>(static_cast<uint16_t>(data[offset + 2]) |
                                                 (static_cast<uint16_t>(data[offset + 3]) << 8));
        const double l = static_cast<double>(left) / 32768.0;
        const double r = static_cast<double>(right) / 32768.0;
        sum_l += l * l; sum_r += r * r;
        peak_l = fmax(peak_l, fabs(l)); peak_r = fmax(peak_r, fabs(r));
    }
    PyBuffer_Release(&buffer);
    return Py_BuildValue("(dddd)", sqrt(sum_l / frames), sqrt(sum_r / frames), peak_l, peak_r);
}

static PyObject* process_s16le_stereo(PyObject*, PyObject* args) {
    Py_buffer buffer{};
    double gain_left = 1.0;
    double gain_right = 1.0;
    double limiter = 0.98;
    if (!PyArg_ParseTuple(args, "y*dd|d", &buffer, &gain_left, &gain_right, &limiter)) {
        return nullptr;
    }
    if (limiter <= 0.0 || limiter > 1.0) {
        PyBuffer_Release(&buffer);
        PyErr_SetString(PyExc_ValueError, "limiter must be greater than 0 and no greater than 1");
        return nullptr;
    }
    const auto* input = static_cast<const unsigned char*>(buffer.buf);
    const Py_ssize_t sample_bytes = buffer.len - (buffer.len % 4);
    PyObject* output = PyBytes_FromStringAndSize(nullptr, sample_bytes);
    if (!output) {
        PyBuffer_Release(&buffer);
        return nullptr;
    }
    auto* result = reinterpret_cast<unsigned char*>(PyBytes_AS_STRING(output));
    for (Py_ssize_t offset = 0; offset < sample_bytes; offset += 4) {
        const auto left = static_cast<int16_t>(static_cast<uint16_t>(input[offset]) |
                                                (static_cast<uint16_t>(input[offset + 1]) << 8));
        const auto right = static_cast<int16_t>(static_cast<uint16_t>(input[offset + 2]) |
                                                 (static_cast<uint16_t>(input[offset + 3]) << 8));
        const auto clamp = [limiter](double value) {
            return fmax(-limiter, fmin(limiter, value));
        };
        const auto out_left = static_cast<int16_t>(std::lround(clamp(left / 32768.0 * gain_left) * 32767.0));
        const auto out_right = static_cast<int16_t>(std::lround(clamp(right / 32768.0 * gain_right) * 32767.0));
        result[offset] = static_cast<unsigned char>(out_left & 0xff);
        result[offset + 1] = static_cast<unsigned char>((out_left >> 8) & 0xff);
        result[offset + 2] = static_cast<unsigned char>(out_right & 0xff);
        result[offset + 3] = static_cast<unsigned char>((out_right >> 8) & 0xff);
    }
    PyBuffer_Release(&buffer);
    return output;
}

static PyObject* compute_spectrum(PyObject*, PyObject* args) {
    Py_buffer buffer{};
    if (!PyArg_ParseTuple(args, "y*", &buffer)) return nullptr;
    const auto* data = static_cast<const unsigned char*>(buffer.buf);
    const Py_ssize_t frames = buffer.len / 4;
    if (frames < 2) {
        PyBuffer_Release(&buffer);
        PyObject* empty = PyList_New(32);
        for (int i = 0; i < 32; ++i) {
            PyList_SetItem(empty, i, PyFloat_FromDouble(0.0));
        }
        return empty;
    }
    std::vector<double> bars(32, 0.0);
    compute_spectrum_32(reinterpret_cast<const int16_t*>(data), frames * 2, bars.data());
    PyBuffer_Release(&buffer);
    PyObject* list = PyList_New(32);
    for (int i = 0; i < 32; ++i) {
        PyList_SetItem(list, i, PyFloat_FromDouble(bars[i]));
    }
    return list;
}

static PyObject* waveform_peaks(PyObject*, PyObject* args) {
    PyObject* data_obj = nullptr;
    int num_samples = 200;
    if (!PyArg_ParseTuple(args, "O|i", &data_obj, &num_samples)) return nullptr;
    Py_buffer buffer{};
    if (PyObject_GetBuffer(data_obj, &buffer, PyBUF_SIMPLE) != 0) return nullptr;
    if (num_samples < 1) num_samples = 1;
    if (num_samples > 4096) num_samples = 4096;
    const auto* data = static_cast<const unsigned char*>(buffer.buf);
    const Py_ssize_t total = buffer.len / 2;  // mono int16 samples
    PyObject* list = PyList_New(num_samples);
    if (!list) {
        PyBuffer_Release(&buffer);
        return nullptr;
    }
    if (total <= 0) {
        for (int i = 0; i < num_samples; ++i) {
            PyList_SetItem(list, i, PyFloat_FromDouble(0.0));
        }
        PyBuffer_Release(&buffer);
        return list;
    }
    const Py_ssize_t chunk = total / num_samples;
    const Py_ssize_t remainder = total % num_samples;
    Py_ssize_t offset = 0;
    for (int i = 0; i < num_samples; ++i) {
        Py_ssize_t count = chunk + (i < remainder ? 1 : 0);
        double sum_sq = 0.0;
        double peak = 0.0;
        for (Py_ssize_t s = 0; s < count; ++s, ++offset) {
            const auto sample = static_cast<int16_t>(
                static_cast<uint16_t>(data[offset * 2]) |
                (static_cast<uint16_t>(data[offset * 2 + 1]) << 8));
            const double v = static_cast<double>(sample) / 32768.0;
            sum_sq += v * v;
            const double av = std::fabs(v);
            if (av > peak) peak = av;
        }
        // Blend RMS (body) with peak (transients), then normalize later in Python.
        const double rms = count > 0 ? std::sqrt(sum_sq / count) : 0.0;
        PyList_SetItem(list, i, PyFloat_FromDouble(rms * 0.7 + peak * 0.3));
    }
    PyBuffer_Release(&buffer);
    return list;
}

// ═══════════════════════════════════════════════
// BS.1770-4 gated loudness meter (K-weighted, histogram method)
// ═══════════════════════════════════════════════

struct Biquad {
    double b0 = 1.0, b1 = 0.0, b2 = 0.0, a1 = 0.0, a2 = 0.0;
    double x1 = 0.0, x2 = 0.0, y1 = 0.0, y2 = 0.0;

    void process_into(const double* src, double* dst, Py_ssize_t n) {
        for (Py_ssize_t i = 0; i < n; ++i) {
            const double x = src[i];
            const double y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
            x2 = x1; x1 = x; y2 = y1; y1 = y;
            dst[i] = y;
        }
    }

    void reset() { x1 = x2 = y1 = y2 = 0.0; }
};

// K-weighting filter design (ITU-R BS.1770-4). The classic published
// coefficients are the 48 kHz special case; these parametric formulas
// reproduce them exactly and generalize to any sample rate.
static void design_kweighting(double fs, Biquad& shelf, Biquad& highpass) {
    const double PI = 3.14159265358979323846;
    {   // Stage 1: high shelf (+4 dB above ~1.68 kHz)
        const double f0 = 1681.974450955533;
        const double G = 3.999843853973347;
        const double Q = 0.7071752369554196;
        const double K = std::tan(PI * f0 / fs);
        const double Vh = std::pow(10.0, G / 20.0);
        const double Vb = std::pow(Vh, 0.4996667741545416);
        const double a0 = 1.0 + K / Q + K * K;
        shelf.b0 = (Vh + Vb * K / Q + K * K) / a0;
        shelf.b1 = 2.0 * (K * K - Vh) / a0;
        shelf.b2 = (Vh - Vb * K / Q + K * K) / a0;
        shelf.a1 = 2.0 * (K * K - 1.0) / a0;
        shelf.a2 = (1.0 - K / Q + K * K) / a0;
    }
    {   // Stage 2: high pass (~38 Hz)
        const double f0 = 38.13547087602444;
        const double Q = 0.5003270373238773;
        const double K = std::tan(PI * f0 / fs);
        const double a0 = 1.0 + K / Q + K * K;
        highpass.b0 = 1.0;
        highpass.b1 = -2.0;
        highpass.b2 = 1.0;
        highpass.a1 = 2.0 * (K * K - 1.0) / a0;
        highpass.a2 = (1.0 - K / Q + K * K) / a0;
    }
}

struct LoudnessMeter {
    static constexpr double kBinWidth = 0.1;   // LU per histogram bin
    static constexpr double kHistMin = -80.0;  // lowest bin edge (LUFS)
    static constexpr int kBins = 1000;         // covers -80 .. +20 LUFS

    double sample_rate;
    Biquad shelf[2], highpass[2];
    std::vector<double> ring_l, ring_r;        // filtered samples, 400 ms window
    std::vector<double> s_l, s_r, t_l, t_r;    // scratch buffers
    Py_ssize_t window = 0, hop = 0;
    Py_ssize_t ring_pos = 0, ring_fill = 0, since_hop = 0;
    long long bin_count[kBins] = {};
    double bin_energy[kBins] = {};

    explicit LoudnessMeter(double fs) : sample_rate(fs) {
        Biquad shelf_design, hp_design;
        design_kweighting(fs, shelf_design, hp_design);
        for (int ch = 0; ch < 2; ++ch) {
            shelf[ch] = shelf_design;
            highpass[ch] = hp_design;
        }
        window = static_cast<Py_ssize_t>(0.4 * fs);  // 400 ms blocks
        hop = static_cast<Py_ssize_t>(0.1 * fs);     // 100 ms hop
        if (window < 1) window = 1;
        if (hop < 1) hop = 1;
        ring_l.assign(window, 0.0);
        ring_r.assign(window, 0.0);
    }

    void process(const int16_t* interleaved, Py_ssize_t count) {
        const Py_ssize_t frames = count / 2;
        if (frames <= 0) return;
        s_l.resize(frames); s_r.resize(frames);
        t_l.resize(frames); t_r.resize(frames);
        for (Py_ssize_t i = 0; i < frames; ++i) {
            s_l[i] = interleaved[2 * i] / 32768.0;
            s_r[i] = interleaved[2 * i + 1] / 32768.0;
        }
        shelf[0].process_into(s_l.data(), t_l.data(), frames);
        highpass[0].process_into(t_l.data(), s_l.data(), frames);
        shelf[1].process_into(s_r.data(), t_r.data(), frames);
        highpass[1].process_into(t_r.data(), s_r.data(), frames);
        append(s_l, s_r, frames);
    }

    void append(const std::vector<double>& l, const std::vector<double>& r, Py_ssize_t n) {
        for (Py_ssize_t i = 0; i < n; ++i) {
            ring_l[ring_pos] = l[i];
            ring_r[ring_pos] = r[i];
            ring_pos = (ring_pos + 1) % window;
            if (ring_fill < window) ++ring_fill;
        }
        since_hop += n;
        while (ring_fill == window && since_hop >= hop) {
            since_hop -= hop;
            emit_block();
        }
    }

    void emit_block() {
        double sum_l = 0.0, sum_r = 0.0;
        for (Py_ssize_t i = 0; i < window; ++i) {
            const Py_ssize_t idx = (ring_pos + i) % window;  // oldest first
            sum_l += ring_l[idx] * ring_l[idx];
            sum_r += ring_r[idx] * ring_r[idx];
        }
        const double z = (sum_l + sum_r) / static_cast<double>(window);
        if (z <= 0.0) return;  // silent block: fails the absolute gate
        const double loudness = -0.691 + 10.0 * std::log10(z);
        int bin = static_cast<int>((loudness - kHistMin) / kBinWidth);
        if (bin < 0 || bin >= kBins) return;
        bin_count[bin]++;
        bin_energy[bin] += z;
    }

    // Gated integrated loudness (LUFS): absolute gate -70, relative gate -10 LU.
    double integrated() const {
        double sum = 0.0;
        long long n = 0;
        for (int i = 0; i < kBins; ++i) {
            if (!bin_count[i]) continue;
            const double mean_loud =
                -0.691 + 10.0 * std::log10(bin_energy[i] / static_cast<double>(bin_count[i]));
            if (mean_loud > -70.0) {
                sum += bin_energy[i];
                n += bin_count[i];
            }
        }
        if (!n) return -INFINITY;
        const double gamma_a = -0.691 + 10.0 * std::log10(sum / static_cast<double>(n));
        const double threshold = gamma_a - 10.0;
        sum = 0.0; n = 0;
        for (int i = 0; i < kBins; ++i) {
            if (!bin_count[i]) continue;
            const double mean_loud =
                -0.691 + 10.0 * std::log10(bin_energy[i] / static_cast<double>(bin_count[i]));
            if (mean_loud >= threshold) {
                sum += bin_energy[i];
                n += bin_count[i];
            }
        }
        if (!n) return -INFINITY;
        return -0.691 + 10.0 * std::log10(sum / static_cast<double>(n));
    }

    void reset() {
        for (int ch = 0; ch < 2; ++ch) {
            shelf[ch].reset();
            highpass[ch].reset();
        }
        ring_pos = ring_fill = since_hop = 0;
        std::memset(bin_count, 0, sizeof(bin_count));
        std::memset(bin_energy, 0, sizeof(bin_energy));
    }
};

static void loudness_capsule_destructor(PyObject* capsule) {
    auto* meter = static_cast<LoudnessMeter*>(
        PyCapsule_GetPointer(capsule, "native_audio_meter.LoudnessMeter"));
    delete meter;
}

static PyObject* loudness_new(PyObject*, PyObject* args) {
    double sample_rate = 48000.0;
    if (!PyArg_ParseTuple(args, "|d", &sample_rate)) return nullptr;
    if (sample_rate < 8000.0 || sample_rate > 192000.0) {
        PyErr_SetString(PyExc_ValueError, "sample_rate must be between 8000 and 192000");
        return nullptr;
    }
    auto* meter = new LoudnessMeter(sample_rate);
    return PyCapsule_New(meter, "native_audio_meter.LoudnessMeter", loudness_capsule_destructor);
}

static PyObject* loudness_process(PyObject*, PyObject* args) {
    PyObject* capsule = nullptr;
    Py_buffer buffer{};
    if (!PyArg_ParseTuple(args, "Oy*", &capsule, &buffer)) return nullptr;
    auto* meter = static_cast<LoudnessMeter*>(
        PyCapsule_GetPointer(capsule, "native_audio_meter.LoudnessMeter"));
    if (!meter) {
        PyBuffer_Release(&buffer);
        return nullptr;
    }
    meter->process(reinterpret_cast<const int16_t*>(buffer.buf), buffer.len / 2);
    PyBuffer_Release(&buffer);
    return PyFloat_FromDouble(meter->integrated());
}

static PyObject* loudness_reset(PyObject*, PyObject* args) {
    PyObject* capsule = nullptr;
    if (!PyArg_ParseTuple(args, "O", &capsule)) return nullptr;
    auto* meter = static_cast<LoudnessMeter*>(
        PyCapsule_GetPointer(capsule, "native_audio_meter.LoudnessMeter"));
    if (!meter) return nullptr;
    meter->reset();
    Py_RETURN_NONE;
}

// ── Legacy sample-list conversion + silence detection ──

static inline int16_t abs16(int16_t v) {
    return v < 0 ? static_cast<int16_t>(-v) : v;
}

static PyObject* samples_to_stereo_pcm(PyObject*, PyObject* args) {
    PyObject* seq = nullptr;
    if (!PyArg_ParseTuple(args, "O", &seq)) return nullptr;
    PyObject* fast = PySequence_Fast(seq, "expected a sequence of float samples");
    if (!fast) return nullptr;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(fast);
    PyObject* out = PyBytes_FromStringAndSize(nullptr, n * 4);
    if (!out) {
        Py_DECREF(fast);
        return nullptr;
    }
    char* dst = PyBytes_AS_STRING(out);
    for (Py_ssize_t i = 0; i < n; ++i) {
        const double v = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(fast, i));
        if (v == -1.0 && PyErr_Occurred()) {
            Py_DECREF(fast);
            Py_DECREF(out);
            return nullptr;
        }
        double s = v * 32767.0;
        if (s > 32767.0) s = 32767.0;
        if (s < -32768.0) s = -32768.0;
        // Truncate like the Python fallback (int()) so both paths are
        // bit-exact — matches the legacy conversion semantics.
        const int16_t q = static_cast<int16_t>(s);
        std::memcpy(dst + i * 4, &q, 2);      // left
        std::memcpy(dst + i * 4 + 2, &q, 2);  // right (duplicated mono frame)
    }
    Py_DECREF(fast);
    return out;
}

static PyObject* find_silence_end(PyObject*, PyObject* args) {
    Py_buffer buffer{};
    double threshold = 0.005;   // ≈ -46 dBFS
    double min_ms = 120.0;      // silence shorter than this is ignored
    double sample_rate = 11025.0;
    if (!PyArg_ParseTuple(args, "y*|ddd", &buffer, &threshold, &min_ms, &sample_rate)) {
        return nullptr;
    }
    const int16_t* samples = reinterpret_cast<const int16_t*>(buffer.buf);
    const Py_ssize_t count = buffer.len / 2;  // mono s16le
    const int16_t q = static_cast<int16_t>(threshold * 32768.0);
    Py_ssize_t start = 0;
    while (start < count && abs16(samples[start]) <= q) ++start;
    Py_ssize_t end = count;
    while (end > start && abs16(samples[end - 1]) <= q) --end;
    const double lead = start / sample_rate;
    const double trail = (count - end) / sample_rate;
    PyBuffer_Release(&buffer);
    // Only report silence that is at least min_ms long (guards quiet intros).
    return Py_BuildValue("dd",
                         lead * 1000.0 >= min_ms ? lead : 0.0,
                         trail * 1000.0 >= min_ms ? trail : 0.0);
}

static PyMethodDef methods[] = {
    {"analyze_s16le_stereo", analyze_s16le_stereo, METH_VARARGS,
     "Return RMS and peak levels for signed-16-bit little-endian stereo PCM."},
    {"process_s16le_stereo", process_s16le_stereo, METH_VARARGS,
     "Apply left/right gain and a limiter to signed-16-bit stereo PCM."},
    {"compute_spectrum", compute_spectrum, METH_VARARGS,
     "Return 32 log-frequency spectrum bars (0.0-1.0) from stereo s16le PCM."},
    {"waveform_peaks", waveform_peaks, METH_VARARGS,
     "Return RMS/peak values per bucket for mono s16le PCM (waveform drawing)."},
    {"loudness_new", loudness_new, METH_VARARGS,
     "Create a BS.1770-4 loudness meter handle for the given sample rate."},
    {"loudness_process", loudness_process, METH_VARARGS,
     "Feed s16le stereo PCM to the meter; return gated integrated loudness (LUFS)."},
    {"loudness_reset", loudness_reset, METH_VARARGS,
     "Reset a loudness meter handle to its initial state."},
    {"samples_to_stereo_pcm", samples_to_stereo_pcm, METH_VARARGS,
     "Convert a sequence of float samples (-1..1) to s16le stereo PCM bytes."},
    {"find_silence_end", find_silence_end, METH_VARARGS,
     "Find leading/trailing silence (seconds) in mono s16le PCM."},
    {nullptr, nullptr, 0, nullptr},
};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "native_audio_meter", "FreQ native meter.", -1, methods};
PyMODINIT_FUNC PyInit_native_audio_meter() { return PyModule_Create(&module); }
