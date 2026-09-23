"""
FreQ — Centralized Log Handler
Captures all application logs into an in-memory ring buffer
for display in the desktop GUI and web GUI.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LogEntry:
    """A single log message."""
    timestamp: str
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }

    def to_plain(self, colorize: bool = False) -> str:
        """Format as a plain-text line, optionally with ANSI colors."""
        prefix = f"[{self.timestamp}] [{self.level:5s}] [{self.logger}]"
        if colorize:
            colors = {
                "DEBUG": "\033[36m",     # cyan
                "INFO": "\033[32m",      # green
                "WARNING": "\033[33m",   # yellow
                "ERROR": "\033[31m",     # red
                "CRITICAL": "\033[1;31m",  # bold red
            }
            reset = "\033[0m"
            color = colors.get(self.level, "")
            return f"{color}{prefix} {self.message}{reset}"
        return f"{prefix} {self.message}"


class MemoryLogHandler(logging.Handler):
    """Logging handler that stores messages in a thread-safe ring buffer."""

    def __init__(self, max_entries: int = 2000):
        super().__init__()
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
            entry = LogEntry(
                timestamp=ts,
                level=record.levelname,
                logger=record.name or "root",
                message=self.format(record),
            )
            with self._lock:
                self._entries.append(entry)
        except Exception:
            pass  # Never let logging errors crash the app

    def get_entries(
        self,
        last_n: Optional[int] = None,
        min_level: str = "DEBUG",
    ) -> list[LogEntry]:
        """Return log entries, optionally filtered by level."""
        level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        min_val = level_order.get(min_level.upper(), 0)

        with self._lock:
            entries = list(self._entries)

        if min_level:
            entries = [e for e in entries if level_order.get(e.level, 0) >= min_val]

        if last_n and last_n > 0:
            entries = entries[-last_n:]

        return entries

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)


# ═══════════════════════════════════════════════
# Singleton + Setup
# ═══════════════════════════════════════════════

# The global memory handler — import and use in gui.py / app.py
memory_handler = MemoryLogHandler(max_entries=2000)


def setup_logging(level: int = logging.DEBUG) -> None:
    """Configure root logger with the memory handler.

    Call once at application startup. All modules that use
    `logging.getLogger(__name__)` will automatically be captured.
    """
    memory_handler.setFormatter(
        logging.Formatter("%(message)s")
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers if called multiple times
    if memory_handler not in root.handlers:
        root.addHandler(memory_handler)

    # Also capture these noisy libraries at WARNING level
    for noisy in ("urllib3", "yt_dlp", "pygame", "werkzeug"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Log the setup itself
    logging.getLogger("freq").info("Logging initialized — capturing to memory buffer")


# Module-level convenience
def get_logger(name: str = "freq") -> logging.Logger:
    return logging.getLogger(name)
