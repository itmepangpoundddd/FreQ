#!/usr/bin/env python3
"""Commit and push FreQ source code without publishing a binary release."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def version() -> str:
    installer = ROOT / "installer.iss"
    match = re.search(
        r'^\s*#define\s+MyAppVersion\s+"([^"]+)"',
        installer.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group(1) if match else "unknown"


def stage_source() -> None:
    # Include source and documentation while leaving build outputs, caches,
    # downloaded dependencies, and local radio data outside the commit.
    modified = run(
        "git", "diff", "--name-only", "--diff-filter=ACMRTUXB", capture=True
    ).splitlines()
    modified = [path for path in modified if (ROOT / path).exists()]
    if modified:
        run("git", "add", "--", *modified)
    source_patterns = [
        "*.py", "*.cpp", "*.cc", "*.c", "*.h", "*.hpp",
        "*.iss", "*.md", "*.html", "*.svg", "*.png", "*.ico",
    ]
    def is_ignored(path: str) -> bool:
        return subprocess.run(
            ["git", "check-ignore", "-q", "--", path],
            cwd=ROOT,
            check=False,
        ).returncode == 0

    source_files = [
        path.name
        for pattern in source_patterns
        for path in ROOT.glob(pattern)
        if path.is_file() and not is_ignored(path.name)
    ]
    if (ROOT / "templates").is_dir():
        source_files.append("templates")
    if source_files:
        run("git", "add", "--", *sorted(set(source_files)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Push FreQ source code to GitHub")
    parser.add_argument("-m", "--message", help="commit message")
    parser.add_argument("--dry-run", action="store_true", help="show staged changes without committing")
    args = parser.parse_args()

    branch = run("git", "branch", "--show-current", capture=True)
    if not branch:
        raise RuntimeError("The current checkout is not on a branch")
    remote = run("git", "remote", "get-url", "origin", capture=True)
    if not remote:
        raise RuntimeError("Git remote 'origin' is not configured")

    stage_source()
    staged = run("git", "diff", "--cached", "--name-status", capture=True)
    if not staged:
        print("No source changes to push.")
        return
    print(staged)
    if args.dry_run:
        print("Dry run: nothing was committed or pushed.")
        return

    message = args.message or f"Update source for FreQ v{version()}"
    run("git", "commit", "-m", message)
    run("git", "push", "origin", branch)
    print(f"Source pushed to {remote} ({branch}).")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
