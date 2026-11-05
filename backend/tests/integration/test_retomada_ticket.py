"""Retomada por protocolo TKT e resumo do histórico para o atendente (RTK-01..RTK-16).

Sem rede: Chatwoot e LLM falsos. Cenários sintéticos de desenvolvimento; não validam o atendimento real.
"""
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.application.chat import flow
from app.application.chat import mulher as ovm
from app.application.chat import retomada
from app.application.chat.handler import MulherSettings, TicketSettings
from app.domain.handoff import HandoffState as S
from app.domain.triage_mulher import OuvidoriaMulher
from app.infrastructure.sqlite.operational_store import OperationalStore
from tests.conftest import FakeGateway
from tests.integration.test_chatwoot_handler import msg_event, process
from tests.integration.test_roteiro_tjpa import cnj, env, last_opts, texts, unit_dir  # noqa: F401  (fixture `env`)

BASE = "https://chat.example"
CONTACTS = OuvidoriaMulher(phone="(91) 3289-7182", email="ouvidoria.mulher@tjpa.jus.br", source="Carta de Serviços do TJPA")
SECRET = "DILIGENCIA-SECRETA-XYZ"
counter = {"n": 100_000}


class Gw(FakeGateway):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.msgs: dict[int, list[dict]] = {}
        self.events: list[str] = []

    def list_messages(self, account_id, conversation_id):
        return self.msgs.get(conversation_id, [])

    def send_message(self, account_id, conversation_id, content, options=None, private=False):
        if private and "ATENDIMENTO VIRTUAL" in content:
            self.events.append("nota")
        return super().send_message(account_id, conversation_id, content, options, private)

    def assign_team(self, account_id, conversation_id, team_name):
        self.events.append("equipe")
        return super().assign_team(account_id, conversation_id, team_name)


class SummLLM:
    def __init__(self, out):
        self.out, self.prompts = out, []

    def generate(self, *, system, user):
        self.prompts.append((system, user))
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def mk(env, *, ai=False, mulher=True, gw=None, units=None):
    make, ops, _ = env
    h, gw = make(units=units, gw=gw or Gw(), tickets=TicketSettings(enabled=True, ai_enabled=ai, base_url=BASE),
                 mulher=MulherSettings(enabled=mulher, contacts=CONTACTS))
    return h, gw, ops


def say(h, content, conv=77, contact=5, phone="+5591912345678", mtype="incoming", private=False, sender_type="contact"):
    counter["n"] += 1
    ev = msg_event(content, msg_id=counter["n"], conv=conv, mtype=mtype, private=private, sender_type=sender_type)
    ev["sender"] = {"type": sender_type, "id": contact, "phone_number": phone, "name": "Pessoa Teste"}
    return process(h, ev)


def agent(h, text, conv=77):
    return say(h, text, conv=conv, mtype="outgoing", private=True, sender_type="user", contact=900)


def key(conv):
    return f"1:{conv}"


def finished_case(h, ops, conv=77, contact=5, summary="Orientada a juntar a certidão.", process_number=None):
    """Atendimento completo, transferido e encerrado pelo atendente; devolve o TKT."""
    say(h, "Olá", conv=conv, contact=contact)
    say(h, flow.OPT_PARTE.title, conv=conv, contact=contact)
    say(h, flow.OPT_HUMAN_LIST.title, conv=conv, contact=contact)
    tkt = ops.get_open_ticket(key(conv)).ticket_id
    agent(h, f"/encerrar {summary}" if summary else "/encerrar", conv=conv)
    return tkt


# ----------------------------------------------------------------------------------------- menu e dono
def test_menu_com_a_chave_ligada_traz_retomar_atendimento_antes_do_atendente(env):  # RTK-01
    h, gw, _ = mk(env)
    say(h, "Olá")
    opts = last_opts(gw)
    assert retomada.OPT_RESUME.title in opts and opts.index(retomada.OPT_RESUME.title) == opts.index(flow.OPT_HUMAN_LIST.title) - 1
    assert len(opts) <= 10 and all(len(o.title.encode("utf-16-le")) // 2 <= 24 for o in flow.profile_menu(True, True))


def test_chave_desligada_menu_igual_e_nada_e_guardado(env):  # RTK-01, RTK-02
    make, ops, _ = env
    h, gw = make(gw=Gw())
    say(h, "Olá")
    assert last_opts(gw) == [o.title for o in flow.PROFILE_MENU]
    say(h, retomada.OPT_RESUME.title)
    assert ops.get_open_ticket(key(77)).contact_id is None and "protocolo" not in texts(gw)[-1].lower().replace("atendimento aberto", "")


def test_protocolo_guarda_o_contato_so_de_mensagem_recebida_do_proprio_contato(env):  # RTK-02
    h, gw, ops = mk(env)
    say(h, "Olá", contact=5)
    assert ops.get_open_ticket(key(77)).contact_id == "5"
    say(h, "Olá", conv=80, contact=900, sender_type="agent_bot")
    assert ops.get_open_ticket(key(80)).contact_id is None


# ----------------------------------------------------------------------------------------- retomada
def test_retomar_pelo_protocolo_liga_ao_anterior_e_restaura_so_o_que_esta_vazio(env):  # RTK-03, RTK-04
    h, gw, ops = mk(env, units=unit_dir())
    old = finished_case(h, ops)
    assert ops.get_ticket(old).facts and json.loads(ops.get_ticket(old).facts)["agent_summary"] == "Orientada a juntar a certidão."
    say(h, "Olá", conv=78)
    new = ops.get_open_ticket(key(78)).ticket_id
    say(h, retomada.OPT_RESUME.title, conv=78)
    assert texts(gw)[-1] == retomada.ASK_TEXT
    say(h, f"*{old.lower()}*", conv=78)
    assert ops.get_open_ticket(key(78)).parent_ticket_id == old
    assert f"({new}" not in texts(gw)[-1] and new in texts(gw)[-1] and old in texts(gw)[-1] and "Como deseja continuar" in texts(gw)[-1]
    assert ops.get(key(78)).profile.value == "parte_testemunha" and ops.get(key(78)).profile_locked


def test_dono_errado_inexistente_sem_dono_e_antigo_recebem_a_mesma_resposta(env):  # RTK-03
    h, gw, ops = mk(env)
    mine = finished_case(h, ops)
    legacy = ops.open_ticket("1:555")  # sem contato (protocolo antigo)
    say(h, "Olá", conv=79, contact=6)  # outro contato
    replies = []
    for tkt in (mine, "TKT-00000000", legacy.ticket_id):
        say(h, retomada.OPT_RESUME.title, conv=79, contact=6)
        say(h, tkt, conv=79, contact=6)
        replies.append((texts(gw)[-1], tuple(last_opts(gw))))
    assert len({r for r in replies}) == 1 and replies[0][0] == retomada.NOT_FOUND_TEXT
    assert mine not in replies[0][0] and ops.get_open_ticket(key(79)).parent_ticket_id is None


def test_tres_tentativas_oferecem_o_atendimento_humano(env):  # RTK-03
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, retomada.OPT_RESUME.title)
    for n in ("TKT-AAAAAAAA", "TKT-BBBBBBBB", "TKT-CCCCCCCC"):
        say(h, n)
    assert texts(gw)[-1] == retomada.GIVE_UP_TEXT and flow.OPT_HUMAN_LIST.title in last_opts(gw)


def test_o_proprio_protocolo_e_texto_digitado_sem_pedido_tambem_funcionam(env):  # RTK-03, RTK-04
    h, gw, ops = mk(env)
    old = finished_case(h, ops)
    say(h, "Olá", conv=78)
    cur = ops.get_open_ticket(key(78)).ticket_id
    say(h, cur, conv=78)  # o próprio protocolo atual não retoma
    assert ops.get_open_ticket(key(78)).parent_ticket_id is None
    say(h, f"meu protocolo é {old}", conv=78)  # digitado sem passar pelo menu
    assert ops.get_open_ticket(key(78)).parent_ticket_id == old
    assert ops.link_parent(key(78), "TKT-11111111") is False  # gravado uma só vez


def test_tkt_dentro_da_diligencia_continua_sendo_a_diligencia(env):  # RTK-03
    h, gw, ops = mk(env, units=unit_dir())
    old = finished_case(h, ops)
    st = ops.get(key(78))
    st.awaiting = "diligence"
    ops.save(st)
    say(h, f"Peço a juntada, conforme {old}", conv=78)
    assert ops.get_open_ticket(key(78)).parent_ticket_id is None
    assert ops.get(key(78)).diligence == f"Peço a juntada, conforme {old}"


def test_nao_sei_o_numero_lista_so_os_protocolos_deste_contato(env):  # RTK-08
    h, gw, ops = mk(env)
    mine = finished_case(h, ops, conv=77)
    other = finished_case(h, ops, conv=88, contact=6, summary=None)
    restricted = ops.open_ticket("1:99", "5")
    ops.set_ticket_visibility("1:99", "restricted")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, retomada.OPT_UNKNOWN.title, conv=78)
    body, opts = texts(gw)[-1], last_opts(gw)
    assert mine in body and other not in body and restricted.ticket_id not in body and mine in opts
    say(h, mine, conv=78)  # tocar na opção volta pelo caminho digitado
    assert ops.get_open_ticket(key(78)).parent_ticket_id == mine


def test_nao_sei_o_numero_sem_protocolos_oferece_humano(env):  # RTK-08
    h, gw, _ = mk(env)
    say(h, "Olá")
    say(h, retomada.OPT_RESUME.title)
    say(h, retomada.OPT_UNKNOWN.title)
    assert texts(gw)[-1] == retomada.NO_LIST_TEXT


# ----------------------------------------------------------------------------------------- fatos e retenção
def test_fatos_sao_gravados_uma_vez_e_sem_diligencia(env):  # RTK-05, RTK-11
    h, gw, ops = mk(env, units=unit_dir())
    say(h, "Olá")
    st = ops.get(key(77))
    st.diligence, st.unit_label, st.unit_id, st.request_reason = SECRET, "Unidade de Teste", "ut", "diligencia"
    ops.save(st)
    tkt = ops.get_open_ticket(key(77)).ticket_id
    agent(h, "/Encerrar  Resolvido por Telefone ")
    ev = {"event": "conversation_resolved", "id": 77, "status": "resolved", "updated_at": "2026-11-05T10:00:00Z", "account": {"id": 1},
          "conversation": {"id": 77, "status": "resolved"}}
    process(h, ev)  # o evento de resolvido depois do /encerrar não sobrescreve
    facts = json.loads(ops.get_ticket(tkt).facts)
    assert facts["agent_summary"] == "Resolvido por Telefone" and facts["unit_label"] == "Unidade de Teste" and facts["reason"] == "diligencia"
    assert SECRET not in ops.get_ticket(tkt).facts


def test_protocolo_sem_dono_ou_restrito_nao_guarda_fatos(env):  # RTK-05
    h, gw, ops = mk(env)
    t = ops.open_ticket("1:600")
    ops.close_ticket("1:600", "atendente", {"agent_summary": "x"})
    assert ops.get_ticket(t.ticket_id).facts is None
    ops.open_ticket("1:601", "5")
    ops.set_ticket_visibility("1:601", "restricted")
    closed = ops.close_ticket("1:601", "atendente", {"agent_summary": "x"})
    assert closed.facts is None


def test_retencao_apaga_fatos_e_dono_apos_os_dias_e_mantem_a_linha(env):  # RTK-06
    h, gw, ops = mk(env)
    old = finished_case(h, ops)
    assert ops.purge_ticket_facts(90) == 0
    later = datetime.now(timezone.utc) + timedelta(days=91)
    assert ops.purge_ticket_facts(90, later) == 1
    t = ops.get_ticket(old)
    assert t.facts is None and t.contact_id is None and t.status == "closed"
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, old, conv=78)
    assert texts(gw)[-1] == retomada.NOT_FOUND_TEXT  # sem dono, não retoma


def test_a_varredura_de_inatividade_tambem_aplica_a_retencao(env):  # RTK-06
    h, gw, ops = mk(env)
    old = finished_case(h, ops)
    h.close_inactive(now=datetime.now(timezone.utc) + timedelta(days=120))
    assert ops.get_ticket(old).facts is None


# ----------------------------------------------------------------------------------------- Ouvidoria da Mulher
def test_caso_da_ouvidoria_nao_aparece_em_listas_nem_no_historico_de_outros_times(env):  # RTK-07
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    mulher_tkt = ops.get_open_ticket(key(77)).ticket_id
    assert ops.get_ticket(mulher_tkt).visibility == "restricted"
    agent(h, "/encerrar")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, retomada.OPT_UNKNOWN.title, conv=78)
    assert mulher_tkt not in texts(gw)[-1]
    say(h, flow.OPT_HUMAN_LIST.title, conv=78)
    notes = " ".join(t for c, t in gw.notes if c == 78)
    assert mulher_tkt not in notes and "Ouvidoria" not in notes


def test_retomar_caso_restrito_nao_cita_assunto_e_vai_ao_time_com_so_o_link(env):  # RTK-07
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    old = ops.get_open_ticket(key(77)).ticket_id
    agent(h, "/encerrar")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, old, conv=78)
    spoken = " ".join(t for c, t in gw.sent if c == 78)
    assert "Ouvidoria" not in spoken and "violência" not in spoken.lower() and retomada.RESTRICTED_TEXT in spoken
    note = next(t for c, t in gw.notes if c == 78 and "RETOMADA DE ATENDIMENTO RESTRITO" in t)
    assert old in note and f"{BASE}/app/accounts/1/conversations/77" in note and "Ouvidoria" not in note
    assert gw.assigned[-1] == (78, "ouvidoria_mulher") and ops.get(key(78)).handoff_state is S.HUMAN_ACTIVE
    assert retomada.RESTRICTED_NOTICE.split("{")[0] in gw.sent[-1][1] and ops.get_open_ticket(key(78)).visibility == "restricted"


def test_contato_nao_seguro_solta_o_dono_e_nao_retoma_por_chat(env):  # RTK-07
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    say(h, ovm.OPT_TEAM.title)
    for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "parceiro_ex"), ("processo", "nao"), ("contato", "nao")):
        say(h, ovm.ANSWERS[(f, v)].title)
    t = ops.get_open_ticket(key(77))
    assert t.visibility == "no_chat" and t.contact_id is None
    agent(h, "/encerrar")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, t.ticket_id, conv=78)
    assert texts(gw)[-1] == retomada.NOT_FOUND_TEXT


def test_oferta_por_texto_livre_de_violencia_tambem_restringe_o_protocolo(env):  # RTK-07
    h, gw, ops = mk(env)
    say(h, "meu marido me bate")
    assert ops.get_open_ticket(key(77)).visibility == "restricted"


# ----------------------------------------------------------------------------------------- nota para o atendente
def test_falar_com_atendente_sem_unidade_agora_leva_nota_com_historico(env):  # RTK-09
    h, gw, ops = mk(env)
    old = finished_case(h, ops, summary="Orientada a juntar a certidão.")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, old, conv=78)
    say(h, flow.OPT_HUMAN_LIST.title, conv=78)
    note = next(t for c, t in gw.notes if c == 78 and "SOLICITAÇÃO DE ATENDIMENTO HUMANO" in t)
    assert f"continua o {old}" in note and "vinculado pela pessoa" in note and "resumo do atendente: Orientada a juntar a certidão." in note
    assert f"{BASE}/app/accounts/1/conversations/77" in note and "equipe:" in note
    assert gw.events.index("nota") < gw.events.index("equipe")


def test_historico_mesmo_contato_sem_vinculo_e_marcado_como_nao_confirmado(env):  # RTK-09
    h, gw, ops = mk(env)
    old = finished_case(h, ops)
    say(h, "Olá", conv=78)
    say(h, flow.OPT_PARTE.title, conv=78)
    say(h, flow.OPT_HUMAN_LIST.title, conv=78)
    note = next(t for c, t in gw.notes if c == 78 and "SOLICITAÇÃO DE ATENDIMENTO HUMANO" in t)
    assert old in note and "mesmo contato, não confirmado pela pessoa" in note


def test_nota_uma_vez_so_entre_tentativas_e_erro_no_historico_nao_trava(env, monkeypatch):  # RTK-09
    h, gw, ops = mk(env, gw=Gw(fail_assign=1))
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    say(h, "oi de novo")
    assert sum("SOLICITAÇÃO DE ATENDIMENTO HUMANO" in t for _, t in gw.notes) == 1 and ops.get(key(77)).handoff_state is S.HUMAN_ACTIVE
    h2, gw2, ops2 = mk(env)
    monkeypatch.setattr(h2, "_history_lines", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    say(h2, "Olá", conv=90)
    say(h2, flow.OPT_HUMAN_LIST.title, conv=90)
    assert any("SOLICITAÇÃO DE ATENDIMENTO HUMANO" in t for c, t in gw2.notes if c == 90) and ops2.get(key(90)).handoff_state is S.HUMAN_ACTIVE


def test_chave_desligada_a_nota_e_a_de_antes(env):  # RTK-09
    make, ops, _ = env
    h, gw = make(gw=Gw())
    say(h, "Olá")
    say(h, flow.OPT_HUMAN_LIST.title)
    assert not [t for _, t in gw.notes if "SOLICITAÇÃO DE ATENDIMENTO HUMANO" in t]  # sem a chave, o caminho sem unidade segue sem nota
    assert "Histórico" not in " ".join(t for _, t in gw.notes)


# ----------------------------------------------------------------------------------------- Balcão e telefone de retorno
def go_balcao(h, conv=77):
    say(h, "Olá", conv=conv)
    say(h, flow.OPT_ADV.title, conv=conv)
    st = h.ops.get(key(conv))
    st.unit_label, st.unit_id, st.request_reason = "Unidade de Teste", "ut", "atendimento"
    h.ops.save(st)
    say(h, flow.OPT_HUMAN_LIST.title, conv=conv)
    say(h, flow.OPT_CH_BALCAO.title, conv=conv)


def test_balcao_pergunta_o_telefone_de_retorno_uma_vez(env):  # RTK-10
    h, gw, ops = mk(env, units=unit_dir(balcao_url="https://www.tjpa.jus.br/balcao/unidade-de-teste"))
    go_balcao(h)
    assert texts(gw)[-1] == retomada.RETURN_ASK and last_opts(gw) == [o.title for o in retomada.PHONE_OPTIONS]
    say(h, retomada.OPT_PHONE_YES.title)
    tkt = ops.get_open_ticket(key(77)).ticket_id
    assert "https://www.tjpa.jus.br/balcao/unidade-de-teste" in " ".join(texts(gw)[-3:]) and tkt in " ".join(texts(gw)[-3:])
    note = next(t for _, t in gw.notes if "BALCÃO VIRTUAL" in t)
    assert tkt in note and "(91)" in note and not gw.assigned and ops.get(key(77)).handoff_state is S.BOT_ACTIVE
    n = len(texts(gw))
    say(h, flow.OPT_HUMAN_LIST.title)
    say(h, flow.OPT_CH_BALCAO.title)
    assert retomada.RETURN_ASK not in texts(gw)[n:]  # não pergunta de novo no mesmo protocolo


def test_outro_numero_e_validado_e_recusar_e_possivel(env):  # RTK-10
    h, gw, ops = mk(env, units=unit_dir(balcao_url="https://www.tjpa.jus.br/balcao/unidade-de-teste"))
    go_balcao(h)
    say(h, retomada.OPT_PHONE_OTHER.title)
    say(h, "123")
    assert texts(gw)[-1] == retomada.RETURN_NUMBER_BAD
    say(h, "91 98888-7777")
    note = next(t for _, t in gw.notes if "BALCÃO VIRTUAL" in t)
    assert "(91) 98888-7777" in note or "98888-7777" in note
    h2, gw2, ops2 = mk(env, units=unit_dir(balcao_url="https://www.tjpa.jus.br/balcao/unidade-de-teste"))
    go_balcao(h2, conv=91)
    say(h2, retomada.OPT_PHONE_NO.title, conv=91)
    assert "preferiu não informar" in next(t for c, t in gw2.notes if c == 91 and "BALCÃO VIRTUAL" in t)


def test_telefone_de_retorno_nao_vai_para_auditoria_nem_log(env, caplog):  # RTK-16
    caplog.set_level(logging.DEBUG)
    h, gw, ops = mk(env, units=unit_dir(balcao_url="https://www.tjpa.jus.br/balcao/unidade-de-teste"))
    go_balcao(h)
    say(h, retomada.OPT_PHONE_OTHER.title)
    say(h, "91 98888-7777")
    audit = " ".join(f"{r['action']} {r['detail']}" for r in ops.audit_rows(limit=500))
    assert "98888" not in audit and "98888" not in caplog.text and "5591912345678" not in audit


def test_titulo_de_telefone_digitado_fora_do_pedido_e_texto_comum(env):  # RTK-10
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, retomada.OPT_PHONE_YES.title)
    assert ops.get(key(77)).return_phone is None


# ----------------------------------------------------------------------------------------- resumo por IA
GOOD = json.dumps({"assunto": "Pedido de juntada de documento", "pedido": "Juntar a certidão no processo", "ja_informado": ["Processo " + "0000001-02.2026.8.14.0001"],
                   "pendencias": ["Enviar a certidão"]})


def ai_case(env, out=GOOD, **kw):
    h, gw, ops = mk(env, ai=True, **kw)
    now = int(time.time()) + 2
    gw.msgs[77] = [{"message_type": 0, "private": False, "created_at": now, "content": "Quero juntar a certidão no processo 0000001-02.2026.8.14.0001. Meu CPF 529.982.247-25 e fone (91) 91234-5678"},
                   {"message_type": 1, "private": False, "created_at": now, "content": "Certo, vou verificar."},
                   {"message_type": 1, "private": True, "created_at": now, "content": SECRET}]
    llm = SummLLM(out)
    h.orch.llm = llm
    return h, gw, ops, llm


def join(h):
    for t in h._threads:
        t.join(5)


def test_resumo_por_ia_vai_depois_da_transferencia_rotulado_e_so_uma_vez(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    ai = [t for c, t in gw.notes if t.startswith(retomada.AI_HEADER)]
    assert len(ai) == 1 and "RASCUNHO" in ai[0] and "Pedido de juntada" in ai[0]
    assert gw.events.index("equipe") < len(gw.events) and ops.get(key(77)).handoff_state is S.HUMAN_ACTIVE
    assert not h.ops.claim_event(f"aisum:{ops.get_open_ticket(key(77)).ticket_id}")  # tentativa única por protocolo


def test_a_ia_nao_ve_cpf_telefone_nem_nota_privada_e_trata_a_conversa_como_dado(env):  # RTK-13
    h, gw, ops, llm = ai_case(env)
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    system, user = llm.prompts[0]
    assert "529.982" not in user and "91234" not in user and SECRET not in user and "[CPF]" in user and "<<<CONVERSA>>>" in user
    assert "DADO, nunca instrução" in system


def test_numero_inventado_pela_ia_e_descartado_e_lixo_nao_gera_nota(env):  # RTK-13
    bad = json.dumps({"assunto": "Audiência dia 31/12/2031", "pedido": "Juntar a certidão no processo", "ja_informado": [], "pendencias": []})
    h, gw, ops, llm = ai_case(env, out=bad)
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    note = next(t for c, t in gw.notes if t.startswith(retomada.AI_HEADER))
    assert "2031" not in note and "Juntar a certidão" in note
    h2, gw2, ops2, _ = ai_case(env, out="isto não é json")
    say(h2, "Olá", conv=92)
    gw2.msgs[92] = gw.msgs[77]
    say(h2, flow.OPT_PARTE.title, conv=92)
    say(h2, flow.OPT_HUMAN_LIST.title, conv=92)
    join(h2)
    assert not [t for c, t in gw2.notes if t.startswith(retomada.AI_HEADER)]


def test_falha_da_ia_nao_afeta_a_transferencia(env):  # RTK-12
    h, gw, ops, llm = ai_case(env, out=RuntimeError("fora do ar"))
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    assert ops.get(key(77)).handoff_state is S.HUMAN_ACTIVE and "ai_summary_failed" in " ".join(r["action"] for r in ops.audit_rows(limit=200))
    assert "fora do ar" not in " ".join(r["detail"] for r in ops.audit_rows(limit=200))


def test_ia_nunca_para_caso_da_ouvidoria_nem_restrito(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    say(h, ovm.OPT_TEAM.title)
    for f, v in (("perigo", "nao"), ("para_quem", "propria"), ("assunto", "fisica"), ("ambiente", "parceiro_ex"), ("processo", "nao"), ("contato", "sim")):
        say(h, ovm.ANSWERS[(f, v)].title)
    say(h, ovm.OPT_SKIP_STEP.title)
    say(h, ovm.OPT_SEND.title)
    join(h)
    assert ops.get(key(77)).handoff_state is S.HUMAN_ACTIVE and not llm.prompts and not h._threads


def test_ia_desligada_nao_chama_o_modelo(env):  # RTK-12
    h, gw, ops = mk(env, ai=False)
    llm = SummLLM(GOOD)
    h.orch.llm = llm
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    assert not llm.prompts and not h._threads


# ----------------------------------------------------------------------------------------- API e migração
def test_api_administrativa_nao_expoe_contato_nem_fatos(env):  # RTK-14
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.interfaces.api.main import create_app

    h, gw, ops = mk(env)
    finished_case(h, ops)

    class C:
        pass

    c = C()
    c.ops = ops
    c.handler = None
    app = create_app(container=c, settings=Settings(admin_api_token="tok", inactivity_close_hours=0))
    rows = TestClient(app).get("/api/admin/tickets", headers={"Authorization": "Bearer tok"}).json()
    assert rows and not {"contact_id", "facts", "visibility"} & set(rows[0])


def test_banco_antigo_sem_colunas_novas_abre_e_protocolo_antigo_nao_retoma(tmp_path):  # RTK-15
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE tickets(ticket_id TEXT PRIMARY KEY, conversation TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT, closed_by TEXT, status TEXT NOT NULL);"
                      "INSERT INTO tickets VALUES('TKT-AAAAAAAA','1:5','2026-10-01T10:00:00+00:00','2026-10-01T11:00:00+00:00','usuario','closed');")
    con.commit()
    con.close()
    ops = OperationalStore(path)
    t = ops.get_ticket("TKT-AAAAAAAA")
    assert t.contact_id is None and t.visibility == "normal" and t.facts is None
    assert ops.list_contact_tickets("5") == []
    n = ops.open_ticket("1:6", "7")
    assert ops.get_ticket(n.ticket_id).contact_id == "7"


def test_leitura_defensiva_dos_fatos():  # RTK-15
    assert retomada.Fact.from_json("lixo") == retomada.Fact() and retomada.Fact.from_json("[1]") == retomada.Fact()
    f = retomada.Fact.from_json(json.dumps({"profile": "advogado", "inventado": "x", "agent_summary": "y" * 900}))
    assert f.profile == "advogado" and len(f.agent_summary) == retomada.SUMMARY_LIMIT


def test_reconhece_o_protocolo_em_formas_comuns_e_recusa_texto_longo():  # RTK-03
    assert retomada.find_ticket_id("*TKT-1a2b3c4d*") == "TKT-1A2B3C4D"
    assert retomada.find_ticket_id("tkt 1A2B3C4D") == "TKT-1A2B3C4D"
    assert retomada.find_ticket_id("1A2B3C4D") is None and retomada.find_ticket_id("1A2B3C4D", bare_ok=True) == "TKT-1A2B3C4D"
    assert retomada.find_ticket_id("TKT-1A2B3C4D " + "palavra " * 20) is None


def test_contato_vem_tambem_de_meta_sender_quando_o_evento_nao_traz_o_tipo(env):  # RTK-02 (evento real do Chatwoot)
    h, gw, ops = mk(env)
    for conv, sender, meta in ((81, {"id": 7, "name": "X"}, {}), (82, {"name": "X"}, {"sender": {"id": 8}}),
                               (83, {"type": "contact"}, {"sender": {"id": 9}})):
        counter["n"] += 1
        ev = msg_event("Olá", msg_id=counter["n"], conv=conv)
        ev["sender"] = sender
        ev["conversation"]["meta"] = {"assignee": None, **meta}
        process(h, ev)
    assert [ops.get_open_ticket(key(c)).contact_id for c in (81, 82, 83)] == ["7", "8", "9"]
    ev = msg_event("Olá", msg_id=counter["n"] + 1, conv=84)
    ev["sender"] = {"type": "contact", "id": None}
    ev["conversation"]["meta"] = {"assignee": None}
    process(h, ev)
    assert ops.get_open_ticket(key(84)).contact_id is None
    assert any(r["action"] == "ticket_sem_contato" for r in ops.audit_rows(key(84), 20))


def test_retomada_funciona_com_o_formato_de_evento_sem_sender_type(env):  # RTK-03
    h, gw, ops = mk(env)

    def ev(content, conv):
        counter["n"] += 1
        e = msg_event(content, msg_id=counter["n"], conv=conv)
        e["sender"] = {"id": 5, "name": "Pessoa"}  # sem `type`, como no evento real
        e["conversation"]["meta"] = {"assignee": None, "sender": {"id": 5, "phone_number": "+5591988887777"}}
        return process(h, e)

    ev("Olá", 77)
    old = ops.get_open_ticket(key(77)).ticket_id
    agent(h, "/encerrar")
    ev("Olá", 78)
    ev(old, 78)
    assert ops.get_open_ticket(key(78)).parent_ticket_id == old
