# Initial Support Chatbot Context

**Gathered:** 2026-09-14
**Spec:** `.specs/features/initial-support-chatbot/spec.md`
**Status:** Ready for design

---

## Feature Boundary

A chat widget on the Morpheus IA institutional site that greets visitors, converses in Portuguese using the MariTalk API, extracts their need and (optionally) contact info, classifies the need into an existing solution category, persists a Lead record, and offers a one-click WhatsApp handoff with the conversation summary carried over. A simple protected API exposes the captured leads to the team.

---

## Implementation Decisions

### Model / provider

- Use the MariTalk API (Maritaca AI, model `sabiazinho-4`) as the LLM backing the chatbot, via the credentials already placed in `.env` (`MARITALK_API_KEY`, `MARITALK_API_BASE`, `MARITALK_MODEL`).
- The user may want to switch providers later ("depois verifico possibilidade de mudança") — the LLM call must sit behind a small abstraction (single client module/interface) so swapping providers later doesn't require touching the conversation/lead logic, but no multi-provider support is built now.
- Exact MariTalk request/response contract is a Design-phase research task (Knowledge Verification Chain), not assumed here.

### Lead capture & handoff

- After triage, the system both persists a Lead (backend storage) **and** shows a WhatsApp handoff button — not one or the other.
- Data is extracted conversationally: the model itself extracts structured fields (name/contact/need/category) from natural chat, rather than the widget presenting a rigid form.
- Providing contact info is optional: the bot asks once, naturally; if declined, the conversation and lead-saving continue, with the lead marked as having no direct contact.
- Leads must be durably persisted (survive backend restarts) — not just in-memory — since losing a captured lead has direct business cost.

### Language

- The bot always replies in Portuguese, regardless of the site's PT/EN i18n toggle. No bilingual system prompt or response logic is needed.

### LGPD / consent

- The widget shows a short, fixed consent notice (not a full privacy-policy flow) before/at first personal-data collection, e.g.: "Ao continuar, você concorda com o uso dos seus dados para retorno comercial da Morpheus IA."

### Agent's Discretion

- Widget placement/trigger (floating action button, bottom-right, opening an overlay panel) — standard convention, no user preference expressed; agent decides and matches the existing design system (themes, fonts, palette).
- Rate limiting thresholds, max message length, session-id mechanism, admin-endpoint auth mechanism — all logged as assumptions in spec.md's Assumptions & Open Questions table with rationale; agent has discretion within those defaults.

### Declined / Undiscussed Gray Areas → Assumptions

All gray areas raised were discussed directly with the user (model hosting, handoff behavior, persistence, language, capture style, mandatory-vs-optional contact, LGPD notice) — none were declined. The remaining lower-stakes gray areas (widget visual placement, rate-limit numbers, message length cap, session-id mechanism, admin auth mechanism, lead-uniqueness/upsert behavior, retention policy) were not raised as questions per the Guided-pace "low-stakes → state the assumption" rule; they are recorded with chosen defaults and rationale directly in spec.md's Assumptions & Open Questions table.

---

## Specific References

- Reuse the existing WhatsApp CTA pattern already on the site (`#contato`, `https://wa.me/5500000000000` placeholder) for the handoff button — replace the placeholder number with the real one when known; until then keep using the same number/format already in `index.html`.
- Lead categories should mirror the four existing solution cards in `data/content-pt.json` (Gestão Inteligente de Empresas, Atendimento WhatsApp com IA, Análise de Documentos Técnicos, Gerador de Conteúdos) plus "Outro" for anything that doesn't fit.
- Visual design of the widget (colors, fonts, dark/light theme support) must follow the existing design system already implemented in `css/style.css` (Orbitron/Inter fonts, `data-theme` attribute) — no new design language introduced.

---

## Deferred Ideas

- CRM/ticketing integration, admin dashboard UI for leads, multi-provider LLM support, full LGPD compliance program (privacy policy page, data-subject rights), live human takeover inside the widget itself — all explicitly out of scope for this feature (see spec.md Out of Scope table); captured here so they aren't lost for a future iteration.
