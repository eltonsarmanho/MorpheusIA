"""Curadoria: aprovar, rejeitar e reindexar documentos com revisor e motivo registrados (CUR-03)."""

from __future__ import annotations

from app.domain.models import ReviewState
from app.domain.ports import Embedder
from app.infrastructure.sqlite.knowledge_store import SqliteKnowledgeStore

DECISIONS = (ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.PENDING_REVIEW)


class CurationService:
    def __init__(self, store: SqliteKnowledgeStore, embedder: Embedder) -> None:
        self.store, self.embedder = store, embedder

    def review(self, doc_id: str, decision: ReviewState, *, reviewer: str, reason: str) -> int:
        """Registra a decisão. Aprovação indexa os trechos; qualquer outra decisão retira o documento das buscas."""
        if decision not in DECISIONS:
            raise ValueError(f"decisão inválida: {decision.value}")
        if not reviewer.strip() or not reason.strip():
            raise ValueError("revisor e motivo são obrigatórios")
        self.store.set_review(doc_id, decision, reviewer=reviewer.strip(), reason=reason.strip())
        return self.store.index_pending(self.embedder, doc_id=doc_id) if decision is ReviewState.APPROVED else 0

    def review_many(self, doc_ids: list[str], decision: ReviewState, *, reviewer: str, reason: str) -> int:
        return sum(self.review(d, decision, reviewer=reviewer, reason=reason) for d in doc_ids)
