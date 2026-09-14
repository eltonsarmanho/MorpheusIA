import logging

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app.api import chat, leads
from app.api.rate_limit import RateLimiter
from app.core.config import Settings
from app.domain.conversation import ConversationService
from app.llm.client import FakeLLMClient, LLMResult
from app.main import create_app
from app.storage.models import init_db
from app.storage.repository import LeadRepository, MessageRepository

ALLOWED_ORIGIN = "https://morpheusia.com"
DISALLOWED_ORIGIN = "https://nao-autorizado.example"
ADMIN_TOKEN = "token-de-teste"
WHATSAPP_NUMBER = "5591988887777"


@pytest.fixture()
def engine(tmp_path):
    """A fresh temp SQLite file per test - never the dev DB at backend/data/app.db."""
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    init_db(target_engine=test_engine)
    return test_engine


def build_test_settings() -> Settings:
    """Settings built entirely from literals - the project .env is never read."""
    return Settings(
        _env_file=None,
        MARITALK_API_KEY="chave-de-teste",
        MARITALK_API_BASE="https://exemplo.invalido",
        MARITALK_MODEL="modelo-de-teste",
        ADMIN_API_TOKEN=ADMIN_TOKEN,
        WHATSAPP_NUMBER=WHATSAPP_NUMBER,
        ALLOWED_ORIGINS=[ALLOWED_ORIGIN],
    )


def build_client(engine, llm=None):
    """A TestClient over the real wired app, with every provider overridden.

    Used without a context manager on purpose, so the startup hook (which
    would create backend/data/app.db) never runs during tests.
    """
    app = create_app(settings=build_test_settings())

    llm = llm or FakeLLMClient(result=LLMResult(text="Olá! Como posso ajudar?"))
    message_repo = MessageRepository(engine=engine)
    lead_repo = LeadRepository(engine=engine)
    service = ConversationService(
        llm=llm,
        lead_repo=lead_repo,
        message_repo=message_repo,
        whatsapp_number=WHATSAPP_NUMBER,
    )

    limiter = RateLimiter(per_minute=15, per_session=60)

    app.dependency_overrides[chat.get_conversation_service] = lambda: service
    app.dependency_overrides[chat.get_rate_limiter] = lambda: limiter
    app.dependency_overrides[chat.get_message_repository] = lambda: message_repo
    app.dependency_overrides[leads.get_lead_repository] = lambda: lead_repo
    app.dependency_overrides[leads.get_admin_token] = lambda: ADMIN_TOKEN
    return TestClient(app)


def test_all_three_routes_are_reachable_through_the_wired_app(engine):
    client = build_client(engine)

    chat_response = client.post(
        "/api/chat/message", json={"session_id": "s1", "message": "oi"}
    )
    history_response = client.get("/api/chat/s1/history")
    leads_response = client.get(
        "/api/leads", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )

    assert chat_response.status_code == 200
    assert chat_response.json()["reply"] == "Olá! Como posso ajudar?"
    assert history_response.status_code == 200
    assert [m["content"] for m in history_response.json()["messages"]] == [
        "oi",
        "Olá! Como posso ajudar?",
    ]
    assert leads_response.status_code == 200
    assert leads_response.json() == {"leads": []}


def test_allowed_origin_receives_cors_allow_header(engine):
    client = build_client(engine)

    response = client.get(
        "/api/chat/s1/history", headers={"Origin": ALLOWED_ORIGIN}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_disallowed_origin_receives_no_cors_allow_header(engine):
    client = build_client(engine)

    response = client.get(
        "/api/chat/s1/history", headers={"Origin": DISALLOWED_ORIGIN}
    )

    assert "access-control-allow-origin" not in response.headers


def test_wired_app_logs_validation_errors_with_the_session_id(engine, caplog):
    client = build_client(engine)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": ""}
        )

    assert response.status_code == 422
    assert any(
        "outcome=validation-error" in record.getMessage()
        and "session_id=s1" in record.getMessage()
        for record in caplog.records
    )
