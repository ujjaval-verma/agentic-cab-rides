# Plan: reject foreign Host and Origin on the local web server (#25)

This fits in 6 tasks. It needs no change to a gate, hook or invariant. The one confirmation-route change adds a request check that runs before the route; the channel's logic is not touched.

## Goal

Two attacks are possible today. A DNS-rebinding page, or a page served from another port on `localhost`, can POST to the local web server. That lets it drive `/chat`, which spends the operator's Anthropic quota, and approve its own pending confirmations.

`SameSite=Strict` does not stop the second case. `localhost:3000` and `localhost:8000` count as the same site, so the browser still sends the session cookie.

The fix has two layers:
- **Host check:** only `127.0.0.1`, `localhost` and the configured bind host are accepted. Any other `Host` gets 400.
- **Origin check:** a state-changing request that carries an `Origin` header must match the server's own origin, or it gets 403.

## Acceptance criteria

- `TrustedHostMiddleware` allows only `127.0.0.1`, `localhost` (plus the configured bind host).
- State-changing routes reject requests whose `Origin` is present and not the server's own origin.
- Tests: foreign `Host` → 400; foreign `Origin` on POST → 403; same-origin passes.

## Design notes

**State-changing routes.** All three are POSTs in `src/agentic_cab/web/app.py`. There are no PUT, PATCH or DELETE routes.
- `POST /sessions` mints a session and its cookie.
- `POST /chat` runs an agent turn and spends quota.
- `POST /sessions/{session_id}/confirmations/{subject_id}` approves or declines a confirmation. This is the gate's delivery path.

`GET /`, `GET /static/*`, `GET /events` and `GET /sessions/{id}/log` are reads. The Host check still covers them, so a rebinding page cannot read them either.

**How the Host check works.**
- Use `starlette.middleware.trustedhost.TrustedHostMiddleware` with `www_redirect=False`, registered in `create_app`.
- The allowed set is `{"127.0.0.1", "localhost"}` plus the bind host, when there is one.
- Where the bind host comes from, in priority order:
  1. A new keyword argument on `create_app`: `bind_host: str | None = None`. `python -m agentic_cab web --host X` passes it directly.
  2. Otherwise, the environment variable `AGENTIC_CAB_WEB_HOST`. This is needed because `uvicorn --factory` (used by `make web` and `make demo`) cannot pass arguments to `create_app`.
- Wildcard bind addresses (all-interfaces and the empty string) are never added to the allowed set. `"*"` raises `ValueError`, so configuration can never switch the check off.
- Write the all-interfaces addresses as a named constant, not an inline literal, to stay clear of bandit `S104`.

**What "the server's own origin" means.** It is `f"{scope scheme}://{Host header}"`, compared case-insensitively and exactly against the request's `Origin`. It is not built from the allowed set plus a port, because a factory-mode app does not know its port.

This works because `TrustedHostMiddleware` runs first and has already limited `Host` to allowed values. So any `Origin` that passes the check is also on an allowed host, with the port and scheme the browser actually used. Some cases:
- `Origin: null` is rejected.
- A different port (`http://localhost:3000`) is rejected.
- A different scheme is rejected.
- No `Origin` header passes. Non-browser clients and the existing tests send none, and modern browsers always send `Origin` on a POST.

**Where the Origin check lives.** It is a plain ASGI middleware, `SameOriginMiddleware`, in a new module `src/agentic_cab/web/origin.py`.
- It applies to unsafe methods: `POST`, `PUT`, `PATCH`, `DELETE`. Any state-changing route added later is covered automatically.
- Use a plain ASGI class rather than `BaseHTTPMiddleware`, which interferes with the SSE stream on `/events`.
- A rejected request gets 403 with body `{"detail": "forbidden"}`, the same as `_FORBIDDEN`. It is logged through structlog as `web.origin.rejected` with the method and path only.
- **Order matters:** register `SameOriginMiddleware` first and `TrustedHostMiddleware` second, so the Host check is outermost and runs first. A request with both a foreign Host and a foreign Origin gets 400.

**Test client.** Starlette's `TestClient` and the existing `_async_client` send Host `testserver` by default. Once the Host check is in, about 20 call sites in `tests/unit/test_web_app_routes.py` would get 400.

The fix is to route every client through two helpers, `_client(app, **kw)` and `_async_client(app)`, with `base_url="http://127.0.0.1:8000"`. Tests then run against the production default allowed set. Adding `"testserver"` to that set would weaken what the tests prove, so don't.

Before relying on this, check that the cookie jar replays host-only cookies for the `127.0.0.1` host. Today's tests depend on the same behaviour under `testserver`.

## Invariants touched

- **1 — Layer direction.** All new code is in `web/`, the top layer. `web/origin.py` imports only starlette and structlog. Nothing below `web/` imports it, and `__main__.py` already imports `web.app`.
- **2 — Structural confirmation gate.** The confirmation route gets a check that runs before it. Nothing about the decision changes: the channel still owns y/n, `_authorize` still runs, and the approver is still the session's server principal. A rejected request never reaches `channel.resolve`, and a test asserts the pending confirmation is still pending after a 403.
- **9 — Determinism.** Both middlewares are pure functions of the request. No clock or randomness is involved.
- **14 — No `print()`.** Rejections are logged through structlog.
- **15 — Public repo.** No secrets. The log line includes neither the cookie nor the raw `Origin` value.
- **Lint.** Every new test assert carries `# noqa: S101 pytest assertion`, with no file-level ignore. The wildcard-bind constant avoids `S104`.

## Tasks

### 1. Point the web tests at a loopback base URL (no behaviour change)
- **Files:** `tests/unit/test_web_app_routes.py`
- **Test first:** add `test_test_client_targets_loopback_host`. It asserts the helper client's `base_url.host == "127.0.0.1"`. It fails until the helper exists.
- **Then:** add a `_client(app, **kw)` helper that wraps `TestClient(app, base_url="http://127.0.0.1:8000", **kw)`. Change `_async_client` to the same base URL. Replace every bare `TestClient(...)` (fixture `app`, `_make_clocked_client`, and the per-test constructions) with `_client(...)`. Check other test files that build a `TestClient` for the web app too. The whole suite stays green.
- **Commit:** `test(web): route web tests through a loopback-host client helper`

### 2. Allow only loopback hosts (`TrustedHostMiddleware`)
- **Files:** `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - `test_foreign_host_is_rejected_with_400`, parametrized over `GET /`, `POST /sessions`, `POST /chat` and the confirmation POST. Use host `evil.example`, plus `testserver` to prove it is not allowed.
  - `test_loopback_hosts_are_accepted` for `127.0.0.1`, `localhost` and `localhost:8000`, on `GET /` and `POST /sessions`.
- **Then:** add `_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")` and register `TrustedHostMiddleware(allowed_hosts=..., www_redirect=False)` in `create_app`.
- **Commit:** `feat(web): reject requests whose Host is not loopback`

### 3. Add the configured bind host to the allowed set
- **Files:** `src/agentic_cab/web/app.py`, `src/agentic_cab/__main__.py`, `Makefile` (only the `web` and `demo` run targets), `tests/unit/test_web_app_routes.py`
- **Tests first:**
  - `test_bind_host_argument_is_allowed`: with `create_app(bind_host="192.168.1.5")`, Host `192.168.1.5` → 200 and `evil.example` → 400.
  - `test_bind_host_env_var_is_allowed`: same check through `monkeypatch.setenv("AGENTIC_CAB_WEB_HOST", ...)`.
  - `test_wildcard_bind_host_adds_nothing`: the all-interfaces address and `""` leave `evil.example` at 400.
  - `test_star_bind_host_is_refused`: `create_app(bind_host="*")` raises `ValueError`.
- **Then:**
  - Add the `bind_host` keyword and the environment-variable fallback to `create_app`.
  - The `web` command in `__main__.py` passes `bind_host=host`.
  - In the `Makefile`, add `WEB_HOST ?= 127.0.0.1` and `WEB_PORT ?= 8000`. The `web` and `demo` targets run `AGENTIC_CAB_WEB_HOST=$(WEB_HOST) $(UV) run uvicorn ... --host $(WEB_HOST) --port $(WEB_PORT)`. Gate targets (`verify`, `lint`, `test*`, `transcripts*`) are not touched.
- **Commit:** `feat(web): allow the configured bind host through the host check`

### 4. Reject foreign `Origin` on state-changing requests
- **Files:** `src/agentic_cab/web/origin.py` (new), `src/agentic_cab/web/app.py`, `tests/unit/test_web_origin.py` (new)
- **Tests first:**
  - `test_foreign_origin_on_post_is_rejected_with_403`, parametrized over `POST /sessions`, `POST /chat` and the confirmation POST, with Origin `http://evil.example`, `null`, `http://127.0.0.1:3000` and `https://127.0.0.1:8000`.
  - `test_same_origin_post_passes`: Origin `http://127.0.0.1:8000` on all three routes passes the check (the route's normal status, not 403).
  - `test_absent_origin_post_passes`.
  - `test_get_is_not_origin_checked`: `GET /` with a foreign Origin → 200.
  - `test_foreign_host_checked_before_origin`: foreign Host plus foreign Origin → 400.
  - **Gate test:** `test_foreign_origin_confirmation_leaves_gate_pending`. Use `httpx.AsyncClient` with a pending `channel.request`. The foreign-Origin POST gets 403 and `channel.has_pending()` is still true. Then resolve the request to clean up.
- **Then:** implement `SameOriginMiddleware` as described in the design notes, and register it before `TrustedHostMiddleware` in `create_app`.
- **Commit:** `feat(web): reject cross-origin state-changing requests`

### 5. Document the host and origin checks
- **Files:** [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) (the "Web session authentication" section and the Web row of the surfaces table), [`docs/FOLLOWUPS.md`](../FOLLOWUPS.md)
- **Test first:** add `test_architecture_doc_names_host_and_origin_checks` to `tests/unit/test_web_app_routes.py`. It asserts that `docs/ARCHITECTURE.md` mentions `TrustedHostMiddleware`, `AGENTIC_CAB_WEB_HOST` and `Origin`. It fails until the docs are updated.
- **Then:** add a paragraph covering:
  - the allowed hosts and where the bind host comes from;
  - the definition of "own origin";
  - the middleware order;
  - why `SameSite=Strict` does not stop other ports on `localhost`.

  Add a follow-up for IPv6 bind hosts: the middleware does not parse `[::1]`-style bracketed hosts.
- **Commit:** `docs(web): document host allowlist and same-origin check`

### 6. Verification
- **Files:** none expected.
- **Check:** `make verify` (fmt-check, lint with bandit `S`, pyright, test-fast) and `make transcripts-ci` are green. The in-process test client covers the end-to-end chat → confirm → approve flow on both `127.0.0.1` and `localhost` hosts.
- **Commit:** only if a fix is needed: `fix(web): <specific>`.

## Risk

risk: gate

The confirmation approve route is part of the structural confirmation gate. This change adds a check in front of it. Task 4 pins that a rejected request never touches the channel, and the channel's own logic is unchanged.

## Out of scope

- CSRF tokens and `Sec-Fetch-Site` checks.
- Origin checks on GET routes. The Host check already stops rebinding reads, and CORS stops cross-origin reads.
- IPv6 bind hosts. Recorded as a follow-up.
- TLS and a configurable `Secure` cookie flag. Already in [FOLLOWUPS](../FOLLOWUPS.md).
- Rate-limiting `/chat`.
- Any change to `WebConfirmationChannel`, `tools/confirm.py` or the invariants in [CLAUDE.md](../../CLAUDE.md).

## Self-check

Each acceptance criterion maps to a task:
- **Allow only `127.0.0.1`, `localhost` and the bind host:** Tasks 2 and 3.
- **State-changing routes reject a foreign `Origin`:** Task 4. It covers all three POST routes, the only state-changing routes, plus any added later.
- **Tests:**
  - foreign `Host` → 400: Task 2, plus the ordering test in Task 4;
  - foreign `Origin` on POST → 403: Task 4;
  - same-origin passes: Tasks 2 and 4, and Task 1 keeps the existing suite green.
