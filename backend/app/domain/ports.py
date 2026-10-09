"""Portas (interfaces) que a camada de aplicação exige da infraestrutura."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np

from app.domain.models import ChunkRecord, DocumentRecord, Evidence, KnowledgeDomain, Option, ReviewState


@dataclass(frozen=True)
class OcrResult:
    text: str
    mean_confidence: float  # 0-100


class PdfTextExtractor(Protocol):
    def extract_pages(self, path: Path) -> list[str]: ...


class OcrEngine(Protocol):
    def ocr_page(self, path: Path, page: int) -> OcrResult: ...


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class Reranker(Protocol):
    def rerank(self, query: str, evidences: list[Evidence]) -> list[Evidence]: ...


class KnowledgeStore(Protocol):
    """Armazenamento de documentos, trechos, índices lexical e vetorial."""

    def get_process_sha(self, process_key: str) -> str | None: ...

    def replace_process(self, process: dict[str, Any], pages: list[dict[str, Any]]) -> None: ...

    def replace_documents(self, process_key: str, docs: list[DocumentRecord], chunks: list[ChunkRecord]) -> None: ...

    def lookup_decision(self, doc_id: str, content_hash: str) -> dict[str, Any] | None: ...

    def index_document(self, doc_id: str, embedder: Embedder) -> int: ...

    def deindex_document(self, doc_id: str) -> None: ...

    def set_review(self, doc_id: str, state: ReviewState, *, reviewer: str, reason: str, access_class: str | None = None) -> None: ...

    def get_document(self, doc_id: str) -> DocumentRecord | None: ...

    def list_documents(self, *, domain: KnowledgeDomain | None = None, state: ReviewState | None = None, limit: int = 200, offset: int = 0) -> list[DocumentRecord]: ...

    def search_lexical(self, query: str, domain: KnowledgeDomain, limit: int, process_number: str | None = None) -> list[tuple[int, float]]: ...

    def search_vector(self, vector: np.ndarray, domain: KnowledgeDomain, limit: int, process_number: str | None = None) -> list[tuple[int, float]]: ...

    def load_evidences(self, chunk_ids: Sequence[int], domain: KnowledgeDomain) -> list[Evidence]: ...


class LlmGenerator(Protocol):
    """Gera a resposta a partir de instruções e evidências. Implementado com Agno."""

    def generate(self, *, system: str, user: str) -> str: ...


class ChatwootGateway(Protocol):
    def send_message(
        self, account_id: int, conversation_id: int, content: str, options: Sequence[Option] | None = None, private: bool = False
    ) -> int | None: ...

    def add_labels(self, account_id: int, conversation_id: int, labels: Sequence[str]) -> None: ...

    def assign_team(self, account_id: int, conversation_id: int, team_name: str) -> bool: ...

    def set_status(self, account_id: int, conversation_id: int, status: str) -> bool: ...


class ProcessLookupPort(Protocol):
    """Evolução futura: consulta a uma API processual OFICIAL (PJe/e-SAJ ou equivalente).

    Não há implementação no piloto e nenhuma é simulada: o sistema só responde sobre processos a partir do acervo
    de demonstração. Quando existir uma API autorizada, ela entra aqui como nova infraestrutura, sem mudar o domínio.
    """

    def get_case(self, process_number: str) -> dict[str, Any]: ...

    def list_movements(self, process_number: str, since: str | None = None) -> list[dict[str, Any]]: ...
