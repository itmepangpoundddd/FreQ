#!/usr/bin/env python3
"""Compile the optional C++ PCM meter extension for the active Python."""
from __future__ import annotations

import subprocess
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VCVARS = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat")

def main() -> None:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    output = ROOT / f"native_audio_meter{suffix}"
    if not VCVARS.is_file():
        raise RuntimeError("Visual Studio Build Tools were not found")
    sdk_include = Path(r"C:\Program Files (x86)\Windows Kits\10\Include")
    if not any(sdk_include.glob("*/ucrt/io.h")):
        raise RuntimeError(
            "Windows SDK is missing. Open Visual Studio Installer, modify "
            "Build Tools 2026, then install 'Windows 11 SDK'."
        )
    subprocess.run(["cmd.exe", "/d", "/c", "build_native_meter.bat"], cwd=ROOT, check=True)
    print(f"Built {output.name}")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
