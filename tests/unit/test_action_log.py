import json
from pathlib import Path

import pytest

from agentic_cab.adapters.action_log import JSONLActionLog, redact
from agentic_cab.core.paths import UnsafePathError

_ULID = "01KS5HMK9QJZ8PCJ92B9VM7BRA"


def test_log_rejects_escaping_session_ids(tmp_path: Path) -> None:
    base = tmp_path / "logs"
    log = JSONLActionLog(log_dir=base)
    absolute = str(tmp_path / "abs")
    for bad in ("../escape", absolute):
        with pytest.raises(UnsafePathError):
            log.emit({"session_id": bad, "kind": "requested"})
        with pytest.raises(UnsafePathError):
            log.read(bad)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["logs"]  # noqa: S101 pytest assertion
    assert list(base.iterdir()) == []  # noqa: S101 pytest assertion


def test_log_valid_ulid_round_trips(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path)
    log.emit({"session_id": _ULID, "kind": "requested"})
    assert log.read(_ULID) == [{"session_id": _ULID, "kind": "requested"}]  # noqa: S101 pytest assertion


def test_log_appends_and_reads(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path)
    sid = "01HEXAMPLE"
    log.emit(
        {
            "ts": "2026-05-19T12:00:00+00:00",
            "session_id": sid,
            "action_id": "A",
            "kind": "requested",
            "tool": "discover_options",
            "args": {},
            "actor": "agent",
        }
    )
    log.emit(
        {
            "ts": "2026-05-19T12:00:01+00:00",
            "session_id": sid,
            "action_id": "A",
            "kind": "outcome",
            "status": "ok",
            "result": {},
            "error": None,
        }
    )
    rows = log.read(sid)
    assert len(rows) == 2  # noqa: S101 pytest assertion
    assert [r["kind"] for r in rows] == ["requested", "outcome"]  # noqa: S101 pytest assertion


def test_log_file_is_appendonly_format(tmp_path: Path) -> None:
    log = JSONLActionLog(log_dir=tmp_path)
    log.emit(
        {
            "ts": "2026-05-19T12:00:00+00:00",
            "session_id": "S",
            "action_id": "A",
            "kind": "requested",
        }
    )
    content = (tmp_path / "S.jsonl").read_text()
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1  # noqa: S101 pytest assertion
    assert json.loads(lines[0])["kind"] == "requested"  # noqa: S101 pytest assertion


def test_redact_leaves_approver_id_and_channel_unchanged() -> None:
    """Server-minted principal ULIDs and the channel enum are not PII; redact() keeps them."""
    from agentic_cab.core.ids import new_principal_id

    for _ in range(50):
        principal_id = new_principal_id()
        confirmation = {
            "approved": True,
            "approver": "web-user",
            "approver_id": principal_id,
            "channel": "web",
        }
        assert redact({"checks": {"confirmation": confirmation}}) == {  # noqa: S101 pytest assertion
            "checks": {"confirmation": confirmation}
        }


def test_redact_strips_phone_and_name_in_address() -> None:
    result = redact(
        {
            "intent": {
                "pickup": {
                    "raw": "Jane Doe's house, 555-1234",
                    "formatted": "123 Main St",
                    "lat": 1.0,
                    "lng": 2.0,
                },
                "dropoff": {
                    "raw": "the office",
                    "formatted": "456 Market St",
                    "lat": 3.0,
                    "lng": 4.0,
                },
            }
        }
    )
    serialised = json.dumps(result)
    assert "555-1234" not in serialised  # noqa: S101 pytest assertion
    assert "Jane Doe" not in serialised  # noqa: S101 pytest assertion
    assert "the office" not in serialised  # raw stripped  # noqa: S101 pytest assertion
    assert "123 Main St" in serialised  # formatted survives  # noqa: S101 pytest assertion
    assert "456 Market St" in serialised  # noqa: S101 pytest assertion
