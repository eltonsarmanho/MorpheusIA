# LESSONS - auto-maintained by scripts/lessons.py

> Machine-owned. Do NOT hand-edit. Changes are overwritten on the next `lessons.py` write.
> Canonical state lives in `.specs/lessons.json`. Edit lessons only via the script.
> promote_threshold=2 distinct features · window_days=45 · quarantine_threshold=2

## Confirmed (load these at Specify/Design)

Corroborated across multiple features. Safe to apply as guidance.

_none_

## Candidates (under observation - do NOT load as guidance yet)

Seen once or not yet corroborated. Tracked, not trusted.

### L-001 - Gate a UI affordance on the response field that carries it, not on a sibling flag that is false on error-fallback paths.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `chat-widget` · harmful: 0
- features: initial-support-chatbot
- evidence: AC CHAT-09 / js/chat-widget.js:156 (chat-widget)
- last seen: 2026-09-14T14:24:00Z

### L-002 - Keep only the fallible call inside a broad try/except and hoist field lookups out, so a validation bug cannot masquerade as a storage failure.
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `domain` · harmful: 0
- features: initial-support-chatbot
- evidence: Mutant M4 / backend/app/domain/conversation.py:144 (domain)
- last seen: 2026-09-14T14:24:00Z

### L-003 - Assert spec-named constants such as limits, thresholds and timeouts against their config defaults, not only the mechanism that consumes them.
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `config` · harmful: 0
- features: initial-support-chatbot
- evidence: AC CHAT-10 / backend/app/core/config.py:37 (config)
- last seen: 2026-09-14T14:24:00Z

### L-004 - Implement an acceptance criterion's stated condition directly; a proxy condition derived from adjacent state is a different rule and needs its own test.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `domain` · harmful: 0
- features: initial-support-chatbot
- evidence: AC CHAT-05 / backend/app/domain/conversation.py:90 (domain)
- last seen: 2026-09-14T14:24:00Z

### L-005 - When a design normalizes a field out of the record a spec names, expose it through the same API the spec's consumer uses or the data is unreachable.
- signal: `ac_gap` · recurrence: 1 feature(s) · scope: `api` · harmful: 0
- features: initial-support-chatbot
- evidence: AC CHAT-07 / backend/app/api/leads.py:16 (api)
- last seen: 2026-09-14T14:24:00Z

## Quarantined (failed when applied - ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
