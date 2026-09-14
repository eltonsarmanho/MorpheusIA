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
- **Phase / Task**: Design approved, about to run Tasks phase
- **Completed**: Specify (spec.md, context.md), Design (design.md)
- **In-progress**: none
- **Next step**: Break the design into tasks.md (or implicit task list if small) and begin Execute
- **Blockers**: none
- **Uncommitted files**: none (baseline commit `0121da5` on branch `main`)
- **Branch**: main
