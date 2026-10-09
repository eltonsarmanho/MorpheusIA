"""Chatwoot: idempotência, estado de atendimento, transferência e falhas (CHW-01..CHW-12)."""
import json

import pytest

from app.application.answering.orchestrator import Orchestrator, OrchestratorConfig
from app.application.chat.handler import ChatwootEventHandler, conversation_key
from app.application.retrieval.hybrid import HybridRetriever
from app.domain.handoff import HandoffState as S, InvalidTransition
from app.domain.policies import AbstentionPolicy
from tests.conftest import FakeGateway, FakeLLM, add_doc

P = "1234567-89.2026.8.03.0001"
KEY = conversation_key(1, 77)


def msg_event(content="Quando é a audiência de conciliação do processo " + P + "?", msg_id=1, mtype="incoming", sender_type="contact", conv=77, assignee=None, private=False):
    return {"event": "message_created", "id": msg_id, "content": content, "message_type": mtype, "private": private,
            "sender": {"type": sender_type, "id": 5}, "account": {"id": 1},
            "conversation": {"id": conv, "status": "pending", "meta": {"assignee": assignee}}}


@pytest.fixture
def env(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026 às 10h.", doc_type="Despacho", title="Despacho")
    llm = FakeLLM(json.dumps({"resposta": "A audiência é em 20/07/2026 às 10h [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False}))
    orc = Orchestrator(HybridRetriever(store, embedder, policy=AbstentionPolicy(top_k=4)), store, llm, OrchestratorConfig())

    def make(gw=None):
        gw = gw or FakeGateway()
        return ChatwootEventHandler(orc, ops, gw, max_handoff_attempts=3), gw

    return make, ops


def process(h, payload):
    kind, key = h.claim(payload)
    if kind in ("duplicate", "ignored"):
        return kind
    return h.handle(payload, key)


def test_mensagem_recebida_gera_resposta_e_etiquetas(env):  # CHW-09
    make, ops = env
    h, gw = make()
    res = process(h, msg_event())
    assert res.outcome == "processed" and "20/07/2026" in gw.sent[0][1]
    assert {"ia_orquestrador", "ia_rag", "consulta_processual"} <= set(gw.labels[77])


def test_evento_duplicado_e_processado_uma_vez(env):  # CHW-05
    make, _ = env
    h, gw = make()
    assert process(h, msg_event(msg_id=10)).outcome == "processed"
    assert process(h, msg_event(msg_id=10)) == "duplicate"
    assert len(gw.sent) == 1


def test_mensagens_do_proprio_bot_e_privadas_sao_ignoradas(env):  # CHW-07
    make, _ = env
    h, gw = make()
    assert process(h, msg_event(msg_id=1, mtype="outgoing", sender_type="agent_bot")).outcome == "ignored"
    assert process(h, msg_event(msg_id=2, private=True)).outcome == "ignored"
    assert gw.sent == []


def test_pedido_de_atendente_transfere_na_ordem_correta_e_so_entao_avisa(env):  # CHW-10
    make, ops = env
    h, gw = make()
    res = process(h, msg_event("quero falar com um atendente", msg_id=3))
    assert res.outcome == "handoff"
    assert gw.assigned == [(77, "Atendimento Humano Geral")] and gw.statuses == [(77, "open")]
    assert "humano" in gw.labels[77] and "Atendimento Humano Geral" in gw.sent[-1][1]
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE


def test_bot_nao_responde_depois_da_transferencia(env):  # CHW-02
    make, ops = env
    h, gw = make()
    process(h, msg_event("quero falar com um atendente", msg_id=3))
    n = len(gw.sent)
    for i in (4, 5):
        assert process(h, msg_event("e a audiência?", msg_id=i)).outcome == "silent"
    assert len(gw.sent) == n


def test_falha_do_chatwoot_nao_confirma_transferencia_nem_avisa_conclusao(env):  # CHW-03, CHW-06
    make, ops = env
    h, gw = make(FakeGateway(fail_assign=1))
    res = process(h, msg_event("quero falar com um atendente", msg_id=3))
    assert res.outcome == "error" and ops.get(KEY).handoff_state is S.HANDOFF_REQUESTED
    assert not any("Encaminhei" in t for _, t in gw.sent)
    assert "ia_falha" in gw.labels[77]


def test_proxima_mensagem_repete_a_tentativa_ate_confirmar(env):  # CHW-03
    make, ops = env
    h, gw = make(FakeGateway(fail_assign=1))
    process(h, msg_event("quero falar com um atendente", msg_id=3))
    assert process(h, msg_event("alô?", msg_id=4)).outcome == "handoff"
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and any("Encaminhei" in t for _, t in gw.sent)


def test_conversa_resolvida_fecha_e_so_retoma_com_comando_explicito(env):  # CHW-04
    make, ops = env
    h, gw = make()
    process(h, msg_event("quero falar com um atendente", msg_id=3))
    process(h, {"event": "conversation_status_changed", "id": 77, "status": "resolved", "account": {"id": 1}, "conversation": {"id": 77, "status": "resolved"}})
    assert ops.get(KEY).handoff_state is S.HUMAN_CLOSED
    assert process(h, msg_event("obrigado, e a audiência?", msg_id=9)).outcome == "silent"  # não retoma sozinho
    st = h.resume_automation(1, 77, actor="supervisora@tjpa", reason="atendimento concluído")
    assert st.handoff_state is S.AUTOMATION_RESUMED
    assert process(h, msg_event(msg_id=11)).outcome == "processed"
    audit = [a["action"] for a in ops.audit_rows(KEY)]
    assert "transition:human_closed->automation_resumed" in audit


def test_retomada_invalida_fora_do_estado_fechado(env):  # CHW-01
    make, _ = env
    h, _ = make()
    with pytest.raises(InvalidTransition):
        h.resume_automation(1, 77, actor="x", reason="y")


def test_agente_humano_que_responde_assume_a_conversa(env):
    make, ops = env
    h, gw = make()
    process(h, msg_event(msg_id=1))
    process(h, msg_event("Olá, sou a Ana, vou assumir.", msg_id=2, mtype="outgoing", sender_type="user"))
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE
    assert process(h, msg_event(msg_id=3)).outcome == "silent"


def test_conversa_ja_atribuida_a_humano_nao_recebe_resposta_automatica(env):
    make, ops = env
    h, gw = make()
    assert process(h, msg_event(msg_id=1, assignee={"id": 9})).outcome == "silent"
    assert gw.sent == []


def test_erro_interno_libera_o_evento_para_reentrega_e_marca_falha(env):  # CHW-06
    make, ops = env
    h, gw = make(FakeGateway(fail_send=1))
    payload = msg_event(msg_id=50)
    assert process(h, payload).outcome == "error"
    assert "ia_falha" in gw.labels[77]
    assert process(h, payload).outcome == "processed"  # reentrega funciona
