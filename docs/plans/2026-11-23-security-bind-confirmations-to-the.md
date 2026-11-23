# Plan: bind confirmations to the owning session and set the approver on the server (#24)

One step goes slightly past the issue's wording: `POST /chat` also requires the session token. Today `/chat` creates a session for any ULID it is sent, and a session created that way would have no token, which would leave a hole next to the confirmation route. This only makes things stricter.

## Goal

Make the web confirmation gate session-bound and make the approver come from the server:
- `POST /sessions` issues an unguessable token in a cookie.
- Every session route checks that token.
- A confirmation can only resolve a pending request in the caller's own session.
- The approver identity comes from server-side session state, never from the request body.
- The action log records `approver_id` and `channel` the same way for the CLI, web and scripted channels.

## Acceptance criteria

- Route is session-scoped (`/sessions/{session_id}/confirmations/{subject_id}`) and resolves only within that session; other sessions get 403/404.
- The session is bound to a server-issued, unguessable token (cookie or header) required on confirmation, events, and log routes.
- `approver` is derived server-side (session principal), never accepted from the body; the action log records `approver_id` and `channel`.
- Tests: cross-session approval rejected; body-supplied approver ignored.

## Design decisions

**Token**
- `POST /sessions` mints `token = secrets.token_urlsafe(32)` (256 bits).
- The token is stored only in memory, in `_SessionState.token`, declared with `field(repr=False)`. It is not a ULID on purpose: ULIDs start with a predictable timestamp, so they are fine as identifiers but not as secrets.
- Each request is checked with `hmac.compare_digest(presented.encode(), state.token.encode())`.

**Cookie, not a header**
- The browser's `EventSource` cannot set custom headers, so `/events` would otherwise need the token in the query string. Query strings end up in access logs and browser history, which conflicts with invariant 15.
- Cookies travel automatically on same-origin `fetch` and `EventSource`, and with HttpOnly they are invisible to page JavaScript.
- Cookie settings:
  - Name: `cab_session_<session_id>`. One cookie per session, so two open tabs don't overwrite each other's token.
  - `Path=/`, `HttpOnly`, `SameSite=Strict`. SameSite=Strict blocks cross-site POSTs (CSRF) to the confirmation route.
  - No `Max-Age`, so it is a browser-session cookie.
  - `Secure` is not set, because `make web` serves plain http on localhost. Adding a configurable Secure flag is out of scope.
- The `POST /sessions` response body stays `{"session_id": ...}`. The token is never returned in a body.

**How requests are checked**
- One helper, `_authorize(request, session_id) -> _SessionState`, is called inline at the top of each handler, after FastAPI has validated the parameters. Calling it there means an invalid ULID still gets a 422, so the existing `test_*_rejects_invalid_session_id` tests keep passing unchanged.
- It returns 403 in three cases, all with the same response so it doesn't reveal which sessions exist:
  - the session is unknown or was evicted;
  - the cookie is missing;
  - the cookie doesn't match.
- A caller who passes the check but names a `subject_id` that isn't pending in their own session gets 404.

**Session lifecycle**
- `POST /sessions` now creates the `_SessionState` up front and runs `_evict_stale_sessions` first.
- `/chat` no longer creates sessions on demand. `_get_session` is split into `_create_session` and a lookup.
- A session id that was evicted is rejected with 403 and is never brought back; the client has to call `POST /sessions` again. This is a deliberate tightening.

**Session principal (no login, local prototype)**
- `POST /sessions` also mints a `principal_id` with a new `new_principal_id()` in `core/ids.py`. It is a ULID, which satisfies invariant 11, and it is stored in `_SessionState`.
- It is opaque, issued by the server, and never taken from the client. It is safe to log because it is an identifier, not a credential.

**What each channel records**

New fields on `ConfirmationResult`: `approver_id: str | None = None` and `channel: Literal["cli", "web", "scripted"] | None = None`. The defaults keep the existing test spy channels compiling. All three real channels always fill both fields.

| Channel | `approver` (existing field, kept) | `approver_id` when approved | `channel` |
|---|---|---|---|
| Web | `"web-user"` (constant set on the server) | the session's `principal_id` | `"web"` |
| CLI | `"cli-tty"` | a per-REPL `principal_id`, minted in `run_repl` and passed into the channel's constructor | `"cli"` |
| Scripted | `"scripted"` | `"scripted"` (a constant, so scenarios stay deterministic) | `"scripted"` |

- On a decline or timeout, `approver_id` is `None` (the same as `approver` today) and `channel` is still filled in.
- No OS username, hostname or `getpass` value is ever used.

**Where it shows up in the action log**
- The tools already write `checks.confirmation = confirmation.model_dump(mode="json")` on the `verified` row, in `tools/confirm.py` and `tools/cancel.py`.
- So both new keys show up there without touching any tool code.

**Transcripts and snapshots**
- No re-render is needed. `make transcripts-ci` runs `render-transcripts --dry-run`, and in `src/agentic_cab/__main__.py` the dry run only validates the scenario YAML. It never compares against the committed `transcripts/*.md`.
- `tests/unit/__snapshots__/test_render_transcript.ambr` is built from hand-written rows in `test_render_transcript.py`, so it doesn't change.
- `check_gate_invoked_for_tool` requires the `"approver"` key, which is kept.
- The live scenario tests (marked `scenario`) will produce rows with the two extra keys, and those still pass every checker.
- The committed transcripts will show the older `confirmation` dict until the next deliberate re-render. That gets recorded in `docs/FOLLOWUPS.md`. The model pin is unchanged.

## Invariants touched

- **1 (layer direction).** Changes stay in `web/`, `adapters/confirmation/`, `core/ports.py` and `core/ids.py`. Nothing in tools/ or adapters/ imports from `web/`.
- **2 (confirmation gate is structural).**
  - The tools still call `channel.request(req)` unconditionally, and the agent still never sees an approval or approver parameter.
  - The web route can only reach `_authorize(...).channel`, the channel of the session whose token was presented. The loop over every session's channel is deleted, so a request has no code path to another session's pending request.
  - `WebConfirmationChannel.resolve(subject_id, *, approved)` drops its `approver` parameter entirely. A value from the request body therefore cannot reach `ConfirmationResult`: the type system enforces it, not just a check.
  - Idempotency replays still don't call the channel. The replay path in `tools/confirm.py` emits no `confirmation`, and that is unchanged.
  - CLI still reads from `/dev/tty`.
- **6 (four row kinds per gated action).**
  - The shape is unchanged: still `requested → verified → executed → outcome`, the same `action_id` linkage, and `verified.result == "rejected"` still short-circuits.
  - The only difference is two extra keys inside `verified.checks.confirmation`.
  - `four_row_pattern` and `gate_invoked_for_tool` are untouched and still pass.
- **8 (PII redaction).** `approver_id` is an opaque ULID made by the server, or a constant, and `channel` is a fixed set of values, so no personal data enters the log. A test confirms that `redact()` leaves `approver_id` unchanged and that the CLI never reads OS identity.
- **9 (determinism).** The scripted channel uses constants, and nothing new reads `random` or `datetime.now()` at module level.
- **11 (IDs are ULIDs).** `principal_id` is a ULID. The token is a secret, not an ID.
- **14 (no `print()`).** No new `print()` calls. Logging goes through `structlog` and never includes the token.
- **15 (public repo, no secrets).**
  - The token exists only in process memory and in the browser cookie. It never appears in structlog fields, JSONL, SSE payloads, response bodies or transcripts.
  - A test asserts that a minted token string does not appear in `logs/` after a full confirm flow, and does not appear in the JSON body of `POST /sessions`.
  - Test fixtures use tokens generated at runtime, never literal ones.

## Tasks

For every task, write the failing test first, then the code.

1. **Add the approver fields to the port, plus the principal id helper.**
   - Files: `src/agentic_cab/core/ports.py` (`ConfirmationResult.approver_id`, `.channel`), `src/agentic_cab/core/ids.py` (`new_principal_id`), `src/agentic_cab/adapters/confirmation/scripted.py`.
   - Failing tests first:
     - `tests/unit/test_scripted_channel.py`: an approve returns `channel == "scripted"` and `approver_id == "scripted"`; a decline returns `channel == "scripted"` and `approver_id is None`.
     - `tests/unit/test_tool_confirm.py`: the `verified` row's `checks.confirmation` contains `approver_id` and `channel`, and the action's row kinds are still exactly the four.
     - `tests/unit/test_tool_cancel.py`: the same two assertions for cancel.

2. **CLI channel records its principal.**
   - Files: `src/agentic_cab/adapters/confirmation/cli.py` (new keyword-only `principal_id: str` argument; sets `approver_id` and `channel="cli"`), `src/agentic_cab/agent/chat.py` (`run_repl` passes `new_principal_id()`).
   - Failing test first, in `tests/unit/test_cli_confirmation.py`:
     - An approve via the tty file gives `approver_id == <injected id>` and `channel == "cli"`.
     - A decline gives `approver_id is None` and `channel == "cli"`.
     - The existing test that stdin is never read stays as it is.

3. **Web channel sets the approver itself.**
   - File: `src/agentic_cab/web/channel.py`. The constructor takes `principal_id: str`. `resolve(subject_id, *, approved)` loses its `approver` parameter. Approve gives `approver="web-user"`, `approver_id=principal_id`, `channel="web"`. Decline and timeout give `channel="web"` and `approver_id=None`.
   - Failing tests first:
     - `tests/unit/test_web_confirmation_channel.py`: the approved result's `approver_id` equals the constructor's `principal_id`, and `resolve` called with an `approver=` keyword raises `TypeError`.
     - Update the call sites in `tests/unit/test_web_channel_restart.py`.

4. **Session token and principal, issued by `POST /sessions`.**
   - File: `src/agentic_cab/web/app.py`. `_SessionState` gains `token` (`repr=False`) and `principal_id`. `POST /sessions` runs the eviction sweep, creates the state up front, and sets the cookie as specified above. Add `_create_session` and `_authorize`, the latter using `hmac.compare_digest`. Remove the on-demand creation from `_get_session`.
   - Failing tests first, in `tests/unit/test_web_app_routes.py`:
     - The `Set-Cookie` header from `POST /sessions` has the name `cab_session_<sid>` and the flags `HttpOnly` and `SameSite=Strict`.
     - The response JSON has exactly one key, `session_id`.
     - Two mints give different tokens, each at least 43 characters.

5. **Require the token on `/chat`, `/events` and `/sessions/{id}/log`.**
   - Files: `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_routes.py`.
   - Failing tests first, each run against all three routes:
     - No cookie gives 403.
     - A cookie with the wrong value gives 403.
     - A session that was never minted, or was evicted, gives 403.
     - Session A's cookie used on session B's id gives 403.
     - An invalid ULID still gives 422.
     - `/events` with no cookie returns 403 right away. This is testable because no stream is opened, so the TestClient SSE deadlock doesn't apply.
   - Then update the existing tests to use a `_mint(client) -> sid` helper. This covers the eviction tests, the LRU test, the meter-wiring test and the log tests.
   - `test_session_reused_after_eviction_gets_fresh_state` becomes "an evicted session id is rejected with 403".

6. **Move confirmations to the session-scoped route.**
   - File: `src/agentic_cab/web/app.py`. Add `POST /sessions/{session_id}/confirmations/{subject_id}`, which calls `_authorize` and then `state.channel.resolve(subject_id, approved=body.approved)`. Delete `POST /confirmations/{subject_id}`. The request body model becomes `approved: bool` only; any extra fields are ignored. Update the module docstring's route list.
   - Failing tests first, in `tests/unit/test_web_app_routes.py`:
     - **Cross-session approval is rejected.** Sessions A and B are minted on separate `TestClient` cookie jars, and B has a pending confirmation.
       - A posts to `/sessions/B/confirmations/{subj}` and gets 403.
       - A posts to `/sessions/A/confirmations/{subj}` and gets 404.
       - B's pending request is still unresolved afterwards (`has_pending()` is true).
       - B then resolves it successfully.
     - **A body-supplied approver is ignored.** POST `{"approved": true, "approver": "mallory"}` gets 200, and the resulting `ConfirmationResult` has `approver == "web-user"`, `approver_id == _sessions[sid].principal_id` and `channel == "web"`.
     - **The old route is gone.** `POST /confirmations/{id}` gets 404 or 405.
     - **The token never leaks.** After a full resolve flow, the token string appears nowhere under `tmp_path / "logs"`.
     - **Redaction leaves `approver_id` alone.** It survives `redact()` unchanged. This one goes in `tests/unit/test_action_log*.py`.
     - Rewrite `test_post_confirmations_resolves_pending_future` and `test_post_confirmations_returns_404_when_no_pending` for the new route.

7. **Update the frontend.**
   - File: `src/agentic_cab/web/static/app.js`. Post confirmations to `"/sessions/" + encodeURIComponent(sessionId) + "/confirmations/" + encodeURIComponent(subjectId)` with body `{ approved }`. Make `credentials: "same-origin"` explicit on the `POST /sessions` and `/chat` fetches. `EventSource` stays as it is, since same-origin requests send cookies automatically.
   - Failing test first: extend `test_app_js_uses_server_minted_session_id` (or add a sibling test) to assert that `app.js` has no `"approver"` and no `"/confirmations/"` path outside the `/sessions/` prefix, and that it does contain `"/confirmations/"` under the session path.

8. **Docs.**
   - `docs/ARCHITECTURE.md`: in the verified-row example, add `"approver_id"` and `"channel"`. Add a short note on how web sessions authenticate: cookie, `SameSite=Strict`, the route list, and principal vs token.
   - `docs/FOLLOWUPS.md`, new items:
     - Committed transcripts are missing `approver_id` and `channel` until the next re-render.
     - A configurable `Secure` cookie flag for anything not on localhost.
     - Tokens and the session registry are lost on restart, so the startup flush of pending confirmations has no subscriber that can authenticate.
     - An optional tightening of `gate_invoked_for_tool` to also require `channel`.
   - Test: none (docs only). The gate for this task is `make verify` plus `make transcripts-ci`, both green.

risk: gate

## Out of scope

- Real user login or accounts, and any multi-user principal model.
- Keeping tokens and sessions across a process restart.
- Making the startup re-publish of pending confirmations reachable after a restart.
- A configurable `Secure` flag or TLS.
- Moving `/events` and `/chat` under `/sessions/{id}/`.
- Re-rendering the transcripts or changing the model pin.
- Changing the scenario invariant checkers.
- The `ConfirmationResult` coherence validator already listed in FOLLOWUPS.
- Rate limiting.

## Self-check

- Session-scoped route and cross-session 403/404: tasks 6 and 7.
- Token required on the confirmation, events and log routes: tasks 4 and 5 (plus 6).
- Approver set on the server, with `approver_id` and `channel` in the log: tasks 1, 2, 3 and 6.
- Both required tests (cross-session approval rejected, body approver ignored): task 6.
- `make verify` and `make transcripts-ci` stay green with no re-render, because the dry run validates YAML only and the snapshot test uses hand-built rows.
