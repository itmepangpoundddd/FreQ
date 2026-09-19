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


def run(*command: str) -> None:
    print("\n> " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def version() -> str:
    match = re.search(
        r'^\s*#define\s+MyAppVersion\s+"([^"]+)"',
        INSTALLER.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise RuntimeError("MyAppVersion is missing from installer.iss")
    return match.group(1)


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


def build(app_version: str) -> Path:
    run(sys.executable, "build_native_meter.py")
    run(sys.executable, "build.py")
    run(sys.executable, "create_wizard_images.py")

    run(str(find_iscc()), str(INSTALLER))

    artifact = FINAL_DIR / f"FreQ-Setup-{app_version}.exe"
    if not artifact.is_file():
        raise RuntimeError(f"Installer was not created: {artifact}")
    print(f"\nBuild complete: {artifact}")
    return artifact


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

    run("git", "push", "origin", "main")

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
    run(
        "gh", "release", "create", tag, str(artifact),
        "--title", f"FreQ {tag}", "--generate-notes",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and publish FreQ")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="commit, push, tag, and create the GitHub release after a successful build",
    )
    args = parser.parse_args()

    app_version = version()
    print(f"FreQ release pipeline: v{app_version}")
    artifact = build(app_version)
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
