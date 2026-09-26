#!/usr/bin/env python3
"""
Auto-Update Checker for FreQ
Checks GitHub releases API on launch and notifies user if a new version is available.

The current version is read from version.txt (stamped from installer.iss by
build.py and bundled with the app), so this module never hardcodes one —
bumping the version in installer.iss updates the checker automatically.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError


APP_NAME = "FreQ"
GITHUB_REPO = "SocieticsTv/freq"
CHECK_INTERVAL = 24 * 3600  # Check once per day (in seconds)


def _read_packaged_version() -> str:
    """Current app version, without a hardcoded copy in this file.

    Order: version.txt beside this module, then the PyInstaller _MEIPASS
    data dir (one-file builds), then MyAppVersion in installer.iss
    (running from the source tree), else "".
    """
    candidates = [Path(__file__).with_name("version.txt")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "version.txt")
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            pass
    try:
        content = Path(__file__).with_name("installer.iss").read_text(encoding="utf-8")
        match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^"]+)"', content, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


CURRENT_VERSION = _read_packaged_version()


@dataclass
class UpdateInfo:
    """Holds information about an available update."""
    version: str
    tag: str
    url: str
    body: str
    published_at: str


_SP_RE = re.compile(r"SP(\d+)$", re.IGNORECASE)

# Ultimate era: bare release id '2026.0924.1' (year . mmdd . update-round) or
# the branded form 'Ultimate 2026 (Build 0924.1)'. The bare form is what
# release.py puts in git tags (v2026.0924.1); the branded form is what shows
# in release notes and the UI.
_ULTIMATE_RE = re.compile(
    r"^(?:ultimate[\s-]*)?(\d{4})[\s.()\[\]-]*(?:build[\s-]*)?"
    r"(\d{4})[\s.()\[\]-]*(\d+)\s*\)?$",
    re.IGNORECASE,
)


def _ultimate_tuple(year: str, mmdd: str, build_round: str) -> Tuple[int, int, int, int]:
    """Ultimate id -> comparable tuple (year, month, day*1000+round, 0).

    The day slot packs the update round into its low thousands so that the
    same day sorts by round (0924.2 > 0924.1) and the next day sorts above
    every round of the previous day (0925.1 > 0924.9...). The year slot
    guarantees any Ultimate release outranks the entire legacy 2.x era.
    """
    return (int(year), int(mmdd[:2]), int(mmdd[2:]) * 1000 + int(build_round), 0)


def format_display_version(version_str: str) -> str:
    """Bare Ultimate id -> branded display form; anything else unchanged.

    '2026.0924.1' -> 'Ultimate 2026 (Build 0924.1)'; legacy '2.7.4-SP3'
    passes through untouched, so old releases display exactly as before.
    """
    match = re.fullmatch(r"(\d{4})\.(\d{4})\.(\d+)", (version_str or "").strip())
    if match:
        year, mmdd, build_round = match.groups()
        return f"Ultimate {int(year)} (Build {mmdd}.{build_round})"
    return (version_str or "").strip()


def version_label(version_str: str) -> str:
    """Ready-to-print version for UI labels.

    Ultimate releases show the full branding — 'Ultimate 2026 (Build 0924.1)';
    legacy releases keep the historical 'v' prefix — 'v2.7.4-SP3' — exactly
    as they always displayed.
    """
    display = format_display_version(version_str)
    if display != (version_str or "").strip():
        return display
    return f"v{display}"


def _parse_version(version_str: str) -> Optional[Tuple[int, int, int, int]]:
    """Parse a version string into a comparable tuple.

    Understands the formats this project actually publishes:
      Ultimate era : '2026.0924.1' (bare id, used in tags) and the branded
                     'Ultimate 2026 (Build 0924.1)' form.
      Legacy era   : '2.7.4', 'v2.7.4', '2.7.4-SP3' and old '2.7.2_SP1'
                     tags (underscore separator).

    The stability-pack suffix is significant for ordering: 2.7.4-SP3 sorts
    above 2.7.4, and 2.7.2-SP2 above 2.7.2-SP1. Ultimate years sort above
    every legacy 2.x version by construction.

    Returns None when nothing numeric can be extracted, so a garbage tag
    can never masquerade as version 0.0.0 (which used to make every
    release look like an update — or none at all).
    """
    if not version_str:
        return None
    cleaned = re.sub(r"[_\s]+", "-", version_str.strip())
    cleaned = re.sub(r"^[vV]", "", cleaned)
    ultimate = _ULTIMATE_RE.match(cleaned)
    if ultimate:
        return _ultimate_tuple(*ultimate.groups())
    core = re.split(r"[-+]", cleaned)[0]
    parts = core.split(".")
    try:
        nums = [int(x) for x in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    sp = 0
    m = _SP_RE.search(cleaned)
    if m:
        sp = int(m.group(1))
    return (nums[0], nums[1], nums[2], sp)


def check_for_updates(silent: bool = True) -> Optional[UpdateInfo]:
    """
    Check GitHub releases API for a newer version.

    Args:
        silent: If True, only check in background. If False, show errors.

    Returns:
        UpdateInfo if update available, None otherwise.
    """
    current = _parse_version(CURRENT_VERSION)
    if current is None:
        # No usable local version (e.g. fresh source checkout without
        # version.txt) — refuse to guess, never nag the user.
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": f"{APP_NAME}-UpdateChecker/{CURRENT_VERSION}"
        }

        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        latest_version = str(data.get('tag_name', '')).lstrip('vV')
        if not latest_version:
            return None

        latest = _parse_version(latest_version)
        if latest is None or latest <= current:
            return None

        return UpdateInfo(
            version=latest_version,
            tag=data.get('tag_name', ''),
            url=data.get('html_url', ''),
            body=data.get('body', ''),
            published_at=data.get('published_at', '')
        )

    except (URLError, json.JSONDecodeError, KeyError, OSError) as e:
        if not silent:
            print(f"Update check failed: {e}")
        return None


def check_for_updates_async(callback, silent: bool = True) -> threading.Thread:
    """
    Check for updates in a background thread.

    Args:
        callback: Function to call with UpdateInfo or None
        silent: If True, suppress errors

    Returns:
        The background thread
    """
    def _check():
        result = check_for_updates(silent=silent)
        callback(result)

    thread = threading.Thread(target=_check, daemon=True)
    thread.start()
    return thread


def open_download_page(url: str = None) -> None:
    """Open the download page in the default browser."""
    if url is None:
        url = f"https://github.com/{GITHUB_REPO}/releases/latest"
    webbrowser.open(url)


def format_release_notes(body: str, max_length: int = 500) -> str:
    """Format release notes for display, truncating if needed."""
    if not body:
        return "No release notes available."

    # Remove markdown headers formatting for plain text
    cleaned = re.sub(r'^#+\s+', '', body, flags=re.MULTILINE)

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rsplit(' ', 1)[0] + '...'

    return cleaned
