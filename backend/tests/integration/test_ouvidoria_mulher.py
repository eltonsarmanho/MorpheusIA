"""Ouvidoria da Mulher no WhatsApp (OVM-01..OVM-26): acolhimento, orientações, triagem, segurança e encaminhamento.

Sem rede: Chatwoot e LLM falsos. Cenários sintéticos, só de desenvolvimento; não validam o atendimento real.
"""
import logging
from datetime import datetime, timezone

from app.application.chat import flow
from app.application.chat import mulher as ovm
from app.application.chat.handler import MulherSettings
from app.domain.handoff import HandoffState as S
from app.domain.triage_mulher import OuvidoriaMulher
from tests.conftest import FakeGateway
from tests.integration.test_chatwoot_handler import KEY, msg_event, process
from tests.integration.test_roteiro_tjpa import cnj, env, last_opts, say, texts  # noqa: F401  (fixture `env`)

CONTACTS = OuvidoriaMulher(phone="(91) 3289-7182", email="ouvidoria.mulher@tjpa.jus.br", source="Carta de Serviços do TJPA")
NOON_BELEM = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)  # 12h em Belém: aberta
EVENING_BELEM = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)  # 17h em Belém: fechada
SECRET = "RELATO-SECRETO-XYZ"
VALID = cnj("0000001", "20268140001")  # número CNJ com dígitos verificadores corretos


class AttrGateway(FakeGateway):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.attrs: list[dict] = []
        self.events: list[str] = []

    def send_message(self, account_id, conversation_id, content, options=None, private=False):
        if private and "OUVIDORIA DA MULHER" in content:
            self.events.append("nota")
        return super().send_message(account_id, conversation_id, content, options, private)

    def assign_team(self, account_id, conversation_id, team_name):
        self.events.append("equipe")
        return super().assign_team(account_id, conversation_id, team_name)

    def set_custom_attributes(self, account_id, conversation_id, attributes):
        self.attrs.append(attributes)


def mk(env, enabled=True, now=NOON_BELEM, gw=None):
    make, ops, _ = env
    h, gw = make(gw=gw or AttrGateway(), mulher=MulherSettings(enabled=enabled, contacts=CONTACTS))
    h.now = lambda: now
    return h, gw, ops


def start(h):
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    say(h, ovm.OPT_TEAM.title)


def title(field, value):
    return ovm.ANSWERS[(field, value)].title


def run(h, *, perigo="nao", para="propria", assunto="psicologica", ambiente="parceiro_ex", processo="nao", contato="sim", relato=None):
    start(h)
    say(h, title("perigo", perigo))
    say(h, title("para_quem", para))
    say(h, title("assunto", assunto))
    say(h, title("ambiente", ambiente))
    say(h, title("processo", processo))
    if processo == "numero":
        say(h, VALID)
    say(h, title("contato", contato))
    if relato is None:
        say(h, ovm.OPT_SKIP_STEP.title)
    else:
        say(h, relato)


def note(gw):
    return next(t for _, t in gw.notes if "OUVIDORIA DA MULHER" in t)


# ----------------------------------------------------------------------------------------- entrada
def test_chave_desligada_menu_identico_e_opcao_vira_texto_livre(env):  # OVM-01
    h, gw, ops = mk(env, enabled=False)
    say(h, "Olá")
    assert last_opts(gw) == [o.title for o in flow.PROFILE_MENU] and ovm.OPT_ENTRY.title not in last_opts(gw)
    say(h, ovm.OPT_ENTRY.title)
    assert "190" not in texts(gw)[-1] and ops.get(KEY).triage is None


def test_chave_ligada_coloca_a_ouvidoria_na_linha_4_do_menu(env):  # OVM-01
    h, gw, _ = mk(env)
    say(h, "Olá")
    opts = last_opts(gw)
    assert opts[3] == ovm.OPT_ENTRY.title and len(opts) == 6 and opts[4] == flow.OPT_HUMAN_LIST.title


def test_acolhimento_cita_190_e_180_e_oferece_tres_caminhos(env):  # OVM-02
    h, gw, _ = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    assert "190" in texts(gw)[-1] and "180" in texts(gw)[-1]
    assert last_opts(gw) == [o.title for o in ovm.ENTRY_OPTIONS]


def test_relato_livre_de_violencia_oferece_sem_iniciar_a_triagem(env):  # OVM-03
    h, gw, ops = mk(env)
    say(h, "meu marido me bate")
    assert "190" in texts(gw)[-1] and ovm.OPT_ENTRY.title in last_opts(gw)
    assert ops.get(KEY).triage is None and ops.get(KEY).awaiting is None


def test_pergunta_comum_nao_dispara_a_oferta(env):  # OVM-03
    h, gw, _ = mk(env)
    say(h, "Qual a situação do processo?")
    assert ovm.OFFER_TEXT not in texts(gw)


# ----------------------------------------------------------------------------------------- orientações
def test_orientacoes_listam_os_sete_temas_e_citam_a_pagina(env):  # OVM-04, OVM-05
    h, gw, _ = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ORIENT.title)
    opts = last_opts(gw)
    assert len(opts) <= 10 and all(o.title in opts for o, _ in ovm.TOPICS.values())
    say(h, ovm.TOPICS["penha"][0].title)
    assert "11.340/2006" in texts(gw)[-2] and "p. 15 e 16" in texts(gw)[-2]


def test_como_denunciar_ressalva_os_passos_de_trabalho(env):  # OVM-06
    h, gw, _ = mk(env)
    say(h, "Olá")
    say(h, ovm.TOPICS["denunciar"][0].title)
    body = texts(gw)[-2]
    assert "passos 2 e 5" in body and "assédio no trabalho" in body and "p. 21" in body and "180" in body


def test_telefones_uteis_vem_do_arquivo_com_fonte(env):  # OVM-07
    h, gw, _ = mk(env)
    say(h, "Olá")
    say(h, ovm.TOPICS["telefones"][0].title)
    body = texts(gw)[-2]
    assert "(91) 3289-7182" in body and "ouvidoria.mulher@tjpa.jus.br" in body and "8h às 14h" in body and "Carta de Serviços" in body
    assert "190" in body and "180" in body


def test_sem_contatos_cadastrados_nao_inventa_telefone(env):  # OVM-07
    make, ops, _ = env
    h, gw = make(gw=AttrGateway(), mulher=MulherSettings(enabled=True))
    say(h, "Olá")
    say(h, ovm.TOPICS["telefones"][0].title)
    assert "3289" not in texts(gw)[-2] and "@" not in texts(gw)[-2]


# ----------------------------------------------------------------------------------------- triagem
def test_triagem_pergunta_na_ordem_do_plano(env):  # OVM-08
    h, gw, _ = mk(env)
    start(h)
    asked = [texts(gw)[-1]]
    for field, value in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"),
                         ("contato", "sim")):
        say(h, title(field, value))
        asked.append(texts(gw)[-1])
    assert asked[0] == ovm.QUESTIONS["perigo"] and asked[1] == ovm.QUESTIONS["para_quem"] and asked[2] == ovm.QUESTIONS["assunto"]
    assert asked[3] == ovm.QUESTIONS["ambiente"] and asked[4] == ovm.QUESTIONS["processo"] and asked[5] == ovm.QUESTIONS["contato"]
    assert asked[6] == ovm.QUESTIONS["relato"]


def test_prefiro_nao_dizer_grava_nao_informado_e_segue(env):  # OVM-09
    h, gw, ops = mk(env)
    start(h)
    say(h, ovm.OPT_SKIP.title)
    assert texts(gw)[-1] == ovm.QUESTIONS["para_quem"]
    say(h, ovm.OPT_SKIP.title)
    say(h, title("assunto", "outro"))
    for _ in range(3):
        say(h, ovm.OPT_SKIP.title)
    say(h, ovm.OPT_SKIP_STEP.title)
    say(h, ovm.OPT_SEND.title)
    body = note(gw)
    assert "Urgência: não informado" in body and "Para quem: não informado" in body and "Contato por este WhatsApp: não informado" in body


def test_perigo_envia_190_e_180_antes_da_proxima_pergunta_e_marca_urgencia(env):  # OVM-10, OVM-18
    h, gw, _ = mk(env)
    start(h)
    say(h, title("perigo", "sim"))
    assert "190" in texts(gw)[-2] and texts(gw)[-1] == ovm.QUESTIONS["para_quem"]
    for f, v in (("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "parceiro_ex"), ("processo", "nao"), ("contato", "sim")):
        say(h, title(f, v))
    say(h, ovm.OPT_SKIP_STEP.title)
    say(h, ovm.OPT_SEND.title)
    assert "urgencia_imediata" in gw.labels[77] and "PERIGO IMEDIATO" in note(gw)


def test_botao_de_etapa_anterior_repete_a_pergunta_atual(env):  # OVM-11
    h, gw, ops = mk(env)
    start(h)
    say(h, title("perigo", "nao"))
    say(h, title("perigo", "sim"))  # botão antigo
    assert texts(gw)[-1].endswith(ovm.QUESTIONS["para_quem"]) and ops.get(KEY).triage.count('"perigo": "nao"') == 1


def test_numero_do_processo_e_validado_e_marcado_como_informado(env):  # OVM-12
    h, gw, ops = mk(env)
    start(h)
    for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "morosidade"), ("ambiente", "servico_publico")):
        say(h, title(f, v))
    say(h, title("processo", "numero"))
    assert texts(gw)[-1] == ovm.QUESTIONS["numero"]
    say(h, "abc")
    assert texts(gw)[-1] == ovm.NUMBER_BAD_TEXT and ops.get(KEY).awaiting == "mulher_numero"
    say(h, VALID)
    assert texts(gw)[-1] == ovm.QUESTIONS["contato"]
    say(h, title("contato", "sim"))
    say(h, ovm.OPT_SKIP_STEP.title)
    say(h, ovm.OPT_SEND.title)
    assert f"nº {VALID}, informado pela pessoa, não verificado" in note(gw)


def test_pular_o_numero_registra_processo_sem_numero(env):  # OVM-09, OVM-12
    h, gw, _ = mk(env)
    start(h)
    for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "numero")):
        say(h, title(f, v))
    say(h, ovm.OPT_SKIP_STEP.title)
    assert texts(gw)[-1] == ovm.QUESTIONS["contato"]


def test_relato_e_guardado_como_chegou_e_o_excesso_pede_versao_menor(env):  # OVM-13
    h, gw, ops = mk(env)
    start(h)
    for f, v in (("perigo", "nao"), ("para_quem", "terceiro"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"), ("contato", "sim")):
        say(h, title(f, v))
    say(h, "x" * 2001)
    assert "2000" in texts(gw)[-1] and ops.get(KEY).awaiting == "mulher_relato"
    relato = "  Ele me  empurrou\nontem 🙂 "
    say(h, relato)
    say(h, ovm.OPT_SEND.title)
    assert relato in note(gw) and "outra pessoa" in note(gw)


def test_falar_com_atendente_na_triagem_vai_ao_resumo(env):  # OVM-14
    h, gw, _ = mk(env)
    start(h)
    say(h, title("perigo", "nao"))
    say(h, flow.OPT_HUMAN_LIST.title)
    assert "resumo" in texts(gw)[-1].lower() and last_opts(gw)[0] == ovm.OPT_SEND.title


def test_refazer_recomeca_a_triagem(env):
    h, gw, _ = mk(env)
    run_to_summary = lambda: [say(h, title(f, v)) for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"), ("contato", "sim"))]
    start(h)
    run_to_summary()
    say(h, ovm.OPT_SKIP_STEP.title)
    say(h, ovm.OPT_REDO.title)
    assert texts(gw)[-1] == ovm.QUESTIONS["perigo"]


# ----------------------------------------------------------------------------------------- segurança
def test_contato_inseguro_orienta_e_registra_sem_retorno_por_este_whatsapp(env):  # OVM-16
    h, gw, ops = mk(env)
    run(h, contato="nao")
    assert any("não vai responder por este WhatsApp" in t and "(91) 3289-7182" in t and "180" in t for t in texts(gw))
    assert "sem retorno por este WhatsApp" in texts(gw)[-1]
    say(h, ovm.OPT_SEND.title)
    assert "NÃO RESPONDER POR ESTE WHATSAPP" in note(gw)
    final = texts(gw)[-1]
    assert "não vai responder por este WhatsApp" in final and "continuidade ao atendimento por aqui" not in final and "TKT-" in final
    assert gw.assigned[-1] == (77, "ouvidoria_mulher") and ops.get(KEY).handoff_state is S.HUMAN_ACTIVE


def test_prefere_telefone_tem_o_mesmo_tratamento(env):  # OVM-16
    h, gw, _ = mk(env)
    run(h, contato="telefone")
    say(h, ovm.OPT_SEND.title)
    assert "NÃO RESPONDER POR ESTE WHATSAPP" in note(gw)


def test_sair_ou_menu_descarta_a_triagem(env):  # OVM-17
    h, gw, ops = mk(env)
    start(h)
    say(h, title("perigo", "nao"))
    say(h, "sair")
    assert ops.get(KEY).triage is None and ovm.LEFT_TEXT in texts(gw)
    start(h)
    say(h, title("perigo", "nao"))
    say(h, flow.OPT_MENU.title)
    assert ops.get(KEY).triage is None and ovm.LEFT_TEXT in texts(gw)[-3:]


def test_texto_solto_em_etapa_de_botoes_repete_a_pergunta(env):
    h, gw, _ = mk(env)
    start(h)
    say(h, "qualquer coisa")
    assert texts(gw)[-1].endswith(ovm.QUESTIONS["perigo"])


def test_resposta_de_botao_sem_triagem_vira_texto_comum(env):
    h, gw, ops = mk(env)
    say(h, "Familiar")  # título de opção digitado fora da triagem
    assert ops.get(KEY).triage is None and ops.get(KEY).handoff_state is S.BOT_ACTIVE


# ----------------------------------------------------------------------------------------- encaminhamento
def test_encaminhamento_grava_nota_antes_da_equipe_e_orienta_guardar_o_protocolo(env):  # OVM-18, OVM-19, OVM-21
    h, gw, ops = mk(env)
    run(h, assunto="assedio_importunacao", ambiente="trabalho", relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    body = note(gw)
    assert "Assunto: assédio ou importunação" in body and "Onde ou com quem: trabalho" in body and SECRET in body
    assert "A pessoa foi orientada a guardar o protocolo para eventual atendimento presencial." in body and "TKT-" in body
    assert gw.assigned[-1] == (77, "ouvidoria_mulher") and {"humano", "ouvidoria_mulher"} <= set(gw.labels[77])
    final = texts(gw)[-1]
    assert "Guarde este número" in final and "atendimento presencial" in final and "TKT-" in final and "Ouvidoria da Mulher" in final
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and gw.statuses[-1] == (77, "open")


def test_atributos_da_conversa_so_levam_enumeracoes(env):
    h, gw, _ = mk(env)
    run(h, assunto="fisica", relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    assert gw.attrs and gw.attrs[-1]["assunto"] == "fisica" and gw.attrs[-1]["servico"] == "ouvidoria_mulher"
    assert SECRET not in str(gw.attrs)


def test_dentro_do_horario_informa_o_horario_sem_prometer_prazo(env):  # OVM-20
    h, gw, _ = mk(env, now=NOON_BELEM)
    run(h)
    say(h, ovm.OPT_SEND.title)
    final = texts(gw)[-1]
    assert "atende das 8h às 14h" in final and "prazo" not in final.lower() and "Fora do horário" not in note(gw)


def test_fora_do_horario_avisa_e_registra_na_nota(env):  # OVM-20
    h, gw, _ = mk(env, now=EVENING_BELEM)
    run(h)
    say(h, ovm.OPT_SEND.title)
    assert "fora do horário de atendimento (8h às 14h" in texts(gw)[-1] and "fora do horário da Ouvidoria" in note(gw)


def test_falha_na_transferencia_nao_afirma_encaminhamento_e_preserva_a_triagem(env):  # OVM-22
    h, gw, ops = mk(env, gw=AttrGateway(fail_assign=1))
    run(h, relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    last = texts(gw)[-1]
    assert "Não consegui confirmar" in last and "(91) 3289-7182" in last and "Guarde este número" not in " ".join(texts(gw)[-2:])
    assert last_opts(gw) == [flow.OPT_RETRY.title, flow.OPT_MENU.title]
    assert ops.get(KEY).handoff_state is S.HANDOFF_REQUESTED and SECRET in ops.get(KEY).triage
    say(h, flow.OPT_RETRY.title)
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and "Guarde este número" in texts(gw)[-1]
    assert "urgencia_imediata" not in gw.labels[77] and "ouvidoria_mulher" in gw.labels[77]


def test_nota_de_contexto_e_gravada_uma_vez_na_nova_tentativa(env):  # OVM-18
    h, gw, _ = mk(env, gw=AttrGateway(fail_assign=1))
    run(h)
    say(h, ovm.OPT_SEND.title)
    say(h, flow.OPT_RETRY.title)
    assert sum("OUVIDORIA DA MULHER" in t for _, t in gw.notes) == 1


# ----------------------------------------------------------------------------------------- privacidade e estado
def test_relato_e_processo_nao_aparecem_em_auditoria_nem_em_log(env, caplog):  # OVM-23
    caplog.set_level(logging.DEBUG)
    h, gw, ops = mk(env)
    run(h, processo="numero", relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    audit = " ".join(f"{r['action']} {r['detail']}" for r in ops.audit_rows(limit=500))
    assert SECRET not in audit and VALID not in audit and SECRET not in caplog.text and VALID not in caplog.text


def test_encerrar_o_protocolo_apaga_a_triagem(env):  # OVM-24
    h, gw, ops = mk(env)
    run(h, relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    assert SECRET in ops.get(KEY).triage
    process(h, msg_event("/encerrar", msg_id=99_001, mtype="outgoing", sender_type="user", private=True))
    st = ops.get(KEY)
    assert st.triage is None and st.request_reason is None


def test_sem_a_chave_nenhum_caminho_da_ouvidoria_e_acionavel(env):  # OVM-01
    h, gw, ops = mk(env, enabled=False)
    for t in (ovm.OPT_TEAM.title, ovm.OPT_DANGER.title, ovm.OPT_ORIENT.title, "meu marido me bate"):
        say(h, t)
    assert ops.get(KEY).triage is None and ovm.OFFER_TEXT not in texts(gw) and ovm.WELCOME_TEXT not in texts(gw)


# ----------------------------------------------------------------------------------------- lacunas apontadas pelo Verifier
def reach(h, *steps):
    start(h)
    for f, v in steps:
        say(h, title(f, v))


def test_sair_digitado_nas_etapas_de_texto_tambem_sai(env):  # OVM-17
    h, gw, ops = mk(env)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"), ("contato", "sim"))
    assert ops.get(KEY).awaiting == "mulher_relato"
    say(h, "sair")
    assert ops.get(KEY).triage is None and ops.get(KEY).awaiting is None and ovm.LEFT_TEXT in texts(gw)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "numero"))
    assert ops.get(KEY).awaiting == "mulher_numero"
    say(h, "sair")
    assert ops.get(KEY).triage is None and ops.get(KEY).awaiting is None


def test_chave_desligada_nao_oferece_nem_le_triagem_guardada(env):  # OVM-01
    h, gw, ops = mk(env, enabled=False)
    say(h, "meu marido me bate")
    assert ovm.OFFER_TEXT not in texts(gw)
    st = ops.get(KEY)
    from app.domain.triage_mulher import Triage
    st.triage = Triage(step="assunto", perigo="nao").to_json()  # chave desligada com a triagem ainda guardada
    ops.save(st)
    n = len(texts(gw))
    say(h, "alguma coisa")
    assert all(ovm.QUESTIONS["assunto"] not in t for t in texts(gw)[n:])


def test_botao_perigo_agora_envia_190_180_e_marca_perigo(env):  # OVM-10
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    say(h, ovm.OPT_DANGER.title)
    assert texts(gw)[-2] == ovm.EMERGENCY_TEXT and last_opts(gw) == [o.title for o in ovm.AFTER_EMERGENCY]
    from app.domain.triage_mulher import Triage
    assert Triage.from_json(ops.get(KEY).triage).perigo == "sim"
    say(h, ovm.OPT_TEAM.title)
    assert texts(gw)[-1] == ovm.QUESTIONS["para_quem"]  # perigo já respondido; não pergunta de novo


def test_perigo_agora_no_meio_da_triagem_nao_volta_etapa(env):  # OVM-10
    h, gw, ops = mk(env)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"))
    say(h, ovm.OPT_DANGER.title)
    from app.domain.triage_mulher import Triage
    t = Triage.from_json(ops.get(KEY).triage)
    assert t.step == "ambiente" and t.perigo == "sim"


def test_encaminhar_fora_do_resumo_nao_encaminha(env):  # consentimento (OVM-08)
    h, gw, ops = mk(env)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"))
    say(h, ovm.OPT_SEND.title)
    assert gw.assigned == [] and ops.get(KEY).handoff_state is S.BOT_ACTIVE and texts(gw)[-1].endswith(ovm.QUESTIONS["assunto"])


def test_numero_com_digitos_verificadores_errados_e_recusado(env):  # OVM-12
    h, gw, ops = mk(env)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "morosidade"), ("ambiente", "servico_publico"), ("processo", "numero"))
    bad = VALID[:8] + ("00" if VALID[8:10] != "00" else "01") + VALID[10:]
    say(h, bad)
    assert texts(gw)[-1] == ovm.NUMBER_BAD_TEXT and ops.get(KEY).awaiting == "mulher_numero"


def test_nao_informado_e_gravado_no_estado(env):  # OVM-09
    h, gw, ops = mk(env)
    start(h)
    say(h, ovm.OPT_SKIP.title)
    from app.domain.triage_mulher import Triage
    assert Triage.from_json(ops.get(KEY).triage).perigo == "nao_informado"


def test_contato_prefiro_nao_dizer_e_tratado_como_nao_seguro(env):  # OVM-16
    h, gw, _ = mk(env)
    start(h)
    for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao")):
        say(h, title(f, v))
    say(h, ovm.OPT_SKIP.title)
    say(h, ovm.OPT_SKIP_STEP.title)
    assert "sem retorno por este WhatsApp" in texts(gw)[-1] and "(91) 3289-7182" in texts(gw)[-1]
    say(h, ovm.OPT_SEND.title)
    assert "NÃO RESPONDER POR ESTE WHATSAPP" in note(gw) and "continuidade ao atendimento por aqui" not in texts(gw)[-1]


def test_nota_e_gravada_antes_de_atribuir_a_equipe(env):  # OVM-18
    h, gw, _ = mk(env)
    run(h)
    say(h, ovm.OPT_SEND.title)
    assert gw.events == ["nota", "equipe"]


def test_resumo_lista_o_que_foi_informado_sem_o_relato(env):  # OVM-14
    h, gw, _ = mk(env)
    run(h, assunto="moral", ambiente="familiar", relato=SECRET)
    summary = texts(gw)[-1]
    assert "violência moral" in summary and "familiar" in summary and "você escreveu um relato" in summary and SECRET not in summary


def test_nota_traz_data_e_hora_e_nao_promete_prazo_fora_do_horario(env):  # OVM-20, OVM-21
    h, gw, _ = mk(env, now=EVENING_BELEM)
    run(h)
    say(h, ovm.OPT_SEND.title)
    assert "Data e horário: 04/11/2026 17:00 (Belém)" in note(gw) and "prazo" not in texts(gw)[-1].lower()


def test_orientacoes_nao_dao_conselho_juridico_individual(env):  # OVM-05
    from app.application.chat import mulher as m
    for topic in m.TOPICS:
        if topic != "telefones":
            text = m.topic_text(topic).lower()
            assert "você tem direito" not in text and "você deve processar" not in text
    assert "cada caso é analisado" in m.topic_text("penha").lower()


def test_resolver_no_chatwoot_apaga_a_triagem(env):  # OVM-24
    h, gw, ops = mk(env)
    run(h, relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    ev = {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": "2026-11-04T18:00:00Z", "account": {"id": 1},
          "conversation": {"id": 77, "status": "resolved"}}
    process(h, ev)
    assert ops.get(KEY).triage is None


def test_encerramento_por_inatividade_apaga_a_triagem(env):  # OVM-24
    h, gw, ops = mk(env)
    run(h, relato=SECRET)
    say(h, ovm.OPT_SEND.title)
    closed = h.close_inactive(now=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert closed and ops.get(KEY).triage is None


def test_resumo_com_contato_seguro_nao_diz_sem_retorno(env):  # OVM-16
    h, gw, _ = mk(env)
    run(h, contato="sim", relato=SECRET)
    summary = texts(gw)[-1]
    assert "sem retorno" not in summary and "Ela atende das 8h às 14h" in summary and "Ao encaminhar" in summary
    say(h, ovm.OPT_SEND.title)
    assert "NÃO RESPONDER" not in note(gw) and "continuidade ao atendimento por aqui" in texts(gw)[-1]


def test_sair_ou_menu_nas_etapas_de_texto_volta_ao_menu(env):  # OVM-17
    for word in ("sair", "menu"):
        h, gw, ops = mk(env)
        reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"), ("contato", "sim"))
        say(h, word)
        assert ops.get(KEY).triage is None and ovm.LEFT_TEXT in texts(gw)
        assert last_opts(gw)[3] == ovm.OPT_ENTRY.title, word  # o menu principal voltou


def test_palavra_de_saudacao_sozinha_no_relato_e_guardada_e_a_saida_cita_190(env):  # OVM-17
    h, gw, ops = mk(env)
    reach(h, ("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "familiar"), ("processo", "nao"), ("contato", "sim"))
    say(h, "ajuda")
    from app.domain.triage_mulher import Triage
    t = Triage.from_json(ops.get(KEY).triage)
    assert t.relato == "ajuda" and t.step == "resumo"
    say(h, "sair")
    assert "190" in ovm.LEFT_TEXT and "180" in ovm.LEFT_TEXT and ovm.LEFT_TEXT in texts(gw)
