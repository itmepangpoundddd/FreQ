// Native air-check recorder for FreQ — WAV writing moved fully into C++.
//
// Python hands over a destination path (+ optional endpoint id / loopback
// flag). A dedicated C++ thread captures s16le PCM from WASAPI and appends
// it straight to the WAV file — the audio bytes never detour through a
// Python loop (read → Python → wave write), so a busy GIL cannot stall or
// corrupt a broadcast recording.
//
// Design notes (mirror native_audio_capture):
//  * Polling mode (GetCurrentPadding/GetBuffer) for both mic and loopback —
//    event-driven loopback capture is unreliable across devices.
//  * All state is module-level atomics; Python polls aircheck_state() /
//    aircheck_levels(). Only ONE recorder runs at a time (the app has one
//    air-check recorder); a second aircheck_start() raises.
//  * stop() joins with a timeout and reports (frames, seconds, rate, ch).
//    The WAV header sizes are patched on stop, so the file stays a valid
//    WAV even if the process dies mid-recording (header is written
//    up-front with data size 0 and patched at close).
//
// Build: added to SOURCES/STEMS in build_native_meter.py (ole32+avrt).
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mmdeviceapi.h>
#include <audioclient.h>
#include <avrt.h>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace {

std::atomic<int>  g_state{0};          // 0 idle, 1 recording
std::atomic<bool> g_running{false};
std::atomic<bool> g_zombie{false};     // thread outlived a stop join
std::atomic<uint64_t> g_frames{0};

HANDLE g_thread = nullptr;
std::mutex g_meter_mtx;
float g_peak[2] = {0.f, 0.f};
std::atomic<int> g_rate{48000};
std::atomic<int> g_ch{2};
std::string g_last_error;
std::mutex g_err_mtx;

// ── helpers ────────────────────────────────────────────────────────────────
static std::wstring ToWide(const std::string& s) {
    int wlen = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w((size_t)wlen, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), wlen);
    if (!w.empty() && w.back() == L'\0') w.pop_back();
    return w;
}

static void SetError(const std::string& msg) {
    std::lock_guard<std::mutex> lk(g_err_mtx);
    g_last_error = msg;
}

static void WriteWaveHeader(FILE* f, uint32_t rate, uint16_t ch) {
    uint32_t byte_rate = rate * ch * 2;
    uint8_t h[44] = {
        'R','I','F','F', 0,0,0,0, 'W','A','V','E',
        'f','m','t',' ', 16,0,0,0, 1,0, (uint8_t)(ch & 0xFF), 0,
        (uint8_t)(rate & 0xFF), (uint8_t)((rate >> 8) & 0xFF),
        (uint8_t)((rate >> 16) & 0xFF), (uint8_t)((rate >> 24) & 0xFF),
        (uint8_t)(byte_rate & 0xFF), (uint8_t)((byte_rate >> 8) & 0xFF),
        (uint8_t)((byte_rate >> 16) & 0xFF), (uint8_t)((byte_rate >> 24) & 0xFF),
        (uint8_t)((ch * 2) & 0xFF), 0, 16, 0,
        'd','a','t','a', 0,0,0,0,
    };
    fwrite(h, 1, 44, f);
}

static void PatchWaveHeader(FILE* f, uint64_t data_bytes) {
    uint32_t riff = (uint32_t)(36 + data_bytes);
    uint32_t dat = (uint32_t)data_bytes;
    fseek(f, 4, SEEK_SET);
    fwrite(&riff, 4, 1, f);
    fseek(f, 40, SEEK_SET);
    fwrite(&dat, 4, 1, f);
    fseek(f, 0, SEEK_END);
}

static void ConvertToS16(const BYTE* src, WAVEFORMATEX* fmt, UINT32 frames,
                         std::vector<uint8_t>& out) {
    const int ch = fmt->nChannels;
    if (fmt->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        auto* ext = (WAVEFORMATEXTENSIBLE*)fmt;
        if (ext->SubFormat.Data1 == 3 /* IEEE_FLOAT */) {
            const float* f = (const float*)src;
            for (UINT32 i = 0; i < frames * (UINT32)ch; ++i) {
                float v = f[i];
                if (v > 1.0f) v = 1.0f;
                if (v < -1.0f) v = -1.0f;
                int16_t s = (int16_t)(v * 32767.0f);
                out.push_back((uint8_t)(s & 0xFF));
                out.push_back((uint8_t)((s >> 8) & 0xFF));
            }
            return;
        }
    }
    if (fmt->wBitsPerSample == 16) {
        out.insert(out.end(), src, src + (size_t)frames * ch * 2);
        return;
    }
    if (fmt->wBitsPerSample == 32) {
        const int32_t* s32 = (const int32_t*)src;
        for (UINT32 i = 0; i < frames * (UINT32)ch; ++i) {
            int16_t s = (int16_t)(s32[i] >> 16);
            out.push_back((uint8_t)(s & 0xFF));
            out.push_back((uint8_t)((s >> 8) & 0xFF));
        }
        return;
    }
    if (fmt->wBitsPerSample == 24) {
        const uint8_t* b = src;
        for (UINT32 i = 0; i < frames * (UINT32)ch; ++i) {
            int32_t v = (int32_t)((uint32_t)b[i * 3]
                                  | ((uint32_t)b[i * 3 + 1] << 8)
                                  | ((uint32_t)b[i * 3 + 2] << 16));
            v <<= 8; v >>= 8;
            int16_t s = (int16_t)(v >> 8);
            out.push_back((uint8_t)(s & 0xFF));
            out.push_back((uint8_t)((s >> 8) & 0xFF));
        }
        return;
    }
    out.resize(out.size() + (size_t)frames * ch * 2, 0);
}

static void UpdateMeter(const int16_t* samples, size_t count, int ch) {
    float pk[2] = {0.f, 0.f};
    const int mch = ch >= 2 ? 2 : ch;
    for (size_t i = 0; i + (size_t)ch <= count; i += (size_t)ch) {
        for (int k = 0; k < mch; ++k) {
            float v = std::fabs(samples[i + k] / 32768.0f);
            if (v > pk[k]) pk[k] = v;
        }
    }
    std::lock_guard<std::mutex> lk(g_meter_mtx);
    for (int k = 0; k < mch; ++k)
        if (pk[k] > g_peak[k]) g_peak[k] = pk[k];
}

// ── recorder thread ───────────────────────────────────────────────────────
struct RecSpec {
    std::wstring path;
    std::wstring endpoint;   // empty = default endpoint
    bool loopback;
};

static DWORD WINAPI RecordProc(LPVOID param) {
    RecSpec* spec = (RecSpec*)param;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);

    FILE* f = nullptr;
    _wfopen_s(&f, spec->path.c_str(), L"wb");
    if (!f) {
        SetError("cannot open destination WAV file");
        g_state = 0; g_running = false;
        delete spec;
        return 0;
    }

    IMMDeviceEnumerator* en = nullptr;
    IMMDevice* dev = nullptr;
    IAudioClient* client = nullptr;
    IAudioCaptureClient* cap = nullptr;
    WAVEFORMATEX* fmt = nullptr;
    uint64_t data_bytes = 0;

    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                  CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                  (void**)&en);
    if (SUCCEEDED(hr)) {
        if (!spec->endpoint.empty())
            hr = en->GetDevice(spec->endpoint.c_str(), &dev);
        else if (spec->loopback)
            hr = en->GetDefaultAudioEndpoint(eRender, eConsole, &dev);
        else
            hr = en->GetDefaultAudioEndpoint(eCapture, eConsole, &dev);
    }
    if (SUCCEEDED(hr))
        hr = dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                           (void**)&client);
    if (SUCCEEDED(hr))
        hr = client->GetMixFormat(&fmt);
    if (SUCCEEDED(hr))
        hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                                spec->loopback ? AUDCLNT_STREAMFLAGS_LOOPBACK : 0,
                                200000 /*20 ms*/, 0, fmt, nullptr);
    if (SUCCEEDED(hr))
        hr = client->GetService(__uuidof(IAudioCaptureClient), (void**)&cap);
    if (SUCCEEDED(hr))
        hr = client->Start();

    if (FAILED(hr) || !fmt) {
        SetError("cannot open capture device for recording");
        if (f) { PatchWaveHeader(f, 0); fclose(f); }
        if (fmt) CoTaskMemFree(fmt);
        if (cap) cap->Release();
        if (client) client->Release();
        if (dev) dev->Release();
        if (en) en->Release();
        g_state = 0; g_running = false;
        delete spec;
        return 0;
    }

    const int ch = fmt->nChannels;
    const int rate = fmt->nSamplesPerSec;
    g_rate = rate;
    g_ch = ch;
    WriteWaveHeader(f, (uint32_t)rate, (uint16_t)ch);

    std::vector<uint8_t> conv;
    conv.reserve(44100 * (size_t)ch * 2);

    while (g_running) {
        Sleep(8);
        UINT32 packet = 0;
        while (g_running &&
               cap->GetNextPacketSize(&packet) == S_OK && packet > 0) {
            BYTE* data = nullptr;
            UINT32 frames = 0;
            DWORD flags = 0;
            if (cap->GetBuffer(&data, &frames, &flags, nullptr, nullptr) != S_OK)
                break;
            conv.clear();
            if (data && !(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                ConvertToS16(data, fmt, frames, conv);
            else
                conv.assign((size_t)frames * ch * 2, 0);
            if (!conv.empty()) {
                fwrite(conv.data(), 1, conv.size(), f);
                data_bytes += conv.size();
                g_frames = data_bytes / ((uint64_t)ch * 2);
                UpdateMeter((const int16_t*)conv.data(), conv.size() / 2, ch);
            }
            cap->ReleaseBuffer(frames);
        }
        std::lock_guard<std::mutex> lk(g_meter_mtx);
        g_peak[0] *= 0.72f;
        g_peak[1] *= 0.72f;
    }

    client->Stop();
    PatchWaveHeader(f, data_bytes);
    fclose(f);
    CoTaskMemFree(fmt);
    if (cap) cap->Release();
    if (client) client->Release();
    if (dev) dev->Release();
    if (en) en->Release();
    delete spec;
    g_state = 0;
    return 0;
}

// ── Python API ────────────────────────────────────────────────────────────
static PyObject* py_aircheck_start(PyObject*, PyObject* args) {
    const char* path = nullptr;
    const char* endpoint = "";
    int loopback = 0;
    if (!PyArg_ParseTuple(args, "s|sp", &path, &endpoint, &loopback))
        return nullptr;
    if (g_zombie.load()) {
        PyErr_SetString(PyExc_RuntimeError,
                        "previous recorder thread leaked (stuck device) — "
                        "the process cannot record again");
        return nullptr;
    }
    int expected = 0;
    if (!g_state.compare_exchange_strong(expected, 1)) {
        PyErr_SetString(PyExc_RuntimeError, "air-check is already recording");
        return nullptr;
    }
    if (g_thread) { CloseHandle(g_thread); g_thread = nullptr; }
    g_frames = 0;
    g_peak[0] = g_peak[1] = 0.f;
    { std::lock_guard<std::mutex> lk(g_err_mtx); g_last_error.clear(); }

    RecSpec* spec = new RecSpec{ToWide(path), ToWide(endpoint),
                                loopback != 0};
    g_running = true;
    g_thread = CreateThread(nullptr, 0, RecordProc, spec, 0, nullptr);
    if (!g_thread) {
        g_running = false;
        g_state = 0;
        delete spec;
        PyErr_SetString(PyExc_RuntimeError, "cannot start recorder thread");
        return nullptr;
    }
    Py_RETURN_NONE;
}

static PyObject* py_aircheck_stop(PyObject*, PyObject*) {
    if (g_running.load()) {
        g_running = false;
        if (g_thread) {
            if (WaitForSingleObject(g_thread, 4000) != WAIT_OBJECT_0) {
                // Parked inside a stuck WASAPI call — leak it (same policy
                // as native_audio_capture) and refuse future starts.
                g_zombie = true;
                SetError("recorder thread leaked on stop");
                Py_RETURN_NONE;
            }
            CloseHandle(g_thread);
            g_thread = nullptr;
        }
    }
    uint64_t frames = g_frames.load();
    int ch = g_ch.load(), rate = g_rate.load();
    return Py_BuildValue("(Kii)", (unsigned long long)frames,
                         ch, rate);
}

static PyObject* py_aircheck_levels(PyObject*, PyObject*) {
    std::lock_guard<std::mutex> lk(g_meter_mtx);
    PyObject* t = PyTuple_New(2);
    PyTuple_SET_ITEM(t, 0, PyFloat_FromDouble(g_peak[0]));
    PyTuple_SET_ITEM(t, 1, PyFloat_FromDouble(g_peak[1]));
    return t;
}

static PyObject* py_aircheck_state(PyObject*, PyObject*) {
    return PyLong_FromLong(g_state.load());
}

static PyObject* py_aircheck_error(PyObject*, PyObject*) {
    std::lock_guard<std::mutex> lk(g_err_mtx);
    if (g_last_error.empty()) Py_RETURN_NONE;
    return PyUnicode_FromString(g_last_error.c_str());
}

static PyMethodDef module_methods[] = {
    {"aircheck_start", py_aircheck_start, METH_VARARGS,
     "aircheck_start(path, endpoint='', loopback=False) — record WAV in C++"},
    {"aircheck_stop", py_aircheck_stop, METH_NOARGS,
     "aircheck_stop() -> (frames, channels, samplerate)"},
    {"aircheck_levels", py_aircheck_levels, METH_NOARGS,
     "aircheck_levels() -> (peak_l, peak_r) linear, decaying"},
    {"aircheck_state", py_aircheck_state, METH_NOARGS,
     "aircheck_state() -> 0 idle / 1 recording"},
    {"aircheck_error", py_aircheck_error, METH_NOARGS,
     "aircheck_error() -> last error string or None"},
    {nullptr, nullptr, 0, nullptr},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "native_aircheck",
    "Native WASAPI air-check recorder — WAV written entirely in C++.",
    -1,
    module_methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

extern "C" __declspec(dllexport) PyObject* PyInit_native_aircheck(void) {
    return PyModule_Create(&moduledef);
}
