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
from app.application.chat.process_search import DemoCorpusProcessSearch, name_tokens
from app.domain.handoff import HandoffState, InvalidTransition, bot_may_reply
from app.domain.models import Option, Profile, ResponseKind
from app.domain.policies import cnj_check_digits_ok, cpf_is_valid, extract_cpf, find_process_numbers, mask_cpf
from app.domain.ports import ChatwootGateway, ProcessCandidate, ProcessSearchPort
from app.domain.units import UnitDirectory, fold, format_phone, whatsapp_link
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
                 whatsapp_mode: str = "chatwoot", max_diligence_chars: int = 3000) -> None:
        self.orch, self.ops, self.gw = orchestrator, ops, gateway
        self.units = units or UnitDirectory()
        self.search = search or DemoCorpusProcessSearch(orchestrator.store)
        self.team_secretaria, self.team_gabinete = teams
        self.whatsapp_mode = whatsapp_mode  # chatwoot: transferência integrada nesta conversa | link: WhatsApp oficial da unidade
        self.max_diligence = max_diligence_chars
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
        if awaiting == "diligence" and not flow.is_option_click(content):
            # campo livre: tudo o que não é toque em uma opção é o texto da diligência, guardado como chegou (RTA-15)
            return self._receive_diligence(account, conv_id, key, st, payload.get("content") or "")
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
        return self._answer(account, conv_id, key, st, content)

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
            self._send(account, conv_id, flow.profile_menu_text(name.title() if name.isalpha() else ""), flow.PROFILE_MENU)
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
        unit = self.units.get(st.unit_id)
        self._send(account, conv_id, flow.balcao_text(self._unit_name(st), url, direct, st.process_number, unit.hours if unit else None,
                                                      bool(st.diligence)))
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
        unit_origin = "do processo localizado" if st.process_number else "informada pelo usuário"
        lines = [
            "ATENDIMENTO VIRTUAL — SOLICITAÇÃO DE ATENDIMENTO HUMANO",
            "Canal escolhido: WhatsApp (esta conversa)",
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
        return "\n".join(lines)

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
            if self.rich and st.request_channel == "whatsapp" and st.request_note_id is None:
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
            self._safe_label(account, conv_id, list(dict.fromkeys(labels + ["humano"])))
            self.ops.transition(key, HandoffState.HUMAN_ACTIVE, actor="chatwoot", detail="transferência confirmada pela API")
            st = self.ops.get(key)
            st.handoff_attempts = 0
            self.ops.save(st)
            try:
                ticket = self.ops.get_open_ticket(key) if self.rich else None
                request = st.request_reason in ("diligencia", "providencia")
                self._send(account, conv_id, flow.handoff_notice(team, ticket.ticket_id if ticket else None, request))  # só após a confirmação (CHW-10)
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
        if self.rich and st.request_channel:
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
            ticket = self.ops.close_ticket(key, "atendente")  # resolvido direto na interface do Chatwoot
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
