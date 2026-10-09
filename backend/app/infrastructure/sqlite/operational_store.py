"""Estado operacional: conversa, eventos processados (idempotência) e auditoria."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.domain.handoff import HandoffState, ensure_transition
from app.domain.models import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  key TEXT PRIMARY KEY, handoff_state TEXT NOT NULL, profile TEXT NOT NULL DEFAULT 'indefinido',
  process_number TEXT, failed_retrievals INTEGER NOT NULL DEFAULT 0, offer_pending INTEGER NOT NULL DEFAULT 0,
  handoff_attempts INTEGER NOT NULL DEFAULT 0, last_domain TEXT, handoff_team TEXT, handoff_reason TEXT, last_reply_hash TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (event_key TEXT PRIMARY KEY, received_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, conversation TEXT, action TEXT, actor TEXT, detail TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ConversationState:
    key: str
    handoff_state: HandoffState = HandoffState.BOT_ACTIVE
    profile: Profile = Profile.INDEFINIDO
    process_number: str | None = None
    failed_retrievals: int = 0
    offer_pending: bool = False
    handoff_attempts: int = 0
    last_domain: str | None = None
    handoff_team: str | None = None
    handoff_reason: str | None = None
    last_reply_hash: str | None = None


class OperationalStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def get(self, key: str) -> ConversationState:
        with self._lock:
            r = self._conn.execute("SELECT * FROM conversations WHERE key=?", (key,)).fetchone()
        if r is None:
            return ConversationState(key)
        return ConversationState(
            key=key, handoff_state=HandoffState(r["handoff_state"]), profile=Profile(r["profile"]),
            process_number=r["process_number"], failed_retrievals=r["failed_retrievals"], offer_pending=bool(r["offer_pending"]),
            handoff_attempts=r["handoff_attempts"], last_domain=r["last_domain"], handoff_team=r["handoff_team"], handoff_reason=r["handoff_reason"],
            last_reply_hash=r["last_reply_hash"],
        )

    def save(self, st: ConversationState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO conversations(key,handoff_state,profile,process_number,failed_retrievals,offer_pending,handoff_attempts,"
                "last_domain,handoff_team,handoff_reason,last_reply_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET handoff_state=excluded.handoff_state, profile=excluded.profile, "
                "process_number=excluded.process_number, failed_retrievals=excluded.failed_retrievals, offer_pending=excluded.offer_pending, "
                "handoff_attempts=excluded.handoff_attempts, last_domain=excluded.last_domain, handoff_team=excluded.handoff_team, handoff_reason=excluded.handoff_reason, "
                "last_reply_hash=excluded.last_reply_hash, updated_at=excluded.updated_at",
                (st.key, st.handoff_state.value, st.profile.value, st.process_number, st.failed_retrievals, int(st.offer_pending),
                 st.handoff_attempts, st.last_domain, st.handoff_team, st.handoff_reason, st.last_reply_hash, _now()),
            )

    def transition(self, key: str, target: HandoffState, *, actor: str, detail: str = "") -> ConversationState:
        """Aplica uma transição válida e a registra na auditoria (CHW-01)."""
        with self._lock:
            st = self.get(key)
            ensure_transition(st.handoff_state, target)
            before = st.handoff_state
            st.handoff_state = target
            self.save(st)
            self.audit(key, f"transition:{before.value}->{target.value}", actor, detail)
            return st

    def claim_event(self, event_key: str) -> bool:
        """True se o evento é novo; False se já foi processado (CHW-05)."""
        with self._lock, self._conn:
            try:
                self._conn.execute("INSERT INTO processed_events VALUES(?,?)", (event_key, _now()))
                return True
            except sqlite3.IntegrityError:
                return False

    def release_event(self, event_key: str) -> None:
        """Libera o evento quando o processamento falhou antes de responder, permitindo a reentrega."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM processed_events WHERE event_key=?", (event_key,))

    def audit(self, conversation: str, action: str, actor: str, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit(at,conversation,action,actor,detail) VALUES(?,?,?,?,?)",
                (_now(), conversation, action, actor, detail[:500]),
            )

    def audit_rows(self, conversation: str | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM audit", []
        if conversation:
            sql += " WHERE conversation=?"; params.append(conversation)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params)]
