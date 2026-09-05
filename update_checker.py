#!/usr/bin/env python3
"""
Auto-Update Checker for FreQ
Checks GitHub releases API on launch and notifies user if a new version is available.
"""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError


APP_NAME = "FreQ"
CURRENT_VERSION = "2.5.46"
GITHUB_REPO = "SocieticsTv/freq"
CHECK_INTERVAL = 24 * 3600  # Check once per day (in seconds)


@dataclass
class UpdateInfo:
    """Holds information about an available update."""
    version: str
    tag: str
    url: str
    body: str
    published_at: str


def _parse_version(version_str: str) -> tuple[int, ...]:
    """
    Parse version string into tuple for comparison.
    Handles formats like '1.1.0', 'v1.1.0', '1.1.0-beta', etc.
    """
    # Remove 'v' prefix and any suffix after '-'
    cleaned = re.sub(r'^v', '', version_str)
    cleaned = re.split(r'[-+]', cleaned)[0]
    try:
        return tuple(int(x) for x in cleaned.split('.'))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def check_for_updates(silent: bool = True) -> Optional[UpdateInfo]:
    """
    Check GitHub releases API for a newer version.
    
    Args:
        silent: If True, only check in background. If False, show errors.
    
    Returns:
        UpdateInfo if update available, None otherwise.
    """
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": f"{APP_NAME}-UpdateChecker/{CURRENT_VERSION}"
        }
        
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            latest_version = data.get('tag_name', '').lstrip('v')
            if not latest_version:
                return None
            
            current = _parse_version(CURRENT_VERSION)
            latest = _parse_version(latest_version)
            
            if latest > current:
                return UpdateInfo(
                    version=latest_version,
                    tag=data.get('tag_name', ''),
                    url=data.get('html_url', ''),
                    body=data.get('body', ''),
                    published_at=data.get('published_at', '')
                )
            
            return None
            
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
