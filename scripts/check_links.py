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
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def iter_markdown_files() -> list[Path]:
    """Enumerate git-tracked markdown files.

    Using `git ls-files` (not `rglob`) keeps local pre-commit and CI in
    lockstep: gitignored-but-locally-present files (drafts, scratch, formerly-
    tracked dirs) cannot silently mask broken links that would surface only on
    a fresh clone in CI.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    files: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = REPO_ROOT / raw.decode("utf-8")
        if path.is_file():
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
