"""
Audio Device Manager — Manage input/output devices

Uses sounddevice to detect and select output devices

dependencies:
    pip install sounddevice
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


@dataclass
class AudioDevice:
    """Represents an audio device"""
    id: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    def __str__(self) -> str:
        ch = self.max_output_channels if self.is_output else self.max_input_channels
        kind = "OUTPUT" if self.is_output else "INPUT" if self.is_input else "BOTH"
        return f"[{self.id}] {self.name} ({kind}, {ch}ch, {self.default_sample_rate:.0f}Hz)"


class AudioDeviceManager:
    """Manage output audio devices"""

    def __init__(self) -> None:
        self._devices: list[AudioDevice] = []
        self._selected_id: Optional[int] = None
        self._refresh()

    @property
    def available(self) -> bool:
        return SOUNDDEVICE_AVAILABLE

    def _refresh(self) -> None:
        """Detect all audio devices"""
        if not SOUNDDEVICE_AVAILABLE:
            self._devices = []
            return
        try:
            raw = sd.query_devices()
            self._devices = []
            for i, d in enumerate(raw):
                self._devices.append(AudioDevice(
                    id=i,
                    name=d.get("name", f"Device {i}"),
                    host_api=sd.hostapi_info.get("name", "Unknown") if hasattr(sd, 'hostapi_info') else "",
                    max_input_channels=d.get("max_input_channels", 0),
                    max_output_channels=d.get("max_output_channels", 0),
                    default_sample_rate=d.get("default_samplerate", 44100),
                ))
        except Exception:
            self._devices = []

        # Set default if not selected
        if self._selected_id is None:
            try:
                default = sd.default.device[1]  # output device index
                if default is not None:
                    self._selected_id = int(default)
            except Exception:
                pass

    def refresh(self) -> None:
        """Refresh device list (for calling from GUI)"""
        self._refresh()

    def get_all_devices(self) -> list[AudioDevice]:
        """Get all devices"""
        return list(self._devices)

    def get_output_devices(self) -> list[AudioDevice]:
        """Get output devices only"""
        return [d for d in self._devices if d.is_output]

    def get_input_devices(self) -> list[AudioDevice]:
        """Get input devices only"""
        return [d for d in self._devices if d.is_input]

    def get_selected_device(self) -> Optional[AudioDevice]:
        """Get the currently selected device"""
        if self._selected_id is not None:
            for d in self._devices:
                if d.id == self._selected_id:
                    return d
        return None

    def set_device(self, device_id: int) -> bool:
        """Select output device"""
        for d in self._devices:
            if d.id == device_id:
                self._selected_id = device_id
                # Set as default output
                if SOUNDDEVICE_AVAILABLE:
                    try:
                        sd.default.device = (sd.default.device[0], device_id)
                    except Exception:
                        pass
                return True
        return False

    def set_device_by_name(self, name: str) -> bool:
        """Select device by name (partial match)"""
        name_lower = name.lower()
        for d in self._devices:
            if name_lower in d.name.lower() and d.is_output:
                return self.set_device(d.id)
        return False

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
            marker = " ◀ SELECTED" if d.id == self._selected_id else ""
            lines.append(f"  {d}{marker}")
        return "\n".join(lines)
