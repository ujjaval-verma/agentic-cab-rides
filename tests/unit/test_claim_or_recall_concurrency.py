"""claim_or_recall must be atomic — no TOCTOU window between read and insert."""

from __future__ import annotations

import threading
from collections import Counter

from agentic_cab.adapters.state_store import SqliteStateStore


def test_concurrent_claim_one_new_others_in_progress(tmp_path):
    store = SqliteStateStore(tmp_path / "state.db", check_same_thread=False)
    store.apply_schema()
    key = "01JABCDEF0123456789ABCDE"
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait()
            res = store.claim_or_recall(key, scope="confirm")
            results.append(res.status)
        except BaseException as exc:  # capture any thread exception
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"no thread should have crashed; got {errors}"  # noqa: S101 pytest assertion
    counts = Counter(results)
    assert counts["new"] == 1, f"expected exactly one 'new', got {counts}"  # noqa: S101 pytest assertion
    assert counts["in_progress"] == 7, f"expected 7 'in_progress', got {counts}"  # noqa: S101 pytest assertion
