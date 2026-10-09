"""Classificação determinística de intenção e de perfil (ORQ-01, ORQ-02)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.domain.models import Intent, Profile
from app.domain.policies import find_process_numbers


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


_HUMAN = re.compile(
    r"\b(atendente|atendimento humano|falar com (um |uma )?(pessoa|humano|atendente|alguem|servidor)|"
    r"quero (falar|conversar) com (um |uma )?(pessoa|humano|atendente|alguem)|passar para (um )?humano|"
    r"transfer(ir|encia)|me (ajude|ajuda) com (um )?atendente|nao quero (falar com )?(robo|bot))\b"
)
_GREETING = re.compile(r"^\s*(oi+|ola+|bom dia|boa tarde|boa noite|e ai|tudo bem\??|menu|inicio|iniciar|ajuda)[\s!.,?]*$")

_PROCESSUAL = re.compile(
    r"\b(processo|autos|autor(a)?|reu|re|requerente|requerido|vara|decisao|decisoes|despacho|sentenca|certidao|peticao|"
    r"audiencia|movimentacao|movimentacoes|juntad[ao]|documento[s]? do processo|cronologia|andamento|ultima decisao|"
    r"acordao|mandado|intimacao|citacao|contestacao|replica|alvara|honorarios|valor da causa|distribuicao|partes?)\b"
)
_INSTITUCIONAL = re.compile(
    r"\b(balcao virtual|endereco|horario|funcionamento|telefone|contato|e-?mail|onde fica|fica onde|forum|unidade[s]?|comarca|"
    r"atendimento presencial|canais? de atendimento|servicos? (do )?(tribunal|tjpa)|como (acesso|solicito|agendo)|agendar|"
    r"protocolo|ouvidoria|portal|sistema de consulta|pje|balcao|secretaria|cartorio|tjpa|tribunal de justica do (estado do )?para)\b"
)
_JURIDICA = re.compile(
    r"\b(o que (e|significa|quer dizer)|qual a diferenca|diferenca entre|conceito|definicao|significado|prazo (para|de)|"
    r"recurso|apelacao|agravo|embargos|competencia|prescricao|decadencia|lei|codigo|cpc|cpp|cf/?88|constituicao|cnj|resolucao|"
    r"artigo|art\.|norma|jurisprudencia|tutela|liminar|justica gratuita|gratuidade|ato processual|termo juridico)\b"
)
_GENERIC_QUESTION = re.compile(r"^\s*(o que (e|significa|quer dizer)|qual a diferenca|como funciona)\b")

_ADVOGADO = re.compile(
    r"\b(oab|advogad[oa]|doutor|dr\.?|dra\.?|patrono|causidico|procurador|peticionar|protocolar peti|agravo|embargos|"
    r"prequestionamento|art\.|cpc|cpp|inc\.|§|jurisprudencia|tese|sustentacao oral|minha parte|meu cliente|cliente)\b"
)
_CIDADAO = re.compile(
    r"\b(sou (o |a )?(autor|autora|reu|parte)|meu processo|minha acao|nao entendo|nao sei|me explica|explica pra mim|"
    r"em palavras simples|pode explicar|ajuda|moro|minha familia|fui (citado|intimado)|recebi (uma )?(intimacao|carta))\b"
)


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    reasons: tuple[str, ...]


def classify_intent(message: str) -> IntentResult:
    t = _fold(message)
    if _HUMAN.search(t):
        return IntentResult(Intent.ATENDIMENTO_HUMANO, 0.95, ("pedido_de_atendimento_humano",))
    if _GREETING.match(t):
        return IntentResult(Intent.SAUDACAO, 0.95, ("saudacao",))
    numbers = find_process_numbers(message)
    scores = {
        Intent.CONSULTA_PROCESSUAL: 2.0 * bool(numbers) + len(_PROCESSUAL.findall(t)),
        Intent.DUVIDA_INSTITUCIONAL: 1.5 * len(_INSTITUCIONAL.findall(t)),
        Intent.DUVIDA_JURIDICA: 1.2 * len(_JURIDICA.findall(t)),
    }
    if _GENERIC_QUESTION.match(t) and not numbers:
        scores[Intent.DUVIDA_JURIDICA] += 2.0  # "o que é uma certidão?" é conceito, não consulta
        scores[Intent.CONSULTA_PROCESSUAL] -= 1.0
    best = max(scores, key=lambda i: scores[i])
    if scores[best] <= 0:
        return IntentResult(Intent.FORA_DE_ESCOPO, 0.4, ("sem_sinais_de_dominio",))
    return IntentResult(best, min(0.95, 0.5 + 0.15 * scores[best]), tuple(f"{i.value}={s:g}" for i, s in scores.items() if s > 0))


def detect_profile(message: str, previous: Profile = Profile.INDEFINIDO) -> Profile:
    t = _fold(message)
    adv, cit = len(_ADVOGADO.findall(t)), len(_CIDADAO.findall(t))
    if adv > cit and adv >= 1:
        return Profile.ADVOGADO
    if cit > adv:
        return Profile.CIDADAO
    return previous
