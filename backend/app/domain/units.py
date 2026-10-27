"""Cadastro de unidades e contatos institucionais (roteiro TJPA, seção 8). Sem I/O.

Nenhum dado é inventado: um campo só é usado quando foi cadastrado e passou na validação. Campo ausente ou inválido
vira None e o fluxo declara a falta, em vez de improvisar um contato.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

# Hosts aceitos para links institucionais (Balcão Virtual). Ampliar só com fonte oficial verificada.
OFFICIAL_HOSTS = ("tjpa.jus.br",)


def fold(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", folded)).strip()


def valid_official_url(url: str | None) -> str | None:
    """HTTPS em host oficial (ou subdomínio dele); qualquer outra coisa é descartada."""
    if not url:
        return None
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS):
        return None
    return url.strip()


def valid_br_phone(raw: str | None) -> str | None:
    """Telefone brasileiro com DDD (10 ou 11 dígitos, aceita +55). Devolve só dígitos com 55 na frente."""
    if not raw:
        return None
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11) or d[0] == "0" or d[2] == "0":
        return None
    return "55" + d


def format_phone(digits55: str) -> str:
    d = digits55[2:]
    return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"


def whatsapp_link(digits55: str, text: str) -> str:
    """Link de abertura de conversa (wa.me) com mensagem preenchida. Abrir o link não envia nada."""
    return f"https://wa.me/{digits55}?text={quote(text, safe='')}"


@dataclass(frozen=True)
class Unit:
    id: str
    name: str
    city: str
    aliases: tuple[str, ...] = ()
    address: str | None = None
    phone: str | None = None  # só dígitos, com 55
    whatsapp: str | None = None  # só dígitos, com 55; número oficial validado
    balcao_url: str | None = None  # link direto da unidade, não a página geral
    hours: str | None = None
    timezone: str | None = None
    source: str = ""
    verified_at: str = ""

    def keys(self) -> set[str]:
        return {fold(self.name), *(fold(a) for a in self.aliases)} - {""}


@dataclass(frozen=True)
class InstitutionalContacts:
    balcao_general_url: str | None = None  # página geral do Balcão Virtual (escolha de unidade)
    balcao_general_source: str = ""
    telejudiciario: str | None = None  # texto do contato oficial, só se configurado
    telejudiciario_source: str = ""


@dataclass
class UnitDirectory:
    units: list[Unit] = field(default_factory=list)
    contacts: InstitutionalContacts = field(default_factory=InstitutionalContacts)
    rejected: list[str] = field(default_factory=list)  # campos descartados na carga (para o diagnóstico de operação)

    def get(self, unit_id: str | None) -> Unit | None:
        return next((u for u in self.units if u.id == unit_id), None) if unit_id else None

    def find(self, text: str) -> list[Unit]:
        """Unidades cujo nome ou apelido aparece no texto; sem nome, as da cidade citada. Nunca escolhe por semelhança."""
        t = f" {fold(text)} "
        by_name = [u for u in self.units if any(f" {k} " in t for k in u.keys())]
        if by_name:
            return by_name
        return [u for u in self.units if fold(u.city) and f" {fold(u.city)} " in t]
