#!/usr/bin/env python3
"""Audit the locked dependency set (uv.lock, all groups) with pip-audit.

Outcomes:
  clean        no known vulnerabilities                       -> exit 0
  vulnerable   at least one advisory against a locked package -> exit 1 (always)
  unavailable  the audit could not complete (network, OSV/PyPI outage, tool
               missing)                                       -> warn, exit 0;
               with --strict                                  -> exit 2

pip-audit exits 1 both for findings and for fatal errors, so the result is
classified by parsing its JSON report rather than by exit code alone.

Two different "strict" flags exist: this script's `--strict` makes an
unavailable audit fail, while pip-audit's own `--strict` (always passed here)
makes it fail on skipped dependencies so none are silently ignored.

The exported requirements file lives in a temp directory outside the tree and
is never committed. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent

Outcome = Literal["clean", "vulnerable", "unavailable"]
Runner = Callable[..., "subprocess.CompletedProcess[str]"]

EXIT_OK = 0
EXIT_VULNERABLE = 1
EXIT_UNAVAILABLE_STRICT = 2
STDERR_TAIL_LINES = 10


def _parse_report(stdout: str) -> list[dict[str, Any]] | None:
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(report, dict):
        return None
    deps = report.get("dependencies")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(deps, list):
        return None
    return [d for d in deps if isinstance(d, dict)]  # pyright: ignore[reportUnknownVariableType]


def _vulnerable_deps(deps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in deps if d.get("vulns")]


def classify(returncode: int, stdout: str) -> Outcome:
    deps = _parse_report(stdout)
    if deps is None:
        return "unavailable"
    if _vulnerable_deps(deps):
        return "vulnerable"
    return "clean" if returncode == 0 else "unavailable"


def _report_findings(stdout: str) -> None:
    for dep in _vulnerable_deps(_parse_report(stdout) or []):
        for vuln in dep["vulns"]:
            fixes = ", ".join(vuln.get("fix_versions") or []) or "none"
            print(f"{dep.get('name')} {dep.get('version')} {vuln.get('id')} fix: {fixes}")


def _unavailable(detail: str, *, strict: bool) -> int:
    tail = "\n".join(detail.strip().splitlines()[-STDERR_TAIL_LINES:])
    print("warning: dependency audit could not complete", file=sys.stderr)
    if tail:
        print(tail, file=sys.stderr)
    return EXIT_UNAVAILABLE_STRICT if strict else EXIT_OK


def main(argv: list[str] | None = None, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail (exit 2) when the audit cannot complete, e.g. offline",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        export_cmd = [
            "uv",
            "export",
            "--frozen",
            "--all-groups",
            "--no-hashes",
            "--no-emit-project",
            "-o",
            str(requirements),
        ]
        exported = run(export_cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        if exported.returncode != 0:
            print(f"error: uv export failed\n{exported.stderr}", file=sys.stderr)
            return EXIT_VULNERABLE

        audit_cmd = [
            "uv",
            "run",
            "--frozen",
            "pip-audit",
            "--disable-pip",
            "--no-deps",
            "--strict",
            "--progress-spinner",
            "off",
            "-f",
            "json",
            "-r",
            str(requirements),
        ]
        try:
            audited = run(audit_cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        except OSError as exc:
            return _unavailable(str(exc), strict=args.strict)

    outcome = classify(audited.returncode, audited.stdout)
    if outcome == "vulnerable":
        print("Known vulnerabilities found in locked dependencies:")
        _report_findings(audited.stdout)
        return EXIT_VULNERABLE
    if outcome == "unavailable":
        return _unavailable(audited.stderr, strict=args.strict)
    print("no known vulnerabilities")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
