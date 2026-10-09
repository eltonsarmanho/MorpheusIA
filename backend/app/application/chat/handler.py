"""Tratamento dos eventos do Chatwoot: idempotência, estado de atendimento, resposta e transferência.

Inbox = canal de entrada; team = responsável; etiqueta = classificação; estado de atendimento = controle do bot.
Só o estado (tabela `conversations`) decide se o bot responde; etiquetas são informativas (CHW-12).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.application.answering.orchestrator import TEAM_GENERAL, Orchestrator, QuestionError
from app.application.chat import flow
from app.domain.handoff import HandoffState, InvalidTransition, bot_may_reply
from app.domain.models import Option, ResponseKind
from app.domain.policies import find_process_numbers
from app.domain.ports import ChatwootGateway
from app.infrastructure.sqlite.operational_store import ConversationState, OperationalStore

log = logging.getLogger(__name__)

HANDOFF_OK_TEXT = "Encaminhei a sua conversa para a equipe {team}. Uma pessoa vai dar continuidade ao atendimento por aqui."
HANDOFF_RETRY_TEXT = ("Ainda não consegui concluir o encaminhamento para um atendente. Vou tentar de novo quando você enviar a próxima "
                      "mensagem.")
HANDOFF_FAILED_TEXT = ("Não foi possível concluir o encaminhamento automaticamente. A conversa foi marcada para atenção da equipe, que "
                       "retomará o atendimento assim que possível.")
HANDOFF_FAILED_UNFLAGGED_TEXT = ("Não foi possível concluir o encaminhamento automaticamente e também não consegui sinalizar a equipe. "
                                 "Tente novamente mais tarde ou procure outro canal de atendimento.")
INTERACTIVE_BODY_LIMIT = 900  # limite do WhatsApp é 1024; margem para emojis e quebras
TECHNICAL_TEXT = "Tive uma dificuldade técnica para processar a sua mensagem. Tente novamente em instantes ou peça para falar com um atendente."
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
                 max_question_chars: int = 1000, default_account_id: int = 1, rich_flow: bool = False) -> None:
        self.orch, self.ops, self.gw = orchestrator, ops, gateway
        self.default_account = default_account_id
        self.rich = rich_flow  # menus com botões/listas, protocolo (ticket) e encerramento guiado
        self.max_attempts, self.max_chars = max_handoff_attempts, max_question_chars
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    # ---------------------------------------------------------- entrada
    def claim(self, payload: dict) -> tuple[str, str | None]:
        """Classifica o evento e reserva a chave de idempotência. Devolve (tipo, event_key|None se duplicado/ignorado)."""
        event = payload.get("event", "")
        account = self._account(payload)
        conv = payload.get("conversation") or {}
        conv_id = conv.get("id") or (payload.get("id") if event.startswith("conversation_") else None)
        if not account or not conv_id:
            return "ignored", None
        if event == "message_created":
            key = f"msg:{account}:{conv_id}:{payload.get('id')}"
        elif event in ("conversation_resolved", "conversation_opened", "conversation_status_changed", "conversation_updated"):
            stamp = payload.get("updated_at")
            if not stamp:
                return event, None  # sem carimbo não há chave segura; as transições de estado já são idempotentes
            key = f"{event}:{account}:{conv_id}:{payload.get('status') or conv.get('status')}:{stamp}"
        else:
            return "ignored", None
        return ("duplicate", None) if not self.ops.claim_event(key) else (event, key)

    def _account(self, payload: dict) -> int:
        """Eventos de conversa do Agent Bot não trazem `account`; nesse caso vale a conta configurada."""
        return int((payload.get("account") or {}).get("id") or payload.get("account_id") or self.default_account)

    def handle(self, payload: dict, event_key: str | None = None) -> HandleResult:
        event = payload.get("event", "")
        account = self._account(payload)
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
                if event == "message_created" and self.ops.get(key).handoff_state in (HandoffState.BOT_ACTIVE, HandoffState.AUTOMATION_RESUMED):
                    try:
                        self._send(account, conv_id, TECHNICAL_TEXT)
                    except Exception:  # noqa: BLE001
                        pass
                return HandleResult("error", type(exc).__name__)

    # --------------------------------------------------------- mensagens
    def _on_message(self, payload: dict, account: int, conv_id: int, key: str) -> HandleResult:
        mtype = _msg_type(payload.get("message_type"))
        sender = payload.get("sender") or {}
        sender_type = str(sender.get("type") or "").lower()
        if payload.get("private"):
            if self.rich and mtype == "outgoing" and sender_type in ("user", "agent"):
                cmd = (payload.get("content") or "").strip().lower()
                if cmd == "/encerrar" or cmd.startswith("/encerrar "):
                    return self._close_by_agent(account, conv_id, key)
            return HandleResult("ignored", "mensagem privada")
        if self.rich:
            self.ops.touch(key)  # base da regra de inatividade (qualquer mensagem pública)
        st = self.ops.get(key)

        if mtype == "outgoing":
            # CHW-07: ignora o próprio bot; mensagem de agente humano indica atuação humana
            if sender_type in ("agent_bot", "bot") or sender.get("type") == "AgentBot" or self.ops.has_event(f"botmsg:{account}:{payload.get('id')}"):
                return HandleResult("ignored", "mensagem do bot")
            if sender_type in ("user", "agent") and st.handoff_state in (
                HandoffState.BOT_ACTIVE, HandoffState.HANDOFF_REQUESTED, HandoffState.HANDOFF_IN_PROGRESS, HandoffState.AUTOMATION_RESUMED
            ):
                self._to_human_active(key, st, "agente humano respondeu")
            return HandleResult("ignored", "mensagem de saída")
        if mtype != "incoming":
            return HandleResult("ignored", f"tipo {mtype}")

        meta = (payload.get("conversation") or {}).get("meta") or {}
        human_assignee = bool(meta.get("assignee")) and meta.get("assignee_type") != "AgentBot"  # o próprio bot pode ser o responsável
        if human_assignee and st.handoff_state is HandoffState.BOT_ACTIVE:  # após retomada explícita, o responsável antigo é ignorado
            self._to_human_active(key, st, "conversa já atribuída a um agente")
            return HandleResult("silent", "atribuída a humano")

        if self.rich and st.handoff_state is HandoffState.HUMAN_CLOSED and self.ops.get_open_ticket(key) is None:
            # o atendimento anterior foi encerrado: a nova mensagem abre um novo protocolo e devolve a conversa à automação
            st = self.ops.transition(key, HandoffState.AUTOMATION_RESUMED, actor="sistema", detail="novo atendimento após encerramento")
        if st.handoff_state is HandoffState.HANDOFF_IN_PROGRESS:
            # tentativa interrompida (queda do processo): volta a "solicitado" para repetir
            st = self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail="recuperação após interrupção")
        if st.handoff_state is HandoffState.HANDOFF_REQUESTED:
            return self._retry_handoff(account, conv_id, key, st)
        if not bot_may_reply(st.handoff_state):
            self.ops.audit(key, "bot_silent", "bot", st.handoff_state.value)
            return HandleResult("silent", st.handoff_state.value)  # CHW-02

        content = (payload.get("content") or "").strip()
        if self.rich:
            return self._rich_incoming(payload, account, conv_id, key, st, content)
        return self._answer(account, conv_id, key, st, content)

    def _answer(self, account: int, conv_id: int, key: str, st: ConversationState, content: str) -> HandleResult:
        try:
            turn = self.orch.respond(content, st)
        except QuestionError:
            self._send(account, conv_id, QUESTION_ERROR_TEXT.format(n=self.max_chars))
            return HandleResult("processed", "pergunta inválida")

        reply = turn.reply
        self.ops.save(turn.state)
        if reply.kind is ResponseKind.HANDOFF:
            return self._do_handoff(account, conv_id, key, turn.state, turn.handoff_team or TEAM_GENERAL, reply.handoff_reason or "", turn.labels)
        if self.rich and (reply.kind is ResponseKind.CLARIFY or reply.trace.get("deterministic") == "lista_de_processos"):
            # no WhatsApp o acervo não é listado: o usuário precisa informar o número do processo
            turn.state.awaiting = "process"
            self.ops.save(turn.state)
            self._send(account, conv_id, flow.PROCESS_PICK_TEXT, flow.PROCESS_ASK_OPTIONS)
        else:
            self._send(account, conv_id, reply.text)
            if self.rich and reply.kind in (ResponseKind.ANSWER, ResponseKind.ABSTAIN, ResponseKind.GREETING):
                self._send(account, conv_id, flow.AFTER_ANSWER_TEXT, flow.AFTER_ANSWER)
        self._safe_label(account, conv_id, turn.labels + (["ia_falha"] if "ia_falha" in turn.labels else []))
        self.ops.audit(key, f"reply:{reply.kind.value}", "bot", str(reply.trace.get("abstain_reason") or ""))
        if reply.trace.get("injection_flagged"):  # SEC-02: o evento fica no registro de auditoria, não só no trace
            self.ops.audit(key, "injection_flagged", "bot", ",".join(reply.trace["injection_flagged"]))
        return HandleResult("processed", reply.kind.value)

    # ------------------------------------------------------- fluxo guiado (WhatsApp)
    def _known_processes(self) -> list[str]:
        return list(self.orch.store.approved_process_numbers())  # type: ignore[attr-defined]

    def _ensure_ticket(self, account: int, conv_id: int, key: str, status: str | None) -> bool:
        """Garante o protocolo do atendimento. Devolve True quando acabou de abrir um novo (e o anunciou)."""
        if self.ops.get_open_ticket(key) is not None:
            return False
        ticket = self.ops.open_ticket(key)
        self._send(account, conv_id, flow.opening_text(ticket.ticket_id, ticket.opened_at))
        try:  # nota privada para a equipe e conversa de volta a "pendente" (atendida pelo bot)
            self._send(account, conv_id, f"🎫 {ticket.ticket_id} aberto em {flow.fmt_time(ticket.opened_at)}", private=True)
            if status and status != "pending":
                self.gw.set_status(account, conv_id, "pending")
        except Exception:  # noqa: BLE001 - informativo
            log.warning("não foi possível registrar a nota do ticket")
        return True

    def _rich_incoming(self, payload: dict, account: int, conv_id: int, key: str, st: ConversationState, content: str) -> HandleResult:
        status = (payload.get("conversation") or {}).get("status")
        self._ensure_ticket(account, conv_id, key, status)
        known = self._known_processes()
        action = flow.parse(content, known)
        k = action.kind
        awaiting, st.awaiting = st.awaiting, None  # a expectativa vale só para a mensagem seguinte
        self.ops.save(st)
        if k is flow.Kind.CLOSE:
            return self._close_by_user(account, conv_id, key)
        if k is flow.Kind.CLOSE_HINT:
            self._send(account, conv_id, flow.CLOSE_HINT_TEXT, flow.CLOSE_HINT_OPTIONS)
            return HandleResult("processed", "encerrar_digitado_sem_efeito")
        if k is flow.Kind.MENU:
            name = ((payload.get("sender") or {}).get("name") or "").split(" ")[0].strip()
            self._send(account, conv_id, flow.MAIN_MENU_TEXT.format(name=f", {name.title()}" if name.isalpha() else ""), flow.MAIN_MENU)
            return HandleResult("processed", "menu")
        if k is flow.Kind.PROCESS_ASK:
            st.awaiting = "process"
            self.ops.save(st)
            self._send(account, conv_id, flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)  # pergunta qual processo, em linguagem natural
            return HandleResult("processed", "perguntou_qual_processo")
        if awaiting == "process" and k is flow.Kind.FREE:
            numbers = find_process_numbers(content)
            if not numbers:
                st.awaiting = "process"
                self.ops.save(st)
                self._send(account, conv_id, flow.PROCESS_NO_NUMBER_TEXT, flow.PROCESS_ASK_OPTIONS)
                return HandleResult("processed", "numero_nao_identificado")
            if numbers[0] not in known:
                st.awaiting = "process"
                self.ops.save(st)
                self._send(account, conv_id, flow.PROCESS_NOT_FOUND_TEXT, flow.PROCESS_ASK_OPTIONS)
                return HandleResult("processed", "processo_fora_do_acervo")
            action, k = flow.Action(flow.Kind.SELECT_PROCESS, numbers[0]), flow.Kind.SELECT_PROCESS
        if k is flow.Kind.SELECT_PROCESS:
            st.process_number = action.arg
            self.ops.save(st)
            docs = len(self.orch.store.list_documents(process_number=action.arg, limit=5000))  # type: ignore[attr-defined]
            info = self.orch.store.process_info(action.arg)  # type: ignore[attr-defined]
            self._send(account, conv_id, flow.process_summary(info, docs))
            self._send(account, conv_id, flow.PROCESS_NEXT_TEXT, flow.PROCESS_MENU)
            return HandleResult("processed", "processo_selecionado")
        if k is flow.Kind.INST_MENU:
            self._send(account, conv_id, flow.INST_MENU_TEXT, flow.inst_options())
            return HandleResult("processed", "menu_institucional")
        if k is flow.Kind.LEGAL_MENU:
            self._send(account, conv_id, flow.LEGAL_MENU_TEXT, flow.legal_options())
            return HandleResult("processed", "menu_juridico")
        if k is flow.Kind.PROMPT:
            if action.arg == "outra_proc" and not st.process_number:
                st.awaiting = "process"
                self.ops.save(st)
                self._send(account, conv_id, flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)
            else:
                self._send(account, conv_id, flow.PROMPTS[action.arg].format(n=st.process_number or ""))
            return HandleResult("processed", "aguardando_texto")
        if k is flow.Kind.HUMAN:
            return self._answer(account, conv_id, key, st, "quero falar com um atendente")
        if k is flow.Kind.ASK:
            question = action.arg
            if action.arg in flow.PROCESS_QUESTIONS:
                if not st.process_number:
                    st.awaiting = "process"
                    self.ops.save(st)
                    self._send(account, conv_id, flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)
                    return HandleResult("processed", "processo_necessario")
                question = flow.PROCESS_QUESTIONS[action.arg].format(n=st.process_number)
            return self._answer(account, conv_id, key, st, question)
        # texto livre: a pergunta de fato
        return self._answer(account, conv_id, key, st, content)

    def _close_by_user(self, account: int, conv_id: int, key: str) -> HandleResult:
        """Opção ENCERRAR do usuário: fecha o protocolo, avisa e marca a conversa como resolvida."""
        return self._finish(account, conv_id, key, "usuario")

    def close_inactive(self, hours: float = 23.0, now: datetime | None = None) -> list[str]:
        """Encerra protocolos sem nenhuma mensagem há mais de `hours` (23 h: ainda dentro da janela de 24 h do WhatsApp)."""
        now = now or datetime.now(timezone.utc)
        closed: list[str] = []
        for t in self.ops.list_tickets("open", 500):
            last = self.ops.last_activity(t.conversation) or t.opened_at
            if now - datetime.fromisoformat(last) < timedelta(hours=hours):
                continue
            account, conv_id = (int(x) for x in t.conversation.split(":"))
            with self._locks[t.conversation]:
                if self.ops.get_open_ticket(t.conversation) is None:
                    continue
                try:
                    self._finish(account, conv_id, t.conversation, "sistema")
                    closed.append(t.ticket_id)
                except Exception:  # noqa: BLE001 - tenta de novo no próximo ciclo
                    log.warning("falha ao encerrar por inatividade %s", t.ticket_id)
        return closed

    def _close_by_agent(self, account: int, conv_id: int, key: str) -> HandleResult:
        """Nota privada `/encerrar` do atendente: fecha o protocolo e resolve a conversa no Chatwoot."""
        return self._finish(account, conv_id, key, "atendente")

    def _finish(self, account: int, conv_id: int, key: str, closed_by: str) -> HandleResult:
        ticket = self.ops.close_ticket(key, closed_by)
        if ticket is not None:
            self._send(account, conv_id, flow.closing_text(ticket.ticket_id, ticket.opened_at, ticket.closed_at or ticket.opened_at, closed_by))
            if closed_by == "atendente":
                try:
                    self._send(account, conv_id, f"✅ {ticket.ticket_id} encerrado via /encerrar", private=True)
                except Exception:  # noqa: BLE001
                    pass
        st = self.ops.get(key)
        if st.handoff_state is HandoffState.HUMAN_ACTIVE:
            self.ops.transition(key, HandoffState.HUMAN_CLOSED, actor=closed_by, detail="atendimento encerrado")
        st = self.ops.get(key)
        st.process_number, st.offer_pending, st.failed_retrievals = None, False, 0
        self.ops.save(st)
        try:
            self.gw.set_status(account, conv_id, "resolved")  # "Resolvido" no Chatwoot
        except Exception:  # noqa: BLE001
            self.ops.audit(key, "resolve_failed", "bot", closed_by)
            self._safe_label(account, conv_id, ["ia_falha"])
        return HandleResult("processed", f"encerrado_por_{closed_by}")

    # ------------------------------------------------------ transferência
    def _do_handoff(self, account: int, conv_id: int, key: str, st: ConversationState, team: str, reason: str, labels: list[str]) -> HandleResult:
        self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail=reason)
        st = self.ops.get(key)
        st.handoff_team, st.handoff_reason = team, reason
        self.ops.save(st)
        return self._attempt_handoff(account, conv_id, key, st, labels)

    def _retry_handoff(self, account: int, conv_id: int, key: str, st: ConversationState) -> HandleResult:
        if st.handoff_attempts >= self.max_attempts:
            # limite esgotado: não insiste a cada mensagem; a equipe resolve e libera pelo comando administrativo
            self.ops.audit(key, "handoff_exhausted", "bot", f"{st.handoff_attempts} tentativas")
            return HandleResult("silent", "tentativas de transferência esgotadas")
        return self._attempt_handoff(account, conv_id, key, st, ["humano"])

    def retry_handoff(self, account: int, conv_id: int, actor: str, reason: str) -> HandleResult:
        """Comando administrativo: zera as tentativas e repete a transferência."""
        key = conversation_key(account, conv_id)
        with self._locks[key]:
            st = self.ops.get(key)
            if st.handoff_state is not HandoffState.HANDOFF_REQUESTED:
                raise InvalidTransition(f"nada a repetir no estado {st.handoff_state.value}")
            st.handoff_attempts = 0
            self.ops.save(st)
            self.ops.audit(key, "handoff_retry_requested", actor, reason)
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
            confirmed = bool(ok_team and ok_status)  # a transferência é confirmada por equipe + status; etiqueta é informativa
        except Exception as exc:  # noqa: BLE001
            log.warning("transferência falhou: %s", type(exc).__name__)
            confirmed = False
        if confirmed:
            self._safe_label(account, conv_id, list(dict.fromkeys(labels + ["humano"])))
            self.ops.transition(key, HandoffState.HUMAN_ACTIVE, actor="chatwoot", detail="transferência confirmada pela API")
            st = self.ops.get(key)
            st.handoff_attempts = 0
            self.ops.save(st)
            try:
                notice = HANDOFF_OK_TEXT.format(team=team)
                ticket = self.ops.get_open_ticket(key) if self.rich else None
                if ticket:
                    notice += f"\n🎫 Protocolo: *{ticket.ticket_id}*"
                self._send(account, conv_id, notice)  # só informa após a confirmação (CHW-10)
            except Exception:  # noqa: BLE001 - a transferência já ocorreu; o aviso falhou
                log.warning("transferência confirmada, mas o aviso ao usuário falhou")
                self.ops.audit(key, "handoff_notice_failed", "bot", team)
                self._safe_label(account, conv_id, ["ia_falha"])
            return HandleResult("handoff", team)
        self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail="transferência não confirmada")
        giving_up = self.ops.get(key).handoff_attempts >= self.max_attempts
        flagged = self._safe_label(account, conv_id, ["ia_falha", "humano"] if giving_up else ["ia_falha"])
        text = HANDOFF_RETRY_TEXT
        if giving_up:
            text = HANDOFF_FAILED_TEXT if flagged else HANDOFF_FAILED_UNFLAGGED_TEXT
            self.ops.audit(key, "handoff_exhausted", "bot", f"equipe={team}")
        try:
            self._send(account, conv_id, text)
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
        if payload.get("event") == "conversation_resolved":
            status = "resolved"
        st = self.ops.get(key)
        if status == "resolved" and self.rich:
            ticket = self.ops.close_ticket(key, "atendente")  # resolvido direto na interface do Chatwoot
            if ticket is not None:
                account, conv_id = (int(x) for x in key.split(":"))
                try:
                    self._send(account, conv_id, flow.closing_text(ticket.ticket_id, ticket.opened_at, ticket.closed_at or ticket.opened_at, "atendente"))
                except Exception:  # noqa: BLE001
                    self.ops.audit(key, "closing_notice_failed", "bot", ticket.ticket_id)
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
        try:
            self.gw.set_status(account, conv_id, "pending")  # conversa volta a ser da automação
        except Exception:  # noqa: BLE001 - o estado local já libera o bot; o status é só coerência no Chatwoot
            self.ops.audit(key, "resume_status_failed", actor, "não foi possível voltar o status para pending")
        return st

    def _send(self, account: int, conv_id: int, text: str, options: list[Option] | None = None, private: bool = False) -> int | None:
        """Envia e registra o id da mensagem para reconhecer o eco do webhook como do próprio bot."""
        if options and len(text) > INTERACTIVE_BODY_LIMIT:
            # o WhatsApp rejeita corpo interativo acima de 1024 caracteres: o texto longo vai antes, em mensagem comum
            self._send(account, conv_id, text)
            text = "Escolha uma opção 👇"
        if options or private:
            msg_id = self.gw.send_message(account, conv_id, text, options=options, private=private)
        else:
            msg_id = self.gw.send_message(account, conv_id, text)
        if msg_id:
            self.ops.claim_event(f"botmsg:{account}:{msg_id}")
        return msg_id

    def _safe_label(self, account: int, conv_id: int, labels: list[str]) -> bool:
        try:
            self.gw.add_labels(account, conv_id, list(dict.fromkeys(labels)))
            return True
        except Exception:  # noqa: BLE001 - etiqueta é informativa; não pode derrubar o atendimento
            log.warning("falha ao aplicar etiquetas %s", labels)
            return False
