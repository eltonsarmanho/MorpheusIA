"""Estado operacional: conversa, eventos processados (idempotência) e auditoria."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain.handoff import HandoffState, ensure_transition
from app.domain.models import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  key TEXT PRIMARY KEY, handoff_state TEXT NOT NULL, profile TEXT NOT NULL DEFAULT 'indefinido',
  process_number TEXT, failed_retrievals INTEGER NOT NULL DEFAULT 0, offer_pending INTEGER NOT NULL DEFAULT 0,
  handoff_attempts INTEGER NOT NULL DEFAULT 0, last_domain TEXT, last_message_at TEXT, awaiting TEXT, handoff_team TEXT, handoff_reason TEXT, last_reply_hash TEXT,
  updated_at TEXT, profile_locked INTEGER NOT NULL DEFAULT 0, unit_label TEXT, unit_id TEXT, diligence TEXT, request_reason TEXT,
  request_channel TEXT, request_note_id INTEGER, choices TEXT
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

# Colunas acrescentadas depois da primeira versão: bancos existentes recebem ALTER TABLE na abertura (sem perder dados).
_ADDED_COLUMNS = {
    "awaiting": "TEXT", "last_message_at": "TEXT",
    # roteiro TJPA (perfil escolhido, unidade, diligência e canal humano)
    "profile_locked": "INTEGER NOT NULL DEFAULT 0", "unit_label": "TEXT", "unit_id": "TEXT", "diligence": "TEXT",
    "request_reason": "TEXT", "request_channel": "TEXT", "request_note_id": "INTEGER", "choices": "TEXT",
    # Ouvidoria da Mulher: triagem estruturada em JSON (relato e número do processo nunca vão para log ou auditoria)
    "triage": "TEXT",
    # retomada por protocolo: telefone de retorno informado (None = não perguntado; "" = recusou)
    "return_phone": "TEXT",
}
_TICKET_COLUMNS = {"contact_id": "TEXT", "parent_ticket_id": "TEXT", "team": "TEXT", "visibility": "TEXT NOT NULL DEFAULT 'normal'", "facts": "TEXT"}

_STATE_FIELDS = ("handoff_state", "profile", "process_number", "failed_retrievals", "offer_pending", "handoff_attempts", "last_domain",
                 "awaiting", "handoff_team", "handoff_reason", "last_reply_hash", "profile_locked", "unit_label", "unit_id", "diligence",
                 "request_reason", "request_channel", "request_note_id", "choices", "triage", "return_phone")


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
    contact_id: str | None = None  # contato do Chatwoot dono do protocolo; None = não retomável (RTK-02)
    parent_ticket_id: str | None = None  # protocolo anterior que este continua (gravado uma vez)
    team: str | None = None  # equipe que recebeu a transferência confirmada
    visibility: str = "normal"  # normal | restricted (Ouvidoria: não listado, retoma só digitando) | no_chat (nunca retoma)
    facts: str | None = None  # JSON com fatos fechados do atendimento encerrado (apagados após a retenção)


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
    profile_locked: bool = False  # perfil escolhido no menu; a heurística de palavras não o troca
    unit_label: str | None = None  # unidade identificada (do processo localizado ou informada pelo usuário)
    unit_id: str | None = None  # id no cadastro de unidades, quando a unidade foi reconhecida nele
    diligence: str | None = None  # texto integral da diligência, exatamente como recebido; nunca vai para log ou auditoria
    request_reason: str | None = None  # motivo do pedido de atendimento humano (diligencia | providencia | atendimento)
    request_channel: str | None = None  # canal escolhido: whatsapp | balcao
    request_note_id: int | None = None  # nota de contexto já registrada no Chatwoot (evita duplicar na nova tentativa)
    choices: str | None = None  # JSON com os números de processo oferecidos para seleção explícita
    triage: str | None = None  # JSON da triagem da Ouvidoria da Mulher (domain/triage_mulher.py)
    return_phone: str | None = None  # telefone de retorno para o Balcão Virtual; None = não perguntado, "" = prefere não informar

    def clear_request(self) -> None:
        self.diligence = self.request_reason = self.request_channel = self.request_note_id = self.choices = self.triage = self.return_phone = None


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
            for name, ddl in _ADDED_COLUMNS.items():
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE conversations ADD COLUMN {name} {ddl}")
            tcols = {r[1] for r in self._conn.execute("PRAGMA table_info(tickets)")}
            for name, ddl in _TICKET_COLUMNS.items():
                if name not in tcols:
                    self._conn.execute(f"ALTER TABLE tickets ADD COLUMN {name} {ddl}")
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_tickets_contact ON tickets(contact_id, opened_at)")  # depois do ALTER (banco antigo)
            self._conn.commit()

    def get(self, key: str) -> ConversationState:
        with self._lock:
            r = self._conn.execute("SELECT * FROM conversations WHERE key=?", (key,)).fetchone()
        if r is None:
            return ConversationState(key)
        return ConversationState(
            key=key, handoff_state=HandoffState(r["handoff_state"]), profile=Profile(r["profile"]),
            process_number=r["process_number"], failed_retrievals=r["failed_retrievals"], offer_pending=bool(r["offer_pending"]),
            handoff_attempts=r["handoff_attempts"], last_domain=r["last_domain"], awaiting=r["awaiting"], handoff_team=r["handoff_team"], handoff_reason=r["handoff_reason"],
            last_reply_hash=r["last_reply_hash"], profile_locked=bool(r["profile_locked"]), unit_label=r["unit_label"], unit_id=r["unit_id"],
            diligence=r["diligence"], request_reason=r["request_reason"], request_channel=r["request_channel"],
            request_note_id=r["request_note_id"], choices=r["choices"], triage=r["triage"], return_phone=r["return_phone"],
        )

    def save(self, st: ConversationState) -> None:
        values = [getattr(st, f) for f in _STATE_FIELDS]
        values[0], values[1] = st.handoff_state.value, st.profile.value
        values = [int(v) if isinstance(v, bool) else v for v in values]
        cols = ",".join(_STATE_FIELDS)
        updates = ", ".join(f"{f}=excluded.{f}" for f in _STATE_FIELDS)
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO conversations(key,{cols},updated_at) VALUES({','.join('?' * (len(_STATE_FIELDS) + 2))}) "
                f"ON CONFLICT(key) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
                (st.key, *values, _now()),
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

    def open_ticket(self, conversation: str, contact_id: str | None = None) -> Ticket:
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
                        self._conn.execute("INSERT INTO tickets(ticket_id,conversation,opened_at,status,contact_id) VALUES(?,?,?,'open',?)",
                                           (tid, conversation, opened, contact_id))
                        break
                    except sqlite3.IntegrityError:
                        continue
            self.audit(conversation, "ticket_opened", "sistema", tid)
            return Ticket(tid, conversation, opened, "open", contact_id=contact_id)

    @staticmethod
    def _row_ticket(r: sqlite3.Row) -> Ticket:
        return Ticket(r["ticket_id"], r["conversation"], r["opened_at"], r["status"], r["closed_at"], r["closed_by"], r["contact_id"],
                      r["parent_ticket_id"], r["team"], r["visibility"] or "normal", r["facts"])

    def get_open_ticket(self, conversation: str) -> Ticket | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM tickets WHERE conversation=? AND status='open' ORDER BY opened_at DESC LIMIT 1", (conversation,)).fetchone()
        return self._row_ticket(r) if r else None

    def close_ticket(self, conversation: str, closed_by: str, facts: dict | None = None) -> Ticket | None:
        """Fecha o protocolo aberto (uma única vez); devolve o ticket fechado ou None se não havia ticket aberto.

        Os fatos só são gravados se o protocolo tem dono e visibilidade normal (RTK-05); protocolo restrito ou sem dono não guarda nada.
        """
        with self._lock, self._conn:
            r = self._conn.execute("SELECT * FROM tickets WHERE conversation=? AND status='open' ORDER BY opened_at DESC LIMIT 1", (conversation,)).fetchone()
            if r is None:
                return None
            closed = _now()
            keep = json.dumps(facts, ensure_ascii=False) if facts and r["contact_id"] and (r["visibility"] or "normal") == "normal" else None
            self._conn.execute("UPDATE tickets SET status='closed', closed_at=?, closed_by=?, facts=? WHERE ticket_id=?",
                               (closed, closed_by, keep, r["ticket_id"]))
            self.audit(conversation, "ticket_closed", closed_by, r["ticket_id"])
            return Ticket(r["ticket_id"], conversation, r["opened_at"], "closed", closed, closed_by, r["contact_id"], r["parent_ticket_id"], r["team"],
                          r["visibility"] or "normal", keep)

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id.upper(),)).fetchone()
        return self._row_ticket(r) if r else None

    def list_contact_tickets(self, contact_id: str | None, *, exclude: str | None = None, limit: int = 5, include_restricted: bool = False) -> list[Ticket]:
        """Protocolos do mesmo contato, do mais recente ao mais antigo. `no_chat` nunca entra; `restricted` só se pedido."""
        if not contact_id:
            return []
        allowed = ("normal", "restricted") if include_restricted else ("normal",)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tickets WHERE contact_id=? AND visibility IN ({','.join('?' * len(allowed))}) AND ticket_id<>? "
                "ORDER BY opened_at DESC LIMIT ?", (contact_id, *allowed, exclude or "", limit)).fetchall()
        return [self._row_ticket(r) for r in rows]

    def set_ticket_visibility(self, conversation: str, visibility: str, *, detach_contact: bool = False) -> None:
        """Marca o protocolo aberto. `no_chat` também solta o contato: o protocolo deixa de ser retomável (RTK-07)."""
        if visibility not in ("normal", "restricted", "no_chat"):
            raise ValueError(visibility)
        guard = " AND visibility='normal'" if visibility == "restricted" else ""  # "sem chat" nunca volta a "restrito" (mais permissivo)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tickets SET visibility=?, contact_id=CASE WHEN ? THEN NULL ELSE contact_id END WHERE conversation=? AND status='open'" + guard,
                (visibility, int(detach_contact), conversation))

    def conversation_has_restricted(self, conversation: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM tickets WHERE conversation=? AND visibility<>'normal' LIMIT 1", (conversation,)).fetchone() is not None

    def set_ticket_team(self, conversation: str, team: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE tickets SET team=? WHERE conversation=? AND status='open'", (team, conversation))

    def link_parent(self, conversation: str, parent_ticket_id: str) -> bool:
        """Liga o protocolo aberto ao anterior, uma única vez e nunca a si mesmo (RTK-04)."""
        with self._lock, self._conn:
            r = self._conn.execute("SELECT ticket_id FROM tickets WHERE conversation=? AND status='open'", (conversation,)).fetchone()
            if r is None or r["ticket_id"] == parent_ticket_id.upper():
                return False
            cur = self._conn.execute("UPDATE tickets SET parent_ticket_id=? WHERE ticket_id=? AND parent_ticket_id IS NULL",
                                     (parent_ticket_id.upper(), r["ticket_id"]))
            return cur.rowcount == 1

    def purge_ticket_facts(self, days: int, now: datetime | None = None) -> int:
        """Retenção: apaga fatos e dono dos protocolos encerrados há mais de `days` dias; a linha do protocolo permanece (RTK-06)."""
        limit = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE tickets SET facts=NULL, contact_id=NULL WHERE status='closed' AND closed_at<? "
                                     "AND (facts IS NOT NULL OR contact_id IS NOT NULL)", (limit,))
            return cur.rowcount

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
