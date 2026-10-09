"""Tratamento dos eventos do Chatwoot: idempotência, estado de atendimento, resposta e transferência.

Inbox = canal de entrada; team = responsável; etiqueta = classificação; estado de atendimento = controle do bot.
Só o estado (tabela `conversations`) decide se o bot responde; etiquetas são informativas (CHW-12).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.application.answering.orchestrator import TEAM_GENERAL, Orchestrator, QuestionError
from app.domain.handoff import HandoffState, InvalidTransition, bot_may_reply
from app.domain.models import ResponseKind
from app.domain.ports import ChatwootGateway
from app.infrastructure.sqlite.operational_store import ConversationState, OperationalStore

log = logging.getLogger(__name__)

HANDOFF_OK_TEXT = "Encaminhei a sua conversa para a equipe {team}. Uma pessoa vai dar continuidade ao atendimento por aqui."
HANDOFF_RETRY_TEXT = ("Ainda não consegui concluir o encaminhamento para um atendente. Vou tentar de novo quando você enviar a próxima "
                      "mensagem.")
HANDOFF_FAILED_TEXT = ("Não foi possível concluir o encaminhamento automaticamente. A equipe foi sinalizada e retomará a conversa assim que "
                       "possível.")
QUESTION_ERROR_TEXT = "Não consegui ler a sua mensagem. Envie uma pergunta com até {n} caracteres."


@dataclass
class HandleResult:
    outcome: str  # processed | duplicate | ignored | silent | handoff | error
    detail: str = ""


def conversation_key(account_id: int, conversation_id: int) -> str:
    return f"{account_id}:{conversation_id}"


def _msg_type(value: Any) -> str:
    if isinstance(value, int):
        return {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(value, "unknown")
    return str(value or "").lower()


class ChatwootEventHandler:
    def __init__(self, orchestrator: Orchestrator, ops: OperationalStore, gateway: ChatwootGateway, *, max_handoff_attempts: int = 3,
                 max_question_chars: int = 1000) -> None:
        self.orch, self.ops, self.gw = orchestrator, ops, gateway
        self.max_attempts, self.max_chars = max_handoff_attempts, max_question_chars
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    # ---------------------------------------------------------- entrada
    def claim(self, payload: dict) -> tuple[str, str | None]:
        """Classifica o evento e reserva a chave de idempotência. Devolve (tipo, event_key|None se duplicado/ignorado)."""
        event = payload.get("event", "")
        account = int((payload.get("account") or {}).get("id") or 0)
        conv = payload.get("conversation") or {}
        conv_id = conv.get("id") or (payload.get("id") if event.startswith("conversation_") else None)
        if not account or not conv_id:
            return "ignored", None
        if event == "message_created":
            key = f"msg:{account}:{conv_id}:{payload.get('id')}"
        elif event in ("conversation_status_changed", "conversation_updated"):
            key = f"{event}:{account}:{conv_id}:{payload.get('status') or conv.get('status')}:{payload.get('updated_at') or ''}"
        else:
            return "ignored", None
        return ("duplicate", None) if not self.ops.claim_event(key) else (event, key)

    def handle(self, payload: dict, event_key: str | None = None) -> HandleResult:
        event = payload.get("event", "")
        account = int((payload.get("account") or {}).get("id") or 0)
        conv = payload.get("conversation") or {}
        conv_id = int(conv.get("id") or payload.get("id") or 0)
        key = conversation_key(account, conv_id)
        with self._locks[key]:
            try:
                if event == "message_created":
                    return self._on_message(payload, account, conv_id, key)
                return self._on_status(payload, key)
            except Exception as exc:  # noqa: BLE001
                log.exception("erro ao tratar evento %s da conversa %s", event, key)
                self.ops.audit(key, "error", "bot", f"{type(exc).__name__}")
                if event_key and event == "message_created":
                    self.ops.release_event(event_key)  # permite reentrega sem perder a mensagem
                self._safe_label(account, conv_id, ["ia_falha"])
                return HandleResult("error", type(exc).__name__)

    # --------------------------------------------------------- mensagens
    def _on_message(self, payload: dict, account: int, conv_id: int, key: str) -> HandleResult:
        mtype = _msg_type(payload.get("message_type"))
        sender = payload.get("sender") or {}
        sender_type = str(sender.get("type") or "").lower()
        if payload.get("private"):
            return HandleResult("ignored", "mensagem privada")
        st = self.ops.get(key)

        if mtype == "outgoing":
            # CHW-07: ignora o próprio bot; mensagem de agente humano indica atuação humana
            if sender_type in ("agent_bot", "bot") or sender.get("type") == "AgentBot":
                return HandleResult("ignored", "mensagem do bot")
            if sender_type in ("user", "agent") and st.handoff_state in (
                HandoffState.BOT_ACTIVE, HandoffState.HANDOFF_REQUESTED, HandoffState.HANDOFF_IN_PROGRESS, HandoffState.AUTOMATION_RESUMED
            ):
                self._to_human_active(key, st, "agente humano respondeu")
            return HandleResult("ignored", "mensagem de saída")
        if mtype != "incoming":
            return HandleResult("ignored", f"tipo {mtype}")

        meta_assignee = ((payload.get("conversation") or {}).get("meta") or {}).get("assignee")
        if meta_assignee and st.handoff_state in (HandoffState.BOT_ACTIVE, HandoffState.AUTOMATION_RESUMED):
            self._to_human_active(key, st, "conversa já atribuída a um agente")
            return HandleResult("silent", "atribuída a humano")

        if st.handoff_state is HandoffState.HANDOFF_REQUESTED:
            return self._retry_handoff(account, conv_id, key, st)
        if not bot_may_reply(st.handoff_state):
            self.ops.audit(key, "bot_silent", "bot", st.handoff_state.value)
            return HandleResult("silent", st.handoff_state.value)  # CHW-02

        content = (payload.get("content") or "").strip()
        try:
            turn = self.orch.respond(content, st)
        except QuestionError:
            self.gw.send_message(account, conv_id, QUESTION_ERROR_TEXT.format(n=self.max_chars))
            return HandleResult("processed", "pergunta inválida")

        reply = turn.reply
        self.ops.save(turn.state)
        if reply.kind is ResponseKind.HANDOFF:
            return self._do_handoff(account, conv_id, key, turn.state, turn.handoff_team or TEAM_GENERAL, reply.handoff_reason or "", turn.labels)
        self.gw.send_message(account, conv_id, reply.text)
        self._safe_label(account, conv_id, turn.labels + (["ia_falha"] if "ia_falha" in turn.labels else []))
        self.ops.audit(key, f"reply:{reply.kind.value}", "bot", str(reply.trace.get("abstain_reason") or ""))
        return HandleResult("processed", reply.kind.value)

    # ------------------------------------------------------ transferência
    def _do_handoff(self, account: int, conv_id: int, key: str, st: ConversationState, team: str, reason: str, labels: list[str]) -> HandleResult:
        self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail=reason)
        st = self.ops.get(key)
        st.handoff_team, st.handoff_reason = team, reason
        self.ops.save(st)
        return self._attempt_handoff(account, conv_id, key, st, labels)

    def _retry_handoff(self, account: int, conv_id: int, key: str, st: ConversationState) -> HandleResult:
        return self._attempt_handoff(account, conv_id, key, st, ["humano"])

    def _attempt_handoff(self, account: int, conv_id: int, key: str, st: ConversationState, labels: list[str]) -> HandleResult:
        team = st.handoff_team or TEAM_GENERAL
        self.ops.transition(key, HandoffState.HANDOFF_IN_PROGRESS, actor="bot", detail=f"equipe={team}")
        st = self.ops.get(key)
        st.handoff_attempts += 1
        self.ops.save(st)
        try:
            ok_team = self.gw.assign_team(account, conv_id, team)
            ok_status = self.gw.set_status(account, conv_id, "open")
            self.gw.add_labels(account, conv_id, list(dict.fromkeys(labels + ["humano"])))
            confirmed = bool(ok_team and ok_status)
        except Exception as exc:  # noqa: BLE001
            log.warning("transferência falhou: %s", type(exc).__name__)
            confirmed = False
        if confirmed:
            self.ops.transition(key, HandoffState.HUMAN_ACTIVE, actor="chatwoot", detail="transferência confirmada pela API")
            st = self.ops.get(key)
            st.handoff_attempts = 0
            self.ops.save(st)
            self.gw.send_message(account, conv_id, HANDOFF_OK_TEXT.format(team=team))  # só informa após a confirmação (CHW-10)
            return HandleResult("handoff", team)
        self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail="transferência não confirmada")
        giving_up = self.ops.get(key).handoff_attempts >= self.max_attempts
        self._safe_label(account, conv_id, ["ia_falha"])
        try:
            self.gw.send_message(account, conv_id, HANDOFF_FAILED_TEXT if giving_up else HANDOFF_RETRY_TEXT)
        except Exception:  # noqa: BLE001
            log.warning("não foi possível avisar o usuário sobre a falha do encaminhamento")
        return HandleResult("error", "transferência não confirmada")

    # ------------------------------------------------------------ estados
    def _to_human_active(self, key: str, st: ConversationState, why: str) -> None:
        try:
            self.ops.transition(key, HandoffState.HUMAN_ACTIVE, actor="chatwoot", detail=why)
        except InvalidTransition:
            pass

    def _on_status(self, payload: dict, key: str) -> HandleResult:
        status = payload.get("status") or (payload.get("conversation") or {}).get("status")
        st = self.ops.get(key)
        if status == "resolved" and st.handoff_state is HandoffState.HUMAN_ACTIVE:
            self.ops.transition(key, HandoffState.HUMAN_CLOSED, actor="chatwoot", detail="conversa resolvida")
            return HandleResult("processed", "human_closed")
        return HandleResult("ignored", f"status {status}")

    def resume_automation(self, account: int, conv_id: int, actor: str, reason: str) -> ConversationState:
        """CHW-04: retomada somente por comando explícito, com autor e motivo auditados."""
        key = conversation_key(account, conv_id)
        st = self.ops.transition(key, HandoffState.AUTOMATION_RESUMED, actor=actor, detail=reason)
        st.failed_retrievals, st.offer_pending, st.handoff_attempts = 0, False, 0
        self.ops.save(st)
        return st

    def _safe_label(self, account: int, conv_id: int, labels: list[str]) -> None:
        try:
            self.gw.add_labels(account, conv_id, list(dict.fromkeys(labels)))
        except Exception:  # noqa: BLE001 - etiqueta é informativa; não pode derrubar o atendimento
            log.warning("falha ao aplicar etiquetas %s", labels)
