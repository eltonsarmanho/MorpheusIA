"""Hybrid RAG: BM25 (FTS5) + busca vetorial + Reciprocal Rank Fusion + reranking opcional (RAG-01 a RAG-09)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Sequence

from app.domain.models import Evidence, KnowledgeDomain
from app.domain.policies import AbstentionPolicy, find_process_numbers
from app.domain.ports import Embedder, KnowledgeStore, Reranker
from app.infrastructure.sqlite.knowledge_store import fold, query_terms

log = logging.getLogger(__name__)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = 60) -> dict[int, float]:
    """RRF: score(d) = soma de 1/(k + posição). Robusto à diferença de escala entre BM25 e cosseno."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos)
    return scores


@dataclass
class RetrievalResult:
    evidences: list[Evidence]
    process_number: str | None = None
    unknown_process_numbers: list[str] = field(default_factory=list)
    term_coverage: float = 0.0
    abstain_reason: str | None = None
    stages: dict[str, object] = field(default_factory=dict)

    @property
    def sufficient(self) -> bool:
        return self.abstain_reason is None


_COVER_TERMS = frozenset(
    "valor causa assunto assuntos classe vara orgao julgador partes parte autor autora reu requerente requerido advogado advogados "
    "distribuicao distribuido distribuida segredo gratuita gratuidade cronologia documentos movimentacoes andamento tribunal".split()
)


def asks_about_cover(query: str) -> bool:
    """A pergunta é sobre dados da capa do processo (classe, vara, valor, partes, cronologia)?"""
    return len(_COVER_TERMS & set(query_terms(query))) >= 1


def term_coverage(query: str, evidences: Sequence[Evidence]) -> float:
    """Fração dos termos da pergunta que aparece em algum trecho recuperado (critério de suficiência)."""
    terms = set(query_terms(query))
    terms = {t for t in terms if not t.isdigit()} or terms
    if not terms:
        return 0.0
    haystack = fold(" ".join(e.text + " " + (e.citation.get("titulo") or "") for e in evidences))
    found = sum(1 for t in terms if re.search(rf"\b{re.escape(t)}", haystack))
    return found / len(terms)


class HybridRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder,
        *,
        reranker: Reranker | None = None,
        policy: AbstentionPolicy | None = None,
        candidates: int = 40,
        rrf_k: int = 60,
        known_processes: Sequence[str] = (),
    ) -> None:
        self.store, self.embedder, self.reranker = store, embedder, reranker
        self.policy = policy or AbstentionPolicy()
        self.candidates, self.rrf_k = candidates, rrf_k
        self._known = set(known_processes)

    def _known_processes(self) -> set[str]:
        fn = getattr(self.store, "known_process_numbers", None)
        return set(fn()) if fn else self._known

    def retrieve(self, query: str, domain: KnowledgeDomain, *, process_number: str | None = None) -> RetrievalResult:
        stages: dict[str, object] = {"domain": domain.value}
        numbers = find_process_numbers(query)
        unknown: list[str] = []
        if domain is KnowledgeDomain.PROCESSUAL and numbers:
            known = self._known_processes()
            matches = [n for n in numbers if n in known]
            unknown = [n for n in numbers if n not in known]
            if matches:
                process_number = matches[0]
            elif unknown:
                # RAG-04: número citado e ausente do acervo — não responder com outro processo
                return RetrievalResult([], None, unknown, 0.0, "processo_ausente_do_acervo", stages)

        lex = self.store.search_lexical(query, domain, self.candidates, process_number)
        qvec = self.embedder.embed_query(query)
        vec = self.store.search_vector(qvec, domain, self.candidates, process_number)
        stages.update(lexical_hits=len(lex), vector_hits=len(vec))
        if not lex and not vec:
            return RetrievalResult([], process_number, unknown, 0.0, "sem_resultados", stages)

        fused = reciprocal_rank_fusion([[c for c, _ in lex], [c for c, _ in vec]], self.rrf_k)
        lex_rank = {c: i for i, (c, _) in enumerate(lex, 1)}
        vec_rank = {c: i for i, (c, _) in enumerate(vec, 1)}
        lex_score, vec_score = dict(lex), dict(vec)
        ordered = sorted(fused, key=lambda c: -fused[c])[: max(self.policy.top_k * 3, 12)]
        loaded = {e.chunk_id: e for e in self.store.load_evidences(ordered, domain)}  # filtro de acesso reaplicado
        evidences = [
            replace(
                loaded[c], score=fused[c], lexical_rank=lex_rank.get(c), vector_rank=vec_rank.get(c),
                lexical_score=lex_score.get(c), vector_score=vec_score.get(c),
            )
            for c in ordered if c in loaded
        ]
        stages["fused"] = len(evidences)
        if domain is KnowledgeDomain.PROCESSUAL and process_number and asks_about_cover(query):
            cover_fn = getattr(self.store, "evidences_for_doc", None)
            if cover_fn:  # regra transparente: perguntas sobre a capa trazem a ficha do processo primeiro
                covers = [replace(e, score=1.0) for e in cover_fn(f"proc-{process_number}:capa", 2)]
                have = {e.chunk_id for e in covers}
                evidences = covers + [e for e in evidences if e.chunk_id not in have]
                stages["cover_boost"] = len(covers)
        if self.reranker and evidences:
            try:
                evidences = self.reranker.rerank(query, evidences)
                stages["reranked"] = True
            except Exception:  # noqa: BLE001 - reranker é opcional; segue com a fusão
                log.exception("reranker falhou; mantendo ordem da fusão")
                stages["reranked"] = False
        evidences = evidences[: self.policy.top_k]
        coverage = term_coverage(query, evidences)
        reason = self._abstention(evidences, coverage)
        stages["coverage"] = round(coverage, 3)
        return RetrievalResult(evidences, process_number, unknown, coverage, reason, stages)

    def _abstention(self, evidences: list[Evidence], coverage: float) -> str | None:
        p = self.policy
        if len(evidences) < p.min_evidences:
            return "evidencias_insuficientes"
        if not any(e.lexical_rank is not None for e in evidences):
            return "sem_correspondencia_lexical"
        if coverage < p.min_term_coverage:
            return "baixa_cobertura_dos_termos_da_pergunta"
        return None
