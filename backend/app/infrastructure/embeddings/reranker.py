"""Reranker cross-encoder opcional (RAG-09) via fastembed. Desligado por padrão."""

from __future__ import annotations

from dataclasses import replace

from app.domain.models import Evidence


class FastEmbedReranker:
    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)

    def rerank(self, query: str, evidences: list[Evidence]) -> list[Evidence]:
        scores = list(self._model.rerank(query, [e.text for e in evidences]))
        paired = sorted(zip(scores, evidences), key=lambda x: -x[0])
        return [replace(e, score=float(s)) for s, e in paired]
