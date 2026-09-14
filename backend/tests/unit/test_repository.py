import time

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import create_engine

from app.storage.models import init_db
from app.storage.repository import ContactInfo, LeadRepository, MessageRepository


@pytest.fixture()
def engine(tmp_path):
    """A fresh temp SQLite file per test - never the dev DB at backend/data/app.db."""
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    init_db(target_engine=test_engine)
    return test_engine


async def test_upsert_lead_twice_same_session_updates_existing_row(engine):
    repo = LeadRepository(engine=engine)

    await repo.upsert_lead(
        session_id="s1", category="outro", need_summary="primeira necessidade"
    )
    await repo.upsert_lead(
        session_id="s1",
        category="gestao_empresas",
        need_summary="necessidade atualizada",
        contact=ContactInfo(phone="+5511999999999"),
    )

    leads = await repo.list_leads()

    assert len(leads) == 1
    assert leads[0].session_id == "s1"
    assert leads[0].category == "gestao_empresas"
    assert leads[0].need_summary == "necessidade atualizada"
    assert leads[0].has_contact is True
    assert leads[0].contact_phone == "+5511999999999"


async def test_upsert_lead_without_contact_marks_has_contact_false(engine):
    repo = LeadRepository(engine=engine)

    lead = await repo.upsert_lead(
        session_id="s1", category="outro", need_summary="sem contato"
    )

    assert lead.has_contact is False
    assert lead.contact_phone is None
    assert lead.contact_email is None


async def test_list_leads_returns_newest_first(engine):
    repo = LeadRepository(engine=engine)

    await repo.upsert_lead(session_id="s1", category="outro", need_summary="a")
    time.sleep(0.01)
    await repo.upsert_lead(session_id="s2", category="outro", need_summary="b")
    time.sleep(0.01)
    await repo.upsert_lead(session_id="s3", category="outro", need_summary="c")

    leads = await repo.list_leads()

    assert [lead.session_id for lead in leads] == ["s3", "s2", "s1"]


async def test_upsert_lead_raises_on_db_error(tmp_path):
    # Points at a subdirectory that does not exist and is never created -
    # SQLite cannot open the database file, so the repository must not
    # silently swallow the failure.
    broken_engine = create_engine(f"sqlite:///{tmp_path}/missing_dir/app.db")
    repo = LeadRepository(engine=broken_engine)

    with pytest.raises(OperationalError):
        await repo.upsert_lead(session_id="s1", category="outro", need_summary="x")


async def test_append_message_and_get_history_in_creation_order(engine):
    repo = MessageRepository(engine=engine)

    await repo.append_message(session_id="s1", role="user", content="oi")
    time.sleep(0.01)
    await repo.append_message(session_id="s1", role="assistant", content="ola")
    time.sleep(0.01)
    await repo.append_message(
        session_id="s1", role="user", content="quero um orcamento"
    )

    history = await repo.get_history(session_id="s1")

    assert [m.content for m in history] == ["oi", "ola", "quero um orcamento"]
    assert [m.role for m in history] == ["user", "assistant", "user"]


async def test_get_history_scoped_to_session_id(engine):
    repo = MessageRepository(engine=engine)

    await repo.append_message(session_id="s1", role="user", content="mensagem s1")
    await repo.append_message(session_id="s2", role="user", content="mensagem s2")

    history = await repo.get_history(session_id="s1")

    assert [m.content for m in history] == ["mensagem s1"]


async def test_append_message_raises_on_db_error(tmp_path):
    broken_engine = create_engine(f"sqlite:///{tmp_path}/missing_dir/app.db")
    repo = MessageRepository(engine=broken_engine)

    with pytest.raises(OperationalError):
        await repo.append_message(session_id="s1", role="user", content="oi")
