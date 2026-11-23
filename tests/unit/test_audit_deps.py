"""Behaviour of scripts/audit_deps.py: clean / vulnerable / unavailable classification."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    path = ROOT / "scripts" / "audit_deps.py"
    spec = importlib.util.spec_from_file_location("audit_deps", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_deps = _load_script()

CLEAN_JSON = json.dumps(
    {"dependencies": [{"name": "requests", "version": "2.32.0", "vulns": []}], "fixes": []}
)
VULN_JSON = json.dumps(
    {
        "dependencies": [
            {"name": "requests", "version": "2.0.0", "vulns": []},
            {
                "name": "badpkg",
                "version": "1.0.0",
                "vulns": [{"id": "PYSEC-0000-1", "fix_versions": ["1.0.1"], "aliases": []}],
            },
        ],
        "fixes": [],
    }
)


class FakeRunner:
    """Stands in for subprocess.run; records argv and returns canned results."""

    def __init__(
        self,
        *,
        export_rc: int = 0,
        audit_rc: int = 0,
        audit_stdout: str = CLEAN_JSON,
        audit_stderr: str = "",
        audit_raises: BaseException | None = None,
    ) -> None:
        self.export_rc = export_rc
        self.audit_rc = audit_rc
        self.audit_stdout = audit_stdout
        self.audit_stderr = audit_stderr
        self.audit_raises = audit_raises
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        if "export" in args:
            return subprocess.CompletedProcess(args, self.export_rc, "", "export failed")
        if self.audit_raises is not None:
            raise self.audit_raises
        return subprocess.CompletedProcess(
            args, self.audit_rc, self.audit_stdout, self.audit_stderr
        )


def test_classify_clean() -> None:
    assert audit_deps.classify(0, CLEAN_JSON) == "clean"


def test_classify_vulnerable() -> None:
    assert audit_deps.classify(1, VULN_JSON) == "vulnerable"


def test_classify_unavailable_on_empty_or_non_json_stdout() -> None:
    assert audit_deps.classify(1, "") == "unavailable"
    assert audit_deps.classify(1, "Traceback: connection refused") == "unavailable"


def test_unavailable_warns_and_passes_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    runner = FakeRunner(audit_rc=1, audit_stdout="", audit_stderr="connection refused")
    assert audit_deps.main([], run=runner) == 0
    err = capsys.readouterr().err
    assert "warning: dependency audit could not complete" in err
    assert "connection refused" in err


def test_unavailable_fails_in_strict_mode() -> None:
    runner = FakeRunner(audit_rc=1, audit_stdout="", audit_stderr="connection refused")
    assert audit_deps.main(["--strict"], run=runner) == 2


@pytest.mark.parametrize("argv", [[], ["--strict"]])
def test_vulnerable_fails_in_both_modes(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner(audit_rc=1, audit_stdout=VULN_JSON)
    assert audit_deps.main(argv, run=runner) == 1
    out = capsys.readouterr().out
    assert "badpkg" in out
    assert "PYSEC-0000-1" in out
    assert "1.0.1" in out


@pytest.mark.parametrize("argv", [[], ["--strict"]])
def test_clean_passes_in_both_modes(argv: list[str]) -> None:
    assert audit_deps.main(argv, run=FakeRunner()) == 0


@pytest.mark.parametrize("argv", [[], ["--strict"]])
def test_failed_export_returns_1_in_both_modes(argv: list[str]) -> None:
    runner = FakeRunner(export_rc=2)
    assert audit_deps.main(argv, run=runner) == 1
    assert len(runner.calls) == 1


def test_missing_pip_audit_binary_counts_as_unavailable() -> None:
    runner = FakeRunner(audit_raises=FileNotFoundError("uv"))
    assert audit_deps.main([], run=runner) == 0
    assert audit_deps.main(["--strict"], run=runner) == 2


def test_argv_passed_to_tools() -> None:
    runner = FakeRunner()
    audit_deps.main([], run=runner)
    export, audit = runner.calls
    for flag in ("--frozen", "--all-groups", "--no-emit-project"):
        assert flag in export
    for flag in ("--no-deps", "--disable-pip", "--strict"):
        assert flag in audit
    assert audit[audit.index("-f") + 1] == "json"


def test_makefile_audit_target_runs_script_with_flags() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    phony = re.search(r"^\.PHONY:((?:.*\\\n)*.*)$", text, re.MULTILINE)
    assert phony is not None
    assert "audit" in phony.group(1).replace("\\", " ").split()
    target = re.search(r"^audit:[^\n]*## \S[^\n]*\n((?:\t[^\n]*\n)+)", text, re.MULTILINE)
    assert target is not None
    assert "scripts/audit_deps.py $(AUDIT_FLAGS)" in target.group(1)
    verify = re.search(r"^verify:([^\n#]*)", text, re.MULTILINE)
    assert verify is not None
    assert "audit" not in verify.group(1).split()
