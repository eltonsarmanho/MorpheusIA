"""Rodada 3: nota ao atendente na retomada (RTK-17) e lacunas da verificação 2 (RTK-02, 03, 06, 07, 12, 13)."""
import asyncio
import threading
import time

from app.application.chat import flow
from app.application.chat import mulher as ovm
from app.application.chat import retomada
from app.domain.handoff import HandoffState as S
from tests.integration.test_chatwoot_handler import msg_event, process
from tests.integration.test_retomada_ticket import (BASE, GOOD, SECRET, Gw, SummLLM, agent, ai_case, counter, finished_case, join, key, mk, say)
from tests.integration.test_retomada_lacunas import iso, set_times
from tests.integration.test_roteiro_tjpa import env, last_opts, texts  # noqa: F401  (fixture `env`)


def ev(h, content, conv, sender, meta=None, contact_inbox=None, mtype="incoming"):
    counter["n"] += 1
    e = msg_event(content, msg_id=counter["n"], conv=conv, mtype=mtype)
    e["sender"] = sender
    e["conversation"]["meta"] = {"assignee": None, **(meta or {})}
    if contact_inbox:
        e["conversation"]["contact_inbox"] = contact_inbox
    return process(h, e)


# ----------------------------------------------------------------------------------------- RTK-17: o atendente vê o anterior
def test_retomar_grava_nota_para_o_atendente_sem_esperar_a_transferencia(env):  # RTK-17
    h, gw, ops = mk(env)
    old = finished_case(h, ops, summary="Orientada a juntar a certidão.")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, old, conv=78)
    note = next(t for c, t in gw.notes if c == 78 and t.startswith("RETOMADA DE ATENDIMENTO"))
    assert old in note and "resumo do atendente: Orientada a juntar a certidão." in note and f"{BASE}/app/accounts/1/conversations/77" in note
    assert ops.get(key(78)).handoff_state is S.BOT_ACTIVE and not [a for a in gw.assigned if a[0] == 78]  # sem transferência


def test_retomada_restrita_nao_grava_a_nota_de_retomada_comum(env):  # RTK-17
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, ovm.OPT_ENTRY.title)
    old = ops.get_open_ticket(key(77)).ticket_id
    agent(h, "/encerrar")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, old, conv=78)
    assert not [t for c, t in gw.notes if c == 78 and t.startswith("RETOMADA DE ATENDIMENTO\n")] and any("RETOMADA DE ATENDIMENTO RESTRITO" in t for c, t in gw.notes)


def test_ia_resume_o_atendimento_anterior_ao_retomar_mesmo_sem_transferencia(env):  # RTK-17
    h, gw, ops, llm = ai_case(env)
    now = int(time.time())
    old = finished_case(h, ops, conv=70)
    set_times(ops, old, opened=iso(-7200), closed=iso(-3600))
    gw.msgs[70] = [{"message_type": 0, "private": False, "created_at": now - 5400, "content": "Quero saber como está o processo 6080680-32.2025.8.03.0001"},
                   {"message_type": 1, "private": False, "created_at": now - 5300, "content": "Consultei o acervo."},
                   {"message_type": 0, "private": False, "created_at": now - 600, "content": f"outro protocolo {SECRET}"}]
    h.orch.llm = SummLLM('{"assunto": "Situação do processo 6080680-32.2025.8.03.0001", "pedido": "Saber como está o processo", "ja_informado": [], "pendencias": []}')
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    join(h)
    ai = [t for c, t in gw.notes if c == 77 and t.startswith(retomada.AI_HEADER)]
    assert len(ai) == 1 and f"atendimento anterior {old}" in ai[0] and "6080680-32.2025.8.03.0001" in ai[0]
    assert SECRET not in h.orch.llm.prompts[0][1] and ops.get(key(77)).handoff_state is S.BOT_ACTIVE


def test_ia_da_retomada_so_roda_com_a_chave_e_para_caso_normal_do_mesmo_contato(env):  # RTK-17
    h, gw, ops = mk(env, ai=False)
    h.orch.llm = SummLLM(GOOD)
    old = finished_case(h, ops, conv=70)
    gw.msgs[70] = [{"message_type": 0, "private": False, "created_at": int(time.time()), "content": "oi"}]
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    assert not h.orch.llm.prompts and not h._threads
    h2, gw2, ops2, llm2 = ai_case(env)
    legacy = ops2.open_ticket("1:555", None)  # sem dono: não retoma, logo não há IA
    say(h2, "Olá", conv=77)
    say(h2, legacy.ticket_id, conv=77)
    join(h2)
    assert not llm2.prompts


# ----------------------------------------------------------------------------------------- S1 e RTK-02
def test_remetente_explicito_que_nao_e_contato_nunca_vira_dono(env):  # RTK-02 (defeito S1)
    h, gw, ops = mk(env)
    for n, t in enumerate(("agent_bot", "user", "agent", "AgentBot"), start=110):
        ev(h, "Olá", n, {"id": 5, "type": t}, meta={"sender": {"id": 5}})
        assert ops.get_open_ticket(key(n)) is None or ops.get_open_ticket(key(n)).contact_id is None, t


def test_contato_inbox_e_telefone_vindos_so_do_meta(env):  # RTK-02
    h, gw, ops = mk(env)
    ev(h, "Olá", 120, {"name": "x"}, contact_inbox={"contact_id": 33})
    assert ops.get_open_ticket(key(120)).contact_id == "33"
    ev(h, "Olá", 121, {"id": 4}, meta={"sender": {"id": 4, "phone_number": "+5591988887777"}})
    ev(h, flow.OPT_ADV.title, 121, {"id": 4}, meta={"sender": {"id": 4, "phone_number": "+5591988887777"}})
    assert h._contact_phone({"sender": {"id": 4}, "conversation": {"meta": {"sender": {"phone_number": "+5591988887777"}}}}) == "+5591988887777"


def test_diagnostico_sem_contato_registra_so_formato_nunca_valores(env):  # RTK-02, RTK-16
    h, gw, ops = mk(env)
    ev(h, "Olá", 122, {"type": "contact", "name": "Maria da Silva", "email": "maria@x.com", "phone_number": "+5591988887777", "id": None})
    row = next(r for r in ops.audit_rows(key(122), 20) if r["action"] == "ticket_sem_contato")
    assert row["detail"] == "sender.type=contact chaves=email,id,name,phone_number,type"
    assert "Maria" not in row["detail"] and "maria@" not in row["detail"] and "9198888" not in row["detail"]


# ----------------------------------------------------------------------------------------- RTK-03, 07, 13
def test_digitar_o_proprio_protocolo_recebe_a_resposta_padrao(env):  # RTK-03
    h, gw, ops = mk(env)
    say(h, "Olá")
    say(h, retomada.OPT_RESUME.title)
    say(h, ops.get_open_ticket(key(77)).ticket_id)
    assert texts(gw)[-1] == retomada.NOT_FOUND_TEXT and ops.get_open_ticket(key(77)).parent_ticket_id is None


def test_falha_da_retomada_restrita_nao_afirma_encaminhamento(env):  # RTK-07
    h, gw, ops = mk(env, gw=Gw(fail_assign=1))
    first = ops.open_ticket("1:300", "5")
    ops.set_ticket_visibility("1:300", "restricted")
    ops.close_ticket("1:300", "atendente")
    say(h, "Olá", conv=78)
    say(h, retomada.OPT_RESUME.title, conv=78)
    say(h, first.ticket_id, conv=78)
    last = texts(gw)[-1]
    assert "Não consegui confirmar" in last and "Aguarde" not in last and "foi encaminhada" not in last and "Ouvidoria" not in last


def test_link_some_tambem_quando_a_conversa_tem_protocolo_sem_chat(env):  # RTK-07
    h, gw, ops = mk(env)
    ops.open_ticket("1:60", "5")
    ops.set_ticket_visibility("1:60", "no_chat", detach_contact=True)
    ops.close_ticket("1:60", "atendente")
    n = ops.open_ticket("1:60", "5")
    ops.close_ticket("1:60", "atendente", {"agent_summary": "ok"})
    say(h, "Olá", conv=78)
    say(h, flow.OPT_PARTE.title, conv=78)
    say(h, flow.OPT_HUMAN_LIST.title, conv=78)
    note = next(x for c, x in gw.notes if c == 78 and "SOLICITAÇÃO DE ATENDIMENTO HUMANO" in x)
    assert "/conversations/60" not in note and n.ticket_id in note


def test_numero_curto_nao_se_apoia_em_numero_maior_da_fonte(env):  # RTK-13
    class L:
        def generate(self, *, system, user):
            return '{"assunto": "Prazo de 10 dias", "pedido": "", "ja_informado": [], "pendencias": []}'

    assert retomada.ai_summary_note(L(), ["PESSOA: O valor é 2100 reais"], []) is None


# ----------------------------------------------------------------------------------------- RTK-12
def test_sem_chat_com_mensagens_nao_chama_o_modelo(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    gw.msgs[95] = [{"message_type": 0, "private": False, "created_at": int(time.time()) + 2, "content": "quero juntar a certidão"}]
    say(h, "Olá", conv=95)
    ops.set_ticket_visibility(key(95), "no_chat", detach_contact=True)
    say(h, flow.OPT_PARTE.title, conv=95)
    say(h, flow.OPT_HUMAN_LIST.title, conv=95)
    join(h)
    assert not llm.prompts and not h._threads


def test_modelo_lento_nao_trava_o_atendimento_da_conversa(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    release = threading.Event()

    class Slow(SummLLM):
        def generate(self, *, system, user):
            release.wait(10)
            return super().generate(system=system, user=user)

    h.orch.llm = Slow(GOOD)
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    worker = threading.Thread(target=lambda: say(h, flow.OPT_HUMAN_LIST.title), daemon=True)
    worker.start()
    worker.join(3)
    finished = not worker.is_alive()
    release.set()
    join(h)
    assert finished and ops.get(key(77)).handoff_state is S.HUMAN_ACTIVE


def test_so_as_30_ultimas_mensagens_publicas_e_sem_atividade(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    t0 = int(time.time()) + 2
    gw.msgs[77] = [{"message_type": 0, "private": False, "created_at": t0, "content": f"mensagem {n:02d}"} for n in range(40)]
    gw.msgs[77].append({"message_type": 2, "private": False, "created_at": t0, "content": "ATIVIDADE-INTERNA"})
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    user = llm.prompts[0][1]
    assert "mensagem 39" in user and "mensagem 10" in user and "mensagem 09" not in user and "ATIVIDADE-INTERNA" not in user


def test_conversa_anterior_ignora_mensagens_antes_da_abertura_do_protocolo_vinculado(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    now = int(time.time())
    old = finished_case(h, ops, conv=70)
    set_times(ops, old, opened=iso(-7200), closed=iso(-3600))
    gw.msgs[70] = [{"message_type": 0, "private": False, "created_at": now - 9000, "content": f"antes do protocolo {SECRET}"},
                   {"message_type": 0, "private": False, "created_at": now - 5400, "content": "dentro do protocolo"}]
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    join(h)
    assert all(SECRET not in p[1] for p in llm.prompts) and any("dentro do protocolo" in p[1] for p in llm.prompts)


# ----------------------------------------------------------------------------------------- RTK-06: varredura da API
def test_varredura_da_api_aplica_a_retencao_mesmo_sem_encerrar_por_inatividade(env, monkeypatch):  # RTK-06
    from fastapi.testclient import TestClient
    from app.config import Settings
    import app.interfaces.api.main as api

    class H:
        purged = 0
        closed = 0

        def purge_expired(self, now=None):
            H.purged += 1
            return 0

        def close_inactive(self, hours):
            H.closed += 1
            return []

    class C:
        handler = H()
        ops = None

    real_sleep = asyncio.sleep

    async def fast(_s):
        await real_sleep(0.01)

    monkeypatch.setattr(api.asyncio, "sleep", fast)
    app = api.create_app(container=C(), settings=Settings(admin_api_token="t", inactivity_close_hours=0, ticket_resume_enabled=True))
    with TestClient(app):
        for _ in range(100):
            if H.purged:
                break
            time.sleep(0.02)
    assert H.purged >= 1 and H.closed == 0
    H.purged = H.closed = 0
    app2 = api.create_app(container=C(), settings=Settings(admin_api_token="t", inactivity_close_hours=0, ticket_resume_enabled=False))
    with TestClient(app2):
        time.sleep(0.2)
    assert H.purged == 0


def test_o_protocolo_atual_nao_le_o_que_chega_depois_da_transferencia(env):  # RTK-12
    h, gw, ops, llm = ai_case(env)
    gw.msgs[77].append({"message_type": 0, "private": False, "created_at": int(time.time()) + 600, "content": f"chegou depois {SECRET}"})
    say(h, "Olá")
    say(h, flow.OPT_PARTE.title)
    say(h, flow.OPT_HUMAN_LIST.title)
    join(h)
    assert SECRET not in llm.prompts[0][1] and "Quero juntar" in llm.prompts[0][1]
