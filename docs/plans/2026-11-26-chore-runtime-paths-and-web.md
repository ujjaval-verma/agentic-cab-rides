# Plan: runtime paths and web port configurable; live geocode cache out of tracked fixtures

## Goal

Make the runtime paths and the web port configurable through environment variables, so separate instances can run side by side without sharing files or a port. Also stop live geocode results from landing in the tracked `fixtures/geocode/`. Each cached file is an `AddressRef` whose `raw` field holds the user's query word for word, so a `git add -A` would publish real addresses.

The issue cites `web/app.py:190`, but the live write path is now `web/app.py:439` (`GeocodeCache(fixtures_dir=_geocode_fixtures_dir)` inside `POST /chat`). The REPL has the same write path at `agent/chat.py:325`. In both, `GeocodeCache.put` writes into `fixtures/geocode` on every cache miss.

The fixed values live in these places today:
- `Path("data")` / `Path("logs")` / `Path("fixtures/geocode")` in `web/app.py::create_app`, `agent/chat.py::run_repl` and `__main__.py` (`init-db`, `render-transcripts`)
- `Path("logs")` in `scripts/replay.py`
- `--port 8000` in `__main__.py web`
- `WEB_PORT ?= 8000` and `rm -rf data logs chat` in the `Makefile`

Chat history is written today with `session_chat_path(log_dir, …)`. Nothing writes to `chat/`, even though `.gitignore` and `make reset` mention it.

## Acceptance criteria

- `AGENTIC_CAB_DATA_DIR`, `AGENTIC_CAB_LOG_DIR`, `AGENTIC_CAB_CHAT_DIR`, `AGENTIC_CAB_GEOCODE_CACHE_DIR`, `AGENTIC_CAB_WEB_PORT` override defaults; `.env.example` documents them.
- Live geocoding caches under the data dir (gitignored); `fixtures/geocode` is read-only at runtime.
- Two instances with different env values run concurrently without touching each other's files or port (test or documented manual check).

## Invariants touched

- **1 — Layer direction.** A new leaf module `src/agentic_cab/core/settings.py` holds a frozen `RuntimeSettings` dataclass (`data_dir`, `log_dir`, `chat_dir`, `geocode_cache_dir`, `geocode_fixtures_dir`, `web_port`) and a pure `RuntimeSettings.from_env(environ: Mapping[str, str])`. It uses only the stdlib and imports nothing from the project. Only the composition roots call it: `__main__.py`, `web/app.py::create_app`, `agent/chat.py::run_repl` and `scripts/replay.py`. Tools and adapters never import it. They keep taking plain `Path` values through their constructors, as `GeocodeCache` does today. No upward edges are added.
- **7 — Persistence split.** SQLite stays at `<data_dir>/state.db`. The action log and chat history stay JSONL, and the geocode cache stays one JSON file per query. Only their base directories change. Nothing moves between SQLite and flat files.
- **8 — PII.** Cache files still hold `AddressRef.raw`, but now in a gitignored runtime directory (`<data_dir>/geocode/` by default) instead of a tracked one. The issue exists to close this exposure. Nothing new is logged. Any new log line names the settings or the cache directory only, never a query or file contents. The existing rule stays: raw query text is never logged.
- **9 — Determinism / DI.** No module-level `os.environ` reads. The environment is read only inside a composition-root function call, the same way `_allowed_hosts` reads `AGENTIC_CAB_WEB_HOST` today. `create_app` and `run_repl` accept `settings: RuntimeSettings | None`. The existing explicit kwargs (`data_dir`, `log_dir`, `geocode_fixtures_dir`) keep working and win over `settings`, so current tests still pass. Scenario runs keep the committed `fixtures/geocode` as their read-only source (`runner._DEFAULT_GEOCODE_FIXTURES` is unchanged), so transcript determinism is unaffected.
- **11 — Path containment.** `core/paths.py` still resolves and contains session paths. `session_chat_path` is now called with `chat_dir` instead of `log_dir`, with the same containment check.
- **2, 3, 4, 5, 6 — gate, fingerprint, re-quote, idempotency, four-row log.** Not touched. No tool code changes.

## Tasks

1. **`RuntimeSettings` + env resolution**
   - Files: `src/agentic_cab/core/settings.py` (new), `tests/unit/test_runtime_settings.py` (new).
   - Failing test first:
     - `from_env({})` gives `data`, `logs`, `chat_dir == log_dir`, `geocode_cache_dir == data/geocode`, `geocode_fixtures_dir == fixtures/geocode`, and port 8000.
     - Each of the five variables overrides its field.
     - `AGENTIC_CAB_GEOCODE_CACHE_DIR` unset means the cache follows `AGENTIC_CAB_DATA_DIR`.
     - An empty string counts as unset.
     - A non-integer port, or one outside 1–65535, raises a `ValueError` that names the variable.
     - A geocode cache dir that resolves to, or inside, the fixtures dir raises `ValueError`.
     - A test monkeypatches `os.environ` after import and checks that importing the module read nothing.
   - Default choice: chat defaults to the log dir. That keeps today's `logs/<sid>.chat.jsonl` layout and the `render-transcripts` / `replay` readers. `AGENTIC_CAB_CHAT_DIR` moves it.

2. **Split the geocode cache into a writable layer and read-only seed layers**
   - Files: `src/agentic_cab/adapters/geocoding/cache.py`, `tests/unit/test_geocode_cache.py`.
   - Failing test first: `GeocodeCache(cache_dir=w, read_only_dirs=(f,))` in a `tmp_path` should:
     - return a hit that exists only in `f`;
     - prefer `w` when both have the key;
     - make `put` write only into `w`;
     - leave `f`'s file set and mtimes unchanged;
     - not create `f` when it is missing (no `mkdir` on read-only dirs).
   - The existing sha1 file-name test still passes.
   - Rename the constructor parameter `fixtures_dir` to `cache_dir`. Every call site is updated in tasks 3–5.

3. **Web composition root uses the settings**
   - Files: `src/agentic_cab/web/app.py`, `tests/unit/test_web_app_runtime_paths.py` (new).
   - Failing test first:
     - With `AGENTIC_CAB_DATA_DIR` and `AGENTIC_CAB_LOG_DIR` monkeypatched to `tmp_path` subdirectories, `create_app()` under `TestClient` plus `POST /sessions` creates `state.db` under the env data dir and nothing under the cwd's `data/`.
     - A `_build_geocoder(settings, http=...)` helper, driven with an `httpx.MockTransport` that returns a canned Nominatim payload, writes its cache file under `<data_dir>/geocode`. The fixtures dir passed in stays unchanged.
   - Implementation:
     - `create_app(..., settings: RuntimeSettings | None = None)` resolves `settings or RuntimeSettings.from_env(os.environ)` inside the factory.
     - `POST /chat` builds its geocoder with `_build_geocoder`, which uses `GeocodeCache(cache_dir=settings.geocode_cache_dir, read_only_dirs=(settings.geocode_fixtures_dir,))`.
     - Existing kwargs still override.

4. **REPL composition root and chat dir**
   - Files: `src/agentic_cab/agent/chat.py`, `tests/unit/test_chat_repl.py`.
   - Failing test first: `run_repl(settings=RuntimeSettings(...tmp dirs...), input_source=<scripted EOF>, channel=<stub>, console=<recording>)` does three things:
     - writes `state.db` under the settings data dir;
     - puts the chat JSONL path under `chat_dir`, not `log_dir`;
     - builds its geocode cache with `cache_dir == settings.geocode_cache_dir` and fixtures only as read-only.
   - The Anthropic client is never called because the input is EOF before the first turn. Reuse the stubbing pattern already in `test_chat_repl.py`, so no API key is needed.

5. **CLI and scenario runner: port, `init-db`, transcripts, read-only fixtures**
   - Files: `src/agentic_cab/__main__.py`, `src/agentic_cab/scenarios/runner.py`, `tests/unit/test_cli_runtime_settings.py` (new), `tests/unit/test_runner_geocode_cache_path.py`.
   - Failing test first:
     - Typer `CliRunner` with `AGENTIC_CAB_WEB_PORT=8123` and `uvicorn.run` monkeypatched to capture its args: `web` is called with `port=8123`, and `--port` still overrides the variable.
     - `init-db` with `AGENTIC_CAB_DATA_DIR=tmp` creates `tmp/state.db`.
     - The runner test asserts that `run_scenario` builds its cache with the committed fixtures as a read-only dir and the writable layer under the passed `data_dir`.
   - For the runner test, monkeypatch `GeocodeCache` to record its constructor args, then stop before `run_turn` by monkeypatching `Anthropic`/`run_turn` to raise a sentinel. No model call is made.
   - Implementation: `web`'s `--port` defaults to `None` and falls back to `settings.web_port`. `render-transcripts` and `init-db` use `settings.data_dir` and `settings.log_dir`.

6. **`replay`, `Makefile`, `.env.example`, `.gitignore`**
   - Files: `scripts/replay.py`, `Makefile`, `.env.example`, `.gitignore`, `tests/unit/test_replay_script.py`, `tests/unit/test_runtime_config_surface.py` (new).
   - Failing test first:
     - `replay` with `AGENTIC_CAB_LOG_DIR` and `AGENTIC_CAB_CHAT_DIR` set to `tmp_path` subdirectories finds the log and chat files there.
     - A surface test reads `.env.example` and asserts that all five variable names appear, as commented-out lines with defaults.
     - A Makefile text test, following the `test_replay_script.py` / `test_audit_deps.py` pattern, asserts three things: the `web` and `demo` port comes from `WEB_PORT ?= $(or $(AGENTIC_CAB_WEB_PORT),8000)`; `reset` removes `$(DATA_DIR) $(LOG_DIR) $(CHAT_DIR)`, which default from the `AGENTIC_CAB_*` variables, instead of the literal `data logs chat`; and the `web` help string no longer hardcodes `:8000`.
   - `.gitignore`: `data/` already covers `data/geocode/`. Add a comment that the live geocode cache lives there.

7. **Two isolated instances (criterion 3)**
   - Files: `tests/unit/test_isolated_instances.py` (new).
   - Failing test first: build `RuntimeSettings.from_env(env_a)` and `from_env(env_b)` with distinct `tmp_path` directories and ports 8101 and 8102. Then assert:
     - All resolved directories and ports are pairwise disjoint, and no directory of A is inside a directory of B.
     - `create_app(settings=a)` and `create_app(settings=b)` each, under `TestClient`, create `state.db` only in their own data dir.
     - `_build_geocoder(a, http=mock)` and `_build_geocoder(b, http=mock)` each write a cache file only under their own data dir.
     - A snapshot of a shared fixtures dir (names and mtimes) is identical before and after.
     - The CLI port capture from task 5, run once per env, gives 8101 and 8102.
   - No model, no network, no API key.
   - Documented limit: two apps in one process share the module-level `_sessions` and `_bus`. Real instances are separate uvicorn processes, which is what the manual check covers.

8. **Docs**
   - Files: `README.md`, `docs/ARCHITECTURE.md` (runtime topology "Geocode cache" and "State" bullets, persistence-split section), `tests/unit/test_runtime_config_surface.py`.
   - Failing test first: assert the README has a "Running two instances" snippet containing `AGENTIC_CAB_DATA_DIR`, `AGENTIC_CAB_WEB_PORT`, and the line saying `fixtures/geocode` is read-only.
   - The README snippet is the manual check: two terminals, each with its own `AGENTIC_CAB_*_DIR` and `AGENTIC_CAB_WEB_PORT`, run `make web`. Then confirm each instance's directories fill independently and `git status` stays clean.
   - The ARCHITECTURE text changes from "fixtures under `fixtures/geocode/`, shared across surfaces" to "read-only committed fixtures + writable cache under the data dir".

## risk: concurrency

Two processes run side by side, and their isolation depends on the resolved directories being disjoint, each with its own SQLite file. Gate, idempotency and fingerprint code are not touched.

## Out of scope

- Moving the module-level `_sessions` / `_bus` singletons in `web/app.py` into per-app state. They stay process-wide; separate processes are isolated.
- Removing `raw` from `AddressRef` or from cached files.
- Changing the cache key format or migrating live entries already committed in `fixtures/geocode`. Auditing those files for real addresses is a separate follow-up for `docs/FOLLOWUPS.md`.
- A tool to promote cache entries into fixtures. Re-recording fixtures becomes an explicit manual copy.
- Making the scenario DB or log locations configurable beyond what `render-transcripts` gets from the settings.
- `AGENTIC_CAB_WEB_HOST`, which already exists, and `.env` loading behaviour.
- No gate, hook or invariant text changes are needed.

**Self-check:** Criterion 1 is covered by tasks 1, 3, 4, 5 and 6 (the five variables, wired in and documented in `.env.example`). Criterion 2 is covered by tasks 2, 3, 4, 5 and 7 (writes go to the data dir, fixtures are a read-only layer, scenarios still read fixtures). Criterion 3 is covered by task 7 (unit test, no model) and task 8 (documented manual check).
