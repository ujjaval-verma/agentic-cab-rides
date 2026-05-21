# Review Follow-ups: Code Fixes, Doc Drift, Presentation Polish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land four high-confidence code fixes, reconcile stale "5 transcripts" copy with the now-shipped 6th transcript, and polish the repo's first-impression surface (README hero, demo GIF, web UI confirm/option cards).

**Architecture:** Three independent phases. Phase A is TDD'd code fixes touching `tools/`, `adapters/state_store.py`, and `adapters/action_log.py`. Phase B is mechanical copy edits across four docs. Phase C is additive: new static assets (logo, GIF, vhs tape script) plus targeted CSS/HTML in the existing `web/static/` tree. No invariants change; no spec updates required.

**Tech Stack:** Python 3.12 + pytest; `vhs` (charmbracelet) for the demo GIF; vanilla HTML/CSS for web UI.

---

## Phase A — Code Fixes (TDD, one commit per task)

### Task A1: `cancel_booking` releases the idempotency claim on user-decline

**Why:** Symmetric to the `confirm_booking` fix in commit `efb5592`. Without this, a declined cancel leaves an `in_progress` row in `idempotency_keys` keyed by `(booking_id, fee_cents, status_at_quote)`. The same booking + same fee + same status will re-derive the same key and short-circuit on "in_progress" forever — declined cancellations become permanently blocked. Pure footgun.

**Files:**
- Modify: `src/agentic_cab/tools/cancel.py` (around line 224)
- Test: `tests/unit/test_cancel_decline_releases_claim.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cancel_decline_releases_claim.py`:

```python
"""Cancel decline must release the idempotency claim so retry is not blocked."""

from __future__ import annotations

import pytest

from agentic_cab.adapters.action_log import JsonlActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.cancel import CancelArgs, cancel_booking
from tests.helpers.confirmation import ScriptedConfirmationChannel
from tests.helpers.bookings import seed_active_booking


@pytest.mark.asyncio
async def test_decline_releases_claim_so_retry_is_not_blocked(tmp_path):
    store = SqliteStateStore(tmp_path / "state.db")
    log = JsonlActionLog(tmp_path / "log.jsonl")
    clock = FrozenClock("2026-05-20T12:00:00+00:00")
    provider = UberSimAdapter(clock=clock, seed=1)
    booking = await seed_active_booking(store, provider, clock)

    decline_then_approve = ScriptedConfirmationChannel(answers=[False, True])

    args = CancelArgs(booking_id=booking.booking_id)
    result1 = await cancel_booking(
        args, provider=provider, store=store, log=log, clock=clock,
        channel=decline_then_approve,
        session_id=new_session_id(), turn_id=new_turn_id(),
    )
    assert result1["status"] == "rejected"

    # Retry with identical intent must NOT short-circuit on in_progress.
    result2 = await cancel_booking(
        args, provider=provider, store=store, log=log, clock=clock,
        channel=decline_then_approve,
        session_id=new_session_id(), turn_id=new_turn_id(),
    )
    assert result2["status"] == "ok", (
        f"expected ok after decline+release, got {result2}"
    )
```

If `tests/helpers/bookings.py` or `seed_active_booking` does not exist, inspect existing cancel tests under `tests/unit/` and `tests/scenarios/` and reuse whatever fixture they use. If the existing `ScriptedConfirmationChannel` lives elsewhere, fix the import.

- [ ] **Step 2: Run test, expect it to fail**

```bash
uv run pytest tests/unit/test_cancel_decline_releases_claim.py -v
```

Expected: FAIL — the second call returns `rejected` or `in_progress`, not `ok`.

- [ ] **Step 3: Apply the fix**

Edit `src/agentic_cab/tools/cancel.py`. Locate the user-decline branch (around line 224):

```python
    if not confirmation.approved:
        _emit_verified(base_checks, "rejected", "user declined")
        return _emit_outcome("rejected", {"reason": "user declined"})
```

Replace with:

```python
    if not confirmation.approved:
        # Release the in_progress claim so a subsequent retry with the same
        # (booking_id, fee_cents, status_at_quote) is not permanently blocked.
        # Symmetric to confirm_booking's release on decline (invariant 5).
        store.release_claim(key, scope="cancel")
        _emit_verified(base_checks, "rejected", "user declined")
        return _emit_outcome("rejected", {"reason": "user declined"})
```

- [ ] **Step 4: Run test, expect it to pass**

```bash
uv run pytest tests/unit/test_cancel_decline_releases_claim.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full fast suite to check for regressions**

```bash
make test-fast
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_cab/tools/cancel.py tests/unit/test_cancel_decline_releases_claim.py
git commit -m "fix(tools/cancel): release in_progress claim on user-decline"
```

---

### Task A2: Atomic `claim_or_recall` (eliminate TOCTOU window)

**Why:** Current impl SELECTs outside the write lock then INSERTs inside. Two concurrent callers can both observe "no row" and one of them then crashes on UNIQUE constraint (unhandled `IntegrityError`). The web path uses `check_same_thread=False`, making the race real. Fix is a single atomic statement: `INSERT OR IGNORE` then read back.

**Files:**
- Modify: `src/agentic_cab/adapters/state_store.py:208-230`
- Test: `tests/unit/test_claim_or_recall_concurrency.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_claim_or_recall_concurrency.py`:

```python
"""claim_or_recall must be atomic — no TOCTOU window between read and insert."""

from __future__ import annotations

import threading
from collections import Counter

from agentic_cab.adapters.state_store import SqliteStateStore


def test_concurrent_claim_one_new_others_in_progress(tmp_path):
    store = SqliteStateStore(tmp_path / "state.db")
    key = "01HXXXXXXXXXXXXXXXXXXXXXXX"
    results: list[str] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        res = store.claim_or_recall(key, scope="confirm")
        results.append(res.status)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(results)
    assert counts["new"] == 1, f"expected exactly one 'new', got {counts}"
    assert counts["in_progress"] == 7, f"expected 7 'in_progress', got {counts}"
    assert sum(counts.values()) == 8, "no thread should have crashed"
```

- [ ] **Step 2: Run test, expect failure**

```bash
uv run pytest tests/unit/test_claim_or_recall_concurrency.py -v
```

Expected: FAIL — either an `IntegrityError` bubbles up, or `counts["new"] > 1`.

- [ ] **Step 3: Apply the fix**

Edit `src/agentic_cab/adapters/state_store.py`. Replace the `claim_or_recall` body (lines 208–230) with:

```python
    def claim_or_recall(self: Self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult:
        # Atomic claim: INSERT OR IGNORE + read-back closes the TOCTOU window
        # between "does the row exist" and "insert if not". Concurrent callers
        # all run this single statement; SQLite serialises writes, exactly one
        # insertion wins, the rest are silent no-ops. Then a single SELECT
        # decides new vs recalled vs in_progress.
        with self._write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys"
                "(key, scope, booking_id, response_blob, created_at)"
                " VALUES (?, ?, NULL, NULL, ?)",
                (key, scope, datetime.now(UTC).isoformat()),
            )
            row = conn.execute(
                "SELECT booking_id, response_blob, created_at FROM idempotency_keys"
                " WHERE key = ?",
                (key,),
            ).fetchone()
        # Row is guaranteed to exist now (we just inserted-or-found it).
        booking_id: str | None = row[0]
        response_blob: str | None = row[1]
        created_at: str = row[2]
        if booking_id is not None and response_blob is not None:
            return ClaimResult(status="recalled", booking_id=booking_id)
        # Distinguish "we just inserted it" from "someone else inserted, still pending".
        # The INSERT OR IGNORE doesn't tell us which happened, but we can derive it
        # by comparing created_at to the value we tried to write (same wall clock
        # is fine — exact equality means we won the race).
        if created_at == self._last_claim_attempt_at(key):
            return ClaimResult(status="new", booking_id=None)
        return ClaimResult(status="in_progress", booking_id=None)
```

The `created_at` comparison is brittle (timestamp collisions are possible). Simpler: use `cursor.rowcount` from the INSERT to know whether the row was new.

Use this instead:

```python
    def claim_or_recall(self: Self, key: str, scope: Literal["confirm", "cancel"]) -> ClaimResult:
        # Atomic claim: INSERT OR IGNORE closes the TOCTOU window. cursor.rowcount
        # reports 1 if this caller's INSERT happened, 0 if a prior row already
        # existed. Then a single SELECT decides recalled vs in_progress for the
        # rowcount==0 case.
        with self._write() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys"
                "(key, scope, booking_id, response_blob, created_at)"
                " VALUES (?, ?, NULL, NULL, ?)",
                (key, scope, datetime.now(UTC).isoformat()),
            )
            if cur.rowcount == 1:
                return ClaimResult(status="new", booking_id=None)
            row = conn.execute(
                "SELECT booking_id, response_blob FROM idempotency_keys WHERE key = ?",
                (key,),
            ).fetchone()
        booking_id: str | None = row[0]
        response_blob: str | None = row[1]
        if booking_id is not None and response_blob is not None:
            return ClaimResult(status="recalled", booking_id=booking_id)
        return ClaimResult(status="in_progress", booking_id=None)
```

Delete the earlier draft block — only this `cursor.rowcount` version stays in the file.

- [ ] **Step 4: Run the new test plus the existing idempotency tests**

```bash
uv run pytest tests/unit/test_claim_or_recall_concurrency.py tests/unit -k "idempot or claim or recall" -v
```

Expected: PASS for new test, no regressions in existing idempotency tests.

- [ ] **Step 5: Run full fast suite**

```bash
make test-fast
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_cab/adapters/state_store.py tests/unit/test_claim_or_recall_concurrency.py
git commit -m "fix(state_store): atomic claim_or_recall via INSERT OR IGNORE + rowcount"
```

---

### Task A3: Inject `Clock` into read-only tools (`track`, `geocode`, `list_active`)

**Why:** Invariant 9 forbids `datetime.now()` outside the `Clock` port. These three tools call `datetime.now(UTC)` directly, breaking `FrozenClock` determinism for the `ts` field in their `requested`/`outcome` log rows. `confirm`/`prepare` already inject `Clock`; this is just consistency.

**Files:**
- Modify: `src/agentic_cab/tools/track.py`
- Modify: `src/agentic_cab/tools/geocode.py`
- Modify: `src/agentic_cab/tools/list_active.py`
- Modify: any caller that constructs these tools (likely `src/agentic_cab/agent/` and `src/agentic_cab/web/app.py` — grep first)
- Test: `tests/unit/test_readonly_tools_use_clock.py` (new)

- [ ] **Step 1: Find all callers**

```bash
grep -rn "track_booking\|geocode_address\|list_active_bookings" src/ tests/ --include="*.py"
```

Note every call site that constructs the tool — those will need the new `clock=` kwarg.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_readonly_tools_use_clock.py`:

```python
"""Read-only tools must use the injected Clock, not wall-clock datetime.now()."""

from __future__ import annotations

import json

import pytest

from agentic_cab.adapters.action_log import JsonlActionLog
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.geocoding.in_memory import InMemoryGeocoder
from agentic_cab.adapters.state_store import SqliteStateStore
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.geocode import GeocodeArgs, geocode_address
from agentic_cab.tools.list_active import ListActiveArgs, list_active_bookings
from agentic_cab.tools.track import TrackArgs, track_booking


FROZEN_TS = "2026-05-20T12:00:00+00:00"


def _read_log_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.asyncio
async def test_geocode_uses_injected_clock(tmp_path):
    log_path = tmp_path / "log.jsonl"
    log = JsonlActionLog(log_path)
    clock = FrozenClock(FROZEN_TS)
    geocoder = InMemoryGeocoder()  # adjust if constructor differs

    await geocode_address(
        GeocodeArgs(query="100 Market St, San Francisco, CA"),
        geocoder=geocoder, log=log, clock=clock,
        session_id=new_session_id(), turn_id=new_turn_id(),
    )

    for row in _read_log_lines(log_path):
        assert row["ts"].startswith("2026-05-20T12:00:00"), row


@pytest.mark.asyncio
async def test_track_uses_injected_clock(tmp_path):
    # Set up booking, then track it and assert log ts.
    ...  # use the same scaffold as test_geocode above

@pytest.mark.asyncio
async def test_list_active_uses_injected_clock(tmp_path):
    ...  # same pattern
```

Fill the `...` blocks by reading the existing `tests/unit/test_track_*` or `test_list_active_*` for fixture conventions.

- [ ] **Step 3: Run test, expect failure**

```bash
uv run pytest tests/unit/test_readonly_tools_use_clock.py -v
```

Expected: FAIL — the test wants a `clock=` kwarg that the tool functions do not yet accept (TypeError), or wall-clock `ts` does not start with `2026-05-20T12:00:00`.

- [ ] **Step 4: Add `clock: Clock` to `track.py`**

Edit `src/agentic_cab/tools/track.py`:

- Remove `from datetime import UTC, datetime`.
- Add `Clock` to the existing `from agentic_cab.core.ports import ActionLog, BookingProvider` import: `from agentic_cab.core.ports import ActionLog, BookingProvider, Clock`.
- Update the signature:

```python
async def track_booking(
    args: TrackArgs,
    *,
    provider: BookingProvider,
    log: ActionLog,
    clock: Clock,
    session_id: str,
    turn_id: str,
) -> dict[str, Any]:
```

- Replace every `datetime.now(UTC).isoformat()` with `clock.now().isoformat()` (three call sites in this file).

- [ ] **Step 5: Apply the same change to `geocode.py` and `list_active.py`**

In `src/agentic_cab/tools/geocode.py`:
- Drop `from datetime import UTC, datetime`.
- Import `Clock`: `from agentic_cab.core.ports import ActionLog, Clock, GeocoderPort`.
- Add `clock: Clock,` to the kwargs in `geocode_address`.
- Replace three `datetime.now(UTC).isoformat()` with `clock.now().isoformat()`.

In `src/agentic_cab/tools/list_active.py`:
- Drop `from datetime import UTC, datetime`.
- Import `Clock`: `from agentic_cab.core.ports import ActionLog, Clock, StateStore`.
- Add `clock: Clock,` to the kwargs in `list_active_bookings`.
- Replace two `datetime.now(UTC).isoformat()` with `clock.now().isoformat()`.

- [ ] **Step 6: Update every call site found in Step 1**

For each call site, add `clock=clock` (or whatever the local clock variable is named — usually `clock` since prepare/confirm/cancel already accept it). The agent's tool dispatcher likely already has a `clock` in scope.

- [ ] **Step 7: Run typecheck and tests**

```bash
uv run pyright src/agentic_cab/tools/track.py src/agentic_cab/tools/geocode.py src/agentic_cab/tools/list_active.py
uv run pytest tests/unit/test_readonly_tools_use_clock.py -v
make test-fast
```

Expected: typecheck clean, new test passes, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/agentic_cab/tools/track.py src/agentic_cab/tools/geocode.py src/agentic_cab/tools/list_active.py tests/unit/test_readonly_tools_use_clock.py src/agentic_cab/agent src/agentic_cab/web
git commit -m "fix(tools): inject Clock into track/geocode/list_active (invariant 9)"
```

---

### Task A4: Redact raw `pickup_address` / `dropoff_address` in `discover_options` logs

**Why:** Invariant 8 says raw address text never enters logs. `discover_options` dumps `args.model_dump()` (which contains `pickup_address` and `dropoff_address` as top-level strings) into the `requested` row's `args` field. The existing `redact()` only matches dict-shaped `pickup`/`dropoff` keys, so these strings leak through unredacted.

**Files:**
- Modify: `src/agentic_cab/adapters/action_log.py` (the `redact` function)
- Test: `tests/unit/test_discover_args_redaction.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_discover_args_redaction.py`:

```python
"""discover_options args must not leak raw pickup_address/dropoff_address."""

from __future__ import annotations

import json

import pytest

from agentic_cab.adapters.action_log import JsonlActionLog, redact
from agentic_cab.adapters.clock import FrozenClock
from agentic_cab.adapters.uber_sim.provider import UberSimAdapter
from agentic_cab.core.ids import new_session_id, new_turn_id
from agentic_cab.tools.discover import DiscoverArgs, discover_options


SECRET_PICKUP = "742 Evergreen Terrace, Springfield"
SECRET_DROPOFF = "31 Spooner Street, Quahog"


def test_redact_strips_pickup_address_and_dropoff_address():
    raw = {
        "args": {
            "pickup_address": SECRET_PICKUP,
            "dropoff_address": SECRET_DROPOFF,
            "pickup_lat": 1.0,
            "pickup_lng": 2.0,
            "dropoff_lat": 3.0,
            "dropoff_lng": 4.0,
            "party_size": 1,
        }
    }
    out = redact(raw)
    serialized = json.dumps(out)
    assert SECRET_PICKUP not in serialized
    assert SECRET_DROPOFF not in serialized
    # lat/lng are not PII — they remain.
    assert out["args"]["pickup_lat"] == 1.0
    assert out["args"]["dropoff_lat"] == 3.0


@pytest.mark.asyncio
async def test_discover_options_does_not_leak_address(tmp_path):
    log_path = tmp_path / "log.jsonl"
    log = JsonlActionLog(log_path)
    clock = FrozenClock("2026-05-20T12:00:00+00:00")
    provider = UberSimAdapter(clock=clock, seed=1)

    await discover_options(
        DiscoverArgs(
            pickup_address=SECRET_PICKUP, pickup_lat=37.78, pickup_lng=-122.41,
            dropoff_address=SECRET_DROPOFF, dropoff_lat=37.79, dropoff_lng=-122.40,
            party_size=1,
        ),
        provider=provider, log=log, clock=clock,
        session_id=new_session_id(), turn_id=new_turn_id(),
    )
    contents = log_path.read_text()
    assert SECRET_PICKUP not in contents
    assert SECRET_DROPOFF not in contents
```

- [ ] **Step 2: Run test, expect failure**

```bash
uv run pytest tests/unit/test_discover_args_redaction.py -v
```

Expected: FAIL — the address strings appear verbatim in both `redact()` output and the JSONL file.

- [ ] **Step 3: Extend `redact()` to cover `*_address` keys**

Edit `src/agentic_cab/adapters/action_log.py`. Locate the `redact()` function (around line 37). The current implementation walks dicts and special-cases keys `"pickup"` / `"dropoff"` when the value is a dict. Add a sibling rule: any key ending in `_address` (or named exactly `pickup_address` / `dropoff_address`) with a string value gets replaced by `"[redacted-address]"`.

Find the elif chain inside the dict-walk in `redact()` and add a new branch alongside the existing `pickup`/`dropoff` dict handler:

```python
            elif k in ("pickup_address", "dropoff_address") and isinstance(v, str):
                out[k] = "[redacted-address]"
```

(Place it before the generic `out[k] = redact(v)` recursive case.)

- [ ] **Step 4: Run test, expect pass**

```bash
uv run pytest tests/unit/test_discover_args_redaction.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full fast suite**

```bash
make test-fast
```

Expected: green. If any existing scenario test asserts on the raw address appearing in the log, it was wrong — update the assertion to `[redacted-address]`.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_cab/adapters/action_log.py tests/unit/test_discover_args_redaction.py
git commit -m "fix(action_log): redact pickup_address/dropoff_address strings (invariant 8)"
```

---

### Task A5: Re-render transcripts (only if Phase A changed log output)

**Why:** Phases A3 and A4 alter log row content. Re-render transcripts so committed artifacts match current behavior.

- [ ] **Step 1: Re-render and diff**

```bash
make transcripts
git diff --stat transcripts/
```

- [ ] **Step 2: If material diffs, commit; if only id-churn, no-op commit**

If diffs:

```bash
git add transcripts/
git commit -m "test(transcripts): re-render after Clock injection + address redaction"
```

If only timestamp/id churn (no semantic change), skip the commit.

---

## Phase B — Reconcile "5 transcripts" copy

The 6th transcript (`06_cross_session_dedup.md`) is committed; four docs still say "5". One commit, four edits.

**Files (all in repo root or `docs/`):**
- `README.md:46` and `README.md:54`
- `CLAUDE.md:43` and `CLAUDE.md:67`
- `transcripts/README.md:16`
- `docs/PHASES.md:158`, `:164`, `:202`

(Plans and specs under `docs/superpowers/` are historical artifacts — leave them alone.)

### Task B1: Update transcript counts in four docs

- [ ] **Step 1: README.md**

Edit `README.md:46`:

```
make transcripts                             # regenerate all 6 scenario transcripts
```

Edit `README.md:54`:

```
- 6 transcripts under `transcripts/`: happy path, price comparison, bad address, surge/edge case, cancellation, cross-session dedup
```

- [ ] **Step 2: CLAUDE.md**

Edit `CLAUDE.md:43`:

```
make transcripts     # Regenerate all 6 transcripts from scenarios/
```

Edit `CLAUDE.md:67`:

```
- Model: `claude-opus-4-7` (or whatever is pinned in `src/agentic_cab/core/constants.py:MODEL_ID`). Do not change without re-rendering all 6 transcripts.
```

While in CLAUDE.md, append one sentence to invariant 5 (around the `claim_or_recall` mention) to record the decline-releases-claim behavior:

> A user-decline at the gate calls `release_claim` (both `confirm` and `cancel`), so a retry with identical intent re-derives the same key but finds it fresh — declines do not become poison pills.

- [ ] **Step 3: transcripts/README.md**

Edit `transcripts/README.md:16`:

```
make transcripts                       # all 6
```

Also add a row to whatever table is in `transcripts/README.md` for `06_cross_session_dedup.md` — read the file first to mirror the existing row format.

- [ ] **Step 4: docs/PHASES.md**

Edit `:158`:

```
**Goal:** all 6 transcripts render with invariants passing.
```

Edit `:164`:

```
- [ ] All 6 transcripts committed to `transcripts/`
```

Edit `:202`:

```
- [ ] All 6 transcripts re-rendered and committed
```

While here, add a one-line "P7 — Cross-session idempotency (shipped)" entry near the bottom of PHASES.md with one acceptance bullet:

```markdown
### P7 — Cross-session intent-bound idempotency

- [x] `idempotency_key` derived from `sha256(intent || generation || optional_nonce)`; survives `draft_id` regeneration and process restart. Scenario `06_cross_session_dedup` proves it.
```

- [ ] **Step 5: Verify links and headings still resolve**

```bash
uv run python scripts/check_links.py
```

Expected: no broken links.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md transcripts/README.md docs/PHASES.md
git commit -m "docs: reconcile transcript count (6 not 5) + record P7 idempotency slice"
```

---

## Phase C — Inviting Presentation

Each task is independent. Tackle in the order listed (ROI-descending). One commit per task.

### Task C1: Italic tagline replacing first paragraph

**Files:** `README.md` (lines 3–5)

- [ ] **Step 1: Replace the dense opening with a one-line hook**

Edit `README.md:1-5`. Current:

```markdown
# Agentic Cab Rides

> An AI agent that books rides end-to-end — built on a **marketplace-agnostic adapter port** designed so that swapping Uber for Lyft is trivial, and swapping for **Zocdoc** (which is not a cab service) requires only a new `Intent` variant and a new adapter. The tool layer, confirmation gate, fingerprint logic, and action log do not change.

The repo's name is `agentic-cab-rides` because the visible deliverable today is booking Uber rides. The architecture is intentionally not: see `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` for the design, and `docs/PHASES.md` for what is shipped vs. planned.
```

Replace with:

```markdown
# Agentic Cab Rides

> *A take-home prototype that books rides end-to-end — structural confirmation gates, fingerprint-verified drafts, and a replayable four-phase action log.*

The repo's name is `agentic-cab-rides` because the visible deliverable today is booking Uber rides — the architecture is intentionally not. The marketplace-agnostic adapter port means swapping Uber for Lyft is trivial; swapping for [Zocdoc](https://zocdoc.com) (not a cab service) requires only a new `Intent` variant and a new adapter. See `docs/superpowers/specs/2026-05-19-agentic-cab-rides-design.md` for the design and `docs/PHASES.md` for what is shipped.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): lead with tagline; defer caveats below the fold"
```

---

### Task C2: Logo SVG placeholder

**Files:** `docs/assets/logo.svg` (new), `README.md`

- [ ] **Step 1: Create `docs/assets/` directory and `logo.svg`**

```bash
mkdir -p docs/assets
```

Write `docs/assets/logo.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 120" width="600" height="120">
  <rect width="600" height="120" rx="16" fill="#1a1a2e"/>
  <text x="40" y="78" font-family="ui-monospace,SFMono-Regular,Menlo,monospace"
        font-size="48" font-weight="700" fill="#e94560">agentic-cab</text>
  <text x="380" y="78" font-family="ui-monospace,SFMono-Regular,Menlo,monospace"
        font-size="48" font-weight="300" fill="#f5f5f5">▸rides</text>
</svg>
```

- [ ] **Step 2: Embed logo at top of README**

Edit `README.md`. Above the `# Agentic Cab Rides` heading, insert:

```markdown
<div align="center">
  <img src="docs/assets/logo.svg" alt="agentic-cab-rides" width="600"/>
</div>

```

- [ ] **Step 3: Commit**

```bash
git add docs/assets/logo.svg README.md
git commit -m "docs(readme): add wordmark SVG"
```

---

### Task C3: Badges row

**Files:** `README.md`

- [ ] **Step 1: Add a badges row directly below the heading**

Edit `README.md`, right below the `# Agentic Cab Rides` heading and tagline blockquote, insert:

```markdown
<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?style=flat-square" alt="Python 3.12"/>
  <img src="https://img.shields.io/badge/uv-managed-purple?style=flat-square" alt="uv"/>
  <img src="https://img.shields.io/badge/pre--push-green-success?style=flat-square" alt="pre-push green"/>
  <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square" alt="MIT license"/>
</p>
```

(If the repo has no LICENSE file, drop the MIT badge or add `LICENSE` separately.)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): add shields.io badges row"
```

---

### Task C4: VHS demo GIF

**Files:** `scripts/demo.tape` (new), `docs/assets/demo.gif` (new, binary), `README.md`

**Pre-req:** `vhs` must be installed. If unavailable, document the manual capture path in the commit message; this is the only task in this plan that can soft-block on local tooling.

- [ ] **Step 1: Write the vhs tape script**

Create `scripts/demo.tape`:

```
# vhs tape for agentic-cab-rides demo GIF
# Run:  vhs scripts/demo.tape  →  produces docs/assets/demo.gif

Output docs/assets/demo.gif

Set Shell "zsh"
Set FontSize 14
Set Width 1100
Set Height 640
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 30ms

# Boot into the REPL agent against the in-memory provider so it's deterministic.
Type "make repl"
Enter
Sleep 1500ms

Type "I need a ride from 100 Market St San Francisco to SFO around 6pm tonight"
Enter
Sleep 6s

Type "yes"
Enter
Sleep 4s

Type "/quit"
Enter
Sleep 800ms
```

- [ ] **Step 2: Generate the GIF**

```bash
vhs scripts/demo.tape
ls -lh docs/assets/demo.gif
```

If `vhs` is not on PATH:

```bash
brew install vhs   # macOS
# or: see https://github.com/charmbracelet/vhs
```

If recording fails because `make repl` requires an API key the recorder doesn't have, run the demo against a `--mock` flag if one exists, or capture against a scripted scenario instead:

```bash
# Alternative: capture a scenario run
```

Replace the `make repl` line with `uv run python -m agentic_cab run-scenario 01_happy_path`. The output is less interactive but more reliable.

- [ ] **Step 3: Embed in README**

Edit `README.md` — insert below the badges row (before "What this is"):

```markdown
<div align="center">
  <img src="docs/assets/demo.gif" alt="Demo: booking a ride end-to-end" width="900"/>
</div>
```

- [ ] **Step 4: Commit**

```bash
git add scripts/demo.tape docs/assets/demo.gif README.md
git commit -m "docs(readme): vhs demo GIF + reusable tape script"
```

---

### Task C5: Collapsible architecture diagram in README

**Files:** `README.md`

- [ ] **Step 1: Add a `<details>` block above "Where things live"**

Edit `README.md`, insert a new section between "Stack & non-negotiables" and "Where things live":

```markdown
## Architecture

<details>
<summary>Layer diagram (click to expand)</summary>

```
┌──────────────────────────────────────────────────────────┐
│ CLI (make repl)     │     Web (make web, FastAPI)        │
├──────────────────────────────────────────────────────────┤
│                       Agent (Anthropic SDK, native tools)│
├──────────────────────────────────────────────────────────┤
│  Tools: discover · prepare · confirm · cancel · track    │
│         geocode · list_active   (confirmation gate here) │
├──────────────────────────────────────────────────────────┤
│  Ports: BookingProvider · GeocoderPort · StateStore      │
│         ActionLog · Clock · ConfirmationChannel          │
├──────────────────────────────────────────────────────────┤
│  Adapters: UberSim · MockAppointment · Nominatim         │
│            SqliteStateStore · JsonlActionLog · …         │
└──────────────────────────────────────────────────────────┘
```

Dependency direction is strictly downward. The agent never sees an adapter.
Full diagram: `docs/ARCHITECTURE.md`.

</details>
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): collapsible architecture diagram"
```

---

### Task C6: Web UI — confirm card polish

**Why:** The modal `.summary` div is a flat text block. A two-line ride-card layout with a vertical connector dot reads as intentional. Add a copy button next to a truncated fingerprint to surface "tamper-evident" as a feature, not debug noise.

**Files:** `src/agentic_cab/web/static/index.html` (or wherever the confirm modal markup lives), `src/agentic_cab/web/static/style.css`

- [ ] **Step 1: Inspect current modal markup**

```bash
ls src/agentic_cab/web/static/
grep -rn "summary\|fingerprint\|approve" src/agentic_cab/web/static/
```

Identify the exact element ids/classes used for the summary block and approve/decline buttons.

- [ ] **Step 2: Update the summary template**

The exact diff depends on whether the summary is server-rendered or built client-side in JS. The shape to land:

```html
<div class="ride-card">
  <div class="ride-route">
    <span class="dot dot-pickup"></span>
    <span class="addr addr-pickup">{{ pickup }}</span>
  </div>
  <div class="ride-connector"></div>
  <div class="ride-route">
    <span class="dot dot-dropoff"></span>
    <span class="addr addr-dropoff">{{ dropoff }}</span>
  </div>
  <div class="ride-meta">
    <span class="ride-price">${{ price }}</span>
    <span class="ride-eta">ETA {{ eta }} min</span>
  </div>
  <div class="ride-fingerprint" title="Fingerprint of the option snapshot — re-checked at confirm time to detect tampering">
    fp <code>{{ fingerprint_short }}</code>
    <button class="copy-btn" data-copy="{{ fingerprint_full }}">copy</button>
  </div>
</div>
```

Wire a tiny JS handler for `.copy-btn` if there isn't already one — `navigator.clipboard.writeText(e.target.dataset.copy)`.

- [ ] **Step 3: Add the CSS**

Append to `src/agentic_cab/web/static/style.css`:

```css
.ride-card { display: flex; flex-direction: column; gap: 8px; padding: 16px;
  background: #f7f8fc; border-radius: 12px; border: 1px solid #e5e7f0; }
.ride-route { display: flex; align-items: center; gap: 10px; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.dot-pickup { background: #6366f1; }
.dot-dropoff { background: #e94560; }
.ride-connector { width: 2px; height: 18px; background: #c7cae0; margin-left: 4px; }
.addr { font-size: 14px; color: #1a1a2e; }
.ride-meta { display: flex; gap: 16px; margin-top: 8px; font-size: 13px; color: #4b4f6e; }
.ride-price { font-weight: 600; color: #1a1a2e; }
.ride-fingerprint { font-size: 11px; color: #6b7090; margin-top: 4px;
  display: flex; align-items: center; gap: 6px; }
.ride-fingerprint code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.copy-btn { background: transparent; border: 1px solid #c7cae0; border-radius: 4px;
  padding: 2px 6px; font-size: 11px; cursor: pointer; color: #4b4f6e; }
.copy-btn:hover { background: #eef0f8; }

/* Promote approve to indigo so it reads as intentional, not default. */
.btn-approve { background: #6366f1; color: white; }
.btn-approve:hover { background: #4f46e5; }
```

- [ ] **Step 4: Manual verify**

```bash
make web
```

Open `http://localhost:8000`, drive a happy-path booking, eyeball the confirm modal. Click the copy button.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_cab/web/static/
git commit -m "feat(web): polished confirm card with route layout + fingerprint copy"
```

---

### Task C7: Web UI — option-comparison card strip

**Why:** When the agent returns multiple options, they currently render as prose. A scannable three-column card strip (class · ETA · price) makes price comparison instinctive.

**Files:** `src/agentic_cab/web/static/index.html`, `src/agentic_cab/web/static/app.js` (or wherever the chat rendering lives), `src/agentic_cab/web/static/style.css`

- [ ] **Step 1: Find the message-rendering code**

```bash
grep -rn "options\|render.*message\|chat.*message" src/agentic_cab/web/static/
```

Identify where agent messages are inserted into the DOM and where the options payload would be available (likely an SSE handler in `sse.py` or a fetch handler in JS).

- [ ] **Step 2: Detect option-list shape and render as cards**

In the JS that renders agent messages, add a branch:

```js
function renderOptions(options, container) {
  const strip = document.createElement('div');
  strip.className = 'option-strip';
  for (const o of options) {
    const card = document.createElement('div');
    card.className = 'option-card';
    card.innerHTML = `
      <div class="opt-class">${o.vehicle_class}</div>
      <div class="opt-eta">${o.eta_minutes} min</div>
      <div class="opt-price">$${(o.price.amount_cents / 100).toFixed(2)}</div>
    `;
    strip.appendChild(card);
  }
  container.appendChild(strip);
}
```

Detect the shape by `typeof msg.options === 'object' && Array.isArray(msg.options)` and call `renderOptions` before the prose text (or instead of it).

- [ ] **Step 3: CSS**

Append to `style.css`:

```css
.option-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px; margin: 8px 0; }
.option-card { padding: 12px; background: white; border: 1px solid #e5e7f0;
  border-radius: 10px; text-align: center; }
.option-card:hover { border-color: #6366f1; cursor: pointer; }
.opt-class { font-weight: 600; color: #1a1a2e; }
.opt-eta { font-size: 12px; color: #6b7090; margin-top: 2px; }
.opt-price { font-size: 18px; font-weight: 700; color: #6366f1; margin-top: 6px; }
```

- [ ] **Step 4: Manual verify with the price-comparison scenario**

```bash
make web
```

Drive the price-comparison flow ("compare options"); confirm cards appear.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_cab/web/static/
git commit -m "feat(web): scannable option-comparison card strip"
```

---

### Task C8: Web UI — typography micro-fix

**Files:** `src/agentic_cab/web/static/style.css`

- [ ] **Step 1: Bump body font and line-height**

Append to `style.css`:

```css
body { font-size: 15px; }
.msg.agent { line-height: 1.6; }
```

(Adjust selectors to match the existing markup — read the file first.)

- [ ] **Step 2: Commit**

```bash
git add src/agentic_cab/web/static/style.css
git commit -m "style(web): bump body font + agent message line-height for readability"
```

---

### Task C9: `transcripts/README.md` index polish

**Files:** `transcripts/README.md`

- [ ] **Step 1: Add a scannable scenario table**

Read the current `transcripts/README.md`. If it does not already have a scenarios table, add one near the top:

```markdown
## Scenarios

| #  | Scenario | What it proves |
|----|----------|----------------|
| 01 | Happy path (`01_happy_path.md`) | discover → prepare → confirm → outcome |
| 02 | Price comparison (`02_price_comparison.md`) | multiple options, agent picks cheapest |
| 03 | Bad address (`03_bad_address.md`) | geocoder confidence + clarify loop |
| 04 | Surge (`04_surge.md`) | re-quote at confirm, price-changed handling |
| 05 | Cancellation (`05_cancellation.md`) | gated cancel with fee disclosure |
| 06 | Cross-session dedup (`06_cross_session_dedup.md`) | intent-bound idempotency across restarts |
```

If the table already exists, just add the row for `06`.

- [ ] **Step 2: Commit**

```bash
git add transcripts/README.md
git commit -m "docs(transcripts): scenarios table with 6 entries"
```

---

## Phase D — Verify and Wrap

### Task D1: Full verification

- [ ] **Step 1: Run the pre-push gate**

```bash
make verify
```

Expected: all green (fmt-check, lint, typecheck, test-fast).

- [ ] **Step 2: Run the full suite including scenarios**

```bash
make test
```

Expected: green.

- [ ] **Step 3: Render fresh and check for unexpected diff**

```bash
make transcripts
git status transcripts/
```

If diffs appear that weren't already committed by Task A5, investigate before committing.

- [ ] **Step 4: Final commit if anything dangling**

```bash
git add -A
git status
```

If working tree is clean, done. Otherwise commit any straggler.

---

## Self-Review Notes

- **Spec coverage:** Plan covers the four code findings (A1–A4), the doc reconciliation (B1), and seven presentation tasks (C1–C9 inclusive). Phase A5 + D1 close the verification loop.
- **Types consistency:** `Clock` is the existing port in `core/ports.py` already imported by `confirm`/`prepare`; the new `clock=` kwargs in A3 reuse that exact name. `release_claim(key, scope=...)` already exists in `state_store.py` and is called by `confirm_booking` — A1 reuses the same signature. `redact()` in `action_log.py` already exists; A4 only adds a new branch.
- **Placeholders:** Two `...` spots in the A3 test scaffold are explicit "fill from existing fixture" instructions, not "TBD" placeholders — the engineer is told exactly where to look for the convention. C6 step 2's `{{ pickup }}` style is template syntax for whichever templating the web layer uses; the engineer is instructed to inspect the existing markup first.
- **Effort estimates** (for execution planning):
  - Phase A: ~3 hrs total (A1: 30 min · A2: 45 min · A3: 1 hr · A4: 30 min · A5: 15 min)
  - Phase B: 30 min
  - Phase C: ~4.5 hrs total (C1+C2+C3+C5+C8+C9: 15 min each · C4 vhs GIF: 1 hr · C6 confirm card: 1 hr · C7 option strip: 1 hr)
  - Phase D: 15 min
