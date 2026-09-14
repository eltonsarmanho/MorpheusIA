from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlmodel import Field, Session, SQLModel, create_engine

# backend/data/app.db (this file lives at backend/app/storage/models.py)
_DB_DIR = Path(__file__).resolve().parents[2] / "data"
_DB_PATH = _DB_DIR / "app.db"

DATABASE_URL = f"sqlite:///{_DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class Lead(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(unique=True, index=True)
    category: str  # gestao_empresas | whatsapp_atendimento | analise_documentos | gerador_conteudo | outro
    need_summary: str
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    has_contact: bool = False
    created_at: datetime
    updated_at: datetime


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime


def init_db(target_engine=engine) -> None:
    """Create the SQLite file/tables if they don't exist yet."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(target_engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
