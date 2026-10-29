"""Measure + capture the As-run action row: 6 buttons, each with a tooltip."""
import os, sys, tempfile, time
sys.path.insert(0, ".")
os.chdir(tempfile.mkdtemp(prefix="freq_capture_asrun_btn_"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import gui
from PIL import ImageGrab

app = gui.RadioApp()
app.attributes("-topmost", True); app.lift()
for _ in range(6):
    app.update(); time.sleep(0.1)
app._on_sidebar_tab_switch(0, "As-run")
for _ in range(4):
    app.update(); time.sleep(0.1)

row = app.asrun_refresh_btn.master
buttons = [w for w in row.winfo_children()]
texts = []
for b in buttons:
    try:
        texts.append(b.cget("text"))
    except Exception:
        texts.append("?")
total = sum(b.winfo_width() for b in buttons) + sum(
    1 * 2 for _ in buttons)   # padx=1 on each side
print("buttons:", texts)
print("row width:", row.winfo_width(), "buttons total:", total)
print("fits:", total <= row.winfo_width())
for b, t in zip(buttons, texts):
    # CTkButton.bind() proxies to its canvas, so inspect both.
    target = getattr(b, "_canvas", None) or b
    has_tip = bool(target.bind("<Enter>")) or bool(b.bind("<Enter>"))
    print(f"  tooltip on {t!r}: {has_tip}")
    assert has_tip, f"no tooltip bound on {t!r}"

# The refresh button must actually redraw.
app._asrun_last_store = ("stale", 999, 999)
app.asrun_refresh_btn.invoke()
app.update()
print("store after refresh:", app._asrun_last_store)
assert app._asrun_last_store != ("stale", 999, 999), "refresh did not redraw"
print("last toast:", getattr(app, "_toast_label", None) and app._toast_label.cget("text"))

x, y = app.winfo_rootx(), app.winfo_rooty()
ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(
    "_ui_asrun_actions_1019.png")
app.destroy()
print("saved _ui_asrun_actions_1019.png")
