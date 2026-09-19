#!/usr/bin/env python3
"""
FreQ - Build Script
Compiles the app into a standalone .exe using PyInstaller.

Usage:
    python build.py          # Build with default settings
    python build.py --clean   # Clean build (remove old dist/build)
    python build.py --debug   # Build with console window (for debugging)
"""

import subprocess
import sys
import shutil
import os
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
BUILD_DIR = ROOT / "build"


def clean():
    """Remove old build artifacts."""
    print("[CLEAN] Removing old build files...")
    for d in [DIST, BUILD_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"  Removed {d.name}/")
    for f in ROOT.glob("FreQ*.spec"):
        f.unlink()
        print(f"  Removed {f.name}")


def check_dependencies():
    """Verify all required packages are installed."""
    print("[CHECK] Checking dependencies...")
    required = {
        "PyInstaller": "PyInstaller",
        "customtkinter": "customtkinter",
        "yt-dlp": "yt_dlp",
        "pygame": "pygame",
        "Pillow": "PIL",
        "sounddevice": "sounddevice",
        "Flask": "flask",
        "mutagen": "mutagen",
    }
    missing = []
    for name, module in required.items():
        try:
            __import__(module)
            print(f"  [OK] {name}")
        except ImportError:
            print(f"  [MISSING] {name}")
            missing.append(name)

    if missing:
        print(f"\n[WARN] Missing packages: {', '.join(missing)}")
        return False
    return True


def build(debug=False):
    """Run PyInstaller build."""
    print("\n[BUILD] Building FreQ...")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", "FreQ",
    ]

    if not debug:
        cmd.append("--noconsole")
    else:
        cmd.append("--console")

    icon = ROOT / "logo.png"
    if icon.exists():
        cmd.extend(["--icon", str(icon)])

    # Add data files
    datas = []
    for f in ["logo.png", "logo.svg", "logo.ico"]:
        p = ROOT / f
        if p.exists():
            datas.append(f"{p};.")
    templates = ROOT / "templates"
    if templates.exists():
        datas.append(f"{templates};templates")

    # Optional C++ PCM meter. The pure-Python implementation remains available
    # when this extension has not been compiled on the build machine.
    native_meter = ROOT / "native_audio_meter.cp314-win_amd64.pyd"
    if native_meter.exists():
        cmd.extend(["--add-binary", f"{native_meter};."])

    for d in datas:
        cmd.extend(["--add-data", d])

    # Hidden imports
    hidden = [
        "radio_manager", "audio", "cache", "features", "player", "icons",
        "streaming", "tooltips", "audio_meter",
        "customtkinter", "yt_dlp", "pygame", "PIL", "sounddevice",
        "flask", "mutagen",
    ]
    for h in hidden:
        cmd.extend(["--hidden-import", h])

    cmd.append(str(ROOT / "gui.py"))

    print(f"  Running: PyInstaller ...")
    print()

    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode == 0:
        exe = DIST / "FreQ" / "FreQ.exe"
        if exe.exists():
            size_mb = exe.stat().st_size / (1024 * 1024)
            print(f"\n[BUILD] Build successful!")
            print(f"  Output: {exe}")
            print(f"  Size: {size_mb:.1f} MB")
            print(f"\n  Run with: dist\\FreQ\\FreQ.exe")
        else:
            print(f"\n[WARN] Build completed but FreQ.exe not found in dist/FreQ/")
    else:
        print(f"\n[ERROR] Build failed (exit code {result.returncode})")
        return False

    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build FreQ desktop app")
    parser.add_argument("--clean", action="store_true", help="Clean old build files first")
    parser.add_argument("--debug", action="store_true", help="Build with console window for debugging")
    args = parser.parse_args()

    print("=" * 50)
    print("  FreQ - Build Script")
    print("  Radio Playlist Manager")
    print("=" * 50)

    if args.clean:
        clean()

    if not check_dependencies():
        sys.exit(1)

    success = build(debug=args.debug)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
