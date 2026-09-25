// Native WASAPI capture for FreQ (Air-Check Recorder base).
//
// Records from a microphone endpoint OR from the render loopback (everything
// the machine plays) as s16le PCM into an internal ring buffer that Python
// pulls with capture_read(). A live peak meter (per channel, dBFS) is
// updated on the capture thread so the UI can poll it without extra work.
// Polling mode (GetCurrentPadding + GetBuffer) is used for both capture
// types: event-driven loopback capture is unreliable across devices, while
// polling is robust and plenty accurate for recording.
//
// Build with build_native_meter.bat native_audio_capture
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mmdeviceapi.h>
#include <audioclient.h>
#include <avrt.h>
#include <functiondiscoverykeys_devpkey.h>

#include <cmath>
#include <cstring>
#include <mutex>
#include <new>
#include <string>
#include <vector>

namespace {

// ── Ring buffer (bytes, s16le interleaved) ────────────────────────────────
struct Ring {
    std::vector<uint8_t> buf;
    size_t head = 0, tail = 0, used = 0;
    std::mutex mtx;

    void init(size_t bytes) {
        buf.assign(bytes, 0);
        head = tail = used = 0;
    }
    size_t write(const uint8_t* src, size_t n) {
        std::lock_guard<std::mutex> lk(mtx);
        size_t written = 0;
        while (written < n && used < buf.size()) {
            size_t space = buf.size() - used;
            size_t tail_room = buf.size() - head;
            size_t chunk = n - written < space ? n - written : space;
            if (chunk > tail_room) chunk = tail_room;
            std::memcpy(buf.data() + head, src + written, chunk);
            head = (head + chunk) % buf.size();
            used += chunk;
            written += chunk;
        }
        return written;
    }
    size_t read(uint8_t* dst, size_t n) {
        std::lock_guard<std::mutex> lk(mtx);
        size_t got = 0;
        while (got < n && used > 0) {
            size_t chunk = n - got;
            if (chunk > used) chunk = used;
            size_t tail_room = buf.size() - tail;
            if (chunk > tail_room) chunk = tail_room;
            if (dst) std::memcpy(dst + got, buf.data() + tail, chunk);
            tail = (tail + chunk) % buf.size();
            used -= chunk;
            got += chunk;
        }
        return got;
    }
};

// ── Capture state ─────────────────────────────────────────────────────────
typedef struct {
    PyObject_HEAD
    IMMDeviceEnumerator* enumerator;
    IMMDevice* device;
    IAudioClient* client;
    IAudioCaptureClient* capture;
    HANDLE thread;
    volatile LONG running;
    volatile LONG paused;
    volatile LONG zombie;      // 1 = its thread outlived a stop join — leaked

    Ring ring;
    int rate;
    int channels;
    int bytes_per_frame_out;   // s16le * channels

    // live meter (peak per channel, linear 0..~1.7, atomic-ish via lock)
    std::mutex meter_mtx;
    float peak[2];

    bool loopback;
    std::string last_error;
} CaptureObject;

static void ConvertToS16(const BYTE* src, WAVEFORMATEX* fmt, UINT32 frames,
                         std::vector<uint8_t>& out) {
    const int ch = fmt->nChannels;
    if (fmt->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        auto* ext = (WAVEFORMATEXTENSIBLE*)fmt;
        if (ext->SubFormat.Data1 == 3 /* KSDATAFORMAT_SUBTYPE_IEEE_FLOAT */) {
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
            v <<= 8; v >>= 8;  // sign-extend
            int16_t s = (int16_t)(v >> 8);
            out.push_back((uint8_t)(s & 0xFF));
            out.push_back((uint8_t)((s >> 8) & 0xFF));
        }
        return;
    }
    // Unknown format: silence of the right size (should not happen).
    out.resize(out.size() + (size_t)frames * ch * 2, 0);
}

static void UpdateMeter(const int16_t* samples, size_t count, int ch,
                        CaptureObject* c) {
    float pk[2] = {0.f, 0.f};
    const int mch = ch >= 2 ? 2 : ch;
    for (size_t i = 0; i + (size_t)ch <= count; i += (size_t)ch) {
        for (int k = 0; k < mch; ++k) {
            float v = std::fabs(samples[i + k] / 32768.0f);
            if (v > pk[k]) pk[k] = v;
        }
    }
    std::lock_guard<std::mutex> lk(c->meter_mtx);
    for (int k = 0; k < mch; ++k)
        if (pk[k] > c->peak[k]) c->peak[k] = pk[k];
}

static void SetPeakDecay(CaptureObject* c) {
    std::lock_guard<std::mutex> lk(c->meter_mtx);
    for (int k = 0; k < 2; ++k) c->peak[k] *= 0.72f;   // gentle decay per poll
}

static DWORD WINAPI CaptureThread(LPVOID param) {
    CaptureObject* c = (CaptureObject*)param;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    WAVEFORMATEX* fmt = nullptr;
    if (FAILED(c->client->GetMixFormat(&fmt)) || !fmt) {
        c->running = 0;
        return 0;
    }
    if (FAILED(c->client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                                     c->loopback ? AUDCLNT_STREAMFLAGS_LOOPBACK : 0,
                                     200000 /*20 ms*/, 0, fmt, nullptr))) {
        CoTaskMemFree(fmt);
        c->running = 0;
        return 0;
    }
    if (FAILED(c->client->GetService(__uuidof(IAudioCaptureClient),
                                     (void**)&c->capture))) {
        CoTaskMemFree(fmt);
        c->running = 0;
        return 0;
    }
    if (FAILED(c->client->Start())) {
        CoTaskMemFree(fmt);
        c->running = 0;
        return 0;
    }

    const int ch = fmt->nChannels;
    std::vector<uint8_t> conv;
    conv.reserve(44100 * ch * 2);

    while (c->running) {
        Sleep(8);   // poll cadence (~20 ms device buffer, well under)
        if (c->paused) { SetPeakDecay(c); continue; }
        UINT32 packet = 0;
        while (c->capture->GetNextPacketSize(&packet) == S_OK && packet > 0
               && c->running && !c->paused) {
            BYTE* data = nullptr;
            UINT32 frames = 0;
            DWORD flags = 0;
            if (c->capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr) != S_OK)
                break;
            conv.clear();
            if (data && !(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                ConvertToS16(data, fmt, frames, conv);
            else
                conv.assign((size_t)frames * ch * 2, 0);
            if (!conv.empty()) {
                c->ring.write(conv.data(), conv.size());
                UpdateMeter((const int16_t*)conv.data(), conv.size() / 2, ch, c);
            }
            c->capture->ReleaseBuffer(frames);
        }
        SetPeakDecay(c);
    }
    c->client->Stop();
    if (c->capture) { c->capture->Release(); c->capture = nullptr; }
    CoTaskMemFree(fmt);
    return 0;
}

static void Capture_dealloc(PyObject* self) {
    CaptureObject* c = (CaptureObject*)self;
    if (c->zombie) {
        // The capture thread outlived a stop join and is still parked inside
        // a WASAPI call (audio service restart, driver reset). Freeing the
        // object, its COM refs or its thread handle under it is a
        // use-after-free — leak the whole thing instead; the thread exits on
        // its next loop check and everything is reclaimed by the OS at exit.
        return;
    }
    if (c->running) {
        c->running = 0;
        if (c->thread) {
            if (WaitForSingleObject(c->thread, 2000) != WAIT_OBJECT_0) {
                c->zombie = 1;
                return;   // leak: the stuck thread still owns everything
            }
            CloseHandle(c->thread);
            c->thread = nullptr;
        }
    }
    if (c->client) c->client->Release();
    if (c->device) c->device->Release();
    if (c->enumerator) c->enumerator->Release();
    // Explicitly destroy the C++ members (the ring buffer is ~960 KB and
    // previously leaked on every cycle). The PyObject header is trivial —
    // destroying only the members keeps the teardown symmetric with the
    // member-wise construction in Capture_new_impl.
    c->ring.~Ring();
    c->meter_mtx.~mutex();
    c->last_error.~basic_string();
    PyObject_Free(self);
}

static PyObject* Capture_new_impl(PyTypeObject* type, PyObject* args,
                                  PyObject* kwargs) {
    const char* endpoint = nullptr;
    int loopback = 0;
    static const char* kwlist[] = {"endpoint", "loopback", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|sp", const_cast<char**>(kwlist),
                                     &endpoint, &loopback)) {
        return nullptr;
    }
    CaptureObject* c = (CaptureObject*)type->tp_alloc(type, 0);
    if (!c) return nullptr;
    // tp_alloc only zero-fills raw bytes: C++ members (Ring's vector,
    // mutexes, std::string) need their constructors RUN. Missing this
    // leaked the ~960 KB ring buffer on every dealloc (PyObject_Free
    // never runs member destructors either — see the matching ~c below).
    // Construct ONLY the C++ members in place — touching the PyObject
    // header itself would clobber ob_refcnt/ob_type.
    new (&c->ring) Ring();
    new (&c->meter_mtx) std::mutex();
    new (&c->last_error) std::string();
    c->enumerator = nullptr; c->device = nullptr; c->client = nullptr;
    c->capture = nullptr; c->thread = nullptr;
    c->running = 0; c->paused = 0; c->zombie = 0;
    c->peak[0] = c->peak[1] = 0.f;
    c->rate = 48000; c->channels = 2; c->bytes_per_frame_out = 4;
    c->loopback = loopback != 0;

    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                  CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                  (void**)&c->enumerator);
    if (FAILED(hr)) {
        Py_DECREF(c);
        PyErr_SetString(PyExc_RuntimeError, "cannot create MMDeviceEnumerator");
        return nullptr;
    }
    if (endpoint && endpoint[0]) {
        // Convert narrow endpoint id (UTF-8) to wide.
        int wlen = MultiByteToWideChar(CP_UTF8, 0, endpoint, -1, nullptr, 0);
        std::vector<wchar_t> wid((size_t)wlen);
        MultiByteToWideChar(CP_UTF8, 0, endpoint, -1, wid.data(), wlen);
        hr = c->enumerator->GetDevice(wid.data(), &c->device);
    } else if (loopback) {
        hr = c->enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &c->device);
    } else {
        hr = c->enumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &c->device);
    }
    if (FAILED(hr)) {
        Py_DECREF(c);
        PyErr_SetString(PyExc_RuntimeError, "capture endpoint not found");
        return nullptr;
    }
    hr = c->device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                             (void**)&c->client);
    if (FAILED(hr)) {
        Py_DECREF(c);
        PyErr_SetString(PyExc_RuntimeError, "cannot activate capture client");
        return nullptr;
    }
    WAVEFORMATEX* fmt = nullptr;
    if (FAILED(c->client->GetMixFormat(&fmt)) || !fmt) {
        Py_DECREF(c);
        PyErr_SetString(PyExc_RuntimeError, "cannot get mix format");
        return nullptr;
    }
    c->rate = fmt->nSamplesPerSec;
    c->channels = fmt->nChannels;
    c->bytes_per_frame_out = c->channels * 2;
    CoTaskMemFree(fmt);

    c->ring.init((size_t)c->rate * (size_t)c->bytes_per_frame_out * 5);  // 5 s
    return (PyObject*)c;
}

static PyObject* Capture_start(CaptureObject* c, PyObject*) {
    if (c->zombie) {
        PyErr_SetString(PyExc_RuntimeError,
                        "capture thread leaked (stuck device) — recreate the recorder");
        return nullptr;
    }
    if (c->running) Py_RETURN_NONE;
    if (c->thread) { CloseHandle(c->thread); c->thread = nullptr; }
    c->paused = 0;
    c->running = 1;
    c->thread = CreateThread(nullptr, 0, CaptureThread, c, 0, nullptr);
    if (!c->thread) {
        c->running = 0;
        PyErr_SetString(PyExc_RuntimeError, "cannot start capture thread");
        return nullptr;
    }
    Py_RETURN_NONE;
}

static PyObject* Capture_stop(CaptureObject* c, PyObject*) {
    if (c->running) {
        c->running = 0;
        if (c->thread) {
            // Same contract as dealloc: a thread parked in WASAPI must never
            // have its handle closed or its object freed under it.
            if (WaitForSingleObject(c->thread, 2000) != WAIT_OBJECT_0) {
                c->zombie = 1;   // leak handle + object; never touch again
                Py_RETURN_NONE;
            }
            CloseHandle(c->thread);
            c->thread = nullptr;
        }
    }
    Py_RETURN_NONE;
}

static PyObject* Capture_pause(CaptureObject* c, PyObject* args) {
    int p = 1;
    if (!PyArg_ParseTuple(args, "|p", &p)) return nullptr;
    c->paused = p ? 1 : 0;
    Py_RETURN_NONE;
}

static PyObject* Capture_read(CaptureObject* c, PyObject* args) {
    int max_bytes = 48000 * 4;   // ~0.25 s at 48k stereo
    if (!PyArg_ParseTuple(args, "|i", &max_bytes)) return nullptr;
    if (max_bytes <= 0) max_bytes = 4096;
    std::vector<uint8_t> out((size_t)max_bytes);
    size_t got = c->ring.read(out.data(), out.size());
    if (got == 0) Py_RETURN_NONE;
    return PyBytes_FromStringAndSize((const char*)out.data(), (Py_ssize_t)got);
}

static PyObject* Capture_levels(CaptureObject* c, PyObject*) {
    std::lock_guard<std::mutex> lk(c->meter_mtx);
    PyObject* t = PyTuple_New(2);
    PyTuple_SET_ITEM(t, 0, PyFloat_FromDouble(c->peak[0]));
    PyTuple_SET_ITEM(t, 1, PyFloat_FromDouble(c->peak[1]));
    return t;
}

static PyObject* Capture_buffered(CaptureObject* c, PyObject*) {
    std::lock_guard<std::mutex> lk(c->ring.mtx);
    size_t frames = c->ring.used / (size_t)c->bytes_per_frame_out;
    return PyFloat_FromDouble((double)frames);
}

static PyObject* Capture_clear(CaptureObject* c, PyObject*) {
    std::lock_guard<std::mutex> lk(c->ring.mtx);
    c->ring.tail = c->ring.head = c->ring.used = 0;
    Py_RETURN_NONE;
}

static PyObject* Capture_get_rate(CaptureObject* c, void*) {
    return PyLong_FromLong(c->rate);
}
static PyObject* Capture_get_ch(CaptureObject* c, void*) {
    return PyLong_FromLong(c->channels);
}

static PyMethodDef Capture_methods[] = {
    {"start",  (PyCFunction)Capture_start,  METH_NOARGS, "start capture thread"},
    {"stop",   (PyCFunction)Capture_stop,   METH_NOARGS, "stop capture thread"},
    {"pause",  (PyCFunction)Capture_pause,  METH_VARARGS, "pause(True/False)"},
    {"read",   (PyCFunction)Capture_read,   METH_VARARGS, "read(max_bytes) -> bytes | None"},
    {"levels", (PyCFunction)Capture_levels, METH_NOARGS, "(peak_l, peak_r) linear, decaying"},
    {"buffered", (PyCFunction)Capture_buffered, METH_NOARGS, "buffered frames"},
    {"clear",  (PyCFunction)Capture_clear,  METH_NOARGS, "drop buffered audio"},
    {nullptr, nullptr, 0, nullptr},
};

static PyGetSetDef Capture_getset[] = {
    {"samplerate", (getter)Capture_get_rate, nullptr, "device sample rate", nullptr},
    {"channels",   (getter)Capture_get_ch,   nullptr, "device channels", nullptr},
    {nullptr, nullptr, 0, nullptr, nullptr},
};

static PyType_Slot Capture_slots[] = {
    {Py_tp_dealloc, (void*)Capture_dealloc},
    {Py_tp_methods, (void*)Capture_methods},
    {Py_tp_getset,  (void*)Capture_getset},
    {Py_tp_doc,     (void*)"WASAPI mic/loopback capture with live peak meter"},
    {Py_tp_new,     (void*)Capture_new_impl},
    {0, nullptr},
};

static PyType_Spec Capture_spec = {
    "native_audio_capture.Capture",
    sizeof(CaptureObject),
    0,
    Py_TPFLAGS_DEFAULT,
    Capture_slots,
};

static PyObject* g_CaptureType = nullptr;

static PyObject* py_enum_capture(PyObject*, PyObject*) {
    // list capture/loopback endpoints as (id, name, is_loopback_candidate)
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IMMDeviceEnumerator* en = nullptr;
    PyObject* out = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                (void**)&en)))
        return PyList_New(0);
    IMMDeviceCollection* col = nullptr;
    if (SUCCEEDED(en->EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, &col))) {
        UINT n = 0;
        col->GetCount(&n);
        out = PyList_New(0);
        for (UINT i = 0; i < n; ++i) {
            IMMDevice* dev = nullptr;
            if (FAILED(col->Item(i, &dev))) continue;
            LPWSTR id = nullptr;
            IPropertyStore* ps = nullptr;
            PyObject* tup = nullptr;
            if (SUCCEEDED(dev->GetId(&id)) && SUCCEEDED(dev->OpenPropertyStore(STGM_READ, &ps))) {
                PROPVARIANT v;
                PropVariantInit(&v);
                if (SUCCEEDED(ps->GetValue(PKEY_Device_FriendlyName, &v))) {
                    wchar_t* wid = nullptr;
                    if (SUCCEEDED(dev->GetId(&wid)) && wid) CoTaskMemFree(wid);
                    int wlen = lstrlenW(id);
                    int ulen = WideCharToMultiByte(CP_UTF8, 0, id, wlen, nullptr, 0, nullptr, nullptr);
                    std::string id8((size_t)ulen, 0);
                    WideCharToMultiByte(CP_UTF8, 0, id, wlen, id8.data(), ulen, nullptr, nullptr);
                    int nlen = lstrlenW(v.pwszVal);
                    int nlen8 = WideCharToMultiByte(CP_UTF8, 0, v.pwszVal, nlen, nullptr, 0, nullptr, nullptr);
                    std::string name8((size_t)nlen8, 0);
                    WideCharToMultiByte(CP_UTF8, 0, v.pwszVal, nlen, name8.data(), nlen8, nullptr, nullptr);
                    PropVariantClear(&v);
                    tup = Py_BuildValue("(ssO)", id8.c_str(), name8.c_str(), Py_False);
                }
                ps->Release();
            }
            if (id) CoTaskMemFree(id);
            if (tup) { PyList_Append(out, tup); Py_DECREF(tup); }
            dev->Release();
        }
        col->Release();
    }
    en->Release();
    if (!out) out = PyList_New(0);
    return out;
}

static PyMethodDef module_methods[] = {
    {"enum_microphones", (PyCFunction)py_enum_capture, METH_NOARGS,
     "enum_microphones() -> [(endpoint_id, name, False)]"},
    {nullptr, nullptr, 0, nullptr},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "native_audio_capture",
    "WASAPI microphone/loopback capture with a live peak meter for FreQ.",
    -1,
    module_methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

extern "C" __declspec(dllexport) PyObject* PyInit_native_audio_capture(void) {
    g_CaptureType = PyType_FromSpec(&Capture_spec);
    if (!g_CaptureType) return nullptr;
    PyObject* m = PyModule_Create(&moduledef);
    if (!m) { Py_DECREF(g_CaptureType); return nullptr; }
    if (PyModule_AddObject(m, "Capture", g_CaptureType) < 0) {
        Py_DECREF(g_CaptureType);
        Py_DECREF(m);
        return nullptr;
    }
    return m;
}
