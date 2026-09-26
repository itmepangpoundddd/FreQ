// Native WASAPI output-device manager for FreQ.
// Enumerates render endpoints and sets the OS default output device using
// the undocumented-but-stable IPolicyConfig COM interface (same mechanism
// as SoundSwitch / AudioSwitcher). Build with build_native_meter.py.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <mmdeviceapi.h>
#include <endpointvolume.h>
#include <functiondiscoverykeys_devpkey.h>

#include <string>
#include <vector>

#ifndef DLLGETCLASSOBJECTPROC_DEFINED
typedef HRESULT (STDAPICALLTYPE *DLLGETCLASSOBJECTPROC)(REFCLSID, REFIID, LPVOID*);
#endif

namespace {

// ── IPolicyConfig: undocumented interface, stable CLSIDs/IIDs for a decade ──

struct IPolicyConfig : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE GetMixFormat(PCWSTR, WAVEFORMATEX**) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetDeviceFormat(PCWSTR, INT, WAVEFORMATEX**) = 0;
    virtual HRESULT STDMETHODCALLTYPE ResetDeviceFormat(PCWSTR) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetDeviceFormat(PCWSTR, WAVEFORMATEX*, WAVEFORMATEX*) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetProcessingPeriod(PCWSTR, INT, PINT64, PINT64) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetProcessingPeriod(PCWSTR, PINT64) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetShareMode(PCWSTR, INT*) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetShareMode(PCWSTR, INT) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetPropertyValue(PCWSTR, const PROPERTYKEY&, PROPVARIANT*) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetPropertyValue(PCWSTR, const PROPERTYKEY&, PROPVARIANT*) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetDefaultEndpoint(PCWSTR wszDeviceId, ERole eRole) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetEndpointVisibility(PCWSTR, INT) = 0;
};

// Windows 10 1803+ / Windows 11
static const CLSID CLSID_PolicyConfigClient = {
    0x870af99c, 0x171d, 0x4f9e, {0xaf, 0x0d, 0x63, 0x63, 0xd2, 0x2f, 0x4f, 0x7a}};
static const IID IID_IPolicyConfig = {
    0xf8679f50, 0x850a, 0x41cf, {0x9c, 0x72, 0x43, 0x0f, 0x02, 0x92, 0x3c, 0xc2}};
// Windows 10 1607-1709 era pair (still serviced by the same DLL)
static const CLSID CLSID_PolicyConfigClient_v1 = {
    0x294935ce, 0x8ee2, 0x48f2, {0x89, 0x41, 0xd0, 0x50, 0x3e, 0xee, 0x36, 0x60}};
static const IID IID_IPolicyConfig_v1 = {
    0xca286fc3, 0x91fd, 0x42c3, {0x8e, 0x2b, 0x4b, 0x99, 0x0e, 0xdf, 0xdf, 0x46}};

// Windows SDK headers omit PKEY_Audio_FormFactor from devpkey headers;
// define it exactly as in functiondiscoverykeys.h (pid 0).
static const PROPERTYKEY PKEY_Audio_FormFactor_local = {
    {0x1da9d503, 0x7441, 0x4bb2, {0x9c, 0x9b, 0x1f, 0x6c, 0x1e, 0x35, 0xf7, 0x64}}, 0};
// PKEY_AudioEngine_DeviceFormat comes from the SDK header but its instance
// lives in the import lib we do not link; define the identical value locally.
static const PROPERTYKEY PKEY_AudioEngine_DeviceFormat_local = {
    {0xf19f064d, 0x82c, 0x469e, {0x99, 0x14, 0x2, 0x0, 0xf4, 0x8d, 0xc1, 0x3b}}, 0};

// ── Helpers ──

std::string wide_to_utf8(const wchar_t* wide) {
    if (!wide) return "";
    const int size = WideCharToMultiByte(CP_UTF8, 0, wide, -1, nullptr, 0, nullptr, nullptr);
    std::string utf8(size > 0 ? size - 1 : 0, '\0');
    if (size > 1) {
        WideCharToMultiByte(CP_UTF8, 0, wide, -1, utf8.data(), size, nullptr, nullptr);
    }
    return utf8;
}

std::wstring utf8_to_wide(const std::string& chunk) {
    const int size = MultiByteToWideChar(
        CP_UTF8, 0, chunk.c_str(), static_cast<int>(chunk.size()), nullptr, 0);
    std::wstring wide(size, L'\0');
    if (size > 0) {
        MultiByteToWideChar(
            CP_UTF8, 0, chunk.c_str(), static_cast<int>(chunk.size()), wide.data(), size);
    }
    return wide;
}

// COM apartment guard: initialize once, always uninitialize exactly once.
struct ComContext {
    bool needs_uninit = false;
    bool ready = false;

    bool init() {
        HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        if (SUCCEEDED(hr)) {
            needs_uninit = true;
        } else if (hr != RPC_E_CHANGED_MODE) {
            // Apartment already running as MTA (e.g. audio threads) is fine;
            // anything else is a real failure.
            return false;
        }
        ready = true;
        return true;
    }

    ~ComContext() {
        if (needs_uninit) CoUninitialize();
    }
};

}  // namespace

// ── Enumeration ──

static PyObject* list_output_devices(PyObject*, PyObject*) {
    ComContext com;
    if (!com.init()) {
        PyErr_SetString(PyExc_RuntimeError, "CoInitializeEx failed");
        return nullptr;
    }

    IMMDeviceEnumerator* enumerator = nullptr;
    HRESULT hr = CoCreateInstance(
        __uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
        __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator));
    if (FAILED(hr)) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot create MMDeviceEnumerator");
        return nullptr;
    }

    IMMDeviceCollection* collection = nullptr;
    hr = enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &collection);
    if (FAILED(hr)) {
        enumerator->Release();
        PyErr_SetString(PyExc_RuntimeError, "EnumAudioEndpoints failed");
        return nullptr;
    }

    PyObject* list = PyList_New(0);
    if (!list) {
        collection->Release();
        enumerator->Release();
        return nullptr;
    }

    // Default + communications default endpoint ids for tagging.
    std::wstring default_id, comm_id;
    IMMDevice* dev = nullptr;
    if (SUCCEEDED(enumerator->GetDefaultAudioEndpoint(eRender, eMultimedia, &dev)) && dev) {
        LPWSTR id = nullptr;
        if (SUCCEEDED(dev->GetId(&id))) {
            default_id = id;
            CoTaskMemFree(id);
        }
        dev->Release();
    }
    if (SUCCEEDED(enumerator->GetDefaultAudioEndpoint(eRender, eCommunications, &dev)) && dev) {
        LPWSTR id = nullptr;
        if (SUCCEEDED(dev->GetId(&id))) {
            comm_id = id;
            CoTaskMemFree(id);
        }
        dev->Release();
    }

    UINT count = 0;
    collection->GetCount(&count);
    for (UINT i = 0; i < count; ++i) {
        IMMDevice* device = nullptr;
        if (FAILED(collection->Item(i, &device)) || !device) continue;

        LPWSTR id_w = nullptr;
        if (FAILED(device->GetId(&id_w))) {
            device->Release();
            continue;
        }
        const std::wstring id = id_w;
        CoTaskMemFree(id_w);

        IPropertyStore* props = nullptr;
        std::string name;
        std::string form_factor = "Unknown";
        int channels = 2;
        int rate = 48000;
        if (SUCCEEDED(device->OpenPropertyStore(STGM_READ, &props)) && props) {
            PROPVARIANT var;
            PropVariantInit(&var);
            if (SUCCEEDED(props->GetValue(PKEY_Device_FriendlyName, &var)) &&
                var.vt == VT_LPWSTR) {
                name = wide_to_utf8(var.pwszVal);
            }
            PropVariantClear(&var);
            PropVariantInit(&var);
            if (SUCCEEDED(props->GetValue(PKEY_AudioEngine_DeviceFormat_local, &var)) &&
                var.vt == VT_BLOB && var.blob.cbSize >= sizeof(WAVEFORMATEX)) {
                const auto* wfx = reinterpret_cast<const WAVEFORMATEX*>(var.blob.pBlobData);
                channels = wfx->nChannels;
                rate = static_cast<int>(wfx->nSamplesPerSec);
            }
            PropVariantClear(&var);
            PropVariantInit(&var);
            if (SUCCEEDED(props->GetValue(PKEY_Audio_FormFactor_local, &var)) && var.vt == VT_I4) {
                switch (static_cast<int>(var.lVal)) {
                    case 0: form_factor = "Remote"; break;
                    case 1: form_factor = "Speakers"; break;
                    case 2: form_factor = "LineLevel"; break;
                    case 3: form_factor = "Headphones"; break;
                    case 5: form_factor = "Headset"; break;
                    case 8: form_factor = "PassThrough"; break;
                    case 9: form_factor = "SPDIF"; break;
                    case 10: form_factor = "HDMI"; break;
                    default: form_factor = "Unknown"; break;
                }
            }
            PropVariantClear(&var);
            props->Release();
        }

        PyObject* item = Py_BuildValue(
            "{s:s,s:s,s:s,s:i,s:i,s:O,s:O}",
            "id", wide_to_utf8(id.c_str()).c_str(),
            "name", name.c_str(),
            "form_factor", form_factor.c_str(),
            "channels", channels,
            "rate", rate,
            "is_default", (id == default_id) ? Py_True : Py_False,
            "is_communications_default", (id == comm_id) ? Py_True : Py_False);
        if (item) {
            PyList_Append(list, item);
            Py_DECREF(item);
        }
        device->Release();
    }

    collection->Release();
    enumerator->Release();
    return list;
}

// ── Default endpoint switching ──

static PyObject* set_default_output_device(PyObject*, PyObject* args) {
    const char* device_id_utf8 = nullptr;
    if (!PyArg_ParseTuple(args, "s", &device_id_utf8)) return nullptr;
    if (!device_id_utf8 || !*device_id_utf8) {
        PyErr_SetString(PyExc_ValueError, "device_id must not be empty");
        return nullptr;
    }

    ComContext com;
    if (!com.init()) {
        PyErr_SetString(PyExc_RuntimeError, "CoInitializeEx failed");
        return nullptr;
    }
    const std::wstring device_id = utf8_to_wide(device_id_utf8);

    IPolicyConfig* policy = nullptr;
    HRESULT hr_last = E_NOINTERFACE;

    // 1) Registry-registered class, newest interface first.
    const CLSID class_ids[] = {CLSID_PolicyConfigClient, CLSID_PolicyConfigClient_v1};
    const IID interface_ids[] = {IID_IPolicyConfig, IID_IPolicyConfig_v1};
    HRESULT hr_registry[2][2] = {};
    int ci = 0;
    for (const CLSID& clsid : class_ids) {
        int ii = 0;
        for (const IID& iid : interface_ids) {
            hr_last = CoCreateInstance(
                clsid, nullptr, CLSCTX_ALL, iid, reinterpret_cast<void**>(&policy));
            hr_registry[ci][ii] = hr_last;
            if (SUCCEEDED(hr_last) && policy) break;
            policy = nullptr;
            ++ii;
        }
        if (policy) break;
        ++ci;
    }

    // 2) Registry-free activation: the class object lives in MMDevApi.dll
    //    even on builds that omit the registry entry.
    if (!policy) {
        const wchar_t* dlls[] = {L"MMDevApi.dll", L"AudioSes.dll"};
        // attempts[dll][clsid][iid] flattened: 2 x 2 x 2
        struct Attempt { unsigned int gco; unsigned int create; } attempts[8] = {};
        int slot = 0;
        for (const wchar_t* dll : dlls) {
            HMODULE module = LoadLibraryW(dll);
            if (!module) continue;
            const auto get_class_object = reinterpret_cast<DLLGETCLASSOBJECTPROC>(
                GetProcAddress(module, "DllGetClassObject"));
            if (get_class_object) {
                for (const CLSID& clsid : class_ids) {
                    for (const IID& iid : interface_ids) {
                        IClassFactory* factory = nullptr;
                        const HRESULT hr_gco = get_class_object(
                            clsid, __uuidof(IClassFactory),
                            reinterpret_cast<void**>(&factory));
                        HRESULT hr_create = E_UNEXPECTED;
                        if (SUCCEEDED(hr_gco) && factory) {
                            hr_create = factory->CreateInstance(
                                nullptr, iid, reinterpret_cast<void**>(&policy));
                            factory->Release();
                            if (SUCCEEDED(hr_create) && policy) {
                                break;
                            }
                            policy = nullptr;
                        }
                        attempts[slot].gco = static_cast<unsigned int>(hr_gco);
                        attempts[slot].create = static_cast<unsigned int>(hr_create);
                        ++slot;
                    }
                    if (policy) break;
                }
            } else {
                for (int k = 0; k < 4 && slot < 8; ++k, ++slot) {
                    attempts[slot].gco = static_cast<unsigned int>(
                        HRESULT_FROM_WIN32(GetLastError()));
                    attempts[slot].create = 0;
                }
            }
            if (!policy) {
                FreeLibrary(module);
            }
            if (policy) break;
        }
        if (!policy) {
            PyErr_Format(
                PyExc_RuntimeError,
                "Cannot create PolicyConfig (registry hr=0x%08lx/0x%08lx/0x%08lx/0x%08lx, "
                "dll attempts gco/create: "
                "0x%08x/0x%08x 0x%08x/0x%08x 0x%08x/0x%08x 0x%08x/0x%08x "
                "0x%08x/0x%08x 0x%08x/0x%08x 0x%08x/0x%08x 0x%08x/0x%08x)",
                static_cast<unsigned long>(hr_registry[0][0]),
                static_cast<unsigned long>(hr_registry[0][1]),
                static_cast<unsigned long>(hr_registry[1][0]),
                static_cast<unsigned long>(hr_registry[1][1]),
                attempts[0].gco, attempts[0].create, attempts[1].gco, attempts[1].create,
                attempts[2].gco, attempts[2].create, attempts[3].gco, attempts[3].create,
                attempts[4].gco, attempts[4].create, attempts[5].gco, attempts[5].create,
                attempts[6].gco, attempts[6].create, attempts[7].gco, attempts[7].create);
            return nullptr;
        }
    }

    // Console is the role apps pick by default; multimedia covers the rest.
    // Some Windows builds reject individual roles, so require both to pass.
    HRESULT hr_console = policy->SetDefaultEndpoint(device_id.c_str(), eConsole);
    HRESULT hr_multi = policy->SetDefaultEndpoint(device_id.c_str(), eMultimedia);
    policy->SetDefaultEndpoint(device_id.c_str(), eCommunications);
    policy->Release();

    if (FAILED(hr_console) || FAILED(hr_multi)) {
        PyErr_Format(PyExc_RuntimeError, "SetDefaultEndpoint failed (hr=0x%08lx/0x%08lx)",
                     static_cast<unsigned long>(hr_console),
                     static_cast<unsigned long>(hr_multi));
        return nullptr;
    }
    Py_RETURN_TRUE;
}

// ── Volume / mute on the default endpoint ──

static PyObject* get_master_volume(PyObject*, PyObject*) {
    ComContext com;
    if (!com.init()) {
        PyErr_SetString(PyExc_RuntimeError, "CoInitializeEx failed");
        return nullptr;
    }
    IMMDeviceEnumerator* enumerator = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                __uuidof(IMMDeviceEnumerator),
                                reinterpret_cast<void**>(&enumerator))) || !enumerator) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot create MMDeviceEnumerator");
        return nullptr;
    }
    IMMDevice* device = nullptr;
    if (FAILED(enumerator->GetDefaultAudioEndpoint(eRender, eMultimedia, &device)) || !device) {
        enumerator->Release();
        PyErr_SetString(PyExc_RuntimeError, "No default output device");
        return nullptr;
    }
    IAudioEndpointVolume* volume = nullptr;
    PyObject* result = nullptr;
    if (SUCCEEDED(device->Activate(__uuidof(IAudioEndpointVolume), CLSCTX_ALL,
                                   nullptr, reinterpret_cast<void**>(&volume))) && volume) {
        float level = 0.0f;
        BOOL muted = FALSE;
        if (SUCCEEDED(volume->GetMasterVolumeLevelScalar(&level)) &&
            SUCCEEDED(volume->GetMute(&muted))) {
            result = Py_BuildValue("(dO)", static_cast<double>(level),
                                   muted ? Py_True : Py_False);
        }
        volume->Release();
    }
    device->Release();
    enumerator->Release();
    if (!result) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot read endpoint volume");
        return nullptr;
    }
    return result;
}

static PyObject* set_master_volume(PyObject*, PyObject* args) {
    double level = 0.0;
    PyObject* mute_obj = Py_None;
    if (!PyArg_ParseTuple(args, "d|O", &level, &mute_obj)) return nullptr;
    if (level < 0.0 || level > 1.0) {
        PyErr_SetString(PyExc_ValueError, "level must be within 0.0-1.0");
        return nullptr;
    }
    ComContext com;
    if (!com.init()) {
        PyErr_SetString(PyExc_RuntimeError, "CoInitializeEx failed");
        return nullptr;
    }
    IMMDeviceEnumerator* enumerator = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                __uuidof(IMMDeviceEnumerator),
                                reinterpret_cast<void**>(&enumerator))) || !enumerator) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot create MMDeviceEnumerator");
        return nullptr;
    }
    IMMDevice* device = nullptr;
    if (FAILED(enumerator->GetDefaultAudioEndpoint(eRender, eMultimedia, &device)) || !device) {
        enumerator->Release();
        PyErr_SetString(PyExc_RuntimeError, "No default output device");
        return nullptr;
    }
    IAudioEndpointVolume* volume = nullptr;
    PyObject* result = nullptr;
    if (SUCCEEDED(device->Activate(__uuidof(IAudioEndpointVolume), CLSCTX_ALL,
                                   nullptr, reinterpret_cast<void**>(&volume))) && volume) {
        if (SUCCEEDED(volume->SetMasterVolumeLevelScalar(static_cast<float>(level), nullptr))) {
            if (mute_obj != Py_None) {
                volume->SetMute(PyObject_IsTrue(mute_obj) ? TRUE : FALSE, nullptr);
            }
            result = Py_True;
            Py_INCREF(result);
        }
        volume->Release();
    }
    device->Release();
    enumerator->Release();
    if (!result) {
        PyErr_SetString(PyExc_RuntimeError, "Cannot set endpoint volume");
        return nullptr;
    }
    return result;
}

static PyMethodDef methods[] = {
    {"list_output_devices", list_output_devices, METH_NOARGS,
     "List active WASAPI render endpoints (id, name, form factor, channels, rate, defaults)."},
    {"set_default_output_device", set_default_output_device, METH_VARARGS,
     "Make the given endpoint id the OS default output device (all roles)."},
    {"get_master_volume", get_master_volume, METH_NOARGS,
     "Return (level 0.0-1.0, muted) of the default output endpoint."},
    {"set_master_volume", set_master_volume, METH_VARARGS,
     "Set the default output endpoint master volume, optionally toggling mute."},
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "native_audio_output",
    "FreQ native WASAPI output-device manager.", -1, methods};
PyMODINIT_FUNC PyInit_native_audio_output(void) { return PyModule_Create(&module); }
