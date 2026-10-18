// Native live-stream sender for FreQ — the broadcast path in C++.
//
// Python hands over a destination (host, port, mount/user/pass or plain
// TCP), an Icecast-style source header, and an encoder command (ffmpeg).
// A dedicated C++ thread:
//   1. taps the render loopback (everything the app plays) as s16le PCM,
//   2. pipes it into the encoder's stdin,
//   3. connects the TCP sink and sends the source header once,
//   4. pumps encoder stdout → socket until error/stop.
// The audio bytes never detour through Python, and a reconnect (with the
// ladder delay the caller chose) happens inside the thread too.
//
// Design notes:
//  * ONE stream at a time (module-level state, mirroring native_aircheck).
//  * The encoder process and sockets are owned by the C++ side: stop()
//    terminates ffmpeg (taskkill-style TerminateProcess), closes sockets.
//  * No CRT-global winsock pollution: WSAStartup once at thread start,
//    WSACleanup at thread end. Sends use SO_SNDTIMEO (30 s) so a stalled
//    server surfaces as a reconnect, never a hang.
//  * Outcomes are atomics; Python polls stream_state()/stream_stats().
//    A dead engine (device gone) surfaces as state=Error with last_error.
//
// Build: added to SOURCES/STEMS in build_native_meter.py (ole32+avrt+ws2_32).
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <winsock2.h>
#include <ws2tcpip.h>

#include <mmdeviceapi.h>
#include <audioclient.h>
#include <avrt.h>

#include <atomic>
#include <cstring>
#include <mutex>
#include <string>
#include <vector>

namespace {

// states (match StreamState vibes in streaming.py)
constexpr int kIdle = 0, kConnecting = 1, kStreaming = 2,
              kReconnecting = 3, kStopped = 4, kError = 5;

std::atomic<int>  g_state{kIdle};
std::atomic<bool> g_running{false};
std::atomic<uint64_t> g_sent_bytes{0};
std::atomic<uint64_t> g_diag_pcm{0};    // bytes fed into ffmpeg stdin
std::atomic<uint64_t> g_diag_enc{0};    // bytes read from ffmpeg stdout
// where the pump currently sits: 0 idle, 1 capture-read, 2 pcm-write,
// 3 enc-peek, 4 enc-read, 5 send — pinpointing a wedged call
std::atomic<int> g_phase{0};
std::atomic<int>  g_attempts{0};
std::atomic<int>  g_rate{48000};
std::atomic<int>  g_ch{2};

HANDLE g_thread = nullptr;
std::string g_last_error;
std::mutex g_err_mtx;
std::string g_log;              // short ring of last events (lines)
std::mutex g_log_mtx;

struct SinkSpec {
    std::string host;
    int port;
    std::string header;         // full HTTP source request (CRLF-terminated)
    int reconnect_delay_ms;
    int max_attempts;           // 0 = unlimited while running
};

struct EncSpec {
    std::string command_line;   // full ffmpeg invocation (ANSI/UTF-8)
};

struct StreamSpec {
    SinkSpec sink;
    EncSpec enc;
    bool loopback;              // true = render loopback; false = default mic
    std::string endpoint;       // optional explicit endpoint id
};

static void SetError(const std::string& m) {
    std::lock_guard<std::mutex> lk(g_err_mtx);
    g_last_error = m;
}
static void LogLine(const std::string& m) {
    std::lock_guard<std::mutex> lk(g_log_mtx);
    if (g_log.size() > 8000) g_log.erase(0, 4000);
    g_log += m;
    g_log += "\n";
}

static std::wstring ToWide(const std::string& s) {
    int wlen = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w((size_t)wlen, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), wlen);
    if (!w.empty() && w.back() == L'\0') w.pop_back();
    return w;
}

// ── per-connection plumbing ───────────────────────────────────────────────
struct Connection {
    SOCKET sock = INVALID_SOCKET;
    HANDLE proc = nullptr;          // ffmpeg process
    HANDLE stdin_w = nullptr;       // write end of ffmpeg stdin
    HANDLE stdout_r = nullptr;      // read end of ffmpeg stdout
    bool header_sent = false;

    void close_all() {
        if (sock != INVALID_SOCKET) { closesocket(sock); sock = INVALID_SOCKET; }
        if (stdin_w) { CloseHandle(stdin_w); stdin_w = nullptr; }
        if (stdout_r) { CloseHandle(stdout_r); stdout_r = nullptr; }
        if (proc) { TerminateProcess(proc, 0); WaitForSingleObject(proc, 1500); CloseHandle(proc); proc = nullptr; }
        header_sent = false;
    }
};

static bool StartEncoder(const EncSpec& enc, Connection& c) {
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    HANDLE in_r = nullptr, out_w = nullptr;
    if (!CreatePipe(&in_r, &c.stdin_w, &sa, 0)) return false;
    SetHandleInformation(c.stdin_w, HANDLE_FLAG_INHERIT, 0);
    if (!CreatePipe(&c.stdout_r, &out_w, &sa, 0)) {
        CloseHandle(in_r); CloseHandle(c.stdin_w); c.stdin_w = nullptr;
        return false;
    }
    SetHandleInformation(c.stdout_r, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in_r;
    si.hStdOutput = out_w;
    // ffmpeg's chatty teardown errors must not pollute the app's stderr —
    // real failures surface via the process-exit check + stream state.
    HANDLE nul = CreateFileA("NUL", GENERIC_WRITE, FILE_SHARE_WRITE,
                             &sa, OPEN_EXISTING, 0, nullptr);
    si.hStdError = nul ? nul : GetStdHandle(STD_ERROR_HANDLE);
    PROCESS_INFORMATION pi{};
    std::vector<char> cmd(enc.command_line.begin(), enc.command_line.end());
    cmd.push_back('\0');
    BOOL ok = CreateProcessA(nullptr, cmd.data(), nullptr, nullptr, TRUE,
                             CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    CloseHandle(in_r);
    CloseHandle(out_w);
    if (nul) CloseHandle(nul);
    if (!ok) {
        c.close_all();
        return false;
    }
    CloseHandle(pi.hThread);
    c.proc = pi.hProcess;
    return true;
}

static bool ConnectSink(const SinkSpec& sink, Connection& c) {
    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return false;
    DWORD timeout = 8000;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, (const char*)&timeout, sizeof(timeout));
    timeout = 8000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));

    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    char port[8];
    _snprintf_s(port, sizeof(port), _TRUNCATE, "%d", sink.port);
    if (getaddrinfo(sink.host.c_str(), port, &hints, &res) != 0 || !res) {
        closesocket(s);
        return false;
    }
    bool connected = false;
    for (addrinfo* p = res; p; p = p->ai_next) {
        if (connect(s, p->ai_addr, (int)p->ai_addrlen) == 0) { connected = true; break; }
    }
    freeaddrinfo(res);
    if (!connected) { closesocket(s); return false; }

    // TCP_NODELAY: the encoder feeds small frames; latency beats throughput.
    int one = 1;
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, (const char*)&one, sizeof(one));

    int sent = 0, total = 0;
    const char* hdr = sink.header.c_str();
    int len = (int)sink.header.size();
    while (total < len) {
        sent = send(s, hdr + total, len - total, 0);
        if (sent <= 0) { closesocket(s); return false; }
        total += sent;
    }
    // Drain the server's response line (Icecast 200 / Shoutcast ok).
    char resp[512] = {0};
    recv(s, resp, sizeof(resp) - 1, 0);
    c.sock = s;
    c.header_sent = true;
    return true;
}

static bool SendAll(Connection& c, const uint8_t* p, size_t n) {
    size_t off = 0;
    while (off < n) {
        int s = send(c.sock, (const char*)p + off, (int)(n - off), 0);
        if (s <= 0) return false;
        off += (size_t)s;
        g_sent_bytes += (uint64_t)s;
    }
    return true;
}

// ── WASAPI loopback tap → encoder stdin, then stdout → socket ────────────
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
    out.resize(out.size() + (size_t)frames * ch * 2, 0);
}

// One full open of the capture device. Returns S_OK-shaped bool.
static bool OpenCapture(const StreamSpec& spec, IMMDeviceEnumerator*& en,
                        IMMDevice*& dev, IAudioClient*& client,
                        IAudioCaptureClient*& cap, WAVEFORMATEX*& fmt) {
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                (void**)&en)))
        return false;
    HRESULT hr;
    if (!spec.endpoint.empty()) {
        std::wstring wid = ToWide(spec.endpoint);
        hr = en->GetDevice(wid.c_str(), &dev);
    } else if (spec.loopback) {
        hr = en->GetDefaultAudioEndpoint(eRender, eConsole, &dev);
    } else {
        hr = en->GetDefaultAudioEndpoint(eCapture, eConsole, &dev);
    }
    if (FAILED(hr)) return false;
    hr = dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                       (void**)&client);
    if (FAILED(hr)) return false;
    if (FAILED(client->GetMixFormat(&fmt)) || !fmt) return false;
    hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                            spec.loopback ? AUDCLNT_STREAMFLAGS_LOOPBACK : 0,
                            200000, 0, fmt, nullptr);
    if (FAILED(hr)) return false;
    if (FAILED(client->GetService(__uuidof(IAudioCaptureClient), (void**)&cap)))
        return false;
    return SUCCEEDED(client->Start());
}

static void CloseCapture(IMMDeviceEnumerator*& en, IMMDevice*& dev,
                         IAudioClient*& client, IAudioCaptureClient*& cap,
                         WAVEFORMATEX*& fmt) {
    if (client) client->Stop();
    if (cap) cap->Release();
    if (client) client->Release();
    if (dev) dev->Release();
    if (en) en->Release();
    if (fmt) CoTaskMemFree(fmt);
    en = nullptr; dev = nullptr; client = nullptr; cap = nullptr; fmt = nullptr;
}

// ── stall monitor ─────────────────────────────────────────────────────────
// The pump thread can park FOREVER inside a blocking WriteFile(stdin):
// socket send buffer full → ffmpeg blocks writing stdout → ffmpeg stops
// reading stdin → our WriteFile never returns (the pump's own watchdog
// can't fire from inside the blocked call). A separate monitor cancels
// the pending I/O with CancelIoEx so the pump errors out and the
// reconnect ladder takes over.
struct StallCtx {
    Connection* conn;
    const std::atomic<DWORD>* last_data;
    const std::atomic<bool>* pump_alive;
    const std::atomic<bool>* running;
};

static DWORD WINAPI StallMonitor(LPVOID param) {
    StallCtx* ctx = (StallCtx*)param;
    while (ctx->running->load() && ctx->pump_alive->load()) {
        Sleep(500);
        if (!ctx->running->load() || !ctx->pump_alive->load()) break;
        if (GetTickCount() - ctx->last_data->load() > 8000) {
            static const char* kPhaseName[] = {
                "idle", "capture-read", "pcm-write(stdin)", "enc-peek",
                "enc-read(stdout)", "send(socket)" };
            int ph = g_phase.load();
            LogLine(std::string("stall watchdog fired in phase ")
                    + (ph >= 0 && ph <= 5 ? kPhaseName[ph] : "?")
                    + " — cancelling blocked I/O");
            SetError("stream stalled — forcing recovery");
            if (ctx->conn->stdin_w) CancelIoEx(ctx->conn->stdin_w, nullptr);
            if (ctx->conn->stdout_r) CancelIoEx(ctx->conn->stdout_r, nullptr);
            if (ctx->conn->sock != INVALID_SOCKET)
                CancelIoEx((HANDLE)ctx->conn->sock, nullptr);
            return 0;
        }
    }
    return 0;
}

static DWORD WINAPI StreamProc(LPVOID param) {
    StreamSpec* spec = (StreamSpec*)param;
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    WSADATA wsa;
    bool wsa_ok = (WSAStartup(MAKEWORD(2, 2), &wsa) == 0);

    const int max_attempts = spec->sink.max_attempts > 0
                                 ? spec->sink.max_attempts : 1 << 30;

    IMMDeviceEnumerator* en = nullptr;
    IMMDevice* dev = nullptr;
    IAudioClient* client = nullptr;
    IAudioCaptureClient* cap = nullptr;
    WAVEFORMATEX* fmt = nullptr;

    int attempt = 0;
    bool have_capture = false;
    Connection conn;

    while (g_running && attempt < max_attempts) {
        ++attempt;
        g_attempts = attempt;
        if (attempt > 1) {
            g_state = kReconnecting;
            LogLine("reconnect attempt " + std::to_string(attempt));
            // Sleep in small slices so stop() stays responsive.
            int waited = 0;
            while (g_running && waited < spec->sink.reconnect_delay_ms) {
                Sleep(50);
                waited += 50;
            }
            if (!g_running) break;
        }

        g_state = kConnecting;

        // (re)open capture only when we don't have a healthy one
        if (!have_capture) {
            CloseCapture(en, dev, client, cap, fmt);   // defensive
            if (!OpenCapture(*spec, en, dev, client, cap, fmt)) {
                SetError("cannot open capture device for streaming");
                LogLine("capture open failed");
                continue;   // ladder will retry
            }
            g_rate = fmt->nSamplesPerSec;
            g_ch = fmt->nChannels;
            have_capture = true;
        }

        conn.close_all();
        if (!StartEncoder(spec->enc, conn)) {
            SetError("cannot start encoder (ffmpeg not found or bad command)");
            LogLine("encoder start failed");
            continue;
        }
        if (!ConnectSink(spec->sink, conn)) {
            SetError("cannot connect to stream server");
            LogLine("sink connect failed");
            continue;
        }

        g_state = kStreaming;
        LogLine("streaming (attempt " + std::to_string(attempt) + ")");

        std::vector<uint8_t> pcm;
        std::vector<uint8_t> enc_out(64 * 1024);
        DWORD swing = 0;
        // Idle watchdog: a stale capture (device went idle after heavy
        // reopen churn) can stop delivering packets while everything
        // still "looks" fine — state=Streaming, no bytes. If NEITHER
        // side produces anything for 5 s, force the ladder to reopen.
        // In a real broadcast music/voice keeps encoder output flowing,
        // so this never fires there. (A pump blocked inside a sync
        // WriteFile is covered by the separate stall monitor below —
        // this in-thread check can't fire from inside a blocked call.)
        std::atomic<DWORD> last_data{GetTickCount()};
        std::atomic<bool> pump_alive{true};
        StallCtx mon_ctx{&conn, &last_data, &pump_alive, &g_running};
        HANDLE mon_thread = CreateThread(nullptr, 0, StallMonitor,
                                         &mon_ctx, 0, nullptr);

        // Pump: capture → encoder stdin; encoder stdout → socket.
        while (g_running) {
            // 1) feed the encoder from WASAPI (non-blocking poll)
            UINT32 packet = 0;
            bool stdin_dead = false;
            while (g_running &&
                   cap->GetNextPacketSize(&packet) == S_OK && packet > 0) {
                g_phase = 1;
                BYTE* data = nullptr;
                UINT32 frames = 0;
                DWORD flags = 0;
                if (cap->GetBuffer(&data, &frames, &flags, nullptr, nullptr) != S_OK)
                    break;
                pcm.clear();
                if (data && !(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                    ConvertToS16(data, fmt, frames, pcm);
                else
                    pcm.assign((size_t)frames * fmt->nChannels * 2, 0);
                cap->ReleaseBuffer(frames);
                if (!pcm.empty()) {
                    g_phase = 2;
                    DWORD written = 0;
                    if (!WriteFile(conn.stdin_w, pcm.data(),
                                   (DWORD)pcm.size(), &written, nullptr) ||
                        written != pcm.size()) {
                        SetError("encoder closed its stdin");
                        stdin_dead = true;
                        break;
                    }
                    last_data = GetTickCount();
                    g_diag_pcm += written;
                }
            }
            if (stdin_dead) break;
            if (!g_running) break;

            // 2) encoder stdout → socket
            g_phase = 3;
            DWORD avail = 0;
            if (!PeekNamedPipe(conn.stdout_r, nullptr, 0, nullptr, &avail, nullptr)) {
                SetError("encoder died");
                break;
            }
            if (avail > 0) {
                g_phase = 4;
                DWORD to_read = avail < enc_out.size() ? avail : (DWORD)enc_out.size();
                DWORD got = 0;
                if (!ReadFile(conn.stdout_r, enc_out.data(), to_read, &got, nullptr) ||
                    got == 0) {
                    SetError("encoder read failed");
                    break;
                }
                if (!SendAll(conn, enc_out.data(), got)) {
                    SetError("stream server send failed");
                    break;
                }
                g_phase = 5;
                last_data = GetTickCount();
                g_diag_enc += got;
            } else {
                if (++swing >= 120) { swing = 0; Sleep(1); }  // yield when idle
            }

            // dead server that never errors send() but stalls? The SO_SNDTIMEO
            // makes SendAll fail; encoder-death is caught above.
            DWORD code = 0;
            if (conn.proc &&
                WaitForSingleObject(conn.proc, 0) == WAIT_OBJECT_0) {
                SetError("encoder process exited unexpectedly");
                break;
            }
            if (GetTickCount() - last_data.load() > 5000) {
                SetError("capture idle — reopening stream");
                LogLine("idle watchdog fired (no data 5 s)");
                break;
            }
        }
        pump_alive = false;
        if (mon_thread) {
            WaitForSingleObject(mon_thread, 3000);
            CloseHandle(mon_thread);
            mon_thread = nullptr;
        }

        // drop this connection; ladder decides what's next
        conn.close_all();
        CloseCapture(en, dev, client, cap, fmt);
        have_capture = false;
        last_data.store(GetTickCount());   // fresh attempt starts fresh
    }

    conn.close_all();
    CloseCapture(en, dev, client, cap, fmt);
    if (wsa_ok) WSACleanup();
    g_state = g_running.load() ? kError : kStopped;
    if (!g_running.load()) LogLine("stopped by caller");
    delete spec;
    return 0;
}

// ── Python API ────────────────────────────────────────────────────────────
static PyObject* py_stream_start(PyObject*, PyObject* args) {
    const char* host = nullptr;
    int port = 8000;
    const char* header = nullptr;
    const char* encoder = nullptr;
    int reconnect_delay_ms = 2000;
    int max_attempts = 0;
    int loopback = 1;
    const char* endpoint = "";
    if (!PyArg_ParseTuple(args, "siss|iisp", &host, &port, &header,
                          &encoder, &reconnect_delay_ms, &max_attempts,
                          &loopback, &endpoint))
        return nullptr;
    if (!header || !header[0]) {
        PyErr_SetString(PyExc_ValueError, "missing source header");
        return nullptr;
    }
    if (!encoder || !encoder[0]) {
        PyErr_SetString(PyExc_ValueError, "missing encoder command");
        return nullptr;
    }
    bool expected = false;
    int cur = kIdle;
    // accept start from Idle or after a previous Stopped/Error
    if (!g_state.compare_exchange_strong(cur, kConnecting) &&
        !(cur == kStopped || cur == kError)) {
        PyErr_SetString(PyExc_RuntimeError, "stream is already active");
        return nullptr;
    }
    g_state = kConnecting;
    if (g_thread) { CloseHandle(g_thread); g_thread = nullptr; }
    g_sent_bytes = 0;
    g_diag_pcm = 0;
    g_diag_enc = 0;
    g_attempts = 0;
    { std::lock_guard<std::mutex> lk(g_err_mtx); g_last_error.clear(); }
    { std::lock_guard<std::mutex> lk(g_log_mtx); g_log.clear(); }

    StreamSpec* spec = new StreamSpec{
        SinkSpec{host, port, header, reconnect_delay_ms, max_attempts},
        EncSpec{encoder},
        loopback != 0,
        endpoint,
    };
    g_running = true;
    g_thread = CreateThread(nullptr, 0, StreamProc, spec, 0, nullptr);
    if (!g_thread) {
        g_running = false;
        g_state = kIdle;
        delete spec;
        PyErr_SetString(PyExc_RuntimeError, "cannot start stream thread");
        return nullptr;
    }
    Py_RETURN_NONE;
}

static PyObject* py_stream_stop(PyObject*, PyObject*) {
    if (g_running.load()) {
        g_running = false;
        if (g_thread) {
            if (WaitForSingleObject(g_thread, 3000) != WAIT_OBJECT_0) {
                // Wedged inside an uncancelable call (WASAPI COM, pipe or
                // socket I/O the monitor could not abort). The thread is
                // inert: nothing it references is reachable from Python
                // anymore, so LEAK it and let the show go on — a fresh
                // stream thread creates brand-new COM objects and sockets.
                // (Locking the whole module like the recorder does would
                // cost a broadcast over one bad device moment.)
                LogLine("thread wedged — abandoned (leaked), stream reset");
                CloseHandle(g_thread);   // safe: closing a handle ≠ killing
                g_thread = nullptr;
                g_state = kIdle;
                Py_RETURN_NONE;
            }
            CloseHandle(g_thread);
            g_thread = nullptr;
        }
    }
    g_state = kIdle;
    Py_RETURN_NONE;
}

static PyObject* py_stream_state(PyObject*, PyObject*) {
    return PyLong_FromLong(g_state.load());
}

static PyObject* py_stream_stats(PyObject*, PyObject*) {
    std::string err;
    { std::lock_guard<std::mutex> lk(g_err_mtx); err = g_last_error; }
    return Py_BuildValue("(KKis)",
                         (unsigned long long)g_sent_bytes.load(),
                         (long long)g_attempts.load(),
                         g_rate.load(), err.c_str());
}

static PyObject* py_stream_diag(PyObject*, PyObject*) {
    return Py_BuildValue("(KKki)",
                         (unsigned long long)g_diag_pcm.load(),
                         (unsigned long long)g_diag_enc.load(),
                         (unsigned long)g_state.load(),
                         g_phase.load());
}

static PyObject* py_stream_log(PyObject*, PyObject*) {
    std::lock_guard<std::mutex> lk(g_log_mtx);
    return PyUnicode_FromString(g_log.c_str());
}

static PyMethodDef module_methods[] = {
    {"stream_start", py_stream_start, METH_VARARGS,
     "stream_start(host, port, source_header, encoder_cmd, "
     "reconnect_delay_ms=2000, max_attempts=0, loopback=True, endpoint='')"},
    {"stream_stop", py_stream_stop, METH_NOARGS, "stop the stream thread"},
    {"stream_state", py_stream_state, METH_NOARGS,
     "0 idle / 1 connecting / 2 streaming / 3 reconnecting / 4 stopped / 5 error"},
    {"stream_stats", py_stream_stats, METH_NOARGS,
     "(sent_bytes, attempts, samplerate, last_error)"},
    {"stream_diag", py_stream_diag, METH_NOARGS,
     "(pcm_bytes_into_encoder, encoded_bytes_from_encoder, state) — diagnostics"},
    {"stream_log", py_stream_log, METH_NOARGS, "recent thread log lines"},
    {nullptr, nullptr, 0, nullptr},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "native_stream",
    "Native WASAPI loopback → ffmpeg → Icecast/SHOUTcast sender for FreQ.",
    -1,
    module_methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

extern "C" __declspec(dllexport) PyObject* PyInit_native_stream(void) {
    return PyModule_Create(&moduledef);
}
