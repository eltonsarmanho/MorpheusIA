# Initial Support Chatbot Validation (re-verification, iteration 1/3)

**Date**: 2026-09-14
**Spec**: `.specs/features/initial-support-chatbot/spec.md`
**Diff range**: full feature `0121da5..84a9572`; **fix range under re-verification `8d36528..84a9572`** (HEAD confirmed at `84a9572` before and after)
**Verifier**: independent sub-agent (author ≠ verifier; did not write the code or the fixes)

**Verdict**: ✅ **PASS** — all 5 gaps from the prior report are resolved. 17/17 ACs now carry evidence matching the spec-defined outcome, the surviving mutant M4 is killed, and 4 additional mutations aimed at the fixed code were all killed. Five non-blocking advisories are recorded below; none contradicts an AC.

> **Supersedes** the prior FAIL report of the same date (diff range `0121da5..9e1fce1`). This file replaces it in full. Each gap section below carries a **Prior finding** note showing what changed.

---

## What changed since the prior report

Four commits, all after the prior validation commit `8d36528`. Diffs read directly, not taken from the summary:

| Commit | Files | Nature |
| --- | --- | --- |
| `e319d82` | `js/chat-widget.js` (+3/-1) | Code fix — CHAT-09 |
| `67a3cae` | `backend/tests/unit/test_config.py` (+27) | Test strengthening — CHAT-10 |
| `0c6efd3` | `backend/app/domain/conversation.py` (+8/-2), `backend/tests/unit/test_conversation.py` (+24/-3) | Code restructure + test strengthening — mutant M4 |
| `84a9572` | `.specs/.../spec.md`, `.specs/LESSONS.md`, `.specs/lessons.json` | Spec wording amendment — CHAT-05, CHAT-07; lessons store |

`git diff --name-only 8d36528..HEAD` returns exactly 7 files: 4 above plus the three `.specs` files. **No application file outside `backend/app/domain/conversation.py` and `js/chat-widget.js` was touched.** `css/`, `index.html`, `data/`, `js/i18n.js`, `js/main.js`, `js/theme.js`, `backend/app/api/**`, `backend/app/storage/**`, `backend/app/llm/**` are all untouched in the fix range.

---

## Task Completion

Unchanged from the prior pass. `grep -n '- \[ \]' tasks.md` returns nothing; all 20 tasks carry `[x]` on every "Done when" criterion. T20's Dockerfile remains self-flagged **unverified-by-build** (no Docker daemon in this sandbox) — pre-existing, outside the fix range.

---

## Spec-Anchored Acceptance Criteria

Backend ACs cite a pytest assertion. Frontend-only ACs cite the implementing `file:line` and are marked **[frontend, no automated assertion]**. That evidence form is not an inherited assumption — it is verified at `tasks.md:27` (Test Coverage Matrix, "Frontend widget … none (manual) … *confirmed with the user*") and `tasks.md:37` (frontend Build gate = `node --check` / `json.tool`).

Rows re-derived this run are marked **↻**; the rest carry forward from the prior pass, where they passed cleanly and nothing in the fix range touches the code they depend on (verified: see "Spot-check — did the fixes disturb the clean 13?" below).

### P1: Chat, understand, categorize, and hand off

| Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| CHAT-01 click FAB → panel opens with PT greeting + LGPD notice | Panel visible; PT greeting; consent notice | `js/chat-widget.js:109` FAB click → `openPanel()`; `js/chat-widget.js:96` `panel.hidden = false`; `index.html:98` greeting; `index.html:101` consent; `css/chat-widget.css:59` **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-02 non-empty <1000 chars → send to backend, display PT reply | `200` + assistant reply rendered | `backend/tests/integration/test_chat_api.py:73` - `assert response.json() == {"reply": "Olá! Como posso ajudar?", "lead_captured": False, "whatsapp_url": None}`; `backend/tests/unit/test_conversation.py:44` - `assert result.reply == "Olá! Como posso ajudar?"`; render at `js/chat-widget.js:155` | ✅ PASS (see note 1) |
| CHAT-03 empty message → blocked client-side, backend NOT called | No request issued; server-side `422` | `js/chat-widget.js:175` - `if (!text) return;` **[frontend]**; `backend/tests/integration/test_chat_api.py:127` - `assert response.status_code == 422` + `:128` `assert llm.calls == []` | ✅ PASS |
| CHAT-04 message ≥1000 chars → inline error, backend NOT called | Reject at exactly 1000; accept 999 | `js/chat-widget.js:177` - `if (text.length > MAX_MESSAGE_LENGTH)` with `MAX_MESSAGE_LENGTH = 999` (`js/chat-widget.js:5`) **[frontend]**; `backend/tests/integration/test_chat_api.py:147` - `assert response.status_code == 422`; boundary `:163` - `assert response.status_code == 200` for `"a"*999` | ✅ PASS |
| **↻ CHAT-05** (amended AC) instruction to ask for a contact channel included in the first reply's turn, **excluded from every later turn** of the session | Ask instruction present in turn 1's system prompt, absent in turn 2+ | `backend/tests/unit/test_conversation.py:69` - `assert llm.calls[0]["messages"][0]["content"] == build_system_prompt(contact_already_asked=False)` + `:72` `== build_system_prompt(contact_already_asked=True)`; `backend/tests/unit/test_tools.py:40` - `assert "pergunte UMA única vez" in prompt` / `:47` `not in prompt`; implementation `backend/app/domain/conversation.py:90` | ✅ **PASS** (was ⚠️) |
| CHAT-06 contact declined → conversation continues, Lead contact absent | `has_contact=False`, lead still saved, chat not blocked | `backend/tests/unit/test_conversation.py:106` - `assert leads[0].has_contact is False`; `:107` `contact_phone is None`; `:110` `assert result.lead_captured is True`; `:114` `assert [m.role for m in history] == ["user", "assistant"]` | ✅ PASS |
| **↻ CHAT-07** (amended AC) classify into 4 categories or "Outro" + persist Lead (timestamp, category, summary, contact); **transcript persisted and retrievable by the same session id, correlated to the Lead** | Exact 5-value enum; Lead row with listed fields; transcript reachable via `session_id` | Enum `backend/tests/unit/test_tools.py:17` - `assert category_schema["enum"] == ["gestao_empresas","whatsapp_atendimento","analise_documentos","gerador_conteudo","outro"]`; Lead fields `backend/tests/unit/test_conversation.py:148-153`; timestamps `backend/app/storage/models.py:27-28` `created_at`/`updated_at`, ordering proven `backend/tests/unit/test_repository.py:66`; **correlation key in the API response** `backend/tests/integration/test_leads_api.py:95` - `assert [lead["session_id"] for lead in body] == ["s2", "s1"]`; **transcript retrieval by that key** `backend/tests/integration/test_chat_api.py:249` - `assert [(m["role"], m["content"]) for m in messages] == [("user","oi"),("assistant","Olá! Como posso ajudar?"),…]` | ✅ **PASS** (was ⚠️) |
| CHAT-08 Lead created/updated → "Continuar no WhatsApp" button opening `wa.me/<numero>` pre-filled with a summary | URL `https://wa.me/<n>?text=<summary>` | `backend/tests/unit/test_conversation.py:157` - `assert result.whatsapp_url.startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")` + `:158` `assert quote(summary) in result.whatsapp_url`; `backend/tests/integration/test_chat_api.py:111`; render `js/chat-widget.js:158` `if (data.whatsapp_url) showWhatsapp(...)`, `js/chat-widget.js:90` sets `href` + unhides; `index.html:116` | ✅ PASS (regate re-checked — see CHAT-09) |
| **↻ CHAT-09** MariTalk fails/times out → fallback message (apology **+ the WhatsApp button**), widget not stuck | Apology shown, WhatsApp button shown, loading state cleared | Backend ✅: `backend/tests/unit/test_conversation.py:170` - `assert result.reply.startswith("Desculpe")` + `:172` `assert result.whatsapp_url is not None`; `backend/tests/integration/test_chat_api.py:214` - `assert body["whatsapp_url"].startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")`. **Button ✅ (fixed)**: `js/chat-widget.js:158` - `if (data.whatsapp_url) { showWhatsapp(data.whatsapp_url); }`, reached on the fallback path because it is a `200` with a non-null URL (`backend/app/domain/conversation.py:113-117`) **[frontend, no automated assertion]**. Not-stuck ✅: `js/chat-widget.js:165` settling `.then(function(){ setBusy(false); … })` runs on both paths | ✅ **PASS** (was ❌) |
| **↻ CHAT-10** over rate limit (15/min or 60/session) → HTTP 429 + friendly widget message, no auto-retry | `429`; limits exactly 15/min and 60/session | `429` ✅: `backend/tests/integration/test_chat_api.py:191` - `assert response.status_code == 429` + `:192` `assert len(llm.calls) == 2`. **Exact numbers ✅ (fixed)**: `backend/tests/unit/test_config.py:53` - `assert settings.RATE_LIMIT_PER_MINUTE == 15` + `:54` `assert settings.RATE_LIMIT_PER_SESSION == 60`; wire-through `backend/tests/unit/test_config.py:64` - `assert limiter._per_minute == 15` + `:65` `assert limiter._per_session == 60`. Widget ✅: `js/chat-widget.js:146` - `if (res.status === 429) { showStatus(rateLimitEl); return null; }`, no retry path in the file; `index.html:106` | ✅ **PASS** (was ⚠️) |
| CHAT-11 request in flight → further sends ignored/visibly disabled | Input + send disabled until response or error | `js/chat-widget.js:138` `setBusy(true)`; `js/chat-widget.js:84-85` `inputEl.disabled = isBusy; sendBtn.disabled = isBusy;`; re-enabled `js/chat-widget.js:166`; styling `css/chat-widget.css:203,224` **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-12 all messages rendered as plain text, no HTML interpretation | `textContent` only, never `innerHTML` | `js/chat-widget.js:67` - `bubble.textContent = text;` is the sole content-writing path in `appendMessage()`, the single renderer for user (`:137`), assistant (`:155`), restored history (`:125`). `grep -n "innerHTML\|insertAdjacentHTML\|outerHTML" js/chat-widget.js` → no hits (re-run this pass) **[frontend, no automated assertion]** | ✅ PASS |
| CHAT-13 must not alter/break existing i18n, theme, nav, or page load | Existing site behavior unchanged | Prior evidence stands; additionally `git diff --name-only 8d36528..HEAD` confirms the fix range touched **no** frontend file except `js/chat-widget.js`, and that change is a 1-line condition inside the widget IIFE **[frontend, static analysis; runtime confirmation is UAT]** | ✅ PASS |

### P2: Lead retrieval for the sales team

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| LEAD-01 valid bearer token → leads list, most recent first | `200`, newest-first ordering | `backend/tests/integration/test_leads_api.py:93` - `assert response.status_code == 200` + `:95` `assert [lead["session_id"] for lead in body] == ["s2", "s1"]`; `:100` `assert body[0]["has_contact"] is True`; repo-level `backend/tests/unit/test_repository.py:66` | ✅ PASS |
| LEAD-02 missing/invalid token → HTTP 401, no lead data | `401` and zero lead data leaked | No header: `backend/tests/integration/test_leads_api.py:53` - `assert response.status_code == 401` + `:54` `assert "leads" not in response.json()` + `:55` `assert "segredo" not in response.text`. Wrong token: `:67`, `:68`, `:69` | ✅ PASS |

### P3: Session continuity and basic observability

| Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --- | --- | --- |
| OBS-01 reload within the same browser session → conversation restored | Prior messages re-rendered, oldest-first | `backend/tests/integration/test_chat_api.py:249` (exact 4-tuple list); unknown session `:231` - `assert response.json() == {"messages": []}`; client `js/chat-widget.js:200-203`, `:118`, `:35` | ✅ PASS |
| OBS-02 log each chat request + outcome with the session id | All four outcomes logged with session id | success `backend/tests/integration/test_chat_api.py:78`; validation `:129`, `:149` `"outcome=validation-error"`; rate-limited `:193`; upstream `:216` `"outcome=upstream-fallback"`; wired-app `backend/tests/integration/test_app_wiring.py:124` | ✅ PASS |

**Status**: ✅ **All 17 ACs covered and matched to the spec-defined outcome.** 0 gaps, 0 spec-precision gaps.

**Note 1 (CHAT-02, carried forward unchanged)**: the deterministic half (reply returned and rendered) is asserted exactly. The "Portuguese" half still rests on the unasserted prompt line `backend/app/llm/tools.py:76` (`"Responda sempre em português do Brasil…"`). Same disposition as the prior pass — PASS with a UAT-confirmable note, not a new finding, and not re-litigated here.

---

## Fix Verification (the four items routed back)

### CHAT-09 — ✅ confirmed fixed

**Prior finding**: `js/chat-widget.js:156` gated `showWhatsapp()` on `data.lead_captured`, which is `false` on exactly the LLM-outage path, so the backend's non-null `whatsapp_url` was discarded and the anchor stayed `hidden`. Verdict was ❌ GAP.

**Now** (`e319d82`, `js/chat-widget.js:156-160`):

```js
// AC CHAT-09: the backend also supplies whatsapp_url on the LLM-outage
// fallback (lead_captured=false), so key the button on the URL itself.
if (data.whatsapp_url) {
  showWhatsapp(data.whatsapp_url);
}
```

**Did keying on the URL break CHAT-08's normal path?** No. I traced every `TurnResult` construction in `backend/app/domain/conversation.py`:

| Path | `file:line` | `lead_captured` | `whatsapp_url` |
| --- | --- | --- | --- |
| LLM unavailable (CHAT-09) | `:113-117` | `False` | **non-null** — `self._whatsapp_url(_WHATSAPP_FALLBACK_TEXT)` |
| Lead captured (CHAT-08) | `:160-167` | `True` | **non-null** — `self._whatsapp_url(…{lead.need_summary})` |
| No/malformed tool call | `:130` | `False` | `None` (default) |
| Lead persistence failed | `:157` | `False` | `None` (default) |

`_whatsapp_url()` (`:169-170`) is a total function returning an f-string — it can never return `None` or `""`. So **`lead_captured is True` ⟹ `whatsapp_url` is a non-empty string**, and the new gate is a strict superset of the old one: every case that showed the button before still shows it, plus the outage case. `whatsapp_url` is `None` only on the two paths where no lead exists and no outage occurred — genuinely nothing to link to, and neither AC 8 nor AC 9 fires there.

API layer confirms the field survives the boundary: `ChatMessageResponse.whatsapp_url: str | None` (`backend/app/api/chat.py:36`) is a straight pass-through of `result.whatsapp_url` (`:121`), no coercion.

**Empirically guarded**: new mutation **M8** (null the URL on the lead-captured path) is killed by 2 tests — so a future regression that would silently hide the button under the new gate is now caught by the backend suite.

### CHAT-10 — ✅ confirmed pinned

**Prior finding**: every blocking test used scaled-down thresholds (`per_minute=3`, `per_session=2`, `per_minute=2`); the spec's 15/60 lived only as unasserted defaults at `backend/app/core/config.py:37-38`. Editing 15 → 150 broke nothing.

**Now** (`67a3cae`): `backend/tests/unit/test_config.py:43-66` adds two tests asserting `RATE_LIMIT_PER_MINUTE == 15`, `RATE_LIMIT_PER_SESSION == 60` on `Settings(_env_file=None)`, and that `get_rate_limiter()` propagates both into the `RateLimiter`.

**Discrimination confirmed empirically, not by reading**: mutations **M5** (`15 → 150`) and **M6** (`60 → 600`) each fail both new tests in an isolated scratch. Editing either default now breaks the suite. See Advisory 1 for a robustness nit that does not affect this result.

### Mutant M4 — ✅ confirmed killed (the headline check of this run)

**Prior finding**: deleting the required-field loop in `_parse_lead_arguments()` left `lead_fields["need_summary"]` raising `KeyError` *inside* the `try:` around the repository call, so the broad `except Exception` swallowed it, logged `lead persistence failed`, and returned `TurnResult(reply=…, lead_captured=False)` — observationally identical to the intended outcome on every axis the test checked. **18 passed, 0 failed → survived.**

**Now** (`0c6efd3`), two independent changes:

1. **Structural** (`backend/app/domain/conversation.py:132-143`): `category` and `need_summary` are read *before* the `try:`, so only `upsert_lead()` is inside it. A contract violation now propagates as its own error instead of being relabelled a persistence failure.
2. **Test strength** (`backend/tests/unit/test_conversation.py:197-206`, `:209-234`): a direct unit test of `_parse_lead_arguments` asserting `is None` for a missing required field, plus a `caplog` assertion on the flow test that the run logged `"malformed save_lead_info arguments"` and **not** `"lead persistence failed"`.

**Re-run of the exact prior mutation** (same deletion, isolated worktree):

```
FAILED tests/unit/test_conversation.py::test_parse_lead_arguments_rejects_a_missing_required_field
FAILED tests/unit/test_conversation.py::test_tool_arguments_missing_required_field_fall_back_to_plain_text
2 failed, 51 passed
```

**M4 is killed.** Both mechanisms fire: the direct test catches the guard's removal at the unit boundary, and the flow test catches the mislabelled log. New mutation **M7** (delete the `logger.warning` for malformed arguments) confirms the log assertion is live rather than vacuous — it kills 1 test.

### CHAT-05 / CHAT-07 wording — ✅ legitimate resolution, with one caveat stated plainly

Both were spec-text amendments with **no code change** (`84a9572`). The task asked me to judge explicitly whether this is the same thing as the anti-pattern the Code Quality row "Spec not weakened to fit code" guards against. My judgment, separately for each:

**CHAT-07 — clean reconciliation, not weakening.** I verified the claim rather than accepting it: `design.md:136-161` defines `Lead` and `Message` as two SQLModel tables correlated by `session_id`, and that design was approved *before* implementation. So the code followed an approved design; what diverged was spec text that was never reconciled at Design time. The amendment brings the spec to the design, and it **preserves the substantive requirement** ("the full conversation transcript SHALL be persisted and retrievable by the same session id") rather than deleting it. The retrieval path is real and asserted end-to-end: `Lead.session_id` is in the `/api/leads` body (`test_leads_api.py:95`) and the transcript comes back from `GET /api/chat/{session_id}/history` (`test_chat_api.py:249`). Nothing the spec required stopped being required. **Legitimate.**

**CHAT-05 — a genuine narrowing, which I accept, and I want that on the record.** Unlike CHAT-07, this amendment *does* reduce what is required: the original text implied the ask should persist while no contact had been obtained; the new text requires it only on turn one. That is a smaller requirement, and calling it anything else would be dishonest. I accept it for three reasons:

- **(a) The original AC was internally contradictory.** "WHILE the assistant has not yet obtained a contact channel … SHALL ask for one, at most once per session" cannot both hold the moment a visitor ignores the first ask. It was not a coherent target to implement against, so amending it is repair, not erosion.
- **(b) The narrowing is already implied by its sibling.** CHAT-06 — "IF the visitor does not provide a contact channel when asked THEN the system SHALL continue the conversation normally" — presupposes a single ask. The amended AC 5 makes AC 5 and AC 6 consistent instead of in tension.
- **(c) It was disclosed at three levels** — an inline HTML comment on the AC itself, a "Verification Notes" table in `spec.md`, and a separate `docs(…)` commit whose message names the Verifier finding — and it is being reviewed here. That is the structural opposite of a silent edit.

**What distinguishes both from the anti-pattern**: that check targets *undisclosed, post-hoc* edits that erase a real requirement so that failing code passes. Here nothing failing was made to pass — the code was already correct under the binding reading in both cases — the edits were disclosed and justified, and the prior Verifier had itself named "amend the AC" as one of two acceptable resolutions, explicitly requiring an orchestrator/user decision rather than an implementer's unilateral call.

**Caveat (non-blocking, for the user)**: the prior report said Fixes 4 and 5 "need an orchestrator/user decision". The orchestrator made the call and documented it; it holds the user relationship, so that is within its authority. But the **user has not personally confirmed that AC 5's narrowed intent — ask once on turn one, never again even if the visitor never answers — is the desired product behavior.** That is a one-line confirmation to obtain at UAT. It does not block this PASS; it is flagged so the decision is visible rather than absorbed.

### Spot-check — did the fixes disturb the clean 13?

Only `conversation.py` and `chat-widget.js` are application files in the fix range.

- **`conversation.py` (Fix 3)**: the diff moves two dict reads — `lead_fields["category"]`, `lead_fields["need_summary"]` — from inside the `try:` to immediately above it, and passes the locals to `upsert_lead()`. `_parse_lead_arguments` guarantees both keys exist as non-empty `str` before this point (`:62-66`), so under non-mutant code the lookups cannot raise and the values are identical. No side effect is reordered (both are pure reads, still strictly before the repository call). **Behaviorally identical**; CHAT-02/06/07/08's assertions all still pass unchanged (53 passed). Confirmed by reading the diff, not assumed.
- **`chat-widget.js` (Fix 1)**: one condition, analyzed exhaustively above; strict superset of the prior gate, so CHAT-08 cannot regress. CHAT-11/12/13 touch different code paths in the same file and are unaffected — the diff does not alter `appendMessage`, `setBusy`, or any DOM-writing path.

---

## Discrimination Sensor

**Scratch**: temporary git worktree at `<scratchpad>/sensor-wt` (`git worktree add … HEAD --detach`), removed with `git worktree remove --force` + `git worktree prune`. Tests run with the real tree's interpreter (`backend/.venv/bin/pytest`) against the worktree's sources — `backend/tests/__init__.py` exists, so pytest prepends the *worktree's* `backend/` to `sys.path`. **No `git stash` at any point.** Worktree baseline before mutating: **53 passed**.

| Mutation | File:line | Description | Killed? |
| --- | --- | --- | --- |
| **M4 (re-run)** | `backend/app/domain/conversation.py:62-66` | Deleted the required-field loop in `_parse_lead_arguments()` — the exact prior-report mutation | ✅ **Killed** — 2 failed, 51 passed (`test_parse_lead_arguments_rejects_a_missing_required_field`, `test_tool_arguments_missing_required_field_fall_back_to_plain_text`) |
| M5 (new) | `backend/app/core/config.py:37` | `RATE_LIMIT_PER_MINUTE: int = 15` → `150` (spec limit drifts) | ✅ Killed — 2 failed, 51 passed (both new `test_config.py` tests) |
| M6 (new) | `backend/app/core/config.py:38` | `RATE_LIMIT_PER_SESSION: int = 60` → `600` | ✅ Killed — 2 failed, 51 passed (both new `test_config.py` tests) |
| M7 (new) | `backend/app/domain/conversation.py:122-127` | Removed the `logger.warning("malformed save_lead_info arguments…")` side effect — probes whether Fix 3's new log assertion is live or vacuous | ✅ Killed — 1 failed, 52 passed (`test_tool_arguments_missing_required_field_fall_back_to_plain_text`) |
| M8 (new) | `backend/app/domain/conversation.py:160-167` | `whatsapp_url=self._whatsapp_url(…)` → `whatsapp_url=None` on the **lead-captured** path — probes the new risk introduced by Fix 1, which now keys the button on this field | ✅ Killed — 2 failed, 51 passed (`test_chat_api.py::test_post_message_returns_whatsapp_url_when_lead_captured`, `test_conversation.py::test_tool_call_persists_lead_and_returns_whatsapp_url`) |

M1–M3 from the prior pass were killed there and target code untouched by the fix range; not re-run.

**Sensor depth**: lightweight (5 behavior-level mutations, 4 of them aimed specifically at code the fixes introduced or changed)
**Result**: **5/5 killed — ✅ PASS**

**Isolation verified**: real-tree `git status --porcelain` after cleanup is byte-identical to the pre-sensor baseline — exactly ` M .gitignore` and `?? AcessoHostinger.txt`, the only two entries before and after, both pre-dating this feature and untouched. `git worktree list` shows only the main worktree. `git diff --stat HEAD -- backend/app js css index.html data` is empty. HEAD still `84a9572`.

---

## Interactive UAT Results

⏭️ **Still deferred** — not performed in this Verifier run (no live backend process and no browser available to the Verifier). It remains the orchestrator's/user's follow-up step, using the script at `tasks.md:684` ("Manual UAT"), and it is the only way to confirm the frontend-only ACs (CHAT-01, 09's rendering, 11, 12, 13) and CHAT-02's Portuguese-reply behavior at runtime.

The script already includes a "simulated MariTalk outage fallback" step. **Suggested refinement** (I am read-only over `tasks.md`, so this is a note, not an edit): make that step name its observable explicitly — *confirm the apology bubble **and** the "Continuar no WhatsApp" button both render, and the input re-enables*. That is precisely what Fix 1 changed, and the current wording does not require the tester to look for the button.

Add one more question for the user while they are there: **confirm AC 5's narrowed intent** (see the CHAT-05 caveat above).

---

## Code Quality

Assessed against the fix range `8d36528..HEAD`.

| Principle | Status |
| --- | --- |
| Minimum code | ✅ Fix 1 is one condition; Fix 3 hoists two lines. No abstraction added anywhere |
| Surgical changes | ✅ 4 files in the fix range (2 app, 2 test) + 3 `.specs` files. No unrelated file touched, verified by `git diff --name-only` |
| No scope creep | ✅ Each commit maps 1:1 to a numbered Fix from the prior report and says so in its message |
| No cross-batch tampering | ✅ Each of the 4 commits touches only its own fix's files; no test outside the fixed behavior was edited |
| **Spec not weakened to fit code** | ✅ **with reasoning stated, not asserted** — see the CHAT-05/CHAT-07 judgment above. CHAT-07 is reconciliation to an already-approved `design.md`; CHAT-05 is a genuine but disclosed, justified, and internally-forced narrowing. Both carry inline rationale in `spec.md` and a `docs(…)` commit citing the finding |
| Matches patterns | ✅ New tests follow the file's existing `monkeypatch`/`REQUIRED_ENV` and `caplog` idioms; the widget change keeps the IIFE/`var`/no-framework style |
| Would a senior engineer approve? | ✅ Yes. Fix 3 in particular is the *right* fix, not the cheap one: it narrowed the blast radius of the broad `except Exception` (the actual root cause) **and** strengthened the test, rather than only patching the assertion to make the mutant die |
| Spec-anchored outcome check | ✅ 0 spec-precision gaps (CHAT-10's was the last one and is closed) |
| Per-layer Coverage Expectation met | ✅ Domain 1:1 to ACs; every route in scope covers happy + edge + error |
| Every test maps to a spec requirement — no unclaimed tests | ✅ All 53 accounted for. The 3 new ones: `test_rate_limit_defaults_match_the_spec_exactly` + `test_get_rate_limiter_wires_the_settings_defaults_through` → CHAT-10; `test_parse_lead_arguments_rejects_a_missing_required_field` → CHAT-07's extraction guard / the malformed-payload edge case |
| Documented guidelines followed | ✅ None exist in the repo (`tasks.md:18`: "Guidelines found: none") — strong defaults applied |
| Secret hygiene | ✅ `SecretStr` for both secrets; constant-time admin-token compare (`backend/app/api/leads.py:36`); no test asserts on or prints a secret. ⚠️ One new test now *reads* the real project-root `.env` — see Advisory 1 |

---

## Edge Cases

- [x] **Malformed/non-JSON tool-call payload** → falls back to plain text without crashing: `backend/tests/unit/test_conversation.py:180` (unparseable JSON) + `:194`. **The companion missing-required-field case is now discriminating** (was the M4 blind spot): `backend/tests/unit/test_conversation.py:206` - `assert _parse_lead_arguments(tool_call) is None`, and `:231` - `assert any("malformed save_lead_info arguments" in r.getMessage() …)` + `:232` - `assert not any("lead persistence failed" in r.getMessage() …)`. The "SHALL retry structured extraction on the next turn" half is still asserted only indirectly (`:53` - `assert llm.calls[0]["tools"] == [SAVE_LEAD_INFO_TOOL]`, unconditional tool pass at `conversation.py:103`); no test drives two turns to confirm the retry. Unchanged from the prior pass; not a gap against the AC as written.
- [x] **Backend unreachable → offline state distinct from "thinking"**: `js/chat-widget.js:162-164` `.catch(function () { showStatus(offlineEl); })`, distinct from the rate-limit state (`:147`) and the busy state (`:84-85`); `index.html:109`. **[frontend, no automated assertion]**
- [x] **Two tabs → two independent sessions**: `sessionStorage` is per-tab; widget keys off it (`js/chat-widget.js:4`, `:49`). Backend independence asserted: `backend/tests/integration/test_chat_api.py:267`; `backend/tests/unit/test_repository.py:105`.
- [x] **Lead persistence fails after a successful reply → reply still returned, failure logged**: `backend/tests/unit/test_conversation.py:243`-`:245`. **Now materially stronger than at the prior pass**: because Fix 3 hoisted the field lookups out of the `try`, this branch can only be reached by a genuine `upsert_lead()` failure, and the sibling test at `:232` asserts the two failure modes never produce the same log.

---

## Gate Check

- **Build gate (backend)**: `cd backend && .venv/bin/pytest -q`
- **Result**: **53 passed**, 0 failed, 0 skipped, 1 warning (third-party `anyio.abc.BlockingPortal` DeprecationWarning from starlette's testclient — not this feature's code)
- **Test count at prior validation**: 50
- **Test count now**: 53
- **Delta**: **+3** (`test_config.py` 2 → 4; `test_conversation.py` 8 → 9)
- **Skipped tests**: none
- **Failures**: none
- **Test integrity**: ✅ no test deleted, skipped, or weakened in the fix range. The one pre-existing test that was edited (`test_tool_arguments_missing_required_field_fall_back_to_plain_text`) **gained** two assertions and kept all three originals — strengthened, not relaxed. `backend/tests/conftest.py:10` still maps pytest exit code 5 to 0; inert with 53 tests collecting and incapable of masking a failure (only code 5 is remapped).

Per-file collection (re-derived this run): `test_app_wiring` 4, `test_chat_api` 10, `test_leads_api` 3, `test_config` 4, `test_conversation` 9, `test_llm_client` 3, `test_maritalk_client` 3, `test_rate_limit` 5, `test_repository` 7, `test_tools` 5 = **53**.

**Frontend gates** (this feature's only frontend gate, per `tasks.md:27,37`):

| Command | Result |
| --- | --- |
| `node --check js/chat-widget.js` | ✅ exit 0 |
| `node --check js/config.js` | ✅ exit 0 |
| `python3 -m json.tool data/content-pt.json` | ✅ exit 0 |
| `python3 -m json.tool data/content-en.json` | ✅ exit 0 |

**Contract check (frontend ↔ backend field names)** — re-read from `backend/app/api/chat.py` this run, since Fix 1 changed which field the widget depends on:

| Direction | Backend | Widget | Match |
| --- | --- | --- | --- |
| POST body | `ChatMessageRequest{session_id, message}` (`backend/app/api/chat.py:28-30`) | `js/chat-widget.js:143` | ✅ |
| POST response | `ChatMessageResponse{reply, lead_captured, whatsapp_url}` (`backend/app/api/chat.py:33-36`) | `js/chat-widget.js:155` `data.reply`, `:158` `data.whatsapp_url` | ✅ (`data.lead_captured` is now unread by the widget — the field remains in the response contract and is asserted by `test_chat_api.py:73`; harmless) |
| History response | `HistoryResponse{messages:[{role, content, created_at}]}` (`backend/app/api/chat.py:39-46`) | `js/chat-widget.js:124-125` | ✅ |
| Routes | `/api/chat/message`, `/api/chat/{session_id}/history` (`backend/app/api/chat.py:97,125`) | `js/chat-widget.js:140`, `:118` | ✅ |
| CORS | `allow_methods=["GET","POST"]`, `allow_headers=["Authorization","Content-Type"]` (`backend/app/main.py:36-37`) | widget sends `Content-Type: application/json` (`js/chat-widget.js:142`) | ✅ |

---

## Advisories (non-blocking — no AC is violated by any of these)

No fix tasks are opened. These are recorded so they are visible rather than absorbed.

**1. `test_get_rate_limiter_wires_the_settings_defaults_through` reads the developer's real `.env`.** (Minor, test robustness)
`get_rate_limiter()` → `Settings()` **without** `_env_file=None`, so it loads `/home/nees/…/morpheusia/.env` (present; currently holds `HF_TOKEN` + `MARITALK_*` and no `RATE_LIMIT_*`, so the test passes today). Neither new test does `monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)`, the way the sibling `test_allowed_origins_falls_back_to_the_documented_dev_default` (`:31`) does for its own variable. Consequences: the test is coupled to local environment state, and it slightly erodes the prior report's "no test reads the real `.env`" hygiene line (monkeypatched env vars out-rank dotenv values in pydantic-settings, so no real secret reaches an assertion). **Why this is not a gap**: the failure direction is conservative — env drift makes the test fail loudly, it cannot make it silently pass — and M5/M6 empirically prove the discrimination is live today. A one-line `delenv` pair for `RATE_LIMIT_*` plus `_env_file=None` would close it whenever the file is next touched.

**2. The WhatsApp button does not reappear after a page reload.** (Cosmetic, UX)
`restoreHistory()` (`js/chat-widget.js:117-132`) re-renders messages but never calls `showWhatsapp()`, so a visitor who captured a lead and then refreshed loses the handoff button until their next message. **Pre-existing — not introduced by Fix 1** (the old `lead_captured` gate had the identical property), and AC 8's trigger ("WHEN a Lead record is created or updated") does not fire on a pure reload, so no AC is violated. Noted because Fix 1 makes the field-level dependency more visible.

**3. CHAT-02's Portuguese-reply half remains prompt-level and unasserted.** (Minor) Carried forward verbatim from the prior pass with the same PASS disposition — `backend/app/llm/tools.py:76` is deterministic and cheap to assert, but nothing pins it. Confirmable in UAT.

**4. `GET /api/chat/{session_id}/history` remains unauthenticated.** (Accepted risk, now documented)
Reviewed this run rather than waved through. The new Assumptions row (`spec.md:44`) states the reasoning: the widget must call it as an anonymous visitor to restore its own session (OBS-01), so a token would break P3, and it doubles as the sales team's transcript path for a known `session_id`. The reasoning holds for this threat model. Worth stating once, plainly: the transcript can contain whatever contact details the visitor typed, so the client-generated UUID is the **sole** access control on that PII. Adequate for an unlisted, low-traffic endpoint; it should be revisited if the transcript surface ever becomes enumerable or link-shared. No AC requires otherwise, and LEAD-02 governs only `/api/leads`.

**5. AC 5's narrowed product intent is unconfirmed by the user.** (Minor) See the CHAT-05 judgment above — a one-line confirmation at UAT.

---

## Requirement Traceability Update

| Requirement | Status at prior validation | Status now | Earned by |
| --- | --- | --- | --- |
| CHAT-01 | ✅ Verified | ✅ Verified | `js/chat-widget.js:96,109`; `index.html:98,101` [frontend] |
| CHAT-02 | ✅ Verified | ✅ Verified | `test_chat_api.py:73`; `test_conversation.py:44` (note 1) |
| CHAT-03 | ✅ Verified | ✅ Verified | `test_chat_api.py:127,128`; `js/chat-widget.js:175` |
| CHAT-04 | ✅ Verified | ✅ Verified | `test_chat_api.py:147,163`; `js/chat-widget.js:5,177` |
| CHAT-05 | ❌ Needs Fix | ✅ **Verified** | AC amended + `test_conversation.py:69,72`; `test_tools.py:40,47` |
| CHAT-06 | ✅ Verified | ✅ Verified | `test_conversation.py:106,107,110,114` |
| CHAT-07 | ❌ Needs Fix | ✅ **Verified** | AC amended + `test_tools.py:17`; `test_conversation.py:148-153`; `test_leads_api.py:95`; `test_chat_api.py:249` |
| CHAT-08 | ✅ Verified | ✅ Verified | `test_conversation.py:157,158`; `test_chat_api.py:111`; `js/chat-widget.js:158` (regate re-checked; M8 killed) |
| CHAT-09 | ❌ Needs Fix | ✅ **Verified** | `test_conversation.py:170,172`; `test_chat_api.py:214`; `js/chat-widget.js:158` |
| CHAT-10 | ❌ Needs Fix | ✅ **Verified** | `test_config.py:53,54,64,65` (M5/M6 killed); `test_chat_api.py:191`; `js/chat-widget.js:146` |
| CHAT-11 | ✅ Verified | ✅ Verified | `js/chat-widget.js:84,85,138,166` [frontend] |
| CHAT-12 | ✅ Verified | ✅ Verified | `js/chat-widget.js:67` [frontend] |
| CHAT-13 | ✅ Verified | ✅ Verified | fix range touches no other frontend file [frontend] |
| LEAD-01 | ✅ Verified | ✅ Verified | `test_leads_api.py:93,95,100` |
| LEAD-02 | ✅ Verified | ✅ Verified | `test_leads_api.py:53,54,55,67,68,69` |
| OBS-01 | ✅ Verified | ✅ Verified | `test_chat_api.py:231,249`; `js/chat-widget.js:118,200` |
| OBS-02 | ✅ Verified | ✅ Verified | `test_chat_api.py:78,129,149,193,216`; `test_app_wiring.py:124` |

**17 Verified, 0 Needs Fix.** The orchestrator's bumps in `spec.md` are confirmed correct — every row is earned by evidence cited above, re-derived rather than assumed. No correction to `spec.md`'s traceability table is required.

---

## Summary

**Overall**: ✅ **Ready** (pending interactive UAT, which is the user's step, not the Verifier's)

**Spec-anchored check**: **17/17** ACs matched the spec-defined outcome; 0 gaps, 0 spec-precision gaps
**Sensor**: **5/5** mutations killed — including the M4 re-run, which now dies
**Gate**: **53 passed**, 0 failed, 0 skipped; all 4 frontend syntax/JSON gates green

**What the fixes actually did**: all four landed, and three of them are better than the minimum that would have cleared the report. Fix 1 is a one-line regate that I traced exhaustively — it is a strict superset of the behavior it replaced, so CHAT-09 is satisfied without putting CHAT-08 at risk, and new mutation M8 now guards the field it depends on. Fix 3 is the standout: rather than only tightening the assertion until M4 died, it also narrowed the blast radius of the broad `except Exception` that was the real root cause, so a missing-validation bug can no longer be mislabelled a storage failure — and both halves are independently proven by mutation (M4 kills via the new direct unit test, M7 confirms the log assertion is live). Fix 2 closes the last spec-precision gap, empirically confirmed by M5/M6. The two spec amendments are disclosed, justified inline, and — in CHAT-07's case — reconcile the spec to a data model that `design.md` had already approved before implementation began.

**Issues found**: none that block. Five advisories are recorded above; the two worth a moment of the user's attention are the unconfirmed product intent behind AC 5's narrowing (advisory 5) and the test that now reads the real `.env` (advisory 1). Neither violates an acceptance criterion.

**Next steps**: run the interactive UAT at `tasks.md:684`, adding two things to it — check that the **"Continuar no WhatsApp" button** appears alongside the apology on the simulated MariTalk outage (that is exactly what Fix 1 changed, and the current script step does not name the button), and confirm AC 5's one-ask-only behavior is the intended product rule. T20's Dockerfile also remains unverified-by-build and needs a real Docker daemon before deployment.
