"""API: webhook autenticado e assíncrono, console, curadoria protegida (UI-02, CHW-05)."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.interfaces.api.main import create_app
from app.domain.models import ReviewState
from tests.conftest import FakeGateway, FakeLLM, add_doc

P = "1234567-89.2026.8.03.0001"
ADM = {"Authorization": "Bearer adm-token"}


@pytest.fixture
def client(store, embedder, ops):
    settings = Settings(_env_file=None, admin_api_token="adm-token", chatwoot_webhook_secret="seg-webhook", embedder_backend="hashing", min_vector_score=0.0)
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026 às 10h.", doc_type="Despacho", title="Despacho")
    add_doc(store, embedder, doc_id="p1", text="Pendente de revisão sobre audiência.", state=ReviewState.PENDING_REVIEW, index=False)
    llm = FakeLLM(json.dumps({"resposta": "É em 20/07/2026 [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False}))
    gw = FakeGateway()
    c = build_container(settings, store=store, ops=ops, embedder=embedder, llm=llm, gateway=gw)
    app = create_app(c, settings)
    tc = TestClient(app)
    tc.gw = gw
    return tc


def event(msg_id=1, content=f"Quando é a audiência de conciliação do processo {P}?"):
    return {"event": "message_created", "id": msg_id, "content": content, "message_type": "incoming", "private": False,
            "sender": {"type": "contact"}, "account": {"id": 1}, "conversation": {"id": 5, "status": "pending", "meta": {}}}


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_webhook_exige_segredo(client):
    assert client.post("/webhooks/chatwoot", json=event()).status_code == 401
    assert client.post("/webhooks/chatwoot?token=errado", json=event()).status_code == 401


def test_webhook_aceita_responde_em_background_e_ignora_duplicata(client):
    r1 = client.post("/webhooks/chatwoot?token=seg-webhook", json=event(7))
    r2 = client.post("/webhooks/chatwoot?token=seg-webhook", json=event(7))
    assert r1.json()["status"] == "accepted" and r2.json()["status"] == "duplicate"
    assert len(client.gw.sent) == 1 and "20/07/2026" in client.gw.sent[0][1]


def test_console_responde_com_fontes_e_valida_entrada(client):
    ok = client.post("/api/chat", headers=ADM, json={"session_id": "sessao-teste-1", "message": f"Quando é a audiência de conciliação do processo {P}?"}).json()
    assert ok["kind"] == "answer" and ok["citations"] and ok["domain"] == "processual" and ok["latency_ms"] >= 0
    assert client.post("/api/chat", headers=ADM, json={"session_id": "sessao-teste-1", "message": ""}).status_code == 422
    assert client.post("/api/chat", headers=ADM, json={"session_id": "sessao-teste-1", "message": "x" * 1001}).status_code == 422
    assert client.post("/api/chat", headers=ADM, json={"session_id": "../etc", "message": "oi"}).status_code == 422


def test_console_nao_finge_transferencia(client):
    r = client.post("/api/chat", headers=ADM, json={"session_id": "sessao-teste-2", "message": "quero falar com um atendente"}).json()
    assert r["kind"] == "handoff" and "nenhuma transferência real" in r["text"]


def test_rotas_de_curadoria_exigem_token(client):  # UI-02
    for path in ("/api/admin/stats", "/api/admin/documents", "/api/admin/audit"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer errado"}).status_code == 401
    assert client.get("/api/admin/stats", headers={"Authorization": "Bearer adm-token"}).status_code == 200


def test_aprovar_documento_pela_api_indexa_e_registra_revisor(client):  # UI-01, CUR-03
    h = {"Authorization": "Bearer adm-token"}
    pend = client.get("/api/admin/documents?state=pending_review", headers=h).json()
    assert [d["doc_id"] for d in pend] == ["p1"]
    r = client.post("/api/admin/documents/p1/review", headers=h, json={"decision": "approved", "reviewer": "Curadora", "reason": "conferido"})
    assert r.status_code == 200 and r.json()["chunks_indexed"] == 1
    assert client.post("/api/admin/documents/inexistente/review", headers=h, json={"decision": "approved", "reviewer": "Cur", "reason": "ok ok"}).status_code == 404
    assert client.post("/api/admin/documents/p1/review", headers=h, json={"decision": "stale", "reviewer": "Cur", "reason": "ok ok"}).status_code == 422


def test_console_exige_token_por_padrao(client):
    body = {"session_id": "sessao-teste-3", "message": "oi"}
    assert client.post("/api/chat", json=body).status_code == 401
    assert client.post("/api/chat", headers={"Authorization": "Bearer errado"}, json=body).status_code == 401
    assert client.post("/api/chat", headers=ADM, json=body).status_code == 200


def test_console_publico_so_com_configuracao_explicita(store, embedder, ops):
    settings = Settings(_env_file=None, admin_api_token="adm-token", console_public=True, embedder_backend="hashing", min_vector_score=0.0)
    c = build_container(settings, store=store, ops=ops, embedder=embedder, llm=FakeLLM("{}"), gateway=FakeGateway())
    tc = TestClient(create_app(c, settings))
    assert tc.post("/api/chat", json={"session_id": "sessao-teste-4", "message": "oi"}).status_code == 200
