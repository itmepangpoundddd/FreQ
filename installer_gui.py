#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║            🎙️  FreQ — GUI Installer                         ║
║     Radio Playlist Manager Setup Wizard                     ║
╚══════════════════════════════════════════════════════════════╝

Run: python installer_gui.py
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path
from typing import Optional

import customtkinter as ctk

# ── Theme ──
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ── Constants ──
APP_NAME = "FreQ"
APP_VERSION = "2.2.0"
APP_DESC = "Radio Playlist Manager"
PUBLISHER = "SocieticsTv Broadcasting & Network"

# Light-theme colors matching the HTML preview
C = {
    "bg":           "#f0f0f0",
    "bg_white":     "#ffffff",
    "bg_header":    "#0a0e14",
    "bg_header2":   "#1a2230",
    "accent":       "#1f6feb",
    "accent_hover": "#388bfd",
    "accent_dark":  "#1a5cc7",
    "green":        "#3fb950",
    "green_header": "#0a3a0a",
    "green_header2":"#1a4a1a",
    "orange":       "#f0883e",
    "text":         "#0a0e14",
    "text2":        "#333333",
    "text3":        "#555555",
    "text_light":   "#8b949e",
    "text_footer":  "#666666",
    "border":       "#cccccc",
    "border_light": "#d0d7de",
    "btn_bg":       "#ffffff",
    "btn_border":   "#999999",
    "log_bg":       "#ffffff",
    "sidebar_top":  "#0a0e14",
    "sidebar_bot":  "#1f6feb",
    "eq_bar":       "#c0c8d4",
}

# ── Paths ──
SRC_DIR = Path(__file__).parent
DIST_DIR = SRC_DIR / "dist" / "FreQ"


# ═══════════════════════════════════════════════════════════════
# Installer Engine
# ═══════════════════════════════════════════════════════════════

class InstallerEngine:
    def __init__(self, install_dir: Path, components: dict):
        self.install_dir = install_dir
        self.components = components
        self.tasks = []
        self._progress_cb = None
        self._log_cb = None
        self._build_tasks()

    def on_progress(self, cb): self._progress_cb = cb
    def on_log(self, cb): self._log_cb = cb

    def _progress(self, cur, total):
        if self._progress_cb: self._progress_cb(cur, total)
    def _log(self, msg, level="info"):
        if self._log_cb: self._log_cb(msg, level)

    def _build_tasks(self):
        self.tasks = [
            ("Checking system requirements", self._check_system),
            ("Creating installation directory", self._create_dir),
            ("Copying application files", self._copy_files),
        ]
        if self.components.get("ffmpeg"):
            self.tasks.append(("Installing FFmpeg", self._install_ffmpeg))
        if self.components.get("shortcuts"):
            self.tasks.append(("Creating shortcuts", self._create_shortcuts))
        if self.components.get("file_association"):
            self.tasks.append(("Registering file associations", self._register_file_assoc))
        self.tasks.append(("Finalizing installation", self._finalize))

    def run(self) -> bool:
        total = len(self.tasks)
        for i, (name, func) in enumerate(self.tasks):
            self._log(f"✓ {name}...", "ok")
            try:
                func()
                self._log(f"  Done.", "done")
            except Exception as e:
                self._log(f"  ✗ Failed: {e}", "error")
                return False
            self._progress(i + 1, total)
        return True

    def _check_system(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows only")
        if not DIST_DIR.exists():
            raise RuntimeError(f"Build not found at {DIST_DIR}\nRun 'python build.py' first.")

    def _create_dir(self):
        self.install_dir.mkdir(parents=True, exist_ok=True)

    def _copy_files(self):
        items = list(DIST_DIR.rglob("*"))
        total = len(items)
        for i, item in enumerate(items):
            rel = item.relative_to(DIST_DIR)
            target = self.install_dir / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            if (i + 1) % 100 == 0 or (i + 1) == total:
                self._log(f"  ({i+1}/{total} files)", "info")

        for doc in ["LICENSE", "README.md", "PRICING.md", "COMMERCIAL_LICENSE.md"]:
            s = SRC_DIR / doc
            if s.exists():
                shutil.copy2(s, self.install_dir / doc)
        icon = SRC_DIR / "logo.ico"
        if icon.exists():
            shutil.copy2(icon, self.install_dir / "logo.ico")

    def _install_ffmpeg(self):
        # Check if already installed
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                self._log("  FFmpeg already installed", "info")
                return
        except Exception:
            pass

        # Check if bundled in deps/
        bundled = SRC_DIR / "deps" / "ffmpeg-essentials"
        if bundled.exists() and any(bundled.glob("*.exe")):
            ffmpeg_dir = self.install_dir / "ffmpeg"
            ffmpeg_dir.mkdir(parents=True, exist_ok=True)
            for f in ["ffmpeg.exe", "ffprobe.exe"]:
                src = next((bundled / "**" / f).glob("*"), None) if not (bundled / f).exists() else bundled / f
                if src and src.exists():
                    shutil.copy2(src, ffmpeg_dir / f)
            self._log("  FFmpeg installed from bundle", "info")
            return

        # Download with curl (much faster than PowerShell WebClient)
        self._log("  Downloading FFmpeg (~80MB)...", "info")
        zip_path = Path(os.environ.get("TEMP", "/tmp")) / "ffmpeg.zip"
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        try:
            r = subprocess.run(
                ["curl", "-L", "--progress-bar", "-o", str(zip_path), url],
                timeout=600
            )
            if r.returncode != 0:
                raise RuntimeError("curl download failed")
        except FileNotFoundError:
            # curl not available, try PowerShell with ProgressPreference
            self._log("  curl not found, using PowerShell (slower)...", "info")
            ps_cmd = f"(New-Object Net.WebClient).DownloadFile('{url}','{zip_path}')"
            r = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                timeout=600
            )
            if r.returncode != 0:
                raise RuntimeError("PowerShell download failed")

        # Extract
        self._log("  Extracting FFmpeg...", "info")
        extract_dir = Path(os.environ.get("TEMP", "/tmp")) / "ffmpeg_extract"
        subprocess.run(
            ["powershell", "-Command",
             f"Expand-Archive -Path '{zip_path}' -DestinationPath '{extract_dir}' -Force"],
            timeout=120
        )

        # Copy ffmpeg.exe + ffprobe.exe
        ffmpeg_dir = self.install_dir / "ffmpeg"
        ffmpeg_dir.mkdir(parents=True, exist_ok=True)
        for exe_name in ["ffmpeg.exe", "ffprobe.exe"]:
            matches = list(extract_dir.rglob(exe_name))
            if matches:
                shutil.copy2(matches[0], ffmpeg_dir / exe_name)
                self._log(f"  Installed {exe_name}", "info")

        # Cleanup
        try:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass

        self._log("  FFmpeg installed", "info")

    def _create_shortcuts(self):
        exe = self.install_dir / f"{APP_NAME}.exe"
        if not exe.exists():
            return
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        self._make_shortcut(exe, desktop / f"{APP_NAME}.lnk")
        sm = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs" / APP_NAME
        sm.mkdir(parents=True, exist_ok=True)
        self._make_shortcut(exe, sm / f"{APP_NAME}.lnk")

    def _make_shortcut(self, target: Path, link: Path):
        ps = (
            f'$ws=New-Object -ComObject WScript.Shell; '
            f'$s=$ws.CreateShortcut("{link}"); '
            f'$s.TargetPath="{target}"; '
            f'$s.WorkingDirectory="{target.parent}"; '
            f'$s.Save()'
        )
        try:
            subprocess.run(["powershell", "-Command", ps], capture_output=True, timeout=10)
        except Exception:
            pass

    def _register_file_assoc(self):
        exe = self.install_dir / f"{APP_NAME}.exe"
        if not exe.exists():
            return
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.freq") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "FreQ.FreqFile")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\FreQ.FreqFile") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "FreQ Playlist File")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\FreQ.FreqFile\DefaultIcon") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"{exe},0")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\FreQ.FreqFile\shell\open\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{exe}" "%1"')
        except Exception:
            pass

    def _finalize(self):
        import json
        (self.install_dir / "install.json").write_text(
            json.dumps({"app": APP_NAME, "version": APP_VERSION, "dir": str(self.install_dir)}, indent=2),
            encoding="utf-8"
        )


# ═══════════════════════════════════════════════════════════════
# Main Installer Window
# ═══════════════════════════════════════════════════════════════

class InstallerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Setup")
        self.geometry("660x480")
        self.minsize(660, 480)
        self.configure(fg_color=C["bg"])
        self.resizable(False, False)

        icon_path = SRC_DIR / "logo.ico"
        if icon_path.exists():
            try: self.iconbitmap(str(icon_path))
            except Exception: pass

        self._page_idx = 0
        self._pages: list[ctk.CTkFrame] = []
        self._install_dir = Path.home() / "AppData" / "Local" / APP_NAME
        self._components = {"shortcuts": True, "file_association": True, "ffmpeg": True}
        self._eq_bars = []

        self._build_layout()
        self._build_pages()
        self._show_page(0)
        self._animate_eq()

    # ═══════════════════════════════════════════════════════════
    # Layout: Sidebar + Main + Footer
    # ═══════════════════════════════════════════════════════════

    def _build_layout(self):
        # Top container: sidebar + main
        self._top = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0)
        self._top.pack(fill="both", expand=True)

        # ── Sidebar (left) ──
        self._sidebar = ctk.CTkFrame(self._top, fg_color=C["sidebar_top"], corner_radius=0, width=160)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # EQ bars
        eq_frame = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        eq_frame.pack(expand=True)
        bar_heights = [20, 40, 30, 50, 25]
        for h in bar_heights:
            bar = ctk.CTkFrame(eq_frame, width=8, height=h, fg_color="#ffffff",
                               corner_radius=2)
            bar.pack(side="left", padx=3, pady=(0, 0))
            bar.pack_propagate(False)
            self._eq_bars.append((bar, h))

        # App name
        ctk.CTkLabel(self._sidebar, text=APP_NAME,
                      font=ctk.CTkFont(size=20, weight="bold"),
                      text_color="#ffffff").pack(pady=(15, 2))
        ctk.CTkLabel(self._sidebar, text="Radio Playlist\nManager",
                      font=ctk.CTkFont(size=11),
                      text_color="#b0b8c4").pack()

        # ── Main content (right) ──
        self._main = ctk.CTkFrame(self._top, fg_color=C["bg"], corner_radius=0)
        self._main.pack(side="left", fill="both", expand=True)

        # ── Footer ──
        self._footer = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0, height=48)
        self._footer.pack(fill="x", side="bottom")
        self._footer.pack_propagate(False)

        ft = ctk.CTkFrame(self._footer, fg_color="transparent")
        ft.pack(fill="both", expand=True, padx=20, pady=8)

        self._step_lbl = ctk.CTkLabel(ft, text="Page 1 of 5",
                                       font=ctk.CTkFont(size=11),
                                       text_color=C["text_footer"])
        self._step_lbl.pack(side="left")

        btn_frame = ctk.CTkFrame(ft, fg_color="transparent")
        btn_frame.pack(side="right")

        self._btn_cancel = ctk.CTkButton(btn_frame, text="Cancel", width=80, height=30,
                                           fg_color=C["btn_bg"], hover_color="#e0e0e0",
                                           text_color=C["text2"],
                                           border_color=C["btn_border"], border_width=1,
                                           corner_radius=3, font=ctk.CTkFont(size=12),
                                           command=self.destroy)
        self._btn_cancel.pack(side="left", padx=(0, 6))

        self._btn_back = ctk.CTkButton(btn_frame, text="< Back", width=80, height=30,
                                         fg_color=C["btn_bg"], hover_color="#e0e0e0",
                                         text_color=C["text2"],
                                         border_color=C["btn_border"], border_width=1,
                                         corner_radius=3, font=ctk.CTkFont(size=12),
                                         command=lambda: self._show_page(self._page_idx - 1))
        self._btn_back.pack(side="left", padx=(0, 6))

        self._btn_next = ctk.CTkButton(btn_frame, text="Next >", width=80, height=30,
                                         fg_color=C["accent"], hover_color=C["accent_hover"],
                                         text_color="#ffffff",
                                         corner_radius=3, font=ctk.CTkFont(size=12, weight="bold"),
                                         command=lambda: self._show_page(self._page_idx + 1))
        self._btn_next.pack(side="left")

    def _animate_eq(self):
        """Animate sidebar EQ bars."""
        for bar, base_h in self._eq_bars:
            new_h = max(10, base_h + random.randint(-12, 12))
            bar.configure(height=new_h)
        self.after(200, self._animate_eq)

    # ═══════════════════════════════════════════════════════════
    # Page Navigation
    # ═══════════════════════════════════════════════════════════

    def _build_pages(self):
        self._pages = [
            self._page_welcome(),
            self._page_license(),
            self._page_options(),
            self._page_installing(),
            self._page_done(),
        ]

    def _show_page(self, idx):
        if idx < 0 or idx >= len(self._pages):
            return
        for p in self._pages:
            p.pack_forget()
        self._page_idx = idx
        self._pages[idx].pack(in_=self._main, fill="both", expand=True)

        total = len(self._pages)
        self._step_lbl.configure(text=f"Page {idx + 1} of {total}")
        self._btn_back.configure(state="normal" if 0 < idx < total - 1 else "disabled")

        if idx == 2:  # Options → Install button
            self._btn_next.configure(text="Install", fg_color=C["accent"],
                                      command=self._start_install)
        elif idx == total - 1:  # Done → Finish
            self._btn_next.configure(text="Finish", fg_color=C["green"],
                                      hover_color="#2ea043",
                                      command=self._finish)
            self._btn_cancel.configure(state="disabled")
            self._btn_back.configure(state="disabled")
        elif idx == 3:  # Installing → hide buttons
            self._btn_next.configure(state="disabled")
            self._btn_back.configure(state="disabled")
            self._btn_cancel.configure(state="disabled")
        else:
            self._btn_next.configure(text="Next >", fg_color=C["accent"],
                                      hover_color=C["accent_hover"], state="normal",
                                      command=lambda: self._show_page(self._page_idx + 1))
            self._btn_cancel.configure(state="normal")

    # ═══════════════════════════════════════════════════════════
    # Page Builders
    # ═══════════════════════════════════════════════════════════

    def _make_header(self, parent, title, subtitle, bg_colors=None):
        """Create a dark header bar."""
        bg1 = bg_colors[0] if bg_colors else C["bg_header"]
        bg2 = bg_colors[1] if bg_colors else C["bg_header2"]
        border_c = bg_colors[2] if len(bg_colors or []) > 2 else C["accent"]

        hdr = ctk.CTkFrame(parent, fg_color=bg1, corner_radius=0, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        # Gradient overlay
        ctk.CTkLabel(hdr, text=title,
                      font=ctk.CTkFont(size=15, weight="bold"),
                      text_color="#ffffff").pack(anchor="w", padx=20, pady=(14, 0))
        ctk.CTkLabel(hdr, text=subtitle,
                      font=ctk.CTkFont(size=11),
                      text_color=C["text_light"]).pack(anchor="w", padx=20)
        # Bottom accent line
        ctk.CTkFrame(hdr, height=3, fg_color=border_c).pack(fill="x", side="bottom")
        return hdr

    # ── Page 1: Welcome ──

    def _page_welcome(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._main, fg_color=C["bg"])
        self._make_header(page, f"Welcome to {APP_NAME} Setup", f"Version {APP_VERSION}")

        body = ctk.CTkFrame(page, fg_color=C["bg_white"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # Logo area
        logo_row = ctk.CTkFrame(body, fg_color="transparent")
        logo_row.pack(anchor="w", pady=(10, 16))

        logo_box = ctk.CTkFrame(logo_row, width=64, height=64,
                                 fg_color=C["accent"], corner_radius=16)
        logo_box.pack(side="left", padx=(0, 14))
        logo_box.pack_propagate(False)
        ctk.CTkLabel(logo_box, text="🎵", font=ctk.CTkFont(size=28),
                      text_color="#ffffff").pack(expand=True)

        lf = ctk.CTkFrame(logo_row, fg_color="transparent")
        lf.pack(side="left")
        ctk.CTkLabel(lf, text=APP_NAME,
                      font=ctk.CTkFont(size=28, weight="bold"),
                      text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(lf, text=f"{APP_DESC} v{APP_VERSION}",
                      font=ctk.CTkFont(size=11),
                      text_color=C["text_light"]).pack(anchor="w")

        # Description
        ctk.CTkLabel(body,
                      text="This wizard will guide you through the installation of FreQ on your computer.",
                      font=ctk.CTkFont(size=12), text_color=C["text2"],
                      wraplength=420, justify="left").pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(body,
                      text="FreQ is a modern radio playlist manager with YouTube integration, "
                           "streaming support, and advanced playback features.",
                      font=ctk.CTkFont(size=11), text_color=C["text_light"],
                      wraplength=420, justify="left").pack(anchor="w")

        return page

    # ── Page 2: License ──

    def _page_license(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._main, fg_color=C["bg"])
        self._make_header(page, "License Agreement", "Please read the following license")

        body = ctk.CTkFrame(page, fg_color=C["bg_white"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # License text
        license_box = ctk.CTkTextbox(
            body, fg_color=C["bg_white"],
            text_color=C["text3"],
            font=ctk.CTkFont(family="Consolas", size=11),
            border_color=C["border_light"], border_width=1,
            corner_radius=4, wrap="word",
        )
        license_box.pack(fill="both", expand=True, pady=(0, 8))

        license_file = SRC_DIR / "LICENSE"
        if license_file.exists():
            text = license_file.read_text(encoding="utf-8")
        else:
            text = (
                "GNU GENERAL PUBLIC LICENSE\n"
                "Version 3, 29 June 2007\n\n"
                "Copyright (C) 2024 SocietiesTv Broadcasting & Network\n\n"
                "This program is free software: you can redistribute it and/or modify "
                "it under the terms of the GNU General Public License as published by "
                "the Free Software Foundation, either version 3 of the License, or "
                "(at your option) any later version.\n\n"
                "Commercial Use Notice:\n"
                "Personal/educational use is free. Commercial use requires a separate license."
            )
        license_box.insert("1.0", text)
        license_box.configure(state="disabled")

        # Accept checkbox
        self._license_var = ctk.BooleanVar(value=False)
        self._cb_license = ctk.CTkCheckBox(
            body, text="I accept the agreement",
            variable=self._license_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=C["text2"],
            fg_color=C["accent"], hover_color=C["accent_hover"],
            command=self._on_license_toggle,
        )
        self._cb_license.pack(anchor="w", pady=(4, 0))

        # Start with Next disabled
        self.after(100, lambda: self._btn_next.configure(state="disabled"))
        return page

    def _on_license_toggle(self):
        self._btn_next.configure(state="normal" if self._license_var.get() else "disabled")

    # ── Page 3: Options (Location + Tasks) ──

    def _page_options(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._main, fg_color=C["bg"])
        self._make_header(page, "Installation Options", "Choose where to install and what to include")

        body = ctk.CTkFrame(page, fg_color=C["bg_white"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # ── Install Location ──
        ctk.CTkLabel(body, text="📁  Install Location",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color=C["text"]).pack(anchor="w", pady=(0, 6))

        path_row = ctk.CTkFrame(body, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, 4))

        self._dir_entry = ctk.CTkEntry(
            path_row, fg_color=C["bg_white"], border_color=C["border"],
            text_color=C["text2"], height=30, corner_radius=3,
            font=ctk.CTkFont(size=12, family="Consolas"),
        )
        self._dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._dir_entry.insert(0, str(self._install_dir))

        ctk.CTkButton(path_row, text="Browse...", width=80, height=30,
                       fg_color=C["btn_bg"], hover_color="#e0e0e0",
                       text_color=C["text2"], border_color=C["btn_border"], border_width=1,
                       corner_radius=3, font=ctk.CTkFont(size=11),
                       command=self._browse_dir).pack(side="right")

        self._space_lbl = ctk.CTkLabel(body, text="",
                                        font=ctk.CTkFont(size=10),
                                        text_color=C["text_light"])
        self._space_lbl.pack(anchor="w", pady=(0, 12))
        self._update_space()

        # ── Separator ──
        ctk.CTkFrame(body, height=1, fg_color=C["border_light"]).pack(fill="x", pady=(0, 12))

        # ── Additional Tasks ──
        ctk.CTkLabel(body, text="✅  Additional Tasks",
                      font=ctk.CTkFont(size=13, weight="bold"),
                      text_color=C["text"]).pack(anchor="w", pady=(0, 6))

        self._comp_vars = {}
        tasks = [
            ("shortcuts", "Create desktop shortcut", True),
            ("file_association", "Associate .freq playlist files", True),
            ("ffmpeg", "Install FFmpeg (for streaming)", True),
        ]

        for key, label, default in tasks:
            var = ctk.BooleanVar(value=default)
            self._comp_vars[key] = var
            ctk.CTkCheckBox(
                body, text=label, variable=var,
                font=ctk.CTkFont(size=12), text_color=C["text2"],
                fg_color=C["accent"], hover_color=C["accent_hover"],
            ).pack(anchor="w", padx=(8, 0), pady=2)

        return page

    def _browse_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=str(self._install_dir))
        if d:
            self._install_dir = Path(d) / APP_NAME
            self._dir_entry.delete(0, "end")
            self._dir_entry.insert(0, str(self._install_dir))
            self._update_space()

    def _update_space(self):
        try:
            usage = sum(f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file()) if DIST_DIR.exists() else 0
            self._space_lbl.configure(text=f"Space required: ~{usage / (1024*1024):.0f} MB")
        except Exception:
            self._space_lbl.configure(text="Space required: ~85 MB")

    # ── Page 4: Installing ──

    def _page_installing(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._main, fg_color=C["bg"])
        self._make_header(page, "Installing FreQ", "Please wait while the installation completes")

        body = ctk.CTkFrame(page, fg_color=C["bg_white"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # Progress bar
        self._progress_bar = ctk.CTkProgressBar(
            body, width=400, height=20,
            fg_color="#dddddd",
            progress_color=C["accent"],
            corner_radius=10,
        )
        self._progress_bar.pack(fill="x", pady=(0, 4))
        self._progress_bar.set(0)

        self._progress_pct = ctk.CTkLabel(body, text="0%",
                                            font=ctk.CTkFont(size=11, weight="bold"),
                                            text_color=C["text"])
        self._progress_pct.pack(anchor="w", pady=(0, 8))

        # Log
        self._log_box = ctk.CTkTextbox(
            body, fg_color=C["log_bg"],
            text_color=C["text3"],
            font=ctk.CTkFont(family="Consolas", size=11),
            border_color=C["border_light"], border_width=1,
            corner_radius=4, wrap="word",
        )
        self._log_box.pack(fill="both", expand=True, pady=(0, 8))
        self._log_box.configure(state="disabled")

        self._install_hint = ctk.CTkLabel(body,
                                           text="This may take a few minutes if FFmpeg is being downloaded.",
                                           font=ctk.CTkFont(size=10),
                                           text_color=C["text_light"])
        self._install_hint.pack(anchor="w")

        return page

    # ── Page 5: Done ──

    def _page_done(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self._main, fg_color=C["bg"])

        # Green success header
        self._make_header(page, f"✅  Installation Complete",
                          f"{APP_NAME} has been installed successfully",
                          bg_colors=(C["green_header"], C["green_header2"], C["green"]))

        body = ctk.CTkFrame(page, fg_color=C["bg_white"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        # Celebration
        ctk.CTkLabel(body, text="🎉", font=ctk.CTkFont(size=48)).pack(pady=(10, 4))
        ctk.CTkLabel(body, text=f"{APP_NAME} is Ready!",
                      font=ctk.CTkFont(size=18, weight="bold"),
                      text_color=C["text"]).pack(pady=(0, 16))

        # Checkboxes
        self._launch_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(body, text="Launch FreQ now",
                         variable=self._launch_var,
                         font=ctk.CTkFont(size=12), text_color=C["text2"],
                         fg_color=C["accent"], hover_color=C["accent_hover"]).pack(anchor="w", padx=20)

        # Info card
        info = ctk.CTkFrame(body, fg_color="#f6f8fa", corner_radius=6,
                             border_color=C["border_light"], border_width=1)
        info.pack(fill="x", padx=20, pady=(16, 0))

        ctk.CTkLabel(info, text="Installed to:",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      text_color=C["text"]).pack(anchor="w", padx=12, pady=(10, 4))

        self._install_path_lbl = ctk.CTkLabel(info, text=str(self._install_dir),
                                               font=ctk.CTkFont(size=11, family="Consolas"),
                                               text_color=C["text3"])
        self._install_path_lbl.pack(anchor="w", padx=12)

        self._install_summary_lbl = ctk.CTkLabel(info, text="",
                                                  font=ctk.CTkFont(size=10),
                                                  text_color=C["text_light"])
        self._install_summary_lbl.pack(anchor="w", padx=12, pady=(4, 10))

        return page

    # ═══════════════════════════════════════════════════════════
    # Install Logic
    # ═══════════════════════════════════════════════════════════

    def _start_install(self):
        self._show_page(3)

        install_dir = Path(self._dir_entry.get()) if self._dir_entry.get() else self._install_dir
        components = {k: v.get() for k, v in self._comp_vars.items()}

        engine = InstallerEngine(install_dir, components)
        engine.on_progress(self._on_progress)
        engine.on_log(self._on_log)

        def _run():
            ok = engine.run()
            self.after(0, lambda: self._on_install_done(ok, install_dir, components))

        threading.Thread(target=_run, daemon=True).start()

    def _on_progress(self, cur, total):
        pct = cur / total if total else 0
        self.after(0, lambda: (
            self._progress_bar.set(pct),
            self._progress_pct.configure(text=f"{int(pct*100)}%"),
        ))

    def _on_log(self, msg, level="info"):
        colors = {"ok": C["accent"], "done": C["green"], "error": C["orange"], "info": C["text3"]}
        def _upd():
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(0, _upd)

    def _on_install_done(self, success, install_dir, components):
        if success:
            self._install_dir = install_dir
            # Update done page labels
            self._install_path_lbl.configure(text=str(install_dir))
            summary_parts = []
            if components.get("shortcuts"):
                summary_parts.append("Desktop shortcut created")
            if components.get("file_association"):
                summary_parts.append(".freq files associated")
            self._install_summary_lbl.configure(text=" • ".join(summary_parts))
            self._show_page(4)
        else:
            self._btn_next.configure(state="normal", text="Retry >",
                                      command=self._start_install)
            self._btn_cancel.configure(state="normal")

    def _finish(self):
        if self._launch_var.get():
            exe = self._install_dir / f"{APP_NAME}.exe"
            if exe.exists():
                try:
                    subprocess.Popen([str(exe)], cwd=str(self._install_dir))
                except Exception:
                    pass
        self.destroy()


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = InstallerApp()
    app.mainloop()
