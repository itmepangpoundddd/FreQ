#!/usr/bin/env python3
"""Bake the end-mark SVGs in `icons/` into PNG assets the app can draw.

Tk has no SVG support and no SVG library is a dependency of FreQ, so the
SVGs stay the source of truth and this script rasterizes them ONCE with the
Chrome that is already on the machine. The app then only ever loads the
PNGs (Pillow resizes them down to the row mark), so nothing new is needed at
runtime and the packaged build stays as light as it was.

Run after editing a source SVG:

    python make_end_icons.py            # writes icons/<name>.png
    python make_end_icons.py --size 192 # bigger source for high-DPI screens

Each PNG keeps the SVG's own 1:1 frame (transparent background), so what the
queue shows is exactly what the file draws.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "icons"
SOURCES = ("play_normally", "stop_immediately", "ads_jingle_then_stop")

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str:
    for env in ("FREQ_SVG_BROWSER", "CHROME", "CHROME_PATH"):
        path = os.environ.get(env)
        if path and Path(path).is_file():
            return path
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium")
    if found:
        return found
    raise SystemExit("No Chrome/Edge found — set FREQ_SVG_BROWSER to a browser "
                     "binary that can render SVG headlessly.")


def bake(browser: str, name: str, size: int, out_dir: Path) -> Path:
    svg = ICON_DIR / f"{name}.svg"
    if not svg.is_file():
        raise SystemExit(f"missing source SVG: {svg}")
    png = out_dir / f"{name}.png"
    with tempfile.TemporaryDirectory(prefix="freq_icons_") as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(
            "<!doctype html><html><body style='margin:0;background:transparent'>"
            f"<img src='{svg.as_uri()}' "
            f"style='display:block;width:{size}px;height:{size}px'>"
            "</body></html>",
            encoding="utf-8")
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--default-background-color=00000000", "--virtual-time-budget=4000",
            f"--window-size={size},{size}", f"--screenshot={png}",
            page.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if not png.is_file() or png.stat().st_size == 0:
            raise SystemExit(
                f"{name}: browser produced no PNG rc={result.returncode}\n"
                f"{result.stderr[-800:]}")
    return png


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=128,
                        help="rendered square size in pixels (default 128)")
    args = parser.parse_args()
    if args.size < 32:
        raise SystemExit("--size must be at least 32 (it is downscaled at runtime)")
    browser = find_browser()
    print(f"browser: {browser}")
    for name in SOURCES:
        png = bake(browser, name, args.size, ICON_DIR)
        print(f"  {png.name}: {png.stat().st_size} bytes, {args.size}x{args.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
