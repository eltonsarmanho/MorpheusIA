"""Portões de promoção (checkpoint-promotion) sobre o índice REAL. Rodam com `pytest -m corpus`.

Critérios objetivos: recuperação, controle de acesso e abstenção. Não usam LLM, então são repetíveis e gratuitos.
"""
import pytest

from app.config import get_settings
from app.container import build_container
from app.domain.models import KnowledgeDomain, ReviewState
from evals.run_eval import load_questions, retrieval_eval

pytestmark = pytest.mark.corpus

MIN_RECALL, MIN_MRR = 0.85, 0.50


@pytest.fixture(scope="module")
def world():
    s = get_settings()
    if not s.resolved_knowledge_db().exists():
        pytest.skip("índice não construído (rode `cli ingest`)")
    return build_container(s, llm=None, gateway=None)


def test_recall_e_mrr_minimos(world):
    r = retrieval_eval(world, load_questions(), k=6)
    assert r["n"] >= 10
    assert r["recall_at_k"] >= MIN_RECALL, f"falhas: {r['failures']}"
    assert r["mrr"] >= MIN_MRR


def test_nenhum_resultado_vem_de_documento_nao_aprovado(world):
    ids = set()
    for q in load_questions():
        res = world.retriever.retrieve(q["q"], KnowledgeDomain.PROCESSUAL, process_number=q.get("process"))
        ids.update(e.doc_id for e in res.evidences)
    assert ids
    for doc_id in ids:
        d = world.store.get_document(doc_id)
        assert d.review_state is ReviewState.APPROVED and d.access_class.value == "public"


def test_processo_ausente_gera_abstencao(world):
    res = world.retriever.retrieve("decisão do processo 9999999-99.2026.8.03.0001", KnowledgeDomain.PROCESSUAL)
    assert res.abstain_reason == "processo_ausente_do_acervo"


def test_pergunta_sem_relacao_com_o_acervo_abstem_na_recuperacao(world):
    for q in ("receita de bolo de chocolate com cobertura", "como instalar o python no windows"):
        assert not world.retriever.retrieve(q, KnowledgeDomain.PROCESSUAL).sufficient


def test_pergunta_fora_de_escopo_nao_chega_nem_a_recuperacao(world):
    from app.infrastructure.sqlite.operational_store import ConversationState

    t = world.orchestrator.respond("qual a previsão do tempo amanhã em Belém", ConversationState("gate"))
    assert t.reply.kind.value == "abstain" and t.reply.abstain_reason == "fora_de_escopo"


def test_dados_pessoais_nao_estao_no_indice_processual(world):
    import re

    texts = [r["text"] for r in world.store._exec("select c.text from chunks c where c.domain='processual'").fetchall()]
    patterns = {
        "cpf": r"\d{3,4}\.\d{3}\.\d{3}-\d{2}", "cep": r"(?<!\d)\d{5}-\d{3}(?!\d)", "email": r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+",
        "telefone": r"\(\s?(?!00)\d{2}\s?\)\s?\d{4,5}[-\s]?\d{4}", "rg": r"(?i)\bRG\b\W{0,6}\d{5,}", "cnh": r"(?i)\bCNH\b\W{0,6}\d{5,}",
        "id11": r"(?<![\d.\-/])\d{11}(?![\d.\-/])",
    }
    leaks = {k: sum(1 for t in texts if re.search(p, t)) for k, p in patterns.items()}
    assert not any(leaks.values()), leaks


def test_titulo_e_contexto_dos_documentos_tambem_estao_mascarados(world):
    import re

    rows = world.store._exec("select title as t from documents where domain='processual' union all select ctx from chunks where domain='processual'").fetchall()
    blob = "\n".join(r["t"] or "" for r in rows)
    assert not re.search(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", blob)
    assert not re.search(r"\d{3,4}\.\d{3}\.\d{3}-\d{2}", blob)
