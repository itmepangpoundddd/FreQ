// Native WASAPI render stream for FreQ.
//
// Decoded s16le stereo PCM (44.1 kHz) is pushed into a ring buffer and an
// event-driven WASAPI render thread drains it into the selected endpoint.
// Playback happens on the chosen device *only* — the OS default stays
// untouched (fixes machines where switching the default is impossible,
// e.g. stripped Windows builds) and gives lower latency than a media
// framework playing through the default device.
//
// The engine converts to the device mix format itself
// (AUDCLNT_STREAMFLAGS_AUTOCONVERT | SRC_DEFAULT_QUALITY), so the app can
// keep one canonical format everywhere.
//
// Threading: the render thread is the only reader of the ring; the Python
// caller is the only writer; both meet inside a CRITICAL_SECTION. Volume is
// applied while copying out, so it affects already-buffered audio instantly.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mmdeviceapi.h>
#include <audioclient.h>

#include <atomic>
#include <cstring>
#include <string>
#include <vector>

namespace {

std::wstring to_wide(const char* s) {
    if (!s || !*s) return std::wstring();
    const int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, nullptr, 0);
    std::wstring out(static_cast<size_t>(n > 0 ? n - 1 : 0), L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, s, -1, out.data(), n);
    return out;
}

// Canonical app format — matches pygame mixer init and the crossfade renderer.
constexpr WORD  kChannels = 2;
constexpr DWORD kSampleRate = 44100;

class RenderEngine {
public:
    RenderEngine() { InitializeCriticalSection(&lock_); }
    ~RenderEngine() {
        stop();
        DeleteCriticalSection(&lock_);
    }

    bool start(const wchar_t* endpoint_id) {
        stop();  // full teardown first; buffered audio is preserved
        paused_.store(false);  // a fresh stream is never paused
        shutdown_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        buffer_event_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!shutdown_event_ || !buffer_event_) return false;
        if (!init_device(endpoint_id)) {
            teardown_device();
            CloseHandle(shutdown_event_); shutdown_event_ = nullptr;
            CloseHandle(buffer_event_);   buffer_event_ = nullptr;
            return false;
        }
        running_.store(true);
        thread_ = CreateThread(nullptr, 0, thread_proc, this, 0, nullptr);
        if (!thread_) {
            running_.store(false);
            teardown_device();
            CloseHandle(shutdown_event_); shutdown_event_ = nullptr;
            CloseHandle(buffer_event_);   buffer_event_ = nullptr;
            return false;
        }
        return true;
    }

    // Stops the render thread and releases the device. Ring contents are
    // kept so start() on a new endpoint resumes the same audio.
    void stop() {
        running_.store(false);
        HANDLE thread = thread_;
        HANDLE shutdown = shutdown_event_;
        thread_ = nullptr;
        shutdown_event_ = nullptr;
        if (shutdown) SetEvent(shutdown);
        if (thread) {
            WaitForSingleObject(thread, 3000);
            CloseHandle(thread);
        }
        teardown_device();
        if (shutdown) CloseHandle(shutdown);
        if (buffer_event_) { CloseHandle(buffer_event_); buffer_event_ = nullptr; }
    }

    void clear() {
        EnterCriticalSection(&lock_);
        read_pos_ = write_pos_ = buffered_ = 0;
        frames_written_ = 0;
        LeaveCriticalSection(&lock_);
    }

    size_t write(const int16_t* src, size_t frames) {
        if (!frames) return 0;
        size_t written = 0;
        EnterCriticalSection(&lock_);
        const size_t free_frames = ring_capacity_ > buffered_
            ? ring_capacity_ - buffered_ : 0;
        const size_t n = frames < free_frames ? frames : free_frames;
        // Copy ALL n frames (n * kChannels samples). An earlier version
        // copied only n samples — silently dropping half of every chunk
        // and double-counting buffered_, which left the second half of
        // every device buffer stale: constant FM-static-like noise.
        const size_t samples = n * kChannels;
        for (size_t i = 0; i < samples; ++i) {
            ring_[write_pos_] = src[i];
            write_pos_ = (write_pos_ + 1) % ring_capacity_;
        }
        buffered_ += n;
        written = n;
        frames_written_ += n;
        LeaveCriticalSection(&lock_);
        if (buffer_event_) SetEvent(buffer_event_);
        return written;
    }

    size_t buffered() const {
        EnterCriticalSection(&lock_);
        const size_t b = buffered_;
        LeaveCriticalSection(&lock_);
        return b;
    }

    // Approximate frames already handed toward the device (excludes data
    // still queued in our ring and in the device buffer).
    long long emitted_frames() {
        EnterCriticalSection(&lock_);
        const long long emitted =
            static_cast<long long>(frames_written_ - buffered_);
        LeaveCriticalSection(&lock_);
        return emitted < 0 ? 0 : emitted;
    }

    void set_volume(float v) {
        if (v < 0.0f) v = 0.0f;
        if (v > 1.0f) v = 1.0f;
        volume_.store(v);
    }

    float volume() const { return volume_.load(); }

    // True pause: the device keeps playing SILENCE while the ring is left
    // untouched, so the stream stays alive, `emitted` freezes, and resume
    // continues exactly at the paused position. (Muting alone would keep
    // draining the ring — playback would advance silently while paused.)
    void set_paused(bool p) { paused_.store(p); }

    bool is_paused() const { return paused_.load(); }

    bool is_running() const { return running_.load() && render_ != nullptr; }

private:
    static DWORD WINAPI thread_proc(LPVOID param) {
        static_cast<RenderEngine*>(param)->render_loop();
        return 0;
    }

    void render_loop() {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        HANDLE wait[2] = { shutdown_event_, buffer_event_ };
        while (running_.load()) {
            const DWORD w = WaitForMultipleObjects(2, wait, FALSE, 500);
            if (w == WAIT_OBJECT_0 || !running_.load()) break;   // shutdown
            if (!render_) break;
            // Paused: hand the device silence instead of draining the ring.
            // emitted_frames() stays frozen because neither frames_written_
            // nor buffered_ changes while paused.
            if (paused_.load()) {
                UINT32 padding = 0;
                if (SUCCEEDED(client_->GetCurrentPadding(&padding))) {
                    const UINT32 space =
                        buffer_frames_ > padding ? buffer_frames_ - padding : 0;
                    if (space) {
                        BYTE* dst = nullptr;
                        if (SUCCEEDED(render_->GetBuffer(space, &dst)) && dst) {
                            std::memset(dst, 0,
                                        static_cast<size_t>(space) * kChannels * 2);
                            render_->ReleaseBuffer(space, 0);
                        }
                    }
                }
                continue;  // failures fall through to the next event wait
            }
            // Drain whatever fits into the device buffer.
            for (int pass = 0; pass < 8; ++pass) {
                UINT32 padding = 0;
                if (FAILED(client_->GetCurrentPadding(&padding))) break;
                UINT32 space = buffer_frames_ > padding ? buffer_frames_ - padding : 0;
                if (!space) break;
                EnterCriticalSection(&lock_);
                size_t avail = buffered_;
                if (avail > space) avail = space;
                if (!avail) { LeaveCriticalSection(&lock_); break; }
                BYTE* dst = nullptr;
                if (FAILED(render_->GetBuffer(static_cast<UINT32>(avail), &dst))) {
                    LeaveCriticalSection(&lock_);
                    break;
                }
                const float vol = volume_.load();
                // Fill ALL avail frames: avail * kChannels samples must be
                // copied, or the tail of every device buffer plays stale
                // garbage (constant FM-static noise).
                const size_t samples = avail * kChannels;
                const size_t first = samples < (ring_capacity_ - read_pos_)
                    ? samples : (ring_capacity_ - read_pos_);
                copy_scaled(dst, ring_.data() + read_pos_, first, vol);
                if (samples > first) {
                    copy_scaled(dst + first * 2,
                                ring_.data(), samples - first, vol);
                }
                read_pos_ = (read_pos_ + samples) % ring_capacity_;
                buffered_ -= avail;
                LeaveCriticalSection(&lock_);
                render_->ReleaseBuffer(static_cast<UINT32>(avail), 0);
                if (avail < space) break;   // ring drained
            }
        }
        CoUninitialize();
    }

    static void copy_scaled(BYTE* dst, const int16_t* src, size_t total_samples, float vol) {
        if (vol >= 0.999f) {
            std::memcpy(dst, src, total_samples * 2);
            return;
        }
        auto* out = reinterpret_cast<int16_t*>(dst);
        for (size_t i = 0; i < total_samples; ++i) {
            double v = static_cast<double>(src[i]) * vol;
            if (v > 32767.0) v = 32767.0;
            if (v < -32768.0) v = -32768.0;
            out[i] = static_cast<int16_t>(v);
        }
    }

    bool init_device(const wchar_t* endpoint_id) {
        // The calling (Python) thread needs COM for MMDevice calls. The
        // render thread initializes its own MTA. If the caller already has
        // a different apartment model, keep using it (RPC_E_CHANGED_MODE).
        const HRESULT ci = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        (void)ci;  // never uninitialized — process-lifetime is fine here
        IMMDeviceEnumerator* enumerator = nullptr;
        HRESULT hr = CoCreateInstance(
            __uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
            __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator));
        if (FAILED(hr)) return false;
        IMMDevice* device = nullptr;
        if (endpoint_id && *endpoint_id) {
            hr = enumerator->GetDevice(endpoint_id, &device);
        } else {
            hr = enumerator->GetDefaultAudioEndpoint(eRender, eMultimedia, &device);
        }
        enumerator->Release();
        if (FAILED(hr)) return false;
        hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                              reinterpret_cast<void**>(&client_));
        device->Release();
        if (FAILED(hr)) return false;

        WAVEFORMATEX fmt{};
        fmt.wFormatTag = WAVE_FORMAT_PCM;
        fmt.nChannels = kChannels;
        fmt.nSamplesPerSec = kSampleRate;
        fmt.wBitsPerSample = 16;
        fmt.nBlockAlign = fmt.nChannels * fmt.wBitsPerSample / 8;
        fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign;
        // Shared mode + AUTOCONVERT: the engine converts our canonical
        // s16/44.1k stream into the device mix format for us.
        constexpr DWORD kAutoConvert = 0x80000000;
        constexpr DWORD kSrcDefault = 0x08000000;
        constexpr REFERENCE_TIME kBuffer = 2000000;  // 200 ms (100 ns units)
        hr = client_->Initialize(
            AUDCLNT_SHAREMODE_SHARED,
            kAutoConvert | kSrcDefault | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
            kBuffer, 0, &fmt, nullptr);
        if (FAILED(hr)) { client_->Release(); client_ = nullptr; return false; }
        hr = client_->GetBufferSize(&buffer_frames_);
        if (FAILED(hr)) { client_->Release(); client_ = nullptr; return false; }
        hr = client_->GetService(__uuidof(IAudioRenderClient),
                                 reinterpret_cast<void**>(&render_));
        if (FAILED(hr)) { client_->Release(); client_ = nullptr; return false; }
        client_->SetEventHandle(buffer_event_);
        // Pre-fill with silence so the first writes are not glued to startup.
        BYTE* silence = nullptr;
        UINT32 prefill = buffer_frames_;
        if (SUCCEEDED(render_->GetBuffer(prefill, &silence)) && silence) {
            std::memset(silence, 0, static_cast<size_t>(prefill) * kChannels * 2);
            render_->ReleaseBuffer(prefill, 0);
        }
        if (FAILED(client_->Start())) {
            render_->Release(); render_ = nullptr;
            client_->Release(); client_ = nullptr;
            return false;
        }
        return true;
    }

    void teardown_device() {
        if (client_) { try { client_->Stop(); } catch (...) {} }
        if (render_) { render_->Release(); render_ = nullptr; }
        if (client_) { client_->Release(); client_ = nullptr; }
        buffer_frames_ = 0;
    }

    static constexpr size_t kRingFrames = kSampleRate * 2;  // ~2 s

    mutable CRITICAL_SECTION lock_{};
    std::vector<int16_t> ring_ = std::vector<int16_t>(kRingFrames * kChannels, 0);
    // Sample capacity of ring_ (write/read indices run over SAMPLES;
    // buffered_/frames_written_ and all device APIs run over FRAMES).
    size_t ring_capacity_ = kRingFrames * kChannels;
    size_t read_pos_ = 0, write_pos_ = 0, buffered_ = 0;
    size_t frames_written_ = 0;
    std::atomic<float> volume_{1.0f};
    std::atomic<bool> running_{false};
    std::atomic<bool> paused_{false};
    HANDLE thread_ = nullptr;
    HANDLE shutdown_event_ = nullptr;
    HANDLE buffer_event_ = nullptr;
    IAudioClient* client_ = nullptr;
    IAudioRenderClient* render_ = nullptr;
    UINT32 buffer_frames_ = 0;
};

PyObject* render_new(PyObject*, PyObject* args) {
    (void)args;
    auto* engine = new (std::nothrow) RenderEngine();
    if (!engine) PyErr_NoMemory();
    return PyCapsule_New(engine, "native_audio_render.Engine",
                         [](PyObject* cap) {
                             auto* e = static_cast<RenderEngine*>(
                                 PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
                             delete e;
                         });
}

RenderEngine* get_engine(PyObject* args) {
    PyObject* cap = nullptr;
    if (!PyArg_ParseTuple(args, "O", &cap)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) PyErr_SetString(PyExc_ValueError, "invalid engine handle");
    return e;
}

PyObject* render_start(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    const char* endpoint_id = nullptr;
    if (!PyArg_ParseTuple(args, "O|z", &cap, &endpoint_id)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    std::wstring id = to_wide(endpoint_id);
    const bool ok = e->start(id.empty() ? nullptr : id.c_str());
    if (!ok) {
        PyErr_SetString(PyExc_RuntimeError, "WASAPI render start failed");
        return nullptr;
    }
    Py_RETURN_TRUE;
}

PyObject* render_stop(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    e->stop();
    Py_RETURN_NONE;
}

PyObject* render_write(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    Py_buffer buf{};
    if (!PyArg_ParseTuple(args, "Oy*", &cap, &buf)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) { PyBuffer_Release(&buf); return nullptr; }
    const Py_ssize_t bytes = buf.len;
    const Py_ssize_t frames = bytes / 4;   // stereo s16le
    const size_t written = e->write(
        reinterpret_cast<const int16_t*>(buf.buf), static_cast<size_t>(frames));
    PyBuffer_Release(&buf);
    return PyLong_FromSize_t(written);
}

PyObject* render_buffered(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromSize_t(e->buffered());
}

PyObject* render_emitted(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLongLong(e->emitted_frames());
}

PyObject* render_set_volume(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    double v = 1.0;
    if (!PyArg_ParseTuple(args, "Od", &cap, &v)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->set_volume(static_cast<float>(v));
    Py_RETURN_NONE;
}

PyObject* render_get_volume(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyFloat_FromDouble(e->volume());
}

PyObject* render_clear(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    e->clear();
    Py_RETURN_NONE;
}

PyObject* render_pause(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int paused = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &paused)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) {
        PyErr_SetString(PyExc_ValueError, "invalid engine handle");
        return nullptr;
    }
    e->set_paused(paused != 0);
    Py_RETURN_NONE;
}

PyObject* render_is_running(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyBool_FromLong(e->is_running() ? 1 : 0);
}

PyMethodDef methods[] = {
    {"render_new", render_new, METH_NOARGS,
     "Create a WASAPI render engine handle."},
    {"render_start", render_start, METH_VARARGS,
     "Start rendering on the given endpoint id (None/'' = OS default)."},
    {"render_stop", render_stop, METH_VARARGS,
     "Stop the render thread; buffered audio is preserved."},
    {"render_write", render_write, METH_VARARGS,
     "Push s16le stereo PCM into the ring; returns frames accepted."},
    {"render_buffered", render_buffered, METH_VARARGS,
     "Frames currently buffered."},
    {"render_emitted", render_emitted, METH_VARARGS,
     "Frames handed to the device so far (rough playback position)."},
    {"render_set_volume", render_set_volume, METH_VARARGS,
     "Set output volume (0.0-1.0), applies to buffered audio too."},
    {"render_get_volume", render_get_volume, METH_VARARGS,
     "Return the current volume."},
    {"render_clear", render_clear, METH_VARARGS,
     "Drop all buffered audio."},
    {"render_pause", render_pause, METH_VARARGS,
     "Pause/resume: 1 = play silence and freeze the ring, 0 = resume."},
    {"render_is_running", render_is_running, METH_VARARGS,
     "True while the render thread is alive on a device."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "native_audio_render",
    "FreQ native WASAPI render stream.", -1, methods,
};
PyMODINIT_FUNC PyInit_native_audio_render() { return PyModule_Create(&module); }
