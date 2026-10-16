#!/usr/bin/env python3
"""Build FreQ and, with --publish, publish it as a GitHub release."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INSTALLER = ROOT / "installer.iss"
FINAL_DIR = ROOT.parent / "_final"

# Keep ✅/⚠/— output (help text, sync report) intact when stdout is
# redirected on a non-UTF-8 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run(*command: str) -> None:
    print("\n> " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


# Ultimate era: the display identity lives in MyAppVersionDisplay and reads
# like "Ultimate 2026 (Build 0924.1)" — year = major version, Build =
# month.day.update-round. MyAppVersion keeps a bare sortable identifier
# (2026.0924.1) so tags/filenames stay filesystem- and git-safe.
_ULTIMATE_DISPLAY_RE = re.compile(
    r"^Ultimate (?P<year>\d{4}) \(Build (?P<mmdd>\d{4})\.(?P<round>\d+)\)$"
)


def version() -> str:
    match = re.search(
        r'^\s*#define\s+MyAppVersion\s+"([^"]+)"',
        INSTALLER.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError("MyAppVersion is missing from installer.iss")
    return match.group(1)


def display_version() -> str:
    """The user-facing version string shown in UI and release notes.

    Ultimate releases (MyAppVersionDisplay present) show the full branding
    form — "Ultimate 2026 (Build 0924.1)"; legacy X.Y.Z releases fall back
    to MyAppVersion untouched, so old-version behaviour is byte-identical.
    """
    match = re.search(
        r'^\s*#define\s+MyAppVersionDisplay\s+"([^"]+)"',
        INSTALLER.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match:
        return match.group(1)
    return version()


# Two accepted release identities:
#   Ultimate era : 2026.0924.1            -> display "Ultimate 2026 (Build 0924.1)"
#   Legacy era   : 2.7.4 / 2.7.4-SP3      -> display unchanged
# The numeric form is kept separately for Windows file metadata (installer.iss
# defines MyAppVersionNumeric for the VersionInfo* fields).
_VERSION_PATTERN = (
    r"(?:\d{4}\.\d{4}\.\d+|(?!\d{4}\.)\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)"
)


def numeric_version(display: str) -> str:
    """Windows file-metadata version for the given identifier.

    Legacy: 2.7.4-SP1 -> 2.7.4.  Ultimate: 2026.0924.1 -> 2026.9.2401
    (day*100 + update-round, so build rounds of one day sort correctly;
    a quad-numeric VersionInfoVersion is spelled out in installer.iss as
    {#MyAppVersionNumeric}.0, so exactly three numeric parts are needed).
    """
    if re.fullmatch(r"\d{4}\.\d{4}\.\d+", display):
        year, mmdd, build_round = display.split(".")
        month = int(mmdd[:2])
        day = int(mmdd[2:])
        return f"{int(year)}.{month}.{day * 100 + int(build_round)}"
    return display.split("-", 1)[0]


def display_version_for(identifier: str) -> str:
    """User-facing branding for an accepted release identifier."""
    match = re.fullmatch(_VERSION_PATTERN, identifier)
    if match and re.fullmatch(r"\d{4}\.\d{4}\.\d+", identifier):
        year, mmdd, build_round = identifier.split(".")
        return f"Ultimate {int(year)} (Build {mmdd}.{build_round})"
    return identifier


def set_version(new_version: str) -> str:
    """Bump MyAppVersion (and MyAppVersionNumeric/Display) in installer.iss.

    installer.iss is the single source of truth for the version — build.py
    (version.txt shown on the splash), create_wizard_images.py, the installer
    filename and the git tag all derive from it, so this one edit updates
    the whole release. Runs as the first step when invoked with --set-version.

    Accepted forms:
      Ultimate era: 2026.0924.1 = year . mmdd . update-round, mirrored to
        MyAppVersionDisplay "Ultimate 2026 (Build 0924.1)" and numeric
        2026.9.241 for the VersionInfo* file metadata (Windows requires
        strictly numeric components).
      Legacy era: 2.7.2-SP1 style, numeric prefix mirrored into
        MyAppVersionNumeric exactly as before.
    """
    if not re.fullmatch(_VERSION_PATTERN, new_version):
        raise RuntimeError(
            f"Invalid version {new_version!r} — use yyyy.mmdd.round (e.g. "
            "2026.0924.1 = Ultimate 2026, Build 0924, update round 1) or "
            "MAJOR.MINOR.PATCH optionally followed by -SUFFIX "
            "(e.g. 2.6.4 or 2.7.2-SP1)"
        )
    display = display_version_for(new_version)
    current = version()
    content = INSTALLER.read_text(encoding="utf-8")
    content, replaced = re.subn(
        r'(^\s*#define\s+MyAppVersion\s+")[^"]+(")',
        rf"\g<1>{new_version}\g<2>",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if replaced != 1:
        raise RuntimeError("MyAppVersion is missing from installer.iss")
    numeric = numeric_version(new_version)
    if re.search(r"^\s*#define\s+MyAppVersionDisplay\s+", content, re.MULTILINE):
        content = re.sub(
            r'(^\s*#define\s+MyAppVersionDisplay\s+")[^"]+(")',
            rf"\g<1>{display}\g<2>",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = re.sub(
            r'(^\s*#define\s+MyAppVersionNumeric\s+"[^"]+")',
            rf'\g<1>\r\n#define MyAppVersionDisplay "{display}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
    if re.search(r"^\s*#define\s+MyAppVersionNumeric\s+", content, re.MULTILINE):
        content = re.sub(
            r'(^\s*#define\s+MyAppVersionNumeric\s+")[^"]+(")',
            rf"\g<1>{numeric}\g<2>",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        content = re.sub(
            r'(^\s*#define\s+MyAppVersion\s+"[^"]+")',
            rf'\g<1>\r\n#define MyAppVersionNumeric "{numeric}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
    INSTALLER.write_text(content, encoding="utf-8")
    # Keep the splash version in sync immediately (build.py re-derives it
    # from installer.iss anyway, but dev runs read this file directly).
    (ROOT / "version.txt").write_text(new_version, encoding="utf-8")
    if new_version == current:
        print(f"Version unchanged: v{current}")
    else:
        print(f"Version bumped: v{current} -> v{new_version}")
    return new_version


def sync_version_files() -> dict:
    """Stage 1 of every release run: re-derive the release identity from
    installer.iss (the single source of truth) and rewrite every version
    file in the source tree to match it.

    Why this exists: installer.iss holds the version, and the whole
    program already READS from it (update_checker falls back to it,
    push_source_code parses it, build.py stamps version.txt from it) —
    but a dev tree edited by hand could drift until the next build. This
    stage makes the tree consistent BEFORE anything builds:

      * version.txt  — rewritten when it disagrees with installer.iss
        (the splash and dev-run update checks read this file directly)
      * RELEASENOTES.md — verified to have a section for this version,
        so publish() never silently falls back to GitHub's auto notes

    update_checker.py / push_source_code.py / gui.py need no writes:
    they derive everything at runtime from the two files above.
    """
    identifier = version()
    display = display_version()
    numeric = numeric_version(identifier)
    changed: list[str] = []

    version_file = ROOT / "version.txt"
    try:
        current_txt = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        current_txt = ""
    if current_txt != identifier:
        version_file.write_text(identifier, encoding="utf-8")
        changed.append(
            f"version.txt {current_txt or '<missing>'!r} -> {identifier!r}")

    notes_ok = release_notes_section(identifier) is not None
    print("Version sync (source of truth: installer.iss):")
    print(f"  identifier    : {identifier}")
    print(f"  display       : {display}")
    print(f"  numeric       : {numeric}")
    print(f"  version.txt   : {'UPDATED — ' + changed[0] if changed else 'in sync'}")
    print(f"  release notes : {'section found' if notes_ok else 'MISSING for this version'}")
    if not notes_ok:
        print("  ⚠ Add a '## FreQ ... " + identifier + " ...' heading to "
              "RELEASENOTES.md, or publish will fall back to GitHub's "
              "auto-generated notes.")
    return {
        "identifier": identifier,
        "display": display,
        "numeric": numeric,
        "changed": changed,
        "release_notes": notes_ok,
    }


# Functions the shipped native_audio_meter extension must expose. A stale
# .pyd from an older build would load fine but silently miss the newer
# spectrum/waveform entry points, dropping the app back to the Python
# fallback — so verify the freshly built module before packaging it.
NATIVE_METER_FUNCTIONS = (
    "analyze_s16le_stereo",
    "process_s16le_stereo",
    "compute_spectrum",
    "waveform_peaks",
    "loudness_new",
    "loudness_process",
    "loudness_reset",
    "samples_to_stereo_pcm",
    "find_silence_end",
    "crossfade_mix",
)

NATIVE_OUTPUT_FUNCTIONS = (
    "list_output_devices",
    "set_default_output_device",
    "get_master_volume",
    "set_master_volume",
)

NATIVE_RENDER_FUNCTIONS = (
    "render_new",
    "render_start",
    "render_stop",
    "render_write",
    "render_buffered",
    "render_emitted",
    "render_set_volume",
    "render_get_volume",
    "render_clear",
    "render_is_running",
    "render_pause",
    "render_set_eq",
    "render_get_eq",
)

# Built-in source API — the jingle-critical path reworked for SP1. These
# were previously defined but never verified, so a stale renderer without
# them could ship silently and crash on every jingle.
NATIVE_RENDER_SOURCE_FUNCTIONS = (
    "render_use_file",
    "render_use_pipe",
    "render_source_state",
    "render_source_seek",
    "render_source_eof",
    "render_source_failed",
)

NATIVE_MASTER_FUNCTIONS = (
    "render_master_set",
    "render_master_get",
    "render_master_selftest",
)

NATIVE_DECODE_FUNCTIONS = (
    "Decoder",
    "decode_file",
)

NATIVE_CAPTURE_FUNCTIONS = (
    "Capture",
    "enum_microphones",
)

NATIVE_STREAM_FUNCTIONS = (
    "stream_pump_start",
    "stream_pump_stop",
    "stream_pump_state",
    "stream_pump_sent",
)

NATIVE_AIRCHECK_FUNCTIONS = (
    "aircheck_start",
    "aircheck_stop",
    "aircheck_levels",
    "aircheck_state",
    "aircheck_error",
)

NATIVE_LIVESTREAM_FUNCTIONS = (
    "stream_start",
    "stream_stop",
    "stream_state",
    "stream_stats",
    "stream_log",
)


def verify_native_meter() -> None:
    """Import the freshly built native extensions and check entry points."""
    checks = (
        ("native_audio_meter", NATIVE_METER_FUNCTIONS),
        ("native_audio_output", NATIVE_OUTPUT_FUNCTIONS),
        ("native_audio_render", NATIVE_RENDER_FUNCTIONS),
        ("native_audio_render", NATIVE_RENDER_SOURCE_FUNCTIONS),
        ("native_audio_render", NATIVE_MASTER_FUNCTIONS),
        ("native_audio_decode", NATIVE_DECODE_FUNCTIONS),
        ("native_audio_capture", NATIVE_CAPTURE_FUNCTIONS),
        ("native_audio_stream", NATIVE_STREAM_FUNCTIONS),
        ("native_aircheck", NATIVE_AIRCHECK_FUNCTIONS),
        ("native_stream", NATIVE_LIVESTREAM_FUNCTIONS),
    )
    for module_name, functions in checks:
        check = (
            "import sys; sys.path.insert(0, r'{root}'); "
            "import {mod} as m; "
            "missing = [f for f in {funcs} if not hasattr(m, f)]; "
            "sys.exit(','.join(missing) or 0)"
        ).format(root=ROOT, mod=module_name, funcs=functions)
        result = subprocess.run(
            [sys.executable, "-c", check],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            missing = result.stdout.strip() or result.stderr.strip()
            raise RuntimeError(
                f"{module_name} is missing entry points: {missing}. "
                "Delete the stale .pyd and rerun the build."
            )
        print(f"{module_name} verified: " + ", ".join(functions))


def verify_bundle() -> None:
    """Check the PyInstaller bundle for runtime-critical files.

    Catches data files silently dropped from the packaging list (e.g. the
    startup splash image) and native modules that failed to ship — both
    would otherwise degrade the installed app with no error at build time.
    """
    dist = ROOT / "dist" / "FreQ"
    if not dist.is_dir():
        raise RuntimeError(f"PyInstaller bundle not found: {dist}")
    # PyInstaller 6+ (onedir) keeps data/binary payload in _internal/.
    payload = dist / "_internal"
    search_dirs = [d for d in (dist, payload) if d.is_dir()]
    native_checks = (
        ("native_audio_meter", NATIVE_METER_FUNCTIONS),
        ("native_audio_output", NATIVE_OUTPUT_FUNCTIONS),
        ("native_audio_render", NATIVE_RENDER_FUNCTIONS),
        ("native_audio_render", NATIVE_RENDER_SOURCE_FUNCTIONS),
        ("native_audio_render", NATIVE_MASTER_FUNCTIONS),
        ("native_audio_decode", NATIVE_DECODE_FUNCTIONS),
        ("native_audio_capture", NATIVE_CAPTURE_FUNCTIONS),
        ("native_audio_stream", NATIVE_STREAM_FUNCTIONS),
        ("native_aircheck", NATIVE_AIRCHECK_FUNCTIONS),
        ("native_stream", NATIVE_LIVESTREAM_FUNCTIONS),
    )
    problems: list[str] = []
    for name in ("FreQ.exe", "splash.jpg", "version.txt"):
        if not any((d / name).is_file() for d in search_dirs):
            problems.append(name)
    bundle_pyd_dir: Path | None = None
    for stem in sorted({mod for mod, _ in native_checks}):
        for d in search_dirs:
            if list(d.glob(f"{stem}*.pyd")):
                if bundle_pyd_dir is None:
                    bundle_pyd_dir = d
                break
        else:
            problems.append(f"{stem}<abi>.pyd")
    if problems:
        raise RuntimeError(
            "PyInstaller bundle is missing: " + ", ".join(problems)
            + ". Check build.py's data/binary lists and rerun."
        )
    print("bundle verified: exe, splash.jpg, native modules present")
    # Import every shipped module FROM THE BUNDLE and verify its entry
    # points. Presence alone once let a stale pre-SP1 renderer ship: it
    # loaded fine yet crashed on every jingle. Verifying the bundled file
    # itself (not the repo copy — cwd is stripped from sys.path) closes it.
    assert bundle_pyd_dir is not None
    for module_name, functions in native_checks:
        check = (
            "import sys; "
            "sys.path = [p for p in sys.path if p not in ('', '.')]"
            f" + [r'{bundle_pyd_dir}']; "
            "import {mod} as m; "
            "assert m.__file__.lower().startswith(r'{bundle_pyd_dir}'.lower()), "
            "m.__file__; "
            "missing = [f for f in {funcs} if not hasattr(m, f)]; "
            "sys.exit(','.join(missing) or 0)"
        ).format(bundle_pyd_dir=bundle_pyd_dir, mod=module_name, funcs=functions)
        result = subprocess.run(
            [sys.executable, "-c", check],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            missing = result.stdout.strip() or result.stderr.strip()
            raise RuntimeError(
                f"bundled {module_name} is missing entry points: {missing}. "
                "Rebuild the native modules (build_native_meter.py), "
                "rebuild the bundle, and rerun."
            )
        print(f"bundled {module_name} verified")


def find_iscc() -> Path:
    """Find Inno Setup in either Program Files location or PATH."""
    candidates = []
    configured = os.environ.get("INNO_SETUP_PATH")
    if configured:
        candidates.append(Path(configured) / "ISCC.exe")
    candidates.extend([
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ])
    on_path = shutil.which("ISCC.exe")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6 "
        "or set INNO_SETUP_PATH to its installation folder."
    )


def run_test_gate() -> bool:
    """Run the main behavioural suite; True when everything is green.

    The gate runs BEFORE any compiler touches anything, so a broken tree
    can never reach an installer. Output is streamed back as a short tail
    so the failure reason stays visible in the release log.
    """
    print("\n> test gate: test_edge_cases.py")
    # capture as BYTES and decode utf-8 ourselves: the suite prints
    # ✅/❌ and the Thai console's cp874 text=True decoding would explode
    # in a reader thread (masking the real result). PYTHONIOENCODING pins
    # the CHILD's stdout to utf-8 too, so the suite cannot die with
    # UnicodeEncodeError under the gate but pass when run directly.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "test_edge_cases.py"],
        cwd=ROOT, capture_output=True, env=env,
    )
    text = ""
    for blob in (result.stdout, result.stderr):
        try:
            text += (blob or b"").decode("utf-8", errors="replace")
        except Exception:
            pass
    tail = "\n".join(text.strip().splitlines()[-3:])
    print(tail if tail else "(no output)")
    return result.returncode == 0


def build(app_version: str, skip_tests: bool = False) -> Path:
    if skip_tests:
        print("\n⚠ Skipping the test gate (--skip-tests) — building untested code!")
    elif not run_test_gate():
        raise RuntimeError(
            "Test gate failed (test_edge_cases.py is red). Fix the failures "
            "first, or pass --skip-tests to build anyway.")
    run(sys.executable, "build_native_meter.py")
    verify_native_meter()
    run(sys.executable, "build.py")
    verify_bundle()
    run(sys.executable, "create_wizard_images.py")

    run(str(find_iscc()), str(INSTALLER))

    artifact = FINAL_DIR / f"FreQ-Setup-{app_version}.exe"
    if not artifact.is_file():
        raise RuntimeError(f"Installer was not created: {artifact}")
    print(f"\nBuild complete: {artifact}")
    return artifact


def release_notes_section(app_version: str) -> str | None:
    """Extract the RELEASENOTES.md section for this version.

    Section headers look like '## FreQ 2.7.2 SP1 (Stable Pack 1) — 2026-09-23'
    or, in the Ultimate era, '## FreQ Ultimate 2026 (Build 0924.1)'.
    The match compares dash-insensitively so a '2.7.2-SP1' installer version
    finds the '2.7.2 SP1' heading; an Ultimate '2026.0924.1' identifier is
    matched against the branded heading form (and against the bare
    identifier too, in case a heading spells it that way). Returns None
    when the file or a matching section is missing, which makes publish()
    fall back to GitHub's generated notes instead of failing the release.
    """
    notes = ROOT / "RELEASENOTES.md"
    if not notes.is_file():
        return None
    wanted = app_version.replace("-", " ").lower()
    ultimate = re.fullmatch(r"(\d{4})\.(\d{4})\.(\d+)", app_version)
    if ultimate:
        year, mmdd, build_round = ultimate.groups()
        wanted = (
            f"ultimate {year} (build {mmdd}.{build_round}) | "
            f"{year}.{mmdd}.{build_round}"
        )
    for section in re.split(r"(?m)^##\s+", notes.read_text(encoding="utf-8"))[1:]:
        heading = section.splitlines()[0].replace("-", " ").lower()
        if any(part and part in heading for part in wanted.split(" | ")):
            return section.strip()
    return None


def publish(app_version: str, artifact: Path) -> None:
    if shutil.which("git") is None:
        raise RuntimeError("Git is required to publish.")
    if shutil.which("gh") is None:
        raise RuntimeError(
            "GitHub CLI is required for releases. Install it, then run 'gh auth login'."
        )

    tag = f"v{app_version}"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status.strip():
        raise RuntimeError(
            "Working tree has uncommitted files. Commit the release changes first, "
            "then run this command again."
        )

    # Push the CURRENT branch — the repo may not always be on 'main'.
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip() or "main"
    run("git", "push", "origin", branch)

    tag_exists = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if tag_exists:
        raise RuntimeError(f"Tag {tag} already exists; choose a new version first.")

    run("git", "tag", "-a", tag, "-m", f"Release {tag}")
    run("git", "push", "origin", tag)

    notes = release_notes_section(app_version)
    if notes is None:
        print(
            "No RELEASENOTES.md section matches v" + app_version
            + " — falling back to --generate-notes"
        )
        run(
            "gh", "release", "create", tag, str(artifact),
            "--title", f"FreQ {tag}", "--generate-notes",
        )
        return
    notes_file = ROOT / "_release_notes_github.md"
    notes_file.write_text(notes, encoding="utf-8")
    try:
        run(
            "gh", "release", "create", tag, str(artifact),
            "--title", f"FreQ {tag}", "--notes-file", str(notes_file),
        )
    finally:
        notes_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build FreQ and publish it as a GitHub release.\n"
                    "installer.iss is the single source of truth for the "
                    "version — every run starts by syncing the whole source "
                    "tree to it (version.txt, release-notes check).",
        epilog=(
            "examples:\n"
            "  python release.py --set-version 2026.0925.1    bump -> Ultimate 2026 (Build 0925.1)\n"
            "  python release.py --sync-version               re-sync version files from installer.iss, no build\n"
            "  python release.py                              build the installer (current version)\n"
            "  python release.py --publish                    build + commit + tag + GitHub release\n"
            "\n"
            "Publishing needs git + GitHub CLI ('gh auth login') and works\n"
            "best with a RELEASENOTES.md section for the version — the sync\n"
            "stage warns when one is missing (GitHub auto-notes are used)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--set-version",
        metavar="yyyy.mmdd.round | X.Y.Z[-SUFFIX]",
        help="bump MyAppVersion (and Numeric/Display mirrors) in "
             "installer.iss first: Ultimate era 2026.0924.1 = year, Build "
             "month+day, update round; legacy 2.6.4 / 2.7.2-SP1",
    )
    parser.add_argument(
        "--sync-version",
        action="store_true",
        help="rewrite every version file in the tree from installer.iss "
             "and exit (no build) - also runs automatically at the start "
             "of every build",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="commit, push, tag (v<version>) and create the GitHub "
             "release after a successful build",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="skip the test gate (test_edge_cases.py) that runs before "
             "every build — for emergency hotfix builds only",
    )
    args = parser.parse_args()

    if args.set_version:
        set_version(args.set_version)

    # Stage 1 of every run: make the whole tree agree with installer.iss
    # before anything builds or publishes.
    info = sync_version_files()
    if args.sync_version and not args.publish:
        return

    app_version = info["identifier"]
    print(f"\nFreQ release pipeline: v{app_version}")
    artifact = build(app_version, skip_tests=args.skip_tests)
    if args.publish:
        publish(app_version, artifact)
        print(f"\nPublished FreQ {app_version}.")
    else:
        print("\nBuild finished. Use --publish to push and create the GitHub release.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        sys.exit(1)
