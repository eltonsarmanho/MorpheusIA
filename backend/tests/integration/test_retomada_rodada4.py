"""Lacunas restantes da verificação 3 (RTK-12, RTK-17): link, guardas da IA do anterior, chaves de idempotência e corridas."""
import time

from app.application.chat import flow
from app.application.chat import retomada
from tests.integration.test_retomada_ticket import (GOOD, SECRET, Gw, SummLLM, ai_case, finished_case, join, key, mk, say)
from tests.integration.test_retomada_lacunas import iso, set_times
from tests.integration.test_roteiro_tjpa import env  # noqa: F401  (fixture `env`)

PREV = ('{"assunto": "Situação do processo", "pedido": "Saber como está o processo", "ja_informado": [], "pendencias": []}')


def previous(h, ops, gw, conv=70, text="Quero saber como está o processo"):
    """Protocolo normal encerrado do contato 5, com uma mensagem dentro do período."""
    old = finished_case(h, ops, conv=conv)
    set_times(ops, old, opened=iso(-7200), closed=iso(-3600))
    gw.msgs[conv] = [{"message_type": 0, "private": False, "created_at": int(time.time()) - 5400, "content": text}]
    return old


def drafts(gw, conv):
    return [t for c, t in gw.notes if c == conv and t.startswith(retomada.AI_HEADER)]


def test_nota_de_retomada_sem_link_quando_a_conversa_anterior_tem_protocolo_restrito_ou_sem_chat(env):  # RTK-17 (Y02)
    for n, vis in enumerate(("restricted", "no_chat"), start=60):
        h, gw, ops = mk(env)
        a = ops.open_ticket(f"1:{n}", "5")
        ops.close_ticket(f"1:{n}", "atendente", {"agent_summary": "ok"})
        ops.open_ticket(f"1:{n}", "5")
        ops.set_ticket_visibility(f"1:{n}", vis, detach_contact=vis == "no_chat")
        ops.close_ticket(f"1:{n}", "atendente")
        say(h, "Olá", conv=78)
        say(h, a.ticket_id, conv=78)
        note = next(t for c, t in gw.notes if c == 78 and t.startswith("RETOMADA DE ATENDIMENTO\n"))
        assert a.ticket_id in note and f"/conversations/{n}" not in note, vis


def test_protocolo_atual_restrito_nao_envia_nada_ao_modelo_ao_retomar_um_normal(env):  # RTK-17 (Y05b)
    h, gw, ops, llm = ai_case(env)
    old = previous(h, ops, gw)
    h.orch.llm = SummLLM(PREV)
    join(h)
    before = len(h._threads)  # a preparação (encerrar com transferência) já usou a IA uma vez
    say(h, "Olá", conv=77)
    ops.set_ticket_visibility(key(77), "restricted")  # o protocolo atual virou restrito antes de retomar
    say(h, old, conv=77)
    join(h)
    assert not h.orch.llm.prompts and len(h._threads) == before


def test_cada_retomada_tem_o_seu_rascunho_e_a_chave_nao_colide_com_a_da_transferencia(env):  # RTK-17 (Y08a, Y08b)
    h, gw, ops, llm = ai_case(env)
    old = previous(h, ops, gw, conv=70)
    old2 = previous(h, ops, gw, conv=71, text="Quero saber do processo da outra conversa")
    h.orch.llm = SummLLM(PREV)
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    say(h, "Olá", conv=78)
    say(h, old2, conv=78)
    join(h)
    assert len(drafts(gw, 77)) == 1 and len(drafts(gw, 78)) == 1  # chave por protocolo atual, não global
    gw.msgs[77] = [{"message_type": 0, "private": False, "created_at": int(time.time()) + 2, "content": "Quero juntar a certidão do processo."}]
    say(h, flow.OPT_PARTE.title, conv=77)
    say(h, flow.OPT_HUMAN_LIST.title, conv=77)
    join(h)
    assert len(drafts(gw, 77)) == 2  # retomada + transferência do mesmo protocolo geram os dois rascunhos


def test_protocolo_anterior_ainda_aberto_aparece_como_aberto_na_nota(env):  # RTK-17 (Y14)
    h, gw, ops = mk(env)
    open_one = ops.open_ticket("1:70", "5")
    say(h, "Olá", conv=77)
    say(h, open_one.ticket_id, conv=77)
    note = next(t for c, t in gw.notes if c == 77 and t.startswith("RETOMADA DE ATENDIMENTO\n"))
    assert "(ainda aberto)" in note and "ainda aberto" in note.split("\n")[1] + note


def test_rascunho_do_anterior_nao_e_postado_se_o_protocolo_deixou_de_ser_normal_durante_a_chamada(env):  # RTK-17 (Y06)
    class Race(Gw):
        def list_messages(self, account_id, conversation_id):
            rows = super().list_messages(account_id, conversation_id)
            if conversation_id == 70:
                self.on_read()
            return rows

    h, gw, ops, llm = ai_case(env, gw=Race())
    old = previous(h, ops, gw)
    h.orch.llm = SummLLM(PREV)
    gw.on_read = lambda: ops.set_ticket_visibility(key(77), "restricted")
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    join(h)
    assert not drafts(gw, 77) and "ai_summary_previous_skipped" in " ".join(r["action"] for r in ops.audit_rows(limit=300))


def test_rascunho_do_anterior_nao_e_postado_se_o_protocolo_aberto_mudou_durante_a_chamada(env):  # RTK-17 (Y07)
    class Race(Gw):
        def list_messages(self, account_id, conversation_id):
            rows = super().list_messages(account_id, conversation_id)
            if conversation_id == 70:
                self.on_read()
            return rows

    h, gw, ops, llm = ai_case(env, gw=Race())
    old = previous(h, ops, gw)
    h.orch.llm = SummLLM(PREV)
    gw.on_read = lambda: ops.close_ticket(key(77), "usuario")
    say(h, "Olá", conv=77)
    say(h, old, conv=77)
    join(h)
    assert not drafts(gw, 77)


def test_resumo_da_transferencia_ignora_o_que_veio_antes_da_abertura_do_protocolo_vinculado(env):  # RTK-12 (W05)
    h, gw, ops, llm = ai_case(env)
    now = int(time.time())
    old = finished_case(h, ops, conv=70)
    set_times(ops, old, opened=iso(-7200), closed=iso(-3600))
    gw.msgs[70] = [{"message_type": 0, "private": False, "created_at": now - 9000, "content": f"antes do protocolo {SECRET}"},
                   {"message_type": 0, "private": False, "created_at": now - 5400, "content": "dentro do protocolo anterior"}]
    say(h, "Olá", conv=77)
    cur = ops.get_open_ticket(key(77))
    parent = ops.get_ticket(old)
    h._ai_summary(1, 77, key(77), cur.ticket_id, (cur.opened_at, parent.conversation, parent.opened_at, parent.closed_at, None))  # caminho da transferência
    user = llm.prompts[0][1]
    assert "dentro do protocolo anterior" in user and SECRET not in user
