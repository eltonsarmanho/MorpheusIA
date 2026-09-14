# Initial Support Chatbot Specification

## Problem Statement

Morpheus IA's institutional site (static HTML/CSS/JS) only offers a generic WhatsApp link as a contact channel. Visitors get no immediate, tailored response, and the sales team has no structured record of who reached out or why. We need an initial-contact chatbot that greets visitors, understands their need, categorizes it against Morpheus IA's existing solution lines, captures a lead, and hands off to WhatsApp for human follow-up — turning passive site traffic into qualified, trackable leads.

## Goals

- [ ] Visitor can open a chat widget from the main page and get a relevant, on-brand reply (in Portuguese) within a few seconds of sending a message.
- [ ] Every meaningful conversation results in a persisted Lead record (need, category, contact if provided) the sales team can act on, even if the visitor never returns.
- [ ] Visitor can move to a WhatsApp conversation with one click, with the summary of what they already said carried over (no need to repeat themselves).
- [ ] Adding the widget does not regress the existing static site (i18n PT/EN, theme toggle, navigation, performance).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Full CRM / ticketing system integration | Beyond MVP; leads are exposed via a simple API for now, integration is a future initiative. |
| Live human takeover inside the widget | Human handoff is delegated to WhatsApp (existing channel) per user decision, not built into the widget itself. |
| Bot responses in English | User decided the bot always replies in Portuguese regardless of the site's PT/EN toggle. |
| Payment processing inside the chat | Not requested; unrelated to initial triage/lead-capture scope. |
| Admin dashboard/UI for browsing leads | Only a simple protected read API is in scope (P2); a UI is future work. |
| Full LGPD compliance program (privacy policy page, data-subject-rights automation, DPO workflows) | Only an in-widget consent notice is in scope; broader legal compliance is a separate initiative. |
| Swapping away from the MariTalk model provider | User explicitly chose MariTalk (sabiazinho-4) now and may reassess later; this feature only needs the LLM call abstracted enough to make a future swap low-cost, not perform it. |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Widget placement & trigger | Floating action button, bottom-right corner of the viewport, opens an overlay chat panel | Standard, low-risk convention; matches how chat widgets are universally recognized; no need to spend a user turn on it | n (agent default, low-stakes) |
| Per-session message rate limit | 15 user messages/minute and 60 messages total per session; excess returns HTTP 429 with a friendly widget message | Protects the paid MariTalk API and backend from abuse/runaway loops without constraining normal conversations | n (agent default, low-stakes) |
| Max message length | 1000 characters per user message; longer input is rejected client- and server-side with an inline error | Keeps prompt size and cost bounded; 1000 chars comfortably fits a real customer inquiry | n (agent default, low-stakes) |
| Session identity | Client-generated UUID stored in `sessionStorage` (not a cookie), sent as a header/body field on every request | Avoids consent/PII implications of tracking cookies; sufficient to correlate a browser tab's conversation with its Lead record | n (agent default, low-stakes) |
| Admin leads-read endpoint auth (P2) | Static bearer token from a new `ADMIN_API_TOKEN` env var, checked on `GET /api/leads` | No user/auth system exists yet in this project; a static token is the minimum viable protection for an internal, low-traffic endpoint | n (agent default, low-stakes) |
| MariTalk API request/response contract | To be confirmed against Maritaca AI's official docs during Design (Knowledge Verification Chain step 3/4) before writing the client | Provider-specific wire format must be verified, not assumed, to avoid building against a fabricated API shape | n (research task, not a product decision) |
| Lead uniqueness per session | One Lead row per session id; later extraction results upsert (update) the same row rather than creating duplicates | Prevents duplicate/fragmented lead records for the same visitor conversation | n (agent default, low-stakes) |
| Lead data retention | No automatic deletion/expiry in this feature; leads are a business record kept indefinitely until a future retention policy is defined | Out of scope to design a retention/TTL policy without a business rule to build it against | n (agent default, low-stakes) |

**Open questions:** none - all resolved above or explicitly deferred to Design as a research task (MariTalk contract).

---

## User Stories

### P1: Chat, understand, categorize, and hand off ⭐ MVP

**User Story**: As a website visitor, I want to open a chat widget, describe what I need in my own words, and get routed appropriately, so that I get an immediate response and don't have to cold-start a WhatsApp conversation.

**Why P1**: This is the entire value proposition of the feature — without it there is no chatbot, no lead, and no handoff.

**Acceptance Criteria**:

1. WHEN the visitor clicks the floating chat icon on the main page THEN the system SHALL open a chat panel showing a Portuguese greeting message and the LGPD consent notice.
2. WHEN the visitor submits a non-empty message under 1000 characters THEN the system SHALL send it to the backend and display the assistant's Portuguese reply in the panel.
3. IF the visitor submits an empty message THEN the system SHALL block the send client-side and SHALL NOT call the backend.
4. IF the visitor submits a message of 1000 characters or more THEN the system SHALL reject it with an inline error and SHALL NOT call the backend.
5. WHILE the assistant has not yet obtained a contact channel (phone or email) for the current session THEN the system SHALL have the assistant ask for one, at most once per session, as a natural part of the conversation.
6. IF the visitor does not provide a contact channel when asked THEN the system SHALL continue the conversation normally and SHALL mark the resulting Lead's contact as absent rather than blocking further chat.
7. WHEN the assistant has gathered enough information to identify the visitor's need THEN the system SHALL classify it into one of the existing solution categories (Gestão Inteligente de Empresas, Atendimento WhatsApp com IA, Análise de Documentos Técnicos, Gerador de Conteúdos) or "Outro", and SHALL persist a Lead record containing timestamp, category, a short summary, the contact info if provided, and the full transcript.
8. WHEN a Lead record is created or updated for the current session THEN the system SHALL show a "Continuar no WhatsApp" button in the widget that opens `https://wa.me/<numero>` with a pre-filled message summarizing the conversation.
9. IF a call to the MariTalk API fails or times out THEN the system SHALL show a graceful fallback message in the widget (apology + the WhatsApp button) and SHALL NOT leave the widget in a stuck/loading state.
10. IF a request exceeds the per-session rate limit (15 messages/minute or 60 messages/session) THEN the system SHALL respond with HTTP 429 and the widget SHALL show a friendly rate-limit message instead of retrying automatically.
11. WHILE a request for the current session is in flight THEN the system SHALL ignore or visibly disable further sends for that session until a response (or error) is received, to preserve message ordering.
12. The system SHALL render all visitor and assistant messages as plain text (no HTML interpretation) to prevent script injection through chat content.
13. The system SHALL NOT alter or break the existing site's i18n toggle, theme toggle, navigation, or page load when the chat widget script is present.

**Independent Test**: Load the site, click the chat icon, have a short conversation describing a need (e.g. "quero automatizar meu atendimento no WhatsApp"), decline or give a phone number when asked, and confirm: a reply appears in Portuguese, a Lead row appears in the backend store with the right category, and a working "Continuar no WhatsApp" button is shown.

---

### P2: Lead retrieval for the sales team

**User Story**: As a Morpheus IA team member, I want to retrieve the leads captured by the chatbot, so that I can follow up with prospects even without a full CRM.

**Why P2**: Necessary for the feature to have business value beyond the widget itself, but the sales team can operate for an initial period by checking the store directly if this slips past MVP.

**Acceptance Criteria**:

1. WHEN an authenticated request (valid `ADMIN_API_TOKEN` bearer token) hits `GET /api/leads` THEN the system SHALL return the list of persisted leads ordered by most recent first.
2. IF `GET /api/leads` is called without a valid bearer token THEN the system SHALL respond with HTTP 401 and SHALL NOT return any lead data.

**Independent Test**: Call `GET /api/leads` with and without the correct token and confirm the authorized call returns the leads created during the P1 test, while the unauthorized call returns 401 with an empty body.

---

### P3: Session continuity and basic observability

**User Story**: As a visitor, I want my conversation to survive an accidental page reload within the same browser tab, and as an operator I want minimal logs of chatbot activity, so that a refresh doesn't lose context and issues are diagnosable.

**Why P3**: Improves resilience and operability but the feature is usable end-to-end without it.

**Acceptance Criteria**:

1. WHEN the visitor reloads the page within the same browser session THEN the system SHALL restore the prior conversation in the widget using the session id stored in `sessionStorage`.
2. The system SHALL log each chat request and its outcome (success, validation error, upstream failure, rate-limited) with the session id, for troubleshooting.

---

## Edge Cases

- IF the MariTalk API returns a malformed/non-JSON structured-extraction payload THEN the system SHALL fall back to treating the turn as plain conversational text and SHALL retry structured extraction on the next turn, without crashing the request.
- IF the backend is unreachable from the browser (network/offline) THEN the widget SHALL show an offline/error state distinct from the "assistant is thinking" state.
- WHEN the same visitor opens the site in two different browser tabs THEN the system SHALL treat them as two independent sessions with two independent (or upserted-by-tab) Lead records.
- IF persisting the Lead record fails (storage error) after a successful assistant reply THEN the system SHALL still return the assistant's reply to the visitor and SHALL log the persistence failure, rather than losing the reply over a storage hiccup.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| CHAT-01 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-02 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-03 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-04 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-05 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-06 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-07 | P1: Chat, understand, categorize, and hand off | Tasks | Implementing |
| CHAT-08 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-09 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-10 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-11 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-12 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| CHAT-13 | P1: Chat, understand, categorize, and hand off | Tasks | In Tasks |
| LEAD-01 | P2: Lead retrieval for the sales team | Tasks | In Tasks |
| LEAD-02 | P2: Lead retrieval for the sales team | Tasks | In Tasks |
| OBS-01 | P3: Session continuity and basic observability | Tasks | Implementing |
| OBS-02 | P3: Session continuity and basic observability | Tasks | In Tasks |

**ID format:** `[CATEGORY]-[NUMBER]` (CHAT = P1 chat/lead-capture behavior, LEAD = P2 lead-retrieval API, OBS = P3 continuity/observability)

**Status values:** Pending → In Design → In Tasks → Implementing → Verified

**Coverage:** 17 total, 17 mapped to tasks, 0 unmapped ✅ (see `tasks.md` Task Breakdown)

---

## Success Criteria

- [ ] A visitor can complete a full triage conversation (greeting → need described → category assigned → lead saved → WhatsApp button shown) in under 2 minutes of active use.
- [ ] Zero widget-caused regressions in the existing static site (i18n, theme, nav) after the change ships.
- [ ] Every completed P1 conversation produces exactly one persisted Lead record, retrievable via the P2 endpoint.
- [ ] MariTalk outages degrade to the fallback message (AC CHAT-09) instead of a broken/stuck widget.
