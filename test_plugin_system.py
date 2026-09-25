#!/usr/bin/env python3
"""Plugin SDK (U5) tests — containment, wiring and the observe-only rule.

All plugins used here are FAKE single files written into a per-test temp
folder — nothing touches audio hardware or the real plugins/ directory.

Covers:
  1.  Valid plugin loads; name/version read from module constants
  2.  Syntax-broken plugin is skipped with a recorded error (app survives)
  3.  Files that are not .py (and _-prefixed files) are ignored
  4.  Hooks receive (ctx, **data); ctx.log/notify/snapshot work
  5.  snapshot() always exposes plugin_api
  6.  Handler exception is contained; app and other plugins keep working
  7.  3 consecutive failures auto-disable the plugin; success resets count
  8.  emit() returns a dispatcher thread and never blocks the caller
  9.  Unknown event → None (never dispatched)
  10. set_enabled / enabled_map / status (no module objects leaked,
      load errors listed)
  11. Observe-only rule (structural): plugin_system imports no playback
      module and PluginContext exposes no playback surface
  12. GUI wiring (structural): events fired at the right places, song is
      passed as a read-only dict (_song_info), shutdown joins with timeout
  13. Reload guard: load_all twice does not duplicate plugins

Run:  python test_plugin_system.py
      (also runs inside test_edge_cases.py via run_all())
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from plugin_system import (  # noqa: E402
    PluginManager, PluginContext, PLUGIN_API_VERSION,
)

RESULTS: list[tuple[str, bool, str]] = []
EVENTS: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))


def fresh_dir() -> Path:
    """Isolated plugin folder per test — no cross-test bleed."""
    return Path(tempfile.mkdtemp(prefix="freq_plugin_test_"))


def write_plugin(folder: Path, stem: str, body: str) -> Path:
    path = folder / f"{stem}.py"
    path.write_text(body, encoding="utf-8")
    return path


def make_manager(folder: Path, snapshot=None) -> PluginManager:
    """Manager over ``folder`` with events captured into EVENTS."""
    EVENTS.clear()
    return PluginManager(
        folder=folder,
        ctx=PluginContext(
            notify=lambda m: EVENTS.append(("notify", m)),
            log=lambda m: EVENTS.append(("log", m)),
            snapshot=snapshot or (lambda: {"playing": False, "queue_length": 0}),
        ),
    )


# ── 1–3. discovery & loading ─────────────────────────────────────────

def test_valid_plugin_loads() -> None:
    folder = fresh_dir()
    write_plugin(folder, "good", (
        "PLUGIN_NAME = 'Counter'\n"
        "PLUGIN_VERSION = '1.2'\n"
        "def on_start(ctx):\n"
        "    ctx.log('started')\n"
    ))
    mgr = make_manager(folder)
    loaded, errors = mgr.load_all()
    rows = mgr.status()
    check("[load] valid plugin loaded", loaded == 1 and not errors
          and rows[0]["name"] == "Counter", str(rows))
    check("[load] version read from constants",
          rows and rows[0]["version"] == "1.2")
    thread = mgr.emit("on_start")
    if thread:
        thread.join(timeout=2.0)
    check("[load] on_start hook ran and ctx.log worked",
          ("log", "started") in EVENTS, str(EVENTS))


def test_broken_plugin_is_contained() -> None:
    folder = fresh_dir()
    write_plugin(folder, "broken_syntax", "def broken(:\n")   # no _ prefix:
    # (a _-prefixed file is invisible to discovery, which is not the case
    #  under test here — a real user plugin with a syntax error is.)
    mgr = make_manager(folder)
    loaded, errors = mgr.load_all()
    check("[load] broken plugin skipped, app survives",
          loaded == 0 and "broken_syntax.py" in errors, str(errors))
    rows = mgr.status()
    check("[load] load error surfaced in status",
          any(r.get("load_error") for r in rows), str(rows))


def test_discovery_filters() -> None:
    folder = fresh_dir()
    (folder / "notes.txt").write_text("not a plugin", encoding="utf-8")
    write_plugin(folder, "_private", "PLUGIN_NAME='X'\n")
    write_plugin(folder, "real", "PLUGIN_NAME='Real'\n")
    mgr = make_manager(folder)
    names = sorted(p.name for p in mgr.discover())
    check("[load] only .py files, _-prefixed ignored",
          names == ["real.py"], str(names))


# ── 4–5. context surface ─────────────────────────────────────────────

def test_hook_payload_and_context() -> None:
    folder = fresh_dir()
    scratch = folder / "scratch.json"
    write_plugin(folder, "hooky", (
        "def on_track_started(ctx, song=None):\n"
        "    _scratch(dict(song or {}), dict(ctx.snapshot()))\n"
        "    ctx.notify('now: ' + str((song or {}).get('title')))\n"
        "def _scratch(a, b):\n"
        "    with open(r'" + str(scratch) + "', 'a', encoding='utf-8') as f:\n"
        "        f.write(json.dumps([a, b]) + '\\n')\n"
        "import json\n"
    ))
    mgr = make_manager(folder, snapshot=lambda: {"playing": True,
                                                 "queue_length": 3})
    mgr.load_all()
    thread = mgr.emit("on_track_started",
                      song={"title": "Song A", "source": "file"})
    check("[ctx] emit returns a dispatcher thread", thread is not None)
    if thread:
        thread.join(timeout=2.0)
    check("[ctx] hook wrote its payload",
          scratch.is_file() and len(
              scratch.read_text(encoding="utf-8").strip().splitlines()) == 1,
          "scratch missing or multi-line")
    if scratch.is_file():
        song, snap = json.loads(
            scratch.read_text(encoding="utf-8").splitlines()[0])
        check("[ctx] song data arrives intact",
              song.get("title") == "Song A" and song.get("source") == "file")
        check("[ctx] snapshot carries plugin_api",
              snap.get("plugin_api") == PLUGIN_API_VERSION)
    check("[ctx] ctx.notify reaches the app sink",
          ("notify", "now: Song A") in EVENTS, str(EVENTS))


def test_snapshot_always_has_api_version() -> None:
    folder = fresh_dir()
    mgr = make_manager(folder, snapshot=lambda: None)
    snap = mgr._ctx.snapshot()
    check("[ctx] even a broken snapshot returns plugin_api",
          snap.get("plugin_api") == PLUGIN_API_VERSION, str(snap))


# ── 6–7. failure containment ─────────────────────────────────────────

def test_exception_containment() -> None:
    folder = fresh_dir()
    write_plugin(folder, "bad", "def on_start(ctx):\n    raise RuntimeError('boom')\n")
    write_plugin(folder, "good2", "def on_start(ctx):\n    ctx.log('alive')\n")
    mgr = make_manager(folder)
    mgr.load_all()
    thread = mgr.emit("on_start")
    if thread:
        thread.join(timeout=2.0)
    check("[fail] other plugin still ran after one crashed",
          ("log", "alive") in EVENTS, str(EVENTS))
    rows = {r["name"]: r for r in mgr.status() if not r.get("load_error")}
    check("[fail] error counted, plugin still enabled (1st failure)",
          rows["bad"]["total_errors"] == 1 and rows["bad"]["enabled"] is True,
          str(rows.get("bad")))


def test_auto_disable_after_three_failures() -> None:
    folder = fresh_dir()
    log = folder / "flaky.log"
    write_plugin(folder, "flaky", (
        "def on_track_finished(ctx, song=None):\n"
        "    with open(r'" + str(log) + "', 'a', encoding='utf-8') as f:\n"
        "        f.write('call\\n')\n"
        "    raise ValueError('always fails')\n"
    ))
    mgr = make_manager(folder)
    mgr.load_all()
    for _ in range(5):                      # 5 events → only 3 handled calls
        t = mgr.emit("on_track_finished", song=None)
        if t:
            t.join(timeout=2.0)
    calls = log.read_text(encoding="utf-8").splitlines() if log.is_file() else []
    check("[fail] hook called exactly 3 times then auto-disabled",
          len(calls) == 3, f"calls={len(calls)}")
    rows = {r["name"]: r for r in mgr.status() if not r.get("load_error")}
    check("[fail] plugin disabled after 3 consecutive failures",
          rows.get("flaky", {}).get("enabled") is False, str(rows.get("flaky")))
    check("[fail] consecutive counter capped at threshold",
          rows.get("flaky", {}).get("consecutive_errors") == 3,
          str(rows.get("flaky")))


def test_success_resets_error_streak() -> None:
    folder = fresh_dir()
    write_plugin(folder, "moody", (
        "STATE = {'fail': True}\n"
        "def on_start(ctx):\n"
        "    if STATE['fail']:\n"
        "        STATE['fail'] = False\n"
        "        raise RuntimeError('first time fails')\n"
    ))
    mgr = make_manager(folder)
    mgr.load_all()
    t = mgr.emit("on_start")
    if t:
        t.join(timeout=2.0)
    t = mgr.emit("on_start")                # succeeds now
    if t:
        t.join(timeout=2.0)
    rows = {r["name"]: r for r in mgr.status() if not r.get("load_error")}
    check("[fail] success resets the consecutive counter",
          rows["moody"]["consecutive_errors"] == 0
          and rows["moody"]["total_errors"] == 1
          and rows["moody"]["enabled"] is True, str(rows["moody"]))


# ── 8–10. dispatch mechanics ─────────────────────────────────────────

def test_emit_is_nonblocking_and_unknown_events_ignored() -> None:
    folder = fresh_dir()
    write_plugin(folder, "slowpoke", "import time\n"
                                     "def on_start(ctx):\n    time.sleep(0.6)\n")
    mgr = make_manager(folder)
    mgr.load_all()
    t0 = time.monotonic()
    thread = mgr.emit("on_start")
    elapsed = time.monotonic() - t0
    check("[dispatch] emit returns before the hook finishes",
          thread is not None and elapsed < 0.3, f"elapsed={elapsed:.2f}s")
    if thread:
        thread.join(timeout=3.0)
    check("[dispatch] unknown event returns None",
          mgr.emit("on_hack_the_mainframe") is None)
    check("[dispatch] event outside HOOKS never dispatched",
          mgr.emit("__init__") is None)


def test_enable_map_and_status_shape() -> None:
    folder = fresh_dir()
    write_plugin(folder, "alpha", "PLUGIN_NAME='Alpha'\n")
    mgr = make_manager(folder)
    mgr.load_all()
    mgr.set_enabled("Alpha", False)
    check("[state] set_enabled flips the map",
          mgr.enabled_map() == {"Alpha": False}, str(mgr.enabled_map()))
    rows = mgr.status()
    check("[state] status never leaks module objects",
          all("module" not in r for r in rows), str(rows))
    check("[state] status rows are JSON-safe primitives",
          all(isinstance(v, (str, int, float, bool, type(None)))
              for r in rows for v in r.values()), str(rows))


def test_no_duplicate_loads() -> None:
    folder = fresh_dir()
    write_plugin(folder, "dup", "PLUGIN_NAME='Dup'\n")
    mgr = make_manager(folder)
    mgr.load_all()
    loaded, _ = mgr.load_all()
    check("[state] load_all twice does not duplicate",
          loaded == 0 and len(mgr.status()) == 1, str(mgr.status()))


# ── 11. observe-only guarantee (structural) ──────────────────────────

def test_observe_only_structural() -> None:
    import ast
    source = (Path(__file__).parent / "plugin_system.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"player", "pygame", "gui", "native_audio_render",
                         "native_audio_capture", "native_audio_decode"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    check("[observe-only] SDK imports no playback module",
          not (imported & forbidden_imports), f"imports: {sorted(imported)}")

    ctx_public = {a for a in dir(PluginContext) if not a.startswith("_")}
    check("[observe-only] PluginContext surface is notify/log/snapshot only",
          ctx_public <= {"notify", "log", "snapshot"}, str(ctx_public))

    manager_methods = {a for a in dir(PluginManager) if not a.startswith("_")}
    playback_shaped = {"play", "stop", "pause", "resume", "skip", "seek",
                       "shutdown", "play_file", "play_youtube"}
    check("[observe-only] manager exposes no playback-shaped method",
          not (manager_methods & playback_shaped), str(manager_methods))


# ── 12. GUI wiring (structural) ──────────────────────────────────────

def test_gui_wiring() -> None:
    gui_src = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
    check("[gui] SDK imported",
          "from plugin_system import PluginManager, PluginContext" in gui_src)
    check("[gui] on_start fired after load",
          'self._emit_plugins("on_start")' in gui_src)
    # track events pass a READ-ONLY dict, never the live Song object
    check("[gui] on_track_started passes _song_info dict",
          'self._emit_plugins("on_track_started", song=self._song_info(song))'
          in gui_src)
    check("[gui] on_track_finished passes _song_info dict",
          'song=self._song_info(self._current_song_or_none())' in gui_src)
    check("[gui] _song_info returns a plain dict copy",
          "def _song_info(song) -> dict:" in gui_src
          and "return {" in gui_src.split("def _song_info")[1].split("def ")[0])
    check("[gui] destroy fires on_shutdown with a join timeout",
          '_shutdown_thread.join(timeout=1.5)' in gui_src
          and 'self._emit_plugins("on_shutdown")' in gui_src)
    check("[gui] plugin notify goes to the status line",
          "def _plugin_notify(self, message: str) -> None:" in gui_src)
    # Settings panel (U5): rows/status/reload/open-folder + refresh after
    # .freq restore, so a loaded preset shows the right switches.
    for token, name in (
        ("def _refresh_plugin_rows", "panel rebuilds plugin rows"),
        ("def _on_plugin_toggle", "per-plugin switch handler"),
        ("def _reload_plugins", "Reload rescans the folder"),
        ("def _open_plugin_folder", "Open folder button wired"),
        ("self._refresh_plugin_rows()", "panel refreshed at build + restore"),
        ('"plugin_enabled_map": self.plugin_manager.enabled_map()',
         "switch state persists in .freq collect"),
        ('settings.get("plugin_enabled_map", {})',
         ".freq restore applies enabled map"),
        ("if hasattr(self, \"_plugin_rows_frame\"):",
         "restore refreshes only when the panel exists"),
    ):
        check(f"[gui-panel] {name}", token in gui_src)


def run_all(into: list | None = None) -> tuple[int, int]:
    """Run every plugin test; copy results into ``into`` when embedding."""
    tests = (
        test_valid_plugin_loads,
        test_broken_plugin_is_contained,
        test_discovery_filters,
        test_hook_payload_and_context,
        test_snapshot_always_has_api_version,
        test_exception_containment,
        test_auto_disable_after_three_failures,
        test_success_resets_error_streak,
        test_emit_is_nonblocking_and_unknown_events_ignored,
        test_enable_map_and_status_shape,
        test_no_duplicate_loads,
        test_observe_only_structural,
        test_gui_wiring,
    )
    for t in tests:
        t()
    if into is not None:
        into.extend(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    return passed, len(RESULTS)


def main() -> None:
    print("\n=== Plugin SDK suite (observe-only v1) ===\n")
    passed, total = run_all()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n=== Summary: {passed}/{total} passed"
          + (" — all green ✅" if not fails else f" — {len(fails)} FAILED"))
    for n, ok, d in fails:
        print(f"  ❌ {n} — {d}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
