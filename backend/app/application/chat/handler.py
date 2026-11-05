"""Tratamento dos eventos do Chatwoot: idempotência, estado de atendimento, resposta e transferência.

Inbox = canal de entrada; team = responsável; etiqueta = classificação; estado de atendimento = controle do bot.
Só o estado (tabela `conversations`) decide se o bot responde; etiquetas são informativas (CHW-12).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.application.answering.orchestrator import TEAM_GENERAL, Orchestrator, QuestionError
from app.application.chat import flow
from app.application.chat import mulher as ovm
from app.application.chat import retomada
from app.application.chat.retomada import ai_summary_note
from app.application.chat.process_search import DemoCorpusProcessSearch, name_tokens
from app.domain.handoff import HandoffState, InvalidTransition, bot_may_reply
from app.domain.models import Option, Profile, ResponseKind
from app.domain.policies import cnj_check_digits_ok, cpf_is_valid, extract_cpf, find_process_numbers, mask_cpf
from app.domain.ports import ChatwootGateway, ProcessCandidate, ProcessSearchPort
from app.domain.triage_mulher import NAO_INFORMADO, OuvidoriaMulher, Triage, mentions_violence
from app.domain.privacy import mask_pii
from app.domain.units import UnitDirectory, fold, format_phone, valid_br_phone, whatsapp_link
from app.infrastructure.sqlite.operational_store import ConversationState, OperationalStore

log = logging.getLogger(__name__)

HANDOFF_RETRY_TEXT = ("Ainda não consegui confirmar o encaminhamento para o atendimento humano. Vou tentar de novo quando você enviar a "
                      "próxima mensagem.")
HANDOFF_FAILED_TEXT = ("Não foi possível concluir o encaminhamento automaticamente. A conversa foi marcada para atenção da equipe, que "
                       "vai retomar o atendimento assim que possível.")
HANDOFF_FAILED_UNFLAGGED_TEXT = ("Não foi possível concluir o encaminhamento automaticamente, e também não consegui sinalizar a equipe. "
                                 "Tente novamente mais tarde ou procure outro canal de atendimento do Tribunal.")
INTERACTIVE_BODY_LIMIT = 900  # limite do WhatsApp é 1024; margem para emojis e quebras
TECHNICAL_TEXT = ("Tive uma dificuldade técnica para processar a sua mensagem. Pode tentar de novo em instantes? Se preferir, peça para "
                  "falar com o atendimento humano.")
QUESTION_ERROR_TEXT = "Não consegui ler a sua mensagem. Pode enviar a pergunta de novo, com até {n} caracteres?"
# Diligência dirigida ao juiz ou urgente vai para o gabinete; o restante, para a secretaria (decisão do responsável, 2026-10-27).
_GABINETE = re.compile(r"\b(gabinete|juiz|juiza|magistrad[oa]|desembargador[a]?|urgente|urgencia)\b")
REQUEST_REASONS = {"diligencia": "solicitação de diligência", "providencia": "solicitação de providência à secretaria",
                   "atendimento": "pedido de atendimento humano"}
PROFILE_LABELS = {Profile.ADVOGADO: "advogado(a)", Profile.PARTE_TESTEMUNHA: "parte ou testemunha", Profile.GERAL: "informações gerais",
                  Profile.CIDADAO: "cidadão (não escolhido no menu)", Profile.INDEFINIDO: "não informado"}


def _looks_like_number(text: str) -> bool:
    """Tentativa de número de processo fora do formato: muitos dígitos e quase nenhuma palavra."""
    return len(re.sub(r"\D", "", text)) >= 7 and len(re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)) <= 1


@dataclass(frozen=True)
class MulherSettings:
    """Ouvidoria da Mulher (OVM). Desligada por padrão: sem `enabled`, o fluxo e o menu são os anteriores."""

    enabled: bool = False
    team: str = ovm.TEAM_DEFAULT
    contacts: OuvidoriaMulher = OuvidoriaMulher()
    max_relato_chars: int = 2000


@dataclass(frozen=True)
class TicketSettings:
    """Retomada por protocolo e resumo para o atendente (RTK). Desligada por padrão: sem `enabled`, tudo é como antes."""

    enabled: bool = False
    ai_enabled: bool = False  # resumo por IA (fase 2); só roda com `enabled`
    retention_days: int = 90
    base_url: str = ""  # URL pública do Chatwoot, para os links das conversas anteriores


AI_WINDOW_SLACK_S = 2
_SPACED_PHONE = re.compile(r"(?<!\d)(?:\+?55[\s.]?)?\(?\d{2}\)?[\s.\-]?9?\d{4}[\s.\-]?\d{4}(?!\d)")  # telefone com espaços, que mask_pii não cobre
_TRIAGE_ONLY = frozenset({flow.Kind.MULHER_ANSWER, flow.Kind.MULHER_SKIP, flow.Kind.MULHER_SEND, flow.Kind.MULHER_REDO})


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
                 max_question_chars: int = 1000, default_account_id: int = 1, rich_flow: bool = False, units: UnitDirectory | None = None,
                 search: ProcessSearchPort | None = None, teams: tuple[str, str] = ("secretaria", "gabinete"),
                 whatsapp_mode: str = "chatwoot", max_diligence_chars: int = 3000, mulher: MulherSettings | None = None,
                 tickets: TicketSettings | None = None) -> None:
        self.orch, self.ops, self.gw = orchestrator, ops, gateway
        self.units = units or UnitDirectory()
        self.search = search or DemoCorpusProcessSearch(orchestrator.store)
        self.team_secretaria, self.team_gabinete = teams
        self.whatsapp_mode = whatsapp_mode  # chatwoot: transferência integrada nesta conversa | link: WhatsApp oficial da unidade
        self.max_diligence = max_diligence_chars
        self.mulher = mulher or MulherSettings()
        self.tickets = tickets or TicketSettings()
        self._threads: list[threading.Thread] = []  # resumos por IA em segundo plano (os testes aguardam por eles)
        self.now = lambda: datetime.now(timezone.utc)  # relógio injetável (horário da Ouvidoria nos testes)
        self.default_account = default_account_id
        self.rich = rich_flow  # menus com botões/listas, protocolo (ticket) e encerramento guiado
        self.max_attempts, self.max_chars = max_handoff_attempts, max_question_chars
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last_public: dict[tuple[int, int], int] = {}

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
                raw = (payload.get("content") or "").strip()
                cmd = raw.lower()
                if cmd == "/encerrar" or cmd.startswith("/encerrar "):
                    return self._close_by_agent(account, conv_id, key, mask_cpf(raw[len("/encerrar"):].strip())[:retomada.SUMMARY_LIMIT] or None)
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
        if self.rich and st.handoff_state is HandoffState.HANDOFF_REQUESTED and st.request_channel:
            recovered = self._channel_recovery(account, conv_id, key, st, (payload.get("content") or "").strip())
            if recovered is not None:
                return recovered
            st = self.ops.get(key)  # a recuperação pode ter cancelado o pedido pendente
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
        content = mask_cpf(content)  # CPF não vai para a busca nem para o LLM (RTA-09)
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
                if turn.state.process_number:
                    # com processo localizado, o contexto continua: itens do processo, atendente ou encerrar (e não só "menu")
                    self._send(account, conv_id, flow.PROCESS_AFTER_TEXT.format(n=turn.state.process_number), flow.process_menu(turn.state.profile))
                else:
                    self._send(account, conv_id, flow.AFTER_ANSWER_TEXT, flow.AFTER_ANSWER)
        self._safe_label(account, conv_id, turn.labels + (["ia_falha"] if "ia_falha" in turn.labels else []))
        self.ops.audit(key, f"reply:{reply.kind.value}", "bot", str(reply.trace.get("abstain_reason") or ""))
        if reply.trace.get("injection_flagged"):  # SEC-02: o evento fica no registro de auditoria, não só no trace
            self.ops.audit(key, "injection_flagged", "bot", ",".join(reply.trace["injection_flagged"]))
        return HandleResult("processed", reply.kind.value)

    # ------------------------------------------------------- fluxo guiado (WhatsApp)
    def _known_processes(self) -> list[str]:
        return list(self.orch.store.approved_process_numbers())  # type: ignore[attr-defined]

    def _contact_id(self, payload: dict) -> str | None:
        """Id do contato do Chatwoot, só de mensagem recebida da própria pessoa; é o que autentica o telefone na retomada (RTK-02)."""
        sender = payload.get("sender") or {}
        conv = payload.get("conversation") or {}
        sender_type = str(sender.get("type") or "").lower()
        if sender_type not in ("contact", ""):
            return None  # bot, agente ou usuário explícito nunca é o contato
        if sender.get("id"):
            return str(sender["id"])
        # o evento real nem sempre traz `sender.type`: o contato também vem em `conversation.meta.sender` e em `contact_inbox`
        meta_sender = (conv.get("meta") or {}).get("sender") or {}
        if meta_sender.get("id"):
            return str(meta_sender["id"])
        contact_inbox = conv.get("contact_inbox") or {}
        if contact_inbox.get("contact_id"):
            return str(contact_inbox["contact_id"])
        return None

    @staticmethod
    def _contact_phone(payload: dict) -> str | None:
        sender = payload.get("sender") or {}
        meta_sender = ((payload.get("conversation") or {}).get("meta") or {}).get("sender") or {}
        return sender.get("phone_number") or meta_sender.get("phone_number")

    def _ensure_ticket(self, account: int, conv_id: int, key: str, status: str | None, contact_id: str | None = None) -> bool:
        """Garante o protocolo do atendimento. Devolve True quando acabou de abrir um novo (e o anunciou)."""
        if self.ops.get_open_ticket(key) is not None:
            return False
        ticket = self.ops.open_ticket(key, contact_id if self.tickets.enabled else None)
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
        contact = self._contact_id(payload)
        if self._ensure_ticket(account, conv_id, key, status, contact) and self.tickets.enabled and contact is None:
            sender = payload.get("sender") or {}  # diagnóstico sem dado pessoal: só o formato do evento
            self.ops.audit(key, "ticket_sem_contato", "bot", f"sender.type={sender.get('type')!s:.20} chaves={','.join(sorted(sender))[:80]}")
        known = self._known_processes()
        action = flow.parse(content, known)
        k = action.kind
        awaiting, st.awaiting = st.awaiting, None  # a expectativa vale só para a mensagem seguinte
        self.ops.save(st)
        if awaiting == "diligence" and not flow.is_option_click(content):
            # campo livre: tudo o que não é toque em uma opção é o texto da diligência, guardado como chegou (RTA-15)
            return self._receive_diligence(account, conv_id, key, st, payload.get("content") or "")
        tri = Triage.from_json(st.triage) if self.mulher.enabled else None
        if (awaiting in ("mulher_numero", "mulher_relato") and tri is not None and not flow.is_option_click(content)
                and not (k is flow.Kind.CLOSE_HINT or flow.clean(content) == "menu")):  # só "sair" e "menu" saem nas etapas de texto (OVM-17)
            return self._mulher_text(account, conv_id, key, st, tri, awaiting, payload.get("content") or "")
        if k in flow.MULHER_KINDS:
            if not self.mulher.enabled or (k in _TRIAGE_ONLY and tri is None):
                action, k = flow.Action(flow.Kind.FREE), flow.Kind.FREE  # sem a chave ligada ou fora da triagem, é texto comum
            else:
                return self._mulher_action(account, conv_id, key, st, tri, action, awaiting)
        if tri is not None:
            if k is flow.Kind.FREE:
                return self._mulher_ask(account, conv_id, st, tri, stale=True)  # texto em etapa de botões: repete a pergunta
            if k is flow.Kind.HUMAN:
                tri.step = "resumo"  # Falar com atendente: resumo com o que já foi coletado (OVM-14)
                return self._mulher_ask(account, conv_id, st, tri)
            if k is not flow.Kind.CLOSE:
                st.triage = None  # saiu da triagem (menu, outro serviço ou "sair"): nada é guardado (OVM-17)
                self.ops.save(st)
                self.ops.audit(key, "mulher_saida", "usuario", "")
                if k in (flow.Kind.MENU, flow.Kind.CLOSE_HINT):
                    self._send(account, conv_id, ovm.LEFT_TEXT)
                    if k is flow.Kind.CLOSE_HINT:
                        return self._menu(payload, account, conv_id, st)
        if k in flow.RESUME_KINDS and (not self.tickets.enabled or (k in (flow.Kind.PHONE_YES, flow.Kind.PHONE_OTHER, flow.Kind.PHONE_NO)
                                                                    and awaiting not in ("return_phone", "return_phone_number"))):
            action, k = flow.Action(flow.Kind.FREE), flow.Kind.FREE  # sem a chave ligada ou fora do pedido, é texto comum
        if self.tickets.enabled:
            handled = self._resume_or_phone(account, conv_id, key, st, payload, action, awaiting, content)
            if handled is not None:
                return handled
        if awaiting == "channel" and flow.clean(content) == flow.clean(flow.OPT_CH_BALCAO.title):
            action, k = flow.Action(flow.Kind.CHANNEL, "balcao"), flow.Kind.CHANNEL  # na escolha de canal, "Balcão Virtual" é o canal
        if k is flow.Kind.CLOSE:
            return self._close_by_user(account, conv_id, key)
        if k is flow.Kind.CLOSE_HINT:
            self._send(account, conv_id, flow.CLOSE_HINT_TEXT, flow.CLOSE_HINT_OPTIONS)
            return HandleResult("processed", "encerrar_digitado_sem_efeito")
        if k is flow.Kind.MENU:
            return self._menu(payload, account, conv_id, st)
        if k is flow.Kind.PROFILE_MENU:
            st.profile, st.profile_locked = Profile.INDEFINIDO, False
            self.ops.save(st)
            return self._menu(payload, account, conv_id, st)
        if k is flow.Kind.PROFILE:
            st.profile, st.profile_locked = Profile(action.arg), True
            self.ops.save(st)
            self.ops.audit(key, "profile_chosen", "usuario", st.profile.value)
            return self._menu(payload, account, conv_id, st)
        if k is flow.Kind.GERAL_MENU:
            if not st.profile_locked:
                st.profile, st.profile_locked = Profile.GERAL, True
                self.ops.save(st)
                self.ops.audit(key, "profile_chosen", "usuario", st.profile.value)
            self._send(account, conv_id, flow.GERAL_MENU_TEXT, flow.GERAL_MENU)
            return HandleResult("processed", "menu_geral")
        if k is flow.Kind.GERAL_ITEM:
            return self._geral_item(account, conv_id, key, st, action.arg)
        if k is flow.Kind.ID_CPF:
            return self._expect(account, conv_id, st, "cpf", flow.CPF_ASK_TEXT, [flow.OPT_MENU])
        if k is flow.Kind.ID_NAME:
            return self._expect(account, conv_id, st, "name", flow.NAME_ASK_TEXT, [flow.OPT_MENU])
        if k is flow.Kind.VARA_YES:
            return self._expect(account, conv_id, st, "vara_unit", flow.VARA_UNIT_ASK_TEXT, [flow.OPT_MENU])
        if k is flow.Kind.VARA_NO:
            return self._telejudiciario(account, conv_id)
        if k is flow.Kind.PICK or (awaiting == "pick" and k is flow.Kind.FREE):
            return self._pick(account, conv_id, key, st, action.arg if k is flow.Kind.PICK else content.strip())
        if k is flow.Kind.PROC_MENU:
            if not st.process_number:
                return self._expect(account, conv_id, st, "process", flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)
            self._send(account, conv_id, flow.PROCESS_NEXT_TEXT, flow.process_menu(st.profile))
            return HandleResult("processed", "menu_do_processo")
        if k is flow.Kind.DILIGENCE:
            if not st.process_number:
                return self._expect(account, conv_id, st, "process", flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)
            if action.arg == "diligencia":
                return self._expect(account, conv_id, st, "diligence", flow.DILIGENCE_ASK_TEXT, [flow.OPT_MENU])
            st.request_reason = "providencia"
            self.ops.save(st)
            return self._offer_channels(account, conv_id, key, st)
        if k is flow.Kind.CHANNEL:
            return self._choose_channel(account, conv_id, key, st, action.arg)
        if k is flow.Kind.OTHER_CHANNEL:
            return self._offer_channels(account, conv_id, key, st, exclude=st.request_channel)
        if k is flow.Kind.RETRY_CHANNEL:
            if st.request_channel in ("whatsapp", "balcao"):
                return self._choose_channel(account, conv_id, key, st, st.request_channel)
            return self._offer_channels(account, conv_id, key, st)
        if k is flow.Kind.UNIT_PHONE:
            return self._unit_phone(account, conv_id, st)
        if k is flow.Kind.FREE and awaiting:
            handled = self._awaited_text(account, conv_id, key, st, awaiting, content)
            if handled is not None:
                return handled
        if k is flow.Kind.PROCESS_ASK:
            st.awaiting = "process"
            self.ops.save(st)
            self._send(account, conv_id, flow.PROCESS_ASK_TEXT, flow.PROCESS_ASK_OPTIONS)  # pergunta qual processo, em linguagem natural
            return HandleResult("processed", "perguntou_qual_processo")
        if awaiting == "process" and k is flow.Kind.FREE:
            numbers = find_process_numbers(content)
            if not numbers:
                if _looks_like_number(content):
                    return self._expect(account, conv_id, st, "process", flow.PROCESS_BAD_FORMAT_TEXT, flow.PROCESS_ASK_OPTIONS)
                # sem número: é uma nova pergunta, não uma resposta ao pedido; o orquestrador identifica o processo ou pede o número
                return self._answer(account, conv_id, key, st, content)
            if numbers[0] not in known:
                # formato válido não significa processo localizado (RTA-08)
                text = flow.PROCESS_NOT_FOUND_TEXT if cnj_check_digits_ok(numbers[0]) else flow.PROCESS_BAD_DIGITS_TEXT
                st.awaiting = "process"
                self.ops.save(st)
                self._send(account, conv_id, text, flow.PROCESS_ASK_OPTIONS)
                return HandleResult("processed", "processo_fora_do_acervo")
            action, k = flow.Action(flow.Kind.SELECT_PROCESS, numbers[0]), flow.Kind.SELECT_PROCESS
        if k is flow.Kind.SELECT_PROCESS:
            return self._select_process(account, conv_id, key, st, action.arg)
        if k is flow.Kind.INST_MENU:
            url = self.units.contacts.balcao_general_url
            if url:
                self._send(account, conv_id, f"🏛️ A página do Balcão Virtual do TJPA, onde você escolhe a unidade, é esta:\n{url}")
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
            if st.unit_label or st.diligence:
                # unidade identificada: o roteiro pede a escolha explícita do canal (RTA-16)
                st.request_reason = st.request_reason or "atendimento"
                self.ops.save(st)
                return self._offer_channels(account, conv_id, key, st)
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
        if (self.mulher.enabled and k is flow.Kind.FREE and not awaiting and not find_process_numbers(content)
                and mentions_violence(flow.clean(content))):
            self.ops.set_ticket_visibility(key, "restricted")
            self.ops.audit(key, "mulher_oferta", "bot", "")  # só oferece o serviço; nunca inicia a triagem sozinho (OVM-03)
            self._send(account, conv_id, ovm.OFFER_TEXT, [ovm.OPT_ENTRY, flow.OPT_MENU])
            return HandleResult("processed", "oferta_ouvidoria_mulher")
        return self._answer(account, conv_id, key, st, content)

    # ----------------------------------------------------- retomada por protocolo (RTK)
    def _resume_or_phone(self, account: int, conv_id: int, key: str, st: ConversationState, payload: dict, action: flow.Action,
                         awaiting: str | None, content: str) -> HandleResult | None:
        """Retomada por protocolo e telefone de retorno. None = a mensagem segue o tratamento comum."""
        k = action.kind
        if k is flow.Kind.RESUME:
            return self._expect(account, conv_id, st, "resume_ticket:0", retomada.ASK_TEXT, [retomada.OPT_UNKNOWN, flow.OPT_MENU], "retomada_pergunta")
        if k is flow.Kind.RESUME_LIST:
            return self._resume_list(account, conv_id, key, st, payload)
        if k is flow.Kind.PHONE_YES:
            phone = valid_br_phone(self._contact_phone(payload)) or "contato desta conversa"
            return self._set_return_phone(account, conv_id, key, st, phone)
        if k is flow.Kind.PHONE_NO:
            return self._set_return_phone(account, conv_id, key, st, "")
        if k is flow.Kind.PHONE_OTHER:
            return self._expect(account, conv_id, st, "return_phone_number", retomada.RETURN_NUMBER_ASK, [retomada.OPT_PHONE_NO], "retorno_numero")
        if k is not flow.Kind.FREE:
            return None
        if awaiting == "return_phone_number":
            phone = valid_br_phone(content)
            if not phone:
                return self._expect(account, conv_id, st, "return_phone_number", retomada.RETURN_NUMBER_BAD, [retomada.OPT_PHONE_NO], "retorno_numero_invalido")
            return self._set_return_phone(account, conv_id, key, st, phone)
        in_resume = bool(awaiting and awaiting.startswith("resume_ticket"))
        if in_resume or not awaiting:
            ticket_id = retomada.find_ticket_id(content, bare_ok=in_resume)
            if ticket_id:
                return self._resume_lookup(account, conv_id, key, st, payload, ticket_id, int(awaiting.split(":")[1]) if in_resume else 0)
            if in_resume:  # esperava o protocolo e veio outra coisa: conta como tentativa, sem tratar o texto como pergunta
                return self._resume_not_found(account, conv_id, st, int(awaiting.split(":")[1]) + 1)
        return None

    def _resume_not_found(self, account: int, conv_id: int, st: ConversationState, attempts: int) -> HandleResult:
        """Protocolo inexistente, de outro contato, sem dono ou sem chat recebem exatamente a mesma resposta (RTK-03)."""
        if attempts >= retomada.MAX_ATTEMPTS:
            self._send(account, conv_id, retomada.GIVE_UP_TEXT, [flow.OPT_HUMAN_LIST, flow.OPT_MENU])
            return HandleResult("processed", "retomada_desistiu")
        return self._expect(account, conv_id, st, f"resume_ticket:{attempts}", retomada.NOT_FOUND_TEXT,
                            [retomada.OPT_UNKNOWN, flow.OPT_HUMAN_LIST, flow.OPT_MENU], "retomada_nao_localizada")

    def _resume_lookup(self, account: int, conv_id: int, key: str, st: ConversationState, payload: dict, ticket_id: str, attempts: int) -> HandleResult:
        contact, current = self._contact_id(payload), self.ops.get_open_ticket(key)
        found = self.ops.get_ticket(ticket_id)
        if not (found and current and contact and found.contact_id == contact and found.visibility != "no_chat" and found.ticket_id != current.ticket_id):
            self.ops.audit(key, "resume_nao_localizado", "usuario", "")
            return self._resume_not_found(account, conv_id, st, attempts + 1)
        self.ops.link_parent(key, found.ticket_id)
        self.ops.audit(key, "resume_vinculado", "usuario", f"{current.ticket_id}->{found.ticket_id}")
        if found.visibility == "restricted":
            return self._resume_restricted(account, conv_id, key, st)
        fact = retomada.Fact.from_json(found.facts)
        if not st.profile_locked and fact.profile in (Profile.PARTE_TESTEMUNHA.value, Profile.ADVOGADO.value, Profile.GERAL.value):
            st.profile, st.profile_locked = Profile(fact.profile), True
        if not st.unit_label and fact.unit_label:
            st.unit_label = fact.unit_label
            st.unit_id = fact.unit_id if fact.unit_id and self.units.get(fact.unit_id) else None
        if not st.process_number and fact.process_number and fact.process_number in self._known_processes():
            st.process_number = fact.process_number
        st.awaiting = None
        self.ops.save(st)
        text = retomada.RESUMED_TEXT.format(new=current.ticket_id, old=found.ticket_id, date=flow.fmt_time(found.opened_at)[:10],
                                            extra=retomada.RESUMED_ONGOING if found.status == "open" else "")
        self._send(account, conv_id, text, flow.continue_options(bool(st.process_number)))
        self._post_resume_note(account, conv_id, key, current, found, fact)
        return HandleResult("processed", "retomada_vinculada")

    def _post_resume_note(self, account: int, conv_id: int, key: str, current, found, fact: "retomada.Fact") -> None:
        """Quem abrir esta conversa vê o atendimento anterior sem esperar a transferência; e, com a IA ligada, um rascunho do assunto (RTK-17)."""
        try:
            line = retomada.history_line(found, fact, flow.fmt_time(found.opened_at), self.tickets.base_url, linked=True, reasons=REQUEST_REASONS,
                                         show_link=not self.ops.conversation_has_restricted(found.conversation))
            self._send(account, conv_id, retomada.resume_note(line, found.ticket_id, found.status == "open"), private=True)
        except Exception:  # noqa: BLE001 - informativo
            self.ops.audit(key, "resume_note_failed", "bot", "")
        if not (self.tickets.ai_enabled and found.visibility == "normal" and current.visibility == "normal" and found.contact_id == current.contact_id
                and self.orch.llm is not None and hasattr(self.gw, "list_messages")):
            return
        t = threading.Thread(target=self._ai_summary_previous, args=(account, key, current.ticket_id, found), daemon=True)
        self._threads.append(t)
        t.start()

    def _ai_summary_previous(self, account: int, key: str, ticket_id: str, found) -> None:
        """Resumo por IA só do atendimento anterior vinculado, no período dele (RTK-17); não depende da transferência."""
        conv_id = int(key.split(":")[1])
        if not self.ops.claim_event(f"aisumr:{ticket_id}"):
            return
        try:
            lines = self._transcript(account, int(found.conversation.split(":")[1]), 30, found.opened_at, found.closed_at)
            text = ai_summary_note(self.orch.llm, lines, [], scope=f"atendimento anterior {found.ticket_id}")  # type: ignore[arg-type]
            current = self.ops.get_open_ticket(key)
            if text and current and current.ticket_id == ticket_id and current.visibility == "normal":
                self.gw.send_message(account, conv_id, text, private=True)
                self.ops.audit(key, "ai_summary_previous_posted", "bot", ticket_id)
            else:
                self.ops.audit(key, "ai_summary_previous_skipped", "bot", ticket_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("resumo por IA do atendimento anterior falhou: %s", type(exc).__name__)
            self.ops.audit(key, "ai_summary_previous_failed", "bot", type(exc).__name__)

    def _resume_restricted(self, account: int, conv_id: int, key: str, st: ConversationState) -> HandleResult:
        """Caso restrito: nada do caso é citado à pessoa; o time responsável recebe só protocolos e o link da conversa anterior."""
        self.ops.set_ticket_visibility(key, "restricted")
        st.request_reason, st.request_channel, st.request_note_id, st.awaiting = "retomada_restrita", "whatsapp", None, None
        self.ops.save(st)
        self._send(account, conv_id, retomada.RESTRICTED_TEXT)
        return self._do_handoff(account, conv_id, key, st, self.mulher.team, "retomada restrita", ["humano", ovm.LABEL_SERVICE])

    def _resume_list(self, account: int, conv_id: int, key: str, st: ConversationState, payload: dict) -> HandleResult:
        current = self.ops.get_open_ticket(key)
        items = self.ops.list_contact_tickets(self._contact_id(payload), exclude=current.ticket_id if current else None, limit=retomada.HISTORY_LIMIT)
        if not items:
            self._send(account, conv_id, retomada.NO_LIST_TEXT, [flow.OPT_HUMAN_LIST, flow.OPT_MENU])
            return HandleResult("processed", "retomada_sem_lista")
        lines = "\n".join(f"• {t.ticket_id} — {flow.fmt_time(t.opened_at)[:10]}" + (f" — {t.team}" if t.team else "") for t in items)
        st.awaiting = "resume_ticket:0"
        self.ops.save(st)
        self._send(account, conv_id, retomada.LIST_TEXT.format(lines=lines), [Option(t.ticket_id, t.ticket_id.lower()) for t in items] + [flow.OPT_MENU])
        return HandleResult("processed", "retomada_lista")

    def _ask_return_phone(self, account: int, conv_id: int, st: ConversationState) -> HandleResult:
        return self._expect(account, conv_id, st, "return_phone", retomada.RETURN_ASK, retomada.PHONE_OPTIONS, "retorno_pergunta")

    def _set_return_phone(self, account: int, conv_id: int, key: str, st: ConversationState, phone: str) -> HandleResult:
        st.return_phone, st.awaiting = phone, None  # o número nunca vai para log nem auditoria
        self.ops.save(st)
        self.ops.audit(key, "return_phone", "usuario", "informado" if phone else "recusado")
        return self._balcao(account, conv_id, key, st)

    def _history_lines(self, key: str, current) -> list[str]:
        """Atendimentos anteriores do mesmo contato (restritos e sem chat nunca entram) e o protocolo vinculado pela pessoa."""
        if not current or not current.contact_id:
            return []
        items = self.ops.list_contact_tickets(current.contact_id, exclude=current.ticket_id, limit=retomada.HISTORY_LIMIT)
        if current.parent_ticket_id and all(t.ticket_id != current.parent_ticket_id for t in items):
            parent = self.ops.get_ticket(current.parent_ticket_id)
            if parent and parent.visibility == "normal":
                items.insert(0, parent)
        return [retomada.history_line(t, retomada.Fact.from_json(t.facts), flow.fmt_time(t.opened_at), self.tickets.base_url,
                                      linked=t.ticket_id == current.parent_ticket_id, reasons=REQUEST_REASONS,
                                      show_link=not self.ops.conversation_has_restricted(t.conversation))  # link abre a conversa inteira
                for t in items]

    def _facts_for_close(self, key: str, summary: str | None = None) -> dict | None:
        if not self.tickets.enabled:
            return None
        st = self.ops.get(key)
        reason = st.request_reason if st.request_reason in REQUEST_REASONS else None
        phone = st.return_phone or None
        return retomada.Fact(profile=st.profile.value if st.profile_locked else None, process_number=st.process_number, unit_id=st.unit_id,
                             unit_label=st.unit_label, reason=reason, return_phone=phone, agent_summary=summary).to_json() or None

    # ----------------------------------------------------- resumo por IA (RTK, fase 2)
    def _start_ai_summary(self, account: int, conv_id: int, key: str, st: ConversationState) -> None:
        """Depois da transferência, fora do lock da conversa; nunca para caso restrito, sem chat ou da Ouvidoria (RTK-12)."""
        ticket = self.ops.get_open_ticket(key)
        if not (self.tickets.enabled and self.tickets.ai_enabled and ticket and ticket.contact_id and ticket.visibility == "normal"):
            return
        if st.request_reason in ("ouvidoria_mulher", "retomada_restrita") or self.orch.llm is None or not hasattr(self.gw, "list_messages"):
            return
        parent = self.ops.get_ticket(ticket.parent_ticket_id) if ticket.parent_ticket_id else None
        parent_conv = parent.conversation if parent and parent.visibility == "normal" and parent.contact_id == ticket.contact_id else None
        until = (self.now() + timedelta(seconds=AI_WINDOW_SLACK_S)).isoformat()  # o protocolo atual não lê nada que chegue depois da transferência
        window = (ticket.opened_at, parent_conv, parent.opened_at if parent else None, parent.closed_at if parent else None, until)
        t = threading.Thread(target=self._ai_summary, args=(account, conv_id, key, ticket.ticket_id, window), daemon=True)
        self._threads.append(t)
        t.start()

    def _transcript(self, account: int, conv_id: int, limit: int, since: str | None, until: str | None = None) -> list[str]:
        """Mensagens públicas só do período do protocolo: uma conversa pode ter hospedado outro protocolo, inclusive restrito (RTK-12).
        Mensagem sem horário não pode ser atribuída a um protocolo e fica de fora."""
        lo = datetime.fromisoformat(since).timestamp() if since else None
        hi = datetime.fromisoformat(until).timestamp() if until else None
        rows = []
        for m in self.gw.list_messages(account, conv_id):  # type: ignore[attr-defined]
            at = m.get("created_at")
            if m.get("private") or m.get("message_type") not in (0, 1) or not (m.get("content") or "").strip() or not isinstance(at, (int, float)):
                continue
            if (lo is not None and at < lo) or (hi is not None and at > hi):
                continue
            rows.append(m)
        out = []
        for m in rows[-limit:]:
            text = _SPACED_PHONE.sub("[TELEFONE]", mask_pii(" ".join(m["content"].split()))[0])
            text = text.replace("<<<", "<").replace(">>>", ">")[:500]  # a conversa não pode fechar o bloco de dados do prompt
            out.append(("PESSOA: " if m["message_type"] == 0 else "ATENDIMENTO: ") + text)
        return out

    def _ai_summary(self, account: int, conv_id: int, key: str, ticket_id: str, window: tuple) -> None:
        opened_at, parent_conv, parent_opened, parent_closed, until = window
        if not self.ops.claim_event(f"aisum:{ticket_id}"):
            return  # uma tentativa por protocolo
        try:
            lines = self._transcript(account, conv_id, 30, opened_at, until)
            before = (self._transcript(account, int(parent_conv.split(":")[1]), 15, parent_opened, parent_closed) if parent_conv else [])
            text = ai_summary_note(self.orch.llm, lines, before)  # type: ignore[arg-type]
            current = self.ops.get_open_ticket(key)
            if text and current and current.ticket_id == ticket_id and self.ops.get(key).handoff_state is HandoffState.HUMAN_ACTIVE:
                self.gw.send_message(account, conv_id, text, private=True)
                self.ops.audit(key, "ai_summary_posted", "bot", ticket_id)
            else:
                self.ops.audit(key, "ai_summary_skipped", "bot", ticket_id)
        except Exception as exc:  # noqa: BLE001 - informativo: a transferência já ocorreu
            log.warning("resumo por IA falhou: %s", type(exc).__name__)
            self.ops.audit(key, "ai_summary_failed", "bot", type(exc).__name__)

    # ----------------------------------------------------- Ouvidoria da Mulher (OVM)
    def _mulher_ask(self, account: int, conv_id: int, st: ConversationState, tri: Triage, stale: bool = False) -> HandleResult:
        """Faz a pergunta da etapa atual; a etapa fica no estado, então botões de etapas anteriores não avançam nada (OVM-11)."""
        step = tri.step
        st.triage, st.awaiting = tri.to_json(), None
        if step == "resumo":
            template = ovm.SUMMARY_NORETURN_TEXT if tri.no_return else ovm.SUMMARY_TEXT
            text = template.format(summary=tri.summary_for_user(), hours=self.mulher.contacts.hours_text,
                                   contacts=self.mulher.contacts.contact_lines())
            options = ovm.summary_options(flow.OPT_MENU)
        else:
            key = "numero" if (step == "processo" and tri.processo == "numero") else step
            text, options = ovm.QUESTIONS[key], ovm.STEP_OPTIONS[key]
            if step == "relato":
                st.awaiting = "mulher_relato"
            elif key == "numero":
                st.awaiting = "mulher_numero"
        self.ops.save(st)
        if stale:
            text = f"{ovm.STALE_TEXT}\n\n{text}"
        self._send(account, conv_id, text, options)
        return HandleResult("processed", f"mulher_{step}")

    def _mulher_advance(self, account: int, conv_id: int, st: ConversationState, tri: Triage) -> HandleResult:
        tri.next_step()
        return self._mulher_ask(account, conv_id, st, tri)

    def _mulher_action(self, account: int, conv_id: int, key: str, st: ConversationState, tri: Triage | None, action: flow.Action,
                       awaiting: str | None) -> HandleResult:
        k = action.kind
        if k in (flow.Kind.MULHER, flow.Kind.MULHER_DANGER, flow.Kind.MULHER_START):
            self.ops.set_ticket_visibility(key, "restricted")  # caso da Ouvidoria: fora de listas, notas de outros times e fatos (RTK-07)
        if k is flow.Kind.MULHER:
            self.ops.audit(key, "mulher_entrada", "usuario", "")
            self._send(account, conv_id, ovm.WELCOME_TEXT, ovm.ENTRY_OPTIONS)
            return HandleResult("processed", "mulher_acolhimento")
        if k is flow.Kind.MULHER_DANGER:
            tri = tri or Triage()
            tri.perigo = "sim"
            if tri.step == "perigo":
                tri.step = "para_quem"  # já respondida; em etapa adiante a triagem não volta atrás
            st.triage = tri.to_json()
            self.ops.save(st)
            self.ops.audit(key, "mulher_perigo", "usuario", "")
            self._send(account, conv_id, ovm.EMERGENCY_TEXT)
            self._send(account, conv_id, ovm.EMERGENCY_NEXT_TEXT, ovm.AFTER_EMERGENCY)
            return HandleResult("processed", "mulher_emergencia")
        if k is flow.Kind.MULHER_ORIENT_MENU:
            self._send(account, conv_id, ovm.ORIENT_MENU_TEXT, ovm.orient_options(flow.OPT_MENU))
            return HandleResult("processed", "mulher_orientacoes")
        if k is flow.Kind.MULHER_TOPIC:
            text = ovm.phones_text(self.mulher.contacts) if action.arg == "telefones" else ovm.topic_text(action.arg)
            self._send(account, conv_id, text)
            self._send(account, conv_id, ovm.ORIENT_AFTER_TEXT, ovm.orient_options(flow.OPT_MENU))
            return HandleResult("processed", f"mulher_tema_{action.arg}")
        if k is flow.Kind.MULHER_START:
            self.ops.audit(key, "mulher_triagem_inicio", "usuario", "")
            return self._mulher_ask(account, conv_id, st, tri or Triage())
        if k is flow.Kind.MULHER_REDO:
            return self._mulher_ask(account, conv_id, st, Triage())
        assert tri is not None
        if k is flow.Kind.MULHER_SEND:
            if tri.step != "resumo":
                return self._mulher_ask(account, conv_id, st, tri, stale=True)
            return self._mulher_handoff(account, conv_id, key, st, tri)
        if k is flow.Kind.MULHER_SKIP:
            if awaiting == "mulher_numero":
                tri.processo = "sem_numero"
            elif tri.step == "relato":
                tri.relato = None
            elif tri.step in ("perigo", "para_quem", "ambiente", "processo", "contato", "assunto"):
                tri.set(tri.step, NAO_INFORMADO)
            else:
                return self._mulher_ask(account, conv_id, st, tri, stale=True)
            return self._mulher_advance(account, conv_id, st, tri)
        field, value = action.arg.split(":", 1)  # MULHER_ANSWER
        if field != tri.step or not tri.set(field, value):
            return self._mulher_ask(account, conv_id, st, tri, stale=True)
        if field == "perigo" and value == "sim":
            self.ops.audit(key, "mulher_perigo", "usuario", "")
            self._send(account, conv_id, ovm.EMERGENCY_TEXT)  # 190 e 180 antes de qualquer outra pergunta (OVM-10)
        if field == "processo" and value == "numero":
            return self._mulher_ask(account, conv_id, st, tri)  # pede o número (etapa de texto)
        if field == "contato" and value in ("nao", "telefone"):
            self.ops.audit(key, "mulher_contato_inseguro", "usuario", "")  # orienta e segue; o registro será sem retorno por aqui (OVM-16)
            self.ops.set_ticket_visibility(key, "no_chat", detach_contact=True)  # não retomável por chat (RTK-07)
            self._send(account, conv_id, ovm.UNSAFE_TEXT.format(contacts=self.mulher.contacts.contact_lines()))
        return self._mulher_advance(account, conv_id, st, tri)

    def _mulher_text(self, account: int, conv_id: int, key: str, st: ConversationState, tri: Triage, awaiting: str, raw: str) -> HandleResult:
        """Texto livre das etapas número do processo e relato; o relato é guardado como chegou e nunca vai para log (OVM-13, OVM-23)."""
        if awaiting == "mulher_numero":
            numbers = find_process_numbers(raw)
            if not numbers or not cnj_check_digits_ok(numbers[0]):
                st.awaiting = "mulher_numero"
                self.ops.save(st)
                self._send(account, conv_id, ovm.NUMBER_BAD_TEXT, ovm.STEP_OPTIONS["numero"])
                return HandleResult("processed", "mulher_numero_invalido")
            tri.numero_processo = numbers[0]  # informado pela pessoa; não é "localizado"
            return self._mulher_advance(account, conv_id, st, tri)
        if not raw.strip():
            st.awaiting = "mulher_relato"
            self.ops.save(st)
            self._send(account, conv_id, ovm.RELATO_EMPTY_TEXT, ovm.STEP_OPTIONS["relato"])
            return HandleResult("processed", "mulher_relato_vazio")
        if len(raw) > self.mulher.max_relato_chars:
            st.awaiting = "mulher_relato"
            self.ops.save(st)
            self._send(account, conv_id, ovm.RELATO_LONG_TEXT.format(n=self.mulher.max_relato_chars), ovm.STEP_OPTIONS["relato"])
            return HandleResult("processed", "mulher_relato_longo")
        tri.relato = raw
        return self._mulher_advance(account, conv_id, st, tri)

    def _mulher_handoff(self, account: int, conv_id: int, key: str, st: ConversationState, tri: Triage) -> HandleResult:
        tri.consentimento, tri.protocol_advice = True, True
        st.triage, st.awaiting = tri.to_json(), None
        st.request_reason, st.request_channel, st.request_note_id = "ouvidoria_mulher", "whatsapp", None
        self.ops.save(st)
        self.ops.audit(key, "mulher_encaminhar", "usuario", "urgente" if tri.urgent else "")
        return self._do_handoff(account, conv_id, key, st, self.mulher.team, "ouvidoria da mulher", self._mulher_labels(tri))

    @staticmethod
    def _mulher_labels(tri: Triage) -> list[str]:
        return ["humano", ovm.LABEL_SERVICE] + ([ovm.LABEL_URGENT] if tri.urgent else [])

    def _mulher_open_now(self) -> bool:
        return self.mulher.contacts.is_open(self.now().astimezone(flow.BELEM).time())

    def _set_attributes(self, account: int, conv_id: int, key: str, tri: Triage) -> None:
        """Atributos da conversa no Chatwoot (só enumerações); informativo, não pode derrubar o atendimento."""
        setter = getattr(self.gw, "set_custom_attributes", None)
        if setter is None:
            return
        try:
            setter(account, conv_id, tri.attributes())
        except Exception:  # noqa: BLE001
            self.ops.audit(key, "mulher_atributos_falhou", "bot", "")

    # ----------------------------------------------------- roteiro TJPA: menus e identificação
    def _expect(self, account: int, conv_id: int, st: ConversationState, awaiting: str, text: str, options: list[Option] | None = None,
                detail: str = "") -> HandleResult:
        st.awaiting = awaiting
        self.ops.save(st)
        self._send(account, conv_id, text, options)
        return HandleResult("processed", detail or f"aguardando_{awaiting.split(':')[0]}")

    def _menu(self, payload: dict, account: int, conv_id: int, st: ConversationState) -> HandleResult:
        """Sem perfil escolhido: apresentação da Júlia e menu de perfis. Com perfil: o menu do perfil (RTA-01, RTA-04, RTA-07)."""
        if not st.profile_locked:
            name = ((payload.get("sender") or {}).get("name") or "").split(" ")[0].strip()
            self._send(account, conv_id, flow.profile_menu_text(name.title() if name.isalpha() else ""), flow.profile_menu(self.mulher.enabled, self.tickets.enabled))
            return HandleResult("processed", "menu")
        if st.profile is Profile.GERAL:
            self._send(account, conv_id, flow.GERAL_MENU_TEXT, flow.GERAL_MENU)
            return HandleResult("processed", "menu_geral")
        self._send(account, conv_id, flow.ID_MENU_TEXT.get(st.profile, flow.ID_MENU_TEXT[Profile.PARTE_TESTEMUNHA]), flow.ID_MENU)
        return HandleResult("processed", "menu_identificacao")

    def _awaited_text(self, account: int, conv_id: int, key: str, st: ConversationState, awaiting: str, content: str) -> HandleResult | None:
        """Texto livre em resposta a um pedido do bot. None = não é uma resposta esperada; segue o tratamento comum."""
        if awaiting == "cpf":
            return self._receive_cpf(account, conv_id, key, st, content)
        if awaiting == "name":
            if len(name_tokens(content)) < 2:
                return self._expect(account, conv_id, st, "name", flow.NAME_SHORT_TEXT, [flow.OPT_MENU])
            result = self.search.by_name(content)
            self.ops.audit(key, "name_search", "bot", f"{len(result.candidates)} resultado(s)" if result.available else "indisponivel")
            return self._offer_candidates(account, conv_id, st, list(result.candidates) if result.available else [])
        if awaiting == "name_refine":
            terms = [t for t in fold(content).split() if len(t) >= 3 and t not in ("vara", "de", "da", "do")]
            pool = [ProcessCandidate(**c) for c in json.loads(st.choices or "[]")]
            kept = [c for c in pool if terms and all(t in fold(c.court_unit) for t in terms)]
            return self._offer_candidates(account, conv_id, st, kept, refine=False)
        if awaiting == "vara_unit":
            label = " ".join(content.split())[:120]
            if not label:
                return self._expect(account, conv_id, st, "vara_unit", flow.VARA_UNIT_ASK_TEXT, [flow.OPT_MENU])
            found = self.units.find(label)
            st.unit_id, st.unit_label = (found[0].id, found[0].name) if len(found) == 1 else (None, label)
            st.process_number = None  # a unidade informada não localiza o processo (RTA-11)
            st.request_reason = st.request_reason or "atendimento"
            self.ops.save(st)
            self._send(account, conv_id, flow.VARA_UNIT_NOTED_TEXT.format(unit=st.unit_label))
            return self._offer_channels(account, conv_id, key, st)
        if awaiting.startswith("unit:"):
            return self._unit_reply(account, conv_id, st, awaiting.split(":", 1)[1], content)
        return None

    def _receive_cpf(self, account: int, conv_id: int, key: str, st: ConversationState, content: str) -> HandleResult:
        """O CPF não é repetido, registrado nem enviado ao LLM; sem fonte autorizada, a busca declara indisponibilidade (RTA-09)."""
        cpf = extract_cpf(content)
        if cpf is None:
            return self._expect(account, conv_id, st, "cpf", flow.CPF_NOT_FOUND_TEXT, [flow.OPT_MENU])
        if not cpf_is_valid(cpf):
            return self._expect(account, conv_id, st, "cpf", flow.CPF_INVALID_TEXT, [flow.OPT_MENU])
        result = self.search.by_cpf(cpf)
        self.ops.audit(key, "cpf_search", "bot", f"{len(result.candidates)} resultado(s)" if result.available else "indisponivel")
        if not result.available:
            self._send(account, conv_id, flow.CPF_UNAVAILABLE_TEXT, [flow.OPT_ID_NUMBER, flow.OPT_ID_NONE, flow.OPT_HUMAN])
            return HandleResult("processed", "cpf_indisponivel")
        return self._offer_candidates(account, conv_id, st, list(result.candidates))

    def _offer_candidates(self, account: int, conv_id: int, st: ConversationState, candidates: list[ProcessCandidate],
                          refine: bool = True) -> HandleResult:
        """Lista para seleção explícita; muitos resultados pedem um dado a mais; nenhum leva à pergunta sobre a Vara (RTA-10)."""
        if not candidates or (len(candidates) > flow.MAX_PICK and not refine):
            st.choices = None
            self.ops.save(st)
            self._send(account, conv_id, flow.VARA_ASK_TEXT, flow.VARA_OPTIONS)
            return HandleResult("processed", "pergunta_vara")
        st.choices = json.dumps([c.__dict__ for c in candidates], ensure_ascii=False)
        if len(candidates) > flow.MAX_PICK:
            return self._expect(account, conv_id, st, "name_refine", flow.NAME_MANY_TEXT, [flow.OPT_MENU])
        text = flow.NAME_PICK_TEXT.format(n=len(candidates), lines=flow.pick_lines(candidates))
        return self._expect(account, conv_id, st, "pick", text, flow.PICK_OPTIONS[: len(candidates)] + [flow.OPT_MENU], "selecao_de_processo")

    def _pick(self, account: int, conv_id: int, key: str, st: ConversationState, choice: str) -> HandleResult:
        pool = [ProcessCandidate(**c) for c in json.loads(st.choices or "[]")]
        chosen = None
        if choice.isdigit() and 1 <= int(choice) <= len(pool):
            chosen = pool[int(choice) - 1].process_number
        elif find_process_numbers(choice) and find_process_numbers(choice)[0] in [c.process_number for c in pool]:
            chosen = find_process_numbers(choice)[0]
        if chosen is None:
            if not pool:
                return self._menu({}, account, conv_id, st)
            return self._expect(account, conv_id, st, "pick", flow.PICK_INVALID_TEXT, flow.PICK_OPTIONS[: len(pool)] + [flow.OPT_MENU])
        st.choices = None
        return self._select_process(account, conv_id, key, st, chosen)

    def _select_process(self, account: int, conv_id: int, key: str, st: ConversationState, number: str) -> HandleResult:
        st.process_number, st.last_domain = number, "processual"
        docs = len(self.orch.store.list_documents(process_number=number, limit=5000))  # type: ignore[attr-defined]
        info = self.orch.store.process_info(number) or {"process_number": number}  # type: ignore[attr-defined]
        unit = (info.get("court_unit") or "").strip()
        if unit and unit != "desconhecido":
            found = self.units.find(unit)
            st.unit_label, st.unit_id = unit, (found[0].id if len(found) == 1 else None)
        self.ops.save(st)
        self._send(account, conv_id, flow.process_summary(info, docs))
        if st.profile is Profile.ADVOGADO:
            where = f", que tramita na *{st.unit_label}*" if st.unit_label else ""
            self._send(account, conv_id, flow.ADV_LOCATED_TEXT.format(n=number, unit=where), flow.ADV_MENU)
        else:
            self._send(account, conv_id, flow.PROCESS_NEXT_TEXT, flow.process_menu(st.profile))
        return HandleResult("processed", "processo_selecionado")

    def _telejudiciario(self, account: int, conv_id: int) -> HandleResult:
        contact = self.units.contacts.telejudiciario
        if contact:
            self._send(account, conv_id, flow.TELEJUDICIARIO_TEXT.format(contact=contact), [flow.OPT_HUMAN, flow.OPT_MENU])
        else:
            self._send(account, conv_id, flow.NO_TELEJUDICIARIO_TEXT, [flow.OPT_HUMAN, flow.OPT_MENU])
        return HandleResult("processed", "telejudiciario")

    # ---------------------------------------------------------- roteiro TJPA: informações gerais
    def _geral_item(self, account: int, conv_id: int, key: str, st: ConversationState, topic: str) -> HandleResult:
        if not self.units.units:
            # cadastro vazio: diz isso e consulta a base institucional aprovada, sem improvisar dados (RTA-06)
            extra = f"\n{flow.TELEJUDICIARIO_TEXT.format(contact=self.units.contacts.telejudiciario)}" if self.units.contacts.telejudiciario else ""
            self._send(account, conv_id, flow.GERAL_NO_REGISTRY_TEXT + extra)
            return self._geral_fallback(account, conv_id, key, st, topic)
        return self._expect(account, conv_id, st, f"unit:{topic}", flow.GERAL_UNIT_ASK_TEXT, [flow.OPT_MENU])

    def _geral_fallback(self, account: int, conv_id: int, key: str, st: ConversationState, topic: str) -> HandleResult:
        """Base institucional aprovada (RAG); se a conversa continua com o bot, oferece o menu de continuidade (RTA-24)."""
        result = self._answer(account, conv_id, key, st, flow.GERAL_FALLBACK_QUESTIONS[topic])
        if result.outcome == "processed" and self.ops.get(key).handoff_state is not HandoffState.HANDOFF_REQUESTED:
            self._send(account, conv_id, flow.CONTINUE_TEXT, flow.continue_options(bool(st.process_number)))
        return result

    def _unit_reply(self, account: int, conv_id: int, st: ConversationState, topic: str, content: str) -> HandleResult:
        found = self.units.find(content)
        if len(found) > 1:
            lines = "\n".join(f"- {u.name} ({u.city})" for u in found[:5])
            return self._expect(account, conv_id, st, f"unit:{topic}", flow.UNIT_MANY_TEXT.format(lines=lines), [flow.OPT_MENU])
        if not found:
            tele = self.units.contacts.telejudiciario
            self._send(account, conv_id, flow.UNIT_NOT_FOUND_SEARCHING_TEXT + (f"\n{flow.TELEJUDICIARIO_TEXT.format(contact=tele)}" if tele else ""))
            return self._geral_fallback(account, conv_id, f"{account}:{conv_id}", st, topic)
        u = found[0]
        st.unit_id, st.unit_label = u.id, u.name
        self.ops.save(st)
        if topic == "endereco":
            body = f"📍 Endereço: {u.address}" if u.address else f"Não tenho o endereço da {u.name} no cadastro deste atendimento."
        elif topic == "telefone":
            parts = [f"📞 Telefone: {format_phone(u.phone)}" if u.phone else "", f"💬 WhatsApp oficial: {format_phone(u.whatsapp)}" if u.whatsapp else ""]
            body = "\n".join(p for p in parts if p) or f"Não tenho telefone nem WhatsApp da {u.name} no cadastro deste atendimento."
        else:
            body = f"🕒 Horário: {u.hours}" if u.hours else f"Não tenho o horário de atendimento da {u.name} no cadastro deste atendimento."
        source = f"\n\nFonte: {u.source}" + (f" (verificado em {u.verified_at})" if u.verified_at else "") if u.source else ""
        self._send(account, conv_id, f"*{u.name}* — {u.city}\n{body}{source}")
        self._send(account, conv_id, flow.CONTINUE_TEXT, flow.continue_options(bool(st.process_number)))
        return HandleResult("processed", f"unidade_{topic}")

    # ------------------------------------------------------ roteiro TJPA: diligência e canal humano
    def _receive_diligence(self, account: int, conv_id: int, key: str, st: ConversationState, raw: str) -> HandleResult:
        if not raw.strip():
            return self._expect(account, conv_id, st, "diligence", flow.DILIGENCE_EMPTY_TEXT, [flow.OPT_MENU])
        if len(raw) > self.max_diligence:
            return self._expect(account, conv_id, st, "diligence", flow.DILIGENCE_TOO_LONG_TEXT.format(n=self.max_diligence), [flow.OPT_MENU])
        st.diligence, st.request_reason, st.request_note_id = raw, "diligencia", None  # sem reescrita, resumo ou correção
        self.ops.save(st)
        self.ops.audit(key, "diligence_received", "usuario", f"{len(raw)} caracteres")  # só o tamanho; o texto não vai para a auditoria
        return self._offer_channels(account, conv_id, key, st)

    def _unit_name(self, st: ConversationState) -> str:
        return st.unit_label or "secretaria responsável"

    def _balcao_url(self, st: ConversationState) -> tuple[str | None, bool]:
        """(url, é link direto da unidade). Sem link direto, a página geral cadastrada, declarada como geral (DIV-01)."""
        unit = self.units.get(st.unit_id)
        if unit and unit.balcao_url:
            return unit.balcao_url, True
        return self.units.contacts.balcao_general_url, False

    def _whatsapp_available(self, st: ConversationState) -> bool:
        if self.whatsapp_mode == "link":
            unit = self.units.get(st.unit_id)
            return bool(unit and unit.whatsapp)
        return True  # integrado: a própria conversa no Chatwoot

    def _offer_channels(self, account: int, conv_id: int, key: str, st: ConversationState, exclude: str | None = None) -> HandleResult:
        """Escolha explícita do canal; só aparecem os canais disponíveis (RTA-16)."""
        opts: list[Option] = []
        if exclude != "whatsapp" and self._whatsapp_available(st):
            opts.append(flow.OPT_CH_WHATSAPP)
        if exclude != "balcao" and self._balcao_url(st)[0]:
            opts.append(flow.OPT_CH_BALCAO)
        if not opts:
            extra = [flow.OPT_UNIT_PHONE] if self._unit_phone_number(st) else []
            self._send(account, conv_id, flow.CHANNEL_NONE_TEXT, extra + [flow.OPT_MENU])
            return HandleResult("processed", "sem_canal_disponivel")
        return self._expect(account, conv_id, st, "channel", flow.CHANNEL_ASK_TEXT.format(unit=self._unit_name(st)), opts + [flow.OPT_MENU],
                            "escolha_de_canal")

    def _unit_phone_number(self, st: ConversationState) -> str | None:
        unit = self.units.get(st.unit_id)
        return unit.phone if unit else None

    def _failure_options(self, st: ConversationState, retry: bool) -> list[Option]:
        opts = ([flow.OPT_RETRY] if retry else []) + [flow.OPT_OTHER_CHANNEL]
        if self._unit_phone_number(st):
            opts.append(flow.OPT_UNIT_PHONE)
        return opts if len(opts) == 3 else opts + [flow.OPT_MENU]

    def _unit_phone(self, account: int, conv_id: int, st: ConversationState) -> HandleResult:
        unit = self.units.get(st.unit_id)
        if not unit or not unit.phone:
            self._send(account, conv_id, f"Não tenho o telefone da {self._unit_name(st)} no cadastro deste atendimento.", [flow.OPT_MENU])
            return HandleResult("processed", "telefone_indisponivel")
        hours = f"\n🕒 Horário: {unit.hours}" if unit.hours else ""
        self._send(account, conv_id, flow.UNIT_PHONE_TEXT.format(unit=unit.name, phone=format_phone(unit.phone), hours=hours), [flow.OPT_MENU])
        return HandleResult("processed", "telefone_da_unidade")

    def _team_for_request(self, st: ConversationState) -> str:
        return self.team_gabinete if st.diligence and _GABINETE.search(fold(st.diligence)) else self.team_secretaria

    def _choose_channel(self, account: int, conv_id: int, key: str, st: ConversationState, channel: str) -> HandleResult:
        """Um único canal por decisão explícita; nada é enviado ao outro canal (RTA-17 a RTA-20)."""
        st.request_reason = st.request_reason or "atendimento"
        st.request_channel = channel
        self.ops.save(st)
        self.ops.audit(key, "channel_chosen", "usuario", channel)
        if channel == "balcao":
            return self._balcao(account, conv_id, key, st)
        if self.whatsapp_mode == "link":
            return self._whatsapp_link(account, conv_id, key, st)
        team = self._team_for_request(st)
        return self._do_handoff(account, conv_id, key, st, team, f"roteiro: {st.request_reason} via whatsapp", ["humano"])

    def _balcao(self, account: int, conv_id: int, key: str, st: ConversationState) -> HandleResult:
        url, direct = self._balcao_url(st)
        if not url:
            self._send(account, conv_id, flow.BALCAO_NO_LINK_TEXT.format(unit=self._unit_name(st)), self._failure_options(st, retry=False))
            return HandleResult("processed", "balcao_sem_link")
        if self.tickets.enabled and st.return_phone is None:
            return self._ask_return_phone(account, conv_id, st)  # só uma vez por protocolo; pode recusar
        unit = self.units.get(st.unit_id)
        current = self.ops.get_open_ticket(key) if self.tickets.enabled else None
        text = flow.balcao_text(self._unit_name(st), url, direct, st.process_number, unit.hours if unit else None, bool(st.diligence))
        if current:
            text += retomada.BALCAO_TKT_NOTE.format(tkt=current.ticket_id)
            try:  # nota para a equipe: não é transferência; o Balcão Virtual não está integrado
                self._send(account, conv_id, retomada.balcao_note(current.ticket_id, flow.fmt_time(self.now().isoformat()), st.return_phone), private=True)
            except Exception:  # noqa: BLE001 - informativo
                self.ops.audit(key, "balcao_note_failed", "bot", "")
        self._send(account, conv_id, text)
        if st.diligence:
            self._send(account, conv_id, st.diligence)  # mensagem só com o texto integral, para copiar
        self.ops.audit(key, "balcao_link_shown", "bot", "direto" if direct else "pagina_geral")  # exibir o link não é envio
        self._send(account, conv_id, flow.CONTINUE_TEXT, flow.continue_options(bool(st.process_number)))
        return HandleResult("processed", "balcao_virtual")

    def _whatsapp_link(self, account: int, conv_id: int, key: str, st: ConversationState) -> HandleResult:
        unit = self.units.get(st.unit_id)
        if not unit or not unit.whatsapp:
            self._send(account, conv_id, flow.WHATSAPP_NO_NUMBER_TEXT.format(unit=self._unit_name(st)), self._failure_options(st, retry=False))
            return HandleResult("processed", "whatsapp_sem_numero")
        head = "Olá! Solicitação vinda do atendimento virtual EscutAI (TJPA)." + (f" Processo: {st.process_number}." if st.process_number else "")
        link = whatsapp_link(unit.whatsapp, head + (f"\n{st.diligence}" if st.diligence else ""))
        separate = len(link) > 2000  # link longo demais para o WhatsApp: a diligência vai à parte, para copiar
        if separate:
            link = whatsapp_link(unit.whatsapp, head)
        proc = f"\nProcesso: *{st.process_number}*" if st.process_number else ""
        self._send(account, conv_id, flow.WHATSAPP_LINK_TEXT.format(unit=unit.name, proc=proc, link=link))
        if separate and st.diligence:
            self._send(account, conv_id, st.diligence)
        self.ops.audit(key, "whatsapp_link_shown", "bot", unit.id)  # abrir o link não confirma envio
        self._send(account, conv_id, flow.CONTINUE_TEXT, flow.continue_options(bool(st.process_number)))
        return HandleResult("processed", "whatsapp_link")

    def _channel_recovery(self, account: int, conv_id: int, key: str, st: ConversationState, content: str) -> HandleResult | None:
        """Escolhas após falha da transferência (RTA-20). None = mensagem comum: segue a nova tentativa automática."""
        k = flow.parse(content, []).kind
        if k is flow.Kind.RETRY_CHANNEL:
            return self._attempt_handoff(account, conv_id, key, st, ["humano"])  # pedido explícito do usuário: uma tentativa por toque
        if k is flow.Kind.UNIT_PHONE:
            return self._unit_phone(account, conv_id, st)
        if k is flow.Kind.OTHER_CHANNEL:
            failed = st.request_channel
            self._cancel_pending_request(account, conv_id, key, st, f"troca de canal após falha ({failed})")
            return self._offer_channels(account, conv_id, key, self.ops.get(key), exclude=failed)
        if flow.is_option_click(content):
            # qualquer outra opção do menu é uma nova escolha do usuário: a transferência pendente é cancelada, nunca repetida às cegas
            clicked_balcao = flow.clean(content) == flow.clean(flow.OPT_CH_BALCAO.title)
            self._cancel_pending_request(account, conv_id, key, st, "opção diferente escolhida após falha", keep_choice=clicked_balcao)
        return None

    def _cancel_pending_request(self, account: int, conv_id: int, key: str, st: ConversationState, why: str, keep_choice: bool = False) -> None:
        """Cancela a transferência não confirmada (a diligência continua guardada) e avisa a equipe se a nota de contexto já existe."""
        had_note = st.request_note_id is not None
        st = self.ops.transition(key, HandoffState.AUTOMATION_RESUMED, actor="usuario", detail=why)
        st.handoff_attempts, st.request_channel, st.request_note_id = 0, None, None
        st.awaiting = "channel" if keep_choice else None  # "Balcão Virtual" tocado agora é a escolha de canal
        self.ops.save(st)
        if had_note:
            try:
                self._send(account, conv_id, "↩️ Pedido de atendimento humano CANCELADO pelo usuário (troca de canal). Desconsidere a nota "
                           "de contexto anterior: nenhuma transferência foi concluída.", private=True)
            except Exception:  # noqa: BLE001 - informativo
                self.ops.audit(key, "cancel_note_failed", "bot", "")

    def _context_note(self, key: str, st: ConversationState) -> str:
        """Mensagem de contexto para o atendente (seção 6.3): só campos disponíveis, nada inventado (RTA-21)."""
        from datetime import datetime as _dt

        ticket = self.ops.get_open_ticket(key)
        if st.request_reason == "ouvidoria_mulher":
            tri = Triage.from_json(st.triage) or Triage()
            return ovm.context_note(tri, ticket.ticket_id if ticket else None, flow.fmt_time(self.now().isoformat()), self._mulher_open_now(),
                                    self.mulher.contacts)
        if st.request_reason == "retomada_restrita":
            parent = self.ops.get_ticket(ticket.parent_ticket_id) if ticket and ticket.parent_ticket_id else None
            link = retomada.conversation_url(self.tickets.base_url, int(key.split(":")[0]), parent.conversation) if parent else "não identificada"
            return retomada.restricted_note(ticket.ticket_id if ticket else None, parent.ticket_id if parent else "não identificado",
                                            flow.fmt_time(self.now().isoformat()), link)
        unit_origin = "do processo localizado" if st.process_number else "informada pelo usuário"
        lines = [
            "ATENDIMENTO VIRTUAL — SOLICITAÇÃO DE ATENDIMENTO HUMANO",
            "Canal escolhido: WhatsApp (esta conversa)" if st.request_channel == "whatsapp" or not self.tickets.enabled
            else "Canal: WhatsApp (esta conversa); transferência pelo atendimento virtual",
            f"Perfil: {PROFILE_LABELS.get(st.profile, st.profile.value)}",
            f"Processo: {st.process_number} (acervo de demonstração)" if st.process_number else "Processo: não identificado",
            f"Unidade: {st.unit_label} ({unit_origin})" if st.unit_label else "Unidade: não identificada",
            "Solicitante: contato desta conversa",
            f"Data e horário: {flow.fmt_time(_dt.now(timezone.utc).isoformat())} (Belém)",
        ]
        if st.diligence:
            lines += ["", "Diligência solicitada (texto integral):", st.diligence]
        else:
            lines += ["", f"Motivo do atendimento: {REQUEST_REASONS.get(st.request_reason or '', 'pedido de atendimento humano')}"]
        if ticket:
            lines.append(f"Número de atendimento: {ticket.ticket_id} (interno; não é protocolo judicial)")
        if self.tickets.enabled:
            try:  # o histórico nunca pode impedir a transferência: erro aqui devolve a nota básica
                if ticket and ticket.parent_ticket_id:
                    lines.append(f"A pessoa informou que este atendimento continua o {ticket.parent_ticket_id}.")
                lines.append(retomada.return_phone_line(st.return_phone).lstrip("\n"))
                lines.append(retomada.history_block(self._history_lines(key, ticket)))
            except Exception:  # noqa: BLE001
                log.warning("não foi possível montar o histórico da nota")
                self.ops.audit(key, "history_failed", "bot", "")
        return "\n".join(line for line in lines if line is not None)

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
        self.purge_expired(now)
        return closed

    def purge_expired(self, now: datetime | None = None) -> int:
        """Retenção dos fatos (RTK-06); roda na varredura mesmo sem encerramento por inatividade."""
        if not self.tickets.enabled:
            return 0
        purged = self.ops.purge_ticket_facts(self.tickets.retention_days, now)
        if purged:
            log.info("fatos de atendimento apagados pela retenção: %d", purged)
        return purged

    def _close_by_agent(self, account: int, conv_id: int, key: str, summary: str | None = None) -> HandleResult:
        """Nota privada `/encerrar [resumo]` do atendente: fecha o protocolo, guarda o resumo e resolve a conversa no Chatwoot."""
        return self._finish(account, conv_id, key, "atendente", summary)

    def _finish(self, account: int, conv_id: int, key: str, closed_by: str, summary: str | None = None) -> HandleResult:
        ticket = self.ops.close_ticket(key, closed_by, self._facts_for_close(key, summary))
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
        self._reset_session(key)
        try:
            self.gw.set_status(account, conv_id, "resolved")  # "Resolvido" no Chatwoot
        except Exception:  # noqa: BLE001
            self.ops.audit(key, "resolve_failed", "bot", closed_by)
            self._safe_label(account, conv_id, ["ia_falha"])
        return HandleResult("processed", f"encerrado_por_{closed_by}")

    def _reset_session(self, key: str) -> None:
        """Atendimento encerrado: o próximo começa do zero e a diligência não sobrevive ao protocolo fechado (RTA-22)."""
        st = self.ops.get(key)
        st.process_number, st.offer_pending, st.failed_retrievals = None, False, 0
        st.clear_request()
        st.unit_label = st.unit_id = st.awaiting = None
        st.profile, st.profile_locked = Profile.INDEFINIDO, False
        self.ops.save(st)

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
            if self.rich and (st.request_channel == "whatsapp" or self.tickets.enabled) and st.request_note_id is None:
                # contexto e diligência chegam à equipe antes da transferência, uma única vez (sem duplicar na nova tentativa)
                note_id = self._send(account, conv_id, self._context_note(key, st), private=True)
                if not note_id:
                    raise RuntimeError("nota de contexto sem confirmação")
                st.request_note_id = note_id
                self.ops.save(st)
            ok_team = self.gw.assign_team(account, conv_id, team)
            ok_status = self.gw.set_status(account, conv_id, "open")
            confirmed = bool(ok_team and ok_status)  # a transferência é confirmada por equipe + status; etiqueta é informativa
        except Exception as exc:  # noqa: BLE001
            log.warning("transferência falhou: %s", type(exc).__name__)
            confirmed = False
        if confirmed:
            if st.request_reason == "ouvidoria_mulher":
                labels = labels + self._mulher_labels(Triage.from_json(st.triage) or Triage())
            self._safe_label(account, conv_id, list(dict.fromkeys(labels + ["humano"])))
            self.ops.transition(key, HandoffState.HUMAN_ACTIVE, actor="chatwoot", detail="transferência confirmada pela API")
            st = self.ops.get(key)
            st.handoff_attempts = 0
            self.ops.save(st)
            self.ops.set_ticket_team(key, team)
            try:
                ticket = self.ops.get_open_ticket(key) if self.rich else None
                request = st.request_reason in ("diligencia", "providencia")
                if st.request_reason == "retomada_restrita":
                    notice = retomada.RESTRICTED_NOTICE.format(tkt=ticket.ticket_id if ticket else "")
                elif st.request_reason == "ouvidoria_mulher":
                    tri = Triage.from_json(st.triage) or Triage()
                    self._set_attributes(account, conv_id, key, tri)
                    tid = ticket.ticket_id if ticket else None
                    notice = (ovm.handoff_notice_no_return(tid, self.mulher.contacts) if tri.no_return
                              else ovm.handoff_notice(tid, self.mulher.contacts, self._mulher_open_now()))
                else:
                    notice = flow.handoff_notice(team, ticket.ticket_id if ticket else None, request)
                self._send(account, conv_id, notice)  # só após a confirmação (CHW-10)
                self._start_ai_summary(account, conv_id, key, st)
            except Exception:  # noqa: BLE001 - a transferência já ocorreu; o aviso falhou
                log.warning("transferência confirmada, mas o aviso ao usuário falhou")
                self.ops.audit(key, "handoff_notice_failed", "bot", team)
                self._safe_label(account, conv_id, ["ia_falha"])
            return HandleResult("handoff", team)
        self.ops.transition(key, HandoffState.HANDOFF_REQUESTED, actor="bot", detail="transferência não confirmada")
        giving_up = self.ops.get(key).handoff_attempts >= self.max_attempts
        flagged = self._safe_label(account, conv_id, ["ia_falha", "humano"] if giving_up else ["ia_falha"])
        text, options = HANDOFF_RETRY_TEXT, None
        if giving_up:
            text = HANDOFF_FAILED_TEXT if flagged else HANDOFF_FAILED_UNFLAGGED_TEXT
            self.ops.audit(key, "handoff_exhausted", "bot", f"equipe={team}")
        if self.rich and st.request_reason == "retomada_restrita":
            text, options = retomada.RESTRICTED_FAILED, [flow.OPT_RETRY, flow.OPT_MENU]
        elif self.rich and st.request_reason == "ouvidoria_mulher":
            text, options = ovm.FAILED_TEXT.format(contacts=self.mulher.contacts.contact_lines()), [flow.OPT_RETRY, flow.OPT_MENU]
        elif self.rich and st.request_channel:
            # pedido do roteiro: o texto fica guardado e a pessoa escolhe o próximo passo (RTA-20)
            text, options = flow.HANDOFF_CHANNEL_FAILED_TEXT, self._failure_options(st, retry=True)
        try:
            self._send(account, conv_id, text, options)
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
            ticket = self.ops.close_ticket(key, "atendente", self._facts_for_close(key))  # resolvido direto na interface do Chatwoot
            if ticket is not None:
                account, conv_id = (int(x) for x in key.split(":"))
                try:
                    self._send(account, conv_id, flow.closing_text(ticket.ticket_id, ticket.opened_at, ticket.closed_at or ticket.opened_at, "atendente"))
                except Exception:  # noqa: BLE001
                    self.ops.audit(key, "closing_notice_failed", "bot", ticket.ticket_id)
        closed = None
        if status == "resolved" and st.handoff_state is HandoffState.HUMAN_ACTIVE:
            self.ops.transition(key, HandoffState.HUMAN_CLOSED, actor="chatwoot", detail="conversa resolvida")
            closed = HandleResult("processed", "human_closed")
        if status == "resolved" and self.rich:
            self._reset_session(key)
        return closed or HandleResult("ignored", f"status {status}")

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
        self._await_previous(account, conv_id)
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
            if not private:
                self._last_public[(account, conv_id)] = msg_id
        return msg_id

    def _await_previous(self, account: int, conv_id: int) -> None:
        """Garante a ordem: a próxima mensagem só sai depois que a anterior foi entregue ao WhatsApp."""
        prev = self._last_public.pop((account, conv_id), None)
        wait = getattr(self.gw, "wait_dispatched", None)
        if prev and wait:
            try:
                wait(account, conv_id, prev)
            except Exception:  # noqa: BLE001 - ordem é desejável, não pode impedir o atendimento
                log.warning("não foi possível confirmar a entrega da mensagem anterior")

    def _safe_label(self, account: int, conv_id: int, labels: list[str]) -> bool:
        try:
            self.gw.add_labels(account, conv_id, list(dict.fromkeys(labels)))
            return True
        except Exception:  # noqa: BLE001 - etiqueta é informativa; não pode derrubar o atendimento
            log.warning("falha ao aplicar etiquetas %s", labels)
            return False
