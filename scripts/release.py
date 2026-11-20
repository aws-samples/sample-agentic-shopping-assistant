#!/usr/bin/env python3
"""Extract an explicit release version. Unreleased baselines do not create tags."""
import re
import argparse
import subprocess
from pathlib import Path


def version(text):
    match = re.search(r"^## \[v?(\d+\.\d+\.\d+)\](?:\s.*)?$", text, re.MULTILINE)
    return f"v{match[1]}" if match else None


def changed_version(current, previous):
    candidate = version(current)
    return candidate if candidate != version(previous) else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-from", help="Tag only if the release heading changed since this Git revision")
    args = parser.parse_args()
    current = Path("CHANGELOG.md").read_text()
    if args.changed_from:
        old = subprocess.run(["git", "show", f"{args.changed_from}:CHANGELOG.md"], capture_output=True, text=True)
        print(changed_version(current, old.stdout if old.returncode == 0 else "") or "")
    else:
        print(version(current) or "")
