#!/usr/bin/env python3
"""Compile the optional C++ audio extensions for the active Python.

Builds both the PCM meter (native_audio_meter) and the WASAPI output-device
manager (native_audio_output) for Windows.
"""
from __future__ import annotations

import subprocess
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VCVARS = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat")
SOURCES = ("native_audio_meter.cpp", "native_audio_output.cpp")

def main() -> None:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    for stem in ("native_audio_meter", "native_audio_output"):
        output = ROOT / f"{stem}{suffix}"
        print(f"Building {output.name} ...")
        subprocess.run(
            ["cmd.exe", "/d", "/c", "build_native_meter.bat", stem],
            cwd=ROOT, check=True,
        )
        if not output.exists():
            raise RuntimeError(f"Compiler did not produce {output.name}")
        print(f"Built {output.name}")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
