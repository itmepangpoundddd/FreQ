"""
Tooltip widget for CustomTkinter.
Shows a small popup with text when hovering over a widget.

Usage:
    from tooltips import ToolTip

    btn = ctk.CTkButton(root, text="Play")
    btn.pack()
    ToolTip(btn, text="Play / Pause", description="Start or pause the current song")
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from theme import COLORS  # single design-token source


class ToolTip:
    """Hover tooltip for any tkinter/CustomTkinter widget.

    Shows a small popup below the widget with:
    - Title (widget name / label)
    - Description (what it does)
    """

    def __init__(
        self,
        widget: tk.Widget,
        text: str = "",
        description: str = "",
        delay: int = 500,
        fg_color: str = COLORS["bg_hover"],
        text_color: str = COLORS["text"],
        desc_color: str = COLORS["text2"],
        border_color: str = COLORS["border"],
        font_size: int = 11,
    ):
        self.widget = widget
        self.text = text
        self.description = description
        self.delay = delay  # ms before showing
        self.fg_color = fg_color
        self.text_color = text_color
        self.desc_color = desc_color
        self.border_color = border_color
        self.font_size = font_size

        self._tip_window: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None

        # Bind events
        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, event=None) -> None:
        """Schedule tooltip to appear after delay."""
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _on_leave(self, event=None) -> None:
        """Cancel scheduled tooltip and hide if visible."""
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        """Display the tooltip popup."""
        if self._tip_window:
            return

        # Get widget position
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4

        # Create popup window
        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)  # no title bar
        tw.wm_attributes("-topmost", True)
        tw.configure(bg=self.border_color)

        # Outer frame (border)
        outer = tk.Frame(tw, bg=self.border_color, padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        # Inner frame (content)
        inner = tk.Frame(outer, bg=self.fg_color, padx=10, pady=8)
        inner.pack(fill="both", expand=True)

        # Title
        if self.text:
            title = tk.Label(
                inner,
                text=self.text,
                font=("Segoe UI", self.font_size, "bold"),
                fg=self.text_color,
                bg=self.fg_color,
                anchor="w",
                justify="left",
            )
            title.pack(fill="x")

        # Description
        if self.description:
            desc = tk.Label(
                inner,
                text=self.description,
                font=("Segoe UI", self.font_size - 1),
                fg=self.desc_color,
                bg=self.fg_color,
                anchor="w",
                justify="left",
                wraplength=280,
            )
            desc.pack(fill="x", pady=(2, 0))

        # Position the window
        tw.wm_geometry(f"+{x}+{y}")

        # Auto-hide after 5 seconds
        tw.after(5000, self._hide)

    def _hide(self) -> None:
        """Destroy the tooltip window."""
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None

    def update_text(self, text: str, description: str = "") -> None:
        """Update tooltip text dynamically."""
        self.text = text
        self.description = description

    def destroy(self) -> None:
        """Clean up."""
        self._cancel()
        self._hide()
