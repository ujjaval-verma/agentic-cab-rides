#!/usr/bin/env python3
"""Verify every relative markdown link resolves to an existing file or directory.

External URLs (http/https/mailto) and pure anchors (#section) are skipped —
they're not the failure mode this hook catches. The failure this catches is
forward references to files that don't exist yet, or stale links after a
rename. Both are silent until a grader (or a reader) clicks them.

Runs in <50ms across the full doc set. No network, no third-party deps.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Markdown files anywhere in the repo, except generated/cached/scratch.
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".pyright",
    "dist",
    "build",
    "data",
    "logs",
    "chat",
}
# Scratch READMEs are checked; everything else under docs/scratch/ is gitignored
# and not committed, so it won't be walked anyway.

LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def iter_markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.md"):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return files


def check_file(path: Path) -> list[tuple[Path, str, str]]:
    text = path.read_text(encoding="utf-8")
    broken: list[tuple[Path, str, str]] = []
    for match in LINK_RE.finditer(text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_clean = target.split("#", 1)[0]
        if not target_clean:
            continue
        resolved = (path.parent / target_clean).resolve()
        if not resolved.exists():
            broken.append((path, target, str(resolved)))
    return broken


def main() -> int:
    all_broken: list[tuple[Path, str, str]] = []
    for f in iter_markdown_files():
        all_broken.extend(check_file(f))

    if not all_broken:
        return 0

    print("Broken relative links found:")
    for path, target, resolved in all_broken:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}  ->  {target}")
        print(f"      (resolved: {resolved})")
    print()
    print("Fix: ship a stub at the target path, or remove/rewrite the link.")
    print("See CLAUDE.md § 'Documentation: durable vs transient'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
