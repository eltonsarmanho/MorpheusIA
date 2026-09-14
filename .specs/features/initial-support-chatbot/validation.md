# Initial Support Chatbot Validation

**Date**: 2026-09-14
**Spec**: `.specs/features/initial-support-chatbot/spec.md`
**Diff range**: `0121da5..9e1fce1` (HEAD confirmed at `9e1fce1` before validation)
**Verifier**: independent sub-agent (author != verifier)

**Verdict**: ❌ FAIL — one AC behavior missing client-side (CHAT-09 WhatsApp button), one surviving mutant, one spec-precision gap. The backend is solid; every gap but one is in the untested frontend layer or in test strength, not in shipped backend behavior.

---

## Task Completion

All 20 tasks carry `[x]` on every "Done when" criterion in `tasks.md`; `grep -n '- \[ \]' tasks.md` returns nothing.

| Task | Status | Notes |
| ---- | ------ | ----- |
| T1-T13 | ✅ Done | Backend foundation → API layer, each with its own commit |
| T14-T19 | ✅ Done | Frontend widget; no automated tests by confirmed project decision |
| T20 | ✅ Done | Dockerfile self-flagged **unverified-by-build** (Docker daemon unreachable in the sandbox); `docker compose config` validated the YAML only |

One unplanned commit is present and legitimate: `5e8bd82 fix(backend): parse ALLOWED_ORIGINS as a comma-separated string`, a real bugfix to T13's own output, shipped with a regression test (`backend/tests/unit/test_config.py:11`) and an explanatory comment at `backend/app/core/config.py:28`.

---

## Spec-Anchored Acceptance Criteria

Backend ACs cite a pytest assertion. Frontend-only ACs cite the implementing `file:line` and are marked **[frontend, no automated assertion]** — the accepted evidence form for this feature's manual-UAT layer per the Test Coverage Matrix in `tasks.md`, not a gap.

### P1: Chat, understand, categorize, and hand off

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| CHAT-01 click FAB → panel opens with PT greeting + LGPD notice | Panel visible; PT greeting; consent notice | `js/chat-widget.js:109` FAB click → `openPanel()`; `js/chat-widget.js:96` `panel.hidden = false`; `index.html:98` greeting `<p data-i18n="chatWidget.greeting">`; `index.html:101` consent `<p data-i18n="chatWidget.consent">`; `css/chat-widget.css:59` `.chat-widget__panel:not([hidden]){display:flex}` **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-02 non-empty <1000 chars → send to backend, display PT reply | `200` + assistant reply rendered | `backend/tests/integration/test_chat_api.py:73` - `assert response.json() == {"reply": "Olá! Como posso ajudar?", "lead_captured": False, "whatsapp_url": None}`; `backend/tests/unit/test_conversation.py:44` - `assert result.reply == "Olá! Como posso ajudar?"`; render at `js/chat-widget.js:155` `appendMessage('assistant', data.reply)` | ✅ PASS (see note 1) |
| CHAT-03 empty message → blocked client-side, backend NOT called | No request issued; server-side `422` | `js/chat-widget.js:173` - `if (!text) return;` (returns before `sendMessage`) **[frontend]**; server defense-in-depth `backend/tests/integration/test_chat_api.py:127` - `assert response.status_code == 422` + `:128` `assert llm.calls == []` | ✅ PASS |
| CHAT-04 message ≥1000 chars → inline error, backend NOT called | Reject at exactly 1000; accept 999 | `js/chat-widget.js:175` - `if (text.length > MAX_MESSAGE_LENGTH)` with `MAX_MESSAGE_LENGTH = 999` (`js/chat-widget.js:5`) → `showStatus(validationEl)`, no fetch **[frontend]**; `backend/tests/integration/test_chat_api.py:147` - `assert response.status_code == 422` for `"a"*1000` + `:148` `assert llm.calls == []`; boundary `:163` - `assert response.status_code == 200` for `"a"*999` | ✅ PASS |
| CHAT-05 while no contact channel obtained → ask once per session | Ask instruction present until a contact is obtained; never more than once | `backend/tests/unit/test_conversation.py:69` - `assert llm.calls[0]["messages"][0]["content"] == build_system_prompt(contact_already_asked=False)` + `:72` `== build_system_prompt(contact_already_asked=True)`; `backend/tests/unit/test_tools.py:40` - `assert "pergunte UMA única vez" in prompt` / `:47` `not in prompt` | ⚠️ Condition divergence (gap 4) |
| CHAT-06 contact declined → conversation continues, Lead contact absent | `has_contact=False`, lead still saved, chat not blocked | `backend/tests/unit/test_conversation.py:106` - `assert leads[0].has_contact is False`; `:107` `assert leads[0].contact_phone is None`; `:110` `assert result.lead_captured is True`; `:114` `assert [m.role for m in history] == ["user", "assistant"]` | ✅ PASS |
| CHAT-07 enough info → classify into 4 categories or "Outro" + persist Lead (timestamp, category, summary, contact, full transcript) | Exact 5-value enum; Lead row with all listed fields | `backend/tests/unit/test_tools.py:17` - `assert category_schema["enum"] == ["gestao_empresas","whatsapp_atendimento","analise_documentos","gerador_conteudo","outro"]`; `backend/tests/unit/test_conversation.py:148` - `assert leads[0].category == "whatsapp_atendimento"` + `:149` `need_summary` + `:150-152` contact fields + `:153` `has_contact is True`; timestamps proven via ordering `backend/tests/unit/test_repository.py:66` - `assert [lead.session_id for lead in leads] == ["s3","s2","s1"]`; transcript `backend/tests/unit/test_conversation.py:49` - `assert [(m.role, m.content) for m in history] == [("user","oi"),("assistant","Olá! Como posso ajudar?")]` | ⚠️ Transcript deviation (gap 3) |
| CHAT-08 Lead created/updated → "Continuar no WhatsApp" button opening `wa.me/<numero>` pre-filled with a summary | URL `https://wa.me/<n>?text=<summary>` | `backend/tests/unit/test_conversation.py:157` - `assert result.whatsapp_url.startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")` + `:158` `assert quote(summary) in result.whatsapp_url`; `backend/tests/integration/test_chat_api.py:111` - `assert body["whatsapp_url"].startswith(...)`; render `js/chat-widget.js:156` `if (data.lead_captured) showWhatsapp(...)`, `js/chat-widget.js:90` sets `href` + unhides; `index.html:116` anchor | ✅ PASS |
| CHAT-09 MariTalk fails/times out → fallback message (apology **+ the WhatsApp button**), widget not stuck | Apology shown, WhatsApp button shown, loading state cleared | Backend ✅: `backend/tests/unit/test_conversation.py:170` - `assert result.reply.startswith("Desculpe")` + `:172` `assert result.whatsapp_url is not None`; `backend/tests/integration/test_chat_api.py:213` - `assert body["reply"].startswith("Desculpe")` + `:214` `assert body["whatsapp_url"].startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")`. Not-stuck ✅: `js/chat-widget.js:163` settling `.then(function(){ setBusy(false); ... })` runs on both paths. **Button ❌: no evidence** — `js/chat-widget.js:156` gates `showWhatsapp()` on `data.lead_captured`, which is `false` on this path (`backend/app/domain/conversation.py:115`) | ❌ GAP (gap 1) |
| CHAT-10 over rate limit (15/min or 60/session) → HTTP 429 + friendly widget message, no auto-retry | `429`; limits exactly 15/min and 60/session | `429` ✅: `backend/tests/integration/test_chat_api.py:191` - `assert response.status_code == 429` + `:192` `assert len(llm.calls) == 2` (fails fast before the LLM). Widget ✅: `js/chat-widget.js:146` - `if (res.status === 429) { showStatus(rateLimitEl); return null; }`, no retry path in the file; `index.html:106`. **Exact numbers ❌: no assertion** — blocking tests use `per_minute=3`/`per_session=2` (`backend/tests/unit/test_rate_limit.py:12,21`); 15/60 live only as unasserted defaults at `backend/app/core/config.py:37-38` | ⚠️ Spec-precision gap (gap 2) |
| CHAT-11 request in flight → further sends ignored/visibly disabled | Input + send disabled until response or error | `js/chat-widget.js:138` `setBusy(true)` before fetch; `js/chat-widget.js:84-85` `inputEl.disabled = isBusy; sendBtn.disabled = isBusy;`; re-enabled on both paths at `js/chat-widget.js:164`; visible styling `css/chat-widget.css:203` and `css/chat-widget.css:224` **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-12 all messages rendered as plain text, no HTML interpretation | `textContent` only, never `innerHTML` | `js/chat-widget.js:67` - `bubble.textContent = text;` is the sole content-writing path in `appendMessage()`, the single renderer used for user (`:137`), assistant (`:155`), and restored history (`:126`). `grep -n "innerHTML\|insertAdjacentHTML\|outerHTML" js/chat-widget.js` → no code hits. Static chrome goes through `js/i18n.js:58` `el.textContent = value` (the `innerHTML` branch at `js/i18n.js:56` needs `data-i18n-html`, which no widget element carries) **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-13 must not alter/break existing i18n toggle, theme toggle, nav, or page load | Existing site behavior unchanged | `git diff 0121da5..HEAD` does not touch `css/style.css`, `js/i18n.js`, `js/main.js`, or `js/theme.js` at all; `index.html` is purely additive (`index.html:26` one `<link>`, `index.html:80-125` the widget block, `index.html:416-417` two `<script>` tags — no existing markup modified or removed). Widget CSS is namespaced under `.chat-widget*` only. `js/chat-widget.js:19` early-returns if its nodes are absent and reads `window.MorpheusI18n` read-only (`js/chat-widget.js:26`) **[frontend, static analysis; runtime confirmation is UAT]** | ✅ PASS |

### P2: Lead retrieval for the sales team

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| LEAD-01 valid bearer token → leads list, most recent first | `200`, newest-first ordering | `backend/tests/integration/test_leads_api.py:93` - `assert response.status_code == 200` + `:95` `assert [lead["session_id"] for lead in body] == ["s2", "s1"]`; `:100` `assert body[0]["has_contact"] is True`; repo-level `backend/tests/unit/test_repository.py:66` | ✅ PASS |
| LEAD-02 missing/invalid token → HTTP 401, no lead data | `401` and zero lead data leaked | No header: `backend/tests/integration/test_leads_api.py:53` - `assert response.status_code == 401` + `:54` `assert "leads" not in response.json()` + `:55` `assert "segredo" not in response.text`. Wrong token: `:67`, `:68`, `:69` (same three) | ✅ PASS |

### P3: Session continuity and basic observability

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| OBS-01 reload within the same browser session → conversation restored from `sessionStorage` session id | Prior messages re-rendered, oldest-first | `backend/tests/integration/test_chat_api.py:249` - `assert [(m["role"], m["content"]) for m in messages] == [("user","oi"),("assistant","Olá! Como posso ajudar?"),("user","quero automatizar meu atendimento"),("assistant","Entendi, posso ajudar com isso.")]`; unknown session `:231` - `assert response.json() == {"messages": []}`; client side `js/chat-widget.js:198` reads the stored id and calls `restoreHistory()`, `js/chat-widget.js:118` GETs `/api/chat/{id}/history`, `js/chat-widget.js:35` `sessionStorage.getItem` | ✅ PASS |
| OBS-02 log each chat request + outcome (success, validation error, upstream failure, rate-limited) with the session id | All four outcomes logged with session id | success `backend/tests/integration/test_chat_api.py:78` - `assert any("outcome=success" in message and "session_id=s1" in message ...)`; validation `:129` and `:149` `"outcome=validation-error"`; rate-limited `:193` `"outcome=rate-limited"`; upstream `:216` `"outcome=upstream-fallback"`; wired-app variant `backend/tests/integration/test_app_wiring.py:124` | ✅ PASS |

**Status**: ❌ Gaps present — 13/17 ACs clean, 1 uncovered behavior (CHAT-09), 1 spec-precision gap (CHAT-10), 2 flagged divergences (CHAT-05, CHAT-07).

**Note 1 (CHAT-02, minor)**: the deterministic half (reply returned and rendered) is asserted exactly. The "Portuguese" half rests on the unasserted prompt line `backend/app/llm/tools.py:76` (`"Responda sempre em português do Brasil..."`). `build_system_prompt()` is deterministic and cheap to assert, but no test pins that line; `backend/tests/unit/test_tools.py` only asserts the contact-ask lines. Confirmable in UAT.

---

## Discrimination Sensor

Scratch: temporary git worktree at `<scratchpad>/sensor-wt` (`git worktree add … HEAD`), removed with `git worktree remove --force` + `git worktree prune`. No `git stash` at any point. Worktree baseline before mutating: 50 passed.

| Mutation | File:line | Description | Killed? |
| --- | --- | --- | --- |
| M1 | `backend/app/domain/conversation.py:90` | `contact_already_asked = any(message.role == "assistant" for message in history)` → `= False` (contact ask instruction re-sent every turn) | ✅ Killed — `test_conversation.py::test_contact_is_asked_at_most_once_across_two_turns` (1 failed, 7 passed) |
| M2 | `backend/app/api/rate_limit.py:35` and `:41` | Both thresholds `>=` → `>` (off-by-one: one extra message allowed past each limit) | ✅ Killed — 5 failed, 10 passed (4 in `test_rate_limit.py` + `test_chat_api.py::test_request_over_the_rate_limit_returns_429_before_calling_the_service`) |
| M3 | `backend/app/domain/conversation.py:116` | `whatsapp_url=self._whatsapp_url(_WHATSAPP_FALLBACK_TEXT)` → `whatsapp_url=None` on the `LLMUnavailableError` path (required side effect removed) | ✅ Killed — 2 failed, 16 passed (`test_conversation.py::test_llm_unavailable_returns_fallback_reply_and_whatsapp_url`, `test_chat_api.py::test_llm_unavailable_returns_200_with_fallback_reply`) |
| M4 | `backend/app/domain/conversation.py:62-66` | Deleted the required-field loop in `_parse_lead_arguments()` so malformed `save_lead_info` arguments are no longer rejected | ❌ **Survived** — 18 passed, 0 failed |

**M4 root cause (confirmed, not inferred)**: with the guard removed, `lead_fields["need_summary"]` raises `KeyError` — but that expression sits *inside* the `try:` at `backend/app/domain/conversation.py:133-143`, so the broad `except Exception` at `:144` swallows it, logs `lead persistence failed: session_id=s1 error='need_summary'`, and returns `TurnResult(reply=reply, lead_captured=False)`. That is observationally identical to the intended "treat as plain text" outcome on every axis the test checks. Verified by re-running the single test under the mutant with `--log-cli-level=ERROR`: it passes while emitting the wrong log line.

`backend/tests/unit/test_conversation.py:197` (`test_tool_arguments_missing_required_field_fall_back_to_plain_text`) asserts only `reply`, `lead_captured is False`, and `list_leads() == []` — all three coincide between the correct path and the masked-crash path. Nothing distinguishes "extraction declined cleanly, retry next turn" from "extraction crashed and was swallowed".

**Sensor depth**: lightweight (4 behavior-level mutations on the highest-risk new code)
**Result**: 3/4 killed — ❌ FAIL

**Isolation verified**: real-tree `git status --porcelain` after cleanup is byte-identical to the pre-sensor baseline — exactly ` M .gitignore` and `?? AcessoHostinger.txt` (both pre-existing, unrelated to this feature, untouched). `git worktree list` shows only the main worktree. HEAD still `9e1fce1`.

---

## Interactive UAT Results

⏭️ **Deferred** — not performed in this Verifier run (no live backend process and no browser available to the Verifier). Interactive UAT is the orchestrator's/user's follow-up step, using the script in `tasks.md` → "Manual UAT". It is the only way to confirm the frontend-only ACs (CHAT-01, 11, 12, 13), the widget's rendering of the CHAT-09 fallback, and the CHAT-02 Portuguese-reply behavior at runtime.

Add one item to that script that it does not currently contain: **simulate a MariTalk outage and confirm whether the "Continuar no WhatsApp" button appears** (gap 1 predicts it will not).

---

## Code Quality

| Principle | Status |
| --- | --- |
| Minimum code | ✅ No speculative abstraction; `LLMClient` Protocol is the one indirection and it is mandated by design.md |
| Surgical changes | ✅ `css/style.css`, `js/i18n.js`, `js/main.js`, `js/theme.js` untouched; `index.html` and `README.txt` additive only |
| No scope creep | ✅ Nothing beyond T1-T20; the extra commit `5e8bd82` is a bugfix to this feature's own code with a regression test |
| No cross-batch tampering | ✅ Walked all 22 commits' file lists: each touches only its own task's files plus the `tasks.md` checkbox. The two `index.html` re-touches (`9336f36` `<link>`, `1562a5d` `<script>` tags) are explicitly pre-authorized by T14's Done-when. `5e8bd82` re-touches T13's `main.py`/`test_app_wiring.py` to fix T13's own bug — legitimate |
| Spec not weakened to fit code | ✅ Every `spec.md` diff in range is traceability-column bookkeeping; no AC text was edited |
| Matches patterns | ✅ Widget JS mirrors `js/theme.js`/`js/i18n.js` (IIFE, `'use strict'`, `var`, no framework); backend is plain FastAPI + SQLModel per AD-001 |
| Would a senior engineer approve? | ✅ For the backend. The broad `except Exception` at `backend/app/domain/conversation.py:144` is deliberate (design.md Error Handling) but is what masked M4 — narrowing it, or hoisting the two `lead_fields[...]` lookups out of the `try`, would remove the blind spot |
| Spec-anchored outcome check | ⚠️ 1 spec-precision gap (CHAT-10 exact 15/60 unasserted) |
| Per-layer Coverage Expectation met | ✅ Domain 1:1 to ACs; every route in scope covers happy + edge + error (`/api/chat/message`: 200, 422 empty, 422 ≥1000, 999 boundary, 429, upstream-fallback 200; `/api/chat/{id}/history`: empty, oldest-first, session-scoped; `/api/leads`: 401 no header, 401 wrong token, 200 newest-first) |
| Every test maps to a spec requirement — no unclaimed tests | ✅ All 50 accounted for: test_config 2 (bugfix regression), test_llm_client 3 (T4), test_maritalk_client 3 (T6), test_rate_limit 5 (T8), test_repository 7 (T7), test_tools 5 (T5), test_conversation 8 (T9), test_chat_api 10 (T10/T11), test_leads_api 3 (T12), test_app_wiring 4 (T13) |
| Documented guidelines followed | ✅ None exist in the repo (`tasks.md` Test Coverage Matrix: "Guidelines found: none") — strong defaults applied |
| Secret hygiene | ✅ `MARITALK_API_KEY` and `ADMIN_API_TOKEN` are `SecretStr` (`backend/app/core/config.py:21,25`); token comparison is constant-time via `secrets.compare_digest` (`backend/app/api/leads.py:36`); no test reads the real `.env` |

---

## Edge Cases

- [x] **Malformed/non-JSON tool-call payload** → falls back to plain text without crashing: `backend/tests/unit/test_conversation.py:180` (unparseable JSON) - `assert result.reply == "Certo, me conta mais sobre o seu processo."` + `:194` `assert await LeadRepository(engine=engine).list_leads() == []`. Covered, **but the companion missing-required-field case at `:197` is non-discriminating** (see M4). The "SHALL retry structured extraction on the next turn" half is implied by the unconditional tool pass at `backend/app/domain/conversation.py:103` and asserted only indirectly (`backend/tests/unit/test_conversation.py:53` - `assert llm.calls[0]["tools"] == [SAVE_LEAD_INFO_TOOL]`); no test drives two turns to confirm the retry.
- [x] **Backend unreachable → offline state distinct from "thinking"**: `js/chat-widget.js:160` - `.catch(function () { showStatus(offlineEl); })`, distinct from the rate-limit state (`rateLimitEl`, `:147`) and from the busy state (disabled controls, `:84-85`); `index.html:109`; `data/content-pt.json` `chatWidget.offline`. **[frontend, no automated assertion]** Note: the "assistant is thinking" state is the disabled input/send pair, not a typing indicator — design.md mentions a "digitando..." wait that was not built. The AC's requirement (distinctness) is met; the affordance is thinner than designed.
- [x] **Two tabs → two independent sessions**: `sessionStorage` is per-tab by definition, and the widget keys off it (`js/chat-widget.js:4` `SESSION_STORAGE_KEY`, `:49` `getOrCreateSessionId`). Backend independence asserted: `backend/tests/integration/test_chat_api.py:267` - `assert [m["content"] for m in messages] == ["aba um", "Olá!"]`; `backend/tests/unit/test_repository.py:105` - `assert [m.content for m in history] == ["mensagem s1"]`.
- [x] **Lead persistence fails after a successful reply → reply still returned, failure logged**: `backend/tests/unit/test_conversation.py:243` - `assert result.reply == "Anotado! Vou encaminhar para o time."` + `:244` `assert result.lead_captured is False` + `:245` `assert any("s1" in record.getMessage() for record in caplog.records)` at ERROR level. Covered — though the log assertion checks only that the session id appears, not *which* failure occurred, which is the same looseness that let M4 survive.

---

## Gate Check

- **Build gate (backend)**: `cd backend && .venv/bin/pytest -q`
- **Result**: **50 passed**, 0 failed, 0 skipped, 1 warning (third-party `anyio.abc.BlockingPortal` DeprecationWarning from starlette's testclient — not this feature's code)
- **Test count before feature**: 0 (no backend or test tooling existed in the repo before `0121da5..HEAD`)
- **Test count after feature**: 50
- **Delta**: +50
- **Skipped tests**: none
- **Failures**: none
- **Test integrity**: no test deleted, skipped, or weakened; no pre-existing assertions to regress. `backend/tests/conftest.py:10` maps pytest exit code 5 (no tests collected) to 0 — scaffolding-era accommodation, inert now that 50 tests collect, and it cannot mask a failure (only exit code 5 is remapped).

**Frontend gates** (this feature's only frontend gate, per the Test Coverage Matrix):

| Command | Result |
| --- | --- |
| `node --check js/chat-widget.js` | ✅ exit 0 |
| `node --check js/config.js` | ✅ exit 0 |
| `python3 -m json.tool data/content-pt.json` | ✅ exit 0 |
| `python3 -m json.tool data/content-en.json` | ✅ exit 0 |

**Contract check (frontend ↔ backend field names)** — read from `backend/app/api/chat.py`, not assumed:

| Direction | Backend | Widget | Match |
| --- | --- | --- | --- |
| POST body | `ChatMessageRequest{session_id, message}` (`backend/app/api/chat.py:28-30`) | `js/chat-widget.js:143` `JSON.stringify({ session_id: sessionId, message: text })` | ✅ |
| POST response | `ChatMessageResponse{reply, lead_captured, whatsapp_url}` (`backend/app/api/chat.py:33-36`) | `js/chat-widget.js:155,156,157` `data.reply`, `data.lead_captured`, `data.whatsapp_url` | ✅ |
| History response | `HistoryResponse{messages:[{role, content, created_at}]}` (`backend/app/api/chat.py:39-46`) | `js/chat-widget.js:124-125` `data.messages`, `message.role`, `message.content` | ✅ |
| Routes | `/api/chat/message`, `/api/chat/{session_id}/history` (`backend/app/api/chat.py:21,97,125`) | `js/chat-widget.js:140`, `js/chat-widget.js:118` | ✅ |
| CORS | `allow_methods=["GET","POST"]`, `allow_headers=["Authorization","Content-Type"]` (`backend/app/main.py:36-37`) | widget sends `Content-Type: application/json` (`js/chat-widget.js:142`) | ✅ |

---

## Fix Plans

### Fix 1 — CHAT-09: WhatsApp button never appears on the LLM-outage fallback (Blocker for the AC, Major overall)

- **Root cause**: `js/chat-widget.js:156` gates `showWhatsapp(data.whatsapp_url)` on `data.lead_captured`. On the fallback path the backend deliberately returns `lead_captured=False` with a **non-null** `whatsapp_url` (`backend/app/domain/conversation.py:113-117`), so the widget discards a URL the backend went out of its way to supply, and the `#chatWidgetWhatsapp` anchor stays `hidden` (`index.html:116`). The visitor gets the apology text only. The offline `<p>` is not shown either, because the response is a `200`.
- **Fix task**: in `js/chat-widget.js`, key the handoff button on the presence of the URL rather than on lead capture — `if (data.whatsapp_url) showWhatsapp(data.whatsapp_url);`. This keeps CHAT-08 satisfied (lead captures always carry a URL) and satisfies CHAT-09 without a second branch.
- **Verify**: with the backend forced into the `LLMUnavailableError` path, send a message and confirm the apology bubble *and* the "Continuar no WhatsApp" button both render, and the input re-enables.
- **Done when**: CHAT-09's "apology + the WhatsApp button" both observable in the widget during UAT.
- **Priority**: Major

### Fix 2 — CHAT-10: the spec's exact limits (15/min, 60/session) are not pinned by any test (Minor)

- **Root cause**: every blocking test constructs the limiter with scaled-down thresholds (`backend/tests/unit/test_rate_limit.py:12` `per_minute=3`, `:21` `per_session=2`; `backend/tests/integration/test_chat_api.py:175` `per_minute=2`). The spec's numbers exist only as unasserted defaults at `backend/app/core/config.py:37-38`, and `backend/tests/unit/test_config.py` covers `ALLOWED_ORIGINS` only. Editing 15 → 150 breaks nothing.
- **Fix task**: add to `backend/tests/unit/test_config.py` — `assert Settings(_env_file=None).RATE_LIMIT_PER_MINUTE == 15` and `== 60` for `RATE_LIMIT_PER_SESSION` (same `monkeypatch` env pattern as the existing tests), and assert `chat.get_rate_limiter()` wires those values through.
- **Done when**: changing either default fails a test.
- **Priority**: Minor

### Fix 3 — M4 surviving mutant: the malformed-arguments test cannot see the guard disappear (Major, test strength)

- **Root cause**: `backend/tests/unit/test_conversation.py:197` asserts only outcomes that the masked-crash path also produces. The broad `except Exception` at `backend/app/domain/conversation.py:144` spans the `lead_fields[...]` lookups at `:139-140`, converting a validation bug into a "persistence failure".
- **Fix task**: two parts. (a) Strengthen the test to assert the distinguishing observable — e.g. `caplog` contains the `"malformed save_lead_info arguments"` warning and **not** `"lead persistence failed"`; or inject a spy `LeadRepository` and assert `upsert_lead` was never called. (b) Optionally narrow the blast radius: hoist `category = lead_fields["category"]` / `need_summary = lead_fields["need_summary"]` above the `try`, so only genuine storage errors reach the persistence-failure branch.
- **Verify**: re-run mutation M4 (delete the required-field loop) and confirm the suite now fails.
- **Done when**: M4 is killed.
- **Priority**: Major

### Fix 4 — CHAT-05: "at most once" is implemented against a proxy condition (Minor, needs a product call)

- **Root cause**: the spec conditions the ask on *"WHILE the assistant has not yet obtained a contact channel (phone or email) for the current session"*. `backend/app/domain/conversation.py:90` implements it as `any(message.role == "assistant" for message in history)` — i.e. "the assistant has replied at least once", which is not the same predicate. The spec's upper bound (never more than once) holds and is asserted. The lower bound does not: the ask instruction is present on exactly the first LLM turn and dropped thereafter, whether or not a contact was ever obtained or even requested. If the model does not ask on that one turn, it never will.
- **Fix task**: either (a) derive the flag from the actual state — `contact_already_asked = (await lead_repo.get(session_id)).has_contact` or an explicit per-session "asked" flag — so the instruction persists until a contact is obtained or explicitly declined, with a test asserting the two-turn behavior in both branches; or (b) amend the AC to describe the shipped rule ("ask on the first assistant turn only") if that is the intended product behavior.
- **Priority**: Minor (needs an orchestrator/user decision on which way to resolve)

### Fix 5 — CHAT-07: the "full transcript" is not part of the Lead record and is not reachable from the leads API (Minor, needs a product call)

- **Root cause**: the AC says the Lead record shall contain *"timestamp, category, a short summary, the contact info if provided, and the full transcript."* `Lead` (`backend/app/storage/models.py:18-28`) has no transcript field; the transcript lives in the separate `Message` table correlated by `session_id`. design.md's Data Models section made this normalization deliberately, so spec and approved design disagree on the literal wording. Practical consequence: `GET /api/leads` returns `LeadsResponse{leads: [Lead]}` (`backend/app/api/leads.py:16-17`) with no transcript and no documented join path; the only transcript route, `GET /api/chat/{session_id}/history`, is **unauthenticated**, so the sales team either cannot see the conversation or reads it over an open endpoint.
- **Fix task**: pick one — (a) include the transcript (or a `messages` sub-array) in the `GET /api/leads` response; (b) amend CHAT-07 to say the transcript is persisted alongside the Lead, correlated by `session_id`, and document the retrieval path. Either way, consider whether `GET /api/chat/{session_id}/history` should stay unauthenticated now that it is the only transcript surface (it is guessable only with the session UUID, so exposure is limited, but it was never security-reviewed as the lead-data path).
- **Priority**: Minor (needs an orchestrator/user decision)

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| --- | --- | --- |
| CHAT-01 | Implementing | ✅ Verified |
| CHAT-02 | Implementing | ✅ Verified |
| CHAT-03 | Implementing | ✅ Verified |
| CHAT-04 | Implementing | ✅ Verified |
| CHAT-05 | Implementing | ❌ Needs Fix (condition divergence — Fix 4) |
| CHAT-06 | Implementing | ✅ Verified |
| CHAT-07 | Implementing | ❌ Needs Fix (transcript deviation — Fix 5) |
| CHAT-08 | Implementing | ✅ Verified |
| CHAT-09 | Implementing | ❌ Needs Fix (WhatsApp button — Fix 1) |
| CHAT-10 | Implementing | ❌ Needs Fix (exact limits unasserted — Fix 2) |
| CHAT-11 | Implementing | ✅ Verified |
| CHAT-12 | Implementing | ✅ Verified |
| CHAT-13 | In Tasks (stale) | ✅ Verified |
| LEAD-01 | Implementing | ✅ Verified |
| LEAD-02 | Implementing | ✅ Verified |
| OBS-01 | Implementing | ✅ Verified |
| OBS-02 | Implementing | ✅ Verified |

13 Verified, 4 Needs Fix. (CHAT-13's previous status was `In Tasks` while every sibling read `Implementing` — stale bookkeeping from the Tasks phase, corrected here.)

---

## Summary

**Overall**: ❌ Not Ready

**Spec-anchored check**: 13/17 ACs matched the spec outcome cleanly; 1 uncovered behavior, 1 spec-precision gap, 2 flagged divergences
**Sensor**: 3/4 mutations killed (M4 survived)
**Gate**: 50 passed, 0 failed; all 4 frontend syntax/JSON gates green

**What works**: the backend is genuinely well built. Every route covers happy, edge, and error paths; OBS-02's four log outcomes are each asserted by exact literal; the CHAT-04 boundary is pinned on both sides (999 accepted, 1000 rejected); LEAD-02 asserts not just the 401 but that no lead data leaks into the body; secrets are `SecretStr` and the admin token comparison is constant-time; no test touches the real MariTalk API or the dev database. Commit discipline is clean across all three independent implementers, with no cross-batch tampering and no spec text softened to fit the code. The widget genuinely honors CHAT-12 (`textContent` only, zero `innerHTML`), CHAT-11 (disabled controls while in flight), and CHAT-13 (purely additive diff; the four pre-existing site scripts and `style.css` are untouched).

**Issues found**:

1. **CHAT-09's WhatsApp button never renders on an LLM outage** — `js/chat-widget.js:156` gates it on `lead_captured`, which is `false` exactly on that path. The backend supplies the URL; the widget throws it away. This is the one gap where shipped behavior misses a spec requirement outright, and it sits in the layer with no automated tests, which is why it survived to here.
2. **M4 survived** — the broad `except Exception` at `backend/app/domain/conversation.py:144` masks a missing-validation bug as a persistence failure, and the test at `:197` asserts nothing that distinguishes them.
3. **CHAT-10's exact 15/60 limits are unasserted** — the limiter mechanism is well tested at arbitrary thresholds, but the spec's numbers are free to drift.
4. **CHAT-05 and CHAT-07 diverge from their AC wording** — both are defensible design calls, but neither was recorded as a deviation, so they need an explicit keep-or-amend decision rather than silent acceptance.

**Next steps**: route Fixes 1-3 to an implementer (Fix 1 is a one-line change and the only shipped-behavior defect). Fixes 4 and 5 need an orchestrator/user decision on whether to change the code or amend the AC — do not let an implementer pick unilaterally. Then re-verify (iteration 1 of the 3-iteration bound). Interactive UAT remains outstanding and should run after Fix 1 lands, with the MariTalk-outage step added to the script.
