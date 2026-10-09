"""Composição das dependências (único lugar que conhece as implementações concretas)."""

from __future__ import annotations

from app.application.ingestion.service import IngestionConfig, IngestionService
from app.config import Settings
from app.domain.ports import Embedder
from app.infrastructure.embeddings.embedders import FastEmbedEmbedder, HashingEmbedder
from app.infrastructure.pdf.poppler import PopplerTextExtractor, pdf_page_count
from app.infrastructure.pdf.tesseract import TesseractOcr
from app.infrastructure.sqlite.knowledge_store import SqliteKnowledgeStore


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedder_backend == "hashing":
        return HashingEmbedder()
    return FastEmbedEmbedder(settings.embedding_model, cache_dir=str(settings.data_dir / "models"))


def build_store(settings: Settings) -> SqliteKnowledgeStore:
    return SqliteKnowledgeStore(settings.resolved_knowledge_db())


def build_ingestion(settings: Settings, store: SqliteKnowledgeStore, embedder: Embedder) -> IngestionService:
    cfg = IngestionConfig(
        corpus_authorized=settings.corpus_authorized,
        authorization_basis=settings.corpus_authorization_basis,
        max_chunk_chars=settings.chunk_max_chars,
        chunk_overlap=settings.chunk_overlap_chars,
    )
    return IngestionService(store, PopplerTextExtractor(), TesseractOcr(), embedder, cfg, page_counter=pdf_page_count)


# ---------------------------------------------------------------- aplicação completa
from dataclasses import dataclass  # noqa: E402

from app.application.answering.orchestrator import Orchestrator, OrchestratorConfig  # noqa: E402
from app.application.chat.handler import ChatwootEventHandler  # noqa: E402
from app.application.retrieval.hybrid import HybridRetriever  # noqa: E402
from app.domain.policies import AbstentionPolicy  # noqa: E402
from app.domain.ports import ChatwootGateway, LlmGenerator  # noqa: E402
from app.infrastructure.sqlite.operational_store import OperationalStore  # noqa: E402


@dataclass
class Container:
    settings: Settings
    store: SqliteKnowledgeStore
    ops: OperationalStore
    embedder: Embedder
    retriever: HybridRetriever
    orchestrator: Orchestrator
    handler: ChatwootEventHandler
    gateway: ChatwootGateway | None
    llm: LlmGenerator | None


def build_llm(settings: Settings) -> LlmGenerator | None:
    if not settings.maritalk_api_key:
        return None
    from app.infrastructure.llm.agno_llm import AgnoLlmGenerator

    return AgnoLlmGenerator(
        api_key=settings.maritalk_api_key, base_url=settings.maritalk_api_base, model=settings.maritalk_model,
        timeout_s=settings.llm_timeout_s,
    )


def build_gateway(settings: Settings) -> ChatwootGateway | None:
    if not (settings.chatwoot_bot_token or settings.chatwoot_api_token):
        return None
    from app.infrastructure.chatwoot.client import ChatwootClient

    return ChatwootClient(
        settings.chatwoot_base_url, settings.chatwoot_account_id,
        bot_token=settings.chatwoot_bot_token, api_token=settings.chatwoot_api_token,
    )


def build_reranker(settings: Settings):
    if not settings.reranker_model:
        return None
    from app.infrastructure.embeddings.reranker import FastEmbedReranker

    return FastEmbedReranker(settings.reranker_model, cache_dir=str(settings.data_dir / "models"))


def build_container(
    settings: Settings, *, store: SqliteKnowledgeStore | None = None, ops: OperationalStore | None = None,
    embedder: Embedder | None = None, llm: LlmGenerator | None = None, gateway: ChatwootGateway | None = None,
) -> Container:
    store = store or build_store(settings)
    ops = ops or OperationalStore(settings.resolved_operational_db())
    embedder = embedder or build_embedder(settings)
    policy = AbstentionPolicy(
        min_term_coverage=settings.min_term_coverage, min_vector_score=settings.min_vector_score, top_k=settings.retrieval_top_k
    )
    retriever = HybridRetriever(
        store, embedder, reranker=build_reranker(settings), policy=policy,
        candidates=settings.retrieval_candidates, rrf_k=settings.rrf_k,
    )
    llm = llm if llm is not None else build_llm(settings)
    orchestrator = Orchestrator(retriever, store, llm, OrchestratorConfig(settings.max_question_chars, policy.max_context_chars))
    gateway = gateway if gateway is not None else build_gateway(settings)
    handler = ChatwootEventHandler(
        orchestrator, ops, gateway, max_handoff_attempts=settings.handoff_max_attempts, max_question_chars=settings.max_question_chars,
        default_account_id=settings.chatwoot_account_id,
        rich_flow=settings.chat_rich_flow,
    ) if gateway is not None else None
    return Container(settings, store, ops, embedder, retriever, orchestrator, handler, gateway, llm)  # type: ignore[arg-type]


def build_collection(settings: Settings, store: SqliteKnowledgeStore, embedder: Embedder | None = None):
    from app.application.collection.service import CollectionService
    from app.application.collection.sources import SourceRegistry
    from app.infrastructure.web.fetcher import HttpFetcher

    registry = SourceRegistry.from_file(settings.sources_file)
    return CollectionService(store, registry, HttpFetcher(registry.settings), embedder)
