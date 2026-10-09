"""Verificação objetiva da resposta contra as evidências (RAG-06, RAG-07). Não confia na confiança do modelo."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.domain.models import Evidence
from app.domain.policies import find_process_numbers

_DATE_BR = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONEY = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)")
_NUMBER = re.compile(r"(?<![\d./-])\d{3,}(?:[.,]\d+)*(?![\d/-])")
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
    def br(m: re.Match[str]) -> str:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    return _DATE_BR.sub(br, text)


def _digits_variants(token: str) -> set[str]:
    base = token.strip(".,")
    return {base, re.sub(r"[.,]", "", base)}


def _fact_tokens(text: str) -> dict[str, set[str]]:
    t = _norm_dates(text)
    return {
        "datas": {"-".join(x) for x in _DATE_ISO.findall(t)},
        "valores": {m for m in _MONEY.findall(text)},
        "processos": set(find_process_numbers(text)),
    }


def verify_grounding(answer: ModelAnswer, evidences: list[Evidence], labels: dict[str, Evidence], question: str) -> GroundingReport:
    """Falha quando: referência inexistente, resposta sem referência, ou fato (data, valor, processo) sem lastro."""
    problems: list[str] = []
    unknown = [r for r in answer.references if r not in labels]
    if unknown:
        problems.append(f"referencias_inexistentes:{','.join(unknown)}")
    cited = [labels[r] for r in answer.references if r in labels]
    if not cited:
        problems.append("resposta_sem_referencia_valida")
        return GroundingReport(False, problems)

    support = " \n".join(e.text for e in cited) + " \n" + " ".join(
        " ".join(v for v in e.citation.values() if v) for e in cited
    )
    support_norm = _norm_dates(support)
    support_digits = re.sub(r"[.,\s]", "", support_norm)
    question_norm = _norm_dates(question)

    answer_clean = _REF.sub("", answer.text)
    facts = _fact_tokens(answer_clean)
    checked = {"datas": 0, "valores": 0, "processos": 0, "numeros": 0}

    for d in facts["datas"]:
        checked["datas"] += 1
        if d not in support_norm and d not in question_norm:
            problems.append(f"data_sem_lastro:{d}")
    for v in facts["valores"]:
        checked["valores"] += 1
        if re.sub(r"[.,\s]", "", v) not in support_digits and re.sub(r"[.,\s]", "", v) not in re.sub(r"[.,\s]", "", question_norm):
            problems.append(f"valor_sem_lastro:R$ {v}")
    for p in facts["processos"]:
        checked["processos"] += 1
        if p not in support and p not in question:
            problems.append(f"processo_sem_lastro:{p}")
    # números isolados (ids de documento, artigos etc.) com 5+ dígitos
    cleaned = _DATE_BR.sub("", _MONEY.sub("", answer_clean))
    cleaned = re.sub(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", "", cleaned)
    for n in set(re.findall(r"(?<![\d./-])\d{5,}(?![\d/-])", cleaned)):
        checked["numeros"] += 1
        if n not in support_digits and n not in question:
            problems.append(f"numero_sem_lastro:{n}")
    return GroundingReport(not problems, problems, checked)
