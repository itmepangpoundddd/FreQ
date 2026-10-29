"""Capture the queue rundown with jingle + ad + anthem + a live download."""
import datetime, os, sys, tempfile, time, wave, struct, math
sys.path.insert(0, ".")
os.chdir(tempfile.mkdtemp(prefix="freq_capture_plan_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import gui
from radio_manager import Song
from PIL import ImageGrab

def make_wav(path, seconds):
    frames = int(seconds * 44100)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / 44100)))
                               for i in range(frames)))

app = gui.RadioApp()
app.attributes("-topmost", True); app.lift()
for _ in range(6):
    app.update(); time.sleep(0.1)
app.audio_player.volume = 0.0
app._playing = False
app._queue_filter = ""
app.rq.queue.clear()
titles = [("FOLK9 - The Waiter [Official MV]", 241), ("FOLK9 - Plant [Official MV]", 132),
          ("FOLK9 - Mermaid [Official MV]", 344), ("FOLK9 - Romantic Scene", 272)]
for i, (t, d) in enumerate(titles):
    s = Song(title=t, artist="FOLK9", source="file", file_path=f"/tmp/plan_{i}.mp3", duration=float(d))
    s.id = f"plan-{i}"; app.rq.queue.append(s)
app._queue_canvas_width = lambda: 922

jingle = str(os.getcwd() + "/jingle.wav"); ad = str(os.getcwd() + "/ad.wav")
make_wav(jingle, 1.6); make_wav(ad, 9.0)
app.jingle.enabled, app.jingle.jingle_file, app.jingle.interval, app.jingle.song_counter = True, jingle, 2, 0
app.commercial.enabled, app.commercial.ads, app.commercial.interval, app.commercial.song_counter = True, [ad], 3, 0
cfg = app.anthem.config
cfg.enabled, cfg.duration = True, 80.0
cfg.pause_before, cfg.fade_in, cfg.fade_out = 1.0, 2.0, 2.0
soon = datetime.datetime.now() + datetime.timedelta(minutes=7)
cfg.morning_time = soon.strftime("%H:%M")
cfg.evening_time = (soon + datetime.timedelta(hours=6)).strftime("%H:%M")
app._download_mode = True
app._dl_state = {"index": 0, "status": "downloading", "pct": 42.0, "started": time.time() - 9.0}
app.rq.current_index = 0
app._playing = True
app.audio_player.get_position = lambda: 0.0
app._refresh_queue()
for _ in range(8):
    app.update(); time.sleep(0.12)
print("HEADER:", app.queue_next_label.cget("text").replace("\n", " | "))
app.update()
x, y = app.winfo_rootx(), app.winfo_rooty()
ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save("_ui_rundown_1020.png")
app.destroy()
print("saved _ui_rundown_1020.png")
