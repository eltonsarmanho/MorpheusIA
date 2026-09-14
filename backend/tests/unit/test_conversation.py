import json
import logging
from urllib.parse import quote

import pytest
from sqlmodel import create_engine

from app.domain.conversation import ConversationService, _parse_lead_arguments
from app.llm.client import FakeLLMClient, LLMResult, LLMUnavailableError, ToolCall
from app.llm.tools import SAVE_LEAD_INFO_TOOL, build_system_prompt
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


def build_service(engine, llm, lead_repo=None):
    return ConversationService(
        llm=llm,
        lead_repo=lead_repo or LeadRepository(engine=engine),
        message_repo=MessageRepository(engine=engine),
        whatsapp_number=WHATSAPP_NUMBER,
    )


# --- CHAT-02: plain reply returned and persisted -----------------------------


async def test_plain_reply_is_returned_and_persisted(engine):
    llm = FakeLLMClient(result=LLMResult(text="Olá! Como posso ajudar?"))
    service = build_service(engine, llm)

    result = await service.handle_turn(session_id="s1", user_message="oi")

    assert result.reply == "Olá! Como posso ajudar?"
    assert result.lead_captured is False
    assert result.whatsapp_url is None

    history = await MessageRepository(engine=engine).get_history("s1")
    assert [(m.role, m.content) for m in history] == [
        ("user", "oi"),
        ("assistant", "Olá! Como posso ajudar?"),
    ]
    assert llm.calls[0]["tools"] == [SAVE_LEAD_INFO_TOOL]


# --- CHAT-05: contact asked at most once per session -------------------------


async def test_contact_is_asked_at_most_once_across_two_turns(engine):
    llm = FakeLLMClient(result=LLMResult(text="Olá! Como posso ajudar?"))
    service = build_service(engine, llm)

    await service.handle_turn(session_id="s1", user_message="oi")
    llm.result = LLMResult(text="Entendi, posso ajudar com isso.")
    await service.handle_turn(
        session_id="s1", user_message="quero automatizar meu atendimento"
    )

    assert llm.calls[0]["messages"][0]["content"] == build_system_prompt(
        contact_already_asked=False
    )
    assert llm.calls[1]["messages"][0]["content"] == build_system_prompt(
        contact_already_asked=True
    )
    # The second turn also carries the prior conversation as context.
    assert llm.calls[1]["messages"][1:] == [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "Olá! Como posso ajudar?"},
        {"role": "user", "content": "quero automatizar meu atendimento"},
    ]


# --- CHAT-06: declined contact still saves a lead ----------------------------


async def test_tool_call_without_contact_saves_lead_with_has_contact_false(engine):
    llm = FakeLLMClient(
        result=LLMResult(
            tool_call=ToolCall(
                name="save_lead_info",
                arguments=json.dumps(
                    {
                        "category": "whatsapp_atendimento",
                        "need_summary": "Quer automatizar o atendimento no WhatsApp",
                    }
                ),
            )
        )
    )
    service = build_service(engine, llm)

    result = await service.handle_turn(session_id="s1", user_message="prefiro não passar")

    leads = await LeadRepository(engine=engine).list_leads()
    assert len(leads) == 1
    assert leads[0].has_contact is False
    assert leads[0].contact_phone is None
    assert leads[0].contact_email is None
    assert leads[0].category == "whatsapp_atendimento"
    assert result.lead_captured is True
    # The conversation continues normally rather than blocking on the refusal.
    assert result.reply != ""
    history = await MessageRepository(engine=engine).get_history("s1")
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].content == result.reply


# --- CHAT-07 + CHAT-08: lead persisted with contact, WhatsApp handoff --------


async def test_tool_call_persists_lead_and_returns_whatsapp_url(engine):
    summary = "Quer automatizar o atendimento no WhatsApp"
    llm = FakeLLMClient(
        result=LLMResult(
            tool_call=ToolCall(
                name="save_lead_info",
                arguments=json.dumps(
                    {
                        "category": "whatsapp_atendimento",
                        "need_summary": summary,
                        "contact_name": "Ana",
                        "contact_phone": "+5591988887777",
                        "contact_email": "ana@exemplo.com",
                    }
                ),
            )
        )
    )
    service = build_service(engine, llm)

    result = await service.handle_turn(
        session_id="s1", user_message="quero automatizar meu whatsapp"
    )

    leads = await LeadRepository(engine=engine).list_leads()
    assert len(leads) == 1
    assert leads[0].session_id == "s1"
    assert leads[0].category == "whatsapp_atendimento"
    assert leads[0].need_summary == summary
    assert leads[0].contact_name == "Ana"
    assert leads[0].contact_phone == "+5591988887777"
    assert leads[0].contact_email == "ana@exemplo.com"
    assert leads[0].has_contact is True

    assert result.lead_captured is True
    assert result.whatsapp_url is not None
    assert result.whatsapp_url.startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")
    assert quote(summary) in result.whatsapp_url


# --- CHAT-09: LLM failure degrades to fallback reply + WhatsApp link ---------


async def test_llm_unavailable_returns_fallback_reply_and_whatsapp_url(engine):
    llm = FakeLLMClient(error=LLMUnavailableError("timeout"))
    service = build_service(engine, llm)

    result = await service.handle_turn(session_id="s1", user_message="oi")

    assert result.reply.startswith("Desculpe")
    assert "WhatsApp" in result.reply
    assert result.whatsapp_url is not None
    assert result.whatsapp_url.startswith(f"https://wa.me/{WHATSAPP_NUMBER}?text=")
    assert result.lead_captured is False


# --- Edge case: malformed structured-extraction payload ----------------------


async def test_unparseable_tool_arguments_fall_back_to_plain_text(engine):
    llm = FakeLLMClient(
        result=LLMResult(
            text="Certo, me conta mais sobre o seu processo.",
            tool_call=ToolCall(name="save_lead_info", arguments="{isso nao e json"),
        )
    )
    service = build_service(engine, llm)

    result = await service.handle_turn(session_id="s1", user_message="oi")

    assert result.reply == "Certo, me conta mais sobre o seu processo."
    assert result.lead_captured is False
    assert result.whatsapp_url is None
    assert await LeadRepository(engine=engine).list_leads() == []


def test_parse_lead_arguments_rejects_a_missing_required_field():
    """Direct unit test of the validation contract itself: this is what
    keeps a missing `need_summary` from ever reaching the persistence call,
    regardless of how the broader turn-handling flow is composed.
    """
    tool_call = ToolCall(
        name="save_lead_info",
        arguments=json.dumps({"category": "whatsapp_atendimento"}),
        call_id="c1",
    )

    assert _parse_lead_arguments(tool_call) is None


async def test_tool_arguments_missing_required_field_fall_back_to_plain_text(
    engine, caplog
):
    llm = FakeLLMClient(
        result=LLMResult(
            text="Certo, me conta mais sobre o seu processo.",
            tool_call=ToolCall(
                name="save_lead_info",
                arguments=json.dumps({"category": "whatsapp_atendimento"}),
            ),
        )
    )
    service = build_service(engine, llm)

    with caplog.at_level(logging.WARNING):
        result = await service.handle_turn(session_id="s1", user_message="oi")

    assert result.reply == "Certo, me conta mais sobre o seu processo."
    assert result.lead_captured is False
    assert await LeadRepository(engine=engine).list_leads() == []
    # Distinguishes "extraction declined cleanly" from "extraction crashed and
    # was swallowed as a persistence failure" - the two must never look alike.
    assert any("malformed save_lead_info arguments" in r.getMessage() for r in caplog.records)
    assert not any("lead persistence failed" in r.getMessage() for r in caplog.records)


# --- Edge case: lead persistence fails after a successful reply --------------


async def test_lead_persistence_failure_still_returns_reply_and_logs(
    engine, tmp_path, caplog
):
    # Points at a subdirectory that is never created, so SQLite cannot open
    # the database file and `upsert_lead` raises.
    broken_lead_repo = LeadRepository(
        engine=create_engine(f"sqlite:///{tmp_path}/missing_dir/app.db")
    )
    llm = FakeLLMClient(
        result=LLMResult(
            text="Anotado! Vou encaminhar para o time.",
            tool_call=ToolCall(
                name="save_lead_info",
                arguments=json.dumps(
                    {"category": "outro", "need_summary": "Precisa de um orçamento"}
                ),
            ),
        )
    )
    service = build_service(engine, llm, lead_repo=broken_lead_repo)

    with caplog.at_level(logging.ERROR):
        result = await service.handle_turn(session_id="s1", user_message="oi")

    assert result.reply == "Anotado! Vou encaminhar para o time."
    assert result.lead_captured is False
    assert any("s1" in record.getMessage() for record in caplog.records)
