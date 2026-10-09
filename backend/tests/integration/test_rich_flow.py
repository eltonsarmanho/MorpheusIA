"""Fluxo guiado no WhatsApp: protocolo TKT, menus com botões/listas, /encerrar e encerramento pelo usuário."""
import json
import re

import pytest

from app.application.chat import flow
from app.application.chat.handler import ChatwootEventHandler
from app.domain.handoff import HandoffState as S
from tests.conftest import FakeGateway, FakeLLM, add_doc
from tests.integration.test_chatwoot_handler import KEY, msg_event, process
from tests.integration.test_orchestrator import P1, P2, build, out


@pytest.fixture
def env(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026 às 10h.", doc_type="Despacho", title="Despacho")
    add_doc(store, embedder, doc_id="d2", text="Certifico o decurso de prazo sem manifestação da ré.", doc_type="Certidão", title="Certidão", process_number=P2, pje_doc_id="2")
    for pn in (P1, P2):
        add_doc(store, embedder, doc_id=f"proc-{pn}:capa", text=f"Ficha do processo {pn}. Classe e órgão julgador, valor da causa, assuntos e partes. Audiência em 20/07/2026 às 10h.", doc_type="Capa", title="Capa", process_number=pn, pje_doc_id=None)
    llm = FakeLLM(json.dumps({"resposta": "A audiência é em 20/07/2026 às 10h [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False}))
    orc = build(store, embedder, llm)

    def make(gw=None):
        gw = gw or FakeGateway()
        return ChatwootEventHandler(orc, ops, gw, rich_flow=True), gw

    return make, ops


def texts(gw):
    return [t for _, t in gw.sent]


def say(h, content, msg_id, **kw):
    return process(h, msg_event(content, msg_id=msg_id, **kw))


# ---------------------------------------------------------------------------------------------- protocolo
def test_primeira_mensagem_abre_ticket_com_data_e_hora_gravadas(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    t = ops.get_open_ticket(KEY)
    assert re.fullmatch(r"TKT-[0-9A-F]{8}", t.ticket_id) and t.opened_at and t.status == "open"
    opening = texts(gw)[0]
    assert t.ticket_id in opening and "Atendimento aberto" in opening and re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", opening)
    assert any(t.ticket_id in n for _, n in gw.notes)  # nota privada para a equipe


def test_mensagens_seguintes_reaproveitam_o_mesmo_ticket(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    first = ops.get_open_ticket(KEY).ticket_id
    say(h, "Quanto é 2 mais 2?", 2)
    assert ops.get_open_ticket(KEY).ticket_id == first and sum("Atendimento aberto" in t for t in texts(gw)) == 1


def test_saudacao_abre_o_menu_principal_em_lista(env):
    make, _ = env
    h, gw = make()
    say(h, "Olá", 1)
    opts = [o for _, o in gw.options if o]
    assert len(opts[-1]) == 5 and "✅ Encerrar atendimento" in opts[-1] and all(len(x) <= 24 for x in opts[-1])


def test_titulos_das_opcoes_respeitam_os_limites_do_whatsapp():
    for group, limit in ((flow.AFTER_ANSWER, 20), (flow.PROCESS_MENU, 20)):
        assert len(group) <= 3 and all(len(o.title) <= limit for o in group), [o.title for o in group if len(o.title) > limit]
    for group in (flow.MAIN_MENU, flow.inst_options(), flow.legal_options(), flow.process_options([P1] * 12)):
        assert len(group) <= 10 and all(len(o.title) <= 24 for o in group), [o.title for o in group if len(o.title) > 24]


# ---------------------------------------------------------------------------------------------- menus
def test_consultar_processo_lista_os_processos_e_a_escolha_mostra_o_submenu(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "📄 Consultar processo", 2)
    assert P1 in texts(gw)[-1] and P2 in texts(gw)[-1]
    titles = [o for _, o in gw.options if o][-1]
    assert f"📄 {P1[:15]}" in titles
    say(h, f"📄 {P1[:15]}", 3)
    assert ops.get(KEY).process_number == P1
    assert [o for _, o in gw.options if o][-1] == ["📋 Dados da capa", "🗓️ Cronologia", "🔎 Outra pergunta"]


def test_botao_de_dados_da_capa_roda_a_pergunta_pronta_e_oferece_proximos_passos(env):
    make, _ = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, f"📄 {P1[:15]}", 2)
    say(h, "📋 Dados da capa", 3)
    assert "20/07/2026" in texts(gw)[-2]  # resposta do RAG (LLM falso)
    assert [o for _, o in gw.options if o][-1] == ["📋 Menu principal", "🙋 Atendente", "✅ Encerrar"]


def test_dados_da_capa_sem_processo_escolhido_pede_a_escolha_por_lista(env):
    make, _ = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "📋 Dados da capa", 2)
    assert "Sobre qual processo" in texts(gw)[-1]


def test_pergunta_ambigua_vira_lista_de_processos_nao_pedido_de_numero_por_extenso(env):
    make, _ = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "Qual foi a sentença?", 2)
    assert "Sobre qual processo" in texts(gw)[-1] and [o for _, o in gw.options if o][-1][0].startswith("📄")


def test_menus_institucional_e_juridico_e_texto_livre_so_quando_pedido(env):
    make, _ = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🏛️ Balcão Virtual", 2)
    assert "🕒 Horário" in [o for _, o in gw.options if o][-1]
    say(h, "💬 Outra dúvida", 3)
    assert "✍️" in texts(gw)[-1]  # aqui, sim, pede texto livre
    say(h, "⚖️ Termos jurídicos", 4)
    assert "⚖️ Justiça gratuita" in [o for _, o in gw.options if o][-1]


def test_opcao_atendente_transfere_e_informa_o_protocolo(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🙋 Falar com atendente", 2)
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE
    assert "Protocolo" in texts(gw)[-1] and ops.get_open_ticket(KEY).ticket_id in texts(gw)[-1]


# -------------------------------------------------------------------------------------------- encerramento
def test_usuario_encerra_pela_opcao_e_recebe_mensagem_com_o_ticket(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    tid = ops.get_open_ticket(KEY).ticket_id
    say(h, "✅ Encerrar atendimento", 2)
    closing = texts(gw)[-1]
    assert "Atendimento encerrado" in closing and tid in closing and "a seu pedido" in closing
    assert ops.get_open_ticket(KEY) is None and gw.statuses[-1] == (77, "resolved")
    closed = ops.list_tickets("closed")[0]
    assert closed.ticket_id == tid and closed.closed_at and closed.closed_by == "usuario"


def test_so_o_usuario_ou_o_atendente_encerram_o_bot_nunca_encerra_sozinho(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "Qual a audiência do processo " + P1 + "?", 2)
    say(h, "🙋 Falar com atendente", 3)
    say(h, "obrigado", 4)  # cliente agradece; bot silencioso, nada de encerramento automático
    assert (77, "resolved") not in gw.statuses and ops.get_open_ticket(KEY) is not None


def test_atendente_encerra_com_nota_privada_encerrar(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🙋 Falar com atendente", 2)
    tid = ops.get_open_ticket(KEY).ticket_id
    note = msg_event("/encerrar", msg_id=3, mtype="outgoing", sender_type="user", private=True)
    assert process(h, note).outcome == "processed"
    assert "pelo atendente" in texts(gw)[-1] and tid in texts(gw)[-1]
    assert gw.statuses[-1] == (77, "resolved") and ops.get_open_ticket(KEY) is None
    assert ops.get(KEY).handoff_state is S.HUMAN_CLOSED
    assert any("encerrado via /encerrar" in n for _, n in gw.notes)


def test_nota_privada_comum_e_nota_de_cliente_nao_encerram(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    assert process(h, msg_event("anotação interna", msg_id=2, mtype="outgoing", sender_type="user", private=True)).outcome == "ignored"
    assert process(h, msg_event("/encerrar", msg_id=3, private=True)).outcome == "ignored"  # cliente não encerra por nota
    assert ops.get_open_ticket(KEY) is not None


def test_resolver_pela_interface_do_chatwoot_tambem_fecha_o_ticket_e_avisa(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🙋 Falar com atendente", 2)
    tid = ops.get_open_ticket(KEY).ticket_id
    process(h, {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": 1.5})
    assert tid in texts(gw)[-1] and "Atendimento encerrado" in texts(gw)[-1] and ops.get(KEY).handoff_state is S.HUMAN_CLOSED


def test_encerramento_nao_envia_a_mensagem_duas_vezes(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "✅ Encerrar atendimento", 2)
    n = sum("Atendimento encerrado" in t for t in texts(gw))
    process(h, {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": 2.5})  # eco do status resolvido
    assert sum("Atendimento encerrado" in t for t in texts(gw)) == n == 1


def test_nova_mensagem_depois_do_encerramento_abre_novo_ticket_e_o_bot_volta(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🙋 Falar com atendente", 2)
    first = ops.get_open_ticket(KEY).ticket_id
    process(h, msg_event("/encerrar", msg_id=3, mtype="outgoing", sender_type="user", private=True))
    say(h, "Olá de novo", 4)
    second = ops.get_open_ticket(KEY)
    assert second.ticket_id != first and ops.get(KEY).handoff_state is S.AUTOMATION_RESUMED
    assert "Atendimento aberto" in "".join(texts(gw)[-3:]) or second.ticket_id in "".join(texts(gw)[-3:])
    audit = [a["action"] for a in ops.audit_rows(KEY)]
    assert audit.count("ticket_opened") == 2 and audit.count("ticket_closed") == 1


def test_modo_somente_texto_continua_disponivel(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026.", doc_type="Despacho")
    gw = FakeGateway()
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("É em 20/07/2026 [E1]."))), ops, gw, rich_flow=False)
    say(h, f"Quando é a audiência do processo {P1}?", 1)
    assert ops.get_open_ticket(KEY) is None and not any(o for _, o in gw.options)


# -------------------------------------------------------------------------------------------- interpretação
@pytest.mark.parametrize("text,kind", [
    ("✅ Encerrar atendimento", flow.Kind.CLOSE), ("✅ Encerrar", flow.Kind.CLOSE), ("encerrar", flow.Kind.CLOSE_HINT), ("ENCERRAR ATENDIMENTO", flow.Kind.CLOSE_HINT), ("tchau", flow.Kind.CLOSE_HINT),
    ("oi", flow.Kind.MENU), ("Menu", flow.Kind.MENU), ("📋 Menu principal", flow.Kind.MENU),
    ("🙋 Atendente", flow.Kind.HUMAN), ("Quando é a audiência do processo 1234567-89.2026.8.03.0001?", flow.Kind.FREE),
])
def test_interpretacao_de_opcoes_e_texto(text, kind):
    assert flow.parse(text, [P1]).kind is kind


def test_numero_do_processo_digitado_sozinho_seleciona_o_processo():
    assert flow.parse(P1, [P1, P2]) == flow.Action(flow.Kind.SELECT_PROCESS, P1)
    assert flow.parse("o processo " + P1, [P1]).kind is flow.Kind.FREE


def test_digitar_encerrar_nao_encerra_so_orienta(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    for i, word in enumerate(("encerrar", "Encerrar atendimento", "tchau", "sair"), start=2):
        say(h, word, i)
    assert ops.get_open_ticket(KEY) is not None and (77, "resolved") not in gw.statuses
    assert "toque em *✅ Encerrar*" in texts(gw)[-1] and [o for _, o in gw.options if o][-1] == ["✅ Encerrar", "📋 Menu principal"]
    say(h, "✅ Encerrar", 9)  # o toque no botão encerra
    assert ops.get_open_ticket(KEY) is None and gw.statuses[-1] == (77, "resolved")


def _ago(hours):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_inatividade_acima_de_23h_encerra_com_mensagem_e_ticket_fechado_pelo_sistema(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    tid = ops.get_open_ticket(KEY).ticket_id
    ops.touch(KEY, _ago(23.5))
    assert h.close_inactive(23) == [tid]
    assert "por inatividade" in texts(gw)[-1] and tid in texts(gw)[-1] and gw.statuses[-1] == (77, "resolved")
    closed = ops.list_tickets("closed")[0]
    assert closed.closed_by == "sistema" and closed.closed_at
    assert h.close_inactive(23) == []  # não repete


def test_atividade_recente_nao_encerra(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    ops.touch(KEY, _ago(22.9))
    assert h.close_inactive(23) == [] and ops.get_open_ticket(KEY) is not None


def test_qualquer_mensagem_renova_o_prazo_de_inatividade(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    ops.touch(KEY, _ago(30))
    say(h, "menu", 2)  # nova mensagem do cliente
    assert h.close_inactive(23) == []


def test_inatividade_com_atendimento_humano_fecha_e_a_proxima_mensagem_abre_novo_ticket(env):
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    say(h, "🙋 Falar com atendente", 2)
    first = ops.get_open_ticket(KEY).ticket_id
    ops.touch(KEY, _ago(24))
    assert h.close_inactive(23) == [first] and ops.get(KEY).handoff_state is S.HUMAN_CLOSED
    say(h, "Oi", 3)
    assert ops.get_open_ticket(KEY).ticket_id != first and ops.get(KEY).handoff_state is S.AUTOMATION_RESUMED


def test_corpo_interativo_longo_vai_em_mensagem_separada_e_nunca_passa_de_1024(env):  # falha real: lista de 10 processos com 1044 caracteres
    make, ops = env
    h, gw = make()
    say(h, "Olá", 1)
    h.orch.store.approved_process_numbers = lambda: [f"{i:07d}-00.2026.8.03.0001" for i in range(10)]
    h.orch.store.process_info = lambda n: {"process_class": "PROCEDIMENTO COMUM CÍVEL", "court_unit": "2ª Vara de Fazenda Pública de Macapá"}
    say(h, "📄 Consultar processo", 2)
    with_options = [(c, o) for c, o in gw.options if o]
    body = texts(gw)[-1]
    assert len(body) <= 1024 and body == "Escolha uma opção 👇" and len(with_options[-1][1]) == 10
    assert "0000009-00.2026.8.03.0001" in texts(gw)[-2]  # o detalhamento foi na mensagem comum anterior
