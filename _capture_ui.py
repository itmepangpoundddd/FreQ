"""One-off: launch RadioApp, capture every sidebar tab as PNG for UI audit."""
import sys
import time

sys.path.insert(0, ".")
import gui  # noqa: E402

from PIL import ImageGrab  # noqa: E402

app = gui.RadioApp()
app.attributes("-topmost", True)
app.lift()
for _ in range(6):
    app.update()
    time.sleep(0.1)


def bbox_of(widget):
    x = widget.winfo_rootx()
    y = widget.winfo_rooty()
    return (x, y, x + widget.winfo_width(), y + widget.winfo_height())


app.update()
time.sleep(0.4)
app.update()
ImageGrab.grab(bbox_of(app)).save("_ui_main.png")

for i, name in enumerate(app._tab_names):
    try:
        app._on_sidebar_tab_switch(i, name)
    except Exception as e:
        print("switch fail", name, e)
    for _ in range(4):
        app.update()
        time.sleep(0.08)
    ImageGrab.grab(bbox_of(app)).save(f"_ui_tab_{name}.png")

app.destroy()
print("captured", len(app._tab_names) + 1, "screenshots")
