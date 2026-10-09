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
    "valor causa assunto assuntos classe vara orgao julgador partes distribuicao distribuido distribuida segredo gratuidade "
    "cronologia documentos movimentacoes andamento autor autora autores re reu reus requerente requerido advogado advogados".split()
)
_TIMELINE_TERMS = frozenset("cronologia documentos movimentacoes andamento".split())
# Se a pergunta já aponta um tipo de documento, a recuperação normal é mais precisa que a ficha da capa.
_DOC_TYPE_TERMS = frozenset(
    "decisao decisoes despacho despachos sentenca acordao voto ementa peticao certidao mandado intimacao contestacao replica recurso "
    "apelacao audiencia denuncia procuracao".split()
)


def cover_boost_size(query: str) -> int:
    """Quantos trechos da capa trazer: 0 (não é pergunta de capa), 1 (ficha) ou 3 (ficha + cronologia)."""
    terms = set(re.findall(r"[a-z0-9]+", fold(query)))  # sem filtro de tamanho: "ré" vira "re"
    if not (_COVER_TERMS & terms) or (_DOC_TYPE_TERMS & terms):
        return 0
    return 3 if _TIMELINE_TERMS & terms else 1


_PREAMBLE = re.compile(r"(?i)^\s*(sou|eu sou|meu nome|ol[aá]|oi|bom dia|boa tarde|boa noite|prezad[oa]s?)\b")
_OAB = re.compile(r"(?i)\boab\s*/?\s*[a-z]{0,2}\s*[\d.\-]+")


def content_query(query: str) -> str:
    """Remove apresentação pessoal e número de OAB, que não precisam existir nas evidências para a resposta ser suficiente."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", _OAB.sub(" ", query)) if p.strip()]
    kept = [p for p in parts if not _PREAMBLE.match(p)]
    return " ".join(kept or parts)


_DATE_BR = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")


# Tipo documental citado na pergunta -> trecho do campo doc_type (minúsculo) usado no filtro
_DOC_TYPE_HINTS = (
    ("sentenca", "senten"), ("acordao", "acórd"), ("decisao", "decis"), ("despacho", "despacho"), ("denuncia", "denún"),
    ("peticao inicial", "petição inicial"), ("contestacao", "contest"), ("certidao", "certid"), ("apelacao", "apela"),
    ("mandado", "mandado"), ("audiencia", "audiência"), ("ementa", "ementa"), ("voto", "voto"),
)


# Pergunta sobre o desfecho -> buscar o DISPOSITIVO (parte final da decisão/sentença, onde o juiz decide)
_OUTCOME = re.compile(r"\b(como (terminou|acabou|foi decidid)|resultado|desfecho|decidiu|decidido|julgou|julgad[oa]|dispositivo|procedente|improcedente|"
                      r"condenou|condenad[oa]|absolvid[oa]|extint[oa]|extinguiu|deferi|indeferi|provimento)")
_DISPOSITIVE_TERMS = " diante do exposto ante o exposto isto posto isso posto pelo exposto julgo dispositivo extingo condeno absolvo defiro indefiro"


def asks_outcome(query: str) -> bool:
    return bool(_OUTCOME.search(fold(query)))


def find_doc_type(query: str) -> str | None:
    q = fold(query)
    for word, like in _DOC_TYPE_HINTS:
        if re.search(rf"\b{word}", q):
            return like
    return None


_MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
           "outubro": 10, "novembro": 11, "dezembro": 12}
_MONTH_YEAR = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+de\s+(\d{4})\b")
_MM_YYYY = re.compile(r"(?<![\d/])(\d{1,2})/(\d{4})(?!\d)")


def find_query_date(query: str) -> str | None:
    """Data citada na pergunta: dia exato ("14/05/2026") ou mês ("abril de 2026", "04/2026") como prefixo AAAA-MM."""
    m = _DATE_BR.search(query)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    q = fold(query)
    m = _MONTH_YEAR.search(q)
    if m:
        return f"{m.group(2)}-{_MONTHS[m.group(1)]:02d}"
    m = _MM_YYYY.search(query)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    return None


def _stem(term: str) -> str:
    t = term[:-1] if term.endswith("s") and len(term) > 3 else term
    return t if len(t) <= 5 else t[: len(t) - 2]


def term_coverage(query: str, evidences: Sequence[Evidence]) -> float:
    """Fração dos termos da pergunta que aparece em algum trecho recuperado (critério de suficiência)."""
    terms = set(query_terms(content_query(query)))
    terms = {t for t in terms if not t.isdigit()} or terms
    if not terms:
        return 0.0
    haystack = fold(" ".join(e.text + " " + (e.citation.get("titulo") or "") for e in evidences))
    found = sum(1 for t in terms if re.search(rf"\b{re.escape(_stem(t))}", haystack))
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
        if domain is not KnowledgeDomain.PROCESSUAL:
            process_number = None  # número de processo só filtra o acervo processual
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

        qvec = self.embedder.embed_query(query)
        doc_date = find_query_date(query) if domain is KnowledgeDomain.PROCESSUAL else None
        lex = vec = []
        if doc_date:  # filtro por data do documento; se nenhum documento tem essa data, volta à busca sem filtro
            lex = self.store.search_lexical(query, domain, self.candidates, process_number, doc_date)
            vec = self.store.search_vector(qvec, domain, self.candidates, process_number, doc_date)
            stages["date_filter"] = doc_date if (lex or vec) else None
        if not (lex or vec):
            lex = self.store.search_lexical(query, domain, self.candidates, process_number)
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
        doc_type = find_doc_type(query) if domain is KnowledgeDomain.PROCESSUAL and process_number else None
        if doc_type:
            # recuperação ciente de metadados: a pergunta cita um tipo de documento ("na sentença"), então os melhores trechos
            # desse tipo no processo entram primeiro, sem descartar os demais
            active_date = stages.get("date_filter") or None  # respeita o filtro de data quando ele se aplicou
            t_query = query + _DISPOSITIVE_TERMS if asks_outcome(query) else query  # desfecho: o dispositivo entra na busca do tipo
            t_lex = self.store.search_lexical(t_query, domain, self.candidates, process_number, active_date, doc_type)
            t_vec = self.store.search_vector(qvec, domain, self.candidates, process_number, active_date, doc_type)
            t_fused = reciprocal_rank_fusion([[c for c, _ in t_lex], [c for c, _ in t_vec]], self.rrf_k)
            # os 2 melhores da fusão + o 1º lexical e o 1º vetorial do tipo: o cabeçalho do documento (forte no BM25,
            # fraco no vetor) costuma trazer o dispositivo, a tipificação ou o resultado
            ranked = sorted(t_fused, key=lambda c: -t_fused[c])
            pool = {e.chunk_id: e for e in self.store.load_evidences(list(dict.fromkeys(ranked[:12] + [c for c, _ in t_lex[:1]] + [c for c, _ in t_vec[:1]])), domain)}
            # diversidade: o melhor trecho de cada documento do tipo (há processos com mais de uma sentença), mais o 1º lexical e o 1º vetorial
            per_doc: list[int] = []
            seen_docs: set[str] = set()
            for c in ranked:
                if c in pool and pool[c].doc_id not in seen_docs:
                    per_doc.append(c)
                    seen_docs.add(pool[c].doc_id)
                if len(per_doc) == 2:
                    break
            t_ids = list(dict.fromkeys(per_doc + [c for c, _ in t_lex[:1]] + [c for c, _ in t_vec[:1]]))[:4]
            typed = {c: pool[c] for c in t_ids if c in pool}
            lex_rank.update({c: lex_rank.get(c) or i for i, (c, _) in enumerate(t_lex, 1)})
            first = [replace(typed[c], score=max(fused.get(c, 0.0), t_fused[c]) + 1.0, lexical_rank=lex_rank.get(c), vector_rank=vec_rank.get(c),
                             lexical_score=dict(t_lex).get(c), vector_score=dict(t_vec).get(c)) for c in t_ids if c in typed]
            have = {e.chunk_id for e in first}
            evidences = first + [e for e in evidences if e.chunk_id not in have]
            stages["doc_type_boost"] = f"{doc_type}:{len(first)}"
        stages["fused"] = len(evidences)
        top_vec = max([e.vector_score or 0.0 for e in evidences] or [0.0])
        stages["top_vector_score"] = round(top_vec, 3)
        boost = cover_boost_size(query) if domain is KnowledgeDomain.PROCESSUAL and process_number else 0
        if boost:
            cover_fn = getattr(self.store, "evidences_for_doc", None)
            if cover_fn:  # regra transparente: perguntas sobre a capa trazem a ficha do processo primeiro
                covers = [replace(e, score=1.0) for e in cover_fn(f"proc-{process_number}:capa", boost)]
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
        reason = self._abstention(evidences, coverage, top_vec)
        stages["coverage"] = round(coverage, 3)
        return RetrievalResult(evidences, process_number, unknown, coverage, reason, stages)

    def _abstention(self, evidences: list[Evidence], coverage: float, top_vec: float = 1.0) -> str | None:
        p = self.policy
        if len(evidences) < p.min_evidences:
            return "evidencias_insuficientes"
        if top_vec < p.min_vector_score:
            return "baixa_similaridade_semantica"
        if not any(e.lexical_rank is not None for e in evidences):
            return "sem_correspondencia_lexical"
        if coverage < p.min_term_coverage:
            return "baixa_cobertura_dos_termos_da_pergunta"
        return None
