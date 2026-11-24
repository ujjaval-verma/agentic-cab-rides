# Plan: per-subscriber SSE queues, bounded bus, generic errors (#26)

This fits in 7 tasks. It needs no change to a gate, hook or invariant. The confirmation channel gets two additions, and both are delivery-only: it replays prompts that are still pending to a new subscriber, and it broadcasts when a prompt is resolved. The y/n decision path (`request` / `resolve`) and the approver logic are unchanged.

One criterion is already met. `/events` and `/sessions/{id}/log` already require the session cookie. #24 added that through `_authorize`, and the `_AUTH_ROUTES` tests in `tests/unit/test_web_app_routes.py` already cover it. This plan adds one regression test (Task 2): a refused `/events` request never touches the bus.

## Goal

Four problems in the web surface today:

- **One queue per session.** [`src/agentic_cab/web/sse.py`](../../src/agentic_cab/web/sse.py) keeps a single `asyncio.Queue` per `session_id`. Two `EventSource` connections on one session compete for items on that queue, so each event reaches only one of them. This includes `pending_confirmation`, which is how the browser learns about the gate. It happens with two tabs, and also when a reconnect overlaps a connection the server has not yet noticed is dead.
- **The bus map never shrinks.** `publish()` calls `_get_or_create`, so any publish creates an entry and nothing ever removes one. Callers include the background chat task after its session is evicted, and the startup flush for sessions from before a restart.
- **Exception text reaches the browser.** The background turn in [`src/agentic_cab/web/app.py`](../../src/agentic_cab/web/app.py) publishes `{"kind": "error", "message": str(exc)}`. The confirmation 404 echoes the raw `subject_id` in its `detail`. There is no handler for unhandled route exceptions.
- **No correlation id.** A user cannot point an operator at the matching log line.

The fix:

- Each subscriber gets its own bounded queue, and `publish` fans out to all of them.
- A bus entry exists only while it has at least one subscriber. It is removed when the last subscriber disconnects or when the session is evicted.
- Every error the browser sees carries a fixed message and a ULID correlation id. The detail goes to structlog only.

## Acceptance criteria

- Each subscriber gets its own queue; two tabs on one session both receive every event.
- `/events` and `/sessions/{id}/log` require the session token from S6.
- Bus queues are removed when the last subscriber disconnects or the session is evicted; a test shows unknown session ids don't grow memory.
- Errors return a generic message plus a correlation id; detail goes to structlog only.

## Design notes

**What "the session token from S6" means.** It is the `cab_session_<session_id>` HttpOnly cookie that `POST /sessions` mints. `_authorize` checks it with `hmac.compare_digest` (see [ARCHITECTURE "Web session authentication"](../ARCHITECTURE.md#web-session-authentication)). `EventSource` sends it automatically on same-origin requests, and [`app.js`](../../src/agentic_cab/web/static/app.js) already relies on that. No client change is needed for auth.

**Bus shape (`web/sse.py`).**
- `SessionEventBus._subs: dict[str, list[Subscription]]`.
- `bus.open(session_id) -> Subscription` is synchronous. It registers the subscription immediately, so code can take a snapshot after it with no gap.
- `Subscription` is a context manager and an async iterator. `__exit__` calls `bus.close(sub)`. Iteration ends when it receives a private `_CLOSED` sentinel.
- `bus.close(sub)` is idempotent. It removes the subscription and deletes the session key once the list is empty.
- `bus.close_session(session_id)` closes every subscription on that session and deletes the key.
- `session_count()` and `subscriber_count(session_id)` are public read-only accessors, used by tests and for observability.

**`publish` never creates an entry.** With no subscribers it does nothing. It iterates over a `tuple(...)` snapshot of the list, because an overflow can close a subscriber partway through.

**Concurrency.** Everything runs on the single uvicorn event loop. `publish`, `open` and `close` are synchronous and never `await`, so check-then-act cannot interleave. No tool publishes from a thread: the grep for `to_thread` and `run_in_executor` finds no use in `src/`.

**Bounds and overflow.**
- Each queue holds at most `_QUEUE_MAX = 256` rows.
- **On overflow, the slow subscriber is disconnected.** The bus drains its queue, enqueues `_CLOSED`, removes it, and logs `web.sse.subscriber_dropped` with `reason="overflow"`. Rows are no longer silently dropped: dropping the oldest row could silently lose a `pending_confirmation`.
- When the stream ends, the browser's `EventSource` reconnects on its own, re-authorizes, and gets the pending prompts replayed (see the next section). The JSONL log stays the durable record.
- Each session allows at most `_MAX_SUBSCRIBERS_PER_SESSION = 8` subscribers. Opening a ninth closes the oldest one (`reason="superseded"`). This evicts zombie connections, so they cannot block a real reconnect.
- Total bus memory is therefore at most live sessions × 8 × 256 rows. Bus keys are always a subset of `_sessions`.

**Pending replay keeps the gate reachable.** Today a prompt published while no stream is attached waits in the shared queue. With per-subscriber queues and drop-when-no-subscriber, a prompt published during a reconnect gap would be lost, and the human would hit a timeout. A timeout is a safe denial, but it is a regression.

The fix:
- `WebConfirmationChannel` keeps the event dict it published for each pending `subject_id`.
- A new method, `pending_events()`, returns copies of those dicts.
- The `/events` stream opens its subscription, takes the `pending_events()` snapshot, yields it, and then iterates the subscription. Both steps are synchronous with no `await` between them, so no prompt is missed or duplicated.
- Only the session's own channel is read, so a non-owner can never receive a prompt.

**Resolved broadcast.** `request()`'s `finally` publishes `{"kind": "confirmation_resolved", "subject_id": ...}` on approve, decline and timeout alike. `app.js` hides the modal when the event matches `pendingSubjectId`, so a second tab does not keep showing a prompt that is already decided. A click in that stale tab would get the existing 404, which is harmless. `resolve()` still pops the future exactly once.

**Stream generator.** The `/events` route body becomes a module-level `_event_stream(bus, channel, session_id)` async generator: `with bus.open(...) as sub:`, then the replay, then `async for`.
- When the client disconnects, sse-starlette cancels the generator, and the `with` block closes the subscription.
- Tests drive `_event_stream` directly with `anext` / `aclose`. This avoids the TestClient deadlock on SSE described in `test_get_events_route_is_registered`.

**Eviction hook.** `_evict_stale_sessions` calls `_bus.close_session(sid)` next to each `del _sessions[sid]` (both the idle rule and the LRU rule). The open stream ends. The browser's reconnect then gets 403, so `EventSource` goes `CLOSED` and the existing pill shows "disconnected — refresh the page".

**Startup flush.** The lifespan hook now publishes into an empty bus, which does nothing and creates no entry. That path was already dead: the sessions from before the restart cannot authenticate (see the existing follow-up in [FOLLOWUPS](../FOLLOWUPS.md)). The follow-up text is updated; the restart-recovery behaviour is unchanged.

**Correlation ids.**
- Add `new_correlation_id()` to [`src/agentic_cab/core/ids.py`](../../src/agentic_cab/core/ids.py). It returns a ULID (invariant 11).
- `create_app` takes `correlation_ids: Callable[[], str] = new_correlation_id`, injected the same way as `clock` (DI, no `_override` field). Tests pass a constant, for example `lambda: _ulid(99)`.

**Error surfaces fixed here** (all in `web/app.py`):
1. **Background turn failure.** The browser gets `{"kind": "error", "message": "internal error", "correlation_id": cid}`. Structlog gets `_log.exception("web.chat.error", session_id=..., correlation_id=cid)`, which carries the traceback through `exc_info`.
2. **Unhandled route exceptions.** `app.add_exception_handler(Exception, ...)` returns 500 `{"detail": "internal error", "correlation_id": cid}` and logs `web.unhandled_error` with `exception`, path and method. Starlette re-raises after the handler, so tests use `raise_server_exceptions=False`.
3. **Confirmation 404.** `detail` becomes the constant `"no pending confirmation"` instead of echoing `subject_id`.

`app.js` renders `"[error] internal error (ref <id>)"` with `textContent`.

## Invariants touched

- **1 — Layer direction.** The bus, channel and route changes are all in `web/`. `core/ids.py` gains one factory with no new imports. `web` → `core` is a downward edge.
- **2 — Structural confirmation gate.** The channel still owns y/n. `resolve()` signature, approver, and the one-pop semantics are unchanged.
  - Fan-out only changes who *sees* a prompt, and only subscribers of the owning session can see one: every `/events` request goes through `_authorize` before `bus.open`.
  - Tests pin all of the following:
    - two subscribers both receive `pending_confirmation`;
    - one approval resolves it;
    - a second `resolve` returns `False`;
    - B's prompt never reaches A's subscription;
    - a late subscriber gets the replayed prompt.
- **6 / 7 — Action log and persistence split.** No change to the rows or to JSONL. The bus stays a real-time projection only.
- **8 / 15 — No PII or secrets.** The token never enters the bus, error bodies or logs. Correlation ids are opaque ULIDs. A test asserts that the exception's text does not appear in the SSE payload or the 500 body.
- **9 — Determinism.** Correlation ids are injected through `create_app`. There is no module-level randomness or clock read.
- **11 — IDs are ULIDs.** `new_correlation_id()` is a ULID.
- **13 / 14.** No `TODO`. Logging goes through structlog only.
- **Lint.** Every new test assert carries `# noqa: S101 pytest assertion`.

## Tasks

### 1. Per-subscriber fan-out bus
- **Files:** `src/agentic_cab/web/sse.py`, `tests/unit/test_web_session_event_bus.py` (rewritten)
- **Tests first** (these fail against today's single-queue bus):
  - `test_two_subscribers_on_one_session_both_receive_every_event`: open two subscriptions, publish three rows, and both get all three in order.
  - `test_publish_with_no_subscribers_creates_no_entry`: publish to 1000 distinct ids, and `session_count() == 0`.
  - `test_closing_last_subscriber_removes_session_entry`.
  - `test_sessions_are_isolated`.
  - `test_overflow_disconnects_only_the_slow_subscriber`: fill one subscriber past `_QUEUE_MAX`. Its iteration ends after draining. The other subscriber still receives rows, and `subscriber_count` drops by one.
  - `test_subscriber_cap_supersedes_oldest`.
  - `test_close_is_idempotent`.

  The old buffering tests (`publish_then_subscribe`, `overflow_drops_oldest`) are removed because that behaviour is gone on purpose.
- **Then:**
  - Implement `Subscription`, `open`, `close`, `close_session`, `session_count`, `subscriber_count`, and the overflow and cap rules from the design notes. Update the module docstring.
  - Keep `bus.subscribe(session_id)` as a thin async-generator wrapper over `with self.open(...)`, so `tests/unit/test_web_confirmation_channel.py` keeps working unchanged.
- **Commit:** `feat(web): give each SSE subscriber its own bounded queue`

### 2. Bus lifecycle: close on disconnect and on session eviction
- **Files:** `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - Add an autouse fixture that `monkeypatch`es `agentic_cab.web.app._bus` to a fresh `SessionEventBus()` for each test, alongside the existing `_sessions` isolation.
  - `test_event_stream_close_removes_bus_entry`: mint a session. Start `anext(_event_stream(_bus, state.channel, sid))` as a task and poll until `subscriber_count(sid) == 1`. Publish a row; the task yields it. `aclose()` the generator, and `session_count() == 0`.
  - `test_session_eviction_closes_its_bus_subscriptions`: use a `FrozenClock` client. Open a subscription on a minted session, tick past `_SESSION_IDLE_TTL`, and mint again. Then `sid not in _sessions`, `session_count() == 0`, and the subscription's iteration ends. Add an LRU-path variant.
  - `test_unknown_session_ids_do_not_grow_bus_memory`:
    - `GET /events` for 50 never-minted ULIDs returns 403 every time;
    - `_bus.session_count()` is still 0 and `_sessions` is still empty;
    - `_bus.publish(unknown_sid, ...)` for those ids leaves the count at 0.
  - Extend `test_startup_flushes_pending_confirmations` to assert `session_count() == 0` after startup.
- **Then:**
  - Extract `_event_stream` as described in the design notes. The `/events` route returns `EventSourceResponse(_event_stream(_bus, state.channel, session_id), ping=15)`, using the state that `_authorize` returned.
  - Add `_bus.close_session(sid)` at both eviction points in `_evict_stale_sessions`.
- **Commit:** `fix(web): drop SSE bus entries on disconnect and session eviction`

### 3. Keep the gate reachable across subscribers (replay and resolved broadcast)
- **Files:** `src/agentic_cab/web/channel.py`, `src/agentic_cab/web/app.py` (`_event_stream` replay), `src/agentic_cab/web/static/app.js`, `tests/unit/test_web_confirmation_channel.py`, `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - `test_two_subscribers_both_see_pending_confirmation_and_one_approval_resolves`. Two subscriptions on the channel's bus. `request()` publishes, and both receive `pending_confirmation`. `resolve(approved=True)` returns `True`, and a second `resolve` returns `False`. Both subscriptions receive `confirmation_resolved`. The result has `approver_id == principal_id`.
  - `test_pending_events_cleared_on_resolve_and_timeout`.
  - `test_event_stream_replays_pending_confirmation_to_late_subscriber`: start `channel.request` first, then open `_event_stream`. Its first item is that prompt.
  - `test_event_stream_never_replays_another_sessions_prompt`: B's prompt never appears on A's stream.
  - `test_app_js_hides_modal_on_confirmation_resolved`: a source assertion that `app.js` handles `"confirmation_resolved"`.
- **Then:**
  - In the channel, add `_events: dict[str, dict[str, Any]]` and `pending_events()`. Pop the entry in `resolve()` and in `request()`'s `finally`, and publish `confirmation_resolved` in that `finally`. `has_pending()` is unchanged.
  - In `app.js`, add a branch that hides the modal when `row.subject_id === pendingSubjectId` and logs the row.
- **Commit:** `feat(web): replay pending confirmations to new SSE subscribers`

### 4. Generic error with a correlation id for failed chat turns
- **Files:** `src/agentic_cab/core/ids.py`, `src/agentic_cab/web/app.py`, `src/agentic_cab/web/static/app.js`, `tests/unit/test_ids.py` (or the existing ids test file), `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - `test_new_correlation_id_is_ulid` uses `parse_session_id` (the same canonical ULID pattern).
  - `test_chat_turn_failure_publishes_generic_error_with_correlation_id`:
    - set up `_async_client` on `create_app(..., correlation_ids=lambda: _ulid(99))`, mint a session, and open `sub = _bus.open(sid)`;
    - patch `agentic_cab.web.app.run_turn` to raise `RuntimeError("internal-detail-/srv/x")` and patch `Anthropic`;
    - `POST /chat`, then `await asyncio.wait_for(anext(sub), 3)`;
    - the row equals `{"kind": "error", "message": "internal error", "correlation_id": _ulid(99)}`, and `"internal-detail"` does not appear in `json.dumps(row)`;
    - under `structlog.testing.capture_logs()`, a `web.chat.error` entry has `correlation_id == _ulid(99)` and carries the exception.
  - `test_app_js_renders_error_correlation_id`: a source assertion that the error branch references `correlation_id`.
- **Then:** add `new_correlation_id()` and the `correlation_ids` parameter to `create_app`. Replace the `except` body as described in the design notes. Update the error branch in `app.js`.
- **Commit:** `fix(web): report chat-turn failures with a correlation id, not exception text`

### 5. Generic 500 handler and non-reflecting 404
- **Files:** `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - `test_unhandled_route_error_returns_generic_500_with_correlation_id`: patch `JSONLActionLog.read` to raise `RuntimeError("internal-detail-/srv/x")`. `GET /sessions/{sid}/log` with `raise_server_exceptions=False` returns 500 with body `{"detail": "internal error", "correlation_id": _ulid(99)}`, and `"internal-detail"` does not appear in `response.text`. `capture_logs` shows `web.unhandled_error` with the same id.
  - `test_confirmation_404_does_not_echo_subject_id`: the body is `{"detail": "no pending confirmation"}` and the subject string is absent.
- **Then:** register the exception handler inside `create_app`, so it closes over `correlation_ids`, and make the 404 detail a constant.
- **Commit:** `fix(web): return generic 500s with a correlation id and stop echoing ids in 404s`

### 6. Documentation
- **Files:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) ("Web session authentication" section and the Web row of the surfaces table), [`docs/FOLLOWUPS.md`](../FOLLOWUPS.md)
- **Test first:** `test_architecture_doc_describes_sse_fanout_and_error_contract` asserts that the doc mentions `per-subscriber`, `correlation_id` and `close_session`.
- **Then:**
  - Document in ARCHITECTURE:
    - per-subscriber fan-out, the bounds, and disconnect-on-overflow;
    - pending replay and `confirmation_resolved`;
    - bus cleanup on disconnect and eviction;
    - the error contract: a generic message plus a ULID correlation id, with detail in structlog only.
  - In FOLLOWUPS:
    - Update the "startup flush has no subscriber" entry: the flush now publishes into an empty bus and creates no entry.
    - Add an entry: action-log `outcome` rows carry `error: str(e)` from tools (`confirm.py`, `cancel.py`, `geocode.py`, `track.py`). These reach the owning session's browser through SSE and `/sessions/{id}/log`. They are owner-only, so this is not a cross-session leak.
- **Commit:** `docs(web): document SSE fan-out lifecycle and the generic error contract`

### 7. Verification
- **Files:** none expected.
- **Check:** `make verify` (fmt-check, ruff with bandit `S`, pyright, test-fast) and `make transcripts-ci` are green. The in-process tests from Tasks 1–3 cover the two-subscriber modal, the cross-tab hide on `confirmation_resolved`, and bus cleanup on stream close.
- **Commit:** only if a fix is needed: `fix(web): <specific>`.

## Risk

risk: gate concurrency

- **gate:** `pending_confirmation` delivery changes from one shared queue to fan-out plus replay, and the channel gains `pending_events()` and the resolved broadcast. Task 3 pins the following: owner-only delivery, both subscribers see the prompt, a single resolution, the approver unchanged, and a late subscriber still reaching the gate.
- **concurrency:** asyncio fan-out with subscribers being closed during `publish`, and cleanup driven by cancellation. Mitigations: everything runs on one loop with synchronous bus methods, `publish` iterates a snapshot, and `close` is idempotent. Tasks 1 and 2 test disconnect, overflow, cap and eviction directly.

Not idempotency or fingerprint: neither is touched.

## Out of scope

- Redacting `error` fields inside action-log rows, or changing the `/sessions/{id}/log` row contents. That would touch the audit trail (invariant 6). It is recorded as a follow-up in Task 6.
- Restart recovery of pending confirmations (persisted or hashed tokens). An existing follow-up covers it.
- Counting an open SSE stream as session activity for the idle TTL.
- Replaying past action-log rows to a late subscriber. The JSONL log stays the durable source.
- FastAPI 422 validation bodies. They echo client input, not internal state.
- Multi-worker or sticky-session SSE.
- Any change to `tools/`, the invariants in [CLAUDE.md](../../CLAUDE.md), hooks, or CI.

## Self-check

Each acceptance criterion maps to a task:
- **Each subscriber has its own queue; two tabs both receive every event:** Task 1 (bus-level fan-out test) and Task 3 (two subscribers both get `pending_confirmation` and the resolved event).
- **`/events` and `/sessions/{id}/log` require the session token:** already enforced by #24. The existing `_AUTH_ROUTES` tests cover a missing cookie, a wrong cookie, a never-minted session, an evicted session, and a cross-session cookie. Task 2 adds that a refused `/events` creates no bus state.
- **Bus queues removed on last-subscriber disconnect or session eviction; unknown ids don't grow memory:** Task 1 (last close removes the key; publish creates nothing) and Task 2 (stream close, idle and LRU eviction, the 50-unknown-id 403 test with `session_count() == 0`).
- **Generic message plus correlation id; detail to structlog only:** Task 4 (chat-turn SSE error) and Task 5 (unhandled 500s and the 404 detail). Both assert the exception text is absent from what the browser receives and present in structlog under the same correlation id.
