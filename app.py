#!/usr/bin/env python3
"""
Flask Web GUI for FreQ — Radio Playlist Manager
Run: python app.py → opens http://localhost:5000
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from radio_manager import (
    RadioQueue,
    Song,
    SongStatus,
    YouTubeManager,
    generate_demo_songs,
)
from log_handler import setup_logging, memory_handler

app = Flask(__name__)
rq = RadioQueue()

# Initialize logging for web mode
setup_logging()

# Load existing data if available
if rq._save_path.exists():
    rq.load_from_file()

# Lock for long-running YouTube operations
yt_lock = threading.Lock()


# ──────────────────────────────────────────────
# Main page
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ──────────────────────────────────────────────
# API: Queue
# ──────────────────────────────────────────────

@app.route("/api/queue", methods=["GET"])
def get_queue():
    return jsonify({
        "queue": [s.to_dict() for s in rq.queue],
        "current_index": rq.current_index,
        "total_duration": sum(s.duration for s in rq.queue),
    })


@app.route("/api/queue", methods=["POST"])
def add_to_queue():
    data = request.json
    song = Song(
        title=data.get("title", "Untitled"),
        artist=data.get("artist", ""),
        duration=float(data.get("duration", 0)),
        source=data.get("source", "local"),
        url=data.get("url", ""),
        thumbnail=data.get("thumbnail", ""),
    )
    rq.add_song(song)
    return jsonify({"ok": True, "song": song.to_dict()})


@app.route("/api/queue/<int:idx>", methods=["DELETE"])
def remove_from_queue(idx):
    removed = rq.remove_song(idx)
    if removed:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid index"}), 400


@app.route("/api/queue/reorder", methods=["POST"])
def reorder_queue():
    """Move song from from_idx to to_idx"""
    data = request.json
    from_idx = data.get("from")
    to_idx = data.get("to")
    if from_idx is None or to_idx is None:
        return jsonify({"ok": False, "error": "Missing from/to"}), 400
    if not rq._valid_index(from_idx) or not rq._valid_index(to_idx):
        return jsonify({"ok": False, "error": "Invalid index"}), 400
    rq.move_song(from_idx, to_idx)
    return jsonify({"ok": True})


@app.route("/api/queue/clear", methods=["POST"])
def clear_queue():
    rq.clear_queue()
    return jsonify({"ok": True})


@app.route("/api/queue/insert", methods=["POST"])
def insert_to_queue():
    data = request.json
    idx = int(data.get("position", 0))
    song = Song(
        title=data.get("title", "Untitled"),
        artist=data.get("artist", ""),
        duration=float(data.get("duration", 0)),
        source=data.get("source", "local"),
        url=data.get("url", ""),
        thumbnail=data.get("thumbnail", ""),
    )
    if not rq.insert_song(idx, song):
        return jsonify({"ok": False, "error": "Invalid position"}), 400
    return jsonify({"ok": True, "song": song.to_dict()})


# ──────────────────────────────────────────────
# API: Playback
# ──────────────────────────────────────────────

@app.route("/api/play", methods=["POST"])
def play():
    data = request.json or {}
    idx = data.get("index")
    rq.play(idx)
    return jsonify({"ok": True, "current_index": rq.current_index})


@app.route("/api/pause", methods=["POST"])
def pause():
    rq.pause()
    return jsonify({"ok": True})


@app.route("/api/next", methods=["POST"])
def next_song():
    rq.skip_next()
    return jsonify({"ok": True, "current_index": rq.current_index})


@app.route("/api/prev", methods=["POST"])
def prev_song():
    rq.skip_previous()
    return jsonify({"ok": True, "current_index": rq.current_index})


# ──────────────────────────────────────────────
# API: YouTube
# ──────────────────────────────────────────────

@app.route("/api/youtube/add", methods=["POST"])
def add_youtube():
    """Add song from YouTube URL (single video)"""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "URL is required"}), 400

    if not YouTubeManager.is_available():
        return jsonify({"ok": False, "error": "yt-dlp not installed"}), 500

    if YouTubeManager.is_playlist_url(url):
        return jsonify({"ok": False, "error": "This is a playlist. Use /api/youtube/playlist instead"}), 400

    try:
        song = YouTubeManager.extract_video_info(url)
        if song:
            rq.add_song(song)
            return jsonify({"ok": True, "song": song.to_dict()})
        return jsonify({"ok": False, "error": "Cannot fetch video info"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/youtube/playlist", methods=["POST"])
def add_youtube_playlist():
    """Add full playlist from YouTube"""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "URL is required"}), 400

    if not YouTubeManager.is_available():
        return jsonify({"ok": False, "error": "yt-dlp not installed"}), 500

    try:
        songs = YouTubeManager.extract_playlist(url)
        if songs:
            rq.add_songs(songs)
            return jsonify({
                "ok": True,
                "count": len(songs),
                "songs": [s.to_dict() for s in songs],
            })
        return jsonify({"ok": False, "error": "No videos found in playlist"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ──────────────────────────────────────────────
# API: Search & Library
# ──────────────────────────────────────────────

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})

    ql = q.lower()
    results = [
        s.to_dict() for s in rq.library
        if ql in s.title.lower() or ql in s.artist.lower()
    ]
    return jsonify({"results": results})


@app.route("/api/library")
def get_library():
    return jsonify({
        "songs": [s.to_dict() for s in rq.library],
        "count": len(rq.library),
    })


@app.route("/api/history")
def get_history():
    return jsonify({
        "history": [s.to_dict() for s in rq.history[-50:]],
    })


# ──────────────────────────────────────────────
# API: Demo
# ──────────────────────────────────────────────

@app.route("/api/demo", methods=["POST"])
def add_demo():
    demos = generate_demo_songs()
    rq.add_songs(demos)
    return jsonify({"ok": True, "count": len(demos)})


# ──────────────────────────────────────────────
# API: Save / Load / Preset
# ──────────────────────────────────────────────

@app.route("/api/save", methods=["POST"])
def save():
    rq.save_to_file()
    return jsonify({"ok": True})


@app.route("/api/load", methods=["POST"])
def load():
    rq.load_from_file()
    return jsonify({"ok": True, "queue_count": len(rq.queue)})


@app.route("/api/preset/save", methods=["POST"])
def preset_save():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name is required"}), 400
    rq.save_preset(name)
    return jsonify({"ok": True})


@app.route("/api/preset/load", methods=["POST"])
def preset_load():
    data = request.json
    name = data.get("name", "").strip()
    rq.load_preset(name)
    return jsonify({"ok": True, "queue_count": len(rq.queue)})


@app.route("/api/preset/delete", methods=["POST"])
def preset_delete():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Name is required"}), 400
    deleted = rq.delete_preset(name)
    if deleted:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": f"Preset '{name}' not found"}), 404


@app.route("/api/presets")
def list_presets():
    return jsonify({
        "presets": {
            name: len(ids) for name, ids in rq.presets.items()
        }
    })


# ──────────────────────────────────────────────
# API: Logs
# ──────────────────────────────────────────────

@app.route("/api/logs")
def get_logs():
    """Return application log entries."""
    level = request.args.get("level", "DEBUG").upper()
    last_n = request.args.get("last", None, type=int)
    entries = memory_handler.get_entries(last_n=last_n, min_level=level)
    return jsonify({
        "logs": [e.to_dict() for e in entries],
        "count": len(entries),
        "total": memory_handler.count,
    })


@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    """Clear all log entries."""
    memory_handler.clear()
    return jsonify({"ok": True})


# ──────────────────────────────────────────────
# Entry
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, port=5000)
