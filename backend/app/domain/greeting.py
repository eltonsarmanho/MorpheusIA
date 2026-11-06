"""Detecção da intenção de iniciar o atendimento (saudação ou pedido de menu). Regra pura, sem I/O.

Vale como saudação a mensagem em que TODAS as palavras são saudação, tratamento pela persona ou
fórmula de cortesia ("Olá Julia!!", "oi, tudo bem?", "boa tarde, EscutAI"). Se sobrar qualquer
palavra de conteúdo ("olá, quero saber do meu processo"), não é saudação: o texto segue como pergunta.
"""
from __future__ import annotations

import re
import unicodedata

_OPENERS = (
    r"o+i+|o+l+a+|oie|opa|eae|eai|e ai|hey|hello|hi|salve|alo|"
    r"bom ?dia|boa ?tarde|boa ?noite|menu|inicio|iniciar|comecar|ajuda|opcoes"
)
_PERSONA = r"julia|escutai|escuta ai|tjpa|assistente|bot|robo"
_COURTESY = (
    r"tudo bem|tudo bom|tudo certo|td bem|tudo|como vai|como voce esta|como vc esta|como esta|"
    r"voce|vc|por favor|pf|gente|pessoal|meu|minha|querida|amiga|bem|bom"
)
_OPENER_RE = re.compile(rf"\b({_OPENERS})\b")
_ALLOWED_RE = re.compile(rf"\b({_OPENERS}|{_PERSONA}|{_COURTESY})\b")
_MAX_WORDS = 8


def _norm(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", folded)).strip()


def is_greeting(text: str) -> bool:
    """A mensagem é só saudação/abertura de conversa, sem conteúdo de pergunta."""
    t = _norm(text)
    if not t or len(t.split()) > _MAX_WORDS or not _OPENER_RE.search(t):
        return False
    return _ALLOWED_RE.sub(" ", t).strip() == ""
