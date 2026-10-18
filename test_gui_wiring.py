#!/usr/bin/env python3
"""GUI wiring guards [wire-1017] — catch "module finished, GUI never wired".

The 1016.1 → 1017.1 debt was invisible to every existing check: `sentinel_heal`
and `aircheck_split` shipped a full module + a passing module-level smoke, but
gui.py never imported them and never assigned the attributes their smoke
asserts on — so the failure only surfaced as
``AttributeError: 'tkapp' object has no attribute 'sentinel_heal'`` deep inside
a Tk smoke run (and only on a machine with a display).

Three guards, all static (they import nothing, so they work headless):

  G1  imported but never touched — every LOCAL project module imported in
      gui.py must be referenced by code, not just by its import line.
  G2  smoke-declared wiring — every ``app.<attr>`` a GUI smoke asserts on must
      exist in gui.py (assigned on ``self`` or defined as a method). A missing
      wiring is reported as a wiring failure instead of an AttributeError crash.
  G3  the guards themselves — both detectors are run against synthetic
      fixtures that MUST be flagged, so a detector that quietly stops
      detecting cannot pass.

Known debt lives in BASELINE_* below, each entry with a ticket. The test fails
when NEW debt appears and when a baselined entry is silently paid off (a stale
baseline is a failure, not a pass) — so the list has to shrink by hand.
``--strict`` ignores the baseline: it fails while any debt remains, which is the
mode to run at the release quality gate.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STRICT = "--strict" in sys.argv

# ────────────────────────────────────────────────────────────────
# Baseline — the known debt, each entry with a ticket/reason.
# Removing an entry without paying the debt (or paying the debt without
# removing the entry) fails the run.
# ────────────────────────────────────────────────────────────────

# G1: local names imported in gui.py that no code in gui.py touches.
BASELINE_UNUSED_IMPORTS: dict[str, str] = {
    "SongStatus": "leftover of the 0925 queue rework — re-check in Q0 cleanup",
    "cached_icon": "icons.cached_icon — no caller since 0926, prefer get_tk_image",
    "ScheduleEvent": "only ScheduleAction/Scheduler are driven from the GUI",
    "CustomTitleBar": "imported 0924, never instantiated — custom frame is inline",
    "PlaybackModes": "mode logic reads the .freq dict directly",
    "EditResult": "AudioEditor result is consumed as a bool in the editor dialog",
    "FONT_SIZES": "gui reads theme.FONT_SIZES through the COLORS/theme contract",
}

# G1 informational: names used ONLY in type annotations (deliberate, but a
# module reached only through a type hint is not "wired" — listed, not failed).
BASELINE_ANNOTATION_ONLY: dict[str, str] = {
    "UpdateInfo": "type hint of the update-check callbacks (gui.py:9761,9766)",
}

# G2: app attributes the GUI smokes assert on that gui.py does not define yet.
BASELINE_MISSING_WIRING: dict[str, str] = {
    "sentinel_heal": "1017.1 Q1 — Sentinel self-heal not wired into gui yet",
    "sentinel_heal_mode_var": "1017.1 Q1 — heal mode dropdown not built yet",
    "_on_sentinel_mode_change": "1017.1 Q1 — heal mode handler not built yet",
    "aircheck_split_var": "1017.1 Q2 — air-check split checkbox not built yet",
    "_aircheck_tracklog": "1017.1 Q2 — TrackLog not constructed in gui yet",
    "_aircheck_note_track": "1017.1 Q2 — TrackLog tagging hook not built yet",
}

# Attributes every RadioApp has through Tk/CTk itself (not wired by us).
INHERITED_ATTRS = {
    "destroy", "update", "update_idletasks", "after", "after_cancel",
    "after_idle", "bind", "unbind", "bind_all", "mainloop", "quit",
    "title", "geometry", "withdraw", "deiconify", "iconify", "lift",
    "protocol", "resizable", "attributes", "wm_attributes", "state",
    "configure", "config", "cget", "focus_set", "focus_force", "focus_get",
    "event_generate", "event_add", "event_info",
    "grab_set", "grab_release", "wait_visibility", "wait_window", "tk",
    "clipboard_append", "clipboard_clear", "clipboard_get", "bell",
    "iconbitmap", "wm_iconbitmap", "minsize", "maxsize", "winfo_toplevel",
}
INHERITED_PREFIXES = ("winfo_", "clipboard_", "after_", "wm_", "grid_",
                      "pack_", "place_", "grab_")


# ────────────────────────────────────────────────────────────────
# Detectors (pure: source text + local module names in, findings out)
# ────────────────────────────────────────────────────────────────

def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _annotation_nodes(tree: ast.AST) -> set[ast.AST]:
    """Name nodes that appear inside an annotation (arg/AnnAssign/return)."""
    parents = _parent_map(tree)
    out: set[ast.AST] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name):
            continue
        cur: ast.AST = node
        while cur in parents:
            par = parents[cur]
            if isinstance(par, ast.arg) and par.annotation is cur:
                out.add(node)
                break
            if isinstance(par, ast.AnnAssign) and par.annotation is cur:
                out.add(node)
                break
            if isinstance(par, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and par.returns is cur:
                out.add(node)
                break
            cur = par
    return out


def _optional_span(tree: ast.AST, parents: dict) -> set[ast.AST]:
    """Nodes inside a try/except ImportError or an `if TYPE_CHECKING:`."""
    out: set[ast.AST] = set()
    for node in ast.walk(tree):
        wrapped = False
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                names = [handler.type] if handler.type is not None else []
                for t in names:
                    if isinstance(t, ast.Name) and t.id == "ImportError":
                        wrapped = True
                    elif isinstance(t, ast.Tuple):
                        wrapped = wrapped or any(
                            isinstance(e, ast.Name) and e.id == "ImportError"
                            for e in t.elts)
        elif isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                wrapped = True
            elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                wrapped = True
        if wrapped:
            for inner in ast.walk(node):
                out.add(inner)
    return out


def analyze_imports(src: str, local_names: set[str]) -> dict:
    """Return imports of local modules plus how each bound name is used."""
    tree = ast.parse(src)
    parents = _parent_map(tree)
    annotations = _annotation_nodes(tree)
    optional = _optional_span(tree, parents)

    imported: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = alias.name.split(".")[0]
                if base in local_names:
                    imported.append({
                        "name": alias.asname or base, "module": base,
                        "line": node.lineno, "optional": node in optional,
                    })
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "").split(".")[0]
            if base and base in local_names:
                for alias in node.names:
                    imported.append({
                        "name": alias.asname or alias.name, "module": base,
                        "line": node.lineno, "optional": node in optional,
                    })

    reexports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        reexports.add(elt.value)

    uses: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            uses.setdefault(node.id, []).append(node.lineno)
        elif isinstance(node, ast.Name):
            uses.setdefault(node.id, []).append(-node.lineno)

    findings = []
    for item in imported:
        loads = [n for n in uses.get(item["name"], []) if n > 0]
        ann = [n for n in annotations
               if isinstance(n, ast.Name) and n.id == item["name"]]
        ann_lines = sorted(n.lineno for n in ann)
        body_loads = [n for n in loads if n not in ann_lines]
        item["body_loads"] = body_loads
        item["annotation_lines"] = ann_lines
        item["reexported"] = item["name"] in reexports
        if not body_loads and not item["reexported"]:
            findings.append(item)
    return {"imports": imported, "findings": findings}


def smoke_expectations(src: str, app_names=("app", "app2")) -> dict[str, list[int]]:
    """`app.<attr>` reads in a smoke file → {attr: [lines]}."""
    out: dict[str, list[int]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id in app_names:
            out.setdefault(node.attr, []).append(node.lineno)
    return out


def gui_members(src: str) -> set[str]:
    """Names RadioApp exposes: `self.<attr> = ...` plus defined methods."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) \
                        and isinstance(target.value, ast.Name) \
                        and target.value.id == "self":
                    out.add(target.attr)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Attribute) \
                    and isinstance(target.value, ast.Name) \
                    and target.value.id == "self":
                out.add(target.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
    return out


def is_inherited(attr: str) -> bool:
    return attr in INHERITED_ATTRS or attr.startswith(INHERITED_PREFIXES)


def constructs_radioapp(src: str) -> bool:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "RadioApp":
            return True
    return False


# ────────────────────────────────────────────────────────────────
# Project wiring — apply the detectors to the real tree
# ────────────────────────────────────────────────────────────────

def local_module_names(root: Path) -> set[str]:
    names = {p.stem for p in root.glob("*.py")}
    names |= {p.name for p in root.iterdir()
              if p.is_dir() and (p / "__init__.py").exists()}
    names.discard(Path(__file__).stem)
    return names


def gui_wiring_state(gui_src: str, local_names: set[str]) -> dict:
    return analyze_imports(gui_src, local_names)


def smoke_wiring_gaps(root: Path, gui_src: str) -> tuple[dict, list[str]]:
    """{attr: [(file, line)]} the smokes need and gui.py does not provide."""
    members = gui_members(gui_src)
    gaps: dict[str, list[tuple[str, int]]] = {}
    scanned: list[str] = []
    files = sorted(root.glob("_smoke_*.py")) + sorted(root.glob("test_*.py"))
    for path in files:
        if path.name == Path(__file__).name:
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if not constructs_radioapp(src):
            continue
        scanned.append(path.name)
        for attr, lines in smoke_expectations(src).items():
            if attr in members or is_inherited(attr):
                continue
            gaps.setdefault(attr, []).extend(
                (path.name, line) for line in lines)
    return gaps, scanned


# ────────────────────────────────────────────────────────────────
# Reporting
# ────────────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✔ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ✘ {name} — {detail}")


def section(title: str) -> None:
    print(f"\n— {title} —")


def ratchet(label: str, fresh: dict, stale: list, strict_fails: dict) -> None:
    """Common shape: fresh debt fails, stale baseline entries fail."""
    shown = ", ".join(sorted(fresh)[:6]) or "-"
    check(f"{label}: no NEW unwired item", not fresh,
          f"{len(fresh)} new — {shown}"
          + (f" (+{len(fresh) - 6} more)" if len(fresh) > 6 else ""))
    check(f"{label}: baseline not stale (paid-off entries removed)",
          not stale, f"remove from BASELINE: {', '.join(sorted(stale)) or '-'}")
    if STRICT and strict_fails:
        check(f"{label}: STRICT — all debt paid", False,
              f"{len(strict_fails)} baselined item(s) still unwired: "
              f"{', '.join(sorted(strict_fails)[:6])}")


def main() -> tuple[int, int]:
    gui_path = ROOT / "gui.py"
    gui_src = gui_path.read_text(encoding="utf-8")
    local_names = local_module_names(ROOT)
    check("gui.py parsed as a local module graph", bool(local_names),
          f"{len(local_names)} local module name(s)")

    # ── G1: imported but never touched ──────────────────────────
    section("G1. Every local import in gui.py is actually touched by code")
    state = gui_wiring_state(gui_src, local_names)
    unused = {f["name"]: f for f in state["findings"]}
    print(f"    {len(state['imports'])} local name(s) imported, "
          f"{len(unused)} with no code use")
    known_unused = set(BASELINE_UNUSED_IMPORTS) | set(BASELINE_ANNOTATION_ONLY)
    fresh_unused = {n: v for n, v in unused.items() if n not in known_unused}
    stale_unused = ([n for n in BASELINE_UNUSED_IMPORTS if n not in unused]
                    + [n for n in BASELINE_ANNOTATION_ONLY if n not in unused])
    strict_unused = {n: v for n, v in unused.items() if n in known_unused}
    for name, info in sorted(unused.items()):
        tag = "baselined" if name in BASELINE_UNUSED_IMPORTS else (
            "baselined (annotation-only)"
            if name in BASELINE_ANNOTATION_ONLY else "NEW")
        ann = (f" (annotation-only at {info['annotation_lines']})"
               if info["annotation_lines"] and not info["body_loads"]
               and name not in BASELINE_ANNOTATION_ONLY else "")
        opt = " [optional-import block]" if info["optional"] else ""
        print(f"      · {name:20s} from {info['module']:18s} "
              f"line {info['line']:5d} — {tag}{ann}{opt}")
    ratchet("G1 unused imports", fresh_unused, stale_unused, strict_unused)

    annotation_only = sorted(
        n for n, i in unused.items()
        if i["annotation_lines"] and not i["body_loads"])
    check("every annotation-only import is declared as such",
          all(n in BASELINE_ANNOTATION_ONLY for n in annotation_only),
          f"undeclared: "
          f"{[n for n in annotation_only if n not in BASELINE_ANNOTATION_ONLY]}")
    mislabelled = [n for n in BASELINE_ANNOTATION_ONLY
                   if n in unused and n not in annotation_only]
    check("names declared annotation-only really are annotation-only",
          not mislabelled, f"mislabelled: {mislabelled}")

    # ── G2: smoke-declared wiring ───────────────────────────────
    section("G2. Every app attribute the GUI smokes assert on exists in gui.py")
    gaps, scanned = smoke_wiring_gaps(ROOT, gui_src)
    check("GUI smokes were found and scanned", len(scanned) >= 3,
          f"scanned: {scanned or '-'} (a vacuous scan must not pass)")
    print(f"    scanned {len(scanned)} smoke(s): {', '.join(scanned)}")
    for attr, places in sorted(gaps.items()):
        where = ", ".join(f"{f}:{l}" for f, l in places[:3])
        tag = "baselined" if attr in BASELINE_MISSING_WIRING else "NEW"
        print(f"      · app.{attr:24s} wanted by {where} — {tag}")
    fresh_gaps = {a: v for a, v in gaps.items()
                  if a not in BASELINE_MISSING_WIRING}
    stale_gaps = [a for a in BASELINE_MISSING_WIRING if a not in gaps]
    strict_gaps = {a: v for a, v in gaps.items()
                   if a in BASELINE_MISSING_WIRING}
    ratchet("G2 smoke-declared wiring", fresh_gaps, stale_gaps, strict_gaps)

    # ── G3: the guards can fail ─────────────────────────────────
    section("G3. Guard self-test on synthetic fixtures")
    tmp = Path(tempfile.mkdtemp(prefix="wire_fixtures_"))
    (tmp / "alpha.py").write_text("class Beta:\n    pass\n", encoding="utf-8")
    unused_fixture = (
        "from alpha import Beta\n"
        "from alpha import Beta as Gamma\n"
        "from alpha import Beta as Delta\n\n"
        "def unrelated():\n"
        "    return 1\n\n"
        "def hinted(x: Gamma) -> None:\n"
        "    print(Delta())\n"
    )
    st = analyze_imports(unused_fixture, {"alpha"})
    found = {f["name"]: f for f in st["findings"]}
    check("fixture: unused local import IS flagged", set(found) == {"Beta", "Gamma"},
          str(sorted(found)))
    check("fixture: genuinely used alias is NOT flagged (Delta)",
          "Delta" not in found, str(sorted(found)))
    check("fixture: annotation-only import is classified as such",
          found.get("Gamma", {}).get("annotation_lines") == [8]
          and found.get("Beta", {}).get("annotation_lines") == [],
          f"Beta={found.get('Beta')} Gamma={found.get('Gamma')}")

    used_fixture = (
        "from alpha import Beta\n\n"
        "def build():\n"
        "    return Beta()\n"
    )
    check("fixture: used local import is NOT flagged",
          analyze_imports(used_fixture, {"alpha"})["findings"] == [])

    reexport_fixture = (
        "from alpha import Beta\n\n"
        "__all__ = [\"Beta\"]\n"
    )
    check("fixture: re-exported name is NOT flagged",
          analyze_imports(reexport_fixture, {"alpha"})["findings"] == [])

    check("fixture: optional (try/except ImportError) import is marked optional",
          analyze_imports(
              "try:\n    from alpha import Beta\nexcept ImportError:\n    Beta = None\n",
              {"alpha"})["findings"][0]["optional"] is True)

    nested = (
        "def build():\n"
        "    from alpha import Beta\n"
        "    return Beta()\n\n"
        "def forget():\n"
        "    from alpha import Beta as Orphan\n"
        "    return 1\n"
    )
    nested_found = {f["name"] for f in analyze_imports(nested, {"alpha"})["findings"]}
    check("fixture: function-local import is checked too (used vs orphaned)",
          nested_found == {"Orphan"}, str(sorted(nested_found)))

    mini_gui = (
        "class RadioApp:\n"
        "    def __init__(self):\n"
        "        self.wired_thing = 1\n\n"
        "    def wire_it(self):\n"
        "        return self.wired_thing\n"
    )
    mini_smoke = (
        "app = RadioApp()\n"
        "app.wired_thing\n"
        "app.wire_it()\n"
        "app.update_idletasks()\n"
    )
    mini_gap = (
        "app = RadioApp()\n"
        "app.not_wired_thing()\n"
        "app.update_idletasks()\n"
    )
    check("fixture: smoke expectation satisfied by gui IS recognised",
          smoke_expectations(mini_smoke)["wired_thing"] == [2]
          and "wired_thing" in gui_members(mini_gui)
          and "wire_it" in gui_members(mini_gui))
    cross = {a: v for a, v in smoke_expectations(mini_gap).items()
             if a not in gui_members(mini_gui) and not is_inherited(a)}
    check("fixture: missing wiring IS flagged, Tk inherited attr is not",
          set(cross) == {"not_wired_thing"}, str(sorted(cross)))
    check("fixture: a file without RadioApp() is not scanned",
          not constructs_radioapp("import os\n"))

    print(f"\n=== Summary: {PASSED}/{PASSED + FAILED} passed"
          f"{' (STRICT)' if STRICT else ''} ===")
    if not FAILED:
        debt = len(BASELINE_UNUSED_IMPORTS) + len(BASELINE_MISSING_WIRING)
        print(f"baselined debt still open: {debt} item(s) — "
              "see BASELINE_* in this file (tickets: 1017.1 Q0–Q2)")
    if FAILURES:
        for line in FAILURES:
            print("FAILED:", line)
        return PASSED, PASSED + FAILED
    return PASSED, PASSED


def run_all(into: list | None = None) -> tuple[int, int]:
    passed, total = main()
    if into is not None:
        into.append(("gui-wiring", passed, total))
    return passed, total


if __name__ == "__main__":
    passed, total = run_all()
    sys.exit(1 if passed != total else 0)
