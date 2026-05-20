from agentic_cab.render.transcript import render


def test_render_snapshot(snapshot) -> None:
    chat = [
        {"role": "user", "content": "Book me a ride."},
        {"role": "assistant", "content": [{"type": "text", "text": "Booked: UberX, $15."}]},
    ]
    rows = [
        {
            "action_id": "A",
            "kind": "requested",
            "tool": "confirm_booking",
            "ts": "2026-05-19T12:00:00+00:00",
        },
        {
            "action_id": "A",
            "kind": "verified",
            "result": "ok",
            "ts": "2026-05-19T12:00:01+00:00",
            "checks": {
                "fingerprint_match": True,
                "confirmation": {"approved": True, "approver": "scripted"},
            },
        },
        {
            "action_id": "A",
            "kind": "executed",
            "provider": "uber-sim",
            "method": "confirm",
            "idempotency_key": "K",
            "latency_ms": 0,
            "replayed": False,
            "ts": "2026-05-19T12:00:02+00:00",
        },
        {
            "action_id": "A",
            "kind": "outcome",
            "status": "confirmed",
            "result": {"booking_id": "B1"},
            "ts": "2026-05-19T12:00:03+00:00",
        },
    ]
    invariants = [("four_row_pattern", True, {"tool": "confirm_booking"})]
    md = render(
        scenario_name="01_happy_path",
        seed=42,
        chat=chat,
        action_log=rows,
        invariants=invariants,
    )
    assert md == snapshot
