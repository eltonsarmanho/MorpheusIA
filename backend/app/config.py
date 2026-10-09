"""Configuração por variáveis de ambiente (arquivo .env na raiz do repositório). Sem segredos no código."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Caminhos
    data_dir: Path = REPO_ROOT / "data"
    processes_dir: Path = REPO_ROOT / "docs" / "processos"
    knowledge_db: Path | None = None
    operational_db: Path | None = None
    sources_file: Path = REPO_ROOT / "backend" / "config" / "sources.yaml"

    # Embeddings e reranking
    embedder_backend: str = "fastembed"  # fastembed | hashing (testes)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    reranker_model: str = ""  # vazio = sem reranker
    hf_token: str = ""

    # Autorização do acervo processual (registro da base declarada pelo responsável)
    corpus_authorized: bool = False
    corpus_authorization_basis: str = "nao_declarada"

    # LLM (MariTalk via API compatível com OpenAI, usada pelo Agno)
    maritalk_api_key: str = ""
    maritalk_api_base: str = "https://chat.maritaca.ai/api"
    maritalk_model: str = "sabiazinho-4"
    llm_timeout_s: float = 45.0

    # Chatwoot
    chatwoot_base_url: str = "https://chatwoot.srv1633081.hstgr.cloud"
    chatwoot_account_id: int = 1
    chatwoot_api_token: str = ""  # token de usuário administrador (setup de etiquetas/equipes)
    chatwoot_bot_token: str = ""  # token do Agent Bot (respostas e transferência)
    chatwoot_webhook_secret: str = ""  # segredo na URL do webhook (?token=...)
    handoff_max_attempts: int = 3
    chat_rich_flow: bool = True  # menus com botões/listas, protocolo (TKT) e encerramento guiado; false = só texto
    public_base_url: str = "https://srv1633081.hstgr.cloud/atendimento"  # exibido na tela de integração

    # Segurança da API
    admin_api_token: str = Field(default="")
    max_question_chars: int = 1000
    console_public: bool = False  # true libera /api/chat sem token (use só com limite de custo)

    # Ingestão
    chunk_max_chars: int = 900
    chunk_overlap_chars: int = 100

    # Parâmetros de recuperação
    retrieval_top_k: int = 6
    retrieval_candidates: int = 40
    rrf_k: int = 60
    min_term_coverage: float = 0.34
    min_vector_score: float = 0.45
    stale_after_days: int = 180

    def resolved_knowledge_db(self) -> Path:
        return self.knowledge_db or self.data_dir / "index" / "knowledge.db"

    def resolved_operational_db(self) -> Path:
        return self.operational_db or self.data_dir / "index" / "operational.db"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.hf_token and not os.environ.get("HF_TOKEN"):
        os.environ["HF_TOKEN"] = s.hf_token  # lido pelo huggingface_hub; nunca registrado em log
    return s
