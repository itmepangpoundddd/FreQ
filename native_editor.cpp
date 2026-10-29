// Native audio editor core for FreQ.
//
// One C++ object holds a decoded track as interleaved float32 PCM and offers
// the destructive edits an operator needs on a queue item before it airs:
// trim (keep), delete, copy/paste, insert silence, fades (four curves), gain,
// normalize, reverse and mute. Waveform peaks are computed here too, so
// painting a 5-minute song costs one list of numbers, not a Python loop over
// millions of samples.
//
// Preview playback is a miniaudio device of its own, opened on the endpoint
// the operator picks ("Monitor"), so checking an edit never depends on — and
// never disturbs — the broadcast output. The callback reads the same buffer
// the edits mutate; a mutex keeps it consistent and a locked callback simply
// outputs silence for that one period instead of blocking the device.
//
// Build with build_native_meter.py (MSVC, see README).

#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <atomic>
#include <cmath>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace {

// ── Shared miniaudio context ────────────────────────────────────────────
// Enumeration and playback must share one context, otherwise the device IDs
// handed to Python do not line up with the ones ma_device_init() expects.

ma_context g_ctx;
bool g_ctx_ready = false;
std::vector<ma_device_id> g_device_ids;
std::vector<std::string> g_device_names;
std::vector<int> g_device_default;
std::mutex g_ctx_lock;

bool ensure_context() {
    std::lock_guard<std::mutex> guard(g_ctx_lock);
    if (g_ctx_ready) return true;
    ma_context_config cfg = ma_context_config_init();
    if (ma_context_init(nullptr, 0, &cfg, &g_ctx) != MA_SUCCESS) return false;
    g_ctx_ready = true;
    return true;
}

bool refresh_devices_locked() {
    ma_device_info* playback = nullptr;
    ma_uint32 count = 0;
    if (ma_context_get_devices(&g_ctx, &playback, &count, nullptr, nullptr) != MA_SUCCESS) {
        return false;
    }
    g_device_ids.clear();
    g_device_names.clear();
    g_device_default.clear();
    for (ma_uint32 i = 0; i < count; ++i) {
        g_device_ids.push_back(playback[i].id);
        g_device_names.push_back(std::string(playback[i].name));
        g_device_default.push_back(playback[i].isDefault ? 1 : 0);
    }
    return true;
}

// ── Track buffer ───────────────────────────────────────────────────────

struct AudioBuffer {
    std::vector<float> data;     // interleaved
    ma_uint32 channels = 2;
    ma_uint32 rate = 44100;

    ma_uint64 frames() const {
        return channels ? (ma_uint64)(data.size() / channels) : 0;
    }
    void resize_frames(ma_uint64 n) { data.resize((size_t)n * channels); }
};

struct EditorState {
    AudioBuffer buf;
    std::vector<float> clip;     // internal clipboard (interleaved)
    std::string path;

    std::mutex lock;             // guards buf/clip against the audio callback

    ma_device device;
    bool device_open = false;
    std::atomic<bool> playing{false};
    std::atomic<ma_uint64> pos{0};      // absolute frame being played
    ma_uint64 play_start = 0;
    ma_uint64 play_end = 0;
    float volume = 1.0f;
};

typedef struct {
    PyObject_HEAD
    EditorState* st;
} EditorObject;

// ── Helpers ────────────────────────────────────────────────────────────

inline ma_uint64 clamp_frames(const AudioBuffer& b, long long v) {
    if (v < 0) return 0;
    ma_uint64 n = b.frames();
    return (ma_uint64)v > n ? n : (ma_uint64)v;
}

inline double db_to_lin(double db) { return std::pow(10.0, db / 20.0); }
inline double lin_to_db(double lin) {
    return lin <= 1e-9 ? -120.0 : 20.0 * std::log10(lin);
}

// Audacity-style fade shapes, sampled at t in [0, 1].
inline float fade_shape(float t, int kind) {
    if (t <= 0.0f) return 0.0f;
    if (t >= 1.0f) return 1.0f;
    switch (kind) {
        case 1:  // logarithmic: fast rise, long tail
            return (float)(std::log10(1.0 + 9.0 * t));
        case 2:  // exponential: slow start, abrupt finish
            return t * t;
        case 3:  // s-curve
            return (float)(0.5 - 0.5 * std::cos(3.14159265358979323846 * t));
        default: // linear
            return t;
    }
}

void preview_callback(ma_device* device, void* output, const void*, ma_uint32 frame_count) {
    EditorState* st = (EditorState*)device->pUserData;
    float* out = (float*)output;
    const ma_uint32 ch = st->buf.channels ? st->buf.channels : 2;
    std::memset(out, 0, (size_t)frame_count * ch * sizeof(float));
    if (!st->playing.load(std::memory_order_acquire)) return;

    // Edits always stop the preview first, so this lock is uncontended during
    // playback. BLOCK rather than skip: skipping a period is exactly the
    // stutter an operator hears while a waveform repaints.
    std::lock_guard<std::mutex> guard(st->lock);

    const float* src = st->buf.data.data();
    const ma_uint64 total = st->buf.frames();
    ma_uint64 pos = st->pos.load(std::memory_order_relaxed);
    ma_uint64 end = st->play_end ? st->play_end : total;
    if (end > total) end = total;

    ma_uint32 written = 0;
    while (written < frame_count) {
        if (pos >= end) {
            st->playing.store(false, std::memory_order_release);
            break;
        }
        ma_uint64 room = end - pos;
        ma_uint32 take = (ma_uint32)((room < frame_count - written) ? room : frame_count - written);
        const float* in = src + pos * ch;
        float* dst = out + (size_t)written * ch;
        const float vol = st->volume;
        for (size_t i = 0; i < (size_t)take * ch; ++i) {
            dst[i] = in[i] * vol;
        }
        pos += take;
        written += take;
    }
    st->pos.store(pos, std::memory_order_relaxed);
}

void stop_preview(EditorState* st) {
    st->playing.store(false, std::memory_order_release);
    if (st->device_open) {
        ma_device_stop(&st->device);
        ma_device_uninit(&st->device);
        st->device_open = false;
    }
}

// ── Editor lifecycle ───────────────────────────────────────────────────

void Editor_dealloc(EditorObject* self) {
    if (self->st) {
        stop_preview(self->st);
        delete self->st;
        self->st = nullptr;
    }
    Py_TYPE(self)->tp_free((PyObject*)self);
}

PyObject* Editor_new(PyTypeObject* type, PyObject*, PyObject*) {
    EditorObject* self = (EditorObject*)type->tp_alloc(type, 0);
    if (!self) return nullptr;
    self->st = nullptr;
    return (PyObject*)self;
}

int Editor_init(EditorObject* self, PyObject* args, PyObject* kwargs) {
    PyObject* path_obj = nullptr;
    static const char* kwlist[] = {"path", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|O", const_cast<char**>(kwlist),
                                     &path_obj)) {
        return -1;
    }
    if (!path_obj || !PyUnicode_Check(path_obj)) {
        PyErr_SetString(PyExc_TypeError, "path must be a str");
        return -1;
    }
    Py_ssize_t path_len = 0;
    const char* path = PyUnicode_AsUTF8AndSize(path_obj, &path_len);
    if (!path) return -1;

    EditorState* st = new EditorState();
    ma_decoder decoder;
    ma_decoder_config cfg = ma_decoder_config_init(ma_format_f32, 0, 0);
    // A Thai (or any non-ANSI) file name must be opened as a WIDE path:
    // miniaudio's narrow API goes through the ANSI code page on Windows, which
    // turns the name into mojibake and fails to find the file at all.
    ma_result open_result = MA_ERROR;
#ifdef _WIN32
    wchar_t* wpath = PyUnicode_AsWideCharString(path_obj, nullptr);
    if (wpath) {
        open_result = ma_decoder_init_file_w(wpath, &cfg, &decoder);
        PyMem_Free(wpath);
    }
#else
    open_result = ma_decoder_init_file(path, &cfg, &decoder);
#endif
    if (open_result != MA_SUCCESS) {
        delete st;
        PyErr_Format(PyExc_RuntimeError, "cannot decode '%s'", path);
        return -1;
    }
    st->buf.rate = decoder.outputSampleRate;
    st->buf.channels = decoder.outputChannels;
    st->path = path;

    ma_uint64 total = 0;
    if (ma_decoder_get_length_in_pcm_frames(&decoder, &total) != MA_SUCCESS || total == 0) {
        // Length unknown (some containers): decode until the stream ends.
        total = 0;
    }
    const ma_uint32 ch = st->buf.channels;
    const ma_uint64 chunk_frames = 65536;

    if (total > 0) {
        // Guard against a "song" that is actually an hour-long file: the whole
        // track lives in RAM while it is open in the editor.
        if (total > (ma_uint64)(st->buf.rate) * 60ULL * 60ULL) {
            ma_decoder_uninit(&decoder);
            delete st;
            PyErr_SetString(PyExc_ValueError, "file is longer than 60 minutes");
            return -1;
        }
        st->buf.resize_frames(total);
        ma_uint64 done = 0;
        while (done < total) {
            ma_uint64 want = total - done;
            if (want > chunk_frames) want = chunk_frames;
            ma_uint64 got = 0;
            if (ma_decoder_read_pcm_frames(&decoder, st->buf.data.data() + done * ch,
                                           want, &got) != MA_SUCCESS || got == 0) {
                break;
            }
            done += got;
        }
        st->buf.resize_frames(done);
    } else {
        std::vector<float> chunk((size_t)chunk_frames * ch);
        for (;;) {
            ma_uint64 got = 0;
            if (ma_decoder_read_pcm_frames(&decoder, chunk.data(), chunk_frames, &got)
                    != MA_SUCCESS || got == 0) {
                break;
            }
            st->buf.data.insert(st->buf.data.end(), chunk.begin(),
                                chunk.begin() + (size_t)got * ch);
        }
    }
    ma_decoder_uninit(&decoder);

    if (self->st) {  // re-init on an existing object
        stop_preview(self->st);
        delete self->st;
    }
    self->st = st;
    return 0;
}

// ── Read-only properties ───────────────────────────────────────────────

PyObject* Editor_get_frames(EditorObject* self, void*) {
    return PyLong_FromUnsignedLongLong((unsigned long long)self->st->buf.frames());
}
PyObject* Editor_get_rate(EditorObject* self, void*) {
    return PyLong_FromUnsignedLong((unsigned long)self->st->buf.rate);
}
PyObject* Editor_get_channels(EditorObject* self, void*) {
    return PyLong_FromUnsignedLong((unsigned long)self->st->buf.channels);
}
PyObject* Editor_get_duration(EditorObject* self, void*) {
    const AudioBuffer& b = self->st->buf;
    double secs = b.rate ? (double)b.frames() / (double)b.rate : 0.0;
    return PyFloat_FromDouble(secs);
}
PyObject* Editor_get_path(EditorObject* self, void*) {
    return PyUnicode_FromString(self->st->path.c_str());
}
PyObject* Editor_get_clip_frames(EditorObject* self, void*) {
    const AudioBuffer& b = self->st->buf;
    ma_uint64 n = b.channels ? self->st->clip.size() / b.channels : 0;
    return PyLong_FromUnsignedLongLong((unsigned long long)n);
}
PyObject* Editor_get_playing(EditorObject* self, void*) {
    if (self->st->playing.load() && self->st->pos.load() >= self->st->play_end
            && self->st->play_end != 0) {
        return PyBool_FromLong(0);
    }
    return PyBool_FromLong(self->st->playing.load() ? 1 : 0);
}
PyObject* Editor_get_position(EditorObject* self, void*) {
    ma_uint64 pos = self->st->pos.load();
    ma_uint64 start = self->st->play_start;
    return PyLong_FromUnsignedLongLong((unsigned long long)(pos > start ? pos - start : 0));
}

PyGetSetDef Editor_getset[] = {
    {"frames", (getter)Editor_get_frames, nullptr, "Length in frames", nullptr},
    {"sample_rate", (getter)Editor_get_rate, nullptr, "Sample rate (Hz)", nullptr},
    {"channels", (getter)Editor_get_channels, nullptr, "Channel count", nullptr},
    {"duration", (getter)Editor_get_duration, nullptr, "Length in seconds", nullptr},
    {"path", (getter)Editor_get_path, nullptr, "Source file", nullptr},
    {"clip_frames", (getter)Editor_get_clip_frames, nullptr, "Frames on the clipboard", nullptr},
    {"playing", (getter)Editor_get_playing, nullptr, "Preview device is running", nullptr},
    {"position_frames", (getter)Editor_get_position, nullptr,
     "Frames played since preview_start", nullptr},
    {nullptr, nullptr, nullptr, nullptr, nullptr},
};

// ── Waveform / statistics ──────────────────────────────────────────────

PyObject* Editor_peaks(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    int buckets = 0;
    if (!PyArg_ParseTuple(args, "KKi", &start, &end, &buckets)) return nullptr;
    if (buckets <= 0) {
        PyErr_SetString(PyExc_ValueError, "buckets must be positive");
        return nullptr;
    }
    // Read-only and always called from the Python (UI) thread — the same
    // thread that mutates the buffer — so no lock is needed here. Taking one
    // made every repaint block the audio callback for tens of milliseconds.
    const AudioBuffer& b = self->st->buf;
    const ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e < s) e = s;

    PyObject* list = PyList_New((Py_ssize_t)buckets * 2);
    if (!list) return nullptr;
    const ma_uint32 ch = b.channels;
    for (int i = 0; i < buckets; ++i) {
        ma_uint64 f0 = s + (ma_uint64)((e - s) * (ma_uint64)i / (ma_uint64)buckets);
        ma_uint64 f1 = s + (ma_uint64)((e - s) * (ma_uint64)(i + 1) / (ma_uint64)buckets);
        if (f1 <= f0) f1 = f0 + 1;
        if (f1 > total) f1 = total;
        float lo = 0.0f, hi = 0.0f;
        bool any = false;
        for (ma_uint64 f = f0; f < f1; ++f) {
            float v = 0.0f;
            for (ma_uint32 c = 0; c < ch; ++c) v += b.data[(size_t)f * ch + c];
            v /= (float)(ch ? ch : 1);
            if (!any) { lo = hi = v; any = true; }
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        PyList_SET_ITEM(list, i * 2, PyFloat_FromDouble(lo));
        PyList_SET_ITEM(list, i * 2 + 1, PyFloat_FromDouble(hi));
    }
    return list;
}

PyObject* Editor_stats(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    // Read-only, UI thread only: see Editor_peaks.
    const AudioBuffer& b = self->st->buf;
    const ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e < s) e = s;
    const ma_uint32 ch = b.channels;
    double peak = 0.0, sumsq = 0.0;
    ma_uint64 count = 0;
    for (ma_uint64 f = s; f < e; ++f) {
        for (ma_uint32 c = 0; c < ch; ++c) {
            double v = b.data[(size_t)f * ch + c];
            double a = std::fabs(v);
            if (a > peak) peak = a;
            sumsq += v * v;
            ++count;
        }
    }
    double rms = count ? std::sqrt(sumsq / (double)count) : 0.0;
    PyObject* dict = Py_BuildValue("{s:d,s:d}", "peak_db", lin_to_db(peak),
                                   "rms_db", lin_to_db(rms));
    return dict;
}

// ── Editing operations ─────────────────────────────────────────────────

PyObject* Editor_delete(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e <= s) {
        Py_RETURN_NONE;
    }
    const ma_uint32 ch = b.channels;
    b.data.erase(b.data.begin() + (size_t)s * ch, b.data.begin() + (size_t)e * ch);
    Py_RETURN_NONE;
}

PyObject* Editor_insert(EditorObject* self, PyObject* args) {
    unsigned long long pos = 0, frames = 0;
    if (!PyArg_ParseTuple(args, "KK", &pos, &frames)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 p = pos > total ? total : pos;
    if (frames == 0) Py_RETURN_NONE;
    b.data.insert(b.data.begin() + (size_t)p * b.channels,
                  (size_t)frames * b.channels, 0.0f);
    Py_RETURN_NONE;
}

// Shared body for gain / normalize / reverse / silence / fades.
PyObject* Editor_gain(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    double db = 0.0;
    if (!PyArg_ParseTuple(args, "KKd", &start, &end, &db)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e > s) {
        const float factor = (float)db_to_lin(db);
        for (size_t i = (size_t)s * b.channels; i < (size_t)e * b.channels; ++i) {
            b.data[i] *= factor;
        }
    }
    Py_RETURN_NONE;
}

PyObject* Editor_normalize(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    double target_db = -1.0;
    if (!PyArg_ParseTuple(args, "KK|d", &start, &end, &target_db)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    double peak = 0.0;
    for (size_t i = (size_t)s * b.channels; i < (size_t)e * b.channels; ++i) {
        double a = std::fabs((double)b.data[i]);
        if (a > peak) peak = a;
    }
    double applied = 0.0;
    if (peak > 1e-9) {
        applied = lin_to_db(db_to_lin(target_db) / peak);
        const float factor = (float)db_to_lin(applied);
        for (size_t i = (size_t)s * b.channels; i < (size_t)e * b.channels; ++i) {
            b.data[i] *= factor;
        }
    }
    return PyFloat_FromDouble(applied);
}

PyObject* Editor_silence(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e > s) {
        std::memset(b.data.data() + (size_t)s * b.channels, 0,
                    (size_t)(e - s) * b.channels * sizeof(float));
    }
    Py_RETURN_NONE;
}

PyObject* Editor_reverse(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    const ma_uint32 ch = b.channels;
    if (e > s) {
        ma_uint64 last = e - 1;
        while (s < last) {
            for (ma_uint32 c = 0; c < ch; ++c) {
                std::swap(b.data[(size_t)s * ch + c], b.data[(size_t)last * ch + c]);
            }
            ++s;
            --last;
        }
    }
    Py_RETURN_NONE;
}

PyObject* Editor_fade(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    int kind = 0;
    int fade_in = 1;
    if (!PyArg_ParseTuple(args, "KK|ii", &start, &end, &kind, &fade_in)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    const ma_uint32 ch = b.channels;
    const ma_uint64 span = e > s ? e - s : 0;
    if (span > 1) {
        for (ma_uint64 f = s; f < e; ++f) {
            float t = (float)((double)(f - s) / (double)(span - 1));
            float g = fade_shape(fade_in ? t : 1.0f - t, kind);
            for (ma_uint32 c = 0; c < ch; ++c) b.data[(size_t)f * ch + c] *= g;
        }
    }
    Py_RETURN_NONE;
}

PyObject* Editor_copy(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e < s) e = s;
    self->st->clip.assign(b.data.begin() + (size_t)s * b.channels,
                          b.data.begin() + (size_t)e * b.channels);
    Py_RETURN_NONE;
}

PyObject* Editor_paste(EditorObject* self, PyObject* args) {
    unsigned long long pos = 0;
    if (!PyArg_ParseTuple(args, "K", &pos)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 p = pos > total ? total : pos;
    if (self->st->clip.empty()) {
        PyErr_SetString(PyExc_ValueError, "clipboard is empty");
        return nullptr;
    }
    b.data.insert(b.data.begin() + (size_t)p * b.channels,
                  self->st->clip.begin(), self->st->clip.end());
    Py_RETURN_NONE;
}

PyObject* Editor_get_range(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    if (!PyArg_ParseTuple(args, "KK", &start, &end)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e < s) e = s;
    size_t begin = (size_t)s * b.channels;
    size_t len = (size_t)(e - s) * b.channels;
    return PyBytes_FromStringAndSize(
        len ? (const char*)(b.data.data() + begin) : "",
        (Py_ssize_t)(len * sizeof(float)));
}

PyObject* Editor_insert_data(EditorObject* self, PyObject* args) {
    unsigned long long pos = 0;
    Py_buffer view;
    if (!PyArg_ParseTuple(args, "Ky*", &pos, &view)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    const size_t frame_bytes = (size_t)b.channels * sizeof(float);
    if (frame_bytes == 0 || (size_t)view.len % frame_bytes != 0) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "data is not whole frames");
        return nullptr;
    }
    ma_uint64 total = b.frames();
    ma_uint64 p = pos > total ? total : pos;
    const float* src = (const float*)view.buf;
    size_t count = (size_t)view.len / sizeof(float);
    b.data.insert(b.data.begin() + (size_t)p * b.channels, src, src + count);
    PyBuffer_Release(&view);
    Py_RETURN_NONE;
}

PyObject* Editor_set_range(EditorObject* self, PyObject* args) {    unsigned long long pos = 0;
    Py_buffer view;
    if (!PyArg_ParseTuple(args, "Ky*", &pos, &view)) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    AudioBuffer& b = self->st->buf;
    ma_uint64 total = b.frames();
    const size_t frame_bytes = (size_t)b.channels * sizeof(float);
    if (frame_bytes == 0 || (size_t)view.len % frame_bytes != 0) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "data is not whole frames");
        return nullptr;
    }
    ma_uint64 frames = (ma_uint64)((size_t)view.len / frame_bytes);
    if (pos > total || pos + frames > total) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "range does not fit the track");
        return nullptr;
    }
    std::memcpy(b.data.data() + (size_t)pos * b.channels, view.buf, view.len);
    PyBuffer_Release(&view);
    Py_RETURN_NONE;
}

PyObject* Editor_save_wav(EditorObject* self, PyObject* args) {
    PyObject* path_obj = nullptr;
    if (!PyArg_ParseTuple(args, "O", &path_obj)) return nullptr;
    if (!PyUnicode_Check(path_obj)) {
        PyErr_SetString(PyExc_TypeError, "path must be a str");
        return nullptr;
    }
    const char* path = PyUnicode_AsUTF8(path_obj);
    if (!path) return nullptr;
    std::lock_guard<std::mutex> guard(self->st->lock);
    const AudioBuffer& b = self->st->buf;
    const ma_uint32 ch = b.channels;
    const ma_uint32 rate = b.rate;
    const size_t frames = (size_t)b.frames();
    const size_t data_bytes = frames * ch * 2;
    // Wide path on Windows: a narrow fopen() writes a mojibake file name that
    // Python (and the queue) can never find again.
#ifdef _WIN32
    wchar_t* wpath = PyUnicode_AsWideCharString(path_obj, nullptr);
    FILE* fp = wpath ? _wfopen(wpath, L"wb") : nullptr;
    if (wpath) PyMem_Free(wpath);
#else
    FILE* fp = std::fopen(path, "wb");
#endif
    if (!fp) {
        PyErr_Format(PyExc_OSError, "cannot write '%s'", path);
        return nullptr;
    }
    auto put32 = [&](unsigned long v) {
        unsigned char buf[4] = {(unsigned char)(v & 0xFF), (unsigned char)((v >> 8) & 0xFF),
                                (unsigned char)((v >> 16) & 0xFF),
                                (unsigned char)((v >> 24) & 0xFF)};
        std::fwrite(buf, 1, 4, fp);
    };
    auto put16 = [&](unsigned short v) {
        unsigned char buf[2] = {(unsigned char)(v & 0xFF), (unsigned char)((v >> 8) & 0xFF)};
        std::fwrite(buf, 1, 2, fp);
    };
    std::fwrite("RIFF", 1, 4, fp);
    put32((unsigned long)(36 + data_bytes));
    std::fwrite("WAVEfmt ", 1, 8, fp);
    put32(16);
    put16(1);                                  // PCM
    put16((unsigned short)ch);
    put32(rate);
    put32(rate * ch * 2);                      // byte rate
    put16((unsigned short)(ch * 2));           // block align
    put16(16);                                 // bits per sample
    std::fwrite("data", 1, 4, fp);
    put32((unsigned long)data_bytes);
    // Convert in blocks so a long track never needs a second full-size copy.
    std::vector<short> block;
    block.resize(65536);
    size_t done = 0;
    while (done < frames * ch) {
        size_t want = frames * ch - done;
        if (want > block.size()) want = block.size();
        for (size_t i = 0; i < want; ++i) {
            float v = b.data[done + i];
            if (v > 1.0f) v = 1.0f;
            if (v < -1.0f) v = -1.0f;
            block[i] = (short)(v * 32767.0f);
        }
        std::fwrite(block.data(), sizeof(short), want, fp);
        done += want;
    }
    std::fclose(fp);
    Py_RETURN_NONE;
}

// ── Monitor preview ────────────────────────────────────────────────────

PyObject* Editor_preview_start(EditorObject* self, PyObject* args) {
    unsigned long long start = 0, end = 0;
    int device_index = -1;
    float volume = 1.0f;
    if (!PyArg_ParseTuple(args, "KK|if", &start, &end, &device_index, &volume)) {
        return nullptr;
    }
    EditorState* st = self->st;
    stop_preview(st);
    if (!ensure_context()) {
        PyErr_SetString(PyExc_RuntimeError, "audio backend unavailable");
        return nullptr;
    }
    const ma_uint64 total = st->buf.frames();
    ma_uint64 s = start > total ? total : start;
    ma_uint64 e = end > total ? total : end;
    if (e <= s) {
        PyErr_SetString(PyExc_ValueError, "empty playback range");
        return nullptr;
    }
    const ma_device_id* dev_id = nullptr;
    {
        std::lock_guard<std::mutex> guard(g_ctx_lock);
        if (!refresh_devices_locked()) {
            PyErr_SetString(PyExc_RuntimeError, "cannot enumerate output devices");
            return nullptr;
        }
        if (device_index >= 0) {
            if ((size_t)device_index >= g_device_ids.size()) {
                PyErr_SetString(PyExc_IndexError, "device index out of range");
                return nullptr;
            }
            dev_id = &g_device_ids[(size_t)device_index];
        }
    }

    ma_device_config cfg = ma_device_config_init(ma_device_type_playback);
    cfg.playback.format = ma_format_f32;
    cfg.playback.channels = st->buf.channels ? st->buf.channels : 2;
    cfg.sampleRate = st->buf.rate ? st->buf.rate : 44100;
    cfg.dataCallback = preview_callback;
    // A steady 20 ms period gives WASAPI enough room to ride out a busy UI
    // thread; the default (10 ms) was where the dropouts showed up.
    cfg.periodSizeInMilliseconds = 20;
    if (dev_id) cfg.playback.pDeviceID = dev_id;
    cfg.pUserData = st;

    ma_result res;
    {
        std::lock_guard<std::mutex> ctx_guard(g_ctx_lock);
        res = ma_device_init(&g_ctx, &cfg, &st->device);
    }
    if (res != MA_SUCCESS) {
        PyErr_Format(PyExc_RuntimeError, "cannot open monitor device (miniaudio %d)", (int)res);
        return nullptr;
    }
    st->device.pUserData = st;
    st->volume = volume <= 0.0f ? 0.0f : (volume > 2.0f ? 2.0f : volume);
    st->play_start = s;
    st->play_end = e;
    st->pos.store(s);
    st->playing.store(true);
    if (ma_device_start(&st->device) != MA_SUCCESS) {
        ma_device_uninit(&st->device);
        st->playing.store(false);
        PyErr_SetString(PyExc_RuntimeError, "cannot start monitor playback");
        return nullptr;
    }
    st->device_open = true;
    Py_RETURN_TRUE;
}

PyObject* Editor_preview_stop(EditorObject* self, PyObject*) {
    stop_preview(self->st);
    Py_RETURN_NONE;
}

PyObject* Editor_preview_set_volume(EditorObject* self, PyObject* args) {
    float volume = 1.0f;
    if (!PyArg_ParseTuple(args, "f", &volume)) return nullptr;
    if (volume < 0.0f) volume = 0.0f;
    if (volume > 2.0f) volume = 2.0f;
    self->st->volume = volume;
    Py_RETURN_NONE;
}

// ── Module functions ───────────────────────────────────────────────────

PyObject* list_devices(PyObject*, PyObject*) {
    if (!ensure_context()) {
        PyErr_SetString(PyExc_RuntimeError, "audio backend unavailable");
        return nullptr;
    }
    std::lock_guard<std::mutex> guard(g_ctx_lock);
    if (!refresh_devices_locked()) {
        PyErr_SetString(PyExc_RuntimeError, "cannot enumerate output devices");
        return nullptr;
    }
    PyObject* list = PyList_New((Py_ssize_t)g_device_ids.size());
    if (!list) return nullptr;
    for (size_t i = 0; i < g_device_ids.size(); ++i) {
        PyObject* item = Py_BuildValue("{s:i,s:s,s:i}", "index", (int)i,
                                       "name", g_device_names[i].c_str(),
                                       "is_default", g_device_default[i]);
        if (!item) {
            Py_DECREF(list);
            return nullptr;
        }
        PyList_SET_ITEM(list, (Py_ssize_t)i, item);
    }
    return list;
}

PyMethodDef Editor_methods[] = {
    {"peaks", (PyCFunction)Editor_peaks, METH_VARARGS,
     "peaks(start_frame, end_frame, buckets) -> [min0, max0, ...]"},
    {"stats", (PyCFunction)Editor_stats, METH_VARARGS,
     "stats(start_frame, end_frame) -> {'peak_db': .., 'rms_db': ..}"},
    {"delete", (PyCFunction)Editor_delete, METH_VARARGS, "delete(start, end)"},
    {"insert_silence", (PyCFunction)Editor_insert, METH_VARARGS, "insert_silence(pos, frames)"},
    {"gain", (PyCFunction)Editor_gain, METH_VARARGS, "gain(start, end, db)"},
    {"normalize", (PyCFunction)Editor_normalize, METH_VARARGS,
     "normalize(start, end, target_db=-1) -> applied db"},
    {"silence", (PyCFunction)Editor_silence, METH_VARARGS, "silence(start, end)"},
    {"reverse", (PyCFunction)Editor_reverse, METH_VARARGS, "reverse(start, end)"},
    {"fade", (PyCFunction)Editor_fade, METH_VARARGS,
     "fade(start, end, curve=0, fade_in=1)"},
    {"copy", (PyCFunction)Editor_copy, METH_VARARGS, "copy(start, end)"},
    {"paste", (PyCFunction)Editor_paste, METH_VARARGS, "paste(pos)"},
    {"get_range", (PyCFunction)Editor_get_range, METH_VARARGS,
     "get_range(start, end) -> float32 bytes"},
    {"set_range", (PyCFunction)Editor_set_range, METH_VARARGS, "set_range(pos, float32 bytes)"},
    {"insert_data", (PyCFunction)Editor_insert_data, METH_VARARGS,
     "insert_data(pos, float32 bytes)"},
    {"save_wav", (PyCFunction)Editor_save_wav, METH_VARARGS, "save_wav(path)"},
    {"preview_start", (PyCFunction)Editor_preview_start, METH_VARARGS,
     "preview_start(start, end, device_index=-1, volume=1.0)"},
    {"preview_stop", (PyCFunction)Editor_preview_stop, METH_NOARGS, "preview_stop()"},
    {"preview_set_volume", (PyCFunction)Editor_preview_set_volume, METH_VARARGS,
     "preview_set_volume(v)"},
    {nullptr, nullptr, 0, nullptr},
};

PyTypeObject EditorType = {
    PyVarObject_HEAD_INIT(nullptr, 0)
    "native_editor.Editor",                 /* tp_name */
    sizeof(EditorObject),                   /* tp_basicsize */
};

PyMethodDef module_methods[] = {
    {"list_devices", (PyCFunction)list_devices, METH_NOARGS,
     "list_devices() -> [{'index':, 'name':, 'is_default':}]"},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    "native_editor",
    "Native audio editor core (decode, edit, peaks, monitor preview).",
    -1,
    module_methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

PyMODINIT_FUNC PyInit_native_editor(void) {
    EditorType.tp_new = Editor_new;
    EditorType.tp_init = (initproc)Editor_init;
    EditorType.tp_dealloc = (destructor)Editor_dealloc;
    EditorType.tp_methods = Editor_methods;
    EditorType.tp_getset = Editor_getset;
    EditorType.tp_flags = Py_TPFLAGS_DEFAULT;
    EditorType.tp_doc = "A decoded track with destructive editing operations.";
    if (PyType_Ready(&EditorType) < 0) return nullptr;

    PyObject* module = PyModule_Create(&module_def);
    if (!module) return nullptr;
    Py_INCREF(&EditorType);
    if (PyModule_AddObject(module, "Editor", (PyObject*)&EditorType) < 0) {
        Py_DECREF(&EditorType);
        Py_DECREF(module);
        return nullptr;
    }
    return module;
}
