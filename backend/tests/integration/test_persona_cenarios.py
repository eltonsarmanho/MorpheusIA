"""Voz da Júlia em cenários difíceis, só nos textos determinísticos (sem LLM real).

Verifica o que importa sem exigir frase exata: acolhimento, próximo passo claro e nenhuma alegação falsa de consulta,
envio, recebimento ou execução.
"""
import pytest

from app.application.chat.handler import ChatwootEventHandler
from app.domain.handoff import HandoffState as S
from tests.conftest import FakeGateway, FakeLLM, add_doc
from tests.integration.test_chatwoot_handler import KEY, msg_event, process
from tests.integration.test_orchestrator import build, out

FALSE_CLAIMS = ("consultei o pje", "consultando o pje", "foi recebida", "atendente recebeu", "diligência foi realizada",
                "já foi realizada", "resolvido", "garanto", "prazo de resposta", "entrada inválida")


@pytest.fixture
def h(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026.", doc_type="Despacho")
    gw = FakeGateway()
    handler = ChatwootEventHandler(build(store, embedder, FakeLLM(out("É em 20/07/2026 [E1]."))), ops, gw, rich_flow=True)
    handler.gw_fake = gw
    return handler


@pytest.mark.parametrize("scenario,message", [
    ("pessoa confusa", "não sei o que fazer, me ajuda"),
    ("pedido ambíguo", "Qual foi a sentença?"),
    ("informação não encontrada", "Qual o horário de funcionamento do fórum de Marabá?"),
])
def test_respostas_acolhem_orientam_e_nao_fazem_alegacao_falsa(h, scenario, message):
    process(h, msg_event("Olá", msg_id=1))
    process(h, msg_event(message, msg_id=2))
    reply = h.gw_fake.sent[-1][1]
    low = reply.lower()
    assert not any(c in low for c in FALSE_CLAIMS), (scenario, reply)
    assert "?" in reply or "👇" in reply, (scenario, reply)  # termina com um próximo passo


def test_usuario_irritado_que_pede_uma_pessoa_e_encaminhado_sem_discussao(h, ops):
    process(h, msg_event("Olá", msg_id=1))
    process(h, msg_event("isso não funciona!!! quero falar com alguém", msg_id=2))
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and "Aguarde um momento" in h.gw_fake.sent[-1][1]


def test_falha_tecnica_explica_o_limite_sem_culpar_o_usuario(store, embedder, ops):
    orc = build(store, embedder, FakeLLM(out("x")))
    orc.respond = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha"))
    gw = FakeGateway()
    process(ChatwootEventHandler(orc, ops, gw), msg_event("Qual a audiência?", msg_id=1))
    reply = gw.sent[-1][1].lower()
    assert "dificuldade técnica" in reply and "você errou" not in reply and "atendimento humano" in reply
