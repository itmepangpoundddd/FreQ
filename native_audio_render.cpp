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
//
// Source lifetime (the divide-by-zero crash fix): every built-in source
// (file decoder / crossfade pipe) runs on ONE SourceBlock. stop_source()
// unblocks the thread (CancelIoEx on pipes), JOINS it, and only then
// uninitialises the decoder and frees the block. The old design shared one
// mutable struct and gave the join up after 3 s, leaving a zombie thread
// reading freed memory — the resampler then divided by a corrupted
// sampleRateOut and Windows killed the process (0xC0000094). If a thread
// ever refuses to die, the engine is LEAKED instead of freed.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mmdeviceapi.h>
#include <audioclient.h>
#include <cmath>

// Embedded file decoding for the built-in source (no device I/O — the
// WASAPI device is opened separately by this same engine).
#define MA_NO_DEVICE_IO
#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#include <atomic>
#include <cstdint>
#include <cstdlib>
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
// Consecutive failed device ops before declaring the endpoint gone
// (each loop pass waits up to 500 ms → ~5 s of dead device).
constexpr int kMaxDeviceFails = 10;

class RenderEngine {
public:
    // Per-source state, defined fully in the private section below.
    // Forward-declared here because member signatures above reference it.
    struct SourceBlock;

    RenderEngine() {
        InitializeCriticalSection(&lock_);
        InitializeCriticalSection(&src_lock_);
        InitializeCriticalSection(&mic_lock_);
    }
    ~RenderEngine() {
        emergency_mic_stop();
        stop();
        stop_source();
        DeleteCriticalSection(&mic_lock_);
        DeleteCriticalSection(&lock_);
        DeleteCriticalSection(&src_lock_);
    }

    bool start(const wchar_t* endpoint_id) {
        stop();  // full teardown first; buffered audio is preserved
        paused_.store(false);  // a fresh stream is never paused
        cur_endpoint_ = endpoint_id ? endpoint_id : L"";
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
        stop_source();   // the built-in feeder must not outlive the device
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

    // ────────────────────────────────────────────────
    // Built-in source: the engine decodes/pulls PCM itself so the Python
    // audio path disappears entirely (decode pump + crossfade feeder
    // moved into C++). Two source kinds:
    //   * file  — miniaudio decodes any supported format directly
    //   * pipe  — raw s16le PCM read from a Windows pipe handle (e.g. an
    //             ffmpeg stdout for codecs miniaudio lacks)
    // plus an optional fade-in ramp (the crossfade B-side feeder).
    // ────────────────────────────────────────────────
    bool use_file_source(const wchar_t* path_w, double seek_seconds,
                         double fade_seconds, float fade_target) {
        stop_source();   // detach + join the previous source FIRST
        auto* b = new (std::nothrow) SourceBlock();
        if (!b) return false;
        b->e = this;
        b->mode = SrcMode::File;
        b->pipe = INVALID_HANDLE_VALUE;
        b->fade_total = (int)(fade_seconds * kSampleRate);
        b->fade_remaining = b->fade_total;
        b->fade_target = fade_target;
        // Request f32 output — ma_linear_resampler only supports s16 and f32.
        // With s16 output, files whose NATIVE format is s24/u8/s32 AND whose
        // sample rate differs from 44100 Hz cause miniaudio to resample in the
        // native format before converting it, which hits MA_ASSERT(MA_FALSE) at
        // miniaudio.h:53690. Using f32 ensures format conversion always happens
        // BEFORE resampling, regardless of the source codec or bit depth.
        // source_pull() converts the resulting f32 frames to s16 manually.
        ma_decoder_config cfg = ma_decoder_config_init(
            ma_format_f32, kChannels, (ma_uint32)kSampleRate);
        // Init the decoder IN PLACE inside the heap block. ma_decoder embeds
        // a data converter whose resampler allocates an inline heap and
        // stores pointers INTO the decoder struct; initializing on the stack
        // and copying the struct afterwards left those pointers aimed at the
        // dead stack frame — silent decoding and 0xC0000374 heap corruption
        // once the frame memory was reused (48 kHz files force the resampler
        // path, which is why 44.1 kHz test fixtures never caught it).
        if (ma_decoder_init_file_w(path_w, &cfg, &b->dec) != MA_SUCCESS) {
            delete b;
            return false;
        }
        b->dec_ok = true;
        src_pending_seek_.store(
            seek_seconds > 0 ? (uint64_t)(seek_seconds * kSampleRate)
                             : UINT64_MAX);
        src_base_.store(src_frames_read_.load());
        src_eof_.store(false);
        src_failed_.store(false);
        EnterCriticalSection(&src_lock_);
        src_ = b;
        LeaveCriticalSection(&src_lock_);
        start_source_thread(b);
        return true;
    }

    bool use_pipe_source(void* pipe_handle, double fade_seconds, float fade_target) {
        stop_source();   // detach + join the previous source FIRST
        if (!pipe_handle) return false;
        auto* b = new (std::nothrow) SourceBlock();
        if (!b) return false;
        b->e = this;
        b->mode = SrcMode::Pipe;
        b->dec_ok = false;
        b->pipe = (HANDLE)pipe_handle;
        b->fade_total = (int)(fade_seconds * kSampleRate);
        b->fade_remaining = b->fade_total;
        b->fade_target = fade_target;
        src_base_.store(src_frames_read_.load());
        src_eof_.store(false);
        src_failed_.store(false);
        EnterCriticalSection(&src_lock_);
        src_ = b;
        LeaveCriticalSection(&src_lock_);
        start_source_thread(b);
        return true;
    }

    void source_seek(double seconds) {
        if (seconds <= 0) return;
        src_pending_seek_.store((uint64_t)(seconds * kSampleRate));
        src_eof_.store(false);          // seeking rewinds past the EOF
        src_failed_.store(false);
    }

    // Seek + discard: flush everything the OLD position still has queued
    // (Python ring AND the device buffer) in one atomic step. A bare
    // source_seek leaves up to a second of pre-seek audio in the ring, so
    // the user hears the old position chase the new one — and pressing
    // the waveform repeatedly stacks two positions on top of each other.
    // Only the render thread may Stop/Reset the device, so the device half
    // goes through the existing flush-request mechanism; the ring half is
    // cleared here. Frames from the NEW position arrive right behind it.
    bool source_seek_flush(double seconds) {
        if (seconds < 0) seconds = 0;
        EnterCriticalSection(&lock_);
        read_pos_ = write_pos_ = buffered_ = 0;
        LeaveCriticalSection(&lock_);
        src_pending_seek_.store((uint64_t)(seconds * kSampleRate));
        src_eof_.store(false);
        src_failed_.store(false);
        src_base_.store(src_frames_read_.load());   // source_state() restarts at 0
        request_flush();
        return true;
    }

    size_t source_state() const {
        return src_frames_read_.load() - src_base_.load();
    }

    bool source_eof() const { return src_eof_.load(); }
    bool source_failed() const { return src_failed_.load(); }

    // Detach, unblock, JOIN and free the current source. The decoder is
    // torn down only after the thread is provably gone — the old shared-
    // struct design uninitialised it under a zombie thread left behind by
    // the 3 s join timeout, which is the use-after-free behind the
    // divide-by-zero crash inside miniaudio (jingle start/stop storms).
    // Returns false only when a source thread refused to die within the
    // wait window: the caller must then leak the engine instead of
    // deleting it (the thread still runs inside it).
    bool stop_source() {
        SourceBlock* b = nullptr;
        EnterCriticalSection(&src_lock_);
        b = src_;
        src_ = nullptr;
        LeaveCriticalSection(&src_lock_);
        if (!b) return true;
        // Unblock a ReadFile() parked on an empty pipe BEFORE waiting —
        // without this the join below could wait the full timeout.
        if (b->pipe != INVALID_HANDLE_VALUE)
            CancelIoEx(b->pipe, nullptr);
        b->exiting.store(true);
        if (b->shutdown) SetEvent(b->shutdown);
        if (b->thread) {
            const DWORD w = WaitForSingleObject(b->thread, 3000);
            if (w != WAIT_OBJECT_0) return false;   // zombie: leak, don't touch
            CloseHandle(b->thread);
        }
        if (b->shutdown) CloseHandle(b->shutdown);
        if (b->mode == SrcMode::File && b->dec_ok) {
            ma_decoder_uninit(&b->dec);   // thread is gone — safe now
            b->dec_ok = false;
        }
        // The pipe handle belongs to Python's subprocess — do NOT close it.
        delete b;
        return true;
    }

    // Full teardown for engine deletion: render thread first, then the
    // source. False means a source thread is still alive — the capsule
    // destructor must LEAK the engine rather than free it.
    bool shutdown_for_delete() {
        stop();
        return stop_source();
    }

private:
    enum class SrcMode { None, File, Pipe };

    // One SourceBlock per started source; stop_source() JOINS the thread
    // before freeing the block or uninitialising the decoder, so no code
    // path can ever touch a torn-down decoder (the old shared-struct
    // design left a zombie thread reading freed memory — the
    // divide-by-zero crash inside miniaudio on jingle start/stop storms).
    static constexpr size_t kSourceChunk = 2048;   // frames per decode call
    static constexpr size_t kSourceRefill = 22050; // top ring up to 0.5 s
    struct SourceBlock {
        RenderEngine* e = nullptr;
        SrcMode mode = SrcMode::None;
        ma_decoder dec = {};
        bool dec_ok = false;
        HANDLE pipe = INVALID_HANDLE_VALUE;
        int fade_total = 0;
        int fade_remaining = 0;
        float fade_target = 1.0f;
        bool eof = false;
        std::atomic<bool> exiting{false};
        HANDLE thread = nullptr;
        HANDLE shutdown = nullptr;
    };

    void start_source_thread(SourceBlock* b) {
        b->shutdown = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!b->shutdown) return;
        src_running_.store(true);
        b->thread = CreateThread(nullptr, 0, source_proc, b, 0, nullptr);
        if (!b->thread) {
            src_running_.store(false);
            CloseHandle(b->shutdown);
            b->shutdown = nullptr;
        }
    }

    static DWORD WINAPI source_proc(LPVOID param) {
        auto* b = static_cast<SourceBlock*>(param);
        b->e->source_loop(b);
        return 0;
    }

    // Read up to ``frames`` from the current source. Returns frames read;
    // 0 means EOF/error.
    size_t source_pull(int16_t* out, size_t frames, SourceBlock* b) {
        if (b->exiting.load()) return 0;   // retired — stop immediately
        if (b->mode == SrcMode::File) {
            uint64_t sk = src_pending_seek_.exchange(UINT64_MAX);
            if (sk != UINT64_MAX)
                ma_decoder_seek_to_pcm_frame(&b->dec, (ma_uint64)sk);
            // Decoder outputs f32 (see use_file_source). Read into a stack
            // f32 buffer (max kSourceChunk * 2 ch * 4 bytes = 16 384 B),
            // then convert to s16 for the ring. This keeps the resampler on
            // an f32 path regardless of the file's native bit-depth.
            float f32buf[kSourceChunk * kChannels];
            ma_uint64 got = 0;
            if (ma_decoder_read_pcm_frames(&b->dec, f32buf,
                                           (ma_uint64)frames, &got) != MA_SUCCESS)
                return 0;
            const size_t total_samples = (size_t)got * kChannels;
            for (size_t i = 0; i < total_samples; ++i) {
                float v = f32buf[i];
                if (v >  1.0f) v =  1.0f;
                if (v < -1.0f) v = -1.0f;
                out[i] = static_cast<int16_t>(v * 32767.0f);
            }
            return (size_t)got;
        }
        if (b->mode == SrcMode::Pipe) {
            const size_t want = frames * kChannels * 2;
            char* p = (char*)out;
            size_t got = 0;
            while (got < want) {
                if (b->exiting.load())
                    return got / (kChannels * 2);   // stop mid-chunk
                DWORD r = 0;
                if (!ReadFile(b->pipe, p + got, (DWORD)(want - got), &r,
                              nullptr) || r == 0)
                    break;
                got += r;
            }
            return got / (kChannels * 2);
        }
        return 0;
    }

    void apply_fade_in(int16_t* buf, size_t frames, SourceBlock* b) {
        if (b->fade_remaining <= 0 || b->fade_total <= 0) return;
        const size_t n = frames < (size_t)b->fade_remaining
            ? frames : (size_t)b->fade_remaining;
        for (size_t i = 0; i < n; ++i) {
            const int pos = b->fade_total - b->fade_remaining + (int)i;
            const float g = b->fade_target *
                ((float)pos / (float)b->fade_total);
            for (int c = 0; c < (int)kChannels; ++c) {
                double v = (double)buf[i * kChannels + c] * g;
                if (v > 32767.0) v = 32767.0;
                if (v < -32768.0) v = -32768.0;
                buf[i * kChannels + c] = (int16_t)v;
            }
        }
        b->fade_remaining -= (int)n;
    }

    void source_loop(SourceBlock* b) {
        std::vector<int16_t> buf(kSourceChunk * kChannels);
        while (src_running_.load()) {
            const DWORD w = WaitForSingleObject(b->shutdown, 60);
            if (w == WAIT_OBJECT_0 || b->exiting.load()) break;
            if (src_eof_.load()) break;
            // Realtime pacing: keep the ring topped up to the refill mark
            // (0.5 s) and NO further — the source must not outrun the
            // device by pre-buffering the whole ring.
            size_t room = 0;
            for (;;) {
                EnterCriticalSection(&lock_);
                room = buffered_ < kSourceRefill
                    ? kSourceRefill - buffered_ : 0;
                LeaveCriticalSection(&lock_);
                if (room || src_eof_.load()) break;
                if (WaitForSingleObject(b->shutdown, 20) == WAIT_OBJECT_0)
                    break;
                if (b->exiting.load()) break;
            }
            if (b->exiting.load() || src_eof_.load()) break;
            // Only the CURRENT source may feed: stop_source() swaps src_ out
            // and joins this thread BEFORE freeing the block, so a retired
            // block is still alive here — pulls are just skipped. (No lock is
            // held across pulls: stop_source() must never wait on src_lock_
            // while this thread is parked inside a blocking ReadFile — that
            // would deadlock the CancelIoEx unblock below.)
            EnterCriticalSection(&src_lock_);
            const bool current = (b == src_);
            LeaveCriticalSection(&src_lock_);
            if (!current) break;
            size_t total = 0;
            while (total < room) {
                const size_t want = kSourceChunk < (room - total)
                    ? kSourceChunk : (room - total);
                const size_t got = source_pull(buf.data(), want, b);
                if (got == 0) {
                    b->eof = true;
                    break;
                }
                apply_fade_in(buf.data(), got, b);
                // Ring write (same contract as render_write: full frames).
                EnterCriticalSection(&lock_);
                const size_t free_frames = ring_capacity_ > buffered_
                    ? ring_capacity_ - buffered_ : 0;
                const size_t n = got < free_frames ? got : free_frames;
                for (size_t i = 0; i < n * kChannels; ++i) {
                    ring_[write_pos_] = buf[i];
                    write_pos_ = (write_pos_ + 1) % ring_capacity_;
                }
                buffered_ += n;
                frames_written_ += n;
                LeaveCriticalSection(&lock_);
                src_frames_read_.fetch_add(n);
                total += n;
                if (n < got) break;   // ring full — pause until drained
                if (b->exiting.load()) break;
            }
            if (b->eof) {
                // Only the still-current source publishes EOF to Python —
                // a retired one must not fake the end of the NEW source.
                EnterCriticalSection(&src_lock_);
                const bool still_current = (b == src_);
                LeaveCriticalSection(&src_lock_);
                if (still_current) src_eof_.store(true);
                break;
            }
            if (b->exiting.load()) break;
            if (buffer_event_) SetEvent(buffer_event_);
        }
        src_running_.store(false);
    }

public:
    static constexpr double kPiPublic = 3.14159265358979323846;

    // Flush the ring AND stop any built-in source: a track change must
    // not let the old source keep feeding the freshly cleared ring.
    void clear() {
        EnterCriticalSection(&lock_);
        read_pos_ = write_pos_ = buffered_ = 0;
        frames_written_ = 0;
        LeaveCriticalSection(&lock_);
        stop_source();
    }

    // Request a device-buffer flush, executed by the render thread on its
    // next wake-up. Only that thread may call IAudioClient methods, so the
    // Python side merely sets this flag. Needed because Stop()/Reset() from
    // a foreign thread is invalid and GetBufferSize() can exceed what was
    // actually requested — leaving 2-3 s of the previous track's tail
    // audible after render_clear().
    void request_flush() { flush_requested_.store(true); }

    void process_flush_request() {
        if (!flush_requested_.exchange(false)) return;
        if (!client_ || !render_ || !running_.load()) return;
        try {
            client_->Stop();
            client_->Reset();          // discard the queued device buffer
            client_->Start();          // continue immediately with new audio
        } catch (...) {
            // Reset can fail on some drivers; the render loop's failure
            // counting will recover the device if it stays broken.
        }
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

    // ── 3-band EQ (biquad: lowshelf / peaking / highshelf) ─────────
    // Gains in dB (-12..+12); 0 dB = bypass. Coefficients are recomputed
    // on set and applied per-frame at copy-out on the render thread.
    void set_eq(double bass_db, double mid_db, double treble_db) {
        EnterCriticalSection(&lock_);
        eq_bass_db_ = bass_db;
        eq_mid_db_ = mid_db;
        eq_treble_db_ = treble_db;
        eq_on_.store(std::fabs(bass_db) > 0.01 || std::fabs(mid_db) > 0.01
                  || std::fabs(treble_db) > 0.01);
        if (eq_on_.load()) {
            design_lowshelf(bass_db, 200.0, c_b_);
            design_peaking(mid_db, 1200.0, 1.0, c_m_);
            design_highshelf(treble_db, 4000.0, c_t_);
        }
        // Discontinuity guard: reset filter memory on parameter change.
        for (int st = 0; st < 3; ++st) {
            for (int k = 0; k < 4; ++k) {
                st_l_[st][k] = 0.0;
                st_r_[st][k] = 0.0;
            }
        }
        LeaveCriticalSection(&lock_);
    }

    void eq_gains(double* out3) const {
        EnterCriticalSection(&lock_);
        out3[0] = eq_bass_db_;
        out3[1] = eq_mid_db_;
        out3[2] = eq_treble_db_;
        LeaveCriticalSection(&lock_);
    }

    // Direct-Form-II Transposed biquad processing of interleaved stereo
    // in-place. Each stage keeps its OWN delay state (s1..s3 per channel);
    // sharing one state across the cascade garbled the filtering.
    // ── Master broadcast chain (0926.2 P1): EQ → Compressor → Limiter ──
    // Runs on the FINAL mix (music + live mics) right before the device
    // buffer is submitted, so listeners/hearers get the broadcast voice.
    // All parameters are atomics: the UI thread writes them lock-free and
    // the render thread picks them up on the next buffer. OFF (mode 0) =
    // the samples pass untouched — byte-identical legacy path.
    struct MasterChain {
        std::atomic<int> mode{0};          // 0 off, 1 gentle, 2 strong (base)
        // EQ (dB, −12…+12), direct-set by params
        std::atomic<int> eq_bass_db{0};
        std::atomic<int> eq_mid_db{0};
        std::atomic<int> eq_treb_db{0};
        // Compressor: threshold dBFS (−48…−6), ratio ×10 (10…80 = 1:1…8:1)
        std::atomic<int> comp_thresh_db{-24};
        std::atomic<int> comp_ratio_x10{30};
        // Limiter: ceiling dBFS (−6…0); enabled independently (like the
        // mic-chain stages) — ON means an explicit set chose a ceiling < 0.
        std::atomic<int> limit_ceiling_db{-1};
        std::atomic<bool> limit_on{false};
        // Per-channel DSP state (render thread only)
        double eq_st[2][3][4] = {};        // [ch][stage][z-1…z-2, w-1…w-2]
        double eq_coef[2][3][5] = {};      // [ch][stage][b0 b1 b2 a1 a2]
        double comp_env[2] = {0, 0};       // envelope follower per channel
        int    eq_applied_key = -1;        // rebuild coefs when params move
    } master_;

    static void MasterEqCoef(double db, int sr, double out[5]) {
        // Gentle tilt shelves (match the song-EQ family); gain folded in.
        const double A = std::pow(10.0, db / 40.0);
        const double f0 = db >= 0 ? 0.0 : 0.0;   // frequency set below
        (void)f0;
        double freq, q = 0.707;
        // stage assignment decided by caller; this helper computes a low
        // shelf when is_low, high shelf when !is_low
        const double w0 = 0.0;
        (void)w0;
        // real computation done in MasterEqCoefShelf
        out[0] = A; out[1] = 0; out[2] = 0; out[3] = 0; out[4] = q;
    }

    static void MasterShelfCoef(bool low, double freq, double db, int sr,
                                double out[5]) {
        const double A = std::pow(10.0, db / 40.0);
        const double w0 = 2.0 * 3.14159265358979323846 * freq / sr;
        const double cw = std::cos(w0), sw = std::sin(w0);
        const double S = 0.707;              // shelf slope
        const double alpha = sw / 2.0 * std::sqrt((A + 1.0 / A) * (1.0 / S - 1.0) + 2.0);
        const double two = 2.0 * std::sqrt(A) * alpha;
        double b0, b1, b2, a0, a1, a2;
        if (low) {
            b0 = A * ((A + 1) - (A - 1) * cw + two);
            b1 = 2 * A * ((A - 1) - (A + 1) * cw);
            b2 = A * ((A + 1) + (A - 1) * cw + two);
            a0 = (A + 1) + (A - 1) * cw + two;
            a1 = -2 * ((A - 1) + (A + 1) * cw);
            a2 = (A + 1) - (A - 1) * cw + two;
        } else {
            b0 = A * ((A + 1) + (A + 1) * cw + two);
            b1 = -2 * A * ((A - 1) + (A + 1) * cw);
            b2 = A * ((A + 1) - (A + 1) * cw - two);
            a0 = (A + 1) - (A - 1) * cw + two;
            a1 = 2 * ((A - 1) - (A + 1) * cw);
            a2 = (A + 1) + (A - 1) * cw - two;
        }
        out[0] = b0 / a0; out[1] = b1 / a0; out[2] = b2 / a0;
        out[3] = a1 / a0; out[4] = a2 / a0;
    }

    void master_rebuild_coefs(int sr) {
        // Bass low-shelf @120 Hz, mid peak @1 kHz, treble high-shelf @3.2 kHz
        MasterShelfCoef(true, 120.0,
                        master_.eq_bass_db.load() / 1.0, sr,
                        master_.eq_coef[0][0]);
        MasterShelfCoef(false, 3200.0,
                        master_.eq_treb_db.load() / 1.0, sr,
                        master_.eq_coef[0][2]);
        // Mid = peaking EQ
        {
            const double A = std::pow(10.0, master_.eq_mid_db.load() / 40.0);
            const double w0 = 2.0 * 3.14159265358979323846 * 1000.0 / sr;
            const double cw = std::cos(w0), sw = std::sin(w0);
            const double Q = 0.9;
            const double alpha = sw / (2 * Q);
            const double b0 = 1 + alpha * A, b1 = -2 * cw,
                         b2 = 1 - alpha * A;
            const double a0 = 1 + alpha / A, a1 = -2 * cw,
                         a2 = 1 - alpha / A;
            master_.eq_coef[0][1][0] = b0 / a0;
            master_.eq_coef[0][1][1] = b1 / a0;
            master_.eq_coef[0][1][2] = b2 / a0;
            master_.eq_coef[0][1][3] = a1 / a0;
            master_.eq_coef[0][1][4] = a2 / a0;
        }
        // Copy to the right channel (same coefficients both sides)
        for (int st = 0; st < 3; ++st)
            for (int k = 0; k < 5; ++k)
                master_.eq_coef[1][st][k] = master_.eq_coef[0][st][k];
        // Reset filter state so old resonance doesn't smear into the new
        // curve; a single-block click is inaudible vs. a wrong-tone chain.
        for (int c = 0; c < 2; ++c)
            for (int s = 0; s < 3; ++s)
                for (int k = 0; k < 4; ++k) master_.eq_st[c][s][k] = 0.0;
        master_.eq_applied_key = sr * 100000 + master_.eq_bass_db.load() * 1000
            + master_.eq_mid_db.load() * 10 + master_.eq_treb_db.load();
    }

    void master_process(int16_t* samples, size_t total) {
        const int mode = master_.mode.load();
        const int bass = master_.eq_bass_db.load();
        const int mid = master_.eq_mid_db.load();
        const int treb = master_.eq_treb_db.load();
        const int comp_t = master_.comp_thresh_db.load();
        const int comp_r = master_.comp_ratio_x10.load();
        const int ceil_db = master_.limit_ceiling_db.load();
        const bool eq_active = (bass != 0 || mid != 0 || treb != 0);
        const bool limit_active = master_.limit_on.load();
        if (mode == 0 && !eq_active && !limit_active)
            return;                            // pure legacy path
        const int key = bass * 10000 + mid * 100 + treb;
        if (key != master_.eq_applied_key) master_rebuild_coefs(kSampleRate);

        // compressor/limiter derived targets (mode presets for attack-ish
        // smoothing): gentle = slow envelope, strong = fast
        const double env_a = mode == 2 ? 0.35 : 0.18;   // coefficient weights
        const double thr_lin = std::pow(10.0, comp_t / 20.0) * 32768.0;
        const double ratio = comp_r / 10.0;
        const double ceil_lin = std::pow(10.0, ceil_db / 20.0) * 32768.0;

        for (size_t i = 0; i < total; ++i) {
            const int ch = i & 1;
            double x = samples[i];
            // 1) EQ biquads (3 stages per channel)
            if (eq_active) {
                double y = x;
                for (int st2 = 0; st2 < 3; ++st2) {
                    const double* c = master_.eq_coef[ch][st2];
                    double* s = master_.eq_st[ch][st2];
                    const double yn = c[0] * y + c[1] * s[0] + c[2] * s[1]
                                    - c[3] * s[2] - c[4] * s[3];
                    s[1] = s[0]; s[0] = y;
                    s[3] = s[2]; s[2] = yn;
                    y = yn;
                }
                x = y;
            }
            // 2) Compressor (downward, envelope follower with smooth gain)
            if (mode != 0) {
                const double ax = std::fabs(x);
                double& env = master_.comp_env[ch];
                env += env_a * (ax - env);
                double g = 1.0;
                if (env > thr_lin && env > 0.0)
                    g = std::pow(thr_lin / env, 1.0 - 1.0 / ratio);
                x *= g;
            }
            // 3) Limiter ceiling (hard clip at the chosen dBFS ceiling)
            if (x > ceil_lin) x = ceil_lin;
            if (x < -ceil_lin) x = -ceil_lin;
            int v = (int)std::lround(x);
            if (v > 32767) v = 32767;
            if (v < -32768) v = -32768;
            samples[i] = (int16_t)v;
        }
    }

    void eq_process(int16_t* samples, size_t total) {
        if (!eq_on_.load()) return;
        for (size_t i = 0; i + 1 < total; i += 2) {
            double l = samples[i];
            double r = samples[i + 1];
            double y;
            // L: bass stage
            y = c_b_.b0 * l + c_b_.b1 * st_l_[0][0] + c_b_.b2 * st_l_[0][1]
                - c_b_.a1 * st_l_[0][2] - c_b_.a2 * st_l_[0][3];
            st_l_[0][1] = st_l_[0][0]; st_l_[0][0] = l;
            st_l_[0][3] = st_l_[0][2]; st_l_[0][2] = y;
            l = y;
            // L: mid stage
            y = c_m_.b0 * l + c_m_.b1 * st_l_[1][0] + c_m_.b2 * st_l_[1][1]
                - c_m_.a1 * st_l_[1][2] - c_m_.a2 * st_l_[1][3];
            st_l_[1][1] = st_l_[1][0]; st_l_[1][0] = l;
            st_l_[1][3] = st_l_[1][2]; st_l_[1][2] = y;
            l = y;
            // L: treble stage
            y = c_t_.b0 * l + c_t_.b1 * st_l_[2][0] + c_t_.b2 * st_l_[2][1]
                - c_t_.a1 * st_l_[2][2] - c_t_.a2 * st_l_[2][3];
            st_l_[2][1] = st_l_[2][0]; st_l_[2][0] = l;
            st_l_[2][3] = st_l_[2][2]; st_l_[2][2] = y;
            double lo = y;
            // R: bass stage
            y = c_b_.b0 * r + c_b_.b1 * st_r_[0][0] + c_b_.b2 * st_r_[0][1]
                - c_b_.a1 * st_r_[0][2] - c_b_.a2 * st_r_[0][3];
            st_r_[0][1] = st_r_[0][0]; st_r_[0][0] = r;
            st_r_[0][3] = st_r_[0][2]; st_r_[0][2] = y;
            r = y;
            // R: mid stage
            y = c_m_.b0 * r + c_m_.b1 * st_r_[1][0] + c_m_.b2 * st_r_[1][1]
                - c_m_.a1 * st_r_[1][2] - c_m_.a2 * st_r_[1][3];
            st_r_[1][1] = st_r_[1][0]; st_r_[1][0] = r;
            st_r_[1][3] = st_r_[1][2]; st_r_[1][2] = y;
            r = y;
            // R: treble stage
            y = c_t_.b0 * r + c_t_.b1 * st_r_[2][0] + c_t_.b2 * st_r_[2][1]
                - c_t_.a1 * st_r_[2][2] - c_t_.a2 * st_r_[2][3];
            st_r_[2][1] = st_r_[2][0]; st_r_[2][0] = r;
            st_r_[2][3] = st_r_[2][2]; st_r_[2][2] = y;
            double ro = y;
            if (lo > 32767.0) lo = 32767.0;
            if (lo < -32768.0) lo = -32768.0;
            if (ro > 32767.0) ro = 32767.0;
            if (ro < -32768.0) ro = -32768.0;
            samples[i] = static_cast<int16_t>(lo);
            samples[i + 1] = static_cast<int16_t>(ro);
        }
    }

    float volume() const { return volume_.load(); }

    // True pause: the device keeps playing SILENCE while the ring is left
    // untouched, so the stream stays alive, `emitted` freezes, and resume
    // continues exactly at the paused position. (Muting alone would keep
    // draining the ring — playback would advance silently while paused.)
    void set_paused(bool p) { paused_.store(p); }

    bool is_paused() const { return paused_.load(); }

    bool is_running() const { return running_.load() && render_ != nullptr; }

    // ─────────────────────────────────────────────────────────────────
    // Emergency-mic live mix-in: open a capture endpoint and MIX its
    // audio straight into the render loop's device buffer. Zero added
    // latency — the mic is heard exactly when the music is.
    //
    // Capture: a dedicated thread parks in a shared-mode capture client
    // (the same pattern as native_audio_capture.cpp). Resampling: an
    // embedded ma_linear_resampler converts the device mix format into
    // the engine's canonical 44.1 kHz stereo (preallocated inline heap,
    // recreated per start — never copied after init). Mixing happens at
    // copy-out inside the SAME lock pass as the ring playback, so a mic
    // burst can never tear between two separate device buffers.
    // ─────────────────────────────────────────────────────────────────
    bool emergency_mic_start(const wchar_t* endpoint_id, float gain) {
        // Multi-mic: each call ADDS one capture to the mix (dual segments).
        auto* m = new (std::nothrow) MicBlock();
        if (!m) return false;
        m->owner = this;
        m->gain = gain;
        // COM on the spawning thread (mirrors init_device). The capture
        // thread runs its own MTA.
        const HRESULT ci = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        (void)ci;   // process-lifetime apartment is fine
        HRESULT hr = CoCreateInstance(
            __uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
            __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&m->enumerator));
        if (FAILED(hr)) { delete m; return false; }
        IMMDevice* device = nullptr;
        if (endpoint_id && *endpoint_id)
            hr = m->enumerator->GetDevice(endpoint_id, &device);
        else
            hr = m->enumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &device);
        if (FAILED(hr)) {
            m->enumerator->Release();
            delete m;
            return false;
        }
        hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                              reinterpret_cast<void**>(&m->client));
        device->Release();
        if (FAILED(hr)) {
            m->enumerator->Release();
            delete m;
            return false;
        }
        WAVEFORMATEX* fmt = nullptr;
        if (FAILED(m->client->GetMixFormat(&fmt)) || !fmt) {
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        // Shared capture client: 20 ms buffer, event NOT required — the
        // capture thread polls at 8 ms like native_audio_capture.cpp.
        if (FAILED(m->client->Initialize(AUDCLNT_SHAREMODE_SHARED, 0,
                                         200000, 0, fmt, nullptr))) {
            CoTaskMemFree(fmt);
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        // Resampler state: capture mix format → canonical 44.1k stereo.
        // The implementation reads the resampler from the SUBFORMAT when
        // the mix format is extensible (virtually every modern device).
        m->in_rate = fmt->nSamplesPerSec;
        m->in_channels = fmt->nChannels;
        WAVEFORMATEXTENSIBLE* ext = nullptr;
        if (fmt->wFormatTag == WAVE_FORMAT_EXTENSIBLE)
            ext = reinterpret_cast<WAVEFORMATEXTENSIBLE*>(fmt);
        ma_format in_fmt = ma_format_unknown;
        if (ext && ext->SubFormat.Data1 == 3)            // IEEE float
            in_fmt = ma_format_f32;
        else if (fmt->wFormatTag == WAVE_FORMAT_IEEE_FLOAT)
            in_fmt = ma_format_f32;
        else if (ext && ext->SubFormat.Data1 == 1)       // PCM
            in_fmt = ma_format_s16;
        else if (fmt->wFormatTag == WAVE_FORMAT_PCM) {
            // Native WAVE_FORMAT_PCM: miniaudio's linear resampler only
            // handles s16/f32 — larger PCM widths go through s32 as well.
            in_fmt = fmt->wBitsPerSample <= 16 ? ma_format_s16 : ma_format_s32;
        } else if (ext && fmt->wBitsPerSample > 16)
            in_fmt = ma_format_s32;
        if (in_fmt == ma_format_unknown) in_fmt = ma_format_s16;
        m->in_format = in_fmt;
        ma_linear_resampler_config rc =
            ma_linear_resampler_config_init(in_fmt, (ma_uint32)kChannels,
                                            (ma_uint32)m->in_rate, kSampleRate);
        size_t heap_size = 0;
        if (ma_linear_resampler_get_heap_size(&rc, &heap_size) != MA_SUCCESS
                || heap_size == 0) {
            CoTaskMemFree(fmt);
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        m->heap = static_cast<ma_uint8*>(malloc(heap_size));
        if (!m->heap ||
            ma_linear_resampler_init_preallocated(&rc, m->heap, &m->resampler)
                != MA_SUCCESS) {
            free(m->heap);
            CoTaskMemFree(fmt);
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        m->resampler_ok = true;
        // The capture client reads the mix format directly (float or int
        // native width) — ConvertMicFrame handles the format below.
        if (FAILED(m->client->GetService(__uuidof(IAudioCaptureClient),
                                         reinterpret_cast<void**>(&m->capture)))) {
            ma_linear_resampler_uninit(&m->resampler, nullptr);
            free(m->heap);
            CoTaskMemFree(fmt);
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        CoTaskMemFree(fmt);
        if (FAILED(m->client->Start())) {
            ma_linear_resampler_uninit(&m->resampler, nullptr);
            free(m->heap);
            m->capture->Release();
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        // Publish + spawn. The array is guarded by mic_lock_; the render
        // thread dereferences entries under the same lock.
        EnterCriticalSection(&mic_lock_);
        if (mic_count_ >= kMaxMics) {
            LeaveCriticalSection(&mic_lock_);
            m->client->Stop();
            ma_linear_resampler_uninit(&m->resampler, nullptr);
            free(m->heap);
            m->capture->Release();
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;   // capacity reached
        }
        mics_[mic_count_++] = m;
        LeaveCriticalSection(&mic_lock_);
        mic_active_.fetch_add(1);
        m->thread = CreateThread(nullptr, 0, mic_thread_proc, m, 0, nullptr);
        if (!m->thread) {
            EnterCriticalSection(&mic_lock_);
            for (size_t i = 0; i < mic_count_; ++i) {
                if (mics_[i] == m) {
                    for (size_t k = i; k + 1 < mic_count_; ++k)
                        mics_[k] = mics_[k + 1];
                    mic_count_--;
                    break;
                }
            }
            LeaveCriticalSection(&mic_lock_);
            mic_active_.fetch_sub(1);
            m->client->Stop();
            ma_linear_resampler_uninit(&m->resampler, nullptr);
            free(m->heap);
            m->capture->Release();
            m->client->Release();
            m->enumerator->Release();
            delete m;
            return false;
        }
        return true;
    }

    void emergency_mic_stop() {
        // Stop ALL live mics (a dual segment ends both voices at once).
        EnterCriticalSection(&mic_lock_);
        MicBlock* blocks[kMaxMics];
        const size_t n = mic_count_;
        for (size_t i = 0; i < n; ++i) blocks[i] = mics_[i];
        mic_count_ = 0;
        LeaveCriticalSection(&mic_lock_);
        if (n == 0) return;
        mic_active_.store(0);
        for (size_t i = 0; i < n; ++i) {
            MicBlock* m = blocks[i];
            if (m->thread) {
                // Same contract as stop_source(): a thread parked in a
                // WASAPI call (audio service restart) must never have its
                // object freed under it. It exits on the next running
                // check and the OS reclaims everything at process exit.
                if (WaitForSingleObject(m->thread, 2000) != WAIT_OBJECT_0) {
                    m->zombie = 1;   // leak the block; never touch it again
                    continue;
                }
                CloseHandle(m->thread);
            }
            emergency_mic_free(m);
        }
    }

    void emergency_mic_set_gain(float g) {
        if (g < 0.0f) g = 0.0f;
        if (g > 4.0f) g = 4.0f;
        EnterCriticalSection(&mic_lock_);
        for (size_t i = 0; i < mic_count_; ++i)
            if (mics_[i]) mics_[i]->gain.store(g);
        LeaveCriticalSection(&mic_lock_);
    }

    // Mic noise suppression: 0 = off (bit-identical legacy path),
    // 1 = gentle (soft high-pass + shallow gate), 2 = strong (higher
    // high-pass + deeper, faster gate). Applied per-mic on the render
    // thread; live toggles take effect on the next mixed buffer.
    void emergency_mic_set_suppression(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_dsp_mode_.store(mode);
    }

    int emergency_mic_suppression() const {
        return mic_dsp_mode_.load();
    }

    // Compressor / limiter chain stages (same per-stage pattern).
    void emergency_mic_set_compressor(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_comp_mode_.store(mode);
    }

    int emergency_mic_compressor() const {
        return mic_comp_mode_.load();
    }

    void emergency_mic_set_limiter(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_limiter_mode_.store(mode);
    }

    int emergency_mic_limiter() const {
        return mic_limiter_mode_.load();
    }

    // De-esser (4th chain stage): 0 off / 1 gentle / 2 strong.
    void emergency_mic_set_deesser(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_deesser_mode_.store(mode);
    }

    int emergency_mic_deesser() const {
        return mic_deesser_mode_.load();
    }

    // Mic EQ (5th chain stage): 0 off / 1 gentle / 2 strong.
    void emergency_mic_set_miceq(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_miceq_mode_.store(mode);
    }

    int emergency_mic_miceq() const {
        return mic_miceq_mode_.load();
    }

    // Echo (6th chain stage): 0 off / 1 gentle / 2 strong.
    void emergency_mic_set_echo(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_echo_mode_.store(mode);
    }

    int emergency_mic_echo() const {
        return mic_echo_mode_.load();
    }

    // Gain (7th chain stage — input trim): 0 off / 1 +6 dB / 2 +12 dB.
    void emergency_mic_set_gainst(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_gainst_mode_.store(mode);
    }

    int emergency_mic_gainst() const {
        return mic_gainst_mode_.load();
    }

    // Upward compressor (8th stage): 0 off / 1 gentle / 2 strong.
    void emergency_mic_set_upcomp(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_upcomp_mode_.store(mode);
    }

    int emergency_mic_upcomp() const {
        return mic_upcomp_mode_.load();
    }

    // Expander (9th stage): 0 off / 1 gentle / 2 strong.
    void emergency_mic_set_expand(int mode) {
        if (mode < 0) mode = 0;
        if (mode > 2) mode = 2;
        mic_expand_mode_.store(mode);
    }

    int emergency_mic_expand() const {
        return mic_expand_mode_.load();
    }

    // ── Fine-grained per-stage parameters (OBS-style sliders) ──
    // One generic store: key -> int value. The Python bridge owns the
    // naming/units; the engine just consumes the numbers. All defaults
    // reproduce the exact Gentle sound of the mode-only releases.
    int emergency_mic_set_param(const char* key, int value) {
        MicParam* p = find_param(key);
        if (!p) return 0;
        p->store(value);
        return 1;
    }

    int emergency_mic_get_param(const char* key) {
        const MicParam* p = find_param(key);
        return p ? p->load() : -1;
    }

    bool emergency_mic_active() const {
        return mic_active_.load() > 0;
    }

    void emergency_mic_set_paused(bool p) {
        if (p) mic_clear();   // drop already-aired pre-pause voice
        mic_paused_.store(p ? 1 : 0);
    }

    int emergency_mic_count() const {
        // Exact number of live capture endpoints mixed in right now.
        // A failed start rolls its own increment back (spawn-failure
        // path) and emergency_mic_stop() resets the counter to zero
        // once every block is accounted for — no phantom counts.
        return mic_active_.load();
    }

private:
    // Echo delay length: ~181 ms at the canonical 44.1 kHz rate.
    static const int kEchoTaps = 8000;

    using MicParam = std::atomic<int>;

    // Parameter store (all ints; the bridge scales/labels them):
    //  suppression: high-pass cutoff Hz / gate threshold dBFS
    //  gain: trim dB          compressor: threshold dB / ratio x10
    //  limiter: ceiling dB    de-esser: split Hz / threshold dB
    //  miceq: low dB / high dB      echo: ms / feedback % / wet %
    //  upcomp: threshold dB / ratio x10    expander: threshold dB / depth %
    MicParam p_sup_hp_{90};       // Hz   (gentle ≈ 88 Hz)
    MicParam p_sup_gate_{-45};    // dBFS (0.0056 linear)
    MicParam p_gain_db_{6};
    MicParam p_comp_db_{-24};     // 0.063 linear
    MicParam p_comp_ratio_{35};   // 3.5 : 1
    MicParam p_lim_db_{-1};       // 0.95 ceiling
    MicParam p_de_hz_{3500};      // 0.50 one-pole coef @ 44.1k
    MicParam p_de_db_{-20};       // 0.10 linear
    MicParam p_eq_low_db_{-3};
    MicParam p_eq_high_db_{3};
    MicParam p_echo_ms_{180};
    MicParam p_echo_fb_{30};      // %
    MicParam p_echo_wet_{28};     // %
    MicParam p_up_db_{-40};       // 0.010 linear
    MicParam p_up_ratio_{15};     // 1.5 : 1
    MicParam p_ex_db_{-45};       // 0.0056 linear
    MicParam p_ex_depth_{75};     // % below-threshold depth
    MicParam p_gate_floor_{25};   // % level the gate closes to (legacy 25%)

    MicParam* find_param(const char* key) {
        if (!key) return nullptr;
        if (!strcmp(key, "sup_hp_hz"))   return &p_sup_hp_;
        if (!strcmp(key, "sup_gate_db")) return &p_sup_gate_;
        if (!strcmp(key, "gain_db"))     return &p_gain_db_;
        if (!strcmp(key, "comp_db"))     return &p_comp_db_;
        if (!strcmp(key, "comp_ratio10"))return &p_comp_ratio_;
        if (!strcmp(key, "lim_db"))      return &p_lim_db_;
        if (!strcmp(key, "de_hz"))       return &p_de_hz_;
        if (!strcmp(key, "de_db"))       return &p_de_db_;
        if (!strcmp(key, "eq_low_db"))   return &p_eq_low_db_;
        if (!strcmp(key, "eq_high_db"))  return &p_eq_high_db_;
        if (!strcmp(key, "echo_ms"))     return &p_echo_ms_;
        if (!strcmp(key, "echo_fb_pct")) return &p_echo_fb_;
        if (!strcmp(key, "echo_wet_pct"))return &p_echo_wet_;
        if (!strcmp(key, "up_db"))       return &p_up_db_;
        if (!strcmp(key, "up_ratio10"))  return &p_up_ratio_;
        if (!strcmp(key, "ex_db"))       return &p_ex_db_;
        if (!strcmp(key, "ex_depth_pct"))return &p_ex_depth_;
        if (!strcmp(key, "gate_floor_pct")) return &p_gate_floor_;
        return nullptr;
    }

    struct MicBlock {
        RenderEngine* owner = nullptr;
        IMMDeviceEnumerator* enumerator = nullptr;
        IAudioClient* client = nullptr;
        IAudioCaptureClient* capture = nullptr;
        HANDLE thread = nullptr;
        ma_linear_resampler resampler{};
        ma_uint8* heap = nullptr;
        bool resampler_ok = false;
        ma_uint32 in_rate = 48000;
        ma_uint32 in_channels = 2;
        ma_format in_format = ma_format_f32;
        std::atomic<float> gain{1.0f};
        std::atomic<bool> zombie{false};
        // Per-mic bounded FIFO (interleaved stereo f32 @ 44.1 kHz).
        std::vector<float> queue;
        size_t used = 0;
        // Noise-suppression state — touched ONLY by the render thread
        // inside mix_emergency_mic (no locking needed): one-pole
        // low-pass state per channel (its complement is the high-pass),
        // a peak envelope follower and the smoothed gate gain.
        float dsp_lp_l = 0.0f;
        float dsp_lp_r = 0.0f;
        float dsp_env = 0.0f;
        float dsp_gate = 1.0f;
        // Compressor stage state (RMS-ish envelope + smoothed gain).
        float comp_env = 0.0f;
        float comp_gain = 1.0f;
        // Limiter stage state (fast peak envelope + smoothed ceiling gain).
        float lim_env = 0.0f;
        float lim_gain = 1.0f;
        // De-esser stage state: split-band (low-pass body + ducked high
        // band), so only the sibilance range ~5 kHz gets reduced.
        float de_lp_l = 0.0f;
        float de_lp_r = 0.0f;
        float de_env = 0.0f;
        float de_gain = 1.0f;
        // Mic EQ state: one-pole shelf states per channel.
        float meq_lp_l = 0.0f;    // low-shelf split (bass)
        float meq_lp_r = 0.0f;
        float meq_hp_l = 0.0f;    // high-shelf split (presence)
        float meq_hp_r = 0.0f;
        // Echo state: stereo delay ring (single tap + feedback).
        float echo_buf[kEchoTaps][2] = {};
        int   echo_pos = 0;
        // Upward-compressor state (envelope + smoothed lift gain).
        float up_env = 0.0f;
        float up_gain = 1.0f;
        // Expander state (envelope + smoothed attenuation gain).
        float ex_env = 0.0f;
        float ex_gain = 1.0f;
    };

    static DWORD WINAPI mic_thread_proc(LPVOID param) {
        auto* m = static_cast<MicBlock*>(param);
        m->owner->emergency_mic_loop(m);
        return 0;
    }

    static void emergency_mic_free(MicBlock* m) {
        if (m->resampler_ok)
            ma_linear_resampler_uninit(&m->resampler, nullptr);
        free(m->heap);
        if (m->capture) m->capture->Release();
        if (m->client) m->client->Release();
        if (m->enumerator) m->enumerator->Release();
        delete m;
    }

    // Capture thread: pull packets, downmix to stereo, resample to the
    // canonical 44.1 kHz and append to the engine FIFO. The FIFO is
    // bounded — when the render thread cannot keep up, the OLDEST voice
    // audio is dropped rather than delayed (live voice must track
    // reality; buffering would only grow the latency the user hears).
    // Membership check: a capture thread lives exactly as long as its
    // block sits in the engine's mic array (works for stopping ONE mic
    // or flushing ALL of them).
    bool mic_is_member(MicBlock* m) {
        EnterCriticalSection(&mic_lock_);
        for (size_t i = 0; i < mic_count_; ++i) {
            if (mics_[i] == m) {
                LeaveCriticalSection(&mic_lock_);
                return true;
            }
        }
        LeaveCriticalSection(&mic_lock_);
        return false;
    }

    // Mic-side mid-stream format recovery: the capture device switched
    // sample rate / format under us (Windows "improve audio" dialogs,
    // driver resets). Re-read the NEW mix format, rebuild the resampler
    // and restart the capture client IN PLACE — the mic block (gain,
    // filter states, queue) is untouched, so the voice keeps flowing
    // through the same chain. Only the endpoint string stays fixed.
    bool mic_reopen(MicBlock* m) {
        if (m->zombie.load() || !mic_is_member(m)) return false;
        if (m->client) { try { m->client->Stop(); } catch (...) {} }
        if (m->capture) { m->capture->Release(); m->capture = nullptr; }
        if (m->client) { m->client->Release(); m->client = nullptr; }
        // The old resampler is re-initialized in place below.
        m->resampler_ok = false;
        for (int attempt = 0; attempt < 8; ++attempt) {
            Sleep(250);             // let the driver finish its switch
            IMMDevice* device = nullptr;
            HRESULT hr = m->enumerator->GetDefaultAudioEndpoint(
                eCapture, eConsole, &device);
            if (SUCCEEDED(hr)) {
                hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL,
                                      nullptr, reinterpret_cast<void**>(&m->client));
                device->Release();
            }
            WAVEFORMATEX* fmt = nullptr;
            if (SUCCEEDED(hr))
                hr = m->client->GetMixFormat(&fmt);
            if (SUCCEEDED(hr) && fmt) {
                m->in_rate = fmt->nSamplesPerSec;
                m->in_channels = fmt->nChannels;
                WAVEFORMATEXTENSIBLE* ext = nullptr;
                if (fmt->wFormatTag == WAVE_FORMAT_EXTENSIBLE)
                    ext = reinterpret_cast<WAVEFORMATEXTENSIBLE*>(fmt);
                ma_format in_fmt = ma_format_unknown;
                if (ext && ext->SubFormat.Data1 == 3)
                    in_fmt = ma_format_f32;
                else if (fmt->wFormatTag == WAVE_FORMAT_IEEE_FLOAT)
                    in_fmt = ma_format_f32;
                else if (ext && ext->SubFormat.Data1 == 1)
                    in_fmt = ma_format_s16;
                else if (fmt->wFormatTag == WAVE_FORMAT_PCM)
                    in_fmt = fmt->wBitsPerSample <= 16 ? ma_format_s16
                                                       : ma_format_s32;
                else if (ext && fmt->wBitsPerSample > 16)
                    in_fmt = ma_format_s32;
                if (in_fmt == ma_format_unknown) in_fmt = ma_format_s16;
                m->in_format = in_fmt;
                ma_linear_resampler_config rc =
                    ma_linear_resampler_config_init(
                        in_fmt, (ma_uint32)kChannels,
                        (ma_uint32)m->in_rate, kSampleRate);
                size_t heap_size = 0;
                if (ma_linear_resampler_get_heap_size(&rc, &heap_size)
                        == MA_SUCCESS && heap_size > 0) {
                    void* heap = realloc(m->heap, heap_size);
                    if (heap &&
                        ma_linear_resampler_init_preallocated(
                            &rc, static_cast<ma_uint8*>(heap), &m->resampler)
                            == MA_SUCCESS) {
                        m->heap = static_cast<ma_uint8*>(heap);
                        m->resampler_ok = true;
                    }
                }
            }
            if (fmt) CoTaskMemFree(fmt);
            if (SUCCEEDED(hr) && m->resampler_ok) {
                hr = m->client->GetService(
                    __uuidof(IAudioCaptureClient),
                    reinterpret_cast<void**>(&m->capture));
                if (SUCCEEDED(hr) && SUCCEEDED(m->client->Start())) {
                    return true;
                }
            }
            // This attempt failed — release and retry.
            if (m->capture) { m->capture->Release(); m->capture = nullptr; }
            if (m->client) { m->client->Release(); m->client = nullptr; }
            m->resampler_ok = false;
        }
        return false;
    }

    void emergency_mic_loop(MicBlock* m) {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        std::vector<float> chn;        // native channel layout
        std::vector<float> stereo;     // downmixed stereo @ in_rate
        std::vector<float> resampled;  // stereo @ 44.1 kHz staging
        while (!m->zombie.load() && mic_is_member(m)) {
            Sleep(8);
            if (m->zombie.load() || !mic_is_member(m)) break;
            if (m->owner && m->owner->mic_paused_.load() > 0) {
                // Paused: drain the device queue WITHOUT mixing, so the
                // driver never silos up and nothing said while paused
                // reaches the output later.
                UINT32 stale = 0;
                while (m->capture->GetNextPacketSize(&stale) == S_OK && stale > 0) {
                    BYTE* sdata = nullptr;
                    UINT32 sframes = 0;
                    DWORD sflags = 0;
                    if (m->capture->GetBuffer(&sdata, &sframes, &sflags, nullptr,
                                              nullptr) != S_OK)
                        break;
                    m->capture->ReleaseBuffer(sframes);
                }
                m->owner->mic_clear();
                continue;
            }
            UINT32 packet = 0;
            if (m->capture->GetNextPacketSize(&packet) != S_OK) {
                // Capture device failing (format switch / reset): heal
                // in place before declaring the mic dead.
                mic_reopen(m);
                continue;
            }
            while (packet > 0) {
                if (m->zombie.load() || !mic_is_member(m)) break;
                BYTE* data = nullptr;
                UINT32 frames = 0;
                DWORD flags = 0;
                if (m->capture->GetBuffer(&data, &frames, &flags, nullptr,
                                          nullptr) != S_OK)
                    break;
                const bool silent = (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0;
                const size_t n = (size_t)frames * m->in_channels;
                chn.resize(n);
                if (data && !silent)
                    ConvertMicFrame(m, data, frames, chn.data());
                else
                    std::memset(chn.data(), 0, n * sizeof(float));
                // N channels -> stereo: average of the left half / right
                // half (mono is duplicated to both sides).
                stereo.resize((size_t)frames * kChannels);
                for (UINT32 i = 0; i < frames; ++i) {
                    const float* row = chn.data() + (size_t)i * m->in_channels;
                    if (m->in_channels == 1) {
                        stereo[(size_t)i * 2] = row[0];
                        stereo[(size_t)i * 2 + 1] = row[0];
                    } else {
                        const ma_uint32 half = m->in_channels / 2;
                        double l = 0.0, r = 0.0;
                        for (ma_uint32 c = 0; c < half; ++c) l += row[c];
                        for (ma_uint32 c = half; c < m->in_channels; ++c) r += row[c];
                        stereo[(size_t)i * 2] = (float)(l / (double)half);
                        stereo[(size_t)i * 2 + 1] =
                            (float)(r / (double)(m->in_channels - half));
                    }
                }
                // Room check FIRST: the queue is bounded.
                EnterCriticalSection(&mic_lock_);
                const size_t room = kMicRingFrames > m->used
                    ? kMicRingFrames - m->used : 0;
                LeaveCriticalSection(&mic_lock_);
                if (room > 0 && frames > 0) {
                    resampled.resize((size_t)room * kChannels);
                    ma_uint64 in_frames = frames;
                    ma_uint64 out_room = room;
                    float* dst = resampled.data();
                    ma_uint64 produced_total = 0;
                    while (in_frames > 0 && out_room > 0) {
                        ma_uint64 take = in_frames;
                        ma_uint64 want = out_room;
                        const ma_result mr = ma_linear_resampler_process_pcm_frames(
                            &m->resampler, stereo.data(), &take, dst, &want);
                        if (mr != MA_SUCCESS) break;
                        dst += (size_t)want * kChannels;
                        produced_total += want;
                        out_room -= want;
                        in_frames -= take;
                        if (take == 0 && want == 0) break;   // resampler stall
                    }
                    if (produced_total > 0) {
                        EnterCriticalSection(&mic_lock_);
                        // Re-check under the lock: the render thread may
                        // have drained while we resampled.
                        const size_t space = kMicRingFrames > m->used
                            ? kMicRingFrames - m->used : 0;
                        const size_t put = (size_t)produced_total < space
                            ? (size_t)produced_total : space;
                        if (put > 0) {
                            m->queue.insert(m->queue.end(), resampled.data(),
                                            resampled.data() + put * kChannels);
                            m->used += put;
                        }
                        LeaveCriticalSection(&mic_lock_);
                    }
                }
                m->capture->ReleaseBuffer(frames);
                if (m->capture->GetNextPacketSize(&packet) != S_OK) {
                    mic_reopen(m);
                    break;
                }
            }
        }
        CoUninitialize();
    }

    // Native mix-format frame -> f32 samples (in_channels per frame).
    static void ConvertMicFrame(MicBlock* m, const BYTE* src, UINT32 frames,
                                float* out) {
        const size_t total = (size_t)frames * m->in_channels;
        if (m->in_format == ma_format_f32) {
            const float* f = reinterpret_cast<const float*>(src);
            for (size_t i = 0; i < total; ++i) out[i] = f[i];
            return;
        }
        if (m->in_format == ma_format_s16) {
            const int16_t* s = reinterpret_cast<const int16_t*>(src);
            for (size_t i = 0; i < total; ++i) out[i] = s[i] / 32768.0f;
            return;
        }
        // s32 (covers 24-in-32 too; the low 8 bits are padding noise at
        // -192 dB — inaudible).
        const int32_t* s32 = reinterpret_cast<const int32_t*>(src);
        for (size_t i = 0; i < total; ++i) out[i] = s32[i] / 2147483648.0f;
    }

    // Called from the RENDER THREAD during copy-out. Mixes every live
    // mic's queued frames into dst (already volume-scaled music) — same
    // buffer, same lock pass, so two voices can never tear between
    // device buffers. Each mic consumes its OWN FIFO; one running dry
    // never stalls the other.
    void mix_emergency_mic(BYTE* dst, size_t frames) {
        if (mic_active_.load() <= 0) return;
        if (mic_paused_.load() > 0) return;
        EnterCriticalSection(&mic_lock_);
        auto* out = reinterpret_cast<int16_t*>(dst);
        const int dsp_mode = mic_dsp_mode_.load();
        // Noise-suppression tuning @ 44.1 kHz. Defaults reproduce the
        // mode-only releases exactly (gentle ≈ 88 Hz, strong ≈ 132 Hz);
        // the parameter store lets the UI override each value live.
        int sup_hp = p_sup_hp_.load();
        int sup_gate = p_sup_gate_.load();
        if (dsp_mode == 2 && sup_hp == 90) sup_hp = 132;   // legacy strong
        if (dsp_mode == 2 && sup_gate == -45) sup_gate = -38;
        const float hp_c      = sup_hp * 0.000143f;   // Hz -> one-pole coef
        const float gate_thr  = powf(10.0f, sup_gate / 20.0f);
        const float env_decay = 0.999849f;             // ~150 ms release of the envelope
        const float attack_c  = dsp_mode == 2 ? 0.0226f : 0.0113f; // ~10/20 ms
        const float release_c = 0.000378f;             // ~120 ms
        const int comp_mode = mic_comp_mode_.load();
        const int lim_mode = mic_limiter_mode_.load();
        const int de_mode = mic_deesser_mode_.load();
        const int meq_mode = mic_miceq_mode_.load();
        const int echo_mode = mic_echo_mode_.load();
        const int gainst_mode = mic_gainst_mode_.load();
        const int upcomp_mode = mic_upcomp_mode_.load();
        const int expand_mode = mic_expand_mode_.load();
        // De-esser tuning: split frequency and threshold from the store
        // (legacy defaults ≈ 3.5/4.4 kHz split, −20/−24 dB threshold).
        int de_hz = p_de_hz_.load();
        int de_db = p_de_db_.load();
        if (de_mode == 2 && de_hz == 3500) de_hz = 4400;
        if (de_mode == 2 && de_db == -20) de_db = -24;
        const float de_lp_c    = de_hz * 0.000143f;
        const float de_thr     = powf(10.0f, de_db / 20.0f);
        const float de_smooth  = 0.05f;       // ~1 ms — must catch fast S bursts
        const float de_release = 0.9990f;     // ~22 ms
        // Mic EQ tuning: shelf depths from the store; strength only
        // fills the defaults when the user has not touched the sliders.
        int eq_lo = p_eq_low_db_.load();
        int eq_hi = p_eq_high_db_.load();
        if (meq_mode == 2 && eq_lo == -3) eq_lo = -6;
        if (meq_mode == 2 && eq_hi == 3) eq_hi = 6;
        const float meq_lp_c   = 0.018f;      // ~125 Hz low-shelf split
        const float meq_hp_c   = 0.22f;       // ~1.8 kHz high-shelf split
        const float meq_low_g  = powf(10.0f, eq_lo / 20.0f);
        const float meq_high_g = powf(10.0f, eq_hi / 20.0f);
        // Echo tuning: delay/feedback/wet all live-adjustable. The dry
        // voice always dominates (wet capped at 60%).
        int echo_ms = p_echo_ms_.load();
        if (echo_ms < 10) echo_ms = 10;
        if (echo_ms > 1000) echo_ms = 1000;
        const float echo_fb    = p_echo_fb_.load() / 100.0f;
        const float echo_wet   = p_echo_wet_.load() / 100.0f;
        // Gain trim: dB straight from the store (legacy: +6/+12).
        const float gainst_mult =
            powf(10.0f, p_gain_db_.load() / 20.0f);
        // Upward compressor: threshold/ratio from the store; a max-boost
        // cap keeps near-silence from being amplified into a noise fest.
        int up_db = p_up_db_.load();
        int up_r = p_up_ratio_.load();
        if (upcomp_mode == 2 && up_db == -40) up_db = -30;
        if (upcomp_mode == 2 && up_r == 15) up_r = 30;
        const float up_thr     = powf(10.0f, up_db / 20.0f);
        const float up_ratio   = up_r / 10.0f;
        const float up_max     = 3.9811f;
        const float up_attack  = 0.5f;
        const float up_release = 0.9995f;
        const float up_smooth  = 0.012f;
        // Expander: threshold + depth from the store (depth = how far
        // below the level the floor sits when the signal is quiet).
        int ex_db = p_ex_db_.load();
        if (expand_mode == 2 && ex_db == -45) ex_db = -40;
        const float ex_thr     = powf(10.0f, ex_db / 20.0f);
        const float ex_floor   = 1.0f - p_ex_depth_.load() / 100.0f;
        const float gate_floor_ = p_gate_floor_.load() / 100.0f;
        // Echo delay in FRAMES (ms -> 44.1 kHz frames, inside the ring).
        int echo_ms_v = p_echo_ms_.load();
        if (echo_ms_v < 10) echo_ms_v = 10;
        if (echo_ms_v > 1000) echo_ms_v = 1000;
        const int echo_len = echo_ms_v * 44100 / 1000;
        const float ex_release = 0.9990f;
        const float ex_smooth  = 0.010f;
        // Legacy bypass ONLY when every chain stage is off — a lone
        // Compressor or Limiter (with Suppression off) must still run.
        const bool chain = dsp_mode || comp_mode || lim_mode || de_mode
                         || meq_mode || echo_mode || gainst_mode
                         || upcomp_mode || expand_mode;
        // Compressor tuning: threshold/ratio from the store (legacy
        // gentle −24 dB @ 3.5:1, strong −30 dB @ 6:1).
        int comp_db = p_comp_db_.load();
        int comp_r = p_comp_ratio_.load();
        if (comp_mode == 2 && comp_db == -24) comp_db = -30;
        if (comp_mode == 2 && comp_r == 35) comp_r = 60;
        const float comp_thr     = powf(10.0f, comp_db / 20.0f);
        const float comp_ratio   = comp_r / 10.0f;
        const float comp_attack  = 0.5f;      // follower jumps fast to peaks
        const float comp_release = comp_mode == 2 ? 0.9995f : 0.9997f;
        const float comp_smooth  = comp_mode == 2 ? 0.02f : 0.012f;
        // Limiter tuning: ceiling dB from the store (legacy −0.45/−1.4 dB).
        int lim_db = p_lim_db_.load();
        if (lim_mode == 2 && lim_db == -1) lim_db = -1;
        const float lim_ceiling = powf(10.0f, lim_db / 20.0f);
        const float lim_release = 0.9990f;    // ~45 ms
        const float lim_smooth  = lim_mode == 2 ? 0.05f : 0.03f;
        for (size_t mi = 0; mi < mic_count_; ++mi) {
            MicBlock* m = mics_[mi];
            if (!m || m->zombie.load() || m->used == 0) continue;
            const float g = m->gain.load();
            const size_t take = m->used < frames ? m->used : frames;
            if (!chain) {
                // Legacy path — byte-identical to pre-DSP releases.
                const size_t samples = take * kChannels;
                for (size_t i = 0; i < samples; ++i) {
                    double v = (double)out[i]
                             + (double)m->queue[i] * g * 32768.0;
                    if (v > 32767.0) v = 32767.0;
                    if (v < -32768.0) v = -32768.0;
                    out[i] = static_cast<int16_t>(v);
                }
            } else {
                // OBS-style filter chain, all native, per mic:
                //   1. Noise Suppression (HPF + downward expander gate)
                //   2. Compressor (envelope follower, smoothed gain)
                //   3. Limiter (fast ceiling guard)
                // then the normal gain+mix. Stage state lives in the
                // MicBlock, touched only on this render thread.
                for (size_t f = 0; f < take; ++f) {
                    float l = m->queue[f * kChannels];
                    float r = m->queue[f * kChannels + 1];
                    // 0) Gain (input trim, dB): applied FIRST so the
                    // downstream compressor/limiter still guard the
                    // boosted peaks.
                    if (gainst_mode) {
                        l *= gainst_mult;
                        r *= gainst_mult;
                    }
                    // 1) Noise Suppression (dsp_mode 1 gentle / 2 strong)
                    if (dsp_mode) {
                        m->dsp_lp_l += hp_c * (l - m->dsp_lp_l); l -= m->dsp_lp_l;
                        m->dsp_lp_r += hp_c * (r - m->dsp_lp_r); r -= m->dsp_lp_r;
                        const float al = l < 0 ? -l : l;
                        const float ar = r < 0 ? -r : r;
                        const float peak = al > ar ? al : ar;
                        m->dsp_env = peak > m->dsp_env
                            ? peak : m->dsp_env * env_decay;
                        const float target = m->dsp_env > gate_thr
                            ? 1.0f : gate_floor_;
                        const float c = target > m->dsp_gate ? attack_c
                                                             : release_c;
                        m->dsp_gate += (target - m->dsp_gate) * c;
                        l *= m->dsp_gate;
                        r *= m->dsp_gate;
                    }
                    // 2) Compressor (mic_comp_mode_ 1 gentle / 2 strong):
                    // evens out loud/soft phrases. Envelope + smoothed
                    // gain, threshold/ratio by strength.
                    if (comp_mode) {
                        const float cl = l < 0 ? -l : l;
                        const float cr = r < 0 ? -r : r;
                        const float cpeak = cl > cr ? cl : cr;
                        m->comp_env = cpeak > m->comp_env
                            ? cpeak * comp_attack
                            : m->comp_env * comp_release;
                        const float above = m->comp_env / comp_thr;
                        const float target = above > 1.0f
                            ? (1.0f + (above - 1.0f) / comp_ratio)
                            : 1.0f;
                        m->comp_gain += (target - m->comp_gain) * comp_smooth;
                        l *= m->comp_gain;
                        r *= m->comp_gain;
                    }
                    // 3) Limiter (mic_limiter_mode_ 1 soft / 2 hard):
                    // transparent when under the ceiling, clamps peaks.
                    if (lim_mode) {
                        const float ll = l < 0 ? -l : l;
                        const float lr = r < 0 ? -r : r;
                        const float lpeak = ll > lr ? ll : lr;
                        m->lim_env = lpeak > m->lim_env
                            ? lpeak
                            : m->lim_env * lim_release;
                        const float target = m->lim_env > lim_ceiling
                            ? lim_ceiling / m->lim_env
                            : 1.0f;
                        m->lim_gain += (target - m->lim_gain) * lim_smooth;
                        l *= m->lim_gain;
                        r *= m->lim_gain;
                        if (lim_mode == 2) {   // hard: never exceed ceiling
                            if (l > lim_ceiling) l = lim_ceiling;
                            if (l < -lim_ceiling) l = -lim_ceiling;
                            if (r > lim_ceiling) r = lim_ceiling;
                            if (r < -lim_ceiling) r = -lim_ceiling;
                        }
                    }
                    // 4) De-esser: split off the ~5 kHz band; when its
                    // envelope pokes over the threshold (an "S" burst),
                    // duck ONLY that band — the voice body is untouched.
                    if (de_mode) {
                        m->de_lp_l += de_lp_c * (l - m->de_lp_l);
                        m->de_lp_r += de_lp_c * (r - m->de_lp_r);
                        float high_l = l - m->de_lp_l;
                        float high_r = r - m->de_lp_r;
                        const float hl = high_l < 0 ? -high_l : high_l;
                        const float hr = high_r < 0 ? -high_r : high_r;
                        const float hpeak = hl > hr ? hl : hr;
                        m->de_env = hpeak > m->de_env
                            ? hpeak
                            : m->de_env * de_release;
                        const float target = m->de_env > de_thr
                            ? de_thr / m->de_env
                            : 1.0f;
                        m->de_gain += (target - m->de_gain) * de_smooth;
                        l = m->de_lp_l + high_l * m->de_gain;
                        r = m->de_lp_r + high_r * m->de_gain;
                    }
                    // 5) Mic EQ: shelf tilt for a broadcast-voice shape —
                    // trims boom below ~125 Hz, lifts presence above
                    // ~1.8 kHz. Strength sets the shelf depths.
                    if (meq_mode) {
                        const float low_l = m->meq_lp_l + meq_lp_c * (l - m->meq_lp_l);
                        const float low_r = m->meq_lp_r + meq_lp_c * (r - m->meq_lp_r);
                        const float high_l = m->meq_hp_l + meq_hp_c * (l - m->meq_hp_l);
                        const float high_r = m->meq_hp_r + meq_hp_c * (r - m->meq_hp_r);
                        l = l - low_l * (1.0f - meq_low_g)
                              + high_l * (meq_high_g - 1.0f);
                        r = r - low_r * (1.0f - meq_low_g)
                              + high_r * (meq_high_g - 1.0f);
                        m->meq_lp_l = low_l;  m->meq_lp_r = low_r;
                        m->meq_hp_l = high_l; m->meq_hp_r = high_r;
                    }
                    // 7) Upward compressor: quiet parts get lifted UP
                    // toward the threshold (whisper stays audible on
                    // air). Capped so silence is never amplified.
                    if (upcomp_mode) {
                        const float ul = l < 0 ? -l : l;
                        const float ur = r < 0 ? -r : r;
                        const float upeak = ul > ur ? ul : ur;
                        m->up_env = upeak > m->up_env
                            ? upeak * up_attack
                            : m->up_env * up_release;
                        float target = 1.0f;
                        if (m->up_env > 1e-4f && m->up_env < up_thr) {
                            const float lifted =
                                up_thr - (up_thr - m->up_env) / up_ratio;
                            target = lifted / m->up_env;
                            if (target > up_max) target = up_max;
                        }
                        m->up_gain += (target - m->up_gain) * up_smooth;
                        l *= m->up_gain;
                        r *= m->up_gain;
                    }
                    // 8) Expander: below the threshold the level is
                    // pulled down along a soft slope (noise floor between
                    // words disappears; strong ≈ a musical gate).
                    if (expand_mode) {
                        const float xl = l < 0 ? -l : l;
                        const float xr = r < 0 ? -r : r;
                        const float xpeak = xl > xr ? xl : xr;
                        m->ex_env = xpeak > m->ex_env
                            ? xpeak
                            : m->ex_env * ex_release;
                        float target = 1.0f;
                        if (m->ex_env < ex_thr) {
                            target = ex_floor
                                + (1.0f - ex_floor) * (m->ex_env / ex_thr);
                        }
                        m->ex_gain += (target - m->ex_gain) * ex_smooth;
                        l *= m->ex_gain;
                        r *= m->ex_gain;
                    }
                    // 6) Echo: stereo delay tap with feedback (radio
                    // station-ID effect). The delay length in FRAMES is
                    // live-adjustable (echo_ms, clamped to the ring).
                    if (echo_mode) {
                        if (m->echo_pos >= echo_len) m->echo_pos = 0;
                        const int tap = echo_len - 1 - m->echo_pos;
                        const float dl = m->echo_buf[tap][0];
                        const float dr = m->echo_buf[tap][1];
                        l = l + dl * echo_wet;
                        r = r + dr * echo_wet;
                        m->echo_buf[tap][0] = l * echo_fb;
                        m->echo_buf[tap][1] = r * echo_fb;
                        if (++m->echo_pos >= echo_len) m->echo_pos = 0;
                    }
                    const float vg = g;
                    double vl = (double)out[f * kChannels]
                              + (double)l * vg * 32768.0;
                    double vr = (double)out[f * kChannels + 1]
                              + (double)r * vg * 32768.0;
                    if (vl > 32767.0) vl = 32767.0;
                    if (vl < -32768.0) vl = -32768.0;
                    if (vr > 32767.0) vr = 32767.0;
                    if (vr < -32768.0) vr = -32768.0;
                    out[f * kChannels] = static_cast<int16_t>(vl);
                    out[f * kChannels + 1] = static_cast<int16_t>(vr);
                }
            }
            const size_t consumed = take * kChannels;
            m->queue.erase(m->queue.begin(),
                           m->queue.begin() + (ptrdiff_t)consumed);
            m->used -= take;
        }
        LeaveCriticalSection(&mic_lock_);
    }

    void mic_clear() {
        EnterCriticalSection(&mic_lock_);
        for (size_t i = 0; i < mic_count_; ++i) {
            if (mics_[i]) {
                mics_[i]->queue.clear();
                mics_[i]->used = 0;
            }
        }
        LeaveCriticalSection(&mic_lock_);
    }

public:
    // Mid-stream format recovery entry point (public so the test export
    // can exercise the exact path a sample-rate/format switch takes).
    // The implementation (heal_device_impl) lives in the private section.
    bool heal_device() { return heal_device_impl(); }

    // Thread-safe heal REQUEST: only the render thread may tear down and
    // re-open the device (it owns client_/render_). Callers just set the
    // flag and wake the loop; the render thread performs the heal itself
    // at the top of its next iteration — the same ordering a real
    // device-failure path produces.
    void request_heal() {
        if (!running_.load()) return;
        heal_request_.store(1);
        SetEvent(buffer_event_);
    }

    static DWORD WINAPI thread_proc(LPVOID param) {
        static_cast<RenderEngine*>(param)->render_loop();
        return 0;
    }

    void render_loop() {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        HANDLE wait[2] = { shutdown_event_, buffer_event_ };
        int device_fails = 0;   // consecutive device-op failures
        while (running_.load()) {
            process_flush_request();
            // A queued device-format recovery runs here, on the render
            // thread that owns the device — never from other threads.
            if (heal_request_.exchange(0)) {
                if (!heal_device_impl()) {
                    running_.store(false);   // device truly gone
                    break;
                }
            }
            // With the emergency mic live the device must be fed on a
            // realtime cadence even when nothing else wakes us (music
            // stopped or paused) — otherwise the voice queue overflows
            // and speech is dropped instead of heard.
            const DWORD wake_ms = mic_active_.load() > 0 ? 20 : 500;
            const DWORD w = WaitForMultipleObjects(2, wait, FALSE, wake_ms);
            if (w == WAIT_OBJECT_0 || !running_.load()) break;   // shutdown
            if (!render_) break;
            // A removed/invalidated device makes every WASAPI call fail
            // forever while the event never fires again. Swallowing those
            // errors would hang playback in silence with is_running() still
            // true — instead, count consecutive failures and shut the engine
            // down so the player can detect the loss and migrate endpoints.
            const bool was_paused = paused_.load();
            bool op_failed = false;
            // Paused: hand the device silence instead of draining the ring.
            // emitted_frames() stays frozen because neither frames_written_
            // nor buffered_ changes while paused.
            if (was_paused) {
                UINT32 padding = 0;
                if (SUCCEEDED(client_->GetCurrentPadding(&padding))) {
                    const UINT32 space =
                        buffer_frames_ > padding ? buffer_frames_ - padding : 0;
                    if (space) {
                        BYTE* dst = nullptr;
                        if (SUCCEEDED(render_->GetBuffer(space, &dst)) && dst) {
                            std::memset(dst, 0,
                                        static_cast<size_t>(space) * kChannels * 2);
                            // Paused music must not mute an emergency voice:
                            // the announcer pauses the deck and still speaks.
                            mix_emergency_mic(dst, space);
                            master_process(dst_as_int16(dst), space * kChannels);
                            render_->ReleaseBuffer(space, 0);
                        } else {
                            op_failed = true;
                        }
                    }
                } else {
                    op_failed = true;
                }
                device_fails = op_failed ? device_fails + 1 : 0;
                if (device_fails >= kMaxDeviceFails) {
                    // Device op failing repeatedly: usually a mid-stream
                    // sample-rate/format switch. Try to re-open in place
                    // FIRST (seamless); dying is the LAST resort.
                    if (heal_device()) {
                        device_fails = 0;
                        continue;
                    }
                    running_.store(false);   // truly gone — engine down
                    break;
                }
                continue;  // failures fall through to the next event wait
            }
            // Drain whatever fits into the device buffer.
            for (int pass = 0; pass < 8; ++pass) {
                UINT32 padding = 0;
                if (FAILED(client_->GetCurrentPadding(&padding))) {
                    op_failed = true;
                    break;
                }
                UINT32 space = buffer_frames_ > padding ? buffer_frames_ - padding : 0;
                if (!space) break;
                EnterCriticalSection(&lock_);
                size_t avail = buffered_;
                if (avail > space) avail = space;
                if (!avail) {
                    LeaveCriticalSection(&lock_);
                    // Empty ring but the emergency mic is live: keep
                    // submitting buffers so the voice keeps flowing over
                    // musical silence (a mic segment IS the programme —
                    // breaking here would mute the announcer).
                    if (mic_active_.load() > 0) {
                        BYTE* sdst = nullptr;
                        if (SUCCEEDED(render_->GetBuffer(space, &sdst)) && sdst) {
                            std::memset(sdst, 0,
                                        static_cast<size_t>(space) * kChannels * 2);
                            mix_emergency_mic(sdst, space);
                            master_process(dst_as_int16(sdst), space * kChannels);
                            render_->ReleaseBuffer(space, 0);
                        }
                    }
                    break;
                }
                BYTE* dst = nullptr;
                if (FAILED(render_->GetBuffer(static_cast<UINT32>(avail), &dst))) {
                    LeaveCriticalSection(&lock_);
                    op_failed = true;
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
                // EQ after volume, before the buffer goes to the device.
                eq_process(dst_as_int16(dst), samples);
                // Emergency mic mixes into the SAME device buffer pass —
                // music + voice share one output clock (zero added latency).
                mix_emergency_mic(dst, avail);
                master_process(dst_as_int16(dst), avail * kChannels);
                render_->ReleaseBuffer(static_cast<UINT32>(avail), 0);
                if (avail < space) break;   // ring drained
            }
            device_fails = op_failed ? device_fails + 1 : 0;
            if (device_fails >= kMaxDeviceFails) {
                // Same recovery contract as the paused path above: re-open
                // the device with the new format before giving up.
                if (heal_device()) {
                    device_fails = 0;
                    continue;
                }
                running_.store(false);   // truly gone — engine down
                break;
            }
        }
        CoUninitialize();
    }

    static int16_t* dst_as_int16(BYTE* dst) {
        return reinterpret_cast<int16_t*>(dst);
    }

    static void copy_scaled(BYTE* dst, const int16_t* src, size_t total_samples, float vol) {
        // Only the exact 1.0 case skips scaling — a boost (>1.0) must go
        // through the saturating scaler below or it would be a silent no-op.
        if (vol > 0.999f && vol < 1.001f) {
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

    // Mid-stream format recovery: the device switched sample rate / bit
    // depth / format under us (or the driver reset itself). Re-open the
    // endpoint IN PLACE on this (render) thread with the new mix format —
    // the ring buffer is untouched, so buffered music keeps flowing
    // seamlessly the moment the new stream starts. Only when even this
    // gives up does the engine report failure (legacy migrate path).
    bool heal_device_impl() {
        teardown_device();          // stops the old client, keeps the ring
        for (int attempt = 0; attempt < 8; ++attempt) {
            Sleep(250);             // let the driver finish its switch
            if (init_device(cur_endpoint_.empty()
                                ? nullptr : cur_endpoint_.c_str())) {
                return true;
            }
            teardown_device();
        }
        return false;
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

    // ── Built-in source state (see SourceBlock above) ──
    SourceBlock* src_ = nullptr;
    CRITICAL_SECTION src_lock_;
    std::atomic<bool> src_running_{false};
    std::atomic<bool> src_eof_{false};
    std::atomic<bool> src_failed_{false};
    std::atomic<uint64_t> src_frames_read_{0};
    std::atomic<uint64_t> src_base_{0};
    std::atomic<uint64_t> src_pending_seek_{UINT64_MAX};
    // EQ state (guarded by lock_ for set; eq_on_ read on render thread)
    struct Biquad { double b0, b1, b2, a1, a2; };
    Biquad c_b_{1, 0, 0, 0, 0}, c_m_{1, 0, 0, 0, 0}, c_t_{1, 0, 0, 0, 0};
    std::atomic<bool> eq_on_{false};
    double eq_bass_db_ = 0.0, eq_mid_db_ = 0.0, eq_treble_db_ = 0.0;
    double x1l_ = 0, x2l_ = 0, y1l_ = 0, y2l_ = 0;   // (legacy, unused)
    double x1r_ = 0, x2r_ = 0, y1r_ = 0, y2r_ = 0;   // (legacy, unused)
    // Per-stage biquad state: [stage][x1, x2, y1, y2] per channel.
    double st_l_[3][4] = {{0}};
    double st_r_[3][4] = {{0}};

    static constexpr double kPi = 3.14159265358979323846;

    static void design_lowshelf(double db, double f0, Biquad& c) {
        double A = std::pow(10.0, db / 40.0);
        double w0 = 2.0 * kPi * f0 / (double)kSampleRate;
        double cw = std::cos(w0), sw = std::sin(w0);
        double S = 1.0;               // shelf slope
        double alpha = (sw / 2.0) * std::sqrt((A + 1.0 / A) * (S - 1.0) + 2.0);
        double two_sqrtA_alpha = 2.0 * std::sqrt(A) * alpha;
        double a0 = (A + 1.0) + (A - 1.0) * cw + two_sqrtA_alpha;
        c.b0 = (A * ((A + 1.0) - (A - 1.0) * cw + two_sqrtA_alpha)) / a0;
        c.b1 = (2.0 * A * ((A - 1.0) - (A + 1.0) * cw)) / a0;
        c.b2 = (A * ((A + 1.0) - (A - 1.0) * cw - two_sqrtA_alpha)) / a0;
        c.a1 = (-2.0 * ((A - 1.0) + (A + 1.0) * cw)) / a0;
        c.a2 = ((A + 1.0) + (A - 1.0) * cw - two_sqrtA_alpha) / a0;
    }
    static void design_highshelf(double db, double f0, Biquad& c) {
        double A = std::pow(10.0, db / 40.0);
        double w0 = 2.0 * kPi * f0 / (double)kSampleRate;
        double cw = std::cos(w0), sw = std::sin(w0);
        double S = 1.0;
        double alpha = (sw / 2.0) * std::sqrt((A + 1.0 / A) * (S - 1.0) + 2.0);
        double two_sqrtA_alpha = 2.0 * std::sqrt(A) * alpha;
        double a0 = (A + 1.0) - (A - 1.0) * cw + two_sqrtA_alpha;
        c.b0 = (A * ((A + 1.0) + (A - 1.0) * cw + two_sqrtA_alpha)) / a0;
        c.b1 = (-2.0 * A * ((A - 1.0) + (A + 1.0) * cw)) / a0;
        c.b2 = (A * ((A + 1.0) + (A - 1.0) * cw - two_sqrtA_alpha)) / a0;
        c.a1 = (2.0 * ((A - 1.0) - (A + 1.0) * cw)) / a0;
        c.a2 = ((A + 1.0) - (A - 1.0) * cw - two_sqrtA_alpha) / a0;
    }
    static void design_peaking(double db, double f0, double Q, Biquad& c) {
        double A = std::pow(10.0, db / 40.0);
        double w0 = 2.0 * kPi * f0 / (double)kSampleRate;
        double cw = std::cos(w0), sw = std::sin(w0);
        double alpha = sw / (2.0 * Q);
        double a0 = 1.0 + alpha / A;
        c.b0 = (1.0 + alpha * A) / a0;
        c.b1 = (-2.0 * cw) / a0;
        c.b2 = (1.0 - alpha * A) / a0;
        c.a1 = (-2.0 * cw) / a0;
        c.a2 = (1.0 - alpha / A) / a0;
    }

    std::atomic<bool> running_{false};
    std::atomic<bool> paused_{false};
    HANDLE thread_ = nullptr;
    HANDLE shutdown_event_ = nullptr;
    HANDLE buffer_event_ = nullptr;
    IAudioClient* client_ = nullptr;
    IAudioRenderClient* render_ = nullptr;
    UINT32 buffer_frames_ = 0;
    std::atomic<bool> flush_requested_{false};

    // ── Emergency-mic live mix-in state ──
    // Multi-mic: each emergency_mic_start() call ADDS one capture to the
    // mix (dual mic segments = two announcers in one segment). Every mic
    // owns a bounded FIFO of interleaved stereo f32 frames at 44.1 kHz,
    // written by its capture thread and drained by the render thread at
    // copy-out. Bounded = a mic can never lag: when the render thread
    // falls behind, the OLDEST voice audio is dropped — a live voice must
    // track reality, not a growing delay.
    static constexpr size_t kMicRingFrames = kSampleRate / 2;   // 0.5 s stereo
    static constexpr size_t kMaxMics = 4;
    // Transport pause for the live-mic mix (render_pause does not cover
    // the capture side): while set, capture frames are drained and
    // dropped and the render thread skips mixing — resumed audio is
    // LIVE, never a burst of everything said while paused.
    std::atomic<int> mic_paused_{0};
    // Mic noise-suppression mode (0 off / 1 gentle / 2 strong). Defaults
    // to OFF: with the feature off, the mix path is byte-identical to
    // every release before it (Ultimate ground rule).
    std::atomic<int> mic_dsp_mode_{0};
    // Mic filter-chain stages (OBS-style): per-stage strength, 0 = off.
    // All default OFF so the legacy path stays untouched unless the user
    // enables filters in Settings.
    std::atomic<int> mic_comp_mode_{0};   // 0 off / 1 gentle / 2 strong
    std::atomic<int> mic_limiter_mode_{0}; // 0 off / 1 soft / 2 hard
    std::atomic<int> mic_deesser_mode_{0}; // 0 off / 1 gentle / 2 strong
    std::atomic<int> mic_miceq_mode_{0};   // 0 off / 1 gentle / 2 strong
    std::atomic<int> mic_echo_mode_{0};    // 0 off / 1 gentle / 2 strong
    std::atomic<int> mic_gainst_mode_{0};  // 0 off / 1 +6 dB / 2 +12 dB
    std::atomic<int> mic_upcomp_mode_{0};  // 0 off / 1 gentle / 2 strong
    std::atomic<int> mic_expand_mode_{0};  // 0 off / 1 gentle / 2 strong
    // Set by request_heal(); consumed by the render loop at the top of
    // each iteration (only the render thread touches the device).
    std::atomic<int> heal_request_{0};
    // Endpoint the engine was last started on — re-opened verbatim by
    // the mid-stream format recovery (heal_device).
    std::wstring cur_endpoint_;
    CRITICAL_SECTION mic_lock_;
    MicBlock* mics_[kMaxMics] = {};
    size_t mic_count_ = 0;             // guarded by mic_lock_
    std::atomic<int> mic_active_{0};   // cheap "any mic live" for the render loop
};

PyObject* render_new(PyObject*, PyObject* args) {
    (void)args;
    auto* engine = new (std::nothrow) RenderEngine();
    if (!engine) PyErr_NoMemory();
    return PyCapsule_New(engine, "native_audio_render.Engine",
                         [](PyObject* cap) {
                             auto* e = static_cast<RenderEngine*>(
                                 PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
                             if (!e) return;
                             if (!e->shutdown_for_delete()) {
                                 // A source thread is still running inside this
                                 // engine — freeing it would be a use-after-free
                                 // (the crash behind the miniaudio divide-by-
                                 // zero). Leak the engine instead; the thread
                                 // exits on its own and the block is never freed.
                                 return;
                             }
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

PyObject* render_set_eq(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    double bass = 0.0, mid = 0.0, treble = 0.0;
    if (!PyArg_ParseTuple(args, "Odd|dd", &cap, &bass, &mid, &treble)) {
        return nullptr;
    }
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    if (bass < -12.0) bass = -12.0;
    if (bass > 12.0) bass = 12.0;
    if (mid < -12.0) mid = -12.0;
    if (mid > 12.0) mid = 12.0;
    if (treble < -12.0) treble = -12.0;
    if (treble > 12.0) treble = 12.0;
    e->set_eq(bass, mid, treble);
    Py_RETURN_NONE;
}

PyObject* render_get_eq(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    double g[3];
    e->eq_gains(g);
    return Py_BuildValue("(ddd)", g[0], g[1], g[2]);
}

// Debug/self-test: run the engine's own biquad cascade over 0.5 s of a
// 60 Hz sine (no device needed) and return the output peak. With bass
// +12 dB the steady-state peak must exceed the input amplitude x3.
PyObject* render_eq_selftest(PyObject*, PyObject*) {
    RenderEngine e;
    e.set_eq(12.0, 0.0, 0.0);
    const size_t frames = (size_t)kSampleRate / 2;
    std::vector<int16_t> buf(frames * 2);
    for (size_t f = 0; f < frames; ++f) {
        int16_t v = (int16_t)(12000.0 * std::sin(
            2.0 * RenderEngine::kPiPublic * 60.0 * (double)f / (double)kSampleRate));
        buf[f * 2] = v;
        buf[f * 2 + 1] = v;
    }
    e.eq_process(buf.data(), buf.size());
    int peak = 0;
    for (int16_t s : buf) {
        int a = s < 0 ? -s : s;
        if (a > peak) peak = a;
    }
    return PyLong_FromLong(peak);
}

// ── Master broadcast chain API (0926.2 P1) ─────────────────────────────
// render_master_set(engine, mode, bass_db, mid_db, treb_db,
//                   comp_thresh_db, comp_ratio_x10, limit_ceiling_db)
// mode: 0 = chain OFF (legacy bytes), 1 gentle, 2 strong
PyObject* render_master_set(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    double bass = 0, mid = 0, treb = 0, comp_t = -24, ceil_db = -1;
    int comp_r = 30;
    if (!PyArg_ParseTuple(args, "Oiddddid", &cap, &mode, &bass, &mid,
                          &treb, &comp_t, &comp_r, &ceil_db))
        return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    if (mode < 0) mode = 0;
    if (mode > 2) mode = 2;
    if (bass < -12) bass = -12; if (bass > 12) bass = 12;
    if (mid < -12) mid = -12;   if (mid > 12) mid = 12;
    if (treb < -12) treb = -12; if (treb > 12) treb = 12;
    if (comp_t < -48) comp_t = -48; if (comp_t > -6) comp_t = -6;
    if (ceil_db < -6) ceil_db = -6; if (ceil_db > 0) ceil_db = 0;
    if (comp_r < 10) comp_r = 10;   if (comp_r > 80) comp_r = 80;
    e->master_.mode.store(mode);
    e->master_.eq_bass_db.store((int)std::lround(bass));
    e->master_.eq_mid_db.store((int)std::lround(mid));
    e->master_.eq_treb_db.store((int)std::lround(treb));
    e->master_.comp_thresh_db.store((int)std::lround(comp_t));
    e->master_.comp_ratio_x10.store(comp_r);
    e->master_.limit_ceiling_db.store((int)std::lround(ceil_db));
    e->master_.limit_on.store(ceil_db < 0.0);   // explicit ceiling = limiter on
    Py_RETURN_NONE;
}

// render_master_get(engine) -> (mode, bass, mid, treb, comp_t, ratio_x10, ceil)
PyObject* render_master_get(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return Py_BuildValue("(iiiiiii)",
                         e->master_.mode.load(),
                         e->master_.eq_bass_db.load(),
                         e->master_.eq_mid_db.load(),
                         e->master_.eq_treb_db.load(),
                         e->master_.comp_thresh_db.load(),
                         e->master_.comp_ratio_x10.load(),
                         e->master_.limit_ceiling_db.load());
}

// Self-test WITHOUT a device: proves each stage does real work.
//   mode 0      → samples pass through byte-identical (legacy)
//   EQ +bass    → 60 Hz sine peak grows well above input
//   compressor  → loud 1 kHz sine's peak pulled toward the threshold
//   limiter     → hard clip at the requested ceiling
PyObject* render_master_selftest(PyObject*, PyObject*) {
    const size_t frames = (size_t)kSampleRate / 2;
    auto make = [&](std::vector<int16_t>& buf, double amp, double freq) {
        buf.assign(frames * 2, 0);
        for (size_t f = 0; f < frames; ++f) {
            int16_t v = (int16_t)(amp * std::sin(
                2.0 * RenderEngine::kPiPublic * freq * (double)f / (double)kSampleRate));
            buf[f * 2] = v;
            buf[f * 2 + 1] = v;
        }
    };
    auto peak_of = [](const std::vector<int16_t>& b) {
        int p = 0;
        for (int16_t s : b) { int a = s < 0 ? -s : s; if (a > p) p = a; }
        return p;
    };
    // 1) OFF = identity
    {
        RenderEngine e;
        e.master_.mode.store(0);
        std::vector<int16_t> buf;
        make(buf, 9000.0, 1000.0);
        std::vector<int16_t> ref = buf;
        e.master_process(buf.data(), buf.size());
        if (buf != ref) return PyLong_FromLong(1);   // FAIL identity
    }
    // 2) EQ bass boost amplifies low end
    {
        RenderEngine e;
        e.master_.mode.store(0);
        e.master_.eq_bass_db.store(9);
        std::vector<int16_t> buf;
        make(buf, 6000.0, 60.0);
        e.master_process(buf.data(), buf.size());
        const int p = peak_of(buf);
        if (p < 9000) return PyLong_FromLong(2);     // FAIL eq (no boost)
    }
    // 3) Compressor pulls a loud 1 kHz peak toward the threshold
    {
        RenderEngine e;
        e.master_.mode.store(2);
        e.master_.comp_thresh_db.store(-24);         // ≈ 2069 counts
        e.master_.comp_ratio_x10.store(40);
        std::vector<int16_t> buf;
        make(buf, 20000.0, 1000.0);
        e.master_process(buf.data(), buf.size());
        const int p = peak_of(buf);
        if (p > 10000) return PyLong_FromLong(3);    // FAIL comp (no gain cut)
        if (p < 1500) return PyLong_FromLong(4);     // FAIL comp (over-cut)
    }
    // 4) Limiter ceiling clips
    {
        RenderEngine e;
        e.master_.mode.store(0);
        e.master_.limit_on.store(true);
        e.master_.limit_ceiling_db.store(-12);       // = 8231 counts
        std::vector<int16_t> buf;
        make(buf, 20000.0, 440.0);
        e.master_process(buf.data(), buf.size());
        const int p = peak_of(buf);
        if (p > 8400) return PyLong_FromLong(5);     // FAIL limiter
    }
    return PyLong_FromLong(0);   // all stages pass
}

PyObject* render_clear(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    e->clear();
    e->request_flush();   // also discard the device-side buffer (render thread)
    Py_RETURN_NONE;
}

// render_use_file(engine, path, seek_seconds, fade_seconds, fade_target)
// -> bool. Built-in file source: the engine decodes the file itself and
// feeds the ring — no Python decode pump involved.
PyObject* render_use_file(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    PyObject* path_obj = nullptr;
    double seek = 0.0, fade = 0.0, target = 1.0;
    if (!PyArg_ParseTuple(args, "OUddd", &cap, &path_obj,
                          &seek, &fade, &target))
        return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) {
        PyErr_SetString(PyExc_ValueError, "invalid engine handle");
        return nullptr;
    }
    const wchar_t* w = PyUnicode_AsWideCharString(path_obj, nullptr);
    if (!w) return nullptr;
    const bool ok = e->use_file_source(w, seek, fade, (float)target);
    PyMem_Free((void*)w);
    if (!ok) Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}

// render_use_pipe(engine, handle_int, fade_seconds, fade_target) -> bool.
// Built-in pipe source: reads raw s16le PCM from the given HANDLE
// (e.g. an ffmpeg stdout) inside C++.
PyObject* render_use_pipe(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    long long handle = 0;
    double fade = 0.0, target = 1.0;
    if (!PyArg_ParseTuple(args, "OLdd", &cap, &handle, &fade, &target))
        return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) {
        PyErr_SetString(PyExc_ValueError, "invalid engine handle");
        return nullptr;
    }
    const bool ok = e->use_pipe_source((void*)(intptr_t)handle,
                                       fade, (float)target);
    if (!ok) Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}

PyObject* render_source_seek(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    double seconds = 0.0;
    if (!PyArg_ParseTuple(args, "Od", &cap, &seconds)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) {
        PyErr_SetString(PyExc_ValueError, "invalid engine handle");
        return nullptr;
    }
    e->source_seek(seconds);
    Py_RETURN_NONE;
}

PyObject* render_source_state(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromSize_t(e->source_state());
}

PyObject* render_source_eof(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return e->source_eof() ? Py_True : Py_False;
}

PyObject* render_source_failed(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return e->source_failed() ? Py_True : Py_False;
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

// render_emergency_mic(engine, endpoint_id|None, gain)
PyObject* render_emergency_mic(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    const char* endpoint_id = nullptr;
    double gain = 1.0;
    if (!PyArg_ParseTuple(args, "O|zd", &cap, &endpoint_id, &gain))
        return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    std::wstring id = to_wide(endpoint_id);
    const bool ok = e->emergency_mic_start(
        id.empty() ? nullptr : id.c_str(), (float)gain);
    if (!ok) {
        PyErr_SetString(PyExc_RuntimeError, "emergency mic start failed");
        return nullptr;
    }
    Py_RETURN_TRUE;
}

PyObject* render_emergency_mic_stop(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    e->emergency_mic_stop();
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_gain(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    double gain = 1.0;
    if (!PyArg_ParseTuple(args, "Od", &cap, &gain)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_gain((float)gain);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_active(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyBool_FromLong(e->emergency_mic_active() ? 1 : 0);
}

PyObject* render_emergency_mic_pause(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int p = 0;
    if (!PyArg_ParseTuple(args, "Op", &cap, &p)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_paused(p != 0);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_count(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_count());
}

// render_emergency_mic_suppression(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_suppression(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_suppression(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_suppression(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_suppression());
}

// render_emergency_mic_compressor(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_compressor(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_compressor(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_compressor(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_compressor());
}

// render_emergency_mic_limiter(engine, mode) — 0 off / 1 soft / 2 hard
PyObject* render_emergency_mic_limiter(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_limiter(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_limiter(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_limiter());
}

// render_emergency_mic_deesser(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_deesser(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_deesser(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_deesser(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_deesser());
}

// render_emergency_mic_miceq(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_miceq(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_miceq(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_miceq(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_miceq());
}

// render_emergency_mic_echo(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_echo(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_echo(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_echo(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_echo());
}

// render_emergency_mic_gainst(engine, mode) — 0 off / 1 +6 dB / 2 +12 dB
PyObject* render_emergency_mic_gainst(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_gainst(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_gainst(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_gainst());
}

// render_emergency_mic_upcomp(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_upcomp(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_upcomp(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_upcomp(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_upcomp());
}

// render_emergency_mic_expand(engine, mode) — 0 off / 1 gentle / 2 strong
PyObject* render_emergency_mic_expand(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    int mode = 0;
    if (!PyArg_ParseTuple(args, "Oi", &cap, &mode)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->emergency_mic_set_expand(mode);
    Py_RETURN_NONE;
}

PyObject* render_emergency_mic_get_expand(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_expand());
}

// render_test_heal(engine) — queue the mid-stream device recovery the
// same way a sample-rate/format switch does; the RENDER thread performs
// the heal at its next wake-up (thread-ownership safe). Returns True
// when the request was accepted (the engine is running).
// render_mic_param_set(engine, key, value) -> bool
PyObject* render_mic_param_set(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    const char* key = nullptr;
    int value = 0;
    if (!PyArg_ParseTuple(args, "Osi", &cap, &key, &value)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    if (!e->emergency_mic_set_param(key, value)) {
        PyErr_SetString(PyExc_KeyError, key);
        return nullptr;
    }
    Py_RETURN_TRUE;
}

// render_mic_param_get(engine, key) -> int
PyObject* render_mic_param_get(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    const char* key = nullptr;
    if (!PyArg_ParseTuple(args, "Os", &cap, &key)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    return PyLong_FromLong(e->emergency_mic_get_param(key));
}

PyObject* render_test_heal(PyObject*, PyObject* args) {
    auto* e = get_engine(args);
    if (!e) return nullptr;
    e->request_heal();
    Py_RETURN_TRUE;
}

PyObject* render_source_seek_flush(PyObject*, PyObject* args) {
    PyObject* cap = nullptr;
    double seconds = 0.0;
    if (!PyArg_ParseTuple(args, "Od", &cap, &seconds)) return nullptr;
    auto* e = static_cast<RenderEngine*>(
        PyCapsule_GetPointer(cap, "native_audio_render.Engine"));
    if (!e) return nullptr;
    e->source_seek_flush(seconds);
    Py_RETURN_NONE;
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
    {"render_master_set", render_master_set, METH_VARARGS,
     "master chain: set mode+params (0 = off)"},
    {"render_master_get", render_master_get, METH_VARARGS,
     "master chain: read all parameters"},
    {"render_master_selftest", render_master_selftest, METH_NOARGS,
     "0 = all stages pass; 1..5 identify the failing stage"},
    {"render_eq_selftest", render_eq_selftest, METH_NOARGS,
     "selftest: peak of 60Hz sine after bass+12dB biquad (no device)"},
    {"render_set_eq", render_set_eq, METH_VARARGS,
     "set_eq(engine, bass_db, mid_db, treble_db) — 3-band EQ, ±12 dB"},
    {"render_get_eq", render_get_eq, METH_VARARGS,
     "get_eq(engine) -> (bass, mid, treble) dB"},
    {"render_clear", render_clear, METH_VARARGS,
     "Drop all buffered audio (ring + device buffer)."},
    {"render_pause", render_pause, METH_VARARGS,
     "pause(engine, 1|0) — native pause: device plays silence, ring frozen"},
    {"render_use_file", render_use_file, METH_VARARGS,
     "use_file(engine, path, seek_s, fade_s, fade_target) -> bool — built-in decode+feed source"},
    {"render_use_pipe", render_use_pipe, METH_VARARGS,
     "use_pipe(engine, handle, fade_s, fade_target) -> bool — raw PCM pipe source"},
    {"render_source_seek", render_source_seek, METH_VARARGS,
     "source_seek(engine, seconds) — seek the built-in file source"},
    {"render_source_state", render_source_state, METH_VARARGS,
     "source_state(engine) -> frames read by the built-in source"},
    {"render_source_eof", render_source_eof, METH_VARARGS,
     "source_eof(engine) -> bool"},
    {"render_source_failed", render_source_failed, METH_VARARGS,
     "source_failed(engine) -> bool"},
    {"render_is_running", render_is_running, METH_VARARGS,
     "True while the render thread is alive on a device."},
    {"render_emergency_mic", render_emergency_mic, METH_VARARGS,
     "emergency_mic(engine, endpoint_id|None, gain) — live-mix a mic into the output"},
    {"render_emergency_mic_stop", render_emergency_mic_stop, METH_VARARGS,
     "emergency_mic_stop(engine)"},
    {"render_emergency_mic_gain", render_emergency_mic_gain, METH_VARARGS,
     "emergency_mic_gain(engine, gain) — live gain update"},
    {"render_emergency_mic_pause", render_emergency_mic_pause, METH_VARARGS,
     "render_emergency_mic_pause(engine, paused)"},
    {"render_emergency_mic_count", render_emergency_mic_count, METH_VARARGS,
     "render_emergency_mic_count(engine) -> int"},
    {"render_emergency_mic_suppression", render_emergency_mic_suppression,
     METH_VARARGS,
     "render_emergency_mic_suppression(engine, mode) — mic noise "
     "suppression: 0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_suppression",
     render_emergency_mic_get_suppression, METH_VARARGS,
     "render_emergency_mic_get_suppression(engine) -> int"},
    {"render_emergency_mic_compressor", render_emergency_mic_compressor,
     METH_VARARGS,
     "render_emergency_mic_compressor(engine, mode) — mic compressor: "
     "0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_compressor",
     render_emergency_mic_get_compressor, METH_VARARGS,
     "render_emergency_mic_get_compressor(engine) -> int"},
    {"render_emergency_mic_limiter", render_emergency_mic_limiter,
     METH_VARARGS,
     "render_emergency_mic_limiter(engine, mode) — mic limiter: "
     "0 off, 1 soft, 2 hard"},
    {"render_emergency_mic_get_limiter", render_emergency_mic_get_limiter,
     METH_VARARGS,
     "render_emergency_mic_get_limiter(engine) -> int"},
    {"render_emergency_mic_deesser", render_emergency_mic_deesser,
     METH_VARARGS,
     "render_emergency_mic_deesser(engine, mode) — mic de-esser: "
     "0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_deesser", render_emergency_mic_get_deesser,
     METH_VARARGS,
     "render_emergency_mic_get_deesser(engine) -> int"},
    {"render_emergency_mic_miceq", render_emergency_mic_miceq,
     METH_VARARGS,
     "render_emergency_mic_miceq(engine, mode) — mic EQ: 0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_miceq", render_emergency_mic_get_miceq,
     METH_VARARGS,
     "render_emergency_mic_get_miceq(engine) -> int"},
    {"render_emergency_mic_echo", render_emergency_mic_echo,
     METH_VARARGS,
     "render_emergency_mic_echo(engine, mode) — mic echo: 0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_echo", render_emergency_mic_get_echo,
     METH_VARARGS,
     "render_emergency_mic_get_echo(engine) -> int"},
    {"render_emergency_mic_gainst", render_emergency_mic_gainst,
     METH_VARARGS,
     "render_emergency_mic_gainst(engine, mode) — mic gain trim: "
     "0 off, 1 +6 dB, 2 +12 dB"},
    {"render_emergency_mic_get_gainst", render_emergency_mic_get_gainst,
     METH_VARARGS,
     "render_emergency_mic_get_gainst(engine) -> int"},
    {"render_emergency_mic_upcomp", render_emergency_mic_upcomp,
     METH_VARARGS,
     "render_emergency_mic_upcomp(engine, mode) — upward compressor: "
     "0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_upcomp", render_emergency_mic_get_upcomp,
     METH_VARARGS,
     "render_emergency_mic_get_upcomp(engine) -> int"},
    {"render_emergency_mic_expand", render_emergency_mic_expand,
     METH_VARARGS,
     "render_emergency_mic_expand(engine, mode) — expander: "
     "0 off, 1 gentle, 2 strong"},
    {"render_emergency_mic_get_expand", render_emergency_mic_get_expand,
     METH_VARARGS,
     "render_emergency_mic_get_expand(engine) -> int"},
    {"render_test_heal", render_test_heal, METH_VARARGS,
     "render_test_heal(engine) -> bool — force the mid-stream device "
     "recovery path used on sample-rate/format changes (tests only)"},
    {"render_mic_param_set", render_mic_param_set, METH_VARARGS,
     "render_mic_param_set(engine, key, value) -> bool — live per-stage "
     "parameter (OBS-style sliders)"},
    {"render_mic_param_get", render_mic_param_get, METH_VARARGS,
     "render_mic_param_get(engine, key) -> int"},
    {"render_emergency_mic_active", render_emergency_mic_active, METH_VARARGS,
     "emergency_mic_active(engine) -> bool"},
    {"render_source_seek_flush", render_source_seek_flush, METH_VARARGS,
     "source_seek_flush(engine, seconds) — seek + drop stale audio (ring + device)"},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "native_audio_render",
    "FreQ native WASAPI render stream.", -1, methods,
};
PyMODINIT_FUNC PyInit_native_audio_render() { return PyModule_Create(&module); }
