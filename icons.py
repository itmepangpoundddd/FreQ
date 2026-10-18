#!/usr/bin/env python3
"""
Icons for FreQ — Emoji labels for buttons.
Returns emoji strings used as button text (no image rendering).
"""

from __future__ import annotations

from typing import Optional


# ═══════════════════════════════════════════════
# All icon functions return None (no images)
# ═══════════════════════════════════════════════

ICON_PATHS: dict[str, str] = {}


def get_pil_icon(name: str, size: int = 24, color: str = "#e8edf4") -> Optional["Image.Image"]:
    return None


def get_tk_image(name: str, size: int = 24, color: str = "#e8edf4"):
    return None


def cached_icon(name: str, size: int = 24, color: str = "#e8edf4"):
    return None
