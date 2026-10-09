"""Mascaramento de dados pessoais no texto indexado (SEC-01).

Nomes de partes e advogados permanecem; o PDF original nunca é alterado.
"""

from __future__ import annotations

import re

_CPF = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_CPF_RAW = re.compile(r"(?i)(\bCPF\b\D{0,12})(\d{11})(?!\d)")
_RG = re.compile(r"(?i)\b(RG|Identidade|C\.?I\.?)\s*(?:n[ºo°.]*\s*)?[:\-]?\s*(\d{1,2}\.?\d{3}\.?\d{3}-?[\dXx]?)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE_AREA = re.compile(r"(?<!\d)\(\s?[1-9]\d\s?\)\s?9?\d{4}[-\s]?\d{4}(?!\d)")
_PHONE_PLAIN = re.compile(r"(?<![\d.\-/])[1-9]\d\s?9\d{4}-?\d{4}(?![\d.\-/])")
_PHONE_CTX = re.compile(r"(?i)(\b(?:tel(?:efone)?|cel(?:ular)?|whats(?:app)?|fone)\b[^\d]{0,12})(\d{4,5}-?\d{4})(?!\d)")
_CEP = re.compile(r"(?<![\d.\-/])\d{5}-\d{3}(?![\d\-])")

_PLACEHOLDERS = {
    "cpf": "[CPF]",
    "rg": "[RG]",
    "email": "[EMAIL]",
    "telefone": "[TELEFONE]",
    "cep": "[CEP]",
}


def mask_pii(text: str) -> tuple[str, dict[str, int]]:
    """Devolve o texto mascarado e a contagem de ocorrências por tipo."""
    counts: dict[str, int] = {}

    def sub(kind: str, pattern: re.Pattern[str], repl, s: str) -> str:
        new, n = pattern.subn(repl, s)
        if n:
            counts[kind] = counts.get(kind, 0) + n
        return new

    out = text
    out = sub("cpf", _CPF, _PLACEHOLDERS["cpf"], out)
    out = sub("cpf", _CPF_RAW, lambda m: m.group(1) + _PLACEHOLDERS["cpf"], out)
    out = sub("rg", _RG, lambda m: f"{m.group(1)} {_PLACEHOLDERS['rg']}", out)
    out = sub("email", _EMAIL, _PLACEHOLDERS["email"], out)
    out = sub("telefone", _PHONE_AREA, _PLACEHOLDERS["telefone"], out)
    out = sub("telefone", _PHONE_CTX, lambda m: m.group(1) + _PLACEHOLDERS["telefone"], out)
    out = sub("telefone", _PHONE_PLAIN, _PLACEHOLDERS["telefone"], out)
    out = sub("cep", _CEP, _PLACEHOLDERS["cep"], out)
    return out, counts
