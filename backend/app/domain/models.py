"""Entidades e valores do domínio. Sem dependência de framework ou I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

DESCONHECIDO = "desconhecido"


class KnowledgeDomain(StrEnum):
    PROCESSUAL = "processual"
    INSTITUCIONAL = "institucional"
    JURIDICO = "juridico"


class ReviewState(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"


class AccessClass(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class Intent(StrEnum):
    CONSULTA_PROCESSUAL = "consulta_processual"
    DUVIDA_INSTITUCIONAL = "duvida_institucional"
    DUVIDA_JURIDICA = "duvida_juridica"
    ATENDIMENTO_HUMANO = "atendimento_humano"
    SAUDACAO = "saudacao"
    FORA_DE_ESCOPO = "fora_de_escopo"


INTENT_DOMAIN: dict[Intent, KnowledgeDomain] = {
    Intent.CONSULTA_PROCESSUAL: KnowledgeDomain.PROCESSUAL,
    Intent.DUVIDA_INSTITUCIONAL: KnowledgeDomain.INSTITUCIONAL,
    Intent.DUVIDA_JURIDICA: KnowledgeDomain.JURIDICO,
}


class Profile(StrEnum):
    CIDADAO = "cidadao"
    ADVOGADO = "advogado"
    INDEFINIDO = "indefinido"


class ResponseKind(StrEnum):
    ANSWER = "answer"
    ABSTAIN = "abstain"
    CLARIFY = "clarify"
    HANDOFF = "handoff"
    GREETING = "greeting"
    SILENT = "silent"


@dataclass(frozen=True)
class DocumentRecord:
    """Unidade curável: um documento PJe, uma página institucional ou uma norma."""

    doc_id: str
    domain: KnowledgeDomain
    title: str
    doc_type: str = DESCONHECIDO
    process_key: str | None = None
    process_number: str | None = None
    pje_doc_id: str | None = None
    doc_date: str = DESCONHECIDO
    doc_date_source: str = DESCONHECIDO
    signed_at: str = DESCONHECIDO
    published_at: str = DESCONHECIDO
    source_file: str | None = None
    source_url: str | None = None
    issuing_body: str = DESCONHECIDO
    page_start: int | None = None
    page_end: int | None = None
    access_class: AccessClass = AccessClass.UNKNOWN
    review_state: ReviewState = ReviewState.PENDING_REVIEW
    review_reason: str = ""
    content_hash: str = ""
    indexed_at: str = ""
    collected_at: str = ""
    valid_from: str = DESCONHECIDO
    valid_until: str = DESCONHECIDO
    pii_counts: dict[str, int] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkRecord:
    doc_id: str
    domain: KnowledgeDomain
    seq: int
    page: int | None
    text: str
    context: str
    content_hash: str
    pje_page: int | None = None


@dataclass(frozen=True)
class Evidence:
    """Trecho recuperado, já filtrado por estado e classe de acesso."""

    chunk_id: int
    doc_id: str
    domain: KnowledgeDomain
    text: str
    page: int | None
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None
    lexical_score: float | None = None
    vector_score: float | None = None
    citation: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Citation:
    ref_id: str
    label: str
    doc_id: str
    page: int | None
    url: str | None = None


@dataclass
class BotReply:
    kind: ResponseKind
    text: str
    domain: KnowledgeDomain | None = None
    intent: Intent | None = None
    citations: list[Citation] = field(default_factory=list)
    abstain_reason: str | None = None
    handoff_reason: str | None = None
    trace: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Option:
    """Opção de botão (até 3) ou de lista (até 10) do WhatsApp. O texto do título volta como a mensagem do usuário."""

    title: str
    value: str
