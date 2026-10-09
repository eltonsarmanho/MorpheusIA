"""Coletores institucional e jurídico-informacional e rotinas de curadoria (COL-01 a COL-09).

Nada coletado fica consultável sem aprovação humana: o estado inicial é sempre `pending_review`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.application.collection.extractors import ExtractedPage, extract_html, split_articles
from app.application.collection.sources import CollectionError, Seed, SourceRegistry
from app.application.ingestion.text_processing import chunk_text, content_hash
from app.domain.models import (
    DESCONHECIDO,
    AccessClass,
    ChunkRecord,
    DocumentRecord,
    KnowledgeDomain,
    ReviewState,
)
from app.domain.ports import Embedder
from app.infrastructure.sqlite.knowledge_store import SqliteKnowledgeStore
from app.infrastructure.web.fetcher import FetchResult

log = logging.getLogger(__name__)

_PREFIX = {KnowledgeDomain.INSTITUCIONAL: "inst", KnowledgeDomain.JURIDICO: "jur"}


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CollectionService:
    def __init__(self, store: SqliteKnowledgeStore, registry: SourceRegistry, fetcher: Fetcher, embedder: Embedder | None = None) -> None:
        self.store, self.registry, self.fetcher, self.embedder = store, registry, fetcher, embedder

    # ------------------------------------------------------------------ coleta
    def collect_url(self, domain: KnowledgeDomain, url: str, *, topic: str = "", norm: str = "") -> dict:
        if domain is KnowledgeDomain.PROCESSUAL:
            raise CollectionError("o domínio processual não é alimentado por coleta web")
        try:
            source = self.registry.resolve(url, domain)
        except CollectionError as exc:
            self.store.log_collection(url, domain.value, "recusada", str(exc))  # COL-01
            raise
        if not topic:
            seed = next((s for src in self.registry.sources for s in src.seeds if s.url == url), None)
            topic, norm = (seed.topic, seed.norm) if seed else ("", norm)
        try:
            res = self.fetcher.fetch(url)
        except CollectionError as exc:
            self.store.log_collection(url, domain.value, "bloqueada", str(exc))
            raise
        if res.status != 200 or "html" not in res.content_type.lower():
            msg = f"HTTP {res.status} / {res.content_type or 'sem content-type'}"
            self.store.log_collection(url, domain.value, "erro", msg)
            raise CollectionError(f"conteúdo não coletável: {msg}")

        page = extract_html(res.body)
        collected_at = _now()
        doc_id = f"{_PREFIX[domain]}:{hashlib.sha1(url.encode()).hexdigest()[:12]}"
        c_hash = content_hash(page.text)
        doc, chunks = self._build(doc_id, domain, source.issuing_body, url, page, c_hash, collected_at, topic, norm, source.id)
        self.store.upsert_document(doc, chunks)

        result = {"doc_id": doc_id, "url": url, "state": doc.review_state.value, "chunks": len(chunks), "reason": doc.review_reason,
                  "validity_flag": page.validity_flag, "quality": page.quality}
        self.store.log_collection(url, domain.value, "coletada", f"{doc.review_state.value}: {doc.review_reason}")
        if doc.review_state is ReviewState.APPROVED and self.embedder is not None:
            result["indexed"] = self.store.index_pending(self.embedder, doc_id=doc_id)
        return result

    def collect_all(self) -> list[dict]:
        out = []
        for src in self.registry.sources:
            for seed in src.seeds:
                try:
                    out.append(self.collect_url(src.domain, seed.url, topic=seed.topic, norm=seed.norm))
                except CollectionError as exc:
                    out.append({"url": seed.url, "error": str(exc)})
        return out

    def _build(
        self, doc_id: str, domain: KnowledgeDomain, issuing_body: str, url: str, page: ExtractedPage, c_hash: str,
        collected_at: datetime, topic: str, norm: str, source_id: str,
    ) -> tuple[DocumentRecord, list[ChunkRecord]]:
        state, reason = ReviewState.PENDING_REVIEW, "coletado; aguardando revisão humana"
        if page.quality.get("low_text"):
            state, reason = ReviewState.NEEDS_REVIEW, "extração com pouco texto; verificar a página de origem"
        if page.validity_flag == "revogado":
            state, reason = ReviewState.NEEDS_REVIEW, "a página indica norma revogada; validar vigência"
        decision = self.store.lookup_decision(doc_id, c_hash)
        previous = self.store.get_document(doc_id)
        if previous is not None and previous.review_state is ReviewState.STALE:
            decision = None  # COL-05: informação desatualizada só volta com nova revisão humana, mesmo com conteúdo idêntico
            reason = "recoletado depois de ficar desatualizado; exige nova revisão humana"
        if decision and decision["decision"] in (ReviewState.APPROVED.value, ReviewState.REJECTED.value):
            # recoleta com conteúdo idêntico renova a validade da decisão humana anterior
            state = ReviewState(decision["decision"])
            reason = f"decisão humana preservada ({decision['reviewer']}): {decision['reason']}; conteúdo idêntico em nova coleta"
        dup = self.store.find_by_hash(c_hash, doc_id)
        if dup is not None and state is not ReviewState.APPROVED:
            state, reason = ReviewState.REJECTED, f"duplicata de {dup.doc_id}"  # COL-04
        doc = DocumentRecord(
            doc_id=doc_id, domain=domain, title=page.title, doc_type="norma" if domain is KnowledgeDomain.JURIDICO else "pagina_institucional",
            source_url=url, issuing_body=issuing_body, published_at=page.published_at, doc_date=page.published_at,
            doc_date_source="pagina_de_origem" if page.published_at != DESCONHECIDO else DESCONHECIDO,
            access_class=AccessClass.PUBLIC, review_state=state, review_reason=reason, content_hash=c_hash,
            collected_at=collected_at.isoformat(timespec="seconds"), indexed_at=collected_at.isoformat(timespec="seconds"),
            extra={"topic": topic, "source_id": source_id, "norm_reference": norm, "validity_flag": page.validity_flag,
                   "quality": page.quality, "original_text_preserved": True, "summary": None,
                   "duplicate_of": dup.doc_id if dup else None},
        )
        ctx_base = " | ".join(x for x in (norm or page.title, issuing_body) if x and x != DESCONHECIDO)
        chunks: list[ChunkRecord] = []
        seq = 0
        parts = split_articles(page.text) if domain is KnowledgeDomain.JURIDICO else [("", page.text)]
        for label, text in parts:
            ctx = f"{ctx_base} | {label}" if label else ctx_base
            for piece in chunk_text(text):
                chunks.append(ChunkRecord(doc_id, domain, seq, None, piece, ctx, content_hash(piece)))
                seq += 1
        return doc, chunks

    # ------------------------------------------------------------- curadoria
    def refresh_staleness(self, now: datetime | None = None, max_age_days: int | None = None) -> list[str]:
        """COL-05: aprovados com coleta antiga demais ou vigência vencida passam a `stale` e saem das respostas."""
        now = now or _now()
        limit = timedelta(days=max_age_days or self.registry.settings.stale_after_days)
        marked = []
        for domain in (KnowledgeDomain.INSTITUCIONAL, KnowledgeDomain.JURIDICO):
            for d in self.store.documents_by_domain_state(domain, ReviewState.APPROVED):
                try:
                    age = now - datetime.fromisoformat(d.collected_at)
                except ValueError:
                    continue
                expired = False
                if d.valid_until != DESCONHECIDO:
                    try:
                        expired = datetime.fromisoformat(d.valid_until).date() < now.date()
                    except ValueError:
                        pass
                if age > limit or expired:
                    self.store.mark_review_system(d.doc_id, ReviewState.STALE, "informação potencialmente desatualizada; recoletar e revisar")
                    marked.append(d.doc_id)
        return marked

    def detect_conflicts(self) -> list[tuple[str, str]]:
        """COL-06: mesmo tema em fontes diferentes com conteúdo distinto => ambos voltam para revisão."""
        pairs: list[tuple[str, str]] = []
        for domain in (KnowledgeDomain.INSTITUCIONAL, KnowledgeDomain.JURIDICO):
            docs = [d for d in self.store.documents_by_domain_state(domain, ReviewState.APPROVED) if d.extra.get("topic")]
            by_topic: dict[str, list[DocumentRecord]] = {}
            for d in docs:
                by_topic.setdefault(str(d.extra["topic"]), []).append(d)
            for group in by_topic.values():
                for i, a in enumerate(group):
                    for b in group[i + 1 :]:
                        if a.extra.get("source_id") != b.extra.get("source_id") and a.content_hash != b.content_hash:
                            for x, y in ((a, b), (b, a)):
                                self.store.mark_review_system(x.doc_id, ReviewState.NEEDS_REVIEW, f"divergência com {y.doc_id} (mesmo tema, fontes distintas)")
                            pairs.append((a.doc_id, b.doc_id))
        return pairs
