#!/usr/bin/env python3
"""FreQ Plugin SDK (U5 — Ultimate Platform), v1: observe-only.

Users drop single-file Python plugins into the ``plugins/`` folder (next
to the app or the source tree) and FreQ calls their event hooks. The
sound engine is NEVER touched by plugins: v1 is a pure observer, so a
bad plugin can cost at most its own thread — not the audio path.

Plugin contract (every hook is optional)::

    PLUGIN_NAME = "Now Playing Webhook"
    PLUGIN_VERSION = "1.0"

    def on_start(ctx): ...
    def on_track_started(ctx, song=None): ...
    def on_track_finished(ctx, song=None): ...
    def on_shutdown(ctx): ...

``ctx`` is a :class:`PluginContext` — ``ctx.log(msg)``, ``ctx.notify(msg)``
and ``ctx.snapshot()`` are ALL it offers. No player, no queue access.

Safety rules (Ultimate ground rules):
  * a plugin that fails to load is skipped and reported — the app runs on
  * every hook call is wrapped: an exception in a plugin never escapes;
    3 consecutive failures auto-disable the plugin (log explains why)
  * hooks run on a daemon thread, one emit at a time — a slow webhook
    never blocks the UI or the audio thread
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("freq.plugins")

PLUGIN_API_VERSION = 1
HOOKS = ("on_start", "on_track_started", "on_track_finished", "on_shutdown")
MAX_CONSECUTIVE_ERRORS = 3


def default_plugin_dir() -> Path:
    """plugins/ beside the frozen exe, or beside this file in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "plugins"
    return Path(__file__).resolve().parent / "plugins"


class PluginContext:
    """The safe surface handed to plugins — observe + report only."""

    def __init__(
        self,
        notify: Callable[[str], None],
        log: Callable[[str], None],
        snapshot: Callable[[], Dict[str, Any]],
    ) -> None:
        self._notify = notify
        self._log = log
        self._snapshot = snapshot

    def notify(self, message: str) -> None:
        """Show a short message on the app's status line."""
        try:
            self._notify(str(message))
        except Exception:
            pass

    def log(self, message: str) -> None:
        """Write a line into FreQ's plugin log."""
        try:
            self._log(str(message))
        except Exception:
            pass

    def snapshot(self) -> Dict[str, Any]:
        """Read-only view of playback state (playing, queue length, ...)."""
        try:
            data = dict(self._snapshot() or {})
        except Exception:
            data = {}
        data["plugin_api"] = PLUGIN_API_VERSION
        return data


class PluginHandle:
    """One loaded plugin plus its runtime bookkeeping."""

    def __init__(self, name: str, version: str, module: Any, path: Path) -> None:
        self.name = name
        self.version = version
        self.module = module
        self.path = path
        self.file = path.name
        self.enabled = True
        self.consecutive_errors = 0
        self.total_errors = 0

    def has_hook(self, event: str) -> bool:
        return callable(getattr(self.module, event, None))


class PluginManager:
    """Discover, load and dispatch — all failures contained."""

    def __init__(
        self,
        folder: Optional[Path] = None,
        ctx: Optional[PluginContext] = None,
    ) -> None:
        self.folder = Path(folder) if folder else default_plugin_dir()
        self._ctx = ctx or PluginContext(
            notify=lambda m: logger.info("[plugin notify] %s", m),
            log=lambda m: logger.info("[plugin] %s", m),
            snapshot=lambda: {},
        )
        self._lock = threading.Lock()
        self._plugins: Dict[str, PluginHandle] = {}
        self._load_errors: Dict[str, str] = {}

    # ── discovery & loading ──────────────────────────────────────────

    def discover(self) -> List[Path]:
        """All *.py plugin files in the folder (folder auto-created)."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except Exception:
            return []
        return sorted(
            p for p in self.folder.glob("*.py")
            if p.is_file() and not p.name.startswith("_")
        )

    def load_all(
        self, enabled_map: Optional[Dict[str, bool]] = None
    ) -> tuple[int, list[str]]:
        """Load every discovered plugin; returns (loaded_count, errors)."""
        enabled_map = enabled_map or {}
        loaded = 0
        for path in self.discover():
            with self._lock:
                already = any(h.file == path.name for h in self._plugins.values())
            if already:
                continue
            handle = self.load_file(path)
            if handle is None:
                continue
            if path.name in enabled_map:
                handle.enabled = bool(enabled_map[path.name])
            loaded += 1
        with self._lock:
            errors = dict(self._load_errors)
        return loaded, errors

    def load_file(self, path: Path) -> Optional[PluginHandle]:
        """Import one plugin file; returns None (with logged error) on any
        failure — a broken plugin can never break the app."""
        path = Path(path)
        try:
            mod_name = f"freq_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("no import loader for file")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception as error:
            logger.error("plugin %s failed to load: %s", path.name, error)
            with self._lock:
                self._load_errors[path.name] = str(error)
            return None
        handle = PluginHandle(
            name=str(getattr(module, "PLUGIN_NAME", path.stem)),
            version=str(getattr(module, "PLUGIN_VERSION", "0")),
            module=module,
            path=path,
        )
        with self._lock:
            self._plugins[handle.name] = handle
            self._load_errors.pop(path.name, None)
        logger.info("plugin loaded: %s v%s (%s)",
                    handle.name, handle.version, handle.file)
        return handle

    # ── dispatch ─────────────────────────────────────────────────────

    def emit(self, event: str, **data) -> Optional[threading.Thread]:
        """Fire an event to every enabled plugin that has the hook.

        Returns the dispatcher thread (for tests) or None when nobody
        listens. Handlers run on that daemon thread — callers return at
        once, so the UI/audio paths are never blocked by plugin code.
        """
        if event not in HOOKS:
            return None
        with self._lock:
            targets = [
                (h, getattr(h.module, event))
                for h in self._plugins.values()
                if h.enabled and h.has_hook(event)
            ]
        if not targets:
            return None
        payload = dict(data)
        thread = threading.Thread(
            target=self._run_hooks, args=(targets, event, payload), daemon=True)
        thread.start()
        return thread

    def _run_hooks(self, targets, event: str, payload: Dict[str, Any]) -> None:
        for handle, hook in targets:
            started = time.monotonic()
            try:
                hook(self._ctx, **payload)
            except Exception:
                with self._lock:
                    handle.consecutive_errors += 1
                    handle.total_errors += 1
                    tripped = (handle.consecutive_errors
                               >= MAX_CONSECUTIVE_ERRORS)
                    if tripped:
                        handle.enabled = False
                logger.exception("plugin %s failed in %s",
                                 handle.name, event)
                if tripped:
                    logger.error(
                        "plugin %s disabled after %d consecutive failures",
                        handle.name, MAX_CONSECUTIVE_ERRORS)
            else:
                with self._lock:
                    handle.consecutive_errors = 0
                spent = time.monotonic() - started
                if spent > 1.0:
                    logger.warning("plugin %s slow in %s: %.2fs",
                                   handle.name, event, spent)

    # ── state ────────────────────────────────────────────────────────

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            handle = self._plugins.get(name)
        if handle is not None:
            handle.enabled = bool(enabled)

    def enabled_map(self) -> Dict[str, bool]:
        with self._lock:
            return {h.name: h.enabled for h in self._plugins.values()}

    def status(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [vars(h).copy() for h in self._plugins.values()]
            rows.sort(key=lambda r: str(r.get("name", "")).lower())
        for row in rows:
            row.pop("module", None)          # not UI-safe
            row["path"] = str(row.get("path", ""))
        with self._lock:
            rows.extend({"file": f, "name": f, "error": msg,
                         "enabled": False, "version": "-", "load_error": True}
                        for f, msg in sorted(self._load_errors.items()))
        return rows
