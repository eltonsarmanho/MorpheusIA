"""Regressões da segunda rodada do verificador (R-A..R-G e mutantes sobreviventes)."""
import httpx
import pytest

from app.application.answering.grounding import ModelAnswer, verify_grounding
from app.application.answering.intent import classify_intent
from app.application.answering.orchestrator import Orchestrator, OrchestratorConfig, _is_affirmative
from app.application.chat.handler import ChatwootEventHandler
from app.application.collection.service import CollectionService
from app.application.collection.sources import CollectionError, SourceRegistry
from app.application.ingestion.service import IngestionConfig, IngestionService
from app.application.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.domain.handoff import HandoffState as S, InvalidTransition
from app.domain.models import Evidence, Intent, KnowledgeDomain as D, ReviewState as R
from app.domain.policies import AbstentionPolicy
from app.infrastructure.sqlite.operational_store import ConversationState
from app.infrastructure.web.fetcher import HttpFetcher
from tests.conftest import FakeExtractor, FakeGateway, FakeLLM, add_doc, make_pdf_pages
from tests.integration.test_chatwoot_handler import KEY, msg_event, process
from tests.integration.test_collection import BV, HTML_BV, REG, FakeFetcher
from tests.integration.test_orchestrator import P1, P2, build, out


# ------------------------------------------------------------------ R-D: COL-05 sobrevive a recoletas repetidas
def test_recoletas_repetidas_nao_republicam_conteudo_vencido(store, embedder):
    from datetime import datetime, timedelta, timezone

    svc = CollectionService(store, SourceRegistry.from_file(REG), FakeFetcher({BV: HTML_BV}), embedder)
    r = svc.collect_url(D.INSTITUCIONAL, BV)
    store.set_review(r["doc_id"], R.APPROVED, reviewer="Cur", reason="ok")
    assert svc.refresh_staleness(now=datetime.now(timezone.utc) + timedelta(days=400)) == [r["doc_id"]]
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "pending_review"
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "pending_review"  # a segunda recoleta também
    store.set_review(r["doc_id"], R.APPROVED, reviewer="Cur2", reason="revisado de novo")  # só revisão humana reabre
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "approved"


# ------------------------------------------------------------------------------------ R-A: aceite do encaminhamento
@pytest.mark.parametrize("msg", ["sim", "Sim, pode encaminhar", "quero sim", "pode", "ok", "claro, por favor", "sim por favor"])
def test_aceites_legitimos(msg):
    assert _is_affirmative(msg)


@pytest.mark.parametrize("msg", [
    "quero saber o horário", "pode informar o horário", "ok, qual o horário do balcão", "não quero", "sim, mas qual é o horário?",
    "sim " + "palavra " * 10, "", "qual o horário",
])
def test_nao_sao_aceites(msg):
    assert not _is_affirmative(msg)


def test_pergunta_com_palavra_de_aceite_depois_de_oferta_e_respondida_nao_transfere(store, embedder):
    add_doc(store, embedder, doc_id="i1", text="O Balcão Virtual funciona de segunda a sexta-feira, das 08h às 14h.", domain=D.INSTITUCIONAL, process_number=None,
            pje_doc_id=None, title="Balcão Virtual", doc_type="pagina_institucional", url="https://x/bv")
    orc = build(store, embedder, FakeLLM(out("Funciona das 08h às 14h [E1].")))
    st = ConversationState("t")
    st.offer_pending = True
    t = orc.respond("quero saber o horário de funcionamento do Balcão Virtual", st)
    assert t.reply.kind.value == "answer"


# --------------------------------------------------------------------------------- R-B: falsos positivos de atendente
@pytest.mark.parametrize("msg", [
    "Quando devo chamar a pessoa citada para a audiência?", "Preciso saber se a pessoa jurídica foi citada",
    "Quero saber o que a pessoa disse na audiência", "A decisão mandou encaminhar os autos ao servidor?",
    "O juiz determinou passar os autos para a pessoa responsável?",
])
def test_vocabulario_processual_nao_vira_pedido_de_atendente(msg):
    assert classify_intent(msg).intent is not Intent.ATENDIMENTO_HUMANO


# --------------------------------------------------------------------------------- R-C: processo sem número (ORQ-03)
def test_termo_exclusivo_de_um_processo_identifica_o_processo_sem_fixa_lo(store, embedder):
    add_doc(store, embedder, doc_id="a", text="Decisão sobre o contrato bancário e a cobrança indevida de tarifas.", process_number=P1, pje_doc_id="1")
    add_doc(store, embedder, doc_id="b", text="Decisão sobre a gratificação de engenharia dos servidores municipais.", process_number=P2, pje_doc_id="2")
    llm = FakeLLM(out("A decisão trata de tarifas bancárias [E1]."))
    st = ConversationState("s")
    t = build(store, embedder, llm).respond("Qual a decisão sobre a cobrança indevida de tarifas bancárias?", st)
    assert t.reply.kind.value == "answer" and f"Considerei o processo {P1}" in t.reply.text and st.process_number is None


def test_termos_que_aparecem_em_varios_processos_pedem_esclarecimento(store, embedder):
    add_doc(store, embedder, doc_id="a", text="Decisão sobre a cobrança indevida de tarifas bancárias.", process_number=P1, pje_doc_id="1")
    add_doc(store, embedder, doc_id="b", text="Decisão sobre a cobrança indevida de tarifas bancárias.", process_number=P2, pje_doc_id="2")
    t = build(store, embedder, FakeLLM(out("x"))).respond("Qual a decisão sobre a cobrança indevida de tarifas bancárias?", ConversationState("s"))
    assert t.reply.kind.value == "clarify" and P1 in t.reply.text and P2 in t.reply.text


def test_termos_exclusivos_de_processos_diferentes_pedem_esclarecimento(store, embedder):
    add_doc(store, embedder, doc_id="a", text="Decisão sobre tarifas bancárias.", process_number=P1, pje_doc_id="1")
    add_doc(store, embedder, doc_id="b", text="Decisão sobre gratificação de engenharia.", process_number=P2, pje_doc_id="2")
    t = build(store, embedder, FakeLLM(out("x"))).respond("Qual a decisão sobre tarifas bancárias e gratificação de engenharia?", ConversationState("s"))
    assert t.reply.kind.value == "clarify"


# ------------------------------------------------------------------------ N103/N103b/N104: eco e status sem carimbo
def test_eco_com_id_proprio_da_mensagem_do_bot_nao_silencia_o_bot(store, embedder, ops):
    add_doc(store, embedder, doc_id="d1", text="Decido. Designo audiência de conciliação para 20/07/2026.", doc_type="Despacho")
    gw = FakeGateway()
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("É em 20/07/2026 [E1]."))), ops, gw)
    process(h, msg_event(msg_id=100))  # mensagem do cliente
    bot_msg_id = 1  # id devolvido pelo gateway para a resposta
    assert ops.has_event(f"botmsg:1:{bot_msg_id}")
    echo = msg_event("É em 20/07/2026", msg_id=500, mtype="outgoing", sender_type="user")
    echo["id"] = bot_msg_id
    assert process(h, echo).outcome in ("ignored", "duplicate")
    assert ops.get(KEY).handoff_state is S.BOT_ACTIVE
    assert process(h, msg_event(msg_id=101)).outcome == "processed"


def test_dois_resolved_sem_carimbo_fecham_duas_vezes(store, embedder, ops):
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, FakeGateway())
    process(h, msg_event("quero falar com um atendente", msg_id=1))
    ev = {"event": "conversation_resolved", "id": 77, "status": "resolved"}
    assert process(h, ev).outcome == "processed" and ops.get(KEY).handoff_state is S.HUMAN_CLOSED
    ops.transition(KEY, S.HUMAN_ACTIVE, actor="chatwoot")  # reaberto por um humano
    assert process(h, dict(ev)).outcome == "processed" and ops.get(KEY).handoff_state is S.HUMAN_CLOSED  # não foi tratado como duplicata


# ----------------------------------------------------------------------------------------- N105: capa oculta pendentes
def test_capa_oculta_documento_pendente_por_sigilo_no_texto(store, embedder, tmp_path):
    pages = make_pdf_pages()
    pages[2] = pages[2].replace("Decido.", "Este feito tramita em segredo de justiça. Decido.", 1)
    pdf = tmp_path / "1234567-89.2026.8.03.0001.pdf"
    pdf.write_bytes(b"%PDF-s")
    IngestionService(store, FakeExtractor(pages), None, embedder, IngestionConfig(corpus_authorized=True)).ingest_file(pdf)
    doc = [d for d in store.list_documents(limit=20) if d.pje_doc_id == "1000002"][0]
    assert doc.review_state is R.PENDING_REVIEW
    cover = " ".join(r["text"] for r in store._exec("select text from chunks where doc_id like '%:capa'"))
    assert "1000002; Decisão" not in cover and "id 1000002" in cover and "acesso em revisão" in cover


# --------------------------------------------------------------------------- N100/N98c/N109/N110: transferência e retry
def test_falha_ao_enviar_o_aviso_depois_de_transferir_nao_desfaz_a_transferencia(store, embedder, ops):
    class NoNotice(FakeGateway):
        def send_message(self, *a, **k):
            raise RuntimeError("falha de envio")

    gw = NoNotice()
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, gw)
    assert process(h, msg_event("quero falar com um atendente", msg_id=1)).outcome == "handoff"
    assert ops.get(KEY).handoff_state is S.HUMAN_ACTIVE and "ia_falha" in gw.labels[77]
    assert "handoff_notice_failed" in [a["action"] for a in ops.audit_rows(KEY)]


def test_retry_zera_as_tentativas_e_a_proxima_falha_nao_conta_como_esgotada(store, embedder, ops):
    gw = FakeGateway(fail_assign=99)
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, gw, max_handoff_attempts=3)
    process(h, msg_event("quero falar com um atendente", msg_id=1))
    for i in (2, 3):
        process(h, msg_event("alô", msg_id=i))
    assert ops.get(KEY).handoff_attempts == 3
    gw.fail_assign = 1  # falha só mais uma vez
    h.retry_handoff(1, 77, "sup", "tentar de novo")
    st = ops.get(KEY)
    assert st.handoff_state is S.HANDOFF_REQUESTED and st.handoff_attempts == 1  # o contador recomeçou do zero
    assert "Vou tentar de novo" in gw.sent[-1][1]  # e a falha não é tratada como esgotada
    assert process(h, msg_event("alô?", msg_id=9)).outcome == "handoff"  # a próxima mensagem ainda tem tentativas


def test_retry_handoff_recusa_outros_estados(store, embedder, ops):
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, FakeGateway())
    process(h, msg_event("oi", msg_id=1))  # bot_active
    with pytest.raises(InvalidTransition):
        h.retry_handoff(1, 77, "x", "y")


def test_so_status_falso_tambem_impede_a_confirmacao(store, embedder, ops):
    class NoStatus(FakeGateway):
        def set_status(self, *a, **k):
            return False

    gw = NoStatus()
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, gw)
    assert process(h, msg_event("quero falar com um atendente", msg_id=1)).outcome == "error"
    assert not any("Encaminhei" in t for _, t in gw.sent)


def test_retomada_devolve_a_conversa_ao_status_pending(store, embedder, ops):
    gw = FakeGateway()
    h = ChatwootEventHandler(build(store, embedder, FakeLLM(out("x"))), ops, gw)
    process(h, msg_event("quero falar com um atendente", msg_id=1))
    ops.transition(KEY, S.HUMAN_CLOSED, actor="chatwoot")
    h.resume_automation(1, 77, "sup", "ok")
    assert gw.statuses[-1] == (77, "pending")


# --------------------------------------------------------------------------------------- N113: esclarecimento só lista elegíveis
def test_esclarecimento_so_lista_processos_com_documento_aprovado(store, embedder):
    add_doc(store, embedder, doc_id="a", text="Texto aprovado.", process_number=P1, pje_doc_id="1")
    add_doc(store, embedder, doc_id="b", text="Texto pendente.", process_number=P2, pje_doc_id="2", state=R.PENDING_REVIEW, index=False)
    assert store.approved_process_numbers() == [P1]


# ------------------------------------------------------------------------------------ RAG-01 pelo retriever e M112
class ScriptedStore:
    """Armazenamento mínimo com rankings controlados, para testar a fusão dentro do HybridRetriever."""

    def __init__(self, lex, vec):
        self.lex, self.vec = lex, vec

    def known_process_numbers(self):
        return []

    def search_lexical(self, q, domain, limit, process_number=None, doc_date=None):
        return [(c, 1.0) for c in self.lex]

    def search_vector(self, v, domain, limit, process_number=None, doc_date=None):
        return [(c, 0.9) for c in self.vec]

    def load_evidences(self, ids, domain):
        return [Evidence(i, f"d{i}", domain, f"texto audiência {i}", 1, 0.0, citation={"processo": P1}) for i in ids]


def test_retriever_funde_as_duas_listas_com_k_60(embedder):
    r = HybridRetriever(ScriptedStore([1, 2], [2, 3]), embedder, policy=AbstentionPolicy(min_vector_score=0.0, top_k=3))
    assert r.rrf_k == 60
    res = r.retrieve("audiência", D.PROCESSUAL)
    assert [e.chunk_id for e in res.evidences] == [2, 1, 3]
    assert res.evidences[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_resultado_da_segunda_tentativa_e_o_usado(store, embedder):
    ok = Evidence(1, "d1", D.PROCESSUAL, "A audiência será em 20/07/2026.", 1, 0.1, lexical_rank=1,
                  citation={"processo": P1, "tipo": "Despacho", "titulo": "Despacho", "data": "2026-06-01", "documento": "9", "pagina_pdf": "2", "arquivo": "a.pdf"})

    class Flaky:
        calls = 0

        def retrieve(self, q, domain, **kw):
            Flaky.calls += 1
            if Flaky.calls == 1:
                return RetrievalResult([], None, [], 0.0, "sem_resultados", {})
            return RetrievalResult([ok], P1, [], 1.0, None, {})

    llm = FakeLLM(out("A audiência será em 20/07/2026 [E1]."))
    t = Orchestrator(Flaky(), store, llm, OrchestratorConfig()).respond(f"Quando é a audiência do processo {P1}?", ConversationState("t"))
    assert Flaky.calls == 2 and t.reply.kind.value == "answer" and "A audiência será em 20/07/2026" in llm.prompts[0][1]


# --------------------------------------------------------------------------------- B2 resíduos e N12d
def _check(answer, text):
    e = Evidence(1, "d1", D.PROCESSUAL, text, 2, 0.1, citation={"processo": P1})
    return verify_grounding(ModelAnswer(answer, ["E1"], True, False), [e], {"E1": e}, "pergunta")


@pytest.mark.parametrize("answer,text", [
    ("Aplica-se o art. 99 do CPC [E1].", "aplica-se o art. 98 do CPC"),
    ("O prazo é de dez dias [E1].", "o prazo é de 5 dias"),
    ("Há 4 réus [E1].", "há 3 réus no processo"),
    ("Prazo de 15 dias [E1].", "prazo de 115 dias"),
])
def test_residuos_de_fundamentacao_numerica_reprovam(answer, text):
    assert not _check(answer, text).ok


@pytest.mark.parametrize("answer,text", [
    ("Aplica-se o art. 98 do CPC [E1].", "aplica-se o art. 98, § 5º, do CPC"),
    ("O prazo é de dez dias [E1].", "o prazo é de 10 dias"),
    ("O prazo é de dez dias [E1].", "o prazo é de dez dias úteis"),
    ("Há 3 réus [E1].", "há 3 réus no processo"),
])
def test_equivalentes_legitimos_continuam_aprovados(answer, text):
    assert _check(answer, text).ok


# ------------------------------------------------------------------------- N35b/N35c/N115/R-E: fetcher
def _fetcher(handler):
    return HttpFetcher(SourceRegistry.from_file(REG).settings, transport=httpx.MockTransport(handler))


def test_redirect_para_http_e_recusado():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://centralservicos.tjpa.jus.br/novo"})

    with pytest.raises(CollectionError):
        _fetcher(handler).fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php")


def test_robots_e_reavaliado_para_o_destino_do_redirect():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privado/")
        if req.url.path == "/bv/balcao.php":
            return httpx.Response(302, headers={"location": "/privado/x"})
        return httpx.Response(200, text="<html>x</html>", headers={"content-type": "text/html"})

    with pytest.raises(CollectionError, match="robots"):
        _fetcher(handler).fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php")


def test_limite_de_saltos_de_redirect():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        n = int(req.url.path.strip("/") or 0) if req.url.path.strip("/").isdigit() else 0
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    with pytest.raises(CollectionError, match="demais"):
        _fetcher(handler).fetch("https://centralservicos.tjpa.jus.br/0")


def test_robots_com_redirect_no_mesmo_host_e_seguido():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(301, headers={"location": "/robots-novo.txt"})
        if req.url.path == "/robots-novo.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:")
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})

    assert _fetcher(handler).fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php").status == 200
