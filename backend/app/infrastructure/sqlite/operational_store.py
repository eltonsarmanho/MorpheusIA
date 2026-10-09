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
  handoff_attempts INTEGER NOT NULL DEFAULT 0, last_domain TEXT, last_message_at TEXT, awaiting TEXT, handoff_team TEXT, handoff_reason TEXT, last_reply_hash TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS processed_events (event_key TEXT PRIMARY KEY, received_at TEXT);
CREATE TABLE IF NOT EXISTS tickets (
  ticket_id TEXT PRIMARY KEY, conversation TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT, closed_by TEXT, status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tickets_conv ON tickets(conversation, status);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, conversation TEXT, action TEXT, actor TEXT, detail TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    conversation: str
    opened_at: str  # ISO 8601 UTC
    status: str  # open | closed
    closed_at: str | None = None
    closed_by: str | None = None  # usuario | atendente | sistema


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
    awaiting: str | None = None  # o que o bot espera do usuário no próximo texto (ex.: "process")
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
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(conversations)")}
            if "awaiting" not in cols:
                self._conn.execute("ALTER TABLE conversations ADD COLUMN awaiting TEXT")
            if "last_message_at" not in cols:  # bancos criados antes da inatividade
                self._conn.execute("ALTER TABLE conversations ADD COLUMN last_message_at TEXT")

    def get(self, key: str) -> ConversationState:
        with self._lock:
            r = self._conn.execute("SELECT * FROM conversations WHERE key=?", (key,)).fetchone()
        if r is None:
            return ConversationState(key)
        return ConversationState(
            key=key, handoff_state=HandoffState(r["handoff_state"]), profile=Profile(r["profile"]),
            process_number=r["process_number"], failed_retrievals=r["failed_retrievals"], offer_pending=bool(r["offer_pending"]),
            handoff_attempts=r["handoff_attempts"], last_domain=r["last_domain"], awaiting=r["awaiting"], handoff_team=r["handoff_team"], handoff_reason=r["handoff_reason"],
            last_reply_hash=r["last_reply_hash"],
        )

    def save(self, st: ConversationState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO conversations(key,handoff_state,profile,process_number,failed_retrievals,offer_pending,handoff_attempts,"
                "last_domain,awaiting,handoff_team,handoff_reason,last_reply_hash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET handoff_state=excluded.handoff_state, profile=excluded.profile, "
                "process_number=excluded.process_number, failed_retrievals=excluded.failed_retrievals, offer_pending=excluded.offer_pending, "
                "handoff_attempts=excluded.handoff_attempts, last_domain=excluded.last_domain, awaiting=excluded.awaiting, handoff_team=excluded.handoff_team, handoff_reason=excluded.handoff_reason, "
                "last_reply_hash=excluded.last_reply_hash, updated_at=excluded.updated_at",
                (st.key, st.handoff_state.value, st.profile.value, st.process_number, st.failed_retrievals, int(st.offer_pending),
                 st.handoff_attempts, st.last_domain, st.awaiting, st.handoff_team, st.handoff_reason, st.last_reply_hash, _now()),
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

    def has_event(self, event_key: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM processed_events WHERE event_key=?", (event_key,)).fetchone() is not None

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

    # ------------------------------------------------------------------ tickets
    @staticmethod
    def _new_ticket_id(conversation: str, opened_at: str) -> str:
        import hashlib
        import secrets

        digest = hashlib.sha256(f"{conversation}|{opened_at}|{secrets.token_hex(8)}".encode()).hexdigest()
        return "TKT-" + digest[:8].upper()

    def open_ticket(self, conversation: str) -> Ticket:
        """Abre o protocolo do atendimento (data e hora gravadas); se já houver um aberto, devolve o existente."""
        with self._lock:
            existing = self.get_open_ticket(conversation)
            if existing:
                return existing
            opened = _now()
            with self._conn:
                for _ in range(5):
                    tid = self._new_ticket_id(conversation, opened)
                    try:
                        self._conn.execute("INSERT INTO tickets(ticket_id,conversation,opened_at,status) VALUES(?,?,?,'open')", (tid, conversation, opened))
                        break
                    except sqlite3.IntegrityError:
                        continue
            self.audit(conversation, "ticket_opened", "sistema", tid)
            return Ticket(tid, conversation, opened, "open")

    @staticmethod
    def _row_ticket(r: sqlite3.Row) -> Ticket:
        return Ticket(r["ticket_id"], r["conversation"], r["opened_at"], r["status"], r["closed_at"], r["closed_by"])

    def get_open_ticket(self, conversation: str) -> Ticket | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM tickets WHERE conversation=? AND status='open' ORDER BY opened_at DESC LIMIT 1", (conversation,)).fetchone()
        return self._row_ticket(r) if r else None

    def close_ticket(self, conversation: str, closed_by: str) -> Ticket | None:
        """Fecha o protocolo aberto (uma única vez); devolve o ticket fechado ou None se não havia ticket aberto."""
        with self._lock, self._conn:
            r = self._conn.execute("SELECT * FROM tickets WHERE conversation=? AND status='open' ORDER BY opened_at DESC LIMIT 1", (conversation,)).fetchone()
            if r is None:
                return None
            closed = _now()
            self._conn.execute("UPDATE tickets SET status='closed', closed_at=?, closed_by=? WHERE ticket_id=?", (closed, closed_by, r["ticket_id"]))
            self.audit(conversation, "ticket_closed", closed_by, r["ticket_id"])
            return Ticket(r["ticket_id"], conversation, r["opened_at"], "closed", closed, closed_by)

    def list_tickets(self, status: str | None = None, limit: int = 100) -> list[Ticket]:
        sql, params = "SELECT * FROM tickets", []
        if status:
            sql += " WHERE status=?"; params.append(status)
        sql += " ORDER BY opened_at DESC LIMIT ?"; params.append(limit)
        with self._lock:
            return [self._row_ticket(r) for r in self._conn.execute(sql, params)]

    # -------------------------------------------------------------- inatividade
    def touch(self, conversation: str, when: str | None = None) -> None:
        """Registra a última mensagem da conversa (qualquer lado, exceto notas privadas)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO conversations(key,handoff_state,last_message_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET last_message_at=excluded.last_message_at",
                (conversation, HandoffState.BOT_ACTIVE.value, when or _now(), _now()),
            )

    def last_activity(self, conversation: str) -> str | None:
        with self._lock:
            r = self._conn.execute("SELECT last_message_at FROM conversations WHERE key=?", (conversation,)).fetchone()
        return r["last_message_at"] if r else None
