"""For any action_id where verified.result == 'rejected', no executed row may exist.

This invariant guards against regressions in the confirm_booking implementation —
the short-circuit must be structural, not merely conventional.
"""

import json
from pathlib import Path

import pytest


@pytest.fixture
def fixture_rows() -> list[dict]:
    """Synthetic mixed-outcome log covering both pass and fail action_ids."""
    return [
        # action A: rejected (fingerprint mismatch) — no executed row
        {"action_id": "A", "kind": "requested", "tool": "confirm_booking"},
        {
            "action_id": "A",
            "kind": "verified",
            "result": "rejected",
            "reason": "fingerprint mismatch",
        },
        {"action_id": "A", "kind": "outcome", "status": "rejected"},
        # action B: ok
        {"action_id": "B", "kind": "requested", "tool": "confirm_booking"},
        {
            "action_id": "B",
            "kind": "verified",
            "result": "ok",
            "checks": {"fingerprint_match": True},
        },
        {"action_id": "B", "kind": "executed", "idempotency_key": "K", "replayed": False},
        {"action_id": "B", "kind": "outcome", "status": "confirmed"},
    ]


def _assert_integrity(rows: list[dict]) -> None:
    by_action: dict[str, list[dict]] = {}
    for r in rows:
        by_action.setdefault(r.get("action_id", ""), []).append(r)
    for action_id, group in by_action.items():
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None or ver.get("result") != "rejected":
            continue
        exe = [r for r in group if r["kind"] == "executed"]
        assert not exe, (
            f"INVARIANT VIOLATED: action_id={action_id} has verified.result=='rejected' "
            f"AND {len(exe)} executed row(s); short-circuit failed"
        )


def test_integrity_holds_on_fixture(fixture_rows: list[dict]) -> None:
    _assert_integrity(fixture_rows)


def test_integrity_catches_violation() -> None:
    bad = [
        {"action_id": "X", "kind": "verified", "result": "rejected"},
        {"action_id": "X", "kind": "executed"},
    ]
    with pytest.raises(AssertionError, match="INVARIANT VIOLATED"):
        _assert_integrity(bad)


def test_integrity_holds_on_rendered_transcript_logs() -> None:
    """Walk every JSONL log under logs/ (if present) and assert integrity.
    Once `make transcripts` has run, this test guards real output against
    regressions. Skips gracefully if logs/ is empty (CI without API key)."""
    log_dir = Path("logs")
    if not log_dir.exists():
        pytest.skip("no logs/ dir — run `make transcripts` to populate")
    found = False
    for p in log_dir.glob("*.jsonl"):
        if p.name.endswith(".chat.jsonl"):
            continue
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        _assert_integrity(rows)
        found = True
    if not found:
        pytest.skip("no session logs found")
