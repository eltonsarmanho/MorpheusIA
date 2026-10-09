"""Orquestrador principal (ETAPA 5): fluxo determinístico, geração via LLM e verificação objetiva.

O LLM só gera texto a partir de evidências já filtradas por domínio, estado e acesso. Abstenção, esclarecimento e
encaminhamento são decididos aqui, por critérios verificáveis.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from app.application.answering.grounding import ModelAnswer, parse_model_output, verify_grounding
from app.application.answering.intent import IntentResult, classify_intent, detect_profile
from app.application.answering.prompts import SYSTEM_PROMPT, build_user_prompt, describe_source, evidence_label
from app.application.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.domain.models import (
    INTENT_DOMAIN,
    BotReply,
    Citation,
    Evidence,
    Intent,
    KnowledgeDomain,
    ResponseKind,
)
from app.domain.policies import find_process_numbers
from app.domain.ports import KnowledgeStore, LlmGenerator
from app.infrastructure.sqlite.operational_store import ConversationState

log = logging.getLogger(__name__)

DISCLAIMER_PROCESSUAL = "Base: documentos do acervo de demonstração do piloto; não é consulta em tempo real ao PJe ou a outro sistema oficial."
MAX_FAILURES_BEFORE_HANDOFF = 2

TEAM_BY_DOMAIN = {
    KnowledgeDomain.PROCESSUAL: "Informações Processuais",
    KnowledgeDomain.INSTITUCIONAL: "Informações Institucionais",
    KnowledgeDomain.JURIDICO: "Informações Jurídico-Institucionais",
}
_DOMAIN_INTENT = {v: k for k, v in INTENT_DOMAIN.items()}
TEAM_GENERAL = "Atendimento Humano Geral"
TEAM_TRIAGE = "Triagem e Orquestração"

LABEL_BY_INTENT = {
    Intent.CONSULTA_PROCESSUAL: "consulta_processual",
    Intent.DUVIDA_INSTITUCIONAL: "duvida_institucional",
    Intent.DUVIDA_JURIDICA: "duvida_institucional",
}

_AFFIRMATIVE = re.compile(r"^\s*(sim|s|pode|quero|por favor|ok|claro|aceito|pode sim|sim,? (pode|quero)|encaminh\w+)[\s!.]*$", re.I)
_LIST_PROCESSES = re.compile(r"(?i)\b(quais|que|lista|listar|liste|mostre|quantos)\b.{0,30}\b(processos|autos)\b|\bacervo\b")
_RECENT = re.compile(r"(?i)\b(mais recente|ultim[ao]|ultima|recentemente)\b")
_DOC_TYPES = (
    ("decisão", "decis"), ("despacho", "despacho"), ("sentença", "senten"), ("certidão", "certid"), ("petição", "peti"),
    ("mandado", "mandado"), ("intimação", "intima"), ("acórdão", "acórd"), ("ofício", "ofício"),
)
_SYNONYMS = {
    "juiz": "juiz magistrado", "réu": "réu acusado denunciado requerido", "reu": "réu acusado denunciado requerido",
    "autor": "autor requerente", "multa": "multa condenação pena", "sentença": "sentença julgamento decisão",
    "audiência": "audiência sessão", "prazo": "prazo dias", "valor": "valor quantia R$", "advogado": "advogado procurador OAB",
}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def reformulate(query: str) -> str:
    """Segunda tentativa de recuperação: remove ruído conversacional e expande termos jurídicos comuns."""
    words = re.findall(r"\w+|\S", query)
    out = []
    for w in words:
        out.append(_SYNONYMS.get(w.lower(), w))
    return " ".join(out)


@dataclass
class OrchestratorConfig:
    max_question_chars: int = 1000
    max_context_chars: int = 7000


@dataclass
class Turn:
    reply: BotReply
    state: ConversationState
    domain: KnowledgeDomain | None = None
    handoff_team: str | None = None
    labels: list[str] = field(default_factory=list)


class QuestionError(ValueError):
    pass


class Orchestrator:
    def __init__(self, retriever: HybridRetriever, store: KnowledgeStore, llm: LlmGenerator | None, config: OrchestratorConfig | None = None) -> None:
        self.retriever, self.store, self.llm = retriever, store, llm
        self.cfg = config or OrchestratorConfig()

    # ------------------------------------------------------------------ API
    def respond(self, message: str, st: ConversationState) -> Turn:
        text = (message or "").strip()
        if not text:
            raise QuestionError("pergunta vazia")
        if len(text) > self.cfg.max_question_chars:
            raise QuestionError(f"pergunta excede {self.cfg.max_question_chars} caracteres")

        st.profile = detect_profile(text, st.profile)
        if st.offer_pending and _AFFIRMATIVE.match(text):
            return self._handoff(st, "usuário aceitou o encaminhamento oferecido", self._team_for(st.last_domain), None)
        st.offer_pending = False

        intent = classify_intent(text)
        trace: dict[str, object] = {"intent": intent.intent.value, "intent_confidence": round(intent.confidence, 2), "profile": st.profile.value}

        if intent.intent is Intent.ATENDIMENTO_HUMANO:
            return self._handoff(st, "pedido do usuário", TEAM_GENERAL, intent.intent, trace)
        if intent.intent is Intent.SAUDACAO:
            return Turn(BotReply(ResponseKind.GREETING, self._greeting(), intent=intent.intent, trace=trace), st)
        if intent.intent is Intent.FORA_DE_ESCOPO and st.last_domain:
            # continuação de uma conversa em andamento: mantém o domínio; a suficiência da evidência continua sendo verificada
            intent = IntentResult(_DOMAIN_INTENT[KnowledgeDomain(st.last_domain)], 0.5, ("continuacao_da_conversa",))
            trace["intent"], trace["followup_of"] = intent.intent.value, st.last_domain
        if intent.intent is Intent.FORA_DE_ESCOPO:
            msg = ("Não identifiquei uma pergunta sobre os assuntos que atendo neste piloto: documentos de processos do acervo de "
                   "demonstração, informações institucionais ou conceitos jurídicos gerais. Pode reformular? Se preferir, posso "
                   "encaminhar você a um atendente humano.")
            return self._abstention(st, None, intent.intent, msg, "fora_de_escopo", trace, ["ia_orquestrador"])

        domain = INTENT_DOMAIN[intent.intent]
        st.last_domain = domain.value
        labels = ["ia_orquestrador", "ia_rag", LABEL_BY_INTENT[intent.intent]]

        if domain is KnowledgeDomain.PROCESSUAL and _LIST_PROCESSES.search(text) and not find_process_numbers(text):
            return Turn(self._list_processes(trace, intent.intent), st, domain, labels=labels)

        numbers = find_process_numbers(text)
        if numbers:
            st.process_number = numbers[0]

        # Pergunta de recência: ordena pela data do documento, nunca pela data de indexação
        if domain is KnowledgeDomain.PROCESSUAL and _RECENT.search(_fold(text)):
            recent = self._recent_document(text, st, trace, intent.intent)
            if isinstance(recent, BotReply):
                return Turn(recent, st, domain, labels=labels)
            if isinstance(recent, RetrievalResult):
                return self._generate(text, recent, st, domain, intent.intent, trace, labels)

        result, attempts = self._retrieve(text, domain, st)
        trace.update(attempts=attempts, **{f"retrieval_{k}": v for k, v in result.stages.items()})
        trace["abstain_reason"] = result.abstain_reason

        if result.abstain_reason == "processo_ausente_do_acervo":
            nums = ", ".join(result.unknown_process_numbers)
            msg = (f"O processo {nums} não consta do acervo de documentos de demonstração deste piloto, por isso não consigo "
                   "informar nada sobre ele. Não consulto o PJe nem outro sistema oficial em tempo real. Posso encaminhar você a um "
                   "atendente humano, se desejar.")
            return self._abstention(st, domain, intent.intent, msg, "processo_ausente_do_acervo", trace, labels)
        if not result.sufficient:
            return self._abstention(st, domain, intent.intent, self._abstain_text(domain), result.abstain_reason or "insuficiente", trace, labels)

        if domain is KnowledgeDomain.PROCESSUAL and not st.process_number:
            procs = list(dict.fromkeys(e.citation.get("processo") for e in result.evidences[:3] if e.citation.get("processo")))
            if len(procs) >= 2:
                return self._clarify(st, domain, intent.intent, procs, trace, labels)
            if len(procs) == 1 and all(e.citation.get("processo") == procs[0] for e in result.evidences):
                st.process_number = procs[0]

        return self._generate(text, result, st, domain, intent.intent, trace, labels)

    # -------------------------------------------------------------- recuperação
    def _retrieve(self, text: str, domain: KnowledgeDomain, st: ConversationState) -> tuple[RetrievalResult, int]:
        result = self.retriever.retrieve(text, domain, process_number=st.process_number)
        if result.sufficient or result.abstain_reason == "processo_ausente_do_acervo":
            return result, 1
        second = self.retriever.retrieve(reformulate(text), domain, process_number=st.process_number)
        return (second if second.sufficient else result), 2

    def _recent_document(self, text: str, st: ConversationState, trace: dict, intent: Intent) -> BotReply | RetrievalResult | None:
        number = st.process_number
        doc_kind = next((stem for label, stem in _DOC_TYPES if stem in _fold(text) or label in text.lower()), None)
        if not number or not doc_kind:
            return None
        doc = self.store.latest_document(number, doc_kind)  # type: ignore[attr-defined]
        if doc is None:
            msg = (f"Não encontrei, no acervo de demonstração, um documento desse tipo com data conhecida para o processo {number}; "
                   "por isso não consigo afirmar qual é o mais recente. Posso encaminhar você a um atendente humano.")
            return BotReply(ResponseKind.ABSTAIN, msg, KnowledgeDomain.PROCESSUAL, intent, abstain_reason="recencia_nao_verificavel", trace=trace)
        evs = self.store.evidences_for_doc(doc.doc_id, 4)  # type: ignore[attr-defined]
        trace.update(recency_doc=doc.doc_id, recency_criterion="data do documento na tabela da capa do PDF")
        return RetrievalResult(evs, number, [], 1.0, None, {"recency": True})

    # ----------------------------------------------------------------- geração
    def _generate(self, question: str, result: RetrievalResult, st: ConversationState, domain: KnowledgeDomain, intent: Intent,
                  trace: dict, labels: list[str]) -> Turn:
        if self.llm is None:
            return self._abstention(st, domain, intent, self._abstain_text(domain), "llm_indisponivel", trace, labels)
        evidences = result.evidences
        prompt, info = build_user_prompt(question, evidences, st.profile, domain, self.cfg.max_context_chars)
        evidences = evidences[: info["evidences_in_prompt"]]
        trace.update(info)
        label_map = {evidence_label(i): e for i, e in enumerate(evidences, start=1)}
        answer: ModelAnswer | None = None
        for attempt in (1, 2):
            try:
                raw = self.llm.generate(system=SYSTEM_PROMPT, user=prompt if attempt == 1 else prompt + "\n\nLembrete: responda SOMENTE com o JSON.")
            except Exception as exc:  # noqa: BLE001 - falha do provedor vira abstenção
                log.warning("falha do LLM: %s", type(exc).__name__)
                trace["llm_error"] = type(exc).__name__
                return self._abstention(st, domain, intent, self._technical_text(), "falha_do_llm", trace, labels + ["ia_falha"], technical=True)
            answer = parse_model_output(raw)
            if answer.parse_ok:
                break
        assert answer is not None
        if not answer.parse_ok:
            return self._abstention(st, domain, intent, self._technical_text(), "saida_do_modelo_invalida", trace, labels + ["ia_falha"], technical=True)
        if answer.handoff:
            return self._handoff(st, "o modelo indicou necessidade de atuação humana", self._team_for(domain.value), intent, trace, labels)
        if not answer.sufficient:
            return self._abstention(st, domain, intent, self._abstain_text(domain), "modelo_declarou_insuficiencia", trace, labels)

        report = verify_grounding(answer, evidences, label_map, question)
        trace["grounding"] = {"ok": report.ok, "problems": report.problems, "checked": report.checked}
        if not report.ok:
            return self._abstention(st, domain, intent, self._abstain_text(domain, grounded=False), "fundamentacao_nao_verificada", trace, labels)

        text, citations = self._render(answer, label_map)
        if domain is KnowledgeDomain.PROCESSUAL:
            text += f"\n\n{DISCLAIMER_PROCESSUAL}"
        st.failed_retrievals = 0
        reply = BotReply(ResponseKind.ANSWER, text, domain, intent, citations, trace=trace)
        return Turn(reply, st, domain, labels=labels)

    @staticmethod
    def _render(answer: ModelAnswer, label_map: dict[str, Evidence]) -> tuple[str, list[Citation]]:
        order: list[str] = []
        for ref in re.findall(r"\[(E\d+)\]", answer.text) + answer.references:
            if ref in label_map and ref not in order:
                order.append(ref)
        numbering = {ref: i for i, ref in enumerate(order, start=1)}
        body = re.sub(r"\[(E\d+)\]", lambda m: f"[{numbering[m.group(1)]}]" if m.group(1) in numbering else "", answer.text)
        body = re.sub(r"\s+([.,;])", r"\1", body).strip()
        citations, lines = [], []
        for ref in order:
            e = label_map[ref]
            c = e.citation
            label = describe_source(e)
            citations.append(Citation(ref_id=str(numbering[ref]), label=label, doc_id=e.doc_id, page=e.page, url=c.get("url") or None))
            lines.append(f"[{numbering[ref]}] {label}")
        return body + "\n\nFontes:\n" + "\n".join(lines), citations

    # ------------------------------------------------------------- desfechos
    def _abstention(self, st: ConversationState, domain, intent, text: str, reason: str, trace: dict, labels: list[str], technical: bool = False) -> Turn:
        st.failed_retrievals += 1
        if st.failed_retrievals >= MAX_FAILURES_BEFORE_HANDOFF:
            return self._handoff(st, f"{st.failed_retrievals} falhas consecutivas de resposta ({reason})", self._team_for(domain.value if domain else None),
                                 intent, trace, labels)
        st.offer_pending = True
        trace["abstain_reason"] = reason
        return Turn(BotReply(ResponseKind.ABSTAIN, text, domain, intent, abstain_reason=reason, trace=trace), st, domain, labels=labels)

    def _clarify(self, st, domain, intent, procs: list[str], trace, labels) -> Turn:
        info = [self.store.process_info(p) for p in procs] if hasattr(self.store, "process_info") else []  # type: ignore[attr-defined]
        lines = []
        for p, i in zip(procs, info):
            lines.append(f"- {p}" + (f" ({i['process_class']}, {i['court_unit']})" if i else ""))
        msg = "Sua pergunta pode se referir a mais de um processo do acervo de demonstração. Informe o número do processo:\n" + "\n".join(lines)
        return Turn(BotReply(ResponseKind.CLARIFY, msg, domain, intent, trace=trace), st, domain, labels=labels)

    def _list_processes(self, trace: dict, intent: Intent) -> BotReply:
        rows = self.store.approved_processes()  # type: ignore[attr-defined]
        if not rows:
            return BotReply(ResponseKind.ABSTAIN, "Nenhum processo está disponível no acervo aprovado deste piloto.", KnowledgeDomain.PROCESSUAL,
                            intent, abstain_reason="acervo_vazio", trace=trace)
        lines = [f"- {r['process_number']}: {r['process_class']}, {r['court_unit']}; assuntos: {r['subjects']}" for r in rows]
        text = f"O acervo de demonstração tem {len(rows)} processos disponíveis:\n" + "\n".join(lines) + f"\n\n{DISCLAIMER_PROCESSUAL}"
        return BotReply(ResponseKind.ANSWER, text, KnowledgeDomain.PROCESSUAL, intent, trace={**trace, "deterministic": "lista_de_processos"})

    def _handoff(self, st: ConversationState, reason: str, team: str, intent: Intent | None, trace: dict | None = None,
                 labels: list[str] | None = None) -> Turn:
        st.offer_pending = False
        st.handoff_reason, st.handoff_team = reason, team
        reply = BotReply(ResponseKind.HANDOFF, "", intent=intent, handoff_reason=reason, trace=trace or {})
        return Turn(reply, st, handoff_team=team, labels=(labels or ["ia_orquestrador"]) + ["humano"])

    # ------------------------------------------------------------------ textos
    @staticmethod
    def _team_for(domain: str | None) -> str:
        try:
            return TEAM_BY_DOMAIN[KnowledgeDomain(domain)] if domain else TEAM_GENERAL
        except ValueError:
            return TEAM_GENERAL

    @staticmethod
    def _greeting() -> str:
        return ("Olá! Sou o Assistente Virtual do projeto-piloto do TJPA. Posso ajudar com (1) documentos de processos do acervo de "
                "demonstração, (2) informações institucionais, como Balcão Virtual e contatos, e (3) conceitos jurídicos gerais. "
                "As respostas processuais se baseiam nos documentos do acervo de demonstração, não em consulta em tempo real ao PJe. "
                "Se quiser falar com uma pessoa, escreva \"atendente\". Como posso ajudar?")

    @staticmethod
    def _abstain_text(domain: KnowledgeDomain, grounded: bool = True) -> str:
        where = {
            KnowledgeDomain.PROCESSUAL: "nos documentos do acervo de demonstração",
            KnowledgeDomain.INSTITUCIONAL: "nas informações institucionais aprovadas",
            KnowledgeDomain.JURIDICO: "nas fontes jurídicas oficiais aprovadas",
        }[domain]
        why = "Não encontrei" if grounded else "Não consegui confirmar com segurança"
        return (f"{why} {where} informação suficiente para responder a essa pergunta. Prefiro não arriscar uma resposta sem fundamento. "
                "Você pode reformular a pergunta ou, se preferir, posso encaminhar você a um atendente humano. Deseja o encaminhamento?")

    @staticmethod
    def _technical_text() -> str:
        return ("Tive uma dificuldade técnica para preparar a resposta agora. Você pode tentar de novo em instantes ou pedir para falar com um "
                "atendente humano.")
