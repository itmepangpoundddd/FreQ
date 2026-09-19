// Native PCM level meter for FreQ. Build with build_native_meter.py.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cmath>
#include <cstdint>

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

static PyMethodDef methods[] = {
    {"analyze_s16le_stereo", analyze_s16le_stereo, METH_VARARGS,
     "Return RMS and peak levels for signed-16-bit little-endian stereo PCM."},
    {"process_s16le_stereo", process_s16le_stereo, METH_VARARGS,
     "Apply left/right gain and a limiter to signed-16-bit stereo PCM."},
    {nullptr, nullptr, 0, nullptr},
};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "native_audio_meter", "FreQ native meter.", -1, methods};
PyMODINIT_FUNC PyInit_native_audio_meter() { return PyModule_Create(&module); }
