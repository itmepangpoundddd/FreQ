// Native stream pump for FreQ — moves the Icecast send loop into C++.
//
// Python hands over an OS pipe HANDLE (the encoder's stdout) and a TCP
// socket FD. A dedicated C++ thread reads s16le PCM from the pipe and
// send()s it to the socket until EOF or error, so the audio bytes never
// detour through a Python loop (read → Python → sendall).
//
// Design notes:
//  * The pipe HANDLE and the socket are OWNED BY PYTHON. The thread never
//    closes either — stop() only ends the loop (owned: thread, event).
//  * send() blocks only up to the socket's SO_SNDTIMEO (Python sets it,
//    e.g. 30 s) — a stalled server surfaces as an error, not a hang.
//  * All outcomes are reported via an atomic state; Python polls
//    stream_pump_state() and logs through its normal channel.
//
// Build: added to SOURCES/STEMS in build_native_meter.py (ws2_32 linked).
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <winsock2.h>
#include <ws2tcpip.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace {

constexpr int kChunkBytes = 16384;  // 8192 frames of s16le stereo

enum class PumpState : long { Idle = 0, Running = 1, Eof = 2, SendError = 3 };

std::atomic<PumpState> g_state{PumpState::Idle};
std::atomic<bool>      g_running{false};
std::atomic<uint64_t>  g_sent_bytes{0};

HANDLE g_thread = nullptr;
HANDLE g_stop_event = nullptr;
HANDLE g_pipe = nullptr;
SOCKET g_sock = INVALID_SOCKET;
std::string g_header;            // sent once before PCM (e.g. Icecast HTTP)
CRITICAL_SECTION g_lock;
bool g_lock_ready = false;

struct Snapshot {
    HANDLE pipe;
    SOCKET sock;
    std::string header;
};

Snapshot capture() {
    EnterCriticalSection(&g_lock);
    Snapshot s{g_pipe, g_sock, g_header};
    LeaveCriticalSection(&g_lock);
    return s;
}

DWORD WINAPI pump_proc(LPVOID) {
    const Snapshot snap = capture();
    std::vector<char> buf(kChunkBytes);

    // Send the app header (e.g. the Icecast HTTP request) first.
    if (!snap.header.empty()) {
        int off = 0;
        const int total = (int)snap.header.size();
        while (off < total && g_running.load()) {
            const int n = ::send(snap.sock, snap.header.data() + off,
                                 total - off, 0);
            if (n == SOCKET_ERROR) {
                g_state.store(PumpState::SendError);
                g_running.store(false);
                return 0;
            }
            off += n;
        }
        g_sent_bytes.fetch_add((uint64_t)off);
    }

    while (g_running.load()) {
        DWORD r = 0;
        if (!ReadFile(snap.pipe, buf.data(), (DWORD)buf.size(), &r, nullptr)
                || r == 0) {
            g_state.store(PumpState::Eof);   // encoder closed — normal end
            break;
        }
        int off = 0;
        while (off < (int)r && g_running.load()) {
            const int n = ::send(snap.sock, buf.data() + off, (int)r - off, 0);
            if (n == SOCKET_ERROR) {
                g_state.store(PumpState::SendError);
                g_running.store(false);
                return 0;
            }
            off += n;
        }
        g_sent_bytes.fetch_add((uint64_t)r);
    }
    g_running.store(false);
    return 0;
}

void ensure_lock() {
    if (!g_lock_ready) {
        InitializeCriticalSection(&g_lock);
        g_lock_ready = true;
    }
}

// stream_pump_start(pipe_handle:int, socket_fd:int, header:bytes) -> bool
PyObject* stream_pump_start(PyObject*, PyObject* args) {
    long long pipe_h = 0, sock_h = 0;
    Py_buffer header{};
    if (!PyArg_ParseTuple(args, "LLy*", &pipe_h, &sock_h, &header))
        return nullptr;
    ensure_lock();
    if (g_running.load()) {
        PyBuffer_Release(&header);
        Py_RETURN_FALSE;   // already running — stop() first
    }
    EnterCriticalSection(&g_lock);
    g_pipe = (HANDLE)(intptr_t)pipe_h;
    g_sock = (SOCKET)(intptr_t)sock_h;
    g_header.assign((const char*)header.buf, (size_t)header.len);
    LeaveCriticalSection(&g_lock);
    PyBuffer_Release(&header);

    g_sent_bytes.store(0);
    g_state.store(PumpState::Running);
    g_stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!g_stop_event) Py_RETURN_FALSE;
    g_running.store(true);
    g_thread = CreateThread(nullptr, 0, pump_proc, nullptr, 0, nullptr);
    if (!g_thread) {
        g_running.store(false);
        CloseHandle(g_stop_event);
        g_stop_event = nullptr;
        g_state.store(PumpState::Idle);
        Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

// stream_pump_stop() — end the loop; never closes the pipe or socket.
PyObject* stream_pump_stop(PyObject*, PyObject*) {
    if (g_thread) {
        g_running.store(false);
        SetEvent(g_stop_event);
        // Wake a blocked send() as a last resort is unnecessary: Python's
        // socket timeout (SO_SNDTIMEO) bounds any stuck send.
        WaitForSingleObject(g_thread, 4000);
        CloseHandle(g_thread);
        g_thread = nullptr;
    }
    if (g_stop_event) {
        CloseHandle(g_stop_event);
        g_stop_event = nullptr;
    }
    EnterCriticalSection(&g_lock);
    g_pipe = nullptr;
    g_sock = INVALID_SOCKET;
    g_header.clear();
    LeaveCriticalSection(&g_lock);
    g_state.store(PumpState::Idle);
    Py_RETURN_NONE;
}

// stream_pump_state() -> int  (0 idle, 1 running, 2 eof, 3 send error)
PyObject* stream_pump_state(PyObject*, PyObject*) {
    PumpState s = g_state.load();
    if (s == PumpState::Running && !g_running.load())
        s = PumpState::Eof;   // thread just exited normally
    return PyLong_FromLong((long)s);
}

// stream_pump_sent() -> bytes handed to the socket so far
PyObject* stream_pump_sent(PyObject*, PyObject*) {
    return PyLong_FromUnsignedLongLong(g_sent_bytes.load());
}

PyMethodDef methods[] = {
    {"stream_pump_start", stream_pump_start, METH_VARARGS,
     "start(pipe_handle, socket_fd, header_bytes) -> bool — native send loop"},
    {"stream_pump_stop", stream_pump_stop, METH_NOARGS,
     "stop() — end the pump (pipe/socket stay owned by Python)"},
    {"stream_pump_state", stream_pump_state, METH_NOARGS,
     "state() -> 0 idle | 1 running | 2 eof | 3 send error"},
    {"stream_pump_sent", stream_pump_sent, METH_NOARGS,
     "sent() -> bytes handed to the socket so far"},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "native_audio_stream",
    "FreQ native stream pump (pipe -> TCP).", -1, methods,
};

}  // namespace

PyMODINIT_FUNC PyInit_native_audio_stream() {
    return PyModule_Create(&module);
}
