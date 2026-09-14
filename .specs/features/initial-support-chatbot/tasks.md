# Initial Support Chatbot Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

---

**Design**: `.specs/features/initial-support-chatbot/design.md`
**Status**: Approved

---

## Test Coverage Matrix

> Generated from codebase (no existing tests - project has no prior backend/test tooling) and confirmed with the user (pytest for backend, manual UAT for the frontend widget - no new JS test framework). Guidelines found: none (no `AGENTS.md`/`CONTRIBUTING.md`/test config in the repo).

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| Domain / business logic (`ConversationService`, `RateLimiter`, tool schema + system prompt builder) | unit | All branches; 1:1 to spec ACs (CHAT-02, 05-10); every listed edge case has a test | `backend/tests/unit/test_*.py` | `cd backend && pytest tests/unit -q` |
| LLM client (`LLMClient` protocol, `FakeLLMClient`, `MaritalkClient`) | unit | Success path, tool-call path, and error/timeout path all covered with a mocked HTTP layer - never calls the real MariTalk API | `backend/tests/unit/test_llm_*.py` | `cd backend && pytest tests/unit -q` |
| Repository (`LeadRepository`, `MessageRepository`) | unit | Key query paths (upsert, list, append, get_history) + error handling, against a temp SQLite file | `backend/tests/unit/test_repository.py` | `cd backend && pytest tests/unit -q` |
| API routes (`/api/chat/message`, `/api/chat/{id}/history`, `/api/leads`) | integration | All routes: happy path + every listed edge case (empty/long message, rate limit, LLM failure, auth) + error paths | `backend/tests/integration/test_*.py` | `cd backend && pytest -q` |
| Entity / config / schema (SQLModel models, Settings, app wiring) | none | Covered indirectly by the layers above - build gate only | `backend/app/storage/models.py`, `backend/app/core/config.py`, `backend/app/main.py` | `cd backend && pytest -q` |
| Frontend widget (HTML/CSS/JS, i18n content) | none (manual) | Covered by the end-of-Execute manual UAT script (P1 Independent Test walkthrough in a real browser), not automated - confirmed with the user | `index.html`, `css/chat-widget.css`, `js/chat-widget.js`, `js/config.js`, `data/content-*.json` | `node --check <file>.js` / `python3 -m json.tool <file>.json >/dev/null` |

## Gate Check Commands

> Generated from the confirmed test strategy - confirm before Execute.

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick | After backend unit-test tasks (LLM client, tool schema, repositories, rate limiter, conversation service) | `cd backend && pytest tests/unit -q` |
| Full | After backend integration/API-route tasks, and after Phase 4 completes | `cd backend && pytest -q` |
| Build | After config/entity-only backend tasks (full backend suite) or a frontend file task (syntax/JSON check for the file(s) touched) | `cd backend && pytest -q`  **or**  `node --check <file>.js` / `python3 -m json.tool <file>.json >/dev/null` (frontend, per file changed in the task) |

---

## Execution Plan

Phases are ordered and run sequentially - each phase completes before the next begins, and tasks within a phase execute in order.

### Phase 1: Backend Foundation

```
T1 → T2
T1 → T3
T1 → T4
T2 → T5
T2 → T6
T4 → T6
```

### Phase 2: Persistence & Rate Limiting

```
T3 → T7
T2 → T8
```

### Phase 3: Domain Orchestration

```
T5 → T9
T6 → T9
T7 → T9
```

### Phase 4: API Layer

```
T8 → T10
T9 → T10
T7 → T11
T10 → T11
T2 → T12
T7 → T12
T10 → T13
T11 → T13
T12 → T13
```

### Phase 5: Frontend Widget

```
T14 → T15
T14 → T16
T14 → T17
T15 → T19
T16 → T19
T17 → T19
T18 → T19
T10 → T19
T11 → T19
```

### Phase 6: Ops & Docs

```
T13 → T20
T19 → T20
```

---

## Task Breakdown

### T1: Scaffold backend app + test tooling

**What**: Create the `backend/` project skeleton - dependency manifest (`requirements.txt`, `requirements-dev.txt`), a minimal bootable FastAPI app, and pytest wiring (`pytest.ini`, `tests/unit/`, `tests/integration/`, `tests/conftest.py`) so every later task can gate on `pytest`.
**Where**: `backend/` (new: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `app/__init__.py`, `app/main.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/conftest.py`)
**Depends on**: None
**Reuses**: n/a (first backend code in the project)
**Requirement**: n/a (infra - backs all `CHAT-*`/`LEAD-*`/`OBS-*` requirements)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `backend/app/main.py` exposes a bootable FastAPI `app` object (no routes yet beyond the framework default)
- [x] `requirements.txt` pins `fastapi`, `uvicorn`, `sqlmodel`, `openai`, `pydantic-settings`; `requirements-dev.txt` pins `pytest`, `pytest-asyncio`, `httpx`
- [x] `cd backend && pip install -r requirements.txt -r requirements-dev.txt && pytest -q` exits 0 (0 or more tests collected, no import errors)

**Tests**: none
**Gate**: build

**Commit**: `chore(backend): scaffold FastAPI project and pytest tooling`

---

### T2: Add Settings/config module

**What**: A `pydantic-settings` `Settings` class loading `MARITALK_API_KEY`, `MARITALK_API_BASE`, `MARITALK_MODEL`, `ADMIN_API_TOKEN`, `WHATSAPP_NUMBER`, `ALLOWED_ORIGINS`, and the rate-limit constants (15/min, 60/session) from `.env`.
**Where**: `backend/app/core/config.py`
**Depends on**: T1
**Reuses**: existing `.env` keys already present in the project root
**Requirement**: n/a (infra)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `Settings` reads all listed env vars, with sane defaults for `ALLOWED_ORIGINS` (dev) and the rate-limit constants
- [x] The raw `MARITALK_API_KEY` value is never logged or included in `repr()`/error messages
- [x] `cd backend && pytest -q` still exits 0

**Tests**: none
**Gate**: build

**Commit**: `feat(backend): add settings module for env-based config`

---

### T3: Define SQLModel data models (`Lead`, `Message`) + DB engine setup

**What**: The `Lead` and `Message` SQLModel table models from `design.md`, plus an engine/session factory pointed at `backend/data/app.db` (creating the file/tables on first run).
**Where**: `backend/app/storage/models.py`
**Depends on**: T1
**Reuses**: n/a
**Requirement**: CHAT-07, OBS-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `Lead` and `Message` match the field lists in `design.md` (including `session_id` unique index on `Lead`, `session_id` index on `Message`)
- [x] Engine/session factory creates `backend/data/app.db` and its tables on startup if missing
- [x] `cd backend && pytest -q` exits 0

**Tests**: none
**Gate**: build

**Commit**: `feat(backend): add Lead and Message SQLModel models`

---

### T4: Define `LLMClient` protocol + `FakeLLMClient` test double

**What**: The `LLMClient` Protocol (`async def complete(messages, tools) -> LLMResult`), the `LLMResult`/`ToolCall` dataclasses, an `LLMUnavailableError` exception type, and a configurable `FakeLLMClient` used by every later unit test.
**Where**: `backend/app/llm/client.py`
**Depends on**: T1
**Reuses**: n/a
**Requirement**: n/a (test infrastructure backing CHAT-02, CHAT-09)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `FakeLLMClient` can be configured to return a plain-text reply, a tool call, or raise `LLMUnavailableError`, per call
- [x] Unit tests cover all three configured behaviors
- [x] `cd backend && pytest tests/unit -q` passes; test count recorded (3 passed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): add LLMClient protocol and FakeLLMClient test double`

---

### T5: Build `save_lead_info` tool schema + system prompt template

**What**: The `save_lead_info` function-calling JSON schema (category enum from the 4 site solutions + "outro", `need_summary`, optional contact fields) and a system-prompt builder that embeds the Morpheus IA persona, the category list, the PT-BR-only instruction, the "ask for contact at most once" instruction, and the LGPD consent line - parameterized by whether contact has already been asked this session.
**Where**: `backend/app/llm/tools.py`
**Depends on**: T2
**Reuses**: category labels from `data/content-pt.json` `solutions.card1..4` (copied into the schema/prompt, not read at runtime)
**Requirement**: CHAT-05, CHAT-07

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `save_lead_info` schema requires `category` (enum of 5 values) and `need_summary`; contact fields are optional
- [x] `build_system_prompt(contact_already_asked: bool)` returns a prompt that instructs "ask once" and omits the ask when `contact_already_asked=True`
- [x] Unit tests assert the schema's required fields/enum values and that the prompt text changes correctly with the `contact_already_asked` flag
- [x] `cd backend && pytest tests/unit -q` passes; test count recorded (8 passed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): add save_lead_info tool schema and system prompt builder`

---

### T6: Implement `MaritalkClient`

**What**: The real `LLMClient` implementation using the official `openai` SDK's `AsyncOpenAI(base_url=MARITALK_API_BASE, api_key=MARITALK_API_KEY)` and `client.responses.create(model=MARITALK_MODEL, input=messages, tools=tools)`, mapping a plain-text reply from `response.output[0].content[0].text` and a `function_call` item into `ToolCall`, and mapping any request exception/timeout into `LLMUnavailableError`.
**Where**: `backend/app/llm/maritalk_client.py`
**Depends on**: T2, T4
**Reuses**: `LLMClient`/`LLMResult`/`ToolCall`/`LLMUnavailableError` from T4
**Requirement**: CHAT-02, CHAT-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Success path (plain text), tool-call path, and error/timeout path are each covered by a unit test with the underlying `openai` client mocked/monkeypatched (no network call)
- [x] `cd backend && pytest tests/unit -q` passes; test count recorded (11 passed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): implement MaritalkClient against the Responses API`

---

### T7: Implement `LeadRepository` + `MessageRepository`

**What**: `upsert_lead(session_id, category, need_summary, contact) -> Lead` (one row per session, insert-or-update), `list_leads() -> list[Lead]` (newest first), `append_message(session_id, role, content) -> None`, `get_history(session_id) -> list[Message]`.
**Where**: `backend/app/storage/repository.py`
**Depends on**: T3
**Reuses**: `Lead`/`Message` models and engine from T3
**Requirement**: CHAT-07, LEAD-01, OBS-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] `upsert_lead` called twice with the same `session_id` updates the existing row rather than creating a second one
- [x] `list_leads` returns newest-first
- [x] `get_history` returns messages in creation order
- [x] Unit tests run against a temp SQLite file (not the dev DB) and cover the above plus a DB-error path
- [x] `cd backend && pytest tests/unit -q` passes; test count recorded (18 passed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): add Lead and Message repositories`

---

### T8: Implement `RateLimiter`

**What**: In-memory sliding-window limiter: `check(session_id) -> bool`, enforcing 15 messages/minute and 60 messages/session (both from `Settings`).
**Where**: `backend/app/api/rate_limit.py`
**Depends on**: T2
**Reuses**: rate-limit constants from `Settings` (T2)
**Requirement**: CHAT-10

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [x] Unit tests cover: under-limit allowed, per-minute limit exceeded blocks, per-session limit exceeded blocks, and the per-minute window resets after time advances (inject a clock/monkeypatch `time.time`)
- [x] `cd backend && pytest tests/unit -q` passes; test count recorded (23 passed)

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): add per-session sliding-window rate limiter`

---

### T9: Implement `ConversationService.handle_turn`

**What**: The orchestration core - builds the message history (system prompt + prior turns from `MessageRepository` + new user message), calls the `LLMClient` with the `save_lead_info` tool, executes a returned tool call against `LeadRepository` (marking `has_contact=False` when contact fields are absent), tracks whether contact has already been asked this session, builds the `wa.me` URL once a lead is saved, and falls back to a canned PT-BR apology + WhatsApp link on `LLMUnavailableError` or on malformed tool-call arguments (never raising past this layer).
**Where**: `backend/app/domain/conversation.py`
**Depends on**: T5, T6, T7
**Reuses**: `LLMClient` (T4/T6), `save_lead_info`/prompt builder (T5), repositories (T7)
**Requirement**: CHAT-02, CHAT-05, CHAT-06, CHAT-07, CHAT-08, CHAT-09

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Unit tests (using `FakeLLMClient` + the T7 repositories against a temp SQLite file) cover, 1:1 with the ACs above: plain reply persisted as a message; contact asked at most once across two turns; declined contact still saves a lead with `has_contact=False`; a `save_lead_info` tool call persists a `Lead` with the right category/summary/contact and returns a non-null `whatsapp_url`; an `LLMUnavailableError` from the client yields the fallback reply + WhatsApp link without raising; a malformed/unparseable tool-call argument string falls back to treating the turn as plain text without raising
- [ ] `cd backend && pytest tests/unit -q` passes; test count recorded

**Tests**: unit
**Gate**: quick

**Commit**: `feat(backend): implement ConversationService turn orchestration`

---

### T10: Implement `POST /api/chat/message` router

**What**: The chat endpoint - Pydantic request/response schemas, calls `RateLimiter.check` before `ConversationService.handle_turn`, returns `429` when rate-limited, `422` on empty/≥1000-char messages (server-side defense in depth), `200 {reply, lead_captured, whatsapp_url}` otherwise, and logs each request's outcome (success/validation/rate-limited/upstream-fallback) with the session id.
**Where**: `backend/app/api/chat.py`
**Depends on**: T8, T9
**Reuses**: `RateLimiter` (T8), `ConversationService` (T9)
**Requirement**: CHAT-01, CHAT-03, CHAT-04, CHAT-08, CHAT-10, OBS-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Integration tests (FastAPI `TestClient`, `ConversationService` wired to `FakeLLMClient` via dependency override - never the real API) cover: happy-path `200` with a reply; empty message `422`; ≥1000-char message `422`; rate limit tripped `429`; `FakeLLMClient` configured to raise `LLMUnavailableError` still returns `200` with the fallback reply
- [ ] Each outcome is logged with the session id (asserted via a caplog/log-capture test)
- [ ] `cd backend && pytest -q` passes; test count recorded

**Tests**: integration
**Gate**: full

**Commit**: `feat(backend): add POST /api/chat/message endpoint`

---

### T11: Implement `GET /api/chat/{session_id}/history` route

**What**: Returns the persisted message list for a session id, oldest-first, as `{messages: [{role, content, created_at}]}`.
**Where**: `backend/app/api/chat.py`
**Depends on**: T7, T10
**Reuses**: `MessageRepository.get_history` (T7)
**Requirement**: OBS-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Integration test: unknown/new `session_id` returns an empty list; a session with prior messages (seeded via the T10 chat endpoint in the same test) returns them oldest-first
- [ ] `cd backend && pytest -q` passes; test count recorded

**Tests**: integration
**Gate**: full

**Commit**: `feat(backend): add GET /api/chat/{session_id}/history endpoint`

---

### T12: Implement `GET /api/leads` route + bearer-token auth

**What**: A FastAPI dependency validating `Authorization: Bearer <ADMIN_API_TOKEN>`, and the route returning `{leads: [...]}` (newest first) on success.
**Where**: `backend/app/api/leads.py`
**Depends on**: T2, T7
**Reuses**: `Settings.ADMIN_API_TOKEN` (T2), `LeadRepository.list_leads` (T7)
**Requirement**: LEAD-01, LEAD-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Integration tests: no `Authorization` header → `401`; wrong token → `401`; correct token → `200` with the leads created in the test setup, newest first
- [ ] `cd backend && pytest -q` passes; test count recorded

**Tests**: integration
**Gate**: full

**Commit**: `feat(backend): add GET /api/leads endpoint with bearer auth`

---

### T13: Wire FastAPI app (CORS, routers, request logging)

**What**: `backend/app/main.py` becomes the real app factory - registers the chat and leads routers, adds CORS middleware restricted to `Settings.ALLOWED_ORIGINS`, and configures basic structured logging (session id + outcome) for chat requests per OBS-02.
**Where**: `backend/app/main.py`
**Depends on**: T10, T11, T12
**Reuses**: routers from T10-T12, `Settings` from T2
**Requirement**: OBS-02

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] All three routes are reachable through the wired `app`
- [ ] A request from a disallowed origin does not receive CORS allow headers; a request from an allowed origin does (integration test)
- [ ] `cd backend && pytest -q` passes; test count recorded

**Tests**: integration
**Gate**: full

**Commit**: `feat(backend): wire routers, CORS, and request logging into the app`

---

### T14: Add chat widget markup + LGPD notice to `index.html`

**What**: Static markup for the floating action button and the (initially hidden) chat panel - message list container, input + send button, and the fixed LGPD consent line - placed alongside the existing header/nav markup, following the same structural style as the theme-toggle button.
**Where**: `index.html`
**Depends on**: None
**Reuses**: existing header/button markup patterns in `index.html`
**Requirement**: CHAT-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] FAB button and chat panel markup present, panel hidden by default (no JS behavior yet - that's T19)
- [ ] `data-i18n` attributes added for every piece of widget copy, matching the key names T16/T17 will add
- [ ] Existing page sections/markup untouched (diff limited to the new widget block + two new `<link>`/`<script>` tags added in T15/T18/T19)

**Tests**: none
**Gate**: build (`python3 -m http.server` smoke-load of `index.html`, or visual check - no automated HTML checker in this project)

**Commit**: `feat(widget): add chat widget markup and LGPD notice to index.html`

---

### T15: Add `css/chat-widget.css`

**What**: Theme-aware styling (light/dark via the existing `data-theme` attribute and CSS custom properties from `css/style.css`) for the FAB, panel, message bubbles, and consent notice; responsive down to ~400px width per the project's existing mobile support.
**Where**: `css/chat-widget.css`
**Depends on**: T14
**Reuses**: design tokens (colors, fonts, spacing) already defined in `css/style.css`
**Requirement**: CHAT-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Widget renders correctly (verified visually in the Phase-6 UAT) in both `data-theme="light"` and `data-theme="dark"`
- [ ] No existing selectors in `css/style.css` are modified
- [ ] `css/chat-widget.css` linked from `index.html`

**Tests**: none
**Gate**: build (visual check, folded into Phase-6 UAT)

**Commit**: `feat(widget): add chat-widget.css theming`

---

### T16: Add `chatWidget` i18n keys to `data/content-pt.json`

**What**: A `chatWidget` key block (greeting, placeholder, send button label, LGPD consent text, rate-limit message, offline/error message, WhatsApp handoff button label) in Portuguese.
**Where**: `data/content-pt.json`
**Depends on**: T14
**Reuses**: existing `data-i18n` key-naming convention in the file
**Requirement**: CHAT-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Every `data-i18n` key added in T14 has a matching entry under `chatWidget` in this file
- [ ] `python3 -m json.tool data/content-pt.json >/dev/null` exits 0

**Tests**: none
**Gate**: build

**Commit**: `feat(widget): add chatWidget i18n strings (pt)`

---

### T17: Add `chatWidget` i18n keys to `data/content-en.json`

**What**: The same `chatWidget` key block, translated to English (used for the widget's static chrome only - the bot's own replies stay Portuguese per the confirmed decision).
**Where**: `data/content-en.json`
**Depends on**: T14
**Reuses**: same key names as T16
**Requirement**: CHAT-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] Key set matches T16 exactly (same keys, English values)
- [ ] `python3 -m json.tool data/content-en.json >/dev/null` exits 0

**Tests**: none
**Gate**: build

**Commit**: `feat(widget): add chatWidget i18n strings (en)`

---

### T18: Implement `js/config.js`

**What**: A single `window.MORPHEUS_CONFIG = { apiBaseUrl: "..." }` (or ES module export), defaulting to `http://localhost:8000` for local dev, overridable without editing `chat-widget.js`.
**Where**: `js/config.js`
**Depends on**: None
**Reuses**: n/a
**Requirement**: n/a (infra)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `node --check js/config.js` exits 0
- [ ] Value is read by `chat-widget.js` in T19 rather than hardcoded there

**Tests**: none
**Gate**: build

**Commit**: `feat(widget): add frontend API base URL config`

---

### T19: Implement `js/chat-widget.js`

**What**: The widget's full client-side behavior - FAB toggle; session id in `sessionStorage` (create on first use); render greeting + LGPD notice on open; restore history via `GET /api/chat/{id}/history` on load if a session id exists; client-side validation (block empty send, reject ≥1000 chars inline) without calling the backend; `POST /api/chat/message` on send, disabling the input while the request is in flight; render all message content via `textContent` (never `innerHTML`); render the WhatsApp handoff button when `lead_captured`; render distinct fallback/offline and rate-limited (`429`) states.
**Where**: `js/chat-widget.js`
**Depends on**: T15, T16, T17, T18, T10, T11
**Reuses**: `js/config.js` (T18), the i18n pattern from `js/i18n.js`, markup from T14
**Requirement**: CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-06, CHAT-08, CHAT-09, CHAT-10, CHAT-11, CHAT-12, OBS-01

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `node --check js/chat-widget.js` exits 0
- [ ] Every behavior listed above is present in code (verified functionally in the Phase-6 manual UAT, per the confirmed no-JS-test-framework decision)
- [ ] `index.html` loads `js/config.js` and `js/chat-widget.js` after the existing `i18n.js`/`main.js`/`theme.js` script tags

**Tests**: none
**Gate**: build

**Commit**: `feat(widget): implement chat widget client logic`

---

### T20: Add backend Dockerfile/compose + run docs

**What**: A `backend/Dockerfile` (uvicorn entrypoint), a root `docker-compose.yml` running the backend service, and a short "Running the chatbot backend" section appended to `README.txt` (env vars required, `docker compose up` and bare-`uvicorn` instructions, where the SQLite file lands).
**Where**: `backend/Dockerfile`
**Depends on**: T13, T19
**Reuses**: `Settings` env var names from T2 (documented, not duplicated)
**Requirement**: n/a (ops/docs, closes the "clear architecture" / "sistema funcional" goals)

**Tools**:
- MCP: NONE
- Skill: NONE

**Done when**:
- [ ] `docker compose build` succeeds for the backend service (or, if Docker is unavailable in this environment, the Dockerfile is reviewed line-by-line against `requirements.txt`/`app/main.py` for correctness and flagged as unverified-by-build)
- [ ] README instructions are accurate against the actual env var names and default port used in the code

**Tests**: none
**Gate**: build

**Commit**: `chore(backend): add Dockerfile, compose service, and run docs`

---

## Phase Execution Map

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
```

| Phase | Tasks (execution order) | Exact edges |
| --- | --- | --- |
| Phase 1: Backend Foundation | T1, T2, T3, T4, T5, T6 | see Phase 1 diagram above |
| Phase 2: Persistence & Rate Limiting | T7, T8 | see Phase 2 diagram above |
| Phase 3: Domain Orchestration | T9 | see Phase 3 diagram above |
| Phase 4: API Layer | T10, T11, T12, T13 | see Phase 4 diagram above |
| Phase 5: Frontend Widget | T14, T15, T16, T17, T18, T19 | see Phase 5 diagram above |
| Phase 6: Ops & Docs | T20 | see Phase 6 diagram above |

Execution is strictly sequential - there is no intra-phase parallelism. A single agent (or batch worker) works one task at a time, in order, following the exact dependency edges drawn per-phase in the Execution Plan section above (this table only restates phase membership and order, not a second dependency graph).

---

## Task Granularity Check

| Task | Scope | Status |
| --- | --- | --- |
| T1: Scaffold backend + test tooling | 1 cohesive project skeleton (several boilerplate files, zero business logic) | ⚠️ OK if cohesive - project bootstrap, not splittable further without breaking `pytest` on day one |
| T2: Settings module | 1 file | ✅ Granular |
| T3: SQLModel models | 1 file | ✅ Granular |
| T4: LLMClient protocol + fake | 1 file | ✅ Granular |
| T5: Tool schema + prompt builder | 1 file | ✅ Granular |
| T6: MaritalkClient | 1 file | ✅ Granular |
| T7: Repositories | 1 file | ✅ Granular |
| T8: RateLimiter | 1 file | ✅ Granular |
| T9: ConversationService | 1 file | ✅ Granular |
| T10: POST /api/chat/message | 1 file | ✅ Granular |
| T11: GET history route | 1 file (same router file, additive) | ✅ Granular |
| T12: GET /api/leads | 1 file | ✅ Granular |
| T13: App wiring | 1 file | ✅ Granular |
| T14: Widget markup | 1 file | ✅ Granular |
| T15: Widget CSS | 1 file | ✅ Granular |
| T16: i18n (pt) | 1 file | ✅ Granular |
| T17: i18n (en) | 1 file | ✅ Granular |
| T18: Frontend config | 1 file | ✅ Granular |
| T19: Widget JS logic | 1 file | ✅ Granular |
| T20: Dockerfile/compose/docs | 1 primary file (Dockerfile) + 2 small companions (compose, README section) | ⚠️ OK if cohesive - one "make it runnable" concern |

---

## Diagram-Definition Cross-Check

| Task | Depends On (task body) | Diagram Shows | Status |
| --- | --- | --- | --- |
| T1 | None | None | ✅ Match |
| T2 | T1 | T1→T2 | ✅ Match |
| T3 | T1 | T1→T3 | ✅ Match |
| T4 | T1 | T1→T4 | ✅ Match |
| T5 | T2 | T2→T5 | ✅ Match |
| T6 | T2, T4 | T2→T6, T4→T6 | ✅ Match |
| T7 | T3 | T3→T7 | ✅ Match |
| T8 | T2 | T2→T8 | ✅ Match |
| T9 | T5, T6, T7 | T5→T9, T6→T9, T7→T9 | ✅ Match |
| T10 | T8, T9 | T8→T10, T9→T10 | ✅ Match |
| T11 | T7, T10 | T7→T11, T10→T11 | ✅ Match |
| T12 | T2, T7 | T2→T12, T7→T12 | ✅ Match |
| T13 | T10, T11, T12 | T10→T13, T11→T13, T12→T13 | ✅ Match |
| T14 | None | None | ✅ Match |
| T15 | T14 | T14→T15 | ✅ Match |
| T16 | T14 | T14→T16 | ✅ Match |
| T17 | T14 | T14→T17 | ✅ Match |
| T18 | None | None | ✅ Match |
| T19 | T15, T16, T17, T18, T10, T11 | T15→T19, T16→T19, T17→T19, T18→T19, T10→T19, T11→T19 | ✅ Match |
| T20 | T13, T19 | T13→T20, T19→T20 | ✅ Match |

---

## Test Co-location Validation

| Task | Code Layer Created/Modified | Matrix Requires | Task Says | Status |
| --- | --- | --- | --- | --- |
| T1 | Entity/config (app skeleton) | none | none | ✅ OK |
| T2 | Entity/config (settings) | none | none | ✅ OK |
| T3 | Entity/config (SQLModel models) | none | none | ✅ OK |
| T4 | LLM client (protocol + fake) | unit | unit | ✅ OK |
| T5 | Domain (tool schema + prompt) | unit | unit | ✅ OK |
| T6 | LLM client (Maritalk impl) | unit | unit | ✅ OK |
| T7 | Repository | unit | unit | ✅ OK |
| T8 | Domain (rate limiter) | unit | unit | ✅ OK |
| T9 | Domain (conversation service) | unit | unit | ✅ OK |
| T10 | API route | integration | integration | ✅ OK |
| T11 | API route | integration | integration | ✅ OK |
| T12 | API route | integration | integration | ✅ OK |
| T13 | Entity/config (app wiring) + touches API routes | integration (highest of the two layers touched) | integration | ✅ OK |
| T14 | Frontend widget | none (manual) | none | ✅ OK |
| T15 | Frontend widget | none (manual) | none | ✅ OK |
| T16 | Frontend widget | none (manual) | none | ✅ OK |
| T17 | Frontend widget | none (manual) | none | ✅ OK |
| T18 | Frontend widget | none (manual) | none | ✅ OK |
| T19 | Frontend widget | none (manual) | none | ✅ OK |
| T20 | Ops/docs | none | none | ✅ OK |

---

## Manual UAT (after all tasks + automatic Verifier - not a coded task)

Per the skill's Execute flow, feature-level validation is automatic (Verifier sub-agent) and does not need a task here. Because this feature has complex user-facing behavior (chat widget interaction), an **interactive UAT walkthrough** runs after the Verifier, covering: open widget → greeting + LGPD notice → free-text conversation → optional contact capture (both accepted and declined branches) → category assigned → lead visible via `GET /api/leads` → WhatsApp button opens the correct pre-filled `wa.me` link → empty-message block → ≥1000-char block → rate-limit trip → simulated MariTalk outage fallback → PT/EN toggle and theme toggle still work (CHAT-13) → mobile-width layout check.
