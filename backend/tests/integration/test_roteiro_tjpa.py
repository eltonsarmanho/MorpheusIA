"""Roteiro TJPA no WhatsApp (RTA-01..RTA-28): perfil, identificação, diligência, escolha de canal e falhas.

Sem rede: Chatwoot falso, LLM falso. A unidade "Unidade de Teste" existe só nestes testes; não é dado real.
"""
import json
import logging

import pytest

from app.application.chat import flow
from app.application.chat.handler import ChatwootEventHandler
from app.domain.handoff import HandoffState as S
from app.domain.models import KnowledgeDomain, Profile
from app.domain.ports import ProcessSearchResult
from app.domain.units import InstitutionalContacts, Unit, UnitDirectory
from tests.conftest import FakeGateway, FakeLLM, add_doc
from tests.integration.test_chatwoot_handler import KEY, msg_event, process
from tests.integration.test_orchestrator import P1, P2, build

GENERAL_URL = "https://www.tjpa.jus.br/PortalExterno/institucional//Balcao-Virtual-e-Contatos/844287-balcao-virtual.xhtml"
UNIT_URL = "https://www.tjpa.jus.br/balcao/unidade-de-teste"
DILIGENCE = "Solicito a juntada do substabelecimento   anexo.\nAtenção: prazo em curso — ação nº 12 🙂 "
CPF_OK = "529.982.247-25"


def cnj(seq: str, rest: str) -> str:
    """Número CNJ com dígitos verificadores corretos (para testar 'válido, porém ausente do acervo')."""
    dv = 98 - (int(seq + rest) * 100) % 97
    return f"{seq}-{dv:02d}.{rest[:4]}.{rest[4]}.{rest[5:7]}.{rest[7:]}"


class SpySearch:
    def __init__(self, by_name=()):
        self.calls, self.names = [], list(by_name)

    def by_cpf(self, cpf):
        self.calls.append("cpf")
        return ProcessSearchResult(available=False)

    def by_name(self, name):
        self.calls.append("name")
        return ProcessSearchResult(available=True, candidates=tuple(self.names))


@pytest.fixture
def env(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026 às 10h.", doc_type="Despacho", title="Despacho")
    add_doc(store, embedder, doc_id="d2", text="Certifico o decurso de prazo sem manifestação da ré.", doc_type="Certidão", title="Certidão",
            process_number=P2, pje_doc_id="2")
    for pn in (P1, P2):
        add_doc(store, embedder, doc_id=f"proc-{pn}:capa", text=f"Ficha do processo {pn}. Audiência em 20/07/2026 às 10h.", doc_type="Capa",
                title="Capa", process_number=pn, pje_doc_id=None)
    parties = {P1: [{"nome": "Maria Exemplo da Silva", "papel": "Autor"}],
               P2: [{"nome": "Maria Exemplo Silva", "papel": "Reu"}, {"nome": "Outra Pessoa", "papel": "Autor"}]}
    store.process_info = lambda n: {"process_number": n, "process_class": "PROCEDIMENTO COMUM CÍVEL", "court_unit": "Unidade de Teste",
                                    "parties": parties.get(n, [])}
    llm = FakeLLM(json.dumps({"resposta": "A audiência é em 20/07/2026 às 10h [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False}))
    orc = build(store, embedder, llm)

    def make(units=None, gw=None, **kw):
        gw = gw or FakeGateway()
        units = units if units is not None else UnitDirectory(contacts=InstitutionalContacts(balcao_general_url=GENERAL_URL))
        return ChatwootEventHandler(orc, ops, gw, rich_flow=True, units=units, **kw), gw

    return make, ops, llm


def texts(gw):
    return [t for _, t in gw.sent]


def last_opts(gw):
    return [o for _, o in gw.options if o][-1]


counter = {"n": 0}


def say(h, content, msg_id=None):
    counter["n"] += 1
    return process(h, msg_event(content, msg_id=msg_id or 10_000 + counter["n"]))


def unit_dir(**kw):
    base = dict(id="ut", name="Unidade de Teste", city="Cidade Teste")
    base.update(kw)
    return UnitDirectory([Unit(**base)], InstitutionalContacts(balcao_general_url=GENERAL_URL))


def lawyer_with_process(h, number=P1):
    say(h, "Olá")
    say(h, flow.OPT_ADV.title)
    say(h, flow.OPT_ID_NUMBER.title)
    say(h, number)


# ----------------------------------------------------------------------------------------- 1. abertura e perfil
def test_primeiro_contato_abre_protocolo_e_a_julia_oferece_os_tres_perfis(env):
    make, ops, _ = env
    h, gw = make()
    say(h, "Oi")
    assert ops.get_open_ticket(KEY) is not None and "Atendimento aberto" in texts(gw)[0]
    assert "Júlia" in texts(gw)[-1] and "assistente virtual" in texts(gw)[-1]
    opts = last_opts(gw)
    assert opts[:3] == [flow.OPT_PARTE.title, flow.OPT_ADV.title, flow.OPT_GERAL.title] and "✅ Encerrar atendimento" in opts


def test_perfil_escolhido_persiste_e_a_heuristica_nao_o_troca(env):
    make, ops, llm = env
    h, gw = make()
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    st = ops.get(KEY)
    assert st.profile is Profile.PARTE_TESTEMUNHA and st.profile_locked and last_opts(gw)[:3] == ["🔢 Sei o número", "🪪 Sei o CPF", "❓ Não sei nenhum"]
    assert "simples" in texts(gw)[-1]
    say(h, f"Meu cliente quer saber quando é a audiência do processo {P1}")  # palavras que a heurística lê como advogado
    assert ops.get(KEY).profile is Profile.PARTE_TESTEMUNHA and "linguagem simples" in llm.prompts[-1][1]


def test_advogado_recebe_rotulos_tecnicos_e_parte_rotulos_simples(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    assert ops.get(KEY).profile is Profile.ADVOGADO
    assert last_opts(gw) == [o.title for o in flow.ADV_MENU] and "Localizei o processo" in texts(gw)[-1]
    say(h, flow.OPT_VIEW_PROC.title)
    assert flow.OPT_STATUS_TECH.title in last_opts(gw) and flow.OPT_DILIGENCE.title in last_opts(gw)
    h2, gw2 = make()
    say(h2, flow.OPT_SWITCH_PROFILE.title)
    say(h2, flow.OPT_PARTE.title)
    say(h2, P2)
    assert flow.OPT_STATUS_SIMPLE.title in last_opts(gw2) and flow.OPT_PROVIDENCE.title in last_opts(gw2)


# ------------------------------------------------------------------------------------------ 2. informações gerais
def test_informacoes_gerais_sem_cadastro_declara_a_falta_e_consulta_a_base_aprovada(env):
    make, ops, _ = env
    h, gw = make()
    say(h, "Olá")
    say(h, flow.OPT_GERAL.title)
    assert ops.get(KEY).profile is Profile.GERAL and last_opts(gw)[:5] == ["📍 Endereço da unidade", "📞 Telefones e contatos", "🕒 Horário de atendimento", "🏛️ Balcão Virtual", "💬 Outra informação"]
    seen = []
    original = h.orch.respond
    h.orch.respond = lambda msg, st: (seen.append(msg), original(msg, st))[1]
    say(h, flow.OPT_G_ADDRESS.title)
    assert any("Ainda não tenho o cadastro oficial" in t for t in texts(gw)) and seen == [flow.GERAL_FALLBACK_QUESTIONS["endereco"]]
    assert "TeleJudiciário" not in "".join(texts(gw))  # contato não configurado: não é oferecido


def test_informacoes_gerais_com_cadastro_responde_so_o_dado_cadastrado_com_fonte(env):
    make, ops, _ = env
    units = unit_dir(phone="559132050000", source="fonte de teste", verified_at="2026-10-27")
    h, gw = make(units)
    say(h, flow.OPT_GERAL.title)
    say(h, flow.OPT_G_PHONE.title)
    assert ops.get(KEY).awaiting == "unit:telefone"
    say(h, "Cidade Teste, Unidade de Teste")
    answer = texts(gw)[-2]
    assert "(91) 3205-0000" in answer and "fonte de teste" in answer and last_opts(gw) == [o.title for o in flow.continue_options(False)]
    say(h, flow.OPT_G_ADDRESS.title)
    say(h, "Unidade de Teste")
    assert "Não tenho o endereço" in texts(gw)[-2]  # campo ausente não é inventado


# ------------------------------------------------------------------------------------------ 3. identificação
def test_numero_fora_do_formato_pede_correcao_e_valido_ausente_nao_e_localizado(env):
    make, ops, _ = env
    h, gw = make()
    say(h, flow.OPT_ID_NUMBER.title)
    say(h, "6080680 32 2025")
    assert "20 dígitos" in texts(gw)[-1] and ops.get(KEY).awaiting == "process"
    absent = cnj("6099999", "20258030001")
    say(h, absent)
    assert "não encontrei esse número" in texts(gw)[-1] and "PJe" in texts(gw)[-1]
    assert ops.get(KEY).process_number is None and ops.get(KEY).unit_label is None


def test_cpf_invalido_nao_busca_e_valido_nao_gera_resultado_nem_vaza(env, caplog):
    make, ops, llm = env
    spy = SpySearch()
    h, gw = make(search=spy)
    caplog.set_level(logging.DEBUG)
    say(h, flow.OPT_ID_CPF.title)
    say(h, "529.982.247-24")
    assert spy.calls == [] and "não conferem" in texts(gw)[-1]
    say(h, CPF_OK)
    assert spy.calls == ["cpf"] and "não está disponível" in texts(gw)[-1] and llm.prompts == []
    everything = "".join(texts(gw)) + "".join(n for _, n in gw.notes) + json.dumps(ops.audit_rows(KEY)) + caplog.text
    assert "52998224725" not in everything and CPF_OK not in everything


def test_busca_por_nome_exige_selecao_explicita_e_seleciona_exatamente_o_item(env):
    make, ops, _ = env
    h, gw = make()
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Maria")
    assert "nome completo" in texts(gw)[-1]
    say(h, "Maria Exemplo Silva")
    reply = texts(gw)[-1]
    assert P1 in reply and P2 in reply and "participação: autor" in reply and "participação: reu" in reply
    assert "TJPA" in reply and last_opts(gw)[:2] == ["Opção 1", "Opção 2"] and ops.get(KEY).process_number is None
    order = [c["process_number"] for c in json.loads(ops.get(KEY).choices)]
    say(h, "Opção 2")
    assert ops.get(KEY).process_number == order[1] and ops.get(KEY).choices is None


def test_nome_sem_identificacao_segura_pergunta_a_vara_e_nao_presume_processo(env):
    make, ops, _ = env
    h, gw = make(units=unit_dir())
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Pessoa Inexistente Total")
    assert "Vara" in texts(gw)[-1] and last_opts(gw)[:2] == [flow.OPT_VARA_YES.title, flow.OPT_VARA_NO.title]
    say(h, flow.OPT_VARA_YES.title)
    say(h, "Cidade Teste, Unidade de Teste")
    st = ops.get(KEY)
    assert st.process_number is None and st.unit_id == "ut" and "não foi localizado" in texts(gw)[-2]
    assert "Como deseja ser atendido pela *Unidade de Teste*" in texts(gw)[-1]


def test_muitos_resultados_pedem_dado_adicional(env):
    make, ops, _ = env
    from app.domain.ports import ProcessCandidate
    many = [ProcessCandidate(f"000000{i}-00.2026.8.03.0001", "X", "autor", "Vara Única de Lugar" if i == 3 else "Outra Vara") for i in range(7)]
    h, gw = make(search=SpySearch(many))
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Nome Comum")
    assert "muitos processos" in texts(gw)[-1] and ops.get(KEY).awaiting == "name_refine"
    say(h, "Lugar")
    assert last_opts(gw)[:1] == ["Opção 1"] and "0000003-00" in texts(gw)[-1] and "0000004-00" not in texts(gw)[-1]


def test_vara_desconhecida_sem_telejudiciario_configurado_e_honesto(env):
    make, _, _ = env
    h, gw = make()
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Ninguem Daqui")
    say(h, flow.OPT_VARA_NO.title)
    assert "não tenho o contato do TeleJudiciário" in texts(gw)[-1]


# ------------------------------------------------------------------------------------- 4 a 6. diligência e canais
def test_diligencia_vazia_nao_avanca_e_texto_e_preservado_byte_a_byte_ate_a_equipe(env, caplog):
    make, ops, _ = env
    h, gw = make()
    caplog.set_level(logging.DEBUG)
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    assert "Descreva a diligência" in texts(gw)[-1]
    process(h, msg_event("   ", msg_id=501))
    assert "Não recebi o texto" in texts(gw)[-1] and ops.get(KEY).diligence is None and ops.get(KEY).awaiting == "diligence"
    process(h, msg_event(DILIGENCE, msg_id=502))
    st = ops.get(KEY)
    assert st.diligence == DILIGENCE and st.request_reason == "diligencia"
    assert last_opts(gw) == [flow.OPT_CH_WHATSAPP.title, flow.OPT_CH_BALCAO.title, flow.OPT_MENU.title] and gw.assigned == []  # nada enviado ainda
    say(h, flow.OPT_CH_WHATSAPP.title)
    note = [n for _, n in gw.notes if "SOLICITAÇÃO DE ATENDIMENTO HUMANO" in n]
    assert len(note) == 1 and DILIGENCE in note[0] and P1 in note[0] and "Unidade de Teste" in note[0] and "advogado(a)" in note[0]
    assert "OAB" not in note[0]  # campo não fornecido não aparece
    assert gw.assigned == [(77, "secretaria")] and ops.get(KEY).handoff_state is S.HUMAN_ACTIVE
    assert "não significa que a diligência" in texts(gw)[-1]
    audit = json.dumps(ops.audit_rows(KEY), ensure_ascii=False)
    assert "substabelecimento" not in audit and "substabelecimento" not in caplog.text


def test_diligencia_dirigida_ao_juiz_vai_para_o_gabinete(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    say(h, "Peço que o pedido de liminar seja levado ao juiz com urgência.")
    say(h, flow.OPT_CH_WHATSAPP.title)
    assert gw.assigned == [(77, "gabinete")]


def test_balcao_virtual_mostra_link_validado_declara_pagina_geral_e_nao_afirma_recebimento(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=601))
    say(h, flow.OPT_CH_BALCAO.title)
    all_text = "\n".join(texts(gw))
    assert GENERAL_URL in all_text and "endereço geral" in all_text and DILIGENCE in texts(gw)  # mensagem só com o texto, para copiar
    assert gw.assigned == [] and gw.notes[-1][1].startswith("🎫")  # nenhuma transferência nem nota: só o link
    assert not any(w in all_text.lower() for w in ("atendente recebeu", "foi recebida", "solicitação enviada"))
    st = ops.get(KEY)
    assert st.handoff_state is S.BOT_ACTIVE and st.diligence == DILIGENCE and last_opts(gw) == [o.title for o in flow.continue_options(True)]


def test_balcao_usa_o_link_direto_da_unidade_quando_cadastrado(env):
    make, _, _ = env
    h, gw = make(units=unit_dir(balcao_url=UNIT_URL, hours="8h às 14h"))
    lawyer_with_process(h)
    say(h, flow.OPT_HUMAN_LIST.title)
    say(h, flow.OPT_CH_BALCAO.title)
    reply = [t for t in texts(gw) if UNIT_URL in t][0]
    assert "endereço geral" not in reply and "8h às 14h" in reply


def test_sem_link_do_balcao_nem_canal_o_retorno_e_honesto(env):
    make, _, _ = env
    h, gw = make(units=UnitDirectory(), whatsapp_mode="link")  # sem cadastro: nenhum canal disponível
    lawyer_with_process(h)
    say(h, flow.OPT_HUMAN_LIST.title)
    assert "não tenho um canal" in texts(gw)[-1] and "https://" not in texts(gw)[-1] and gw.assigned == []


def test_modo_link_do_whatsapp_nao_confirma_envio_e_nao_transfere(env):
    make, ops, _ = env
    h, gw = make(units=unit_dir(whatsapp="5591988887777"), whatsapp_mode="link")
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=701))
    say(h, flow.OPT_CH_WHATSAPP.title)
    reply = [t for t in texts(gw) if "wa.me/5591988887777" in t][0]
    assert "toque em enviar" in reply and "Apenas abrir a conversa não envia" in reply
    assert gw.assigned == [] and ops.get(KEY).handoff_state is S.BOT_ACTIVE and "encaminhada" not in reply


def test_falha_no_canal_preserva_o_texto_oferece_opcoes_e_troca_sem_duplicar(env):
    make, ops, _ = env
    gw = FakeGateway(fail_assign=1)
    h, _ = make(units=unit_dir(phone="559132050000"), gw=gw)
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=801))
    say(h, flow.OPT_CH_WHATSAPP.title)
    assert "Não consegui confirmar o envio" in texts(gw)[-1] and "encaminhada" not in texts(gw)[-1]
    assert last_opts(gw) == [flow.OPT_RETRY.title, flow.OPT_OTHER_CHANNEL.title, flow.OPT_UNIT_PHONE.title]
    assert ops.get(KEY).diligence == DILIGENCE and ops.get(KEY).handoff_state is S.HANDOFF_REQUESTED
    say(h, flow.OPT_UNIT_PHONE.title)
    assert "(91) 3205-0000" in texts(gw)[-1]
    say(h, flow.OPT_OTHER_CHANNEL.title)
    st = ops.get(KEY)
    assert st.handoff_state is S.AUTOMATION_RESUMED and st.diligence == DILIGENCE
    assert last_opts(gw) == [flow.OPT_CH_BALCAO.title, flow.OPT_MENU.title]  # só o outro canal
    say(h, flow.OPT_CH_BALCAO.title)
    assert gw.assigned == [] and DILIGENCE in texts(gw)  # nada foi aos dois canais
    assert sum("SOLICITAÇÃO DE ATENDIMENTO HUMANO" in n for _, n in gw.notes) == 1


def test_tentar_novamente_nao_duplica_a_nota_de_contexto(env):
    make, ops, _ = env
    gw = FakeGateway(fail_assign=1)
    h, _ = make(gw=gw)
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=901))
    say(h, flow.OPT_CH_WHATSAPP.title)
    say(h, flow.OPT_RETRY.title)
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and gw.assigned == [(77, "secretaria")]
    assert sum("SOLICITAÇÃO DE ATENDIMENTO HUMANO" in n for _, n in gw.notes) == 1


def test_reentrega_do_mesmo_evento_nao_duplica_solicitacao(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=1001))
    click = msg_event(flow.OPT_CH_WHATSAPP.title, msg_id=1002)
    process(h, click)
    assert process(h, click) == "duplicate"
    say(h, flow.OPT_CH_WHATSAPP.title)  # novo toque com o atendimento humano ativo: o bot fica em silêncio
    assert gw.assigned == [(77, "secretaria")] and sum("SOLICITAÇÃO" in n for _, n in gw.notes) == 1


def test_parte_pede_providencia_sem_campo_livre_e_o_motivo_vai_na_nota(env):
    make, ops, _ = env
    h, gw = make()
    say(h, flow.OPT_PARTE.title)
    say(h, P1)
    say(h, flow.OPT_PROVIDENCE.title)
    assert ops.get(KEY).awaiting == "channel" and ops.get(KEY).diligence is None
    say(h, flow.OPT_CH_WHATSAPP.title)
    note = [n for _, n in gw.notes if "SOLICITAÇÃO" in n][0]
    assert "Motivo do atendimento: solicitação de providência" in note and "parte ou testemunha" in note


def test_atendente_sem_unidade_identificada_mantem_a_transferencia_direta(env):
    make, ops, _ = env
    h, gw = make()
    say(h, "Olá")
    say(h, flow.OPT_HUMAN_LIST.title)
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and not [n for _, n in gw.notes if "SOLICITAÇÃO" in n]


# ----------------------------------------------------------------------------------------- 7. estado e encerramento
def test_humano_ativo_bloqueia_o_bot_e_o_encerramento_apaga_a_diligencia(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=1101))
    say(h, flow.OPT_CH_WHATSAPP.title)
    n = len(gw.sent)
    assert say(h, "alô?").outcome == "silent" and len(gw.sent) == n
    process(h, msg_event("/encerrar", msg_id=1102, mtype="outgoing", sender_type="user", private=True))
    st = ops.get(KEY)
    assert st.diligence is None and st.unit_label is None and not st.profile_locked and st.handoff_state is S.HUMAN_CLOSED
    assert "não é protocolo de processo judicial" in texts(gw)[0].lower()


def test_menus_do_roteiro_respeitam_os_limites_do_whatsapp():
    buttons = [flow.ADV_MENU, flow.VARA_OPTIONS, [flow.OPT_CH_WHATSAPP, flow.OPT_CH_BALCAO, flow.OPT_MENU],
               [flow.OPT_RETRY, flow.OPT_OTHER_CHANNEL, flow.OPT_UNIT_PHONE], [flow.OPT_ID_NUMBER, flow.OPT_ID_NONE, flow.OPT_HUMAN]]
    for group in buttons:
        assert len(group) <= 3 and all(len(o.title) <= 20 for o in group), [o.title for o in group]
    lists = [flow.PROFILE_MENU, flow.GERAL_MENU, flow.ID_MENU, flow.process_menu(Profile.ADVOGADO), flow.process_menu(Profile.PARTE_TESTEMUNHA),
             flow.continue_options(True), flow.PICK_OPTIONS + [flow.OPT_MENU]]
    for group in lists:
        assert len(group) <= 10 and all(len(o.title) <= 24 for o in group), [o.title for o in group if len(o.title) > 24]
    titles = [o.title for g in buttons + lists for o in g]
    for t in titles:  # todo título volta como a mesma ação (acentos e emojis não atrapalham o reconhecimento)
        assert flow.parse(t, []).kind is not flow.Kind.FREE, t


def test_resolver_pela_interface_do_chatwoot_tambem_apaga_a_diligencia(env):
    make, ops, _ = env
    h, gw = make()
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=1201))
    say(h, flow.OPT_CH_WHATSAPP.title)
    process(h, {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": 9.5})
    st = ops.get(KEY)
    assert st.handoff_state is S.HUMAN_CLOSED and st.diligence is None and st.process_number is None


# ------------------------------------------------------------------ correções da verificação independente
def _failed_whatsapp(make, **kw):
    gw = FakeGateway(fail_assign=1)
    h, _ = make(gw=gw, **kw)
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=2001))
    say(h, flow.OPT_CH_WHATSAPP.title)
    assert last_opts(gw)[0] == flow.OPT_RETRY.title
    return h, gw


def test_menu_depois_da_falha_cancela_o_pedido_em_vez_de_transferir(env):
    make, ops, _ = env
    h, gw = _failed_whatsapp(make)
    say(h, flow.OPT_MENU.title)
    st = ops.get(KEY)
    assert gw.assigned == [] and st.handoff_state is S.AUTOMATION_RESUMED and st.request_channel is None and st.diligence == DILIGENCE
    assert "Sua conversa foi encaminhada" not in "".join(texts(gw))
    assert any("CANCELADO" in n for _, n in gw.notes)  # a equipe vê que a nota de contexto não vale mais
    assert any("opção diferente" in a["detail"] for a in ops.audit_rows(KEY))


def test_balcao_tocado_depois_da_falha_e_a_escolha_do_canal_e_nao_transfere(env):
    make, ops, _ = env
    h, gw = _failed_whatsapp(make)
    say(h, flow.OPT_CH_BALCAO.title)
    assert gw.assigned == [] and GENERAL_URL in "\n".join(texts(gw)) and DILIGENCE in texts(gw)
    assert ops.get(KEY).handoff_state is S.AUTOMATION_RESUMED


def test_troca_de_canal_apos_falha_e_auditada_e_avisa_a_equipe(env):
    make, ops, _ = env
    h, gw = _failed_whatsapp(make)
    say(h, flow.OPT_OTHER_CHANNEL.title)
    assert any("CANCELADO" in n for _, n in gw.notes)
    assert any("troca de canal após falha (whatsapp)" in a["detail"] for a in ops.audit_rows(KEY))


def test_texto_digitado_apos_a_falha_ainda_repete_a_transferencia_automatica(env):
    make, ops, _ = env
    h, gw = _failed_whatsapp(make)
    say(h, "pode tentar de novo?")
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and gw.assigned == [(77, "secretaria")]


def test_unidade_nao_reconhecida_consulta_a_base_institucional_e_oferece_continuidade(env):
    make, ops, _ = env
    tele = "TeleJudiciário de teste: contato fictício apenas neste teste"
    units = unit_dir()
    units.contacts = InstitutionalContacts(balcao_general_url=GENERAL_URL, telejudiciario=tele)
    h, gw = make(units)
    seen = []
    original = h.orch.respond
    h.orch.respond = lambda msg, st: (seen.append(msg), original(msg, st))[1]
    say(h, flow.OPT_GERAL.title)
    say(h, flow.OPT_G_ADDRESS.title)
    say(h, "Lugar Que Não Existe")
    assert seen == [flow.GERAL_FALLBACK_QUESTIONS["endereco"]]
    assert any(tele in t for t in texts(gw)) and last_opts(gw) == [o.title for o in flow.continue_options(False)]


def test_resposta_geral_pela_base_institucional_termina_no_menu_de_continuidade(env):
    make, _, _ = env
    h, gw = make()
    say(h, flow.OPT_GERAL.title)
    say(h, flow.OPT_G_HOURS.title)
    assert last_opts(gw) == [o.title for o in flow.continue_options(False)] and "Como deseja continuar" in texts(gw)[-1]


def test_nome_buscado_nao_aparece_em_log_nem_auditoria(env, caplog):
    make, ops, _ = env
    h, gw = make()
    caplog.set_level(logging.DEBUG)
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Maria Exemplo Silva")
    blob = json.dumps(ops.audit_rows(KEY), ensure_ascii=False) + caplog.text
    assert "Maria" not in blob and "Silva" not in blob and "name_search" in blob


def test_cpf_digitado_em_pergunta_livre_nao_chega_ao_llm(env):
    make, _, llm = env
    h, gw = make()
    say(h, f"Qual a audiência do processo {P1}? Meu CPF é {CPF_OK}")
    assert llm.prompts and all(CPF_OK not in u and "52998224725" not in u for _, u in llm.prompts)


def test_diligencia_acima_do_limite_pede_versao_menor_sem_truncar(env):
    make, ops, _ = env
    h, gw = make(max_diligence_chars=50)
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event("x" * 51, msg_id=3001))
    assert "passou de 50 caracteres" in texts(gw)[-1] and ops.get(KEY).diligence is None and ops.get(KEY).awaiting == "diligence"
    process(h, msg_event("x" * 50, msg_id=3002))
    assert ops.get(KEY).diligence == "x" * 50


def test_numero_com_digitos_verificadores_errados_pede_correcao(env):
    make, ops, _ = env
    h, gw = make()
    say(h, flow.OPT_ID_NUMBER.title)
    say(h, "6099999-00.2025.8.03.0001")  # dígitos verificadores não conferem
    assert "dígitos de verificação" in texts(gw)[-1] and "não consulto" not in texts(gw)[-1].lower() and ops.get(KEY).process_number is None


def test_audiencias_e_situacao_perguntam_ao_acervo_com_aviso_de_demonstracao(env):
    make, ops, llm = env
    h, gw = make()
    say(h, flow.OPT_PARTE.title)
    say(h, P1)
    seen = []
    original = h.orch.respond
    h.orch.respond = lambda msg, st: (seen.append(msg), original(msg, st))[1]
    say(h, flow.OPT_HEARINGS.title)
    assert "20/07/2026" in texts(gw)[-2] and "não é consulta em tempo real" in texts(gw)[-2] and "Há audiência designada" in seen[0]
    say(h, flow.OPT_STATUS_SIMPLE.title)
    assert "situação atual" in seen[1] and P1 in seen[1]


def test_menu_do_processo_traz_os_itens_do_roteiro(env):
    titles = [o.title for o in flow.process_menu(Profile.ADVOGADO)]
    for needed in ("🗓️ Audiências", "📌 Situação atual", "📝 Pedir diligência", "🔎 Fazer pergunta", "🙋 Falar com atendente"):
        assert needed in titles
    simple = [o.title for o in flow.process_menu(Profile.PARTE_TESTEMUNHA)]
    assert "📌 Como está o processo" in simple and "📝 Pedir providência" in simple


def test_perfil_nao_altera_equipe_nem_evidencia(env):
    make, ops, llm = env
    teams = []
    for profile in (flow.OPT_PARTE, flow.OPT_ADV):
        h, gw = make()
        say(h, flow.OPT_SWITCH_PROFILE.title)
        say(h, profile.title)
        say(h, P1)
        say(h, flow.OPT_HEARINGS.title)
        say(h, flow.OPT_HUMAN_LIST.title)
        say(h, flow.OPT_CH_WHATSAPP.title)
        teams.append(gw.assigned[-1][1])
        process(h, {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": float(len(teams))})
    evidences = [u.split("EVIDÊNCIAS:")[1].split("PERGUNTA")[0] for _, u in llm.prompts[-2:]]
    assert teams == ["secretaria", "secretaria"] and evidences[0] == evidences[1]


def test_link_do_whatsapp_leva_o_contexto_e_a_diligencia_e_sem_numero_e_honesto(env):
    from urllib.parse import parse_qs, urlparse
    make, ops, _ = env
    h, gw = make(units=unit_dir(whatsapp="5591988887777"), whatsapp_mode="link")
    lawyer_with_process(h)
    say(h, flow.OPT_DILIGENCE.title)
    process(h, msg_event(DILIGENCE, msg_id=4001))
    say(h, flow.OPT_CH_WHATSAPP.title)
    link = [t for t in texts(gw) if "wa.me/" in t][0].split("\n")[-1]
    body = parse_qs(urlparse(link).query)["text"][0]
    assert P1 in body and DILIGENCE in body
    h2, gw2 = make(units=unit_dir(), whatsapp_mode="link")  # unidade sem WhatsApp: canal não é oferecido
    lawyer_with_process(h2)
    say(h2, flow.OPT_HUMAN_LIST.title)
    assert flow.OPT_CH_WHATSAPP.title not in last_opts(gw2) and flow.OPT_CH_BALCAO.title in last_opts(gw2) and gw2.assigned == []


def test_horario_nao_cadastrado_nao_aparece_no_balcao_e_telejudiciario_so_se_configurado(env):
    make, _, _ = env
    h, gw = make(units=unit_dir(balcao_url=UNIT_URL))
    lawyer_with_process(h)
    say(h, flow.OPT_HUMAN_LIST.title)
    say(h, flow.OPT_CH_BALCAO.title)
    assert "Horário de atendimento" not in [t for t in texts(gw) if UNIT_URL in t][0]
    h2, gw2 = make()
    say(h2, flow.OPT_ID_NONE.title)
    say(h2, "Ninguem Daqui")
    say(h2, flow.OPT_VARA_NO.title)
    assert "TeleJudiciário:" not in texts(gw2)[-1]
    units = unit_dir()
    units.contacts = InstitutionalContacts(balcao_general_url=GENERAL_URL, telejudiciario="contato de teste")
    h3, gw3 = make(units=units)
    say(h3, flow.OPT_ID_NONE.title)
    say(h3, "Ninguem Daqui")
    say(h3, flow.OPT_VARA_NO.title)
    assert "contato de teste" in texts(gw3)[-1]


def test_selecao_pelo_numero_digitado_e_opcao_invalida(env):
    make, ops, _ = env
    h, gw = make()
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Maria Exemplo Silva")
    say(h, "9")
    assert "Não consegui identificar a opção" in texts(gw)[-1] and ops.get(KEY).process_number is None
    first = json.loads(ops.get(KEY).choices)[0]["process_number"]
    say(h, "1")
    assert ops.get(KEY).process_number == first


def test_textos_do_handler_e_do_orquestrador_tambem_sem_jargao(env):
    from app.application.answering.orchestrator import Orchestrator
    from app.application.chat import handler as hmod
    public = [v for k, v in vars(hmod).items() if k.endswith("_TEXT") and isinstance(v, str)]
    public += [Orchestrator._greeting(), Orchestrator._suggest_handoff_text(), Orchestrator._technical_text()]
    public += [Orchestrator._abstain_text(d) for d in KnowledgeDomain]
    for text in public:
        low = text.lower()
        assert not any(w in low for w in ("rag", "handoff", "pipeline", "orquestrador", "abstenção", "ticket", "entrada inválida")), text
    assert "Júlia" in Orchestrator._greeting()


def test_refinamento_ainda_ambiguo_cai_na_pergunta_da_vara(env):
    make, ops, _ = env
    from app.domain.ports import ProcessCandidate
    many = [ProcessCandidate(f"000000{i}-00.2026.8.03.0001", "X", "autor", "Vara Comum de Lugar") for i in range(7)]
    h, gw = make(search=SpySearch(many))
    say(h, flow.OPT_ID_NONE.title)
    say(h, "Nome Comum")
    say(h, "Lugar")  # todas continuam compatíveis: não escolhe nenhuma
    assert "Você sabe em qual *Vara*" in texts(gw)[-1] and ops.get(KEY).process_number is None and ops.get(KEY).choices is None


def test_audiencias_sem_evidencia_declara_a_limitacao_e_nao_inventa(store, embedder, ops):
    add_doc(store, embedder, doc_id="d9", text="Certifico o decurso de prazo.", doc_type="Certidão", title="Certidão")
    add_doc(store, embedder, doc_id=f"proc-{P1}:capa", text=f"Ficha do processo {P1}.", doc_type="Capa", title="Capa", pje_doc_id=None)
    store.process_info = lambda n: {"process_number": n, "process_class": "X", "court_unit": "Y", "parties": []}
    llm = FakeLLM(json.dumps({"resposta": "Não há audiência nos trechos.", "referencias": [], "suficiente": False, "encaminhar": False}))
    gw = FakeGateway()
    h = ChatwootEventHandler(build(store, embedder, llm), ops, gw, rich_flow=True)
    say(h, flow.OPT_PARTE.title)
    say(h, P1)
    say(h, flow.OPT_HEARINGS.title)
    reply = texts(gw)[-2]
    assert "Não encontrei" in reply and "20/07" not in reply and not any(c in reply.lower() for c in ("audiência marcada", "foi designada"))
