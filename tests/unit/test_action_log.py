import json
from pathlib import Path

from agentic_cab.adapters.action_log import JSONLActionLog, redact


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
    assert len(rows) == 2
    assert [r["kind"] for r in rows] == ["requested", "outcome"]


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
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "requested"


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
    assert "555-1234" not in serialised
    assert "Jane Doe" not in serialised
    assert "the office" not in serialised  # raw stripped
    assert "123 Main St" in serialised  # formatted survives
    assert "456 Market St" in serialised
