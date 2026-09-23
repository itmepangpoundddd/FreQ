// Native embedded audio decoder for FreQ.
//
// Wraps miniaudio (MP3 / WAV / FLAC / Ogg) so playback never needs the
// ffmpeg subprocess: the Python player reads s16le stereo 44.1 kHz chunks
// straight from a Decoder object, with resampling handled inside miniaudio.
// Codecs miniaudio cannot handle (e.g. AAC/M4A) still fall back to ffmpeg
// on the Python side. Build with build_native_meter.py.
#define MA_NO_DEVICE_IO
#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MINIAUDIO_IMPLEMENTATION
#include "miniaudio.h"

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <string>
#include <vector>

namespace {

// -- section --

typedef struct {
    PyObject_HEAD
    ma_decoder decoder;
    int open;
    ma_uint32 out_rate;
    ma_uint32 out_ch;
    ma_uint64 frames_total;   // 0 = unknown (streaming source)
} DecoderObject;

static void Decoder_dealloc(PyObject* self) {
    DecoderObject* d = (DecoderObject*)self;
    if (d->open) {
        ma_decoder_uninit(&d->decoder);
        d->open = 0;
    }
    PyObject_Free(self);
}

static PyObject* Decoder_new_impl(PyTypeObject* type, PyObject* args, PyObject* kwargs) {
    const char* path = nullptr;
    int rate = 44100;
    int ch = 2;
    static const char* kwlist[] = {"path", "rate", "channels", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "s|ii", const_cast<char**>(kwlist),
                                     &path, &rate, &ch)) {
        return nullptr;
    }
    if (rate < 8000 || rate > 384000 || ch < 1 || ch > 8) {
        PyErr_SetString(PyExc_ValueError, "rate must be 8000..384000, channels 1..8");
        return nullptr;
    }

    DecoderObject* self = (DecoderObject*)type->tp_alloc(type, 0);
    if (!self) return nullptr;
    self->open = 0;
    self->out_rate = (ma_uint32)rate;
    self->out_ch = (ma_uint32)ch;
    self->frames_total = 0;

    ma_decoder_config cfg = ma_decoder_config_init(
        ma_format_s16, (ma_uint32)ch, (ma_uint32)rate);
    ma_result res = ma_decoder_init_file(path, &cfg, &self->decoder);
    if (res != MA_SUCCESS) {
        Py_DECREF(self);
        PyErr_Format(PyExc_RuntimeError, "cannot decode '%s' (miniaudio result %d) "
                     "- codec not supported, use the ffmpeg fallback", path, (int)res);
        return nullptr;
    }
    self->open = 1;
    ma_uint64 len = 0;
    if (ma_decoder_get_length_in_pcm_frames(&self->decoder, &len) == MA_SUCCESS) {
        self->frames_total = len;
    }
    return (PyObject*)self;
}

// read(frames) -> bytes (s16le interleaved, out_ch channels)
static PyObject* Decoder_read(DecoderObject* self, PyObject* args) {
    long want = 44100;
    if (!PyArg_ParseTuple(args, "|l", &want)) return nullptr;
    if (want <= 0) want = 1;
    if (want > 10 * 44100) want = 10 * 44100;  // cap 10 s per read
    if (!self->open) {
        PyErr_SetString(PyExc_ValueError, "decoder is closed");
        return nullptr;
    }

    const ma_uint64 stride = (ma_uint64)self->out_ch * sizeof(int16_t);
    std::vector<uint8_t> buf((size_t)((ma_uint64)want * stride));
    ma_uint64 got = 0;
    ma_result res = ma_decoder_read_pcm_frames(&self->decoder, buf.data(),
                                               (ma_uint64)want, &got);
    if (res != MA_SUCCESS && res != MA_AT_END) {
        PyErr_Format(PyExc_RuntimeError, "decode read failed (result %d)", (int)res);
        return nullptr;
    }
    if (got == 0) Py_RETURN_NONE;   // EOF - Python side treats as end-of-track
    return PyBytes_FromStringAndSize((const char*)buf.data(),
                                     (Py_ssize_t)(got * stride));
}

// seek(frames) -> None
static PyObject* Decoder_seek(DecoderObject* self, PyObject* args) {
    long long frame = 0;
    if (!PyArg_ParseTuple(args, "L", &frame)) return nullptr;
    if (!self->open) {
        PyErr_SetString(PyExc_ValueError, "decoder is closed");
        return nullptr;
    }
    ma_result res = ma_decoder_seek_to_pcm_frame(&self->decoder, (ma_uint64)frame);
    if (res != MA_SUCCESS) {
        PyErr_Format(PyExc_RuntimeError, "seek failed (result %d)", (int)res);
        return nullptr;
    }
    Py_RETURN_NONE;
}

static PyObject* Decoder_close(DecoderObject* self, PyObject*) {
    if (self->open) {
        ma_decoder_uninit(&self->decoder);
        self->open = 0;
    }
    Py_RETURN_NONE;
}

static PyObject* Decoder_get_total(DecoderObject* self, void*) {
    return PyLong_FromUnsignedLongLong(self->frames_total);
}
static PyObject* Decoder_get_rate(DecoderObject* self, void*) {
    return PyLong_FromLong(self->out_rate);
}
static PyObject* Decoder_get_ch(DecoderObject* self, void*) {
    return PyLong_FromLong(self->out_ch);
}

static PyMethodDef Decoder_methods[] = {
    {"read", (PyCFunction)Decoder_read, METH_VARARGS,
     "read(frames=44100) -> bytes | None at EOF (s16le, interleaved)"},
    {"seek", (PyCFunction)Decoder_seek, METH_VARARGS, "seek(frame) -> None"},
    {"close", (PyCFunction)Decoder_close, METH_NOARGS, "close() -> None"},
    {nullptr, nullptr, 0, nullptr},
};

static PyGetSetDef Decoder_getset[] = {
    {"total_frames", (getter)Decoder_get_total, nullptr, "total frames (0 = unknown)", nullptr},
    {"samplerate", (getter)Decoder_get_rate, nullptr, "output sample rate", nullptr},
    {"channels", (getter)Decoder_get_ch, nullptr, "output channel count", nullptr},
    {nullptr, nullptr, 0, nullptr, nullptr},
};

static PyType_Slot Decoder_slots[] = {
    {Py_tp_dealloc, (void*)Decoder_dealloc},
    {Py_tp_methods, (void*)Decoder_methods},
    {Py_tp_getset,  (void*)Decoder_getset},
    {Py_tp_doc,     (void*)"Embedded miniaudio decoder (s16le output, auto-resampled)"},
    {Py_tp_new,     (void*)Decoder_new_impl},
    {0, nullptr},
};

static PyType_Spec Decoder_spec = {
    "native_audio_decode.Decoder",
    sizeof(DecoderObject),
    0,
    Py_TPFLAGS_DEFAULT,
    Decoder_slots,
};

static PyObject* g_DecoderType = nullptr;

// -- section --

// decode_file(path, rate=44100, channels=2) -> bytes - one-shot full decode
// (convenience for short clips/tests; the player streams via Decoder.read).
static PyObject* py_decode_file(PyObject*, PyObject* args, PyObject* kwargs) {
    const char* path = nullptr;
    int rate = 44100, ch = 2;
    static const char* kwlist[] = {"path", "rate", "channels", nullptr};
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "s|ii", const_cast<char**>(kwlist),
                                     &path, &rate, &ch)) {
        return nullptr;
    }
    ma_decoder_config cfg = ma_decoder_config_init(ma_format_s16, (ma_uint32)ch,
                                                   (ma_uint32)rate);
    ma_decoder dec;
    ma_result res = ma_decoder_init_file(path, &cfg, &dec);
    if (res != MA_SUCCESS) {
        PyErr_Format(PyExc_RuntimeError, "cannot decode '%s' (result %d)", path, (int)res);
        return nullptr;
    }
    std::vector<uint8_t> out;
    std::vector<uint8_t> block((size_t)44100 * ch * 2);   // 1 s per block
    for (;;) {
        ma_uint64 got = 0;
        res = ma_decoder_read_pcm_frames(&dec, block.data(), 44100, &got);
        if (res != MA_SUCCESS && res != MA_AT_END) {
            ma_decoder_uninit(&dec);
            PyErr_Format(PyExc_RuntimeError, "decode read failed (result %d)", (int)res);
            return nullptr;
        }
        if (got == 0) break;
        out.insert(out.end(), block.begin(),
                   block.begin() + (long long)got * ch * 2);
        if (out.size() > (size_t)1024ULL * 1024ULL * 1024ULL) {   // 1 GiB guard
            ma_decoder_uninit(&dec);
            PyErr_SetString(PyExc_RuntimeError, "decoded output exceeds 1 GiB");
            return nullptr;
        }
    }
    ma_decoder_uninit(&dec);
    return PyBytes_FromStringAndSize((const char*)out.data(), (Py_ssize_t)out.size());
}

static PyMethodDef module_methods[] = {
    {"decode_file", (PyCFunction)py_decode_file, METH_VARARGS | METH_KEYWORDS,
     "decode_file(path, rate=44100, channels=2) -> bytes (whole file, s16le)"},
    {nullptr, nullptr, 0, nullptr},
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "native_audio_decode",
    "Embedded miniaudio decoder for FreQ (MP3/WAV/FLAC/Ogg -> s16le PCM).",
    -1,
    module_methods,
    nullptr, nullptr, nullptr, nullptr,
};

}  // namespace

extern "C" __declspec(dllexport) PyObject* PyInit_native_audio_decode(void) {
    g_DecoderType = PyType_FromSpec(&Decoder_spec);
    if (!g_DecoderType) return nullptr;
    PyObject* m = PyModule_Create(&moduledef);
    if (!m) {
        Py_DECREF(g_DecoderType);
        return nullptr;
    }
    if (PyModule_AddObject(m, "Decoder", g_DecoderType) < 0) {
        Py_DECREF(g_DecoderType);
        Py_DECREF(m);
        return nullptr;
    }
    return m;
}
