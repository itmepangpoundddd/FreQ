"""Combo soak: Go Live (native stream) + Auto air-check TOGETHER.

Both share the same loopback tap — this proves they never interfere:
the stream survives server kills while the air-check file keeps growing,
and a clean shutdown leaves a valid recording + no orphaned encoder.
Runs ~75 s. Exit 0 = all clear.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import native_aircheck as na  # noqa: E402
import native_audio_capture as nac  # noqa: E402
import native_stream as ns  # noqa: E402
import wave  # noqa: E402

HOST, PORT = "127.0.0.1", 8166
FFMPEG = r"C:\Program Files (x86)\FreQ\ffmpeg\ffmpeg.exe"
FOLDER = tempfile.mkdtemp(prefix="freq_combo_")

ok = True


def check(name, cond, detail=""):
    global ok
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    ok = ok and cond


active: list[socket.socket] = []
headers: list[bytes] = []
up = threading.Event()


def serve():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(8)
    srv.settimeout(0.4)
    up.set()
    while up.is_set():
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            continue

        def h(c):
            try:
                first = c.recv(4096)
                if first.startswith(b"SOURCE"):
                    headers.append(first)
                    c.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
                    active.append(c)
                    try:
                        while c.recv(65536):
                            pass
                    except OSError:
                        pass
            except OSError:
                pass
            finally:
                try:
                    c.close()
                except OSError:
                    pass
                if c in active:
                    active.remove(c)

        threading.Thread(target=h, args=(conn,), daemon=True).start()
    srv.close()


def run_all(into=None):
    """Run the combo soak now (standalone or via main suite)."""
    main_block(into)


def main_block(into=None):
    threading.Thread(target=serve, daemon=True).start()
    up.wait(2)
    
    
    import winsound  # noqa: E402
    
    audio_on = threading.Event()
    audio_on.set()
    
    
    def music():
        while audio_on.is_set():
            try:
                winsound.Beep(880, 420)
            except Exception:
                time.sleep(0.3)
    
    
    threading.Thread(target=music, daemon=True).start()
    
    rate = nac.Capture(loopback=True).samplerate
    enc = (f'"{FFMPEG}" -hide_banner -loglevel error '
           f"-f s16le -ar {rate} -ac 2 -i pipe:0 "
           f"-c:a libmp3lame -b:a 128k -f mp3 pipe:1")
    header = "SOURCE /combo ICY/1.0\r\ncontent-type:audio/mpeg\r\n\r\n"
    
    print("— start BOTH: Go Live + auto air-check —")
    ns.stream_start(HOST, PORT, header, enc, 700)
    time.sleep(0.5)
    
    # auto air-check via the REAL GUI helper path
    import gui as gui_mod  # noqa: E402
    import customtkinter as ctk  # noqa: E402
    
    root = ctk.CTk()
    root.withdraw()
    app = gui_mod.RadioApp.__new__(gui_mod.RadioApp)
    for attr in ("tk", "master", "children", "_w", "nametowidget"):
        try:
            setattr(app, attr, getattr(root, attr))
        except Exception:
            pass
    
    
    class _Var:
        def __init__(self, v):
            self.v = v
    
        def get(self):
            return self.v
    
    
    class _Entry:
        def __init__(self, v):
            self.v = v
    
        def get(self):
            return self.v
    
    
    class _Label:
        def __init__(self):
            self.text = ""
    
        def configure(self, text="", text_color=None, **_):
            self.text = text
    
    
    app.aircheck_folder = FOLDER
    app.aircheck_mp3_var = _Var(False)          # keep WAV (easier to validate)
    app.aircheck_keep_entry = _Entry("14")
    app.aircheck_auto_var = _Var(True)
    app.aircheck_auto_status = _Label()
    app.aircheck_files_label = _Label()
    app._last_aircheck_files_refresh = 0.0
    app.aircheck_recorder = None                # nothing manual running
    app.aircheck_last_path = ""
    app._toast = lambda m: None
    app.audio_player = type("P", (), {"emergency_mic_active": lambda self: True})()
    
    import streaming  # noqa: E402
    
    streaming.streamer = type("S", (), {"is_streaming": False})()
    
    app._aircheck_auto_poll()
    deadline = time.time() + 5
    while time.time() < deadline and na.aircheck_state() != 1:
        time.sleep(0.05)
    check("auto air-check started WHILE streaming", na.aircheck_state() == 1)
    # The stream ladder needs a beat to settle (Connecting → Streaming);
    # give it up to 8 s before judging.
    stream_deadline = time.time() + 8
    while time.time() < stream_deadline and ns.stream_state() != 2:
        time.sleep(0.1)
    check("stream is live too", ns.stream_state() == 2,
          f"state={ns.stream_state()} sent={ns.stream_stats()[0]:,}")
    
    print("— kill the stream repeatedly; air-check must not care —")
    kills = 0
    t0 = time.time()
    next_kill = time.time() + 6
    while time.time() - t0 < 55:
        time.sleep(0.5)
        if time.time() >= next_kill:
            for s in list(active):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass
            active.clear()
            kills += 1
            next_kill = time.time() + 6
        if na.aircheck_state() != 1:
            check("air-check unaffected by stream kills", False,
                  f"recorder stopped early at kill {kills}")
            break
    check(f"survived {kills} stream kills with air-check running", kills >= 5,
          f"{kills} kills in 55 s")
    check("air-check still recording at the end", na.aircheck_state() == 1)
    check("stream still recovering (ladder alive)",
          ns.stream_state() in (2, 3, 1), f"state={ns.stream_state()}")
    
    print("— show ends: stream off → air-check saves itself —")
    ns.stream_stop()
    app.audio_player = type("P", (), {"emergency_mic_active": lambda self: False})()
    app._aircheck_auto_poll()
    deadline = time.time() + 5
    while time.time() < deadline and na.aircheck_state() != 0:
        time.sleep(0.05)
    check("air-check stopped itself when the show ended", na.aircheck_state() == 0)
    saved = getattr(app, "_aircheck_native_path", "")
    check("recording file exists", bool(saved) and os.path.isfile(saved),
          os.path.basename(saved) if saved else "none")
    if saved and saved.endswith(".wav"):
        try:
            with wave.open(saved, "rb") as w:
                frames = w.getnframes()
            check("recording is a VALID WAV with audio", frames > rate // 2,
                  f"{frames:,} frames ({frames / rate:.1f}s)")
        except Exception as e:
            check("recording is a VALID WAV with audio", False, str(e))
    
    audio_on.clear()
    up.set()  # stop server loop
    time.sleep(0.5)
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe"],
                         capture_output=True, text=True).stdout
    ff = max(0, out.count("ffmpeg.exe") - 1)
    check("no orphaned ffmpeg after combo stop", ff == 0, f"{ff} alive")
    
    print("\nCOMBO PASS ✅" if ok else "\nCOMBO FAIL ❌")
    sys.exit(0 if ok else 1)
    

if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
