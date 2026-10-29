"""Temp debug: exact events when focus is OUTSIDE fields."""
import sys
from pathlib import Path
from types import MethodType

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import gui  # noqa: E402
import customtkinter as ctk  # noqa: E402

root = ctk.CTk()
root.geometry("220x100")
entry = ctk.CTkEntry(root)
entry.pack(padx=10, pady=10)

hits = []
no_typing = MethodType(gui.RadioApp._no_typing, root)
root.bind("<space>", no_typing(lambda e: hits.append(
    ("space", str(e.widget), e.type))))
root.bind("<n>", no_typing(lambda e: hits.append(
    ("n", str(e.widget), e.type))))

root.update()
entry.focus_set()
root.update()
root.event_generate("<space>")
root.update()
inside = list(hits)
hits.clear()
root.focus_set()
root.update()
root.event_generate("<space>")
root.event_generate("<n>")
root.update()
outside = list(hits)
print("inside :", inside)
print("outside:", outside)
root.after(120, root.destroy)
root.mainloop()
