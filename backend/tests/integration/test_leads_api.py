import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlmodel import create_engine

from app.api import leads
from app.storage.models import init_db
from app.storage.repository import ContactInfo, LeadRepository

ADMIN_TOKEN = "token-de-teste"


@pytest.fixture()
def engine(tmp_path):
    """A fresh temp SQLite file per test - never the dev DB at backend/data/app.db."""
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}")
    init_db(target_engine=test_engine)
    return test_engine


def build_app(engine):
    """The leads router with the repository and admin token injected.

    The admin token comes from an overridden dependency, so the test never
    reads the project's real `.env`.
    """
    app = FastAPI()
    app.include_router(leads.router)
    app.dependency_overrides[leads.get_lead_repository] = lambda: LeadRepository(
        engine=engine
    )
    app.dependency_overrides[leads.get_admin_token] = lambda: ADMIN_TOKEN
    return app


def client_for(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- LEAD-02: bearer-token auth ----------------------------------------------


async def test_missing_authorization_header_returns_401_without_lead_data(engine):
    repo = LeadRepository(engine=engine)
    await repo.upsert_lead(session_id="s1", category="outro", need_summary="segredo")

    async with client_for(build_app(engine)) as client:
        response = await client.get("/api/leads")

    assert response.status_code == 401
    assert "leads" not in response.json()
    assert "segredo" not in response.text


async def test_wrong_bearer_token_returns_401_without_lead_data(engine):
    repo = LeadRepository(engine=engine)
    await repo.upsert_lead(session_id="s1", category="outro", need_summary="segredo")

    async with client_for(build_app(engine)) as client:
        response = await client.get(
            "/api/leads", headers={"Authorization": "Bearer token-errado"}
        )

    assert response.status_code == 401
    assert "leads" not in response.json()
    assert "segredo" not in response.text


# --- LEAD-01: authenticated listing, newest first ----------------------------


async def test_valid_token_returns_leads_newest_first(engine):
    repo = LeadRepository(engine=engine)
    await repo.upsert_lead(
        session_id="s1", category="outro", need_summary="primeira necessidade"
    )
    time.sleep(0.01)
    await repo.upsert_lead(
        session_id="s2",
        category="whatsapp_atendimento",
        need_summary="Quer automatizar o atendimento",
        contact=ContactInfo(name="Ana", phone="+5591988887777"),
    )

    async with client_for(build_app(engine)) as client:
        response = await client.get(
            "/api/leads", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )

    assert response.status_code == 200
    body = response.json()["leads"]
    assert [lead["session_id"] for lead in body] == ["s2", "s1"]
    assert body[0]["category"] == "whatsapp_atendimento"
    assert body[0]["need_summary"] == "Quer automatizar o atendimento"
    assert body[0]["contact_name"] == "Ana"
    assert body[0]["contact_phone"] == "+5591988887777"
    assert body[0]["has_contact"] is True
    assert body[1]["has_contact"] is False
