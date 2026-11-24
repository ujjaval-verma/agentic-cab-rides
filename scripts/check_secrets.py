#!/usr/bin/env python3
"""Scan staged files for accidentally committed secrets.

Complements pre-commit's built-in `detect-private-key` (which catches PEM
blocks). This hook catches token-shaped secrets — API keys for Anthropic,
OpenAI, Stripe, GitHub, AWS, etc.

Each pattern requires enough alphanumeric entropy after a recognizable
prefix that placeholder text like `sk-ant-...` or `sk-ant-<your-key>`
does not match. Real keys are vastly longer than these floors.

If a match is a genuine false positive (e.g. a doc explaining what a key
looks like), append `# noqa: secret-scan` (or `<!-- noqa: secret-scan -->`
in markdown) on the same line. Don't sprinkle these — each one is a
deliberate suppression.

Pre-commit invokes this with the staged filenames as args. Run by hand
with no args to scan every tracked file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Each pattern requires enough trailing chars that ordinary text won't match.
# Tightened where possible to the issuer's actual key shape.
PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic": re.compile(r"sk-ant-[A-Za-z0-9_\-]{30,}"),
    "openai": re.compile(
        r"sk-[A-Za-z0-9]{30,}"
    ),  # also matches Anthropic's older shape; checked second
    "stripe": re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{20,}"),
    "github_pat": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "github_fine_grained": re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "aws_temp_key": re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    "slack_bot": re.compile(r"xox[abprs]-[A-Za-z0-9\-]{20,}"),
    "google_api": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "generic_jwt": re.compile(
        r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\b"
    ),
}

NOQA_RE = re.compile(r"noqa:\s*secret-scan", re.IGNORECASE)

# Binary-ish suffixes we never scan.
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".lock",
    ".whl",
    ".so",
    ".dylib",
}


def staged_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],  # noqa: S607 git resolved from PATH by design for repo hooks
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / p for p in out.stdout.splitlines() if p]


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 git resolved from PATH by design for repo hooks
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return [REPO_ROOT / p for p in out.stdout.splitlines() if p]


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    if path.suffix in SKIP_SUFFIXES:
        return []
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary or unreadable; skip

    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if NOQA_RE.search(line):
            continue
        for name, pat in PATTERNS.items():
            m = pat.search(line)
            if m:
                # Show a short, redacted preview — never echo the full match.
                matched = m.group(0)
                preview = (
                    matched[:10] + "…" + matched[-4:] if len(matched) > 18 else matched[:8] + "…"
                )
                hits.append((lineno, name, preview))
                break  # one hit per line is enough
    return hits


def main(argv: list[str]) -> int:
    args = argv[1:]
    files = [Path(a).resolve() for a in args] if args else (staged_files() or tracked_files())

    any_hit = False
    for f in files:
        for lineno, name, preview in scan_file(f):
            try:
                rel = f.relative_to(REPO_ROOT)
            except ValueError:
                rel = f
            print(f"{rel}:{lineno}: potential {name} secret  ({preview})")
            any_hit = True

    if any_hit:
        print()
        print("If any match is a genuine false positive (e.g. doc example),")
        print("add `# noqa: secret-scan` (or `<!-- noqa: secret-scan -->`) on the same line.")
        print("Otherwise: rotate the key, remove it from the file, and amend the commit.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
