"""Triagem da Ouvidoria da Mulher (OVM-08 a OVM-15, OVM-21, OVM-25): dados estruturados para o atendente.

A triagem NÃO avalia risco nem aplica o FONAR: só organiza o que a pessoa escolheu informar. Todo campo tem enumeração
fechada; valor fora dela é descartado ao ler (nunca corrigido). O relato e o número do processo ficam só no estado e na nota
privada, nunca em log ou auditoria.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import time

NAO_INFORMADO = "nao_informado"
SCHEMA_VERSION = 1

STEPS = ("perigo", "para_quem", "assunto", "ambiente", "processo", "contato", "relato", "resumo")

# campo -> {valor: rótulo legível para o atendente}
CHOICES: dict[str, dict[str, str]] = {
    "perigo": {"sim": "perigo imediato informado", "nao": "sem perigo imediato informado", NAO_INFORMADO: "não informado"},
    "para_quem": {"propria": "para si mesma", "terceiro": "outra pessoa (relato de terceiro)", NAO_INFORMADO: "não informado"},
    "assunto": {
        "fisica": "violência física", "sexual": "violência sexual", "psicologica": "violência psicológica",
        "patrimonial": "violência patrimonial", "moral": "violência moral", "online": "violência on-line",
        "perseguicao": "perseguição (stalking)", "assedio_importunacao": "assédio ou importunação",
        "morosidade": "demora em processo", "outro": "outro ou não sabe dizer", NAO_INFORMADO: "não informado",
    },
    "ambiente": {
        "parceiro_ex": "parceiro(a) ou ex", "familiar": "familiar", "trabalho": "trabalho", "espaco_publico": "espaço público",
        "internet": "internet ou redes", "servico_publico": "serviço público", "outro": "outro lugar", NAO_INFORMADO: "não informado",
    },
    "processo": {"numero": "há processo ou medida protetiva, número informado", "sem_numero": "há processo ou medida protetiva, sem número",
                 "nao": "sem processo ou medida protetiva", "nao_sei": "não sabe informar", NAO_INFORMADO: "não informado"},
    "contato": {"sim": "pode continuar por este WhatsApp", "nao": "contato por este WhatsApp NÃO é seguro",
                "telefone": "prefere telefone", NAO_INFORMADO: "não informado"},
}

_CNJ = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


@dataclass
class Triage:
    step: str = "perigo"
    perigo: str | None = None
    para_quem: str | None = None
    assunto: str | None = None
    ambiente: str | None = None
    processo: str | None = None
    numero_processo: str | None = None  # informado pela pessoa; nunca "localizado"
    contato: str | None = None
    relato: str | None = None
    consentimento: bool = False
    protocol_advice: bool = False  # a pessoa foi orientada a guardar o protocolo (OVM-19)
    _extra: dict = field(default_factory=dict, repr=False)

    @property
    def no_return(self) -> bool:
        """Só retorna por este WhatsApp quem disse que é seguro; não saber é tratado como não seguro (OVM-16)."""
        return self.contato != "sim"

    @property
    def urgent(self) -> bool:
        return self.perigo == "sim"

    def set(self, name: str, value: str) -> bool:
        """Grava uma escolha só se estiver na enumeração do campo."""
        if value not in CHOICES.get(name, {}):
            return False
        setattr(self, name, value)
        return True

    def next_step(self) -> str:
        i = STEPS.index(self.step)
        self.step = STEPS[min(i + 1, len(STEPS) - 1)]
        return self.step

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("_extra", None)
        data["versao"] = SCHEMA_VERSION
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> "Triage | None":
        """None para ausente ou ilegível; valores fora da enumeração viram None (OVM-25)."""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        t = cls()
        t.step = data.get("step") if data.get("step") in STEPS else "perigo"
        for name, allowed in CHOICES.items():
            value = data.get(name)
            setattr(t, name, value if value in allowed else None)
        number = data.get("numero_processo")
        t.numero_processo = number if isinstance(number, str) and _CNJ.match(number) else None
        relato = data.get("relato")
        t.relato = relato if isinstance(relato, str) and relato else None
        t.consentimento = data.get("consentimento") is True
        t.protocol_advice = data.get("protocol_advice") is True
        return t

    def label(self, name: str) -> str:
        return CHOICES[name].get(getattr(self, name) or NAO_INFORMADO, CHOICES[name][NAO_INFORMADO])

    def attributes(self) -> dict[str, str]:
        """Atributos da conversa no Chatwoot: só enumerações, nunca relato nem número de processo."""
        return {"servico": "ouvidoria_mulher", "assunto": self.assunto or NAO_INFORMADO, "ambiente": self.ambiente or NAO_INFORMADO,
                "urgencia": "imediata" if self.urgent else "nao_informada" if not self.perigo or self.perigo == NAO_INFORMADO else "sem_perigo_informado",
                "para_quem": self.para_quem or NAO_INFORMADO}

    def summary_for_user(self) -> str:
        """Resumo mostrado antes do consentimento: sem o texto do relato."""
        lines = [f"• Para quem: {self.label('para_quem')}", f"• Assunto: {self.label('assunto')}", f"• Onde ou com quem: {self.label('ambiente')}",
                 f"• Processo ou medida protetiva: {self.label('processo')}", f"• Contato: {self.label('contato')}"]
        lines.append("• Relato: " + ("você escreveu um relato" if self.relato else "não incluído"))
        return "\n".join(lines)


@dataclass(frozen=True)
class OuvidoriaMulher:
    """Contatos da Ouvidoria da Mulher do TJPA, sempre com fonte; ausente = não exibido."""

    phone: str | None = None
    email: str | None = None
    opens: time = time(8, 0)
    closes: time = time(14, 0)
    source: str = ""
    verified_at: str = ""

    def is_open(self, local: time) -> bool:
        return self.opens <= local < self.closes

    @property
    def hours_text(self) -> str:
        return f"{self.opens.hour}h às {self.closes.hour}h"

    def contact_lines(self) -> str:
        lines = []
        if self.phone:
            lines.append(f"📞 Telefone: {self.phone}")
        if self.email:
            lines.append(f"✉️ E-mail: {self.email}")
        lines.append(f"🕒 Atendimento: {self.hours_text} (horário de Belém)")
        return "\n".join(lines)


_VIOLENCE = re.compile(
    r"\b(me bate|me bateu|me bat[eo]u|me agrid[eiu]\w*|fui agredida|sofro violencia|sofrendo violencia|violencia domestica|"
    r"maria da penha|medida protetiva|me amea[cç]\w*|ameaca de morte|fui estuprada|estupro|abuso sexual|assedio sexual|assedio moral|"
    r"importunacao sexual|feminicidio|meu (marido|namorado|companheiro|ex|esposo|noivo|pai|padrasto|chefe) (me )?(bate|bateu|agride|agrediu|amea\w+|persegue|obriga|humilha))\b"
)


def mentions_violence(folded_text: str) -> bool:
    """Heurística de oferta (OVM-03): recebe texto sem acento e em minúsculas. Só oferece o serviço, nunca age sozinha."""
    return bool(_VIOLENCE.search(folded_text))
