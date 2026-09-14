# STATE

## Decisions

### AD-001
- **Decision**: Backend features in this project use FastAPI + SQLModel/SQLite + pytest, with external integrations wrapped behind a small Protocol interface and a fake test double (e.g. `LLMClient`/`FakeLLMClient`).
- **Reason**: This is the first backend code in the project; picked a small, standard, well-documented stack that keeps external paid/rate-limited services out of automated tests and keeps future provider swaps to "write one new class."
- **Trade-off**: SQLite is single-file/single-writer — fine at current lead volume, but will need a migration to Postgres (same ORM, different connection string) if traffic/concurrency grows significantly.
- **Scope**: `backend/` — all backend features in the `morpheusia` project.
- **Date**: 2026-09-14
- **Status**: active

## Handoff

- **Feature**: initial-support-chatbot / `.specs/features/initial-support-chatbot/`
- **Phase / Task**: Done - Execute complete, Verifier PASS (2 iterations), manual UAT complete
- **Completed**: Specify, Design, Tasks, Execute (T1-T20 + 5 post-Verifier fixes + 1 UAT-found fix), Verifier (re-verify PASS, `validation.md`), manual UAT (live MariTalk calls + Playwright browser pass, all P1 flows confirmed working)
- **In-progress**: none
- **Next step**: None required to ship the MVP. Open follow-ups for the user, not blocking: (1) set the real WhatsApp number (still the `5500000000000` placeholder shared with the pre-existing site CTA) in `.env`'s `WHATSAPP_NUMBER` and in `index.html`'s `#contato` link; (2) generate a real `ADMIN_API_TOKEN` for any non-local deployment (a random dev-only token is in the local `.env` now); (3) decide a real deployment target for the backend (T20 ships a Dockerfile + compose file, build itself unverified in this sandbox - no Docker daemon); (4) T20's Dockerfile build was never run against a real Docker daemon - verify before relying on it.
- **Blockers**: none
- **Uncommitted files**: none (HEAD at `8a23bd9` before this lesson commit; lessons-store commit follows)
- **Branch**: main (dedicated repo, remote `origin` = `https://github.com/eltonsarmanho/MorpheusIA.git`, never pushed this session)
