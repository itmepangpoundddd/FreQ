#!/usr/bin/env python3
"""
LiveLAN — Remote Control System for FreQ (like vMix LiveLan)
Control your radio from any device on the local network.

Features:
  - Live now playing display with progress bar
  - Queue management (reorder, remove, add)
  - Volume and playback controls
  - Stream status monitoring
  - Mobile-friendly responsive design
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Optional, Callable

from flask import Flask, jsonify, render_template_string, request


# ═══════════════════════════════════════════════
# LiveLAN HTML Template
# ═══════════════════════════════════════════════

LIVELAN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>FreQ LiveLAN</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        :root {
            --bg: #0a0e14; --bg2: #131920; --bg3: #1a2230;
            --accent: #58a6ff; --accent2: #79b8ff;
            --green: #3fb950; --red: #f85149; --orange: #d29922;
            --text: #e8edf4; --text2: #8b95a5; --text3: #4e5769;
            --border: #262d38;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh; overflow-x: hidden;
        }
        
        /* ── Header ── */
        .header {
            background: linear-gradient(135deg, var(--bg2) 0%, #0d1a2d 100%);
            padding: 16px 20px; display: flex; align-items: center; justify-content: space-between;
            border-bottom: 2px solid var(--accent);
        }
        .header .logo { display: flex; align-items: center; gap: 10px; }
        .header .logo span { font-size: 24px; font-weight: bold; color: var(--accent); }
        .header .logo small { color: var(--text2); font-size: 11px; }
        .header .live-badge {
            background: var(--red); color: white; padding: 4px 12px;
            border-radius: 12px; font-size: 11px; font-weight: bold;
            animation: pulse 2s infinite;
        }
        .header .live-badge.offline { background: var(--text3); animation: none; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
        
        /* ── Now Playing ── */
        .now-playing {
            background: var(--bg2); padding: 20px;
            border-bottom: 1px solid var(--border);
        }
        .np-info { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
        .np-art {
            width: 64px; height: 64px; background: var(--bg3);
            border-radius: 12px; display: flex; align-items: center; justify-content: center;
            font-size: 28px;
        }
        .np-text { flex: 1; }
        .np-title { font-size: 18px; font-weight: bold; line-height: 1.2; }
        .np-artist { color: var(--text2); font-size: 13px; margin-top: 2px; }
        .np-source { color: var(--text3); font-size: 11px; margin-top: 4px; }
        
        .progress-bar {
            width: 100%; height: 6px; background: var(--border);
            border-radius: 3px; overflow: hidden; margin-top: 8px;
        }
        .progress-fill {
            height: 100%; background: var(--accent);
            border-radius: 3px; transition: width 0.3s;
        }
        .progress-time {
            display: flex; justify-content: space-between;
            font-size: 11px; color: var(--text3); margin-top: 4px;
        }
        
        /* ── Controls ── */
        .controls {
            display: flex; justify-content: center; align-items: center;
            gap: 12px; padding: 16px;
            background: var(--bg2); border-bottom: 1px solid var(--border);
        }
        .ctrl-btn {
            width: 48px; height: 48px; border-radius: 50%;
            border: none; background: var(--bg3); color: var(--text);
            font-size: 20px; cursor: pointer; transition: all 0.15s;
            display: flex; align-items: center; justify-content: center;
        }
        .ctrl-btn:hover { background: var(--border); }
        .ctrl-btn:active { transform: scale(0.92); }
        .ctrl-btn.play { width: 64px; height: 64px; background: var(--accent); font-size: 28px; }
        .ctrl-btn.play:hover { background: var(--accent2); }
        .ctrl-btn.play.active { background: var(--red); }
        
        /* ── Volume ── */
        .volume-section {
            display: flex; align-items: center; gap: 12px;
            padding: 12px 20px; background: var(--bg2);
            border-bottom: 1px solid var(--border);
        }
        .volume-slider {
            flex: 1; -webkit-appearance: none; height: 6px;
            background: var(--border); border-radius: 3px; outline: none;
        }
        .volume-slider::-webkit-slider-thumb {
            -webkit-appearance: none; width: 20px; height: 20px;
            background: var(--accent); border-radius: 50%; cursor: pointer;
        }
        .vol-label { min-width: 40px; text-align: center; font-size: 12px; color: var(--text2); }
        
        /* ── Queue ── */
        .section-header {
            padding: 12px 20px; font-size: 13px; font-weight: bold;
            color: var(--text2); background: var(--bg);
            border-bottom: 1px solid var(--border);
            display: flex; justify-content: space-between; align-items: center;
        }
        .queue-list { max-height: 40vh; overflow-y: auto; }
        .queue-item {
            display: flex; align-items: center; padding: 12px 20px;
            border-bottom: 1px solid var(--border); cursor: pointer;
            transition: background 0.15s; gap: 12px;
        }
        .queue-item:hover { background: var(--bg3); }
        .queue-item.active { background: rgba(88, 166, 255, 0.15); border-left: 3px solid var(--accent); }
        .queue-item .num { width: 24px; font-size: 12px; color: var(--text3); text-align: center; }
        .queue-item .info { flex: 1; min-width: 0; }
        .queue-item .title { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .queue-item .meta { font-size: 11px; color: var(--text3); margin-top: 2px; }
        .queue-item .dur { font-size: 11px; color: var(--text3); }
        .queue-item .play-indicator { color: var(--accent); font-size: 14px; }
        
        /* ── Status ── */
        .status-bar {
            padding: 8px 20px; background: var(--bg2);
            font-size: 11px; color: var(--text3);
            display: flex; justify-content: space-between;
            border-top: 1px solid var(--border);
            position: fixed; bottom: 0; left: 0; right: 0;
        }
        
        /* ── Loading ── */
        .loading { text-align: center; padding: 40px; color: var(--text3); }
        
        /* ── Scrollbar ── */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: var(--bg); }
        ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    </style>
</head>
<body>
    <!-- Header -->
    <div class="header">
        <div class="logo">
            <span>FreQ</span>
            <small>LiveLAN Control</small>
        </div>
        <div class="live-badge offline" id="liveBadge">OFFLINE</div>
    </div>
    
    <!-- Now Playing -->
    <div class="now-playing">
        <div class="np-info">
            <div class="np-art">♫</div>
            <div class="np-text">
                <div class="np-title" id="npTitle">No song playing</div>
                <div class="np-artist" id="npArtist">Select a song to start</div>
                <div class="np-source" id="npSource"></div>
            </div>
        </div>
        <div class="progress-bar">
            <div class="progress-fill" id="progressFill" style="width: 0%"></div>
        </div>
        <div class="progress-time">
            <span id="timeCur">0:00</span>
            <span id="timeTotal">0:00</span>
        </div>
    </div>
    
    <!-- Playback Controls -->
    <div class="controls">
        <button class="ctrl-btn" onclick="cmd('shuffle')" title="Shuffle">⇄</button>
        <button class="ctrl-btn" onclick="cmd('prev')" title="Previous">⏮</button>
        <button class="ctrl-btn play" id="playBtn" onclick="cmd('toggle')">▶</button>
        <button class="ctrl-btn" onclick="cmd('next')" title="Next">⏭</button>
        <button class="ctrl-btn" onclick="cmd('repeat')" title="Repeat">↻</button>
    </div>
    
    <!-- Volume -->
    <div class="volume-section">
        <span style="font-size:16px">🔈</span>
        <input type="range" class="volume-slider" min="0" max="100" value="80" 
               id="volSlider" oninput="setVol(this.value)">
        <span class="vol-label" id="volLabel">80%</span>
        <span style="font-size:16px">🔊</span>
        <button class="ctrl-btn" style="width:36px;height:36px;font-size:14px" 
                onclick="cmd('mute')">🔇</button>
    </div>
    
    <!-- Queue Header -->
    <div class="section-header">
        <span>📋 Queue (<span id="queueCount">0</span> songs)</span>
        <span id="queueDuration"></span>
    </div>
    
    <!-- Queue List -->
    <div class="queue-list" id="queueList">
        <div class="loading">Loading queue...</div>
    </div>
    
    <!-- Status Bar -->
    <div class="status-bar">
        <span id="statusLeft">FreQ LiveLAN v2.5.47</span>
        <span id="statusRight"></span>
    </div>
    
    <script>
        let isPlaying = false;
        let currentIdx = -1;
        
        // ── API Calls ──
        async function cmd(action) {
            try {
                const res = await fetch('/api/' + action, { method: 'POST' });
                const data = await res.json();
                if (action === 'toggle') isPlaying = data.playing;
                refresh();
            } catch(e) { console.error(e); }
        }
        
        async function setVol(val) {
            try {
                await fetch('/api/volume', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ volume: parseInt(val) })
                });
                document.getElementById('volLabel').textContent = val + '%';
            } catch(e) { console.error(e); }
        }
        
        async function playSong(idx) {
            try {
                await fetch('/api/play', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ index: idx })
                });
                setTimeout(refresh, 300);
            } catch(e) { console.error(e); }
        }
        
        async function removeSong(idx) {
            try {
                await fetch('/api/queue/' + idx, { method: 'DELETE' });
                setTimeout(refresh, 300);
            } catch(e) { console.error(e); }
        }
        
        // ── Refresh ──
        async function refresh() {
            try {
                // Get status
                const statusRes = await fetch('/api/status');
                const status = await statusRes.json();
                
                // Update now playing
                document.getElementById('npTitle').textContent = status.title || 'No song playing';
                document.getElementById('npArtist').textContent = status.artist || 'Select a song to start';
                document.getElementById('npSource').textContent = status.source ? `Source: ${status.source}` : '';
                
                // Update play button
                isPlaying = status.playing;
                const playBtn = document.getElementById('playBtn');
                playBtn.textContent = isPlaying ? '⏸' : '▶';
                playBtn.className = 'ctrl-btn play' + (isPlaying ? ' active' : '');
                
                // Update progress
                if (status.duration > 0) {
                    const pct = (status.position / status.duration * 100) || 0;
                    document.getElementById('progressFill').style.width = pct + '%';
                    document.getElementById('timeCur').textContent = fmtDur(status.position);
                    document.getElementById('timeTotal').textContent = fmtDur(status.duration);
                }
                
                // Update volume
                if (status.volume !== undefined) {
                    document.getElementById('volSlider').value = status.volume;
                    document.getElementById('volLabel').textContent = Math.round(status.volume) + '%';
                }
                
                // Update live badge
                const badge = document.getElementById('liveBadge');
                if (status.streaming) {
                    badge.textContent = '● LIVE';
                    badge.className = 'live-badge';
                } else {
                    badge.textContent = 'OFFLINE';
                    badge.className = 'live-badge offline';
                }
                
                // Update queue
                const queueRes = await fetch('/api/queue');
                const queueData = await queueRes.json();
                currentIdx = queueData.current_index;
                
                document.getElementById('queueCount').textContent = queueData.queue.length;
                
                const totalDur = queueData.queue.reduce((a, s) => a + (s.duration || 0), 0);
                document.getElementById('queueDuration').textContent = fmtDur(totalDur);
                
                const list = document.getElementById('queueList');
                if (!queueData.queue.length) {
                    list.innerHTML = '<div class="loading">Queue is empty — add songs from FreQ</div>';
                    return;
                }
                
                list.innerHTML = queueData.queue.map((s, i) => `
                    <div class="queue-item ${i === currentIdx ? 'active' : ''}" onclick="playSong(${i})">
                        <div class="num">${i === currentIdx ? '<span class="play-indicator">▶</span>' : (i+1)}</div>
                        <div class="info">
                            <div class="title">${esc(s.title || 'Untitled')}</div>
                            <div class="meta">${esc(s.artist || s.source || '')}</div>
                        </div>
                        <div class="dur">${fmtDur(s.duration)}</div>
                        <button class="ctrl-btn" style="width:28px;height:28px;font-size:12px" 
                                onclick="event.stopPropagation();removeSong(${i})">✕</button>
                    </div>
                `).join('');
                
                // Status bar
                document.getElementById('statusRight').textContent = 
                    status.listeners ? `${status.listeners} listeners` : '';
                    
            } catch(e) { console.error('Refresh failed:', e); }
        }
        
        // ── Helpers ──
        function fmtDur(s) {
            if (!s || s <= 0) return '--:--';
            const m = Math.floor(s / 60);
            const sec = Math.floor(s % 60);
            return m + ':' + String(sec).padStart(2, '0');
        }
        
        function esc(str) {
            const d = document.createElement('div');
            d.textContent = str;
            return d.innerHTML;
        }
        
        // ── Auto Refresh ──
        setInterval(refresh, 2000);
        refresh();
    </script>
</body>
</html>
"""


class LiveLAN:
    """LiveLAN remote control server for FreQ (like vMix LiveLan)."""
    
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
            return render_template_string(LIVELAN_HTML)
        
        @self.flask_app.route("/api/status")
        def status():
            if not self.app_instance:
                return jsonify({
                    "title": "No app", "artist": "", "playing": False,
                    "volume": 80, "position": 0, "duration": 0,
                    "source": "", "streaming": False, "listeners": 0,
                })
            
            try:
                app = self.app_instance
                current = None
                position = 0
                duration = 0
                
                if hasattr(app, 'rq') and app.rq.queue:
                    idx = app.rq.current_index
                    if 0 <= idx < len(app.rq.queue):
                        current = app.rq.queue[idx]
                        duration = current.duration if current else 0
                        if hasattr(app.audio_player, 'get_position'):
                            position = app.audio_player.get_position()
                
                # Check streaming
                streaming = False
                listeners = 0
                try:
                    from streaming import streamer
                    streaming = streamer.is_streaming
                except Exception:
                    pass
                
                return jsonify({
                    "title": current.title if current else "No song playing",
                    "artist": current.artist if current else "",
                    "source": current.source if current else "",
                    "playing": getattr(app, '_playing', False),
                    "volume": int(app.volume_var.get()) if hasattr(app, 'volume_var') else 80,
                    "position": position,
                    "duration": duration,
                    "streaming": streaming,
                    "listeners": listeners,
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
            return jsonify({"ok": True})
        
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
        
        @self.flask_app.route("/api/queue/<int:idx>", methods=["DELETE"])
        def remove_from_queue(idx):
            if self.app_instance and hasattr(self.app_instance, 'rq'):
                self.app_instance.after(0, lambda: self._remove_song(idx))
            return jsonify({"ok": True})
    
    def _set_volume(self, vol: float):
        """Set volume on the main app."""
        if self.app_instance and hasattr(self.app_instance, 'volume_var'):
            self.app_instance.volume_var.set(vol)
            self.app_instance._on_volume_change(vol)
    
    def _remove_song(self, idx: int):
        """Remove song from queue."""
        if self.app_instance and hasattr(self.app_instance, 'rq'):
            if 0 <= idx < len(self.app_instance.rq.queue):
                self.app_instance.rq.remove_song(idx)
                self.app_instance._refresh_queue()
    
    def start(self) -> str:
        """Start the LiveLAN server."""
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
        """Stop the LiveLAN server."""
        self._running = False
    
    def _get_url(self) -> str:
        """Get the LiveLAN URL."""
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
