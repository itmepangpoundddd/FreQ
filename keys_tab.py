"""Keyboard Shortcuts binding UI for FreQ Radio."""
import tkinter as tk
import customtkinter as ctk

from theme import COLORS  # single design-token source — never re-define locally

# Default keybindings: {action_id: (label, default_key, icon)}
DEFAULT_KEYS = {
    "play_pause":  ("Play / Pause",    "Space",  "⏯️"),
    "next_track":  ("Next Track",      "N",      "⏭️"),
    "prev_track":  ("Previous Track",  "P",      "⏮️"),
    "seek_fwd":    ("Seek Forward",    "Right",  "⏩"),
    "seek_bwd":    ("Seek Backward",   "Left",   "⏪"),
    "vol_up":      ("Volume Up",       "Up",     "🔊"),
    "vol_down":    ("Volume Down",     "Down",   "🔉"),
    "mute":        ("Mute / Unmute",   "M",      "🔇"),
    "save":        ("Save Playlist",   "Ctrl+S", "💾"),
    "open_file":   ("Open File",       "Ctrl+O", "📂"),
    "paste":       ("Paste URL",       "Ctrl+V", "📋"),
    "toggle_crossfade": ("Toggle Crossfade", "C", "🔄"),
}


class KeyBindRow(ctk.CTkFrame):
    """A single row in the key bindings table."""

    def __init__(self, parent, action_id: str, label: str, key: str, icon: str,
                 on_change=None):
        super().__init__(parent, fg_color=COLORS["bg_card"], corner_radius=6, height=36)
        self.pack_propagate(False)
        self.action_id = action_id
        self._on_change = on_change
        self._listening = False

        # Icon
        ctk.CTkLabel(self, text=icon, font=ctk.CTkFont(size=16),
                     width=28, text_color=COLORS["text"]).pack(side="left", padx=(10, 4))

        # Action name
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(size=11),
                     text_color=COLORS["text"], width=140, anchor="w").pack(side="left", padx=(0, 8))

        # Current key display
        self.key_var = tk.StringVar(value=key)
        self.key_label = ctk.CTkLabel(
            self, textvariable=self.key_var,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["accent"], width=80, anchor="center",
            fg_color=COLORS["border"], corner_radius=4, height=26)
        self.key_label.pack(side="left", padx=(0, 8))

        # Bind / Rebind button
        self.btn_bind = ctk.CTkButton(
            self, text="Bind", width=52, height=26,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._start_listen)
        self.btn_bind.pack(side="left", padx=(0, 8))

        # Reset button
        self.btn_reset = ctk.CTkButton(
            self, text="↺", width=26, height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=COLORS["border"],
            text_color=COLORS["text3"],
            command=self._reset)
        self.btn_reset.pack(side="left")

    def _start_listen(self):
        """Enter listening mode — next key press becomes the new binding."""
        self._listening = True
        self.key_label.configure(fg_color=COLORS["purple"], text_color="#ffffff")
        self.btn_bind.configure(text="Press key...", fg_color=COLORS["purple"])
        # Bind temporary capture on the root window
        root = self.winfo_toplevel()
        root.bind("<KeyPress>", self._capture_key)
        root.bind("<KeyRelease>", lambda e: None)  # consume release too

    def _capture_key(self, event):
        """Capture the pressed key and set it as the new binding."""
        if not self._listening:
            return
        self._listening = False
        root = self.winfo_toplevel()
        root.unbind("<KeyPress>")
        root.unbind("<KeyRelease>")

        # Build key name
        parts = []
        if event.state & 0x4:  # Ctrl
            parts.append("Ctrl")
        if event.state & 0x1:  # Shift
            parts.append("Shift")
        if event.state & 0x8:  # Alt
            parts.append("Alt")

        key = event.keysym
        # Skip bare modifier presses
        if key in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                    "Alt_L", "Alt_R", "Super_L", "Super_R"):
            self._cancel_listen()
            return

        # Map special keys
        special = {
            "Return": "Enter", "Escape": "Esc", "space": "Space",
            "period": ".", "comma": ",", "slash": "/",
            "bracketleft": "[", "bracketright": "]",
        }
        display = special.get(key, key)

        # Don't duplicate modifier in parts
        if display in ("Ctrl", "Shift", "Alt"):
            self._cancel_listen()
            return

        parts.append(display)
        key_str = "+".join(parts)

        self.key_var.set(key_str)
        self.key_label.configure(fg_color=COLORS["border"], text_color=COLORS["accent"])
        self.btn_bind.configure(text="Bind", fg_color=COLORS["accent"])

        if self._on_change:
            self._on_change(self.action_id, key_str)

    def _cancel_listen(self):
        self._listening = False
        root = self.winfo_toplevel()
        root.unbind("<KeyPress>")
        root.unbind("<KeyRelease>")
        self.key_label.configure(fg_color=COLORS["border"], text_color=COLORS["accent"])
        self.btn_bind.configure(text="Bind", fg_color=COLORS["accent"])

    def _reset(self):
        """Reset to default key."""
        default = DEFAULT_KEYS.get(self.action_id)
        if default:
            self.key_var.set(default[1])
            if self._on_change:
                self._on_change(self.action_id, default[1])

    def get_key(self) -> str:
        return self.key_var.get()


def build_keys_tab(tab, on_change=None) -> list:
    """Build the key bindings UI inside `tab`. Returns list of KeyBindRow."""
    rows = []

    # Header
    hdr = ctk.CTkFrame(tab, fg_color="transparent")
    hdr.pack(fill="x", padx=12, pady=(12, 4))
    ctk.CTkLabel(hdr, text="⌨️  Keyboard Shortcuts",
                 font=ctk.CTkFont(size=16, weight="bold"),
                 text_color=COLORS["text"]).pack(side="left")
    # Accent line
    ctk.CTkFrame(tab, fg_color=COLORS["accent"], height=2, corner_radius=1).pack(
        fill="x", padx=12, pady=(0, 6))

    # Tip
    ctk.CTkLabel(tab,
                 text="Click [Bind] then press a key combination to rebind.\n"
                      "Ctrl+Key, Shift+Key, Alt+Key are supported. Click ↺ to reset.",
                 font=ctk.CTkFont(size=10),
                 text_color=COLORS["text3"], justify="left").pack(
        anchor="w", padx=16, pady=(0, 8))

    # Section: Playback
    _section_label(tab, "⏯️  Playback")
    for aid in ("play_pause", "next_track", "prev_track"):
        row = _add_row(tab, aid, on_change)
        rows.append(row)

    # Section: Seek
    _section_label(tab, "⏩  Seek")
    for aid in ("seek_fwd", "seek_bwd"):
        row = _add_row(tab, aid, on_change)
        rows.append(row)

    # Section: Volume
    _section_label(tab, "🔊  Volume")
    for aid in ("vol_up", "vol_down", "mute"):
        row = _add_row(tab, aid, on_change)
        rows.append(row)

    # Section: General
    _section_label(tab, "📂  General")
    for aid in ("save", "open_file", "paste", "toggle_crossfade"):
        row = _add_row(tab, aid, on_change)
        rows.append(row)

    # Reset All button
    btn_frame = ctk.CTkFrame(tab, fg_color="transparent")
    btn_frame.pack(fill="x", padx=16, pady=(16, 8))

    def _reset_all():
        for r in rows:
            r._reset()

    ctk.CTkButton(btn_frame, text="↺ Reset All to Defaults", width=180, height=30,
                  font=ctk.CTkFont(size=11),
                  fg_color="transparent", border_width=1,
                  border_color=COLORS["border"], hover_color=COLORS["bg_card"],
                  text_color=COLORS["text3"],
                  command=_reset_all).pack(side="left")

    return rows


def _section_label(parent, text):
    f = ctk.CTkFrame(parent, fg_color="transparent")
    f.pack(fill="x", padx=12, pady=(10, 2))
    ctk.CTkLabel(f, text=text, font=ctk.CTkFont(size=11, weight="bold"),
                 text_color=COLORS["text2"]).pack(side="left")
    ctk.CTkFrame(f, fg_color=COLORS["border"], height=1).pack(
        side="left", fill="x", expand=True, padx=(8, 0), pady=4)


def _add_row(tab, action_id, on_change):
    info = DEFAULT_KEYS[action_id]
    row = KeyBindRow(tab, action_id, info[0], info[1], info[2], on_change=on_change)
    row.pack(fill="x", padx=12, pady=2)
    return row
