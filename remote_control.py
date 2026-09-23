#!/usr/bin/env python3
"""
Web Remote Control for FreQ
Control the app from mobile browser via Flask.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template_string, request


# ──────────────────────────────────────────────
# HTML Template for Mobile Remote
# ──────────────────────────────────────────────

REMOTE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FreQ Remote Control</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0a0e14 0%, #1a1f2e 100%);
            color: #e8edf4;
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 20px 0;
        }
        .header h1 {
            font-size: 28px;
            color: #58a6ff;
            margin-bottom: 5px;
        }
        .header p {
            color: #8b95a5;
            font-size: 14px;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 20px;
            margin: 15px 0;
            backdrop-filter: blur(10px);
        }
        .now-playing {
            background: linear-gradient(135deg, #1f6feb 0%, #0d1117 100%);
            text-align: center;
            padding: 25px;
        }
        .now-playing .title {
            font-size: 20px;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .now-playing .artist {
            color: #8b95a5;
            font-size: 14px;
        }
        .now-playing .status {
            color: #3fb950;
            font-size: 12px;
            margin-top: 8px;
        }
        .controls {
            display: flex;
            justify-content: center;
            gap: 15px;
            margin-top: 20px;
        }
        .btn {
            background: rgba(255,255,255,0.1);
            border: none;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            font-size: 24px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn:hover {
            background: rgba(88, 166, 255, 0.3);
            transform: scale(1.1);
        }
        .btn.play {
            background: #58a6ff;
            width: 70px;
            height: 70px;
            font-size: 28px;
        }
        .btn.play:hover {
            background: #79b8ff;
        }
        .volume-section {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        .volume-slider {
            flex: 1;
            -webkit-appearance: none;
            height: 8px;
            background: #262d38;
            border-radius: 4px;
            outline: none;
        }
        .volume-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 24px;
            height: 24px;
            background: #58a6ff;
            border-radius: 50%;
            cursor: pointer;
        }
        .volume-label {
            min-width: 50px;
            text-align: center;
            color: #8b95a5;
        }
        .queue-list {
            max-height: 300px;
            overflow-y: auto;
        }
        .queue-item {
            display: flex;
            align-items: center;
            padding: 12px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            cursor: pointer;
            transition: background 0.2s;
        }
        .queue-item:hover {
            background: rgba(255,255,255,0.05);
        }
        .queue-item.active {
            background: rgba(88, 166, 255, 0.2);
            border-left: 3px solid #58a6ff;
        }
        .queue-item .num {
            width: 30px;
            color: #4e5769;
        }
        .queue-item .info {
            flex: 1;
        }
        .queue-item .info .title {
            font-size: 14px;
        }
        .queue-item .info .artist {
            font-size: 12px;
            color: #8b95a5;
        }
        .quick-actions {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
        }
        .action-btn {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            padding: 15px;
            color: #e8edf4;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .action-btn:hover {
            background: rgba(88, 166, 255, 0.2);
            border-color: #58a6ff;
        }
        .action-btn.danger {
            border-color: rgba(248, 81, 73, 0.3);
        }
        .action-btn.danger:hover {
            background: rgba(248, 81, 73, 0.2);
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🎙️ FreQ Remote</h1>
        <p>Radio Playlist Manager</p>
    </div>

    <div class="card now-playing">
        <div class="title" id="song-title">No song playing</div>
        <div class="artist" id="song-artist">Select a song to start</div>
        <div class="status" id="song-status"></div>
        
        <div class="controls">
            <button class="btn" onclick="sendCommand('prev')">⏮</button>
            <button class="btn play" onclick="sendCommand('toggle')" id="btn-play">▶️</button>
            <button class="btn" onclick="sendCommand('next')">⏭</button>
        </div>
    </div>

    <div class="card">
        <h3 style="margin-bottom: 15px;">🔊 Volume</h3>
        <div class="volume-section">
            <span>🔈</span>
            <input type="range" class="volume-slider" min="0" max="100" value="80" 
                   id="volume-slider" onchange="setVolume(this.value)">
            <span class="volume-label" id="volume-label">80%</span>
            <span>🔊</span>
        </div>
    </div>

    <div class="card">
        <h3 style="margin-bottom: 15px;">📋 Queue</h3>
        <div class="queue-list" id="queue-list">
            <p style="color: #8b95a5; text-align: center;">Loading...</p>
        </div>
    </div>

    <div class="card">
        <h3 style="margin-bottom: 15px;">⚡ Quick Actions</h3>
        <div class="quick-actions">
            <button class="action-btn" onclick="sendCommand('shuffle')">🔀 Shuffle</button>
            <button class="action-btn" onclick="sendCommand('repeat')">🔁 Repeat</button>
            <button class="action-btn" onclick="sendCommand('mute')">🔇 Mute</button>
            <button class="action-btn danger" onclick="sendCommand('stop')">⏹ Stop</button>
        </div>
    </div>

    <script>
        let isPlaying = false;
        let currentVolume = 80;

        async function sendCommand(cmd) {
            try {
                const res = await fetch('/api/' + cmd, { method: 'POST' });
                const data = await res.json();
                if (cmd === 'toggle') {
                    isPlaying = data.playing;
                    document.getElementById('btn-play').textContent = isPlaying ? '⏸' : '▶️';
                }
                updateStatus();
            } catch (e) {
                console.error(e);
            }
        }

        async function setVolume(val) {
            try {
                await fetch('/api/volume', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ volume: parseInt(val) })
                });
                currentVolume = val;
                document.getElementById('volume-label').textContent = val + '%';
            } catch (e) {
                console.error(e);
            }
        }

        async function updateStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                document.getElementById('song-title').textContent = data.title || 'No song playing';
                document.getElementById('song-artist').textContent = data.artist || 'Select a song to start';
                document.getElementById('song-status').textContent = data.status || '';
                document.getElementById('btn-play').textContent = data.playing ? '⏸' : '▶️';
                isPlaying = data.playing;
                
                if (data.volume !== undefined) {
                    document.getElementById('volume-slider').value = data.volume;
                    document.getElementById('volume-label').textContent = data.volume + '%';
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function loadQueue() {
            try {
                const res = await fetch('/api/queue');
                const data = await res.json();
                const list = document.getElementById('queue-list');
                
                if (!data.queue || data.queue.length === 0) {
                    list.innerHTML = '<p style="color: #8b95a5; text-align: center;">Queue is empty</p>';
                    return;
                }
                
                list.innerHTML = data.queue.map((song, i) => `
                    <div class="queue-item ${i === data.current_index ? 'active' : ''}" 
                         onclick="playSong(${i})">
                        <div class="num">${i === data.current_index ? '▶️' : (i+1)}</div>
                        <div class="info">
                            <div class="title">${song.title || 'Untitled'}</div>
                            <div class="artist">${song.artist || song.source || 'Unknown'}</div>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error(e);
            }
        }

        async function playSong(index) {
            try {
                await fetch('/api/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ index })
                });
                setTimeout(updateStatus, 500);
                setTimeout(loadQueue, 500);
            } catch (e) {
                console.error(e);
            }
        }

        // Auto-refresh
        setInterval(updateStatus, 2000);
        setInterval(loadQueue, 5000);

        // Initial load
        updateStatus();
        loadQueue();
    </script>
</body>
</html>
"""


class RemoteControl:
    """Web remote control server for FreQ."""
    
    def __init__(self, app_instance=None, host: str = "127.0.0.1", port: int = 5050):
        self.app_instance = app_instance
        self.host = host
        self.port = port
        self.flask_app = Flask(__name__)
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup Flask routes."""
        
        @self.flask_app.route("/")
        def index():
            return render_template_string(REMOTE_HTML)
        
        @self.flask_app.route("/api/status")
        def status():
            if not self.app_instance:
                return jsonify({"title": "No app", "playing": False})
            
            try:
                current = None
                if hasattr(self.app_instance, 'rq') and self.app_instance.rq.queue:
                    idx = self.app_instance.rq.current_index
                    if 0 <= idx < len(self.app_instance.rq.queue):
                        current = self.app_instance.rq.queue[idx]
                
                return jsonify({
                    "title": current.title if current else "No song playing",
                    "artist": current.artist if current else "",
                    "status": "Playing" if getattr(self.app_instance, '_playing', False) else "Paused",
                    "playing": getattr(self.app_instance, '_playing', False),
                    "volume": int(self.app_instance.volume_var.get()) if hasattr(self.app_instance, 'volume_var') else 80,
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        @self.flask_app.route("/api/queue")
        def queue():
            if not self.app_instance or not hasattr(self.app_instance, 'rq'):
                return jsonify({"queue": [], "current_index": 0})
            
            try:
                return jsonify({
                    "queue": [s.to_dict() for s in self.app_instance.rq.queue],
                    "current_index": self.app_instance.rq.current_index,
                })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        
        @self.flask_app.route("/api/toggle", methods=["POST"])
        def toggle():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._toggle_play)
            return jsonify({"ok": True, "playing": getattr(self.app_instance, '_playing', False)})
        
        @self.flask_app.route("/api/next", methods=["POST"])
        def next_track():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._next_track)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/prev", methods=["POST"])
        def prev_track():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._prev_track)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/stop", methods=["POST"])
        def stop():
            if self.app_instance and self.app_instance._playing:
                self.app_instance.after(0, self.app_instance._toggle_play)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/mute", methods=["POST"])
        def mute():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._toggle_mute)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/shuffle", methods=["POST"])
        def shuffle():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._toggle_shuffle)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/repeat", methods=["POST"])
        def repeat():
            if self.app_instance:
                self.app_instance.after(0, self.app_instance._toggle_repeat)
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/play", methods=["POST"])
        def play():
            data = request.json or {}
            index = data.get("index")
            if self.app_instance and index is not None:
                self.app_instance.after(0, lambda: self.app_instance._play_index(index))
            return jsonify({"ok": True})
        
        @self.flask_app.route("/api/volume", methods=["POST"])
        def volume():
            data = request.json or {}
            vol = data.get("volume", 80)
            if self.app_instance and hasattr(self.app_instance, 'volume_var'):
                self.app_instance.after(0, lambda: self._set_volume(vol))
            return jsonify({"ok": True, "volume": vol})
    
    def _set_volume(self, vol: float):
        """Set volume on the main app."""
        if self.app_instance and hasattr(self.app_instance, 'volume_var'):
            self.app_instance.volume_var.set(vol)
            self.app_instance._on_volume_change(vol)
    
    def start(self) -> str:
        """Start the remote control server."""
        if self._running:
            return self._get_url()
        
        self._running = True
        self._server_thread = threading.Thread(
            target=lambda: self.flask_app.run(
                host=self.host, port=self.port,
                debug=False, use_reloader=False
            ),
            daemon=True
        )
        self._server_thread.start()
        
        return self._get_url()
    
    def stop(self):
        """Stop the remote control server."""
        self._running = False
        # Flask doesn't have a clean stop, but thread is daemon so it'll die
    
    def _get_url(self) -> str:
        """Get the remote control URL."""
        local_ip = self._get_local_ip()
        return f"http://{local_ip}:{self.port}"
    
    @staticmethod
    def _get_local_ip() -> str:
        """Get the local network IP."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
