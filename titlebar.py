#!/usr/bin/env python3
"""
Custom Title Bar for FreQ
Provides a sleek, custom window title bar with app branding and controls.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from typing import Optional, Callable

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False

from icons import get_tk_image, cached_icon
from theme import COLORS  # single design-token source


# ═══════════════════════════════════════════════
# Color Theme
# ═══════════════════════════════════════════════

THEME = {
    "bg_dark": COLORS["bg"],
    "bg_medium": COLORS["bg_card"],
    "accent": COLORS["accent"],
    "accent_hover": COLORS["accent_hover"],
    "text": COLORS["text"],
    "text_dim": COLORS["text2"],
    "border": COLORS["border"],
    "close_hover": COLORS["red_hover"],
    "maximize_hover": COLORS["green_dark"],
    "minimize_hover": COLORS["accent"],
}


# ═══════════════════════════════════════════════
# Custom Title Bar Widget
# ═══════════════════════════════════════════════

class CustomTitleBar(ctk.CTkFrame if CTK_AVAILABLE else object):
    """Custom title bar with app branding and window controls."""
    
    def __init__(self, master, app_title: str = "FreQ",
                 subtitle: str = "Radio Playlist Manager",
                 on_close: Callable = None,
                 on_minimize: Callable = None,
                 on_maximize: Callable = None,
                 **kwargs):
        if CTK_AVAILABLE:
            super().__init__(master, fg_color=THEME["bg_medium"],
                           height=48, corner_radius=0, **kwargs)
        else:
            raise ImportError("customtkinter is required")
        
        self.app_title = app_title
        self.subtitle = subtitle
        self._on_close = on_close
        self._on_minimize = on_minimize
        self._on_maximize = on_maximize
        
        # Window state
        self._maximized = False
        self._normal_geometry = None
        
        # Enable window dragging
        self._drag_data = {"x": 0, "y": 0}
        
        self._build_ui()
        self._setup_drag()
    
    def _build_ui(self) -> None:
        """Build the title bar UI."""
        self.pack_propagate(False)
        
        # Left section: Logo + Title
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.pack(side="left", fill="y", padx=(12, 0))
        
        # Logo icon
        logo_icon = get_tk_image("music_note", 24, THEME["accent"])
        if logo_icon:
            ctk.CTkLabel(left_frame, image=logo_icon, text="").pack(
                side="left", padx=(0, 8), pady=8)
        else:
            ctk.CTkLabel(left_frame, text="🎵", font=ctk.CTkFont(size=18)).pack(
                side="left", padx=(0, 8), pady=8)
        
        # App title
        title_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        title_frame.pack(side="left", pady=8)
        
        ctk.CTkLabel(
            title_frame, text=self.app_title,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=THEME["text"]
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            title_frame, text=self.subtitle,
            font=ctk.CTkFont(size=9),
            text_color=THEME["text_dim"]
        ).pack(anchor="w")
        
        # Center section: Window controls (drag area)
        center_frame = ctk.CTkFrame(self, fg_color="transparent")
        center_frame.pack(side="left", fill="both", expand=True)
        
        # Right section: Minimize, Maximize, Close
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.pack(side="right", fill="y", padx=(0, 4))
        
        btn_size = 36
        btn_height = 32
        
        # Minimize button
        self.btn_minimize = ctk.CTkButton(
            right_frame, width=btn_size, height=btn_height,
            corner_radius=6, fg_color="transparent",
            hover_color=THEME["minimize_hover"],
            command=self._minimize
        )
        min_icon = get_tk_image("remove", 14, THEME["text_dim"])
        if min_icon:
            self.btn_minimize.configure(image=min_icon, text="")
        else:
            self.btn_minimize.configure(text="─", font=ctk.CTkFont(size=14))
        self.btn_minimize.pack(side="left", padx=2, pady=8)
        
        # Maximize button
        self.btn_maximize = ctk.CTkButton(
            right_frame, width=btn_size, height=btn_height,
            corner_radius=6, fg_color="transparent",
            hover_color=THEME["maximize_hover"],
            command=self._maximize
        )
        self._max_icon = get_tk_image("fullscreen", 14, THEME["text_dim"])
        self._restore_icon = get_tk_image("fullscreen_exit", 14, THEME["text_dim"])
        if self._max_icon:
            self.btn_maximize.configure(image=self._max_icon, text="")
        else:
            self.btn_maximize.configure(text="□", font=ctk.CTkFont(size=14))
        self.btn_maximize.pack(side="left", padx=2, pady=8)
        
        # Close button
        self.btn_close = ctk.CTkButton(
            right_frame, width=btn_size, height=btn_height,
            corner_radius=6, fg_color="transparent",
            hover_color=THEME["close_hover"],
            command=self._close
        )
        close_icon = get_tk_image("close", 14, THEME["text_dim"])
        if close_icon:
            self.btn_close.configure(image=close_icon, text="")
        else:
            self.btn_close.configure(text="✕", font=ctk.CTkFont(size=14))
        self.btn_close.pack(side="left", padx=2, pady=8)
        
        # Hover effects for close button
        self.btn_close.bind("<Enter>", lambda e: self.btn_close.configure(
            fg_color=THEME["close_hover"]))
        self.btn_close.bind("<Leave>", lambda e: self.btn_close.configure(
            fg_color="transparent"))
    
    def _setup_drag(self) -> None:
        """Setup window dragging."""
        # Bind drag events to all child widgets
        self.bind("<Button-1>", self._start_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Double-Button-1>", self._double_click)
        
        # Bind to all children recursively
        self._bind_drag_recursive(self)
    
    def _bind_drag_recursive(self, widget) -> None:
        """Recursively bind drag events to all child widgets."""
        try:
            widget.bind("<Button-1>", self._start_drag, add="+")
            widget.bind("<B1-Motion>", self._on_drag, add="+")
            widget.bind("<Double-Button-1>", self._double_click, add="+")
        except Exception:
            pass
        
        try:
            for child in widget.winfo_children():
                self._bind_drag_recursive(child)
        except Exception:
            pass
    
    def _start_drag(self, event) -> None:
        """Start window dragging."""
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
    
    def _on_drag(self, event) -> None:
        """Handle window dragging."""
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]
        
        # Get current window position
        x = self.winfo_rootx() + dx
        y = self.winfo_rooty() + dy
        
        # Move window
        self.winfo_toplevel().geometry(f"+{x}+{y}")
        
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root
    
    def _double_click(self, event) -> None:
        """Handle double-click to maximize/restore."""
        self._maximize()
    
    def _minimize(self) -> None:
        """Minimize the window."""
        if self._on_minimize:
            self._on_minimize()
        else:
            self.winfo_toplevel().iconify()
    
    def _maximize(self) -> None:
        """Maximize or restore the window."""
        if self._maximized:
            # Restore
            if self._normal_geometry:
                self.winfo_toplevel().geometry(self._normal_geometry)
            self._maximized = False
            if self._restore_icon:
                self.btn_maximize.configure(image=self._max_icon)
        else:
            # Maximize
            self._normal_geometry = self.winfo_toplevel().geometry()
            
            # Get screen size
            screen_w = self.winfo_toplevel().winfo_screenwidth()
            screen_h = self.winfo_toplevel().winfo_screenheight()
            
            # Remove window decorations estimate
            self.winfo_toplevel().geometry(
                f"{screen_w}x{screen_h - 40}+0+0"
            )
            self._maximized = True
            if self._restore_icon:
                self.btn_maximize.configure(image=self._restore_icon)
        
        if self._on_maximize:
            self._on_maximize()
    
    def _close(self) -> None:
        """Close the window."""
        if self._on_close:
            self._on_close()
        else:
            self.winfo_toplevel().destroy()


# ═══════════════════════════════════════════════
# Standalone Test
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    root = ctk.CTk()
    root.configure(fg_color=THEME["bg_dark"])
    root.geometry("1200x800")
    root.overrideredirect(True)  # Remove default title bar
    
    # Custom title bar
    title_bar = CustomTitleBar(root, app_title="FreQ", subtitle="Radio Playlist Manager")
    title_bar.pack(fill="x")
    
    # Content area
    content = ctk.CTkFrame(root, fg_color=THEME["bg_dark"])
    content.pack(fill="both", expand=True)
    
    ctk.CTkLabel(content, text="Custom Title Bar Demo",
                font=ctk.CTkFont(size=24, weight="bold"),
                text_color=THEME["text"]).pack(pady=50)
    
    root.mainloop()
