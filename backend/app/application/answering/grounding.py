"""Verificação objetiva da resposta contra as evidências (RAG-06, RAG-07). Não confia na confiança do modelo."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field

from app.domain.models import Evidence
from app.domain.policies import find_process_numbers

_DATE_BR = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
           "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_DATE_LONG = re.compile(r"(?<!\d)(\d{1,2})º?\s+de\s+(" + "|".join(_MONTHS) + r")\s+de\s+(\d{4})(?!\d)", re.I)
_MONEY = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)")
_QUANTITY = re.compile(r"(?<![\d.,/-])(\d+(?:[.,]\d+)?)\s*(dias?|meses|m[eê]s|anos?|horas?|h\b|%|por cento|sal[aá]rios|parcelas?|vezes|reais)", re.I)
_REF = re.compile(r"\[(E\d+)\]")


@dataclass
class ModelAnswer:
    text: str
    references: list[str]
    sufficient: bool
    handoff: bool
    parse_ok: bool = True


@dataclass
class GroundingReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)


def parse_model_output(raw: str) -> ModelAnswer:
    """Extrai o JSON da saída do modelo; se inválido, parse_ok=False."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return ModelAnswer(raw.strip(), [], False, False, parse_ok=False)
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return ModelAnswer(raw.strip(), [], False, False, parse_ok=False)
    text = str(obj.get("resposta", "")).strip()
    refs = [str(r).strip().upper() for r in obj.get("referencias", []) if isinstance(r, (str, int))]
    refs = [r if r.startswith("E") else f"E{r}" for r in refs]
    refs = list(dict.fromkeys(refs + _REF.findall(text)))
    return ModelAnswer(text, refs, bool(obj.get("suficiente", False)), bool(obj.get("encaminhar", False)), parse_ok=bool(text))


def _norm_dates(text: str) -> str:
    """Escreve todas as datas (dd/mm/aaaa e "21 de julho de 2026") no formato ISO."""
    def br(m: re.Match[str]) -> str:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    def long(m: re.Match[str]) -> str:
        return f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"

    return _DATE_LONG.sub(long, _DATE_BR.sub(br, text))


def _money_value(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def verify_grounding(answer: ModelAnswer, evidences: list[Evidence], labels: dict[str, Evidence], question: str) -> GroundingReport:
    """Falha quando: referência inexistente, resposta sem referência, ou fato (data, valor, prazo, processo, id) sem lastro."""
    problems: list[str] = []
    unknown = [r for r in answer.references if r not in labels]
    if unknown:
        problems.append(f"referencias_inexistentes:{','.join(unknown)}")
    cited = [labels[r] for r in answer.references if r in labels]
    if not cited:
        problems.append("resposta_sem_referencia_valida")
        return GroundingReport(False, problems)

    support = " \n".join(e.text for e in cited) + " \n" + " ".join(" ".join(v for v in e.citation.values() if v) for e in cited)
    support_norm = _norm_dates(support)
    question_norm = _norm_dates(question)
    context = support_norm + " " + question_norm
    answer_clean = _norm_dates(_REF.sub("", answer.text))
    checked = {"datas": 0, "valores": 0, "processos": 0, "numeros": 0, "quantidades": 0}

    for d in {"-".join(x) for x in _DATE_ISO.findall(answer_clean)}:
        checked["datas"] += 1
        if d not in context:
            problems.append(f"data_sem_lastro:{d}")

    known_money = {v for v in (_money_value(m) for m in _MONEY.findall(context)) if v is not None}
    for raw in set(_MONEY.findall(answer_clean)):
        checked["valores"] += 1
        v = _money_value(raw)
        if v is None or v not in known_money:  # igualdade exata do valor, não de dígitos soltos
            problems.append(f"valor_sem_lastro:R$ {raw}")

    for p in set(find_process_numbers(answer_clean)):
        checked["processos"] += 1
        if p not in support and p not in question:
            problems.append(f"processo_sem_lastro:{p}")

    for raw, unit in set(_QUANTITY.findall(_MONEY.sub("", answer_clean))):
        checked["quantidades"] += 1
        pattern = rf"(?<![\d.,/-]){re.escape(raw)}(?![\d]|[.,]\d)"
        if not re.search(pattern, context):
            problems.append(f"quantidade_sem_lastro:{raw} {unit}")

    cleaned = _MONEY.sub("", _DATE_ISO.sub("", answer_clean))
    cleaned = re.sub(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", "", cleaned)
    for n in set(re.findall(r"(?<![\d./-])\d{5,}(?![\d/-])", cleaned)):  # ids de documento, números longos
        checked["numeros"] += 1
        if not re.search(rf"(?<![\d]){n}(?![\d])", re.sub(r"[.\s]", "", context)) and n not in context:
            problems.append(f"numero_sem_lastro:{n}")
    return GroundingReport(not problems, problems, checked)
