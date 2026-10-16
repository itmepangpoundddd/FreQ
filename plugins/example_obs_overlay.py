"""
Example FreQ plugin - OBS Overlay (observe-only).

Writes a small JSON file every time a track starts or finishes so an OBS
overlay (or any tool: Streamlabs, a bot, a website) can show what is on
air. Copy this file into the plugins folder (next to FreQ.exe) and point
your overlay tool at the JSON path below.

OBS setup (simplest reliable route):
  1. Copy this file into the plugins folder.
  2. In OBS, add a Text source driven by a script that reads the JSON
     path below (many free "now playing from JSON" scripts exist), or
     use OBS's browser source pointed at a tiny local page that fetches
     this file.
  3. Reload FreQ plugins (Settings -> Reload) and play a song.

The write is ATOMIC (tmp file + os.replace): a reader like OBS can never
catch a half-written JSON, no matter the timing.

Only the documented PluginContext surface is used (ctx.log / ctx.notify /
ctx.snapshot). Plugins cannot control playback - by design.
"""
import json
import os
import tempfile
from pathlib import Path

PLUGIN_NAME = "OBS Overlay Feed"
PLUGIN_VERSION = "1.0"

# Where the overlay JSON goes. OBS and the plugin just need to agree on
# this path (per-user folder: no admin rights needed).
OUTPUT = Path.home() / "FreQLogs" / "now_playing.json"


def _write_atomic(payload: dict) -> None:
    """Replace OUTPUT in one filesystem step — never a torn file."""
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(OUTPUT.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_name, OUTPUT)   # atomic on Windows (same volume)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def on_track_started(ctx, song=None):
    song = song or {}
    try:
        _write_atomic({
            "event": "track_started",
            "title": song.get("title", ""),
            "artist": song.get("artist", ""),
            "source": song.get("source", ""),
            "duration": song.get("duration", 0.0),
            "duration_str": song.get("duration_str", ""),
            "playing": bool(ctx.snapshot().get("playing")),
        })
        ctx.log(f"overlay updated: {song.get('title', '?')}")
    except OSError as error:
        ctx.log(f"overlay write failed: {error}")


def on_track_finished(ctx, song=None):
    song = song or {}
    try:
        _write_atomic({
            "event": "track_finished",
            "title": song.get("title", ""),
            "playing": bool(ctx.snapshot().get("playing")),
        })
    except OSError:
        pass


def on_shutdown(ctx):
    try:
        _write_atomic({"event": "shutdown", "title": ""})
    except OSError:
        pass
