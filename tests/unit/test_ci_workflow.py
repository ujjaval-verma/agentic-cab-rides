"""Structural checks on the CI workflow (least privilege, pinned actions, secret scan)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

PINNED_USES = re.compile(
    r"^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d+(\.\d+){0,2}\s*$"
)


def _load() -> dict[Any, Any]:
    doc: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc


def test_workflow_permissions_are_read_only() -> None:
    doc = _load()
    assert doc.get("permissions") == {"contents": "read"}
    for job in doc["jobs"].values():
        assert "permissions" not in job


def test_every_action_is_pinned_to_a_commit_sha_with_version_comment() -> None:
    lines = [ln for ln in WORKFLOW.read_text(encoding="utf-8").splitlines() if "uses:" in ln]
    assert lines
    for line in lines:
        assert PINNED_USES.match(line), line


def test_secret_scan_job_scans_full_history() -> None:
    job = _load()["jobs"]["secret-scan"]
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts
    assert checkouts[0]["with"]["fetch-depth"] == 0
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert any("gitleaks git" in r and "--redact" in r for r in runs)


def test_every_checkout_drops_persisted_credentials() -> None:
    for job in _load()["jobs"].values():
        checkouts = [
            s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
        ]
        assert checkouts
        for step in checkouts:
            assert step["with"]["persist-credentials"] is False


def test_secret_scan_uses_repo_gitleaks_config() -> None:
    job = _load()["jobs"]["secret-scan"]
    scans = [s["run"] for s in job["steps"] if "gitleaks git" in s.get("run", "")]
    assert scans
    assert all("--config .gitleaks.toml" in r for r in scans)


def test_gitleaks_config_extends_default_with_narrow_allowlists() -> None:
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"] == {"useDefault": True}
    assert "rules" not in config
    allowlists = config["allowlists"]
    assert allowlists
    for allow in allowlists:
        assert allow["targetRules"] == ["generic-api-key"]
        assert allow["condition"] == "AND"
        assert allow["paths"]
        assert allow["regexes"]
        assert "commits" not in allow
        assert "stopwords" not in allow
        for path in allow["paths"]:
            assert path.startswith("^")
            assert not path.startswith("^.*")


def test_security_policy_lives_under_github_dir() -> None:
    policy = ROOT / ".github" / "SECURITY.md"
    assert policy.is_file()
    text = policy.read_text(encoding="utf-8").lower()
    for phrase in ("prototype", "no deployment", "report"):
        assert phrase in text
    assert not (ROOT / "SECURITY.md").exists()


def test_dependency_audit_job_runs_make_audit_strict() -> None:
    job = _load()["jobs"]["dependency-audit"]
    runs = [s["run"] for s in job["steps"] if "run" in s]
    sync = next(i for i, r in enumerate(runs) if "uv sync --frozen" in r)
    assert any("make audit" in r and "--strict" in r for r in runs[sync + 1 :])


def test_triggers_cover_push_to_main_and_pull_requests() -> None:
    doc = _load()
    triggers: dict[str, Any] = doc["on"] if "on" in doc else doc[True]
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" in triggers
