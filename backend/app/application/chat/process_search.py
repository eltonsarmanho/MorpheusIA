"""Busca de processo por nome nas capas aprovadas do acervo de demonstração (roteiro, seção 3.3).

CPF não é pesquisável: o índice mascara CPF (SEC-01) e não há API processual. A busca por CPF declara indisponibilidade
em vez de simular resultado. A busca por nome exige nome completo (2+ palavras) e casa todas as palavras na mesma
parte, nunca por semelhança.
"""

from __future__ import annotations

from app.domain.ports import ProcessCandidate, ProcessSearchResult
from app.domain.units import fold

_PARTICLES = frozenset({"de", "da", "do", "das", "dos", "e"})


def name_tokens(name: str) -> list[str]:
    return [t for t in fold(name).split() if t not in _PARTICLES and len(t) >= 2]


class DemoCorpusProcessSearch:
    def __init__(self, store) -> None:
        self.store = store

    def by_cpf(self, cpf: str) -> ProcessSearchResult:
        return ProcessSearchResult(available=False)

    def by_name(self, name: str) -> ProcessSearchResult:
        tokens = name_tokens(name)
        if len(tokens) < 2:
            return ProcessSearchResult(available=True)
        found: list[ProcessCandidate] = []
        for row in self.store.approved_processes():
            info = self.store.process_info(row["process_number"]) or {}
            for party in info.get("parties") or []:
                party_tokens = set(name_tokens(party.get("nome", "")))
                if all(t in party_tokens for t in tokens):
                    found.append(ProcessCandidate(row["process_number"], row.get("process_class") or "não informada",
                                                  (party.get("papel") or "não informada").lower(), row.get("court_unit") or ""))
                    break
        return ProcessSearchResult(available=True, candidates=tuple(found))
