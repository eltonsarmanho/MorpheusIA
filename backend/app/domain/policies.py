"""Políticas puras: triagem de acesso, números processuais e limiares de abstenção."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain.models import AccessClass, ReviewState

# Tipos documentais do PJe que costumam conter documentos pessoais (CUR-02).
SENSITIVE_DOC_TYPES = frozenset(
    {
        "documento de identificacao",
        "cedula de identidade",
        "comprovante de endereco",
        "ficha financeira",
        "comprovante",
    }
)

# Padrões no nome do documento que indicam dado pessoal sensível (CUR-02).
_SENSITIVE_NAME = re.compile(
    r"(?i)\b(rg|cpf|cnh|identidade|contracheque|holerite|comprovante de endere|"
    r"prontu[aá]rio|laudo m[eé]dico|atestado|exame|certid[aã]o de (?:nascimento|[oó]bito|casamento)|"
    r"extrato banc|declara[cç][aã]o de imposto)"
)

# Marcadores de sigilo no texto de capa ou nas decisões.
_SECRECY_TEXT = re.compile(r"(?i)segredo de justi[cç]a\??\s*[:\-]?\s*SIM|tramita(?:r|ndo)? em segredo de justi[cç]a")

PROCESS_NUMBER = re.compile(r"(?<!\d)(\d{7})-(\d{2})\.(\d{4})\.(\d)\.(\d{2})\.(\d{4})(?!\d)")
PROCESS_NUMBER_LOOSE = re.compile(r"(?<!\d)(\d{7})[-.\s]?(\d{2})[-.\s]?(\d{4})[-.\s]?(\d)[-.\s]?(\d{2})[-.\s]?(\d{4})(?!\d)")


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")


def normalize_process_number(raw: str) -> str | None:
    """Normaliza para o formato CNJ NNNNNNN-DD.AAAA.J.TR.OOOO ou devolve None."""
    m = PROCESS_NUMBER_LOOSE.search(raw)
    if not m:
        return None
    a, b, c, d, e, f = m.groups()
    return f"{a}-{b}.{c}.{d}.{e}.{f}"


def find_process_numbers(text: str) -> list[str]:
    seen: list[str] = []
    for m in PROCESS_NUMBER_LOOSE.finditer(text):
        num = normalize_process_number(m.group(0))
        if num and num not in seen:
            seen.append(num)
    return seen


@dataclass(frozen=True)
class AccessTriage:
    review_state: ReviewState
    access_class: AccessClass
    reason: str


def triage_document(
    *,
    doc_type: str,
    doc_name: str,
    cover_secrecy: str | None,
    body_text: str,
    corpus_authorized: bool,
) -> AccessTriage:
    """Decide o estado inicial de um documento processual.

    `approved` exige: autorização declarada do acervo, capa com "Segredo de justiça? NÃO",
    tipo e nome fora da lista sensível e nenhum marcador de sigilo no texto.
    Qualquer dúvida mantém `pending_review` (CUR-01, CUR-02).
    """
    if not corpus_authorized:
        return AccessTriage(ReviewState.PENDING_REVIEW, AccessClass.UNKNOWN, "acervo sem autorização declarada")
    secrecy = (cover_secrecy or "").strip().upper()
    if secrecy != "NAO" and secrecy != "NÃO":
        reason = "capa indica sigilo" if secrecy in {"SIM", "S"} else "sigilo da capa não identificado"
        return AccessTriage(ReviewState.PENDING_REVIEW, AccessClass.UNKNOWN, reason)
    if _fold(doc_type) in SENSITIVE_DOC_TYPES:
        return AccessTriage(ReviewState.PENDING_REVIEW, AccessClass.RESTRICTED, f"tipo documental sensível: {doc_type}")
    if _SENSITIVE_NAME.search(_fold(doc_name)) or _SENSITIVE_NAME.search(doc_name):
        return AccessTriage(ReviewState.PENDING_REVIEW, AccessClass.RESTRICTED, f"nome sugere dado pessoal: {doc_name[:60]}")
    if _SECRECY_TEXT.search(body_text):
        return AccessTriage(ReviewState.PENDING_REVIEW, AccessClass.UNKNOWN, "texto menciona segredo de justiça")
    return AccessTriage(ReviewState.APPROVED, AccessClass.PUBLIC, "autorização declarada do acervo + triagem automática")


@dataclass(frozen=True)
class AbstentionPolicy:
    """Critérios verificáveis de abstenção (RAG-04)."""

    min_fused_score: float = 0.0
    min_lexical_hits: int = 1
    min_evidences: int = 1
    min_term_coverage: float = 0.34
    max_context_chars: int = 7000
    top_k: int = 6
