#!/usr/bin/env python3
"""Compile the optional C++ audio extensions for the active Python.

Builds the PCM meter (native_audio_meter), the WASAPI output-device
manager (native_audio_output), the WASAPI render stream
(native_audio_render), the embedded decoder (native_audio_decode)
and the WASAPI capture stream (native_audio_capture) for Windows.
"""
from __future__ import annotations

import subprocess
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VCVARS = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat")
SOURCES = ("native_audio_meter.cpp", "native_audio_output.cpp", "native_audio_render.cpp",
           "native_audio_decode.cpp", "native_audio_capture.cpp", "native_audio_stream.cpp")

def main() -> None:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    for stem in ("native_audio_meter", "native_audio_output", "native_audio_render",
                 "native_audio_decode", "native_audio_capture", "native_audio_stream"):
        output = ROOT / f"{stem}{suffix}"
        print(f"Building {output.name} ...")
        # Absolute path: a bare filename after `cmd /c` is resolved against
        # the *launcher's* lookup rules and can be missed depending on how
        # the parent process was started — always pass the full path.
        bat = ROOT / "build_native_meter.bat"
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(bat), stem],
            cwd=ROOT,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stdout, result.stderr)
            raise RuntimeError(f"build failed for {stem} (exit {result.returncode})")
        if not output.exists():
            raise RuntimeError(f"Compiler did not produce {output.name}")
        print(f"Built {output.name}")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
