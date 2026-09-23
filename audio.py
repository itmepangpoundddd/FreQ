"""
Audio Device Manager — Manage input/output devices

Outputs use the native WASAPI module (native_audio_output) when available:
switching the device changes the OS default endpoint, so every playback
engine (pygame included) follows. Input devices and the non-Windows fallback
keep using sounddevice (PortAudio).

dependencies:
    pip install sounddevice
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

try:
    import native_audio_output as _wasapi

    _wasapi.list_output_devices  # ensure the entry point exists
    WASAPI_AVAILABLE = True
except Exception:
    _wasapi = None
    WASAPI_AVAILABLE = False


@dataclass
class AudioDevice:
    """Represents an audio device.

    ``id`` is the PortAudio device index (input path / legacy CLI).
    ``endpoint_id`` is the WASAPI endpoint id for outputs (native backend).
    """

    id: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    endpoint_id: Optional[str] = None
    form_factor: str = ""
    is_default: bool = False

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    def __str__(self) -> str:
        ch = self.max_output_channels if self.is_output else self.max_input_channels
        kind = "OUTPUT" if self.is_output else "INPUT" if self.is_input else "BOTH"
        marker = " ◀ DEFAULT" if self.is_default else ""
        return (
            f"[{self.id}] {self.name} ({kind}, {ch}ch, "
            f"{self.default_sample_rate:.0f}Hz){marker}"
        )


class AudioDeviceManager:
    """Manage audio devices — WASAPI outputs, sounddevice inputs."""

    def __init__(self) -> None:
        self._devices: list[AudioDevice] = []
        self._selected_id: Optional[int] = None
        self._selected_output_name: Optional[str] = None
        self._selected_endpoint: Optional[str] = None
        self._previous_default_endpoint: Optional[str] = None
        self._refresh()

    # ─────────────────────────────────────
    # Availability
    # ─────────────────────────────────────

    @property
    def available(self) -> bool:
        return WASAPI_AVAILABLE or SOUNDDEVICE_AVAILABLE

    @property
    def wasapi_available(self) -> bool:
        return WASAPI_AVAILABLE

    @property
    def backend(self) -> str:
        return "wasapi" if WASAPI_AVAILABLE else "sounddevice"

    # ─────────────────────────────────────
    # Discovery
    # ─────────────────────────────────────

    def _refresh(self) -> None:
        """Detect all devices: WASAPI outputs + sounddevice inputs."""
        self._devices = []
        output_seen = False

        if WASAPI_AVAILABLE:
            try:
                for endpoint in _wasapi.list_output_devices():
                    self._devices.append(AudioDevice(
                        id=-1,
                        name=endpoint.get("name", "Unknown output"),
                        host_api="WASAPI",
                        max_input_channels=0,
                        max_output_channels=endpoint.get("channels", 2),
                        default_sample_rate=endpoint.get("rate", 48000),
                        endpoint_id=endpoint.get("id"),
                        form_factor=endpoint.get("form_factor", ""),
                        is_default=bool(endpoint.get("is_default")),
                    ))
                    output_seen = True
            except Exception:
                pass

        if SOUNDDEVICE_AVAILABLE and sd is not None:
            try:
                raw = sd.query_devices()
                hostapis = sd.hostapi_info if hasattr(sd, "hostapi_info") else []
                for i, d in enumerate(raw):
                    is_output = d.get("max_output_channels", 0) > 0
                    if is_output and output_seen and not WASAPI_AVAILABLE:
                        pass  # fall through: sounddevice is the output backend
                    if is_output and WASAPI_AVAILABLE:
                        continue  # outputs already listed by WASAPI
                    host_api = ""
                    try:
                        host_api = hostapis[d.get("hostapi", 0)].get("name", "")
                    except Exception:
                        pass
                    self._devices.append(AudioDevice(
                        id=i,
                        name=d.get("name", f"Device {i}"),
                        host_api=host_api,
                        max_input_channels=d.get("max_input_channels", 0),
                        max_output_channels=d.get("max_output_channels", 0),
                        default_sample_rate=d.get("default_samplerate", 44100),
                    ))
                    if self._selected_id is None and is_output:
                        try:
                            default = sd.default.device[1]  # output device index
                            if default is not None and int(default) == i:
                                self._selected_id = i
                        except Exception:
                            pass
            except Exception:
                pass

    def refresh(self) -> None:
        """Refresh device list (for calling from GUI)"""
        self._refresh()

    def get_all_devices(self) -> list[AudioDevice]:
        return list(self._devices)

    def get_output_devices(self) -> list[AudioDevice]:
        return [d for d in self._devices if d.is_output]

    def get_input_devices(self) -> list[AudioDevice]:
        return [d for d in self._devices if d.is_input]

    def get_selected_device(self) -> Optional[AudioDevice]:
        if self._selected_output_name:
            for d in self.get_output_devices():
                if d.name == self._selected_output_name:
                    return d
        for d in self.get_output_devices():
            if d.is_default:
                return d
        if self._selected_id is not None:
            return self.get_device_by_id(self._selected_id)
        return None

    # ─────────────────────────────────────
    # Output switching (WASAPI)
    # ─────────────────────────────────────

    def switch_to_endpoint(self, endpoint_id: str) -> bool:
        """Make a WASAPI endpoint the OS default output (all apps follow)."""
        if not WASAPI_AVAILABLE or not endpoint_id:
            return False
        current = next((d for d in self.get_output_devices() if d.is_default), None)
        try:
            _wasapi.set_default_output_device(endpoint_id)
        except Exception:
            return False
        if current and current.endpoint_id and current.endpoint_id != endpoint_id:
            self._previous_default_endpoint = current.endpoint_id
        self._selected_endpoint = endpoint_id
        for d in self.get_output_devices():
            if d.endpoint_id == endpoint_id:
                self._selected_output_name = d.name
                break
        return True

    def switch_by_name(self, name: str) -> bool:
        """Switch the default output to the device whose name matches."""
        name_lower = (name or "").lower()
        for d in self.get_output_devices():
            if name_lower in d.name.lower():
                if d.endpoint_id and WASAPI_AVAILABLE:
                    return self.switch_to_endpoint(d.endpoint_id)
                if SOUNDDEVICE_AVAILABLE and d.id >= 0 and sd is not None:
                    try:
                        sd.default.device = (sd.default.device[0], d.id)
                        self._selected_id = d.id
                        self._selected_output_name = d.name
                        return True
                    except Exception:
                        return False
        return False

    def restore_default(self) -> bool:
        """Revert to the default output that was active before a switch."""
        if WASAPI_AVAILABLE and self._previous_default_endpoint:
            endpoint = self._previous_default_endpoint
            self._previous_default_endpoint = None
            return self.switch_to_endpoint(endpoint)
        return False

    # ─────────────────────────────────────
    # Legacy API (kept for CLI callers)
    # ─────────────────────────────────────

    def set_device(self, device_id: int) -> bool:
        """Select output device by PortAudio id (-1 = system default)."""
        if device_id in (-1, None):
            return self.restore_default()
        for d in self.get_output_devices():
            if d.id == device_id:
                if d.endpoint_id and WASAPI_AVAILABLE:
                    return self.switch_to_endpoint(d.endpoint_id)
                self._selected_id = d.id
                self._selected_output_name = d.name
                if SOUNDDEVICE_AVAILABLE and sd is not None:
                    try:
                        sd.default.device = (sd.default.device[0], d.id)
                    except Exception:
                        pass
                return True
        return False

    def set_device_by_name(self, name: str) -> bool:
        """Select output device by name (partial match)"""
        return self.switch_by_name(name)

    def get_device_by_id(self, device_id: int) -> Optional[AudioDevice]:
        for d in self._devices:
            if d.id == device_id:
                return d
        return None

    def get_device_summary(self) -> str:
        """Show summary of all devices"""
        output_devices = self.get_output_devices()
        selected = self.get_selected_device()

        lines = []
        lines.append(f"🔊 Output Devices ({len(output_devices)}):")
        for d in output_devices:
            marker = " ◀ SELECTED" if selected and d.name == selected.name else ""
            lines.append(f"  {d}{marker}")
        return "\n".join(lines)
