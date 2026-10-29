"""Capture the queue header when the rundown is crowded (many jingles)."""
import os, sys, tempfile, time, wave, struct, math
sys.path.insert(0, ".")
os.chdir(tempfile.mkdtemp(prefix="freq_capture_next_"))
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
for i in range(12):
    s = Song(title=f"FOLK9 - Track {i + 1} [Official MV]", artist="FOLK9",
             source="file", file_path=f"/tmp/next_{i}.mp3", duration=200.0)
    s.id = f"next-{i}"
    app.rq.queue.append(s)
app._queue_canvas_width = lambda: 922

jingle = str(os.getcwd() + "/jingle.wav"); ad = str(os.getcwd() + "/ad.wav")
make_wav(jingle, 6.0); make_wav(ad, 9.0)
app.jingle.enabled, app.jingle.jingle_file = True, jingle
app.jingle.interval, app.jingle.song_counter = 1, 0
app.commercial.enabled, app.commercial.ads = True, [ad]
app.commercial.interval, app.commercial.song_counter = 4, 0
app.rq.current_index = 0
app._refresh_queue()
for _ in range(8):
    app.update(); time.sleep(0.12)
text = app.queue_next_label.cget("text")
print("HEADER:", text.replace("\n", " | "))
print("LINES ON SCREEN:", app.queue_next_label.winfo_height(),
      "px | pady:", app.queue_next_label.winfo_reqheight())
app.update()
frame = app.queue_panel_frame
x, y = frame.winfo_rootx(), frame.winfo_rooty()
w, h = frame.winfo_width(), min(220, frame.winfo_height())
ImageGrab.grab((x, y, x + w, y + h)).save("_ui_nextline_1019.png")
app.destroy()
print("saved _ui_nextline_1019.png")
