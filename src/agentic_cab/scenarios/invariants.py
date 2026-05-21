"""Pure functions over an action-log row list. See spec §11."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _by_action(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["action_id"], []).append(r)
    return groups


def check_action_log_contains(rows: list[dict[str, Any]], where: dict[str, Any]) -> bool:
    return any(all(r.get(k) == v for k, v in where.items()) for r in rows)


def check_four_row_pattern(rows: list[dict[str, Any]], tool: str) -> bool:
    """For every action_id whose requested.tool == tool and verified.result == 'ok',
    the rows must be exactly [requested, verified, executed, outcome] in order.
    Rejected paths are allowed any subset ending in outcome (no executed required)."""
    for group in _by_action(rows).values():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver and ver.get("result") == "ok":
            kinds = [r["kind"] for r in group]
            if kinds != ["requested", "verified", "executed", "outcome"]:
                return False
    return True


def check_no_double_booking(rows: list[dict[str, Any]]) -> bool:
    """For each idempotency_key, at most one NON-replayed executed row."""
    seen: dict[str, int] = {}
    for r in rows:
        if r["kind"] != "executed":
            continue
        if r.get("replayed") is True:
            continue
        key = r.get("idempotency_key", "")
        seen[key] = seen.get(key, 0) + 1
    return all(c <= 1 for c in seen.values())


def check_fingerprint_verified_before_execute(rows: list[dict[str, Any]], tool: str) -> bool:
    for group in _by_action(rows).values():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        exe = next((r for r in group if r["kind"] == "executed"), None)
        if exe is None:
            continue  # rejected; not in scope for this check
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None or not ver.get("checks", {}).get("fingerprint_match"):
            return False
    return True


def check_gate_invoked_for_tool(rows: list[dict[str, Any]], tool: str) -> bool:
    for group in _by_action(rows).values():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver is None:
            return False
        conf = ver.get("checks", {}).get("confirmation")
        if conf is None or "approver" not in conf:
            return False
    return True


def check_price_changed_rejected(rows: list[dict[str, Any]], tool: str) -> bool:
    """Assert that at least one action for `tool` has verified.result=='rejected'
    and verified.reason=='price_changed', and outcome.status=='price_changed'."""
    for group in _by_action(rows).values():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        ver = next((r for r in group if r["kind"] == "verified"), None)
        if ver and ver.get("result") == "rejected" and ver.get("reason") == "price_changed":
            out = next((r for r in group if r["kind"] == "outcome"), None)
            if out and out.get("status") == "price_changed":
                return True
    return False


def check_no_executed_after_price_changed(rows: list[dict[str, Any]], tool: str) -> bool:
    """For every action_id of `tool` whose outcome.status=='price_changed',
    assert there is no executed row (invariant 6: rejected → no executed)."""
    for group in _by_action(rows).values():
        req = next((r for r in group if r["kind"] == "requested"), None)
        if not req or req.get("tool") != tool:
            continue
        out = next((r for r in group if r["kind"] == "outcome"), None)
        if out and out.get("status") == "price_changed":
            exe = next((r for r in group if r["kind"] == "executed"), None)
            if exe is not None:
                return False  # executed row present after price_changed → invariant violated
    return True


def check_provider_confirm_call_count(rows: list[dict[str, Any]], tool: str, count: int) -> bool:
    """Assert the number of provider.confirm calls (executed rows for `tool`) equals `count`.

    Replayed executed rows (replayed=True) are NOT counted — they reflect a recall, not
    a provider call. This is the load-bearing invariant for cross-session dedup:
    two confirm attempts with the same intent should produce exactly one provider call.
    """
    n = sum(
        1
        for r in rows
        if r.get("kind") == "executed" and r.get("tool") == tool and not r.get("replayed", False)
    )
    return n == count


def check_outcome_present(rows: list[dict[str, Any]], tool: str, status: str) -> bool:
    """Assert there is at least one outcome row for `tool` with the given status."""
    return any(
        r.get("kind") == "outcome" and r.get("tool") == tool and r.get("status") == status
        for r in rows
    )


CHECKERS: dict[str, Any] = {
    "action_log_contains": check_action_log_contains,
    "four_row_pattern": check_four_row_pattern,
    "no_double_booking": check_no_double_booking,
    "fingerprint_verified_before_execute": check_fingerprint_verified_before_execute,
    "gate_invoked_for_tool": check_gate_invoked_for_tool,
    "price_changed_rejected": check_price_changed_rejected,
    "no_executed_after_price_changed": check_no_executed_after_price_changed,
    "provider_confirm_call_count": check_provider_confirm_call_count,
    "outcome_present": check_outcome_present,
}


def evaluate(
    rows: list[dict[str, Any]],
    invariants: Iterable[dict[str, Any]],
) -> list[tuple[str, bool, dict[str, Any]]]:
    """Return list of (kind, passed, params_used) for transcript rendering."""
    out: list[tuple[str, bool, dict[str, Any]]] = []
    for inv in invariants:
        kind = inv["kind"]
        fn = CHECKERS[kind]
        params = {k: v for k, v in inv.items() if k != "kind"}
        try:
            passed = bool(fn(rows, **params))
        except Exception:  # invariant evaluation failure is a "fail" signal
            passed = False
        out.append((kind, passed, params))
    return out
