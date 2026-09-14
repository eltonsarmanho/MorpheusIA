import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app.api import chat
from app.api.rate_limit import RateLimiter
from app.domain.conversation import ConversationService
from app.llm.client import FakeLLMClient, LLMResult, LLMUnavailableError, ToolCall
from app.storage.models import init_db
from app.storage.repository import LeadRepository, MessageRepository

WHATSAPP_NUMBER = "5591988887777"


@pytest.fixture()
def engine(tmp_path):
    """A fresh temp SQLite file per test - never the dev DB at backend/data/app.db."""
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    init_db(target_engine=test_engine)
    return test_engine


def build_client(engine, llm, rate_limiter=None):
    """A TestClient over the chat router, wired to a FakeLLMClient.

    The real MariTalk-backed dependency is never constructed - both providers
    are replaced through FastAPI's dependency-override mechanism.
    """
    app = FastAPI()
    app.include_router(chat.router)
    app.add_exception_handler(
        RequestValidationError, chat.validation_exception_handler
    )

    message_repo = MessageRepository(engine=engine)
    service = ConversationService(
        llm=llm,
        lead_repo=LeadRepository(engine=engine),
        message_repo=message_repo,
        whatsapp_number=WHATSAPP_NUMBER,
    )
    limiter = rate_limiter or RateLimiter(per_minute=15, per_session=60)

    app.dependency_overrides[chat.get_conversation_service] = lambda: service
    app.dependency_overrides[chat.get_rate_limiter] = lambda: limiter
    app.dependency_overrides[chat.get_message_repository] = lambda: message_repo
    return TestClient(app)


def log_messages(caplog):
    return [record.getMessage() for record in caplog.records]


# --- CHAT-02 happy path + OBS-02 logging -------------------------------------


def test_post_message_returns_200_with_reply(engine, caplog):
    llm = FakeLLMClient(result=LLMResult(text="Olá! Como posso ajudar?"))
    client = build_client(engine, llm)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": "oi"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Olá! Como posso ajudar?",
        "lead_captured": False,
        "whatsapp_url": None,
    }
    assert any(
        "outcome=success" in message and "session_id=s1" in message
        for message in log_messages(caplog)
    )


# --- CHAT-08: WhatsApp handoff surfaced in the response ----------------------


def test_post_message_returns_whatsapp_url_when_lead_captured(engine):
    llm = FakeLLMClient(
        result=LLMResult(
            tool_call=ToolCall(
                name="save_lead_info",
                arguments=json.dumps(
                    {
                        "category": "whatsapp_atendimento",
                        "need_summary": "Quer automatizar o atendimento",
                    }
                ),
            )
        )
    )
    client = build_client(engine, llm)

    response = client.post(
        "/api/chat/message",
        json={"session_id": "s1", "message": "quero automatizar meu whatsapp"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["lead_captured"] is True
    assert body["whatsapp_url"].startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")
    assert body["reply"] != ""


# --- CHAT-03: empty message rejected server-side -----------------------------


def test_empty_message_returns_422_without_calling_the_service(engine, caplog):
    llm = FakeLLMClient(result=LLMResult(text="nunca deve ser usado"))
    client = build_client(engine, llm)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": ""}
        )

    assert response.status_code == 422
    assert llm.calls == []
    assert any(
        "outcome=validation-error" in message and "session_id=s1" in message
        for message in log_messages(caplog)
    )


# --- CHAT-04: 1000-char message rejected, 999 accepted -----------------------


def test_message_of_1000_chars_returns_422_without_calling_the_service(engine, caplog):
    llm = FakeLLMClient(result=LLMResult(text="nunca deve ser usado"))
    client = build_client(engine, llm)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": "a" * 1000}
        )

    assert response.status_code == 422
    assert llm.calls == []
    assert any(
        "outcome=validation-error" in message and "session_id=s1" in message
        for message in log_messages(caplog)
    )


def test_message_of_999_chars_is_accepted(engine):
    llm = FakeLLMClient(result=LLMResult(text="Recebido!"))
    client = build_client(engine, llm)

    response = client.post(
        "/api/chat/message", json={"session_id": "s1", "message": "a" * 999}
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Recebido!"


# --- CHAT-10: rate limit -----------------------------------------------------


def test_request_over_the_rate_limit_returns_429_before_calling_the_service(
    engine, caplog
):
    llm = FakeLLMClient(result=LLMResult(text="Olá!"))
    client = build_client(
        engine, llm, rate_limiter=RateLimiter(per_minute=2, per_session=60)
    )

    for _ in range(2):
        assert (
            client.post(
                "/api/chat/message", json={"session_id": "s1", "message": "oi"}
            ).status_code
            == 200
        )

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": "oi"}
        )

    assert response.status_code == 429
    assert len(llm.calls) == 2
    assert any(
        "outcome=rate-limited" in message and "session_id=s1" in message
        for message in log_messages(caplog)
    )


# --- CHAT-09: upstream failure still returns 200 with the fallback -----------


def test_llm_unavailable_returns_200_with_fallback_reply(engine, caplog):
    llm = FakeLLMClient(error=LLMUnavailableError("timeout"))
    client = build_client(engine, llm)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/chat/message", json={"session_id": "s1", "message": "oi"}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["reply"].startswith("Desculpe")
    assert body["whatsapp_url"].startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")
    assert body["lead_captured"] is False
    assert any(
        "outcome=upstream-fallback" in message and "session_id=s1" in message
        for message in log_messages(caplog)
    )


# --- OBS-01: session history restore ----------------------------------------


def test_history_for_unknown_session_returns_empty_list(engine):
    client = build_client(engine, FakeLLMClient(result=LLMResult(text="Olá!")))

    response = client.get("/api/chat/desconhecida/history")

    assert response.status_code == 200
    assert response.json() == {"messages": []}


def test_history_returns_prior_messages_oldest_first(engine):
    llm = FakeLLMClient(result=LLMResult(text="Olá! Como posso ajudar?"))
    client = build_client(engine, llm)

    client.post("/api/chat/message", json={"session_id": "s1", "message": "oi"})
    llm.result = LLMResult(text="Entendi, posso ajudar com isso.")
    client.post(
        "/api/chat/message",
        json={"session_id": "s1", "message": "quero automatizar meu atendimento"},
    )

    response = client.get("/api/chat/s1/history")

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "oi"),
        ("assistant", "Olá! Como posso ajudar?"),
        ("user", "quero automatizar meu atendimento"),
        ("assistant", "Entendi, posso ajudar com isso."),
    ]
    assert all(m["created_at"] for m in messages)


def test_history_is_scoped_to_the_requested_session(engine):
    llm = FakeLLMClient(result=LLMResult(text="Olá!"))
    client = build_client(engine, llm)

    client.post("/api/chat/message", json={"session_id": "s1", "message": "aba um"})
    client.post("/api/chat/message", json={"session_id": "s2", "message": "aba dois"})

    messages = client.get("/api/chat/s1/history").json()["messages"]

    assert [m["content"] for m in messages] == ["aba um", "Olá!"]
