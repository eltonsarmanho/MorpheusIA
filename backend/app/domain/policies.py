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


def cnj_check_digits_ok(number: str) -> bool:
    """Dígitos verificadores do número CNJ (Resolução CNJ 65/2008, módulo 97). Formato válido não significa processo localizado."""
    d = "".join(ch for ch in number if ch.isdigit())
    if len(d) != 20:
        return False
    seq, dv, rest = d[:7], d[7:9], d[9:]
    return 98 - (int(seq + rest) * 100) % 97 == int(dv)


_CPF_LOOSE = re.compile(r"(?<![\d])(\d{3})\.?(\d{3})\.?(\d{3})-?(\d{2})(?![\d])")


def extract_cpf(text: str) -> str | None:
    """Os 11 dígitos do CPF presente no texto (com ou sem pontuação), ou None."""
    m = _CPF_LOOSE.search(text)
    return "".join(m.groups()) if m else None


def mask_cpf(text: str) -> str:
    """Troca por [CPF] todo CPF com dígitos válidos; o restante do texto (inclusive números de processo) fica intacto."""
    return _CPF_LOOSE.sub(lambda m: "[CPF]" if cpf_is_valid("".join(m.groups())) else m.group(0), text)


def cpf_is_valid(cpf: str) -> bool:
    """Dígitos verificadores do CPF; sequências repetidas (111.111.111-11) são inválidas."""
    d = "".join(ch for ch in cpf if ch.isdigit())
    if len(d) != 11 or d == d[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d[i]) * (size + 1 - i) for i in range(size))
        if (total * 10) % 11 % 10 != int(d[size]):
            return False
    return True


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
    min_evidences: int = 1
    min_term_coverage: float = 0.34
    min_vector_score: float = 0.45  # calibrado em evals: ruído fora do acervo fica abaixo; perguntas respondíveis ficam acima de 0,59
    max_context_chars: int = 7000
    top_k: int = 6
