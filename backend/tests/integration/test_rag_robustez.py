"""Robustez do RAG após a falha real "Que crime foi imputado ao réu na sentença?" (processo no contexto, sem o número na frase)."""
import json

from app.application.answering.grounding import ModelAnswer, repair_citations
from app.application.answering.orchestrator import Orchestrator, OrchestratorConfig
from app.application.retrieval.hybrid import HybridRetriever, find_doc_type
from app.domain.models import Evidence, KnowledgeDomain as D
from app.domain.policies import AbstentionPolicy
from app.infrastructure.sqlite.knowledge_store import fts_query
from app.infrastructure.sqlite.operational_store import ConversationState
from tests.conftest import FakeLLM, add_doc

P = "1234567-89.2026.8.03.0001"


def ev(i, text):
    return Evidence(i, f"d{i}", D.PROCESSUAL, text, 1, 0.1, citation={"processo": P})


def test_bm25_inclui_o_radical_para_alcancar_flexoes():
    q = fts_query("Que crime foi imputado ao réu?")
    assert '"imputado"' in q and '"imput"*' in q


def test_tipo_documental_citado_na_pergunta():
    assert find_doc_type("Que crime foi imputado na sentença?") == "senten"
    assert find_doc_type("o que diz o acórdão") == "acórd"
    assert find_doc_type("qual o valor da causa") is None


def test_pergunta_sobre_a_sentenca_traz_o_cabecalho_da_sentenca(store, embedder):
    add_doc(store, embedder, doc_id="s1", text="SENTENÇA. O Ministério Público denunciou o réu, imputando-lhe o crime previsto no art. 157 do Código Penal.",
            doc_type="Sentença", title="Sentença", pje_doc_id="10")
    for i in range(8):
        add_doc(store, embedder, doc_id=f"x{i}", text=f"Crime e réu discutidos na apelação número {i}, com argumentos sobre a sentença e a pena.",
                doc_type="Apelação", title="Apelação", pje_doc_id=str(20 + i))
    res = HybridRetriever(store, embedder, policy=AbstentionPolicy(top_k=4, min_vector_score=0.0)).retrieve(
        "Que crime foi imputado ao réu na sentença?", D.PROCESSUAL, process_number=P)
    assert "s1" in [e.doc_id for e in res.evidences] and res.stages["doc_type_boost"].startswith("senten")


def test_reparo_de_citacao_aceita_fato_presente_em_outro_trecho_recebido():
    labels = {"E1": ev(1, "Narrativa dos fatos sem artigo."), "E2": ev(2, "Crime previsto no art. 157 do Código Penal.")}
    fixed, report = repair_citations(ModelAnswer("O crime é o do art. 157 [E1].", ["E1"], True, False), labels, "pergunta")
    assert report.ok and fixed.references == ["E1", "E2"]


def test_reparo_nao_aceita_fato_ausente_de_todo_o_contexto():
    labels = {"E1": ev(1, "Narrativa dos fatos.")}
    _, report = repair_citations(ModelAnswer("O crime é o do art. 157 [E1].", ["E1"], True, False), labels, "pergunta")
    assert not report.ok


def test_segunda_tentativa_guiada_corrige_a_resposta(store, embedder):
    add_doc(store, embedder, doc_id="s1", text="SENTENÇA. Imputado o crime de roubo majorado, art. 157 do Código Penal.", doc_type="Sentença", pje_doc_id="10")
    bad = json.dumps({"resposta": "Roubo, art. 180 do CP [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False})
    good = json.dumps({"resposta": "Roubo majorado, art. 157 do Código Penal [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False})
    llm = FakeLLM(bad, good)
    orc = Orchestrator(HybridRetriever(store, embedder, policy=AbstentionPolicy(min_vector_score=0.0)), store, llm, OrchestratorConfig())
    st = ConversationState("t")
    st.process_number = P
    t = orc.respond("Que crime foi imputado ao réu na sentença?", st)
    assert t.reply.kind.value == "answer" and "art. 157" in t.reply.text and len(llm.prompts) == 2
    assert "fatos sem lastro" in llm.prompts[1][1] and t.reply.trace["grounding_first_try"]


def test_pergunta_curta_de_continuacao_com_processo_em_foco_e_processual(store, embedder):
    add_doc(store, embedder, doc_id="s1", text="A vítima foi Fulano de Tal, que reconheceu o acusado.", doc_type="Sentença", pje_doc_id="10")
    llm = FakeLLM(json.dumps({"resposta": "A vítima foi Fulano de Tal [E1].", "referencias": ["E1"], "suficiente": True, "encaminhar": False}))
    orc = Orchestrator(HybridRetriever(store, embedder, policy=AbstentionPolicy(min_vector_score=0.0)), store, llm, OrchestratorConfig())
    st = ConversationState("t")
    st.process_number = P
    t = orc.respond("E quem foi a vítima?", st)  # sem "processo" na frase: vale o processo em foco
    assert t.reply.domain is D.PROCESSUAL and t.reply.kind.value == "answer"


def test_mes_e_ano_na_pergunta_viram_filtro_de_data():
    from app.application.retrieval.hybrid import find_query_date

    assert find_query_date("na sentença de abril de 2026") == "2026-04"
    assert find_query_date("decisão de 04/2026") == "2026-04"
    assert find_query_date("decisão de 14/05/2026") == "2026-05-14"
    assert find_query_date("art. 98, § 5º") is None


def test_com_duas_sentencas_a_citada_pelo_mes_e_a_escolhida(store, embedder):
    add_doc(store, embedder, doc_id="s2021", text="SENTENÇA. Extingo o feito por perempção.", doc_type="Sentença", doc_date="2021-06-23", pje_doc_id="1")
    add_doc(store, embedder, doc_id="s2026", text="SENTENÇA. Julgo inadmissível a ação e extingo o processo sem resolução de mérito.",
            doc_type="Sentença", doc_date="2026-04-15", pje_doc_id="2")
    res = HybridRetriever(store, embedder, policy=AbstentionPolicy(top_k=4, min_vector_score=0.0)).retrieve(
        "Como terminou a ação na sentença de abril de 2026?", D.PROCESSUAL, process_number=P)
    assert res.evidences[0].doc_id == "s2026" and "s2021" not in [e.doc_id for e in res.evidences]


def test_pergunta_sobre_o_desfecho_traz_o_dispositivo_da_sentenca(store, embedder):
    add_doc(store, embedder, doc_id="rel", text="SENTENÇA. Relatório: trata-se de ação declaratória ajuizada pelos autores contra o Estado.", doc_type="Sentença",
            doc_date="2026-04-15", pje_doc_id="1")
    add_doc(store, embedder, doc_id="disp", text="Diante do exposto, julgo inadmissível a presente ação e extingo o processo sem resolução de mérito.",
            doc_type="Sentença", doc_date="2026-04-15", pje_doc_id="1b")
    for i in range(6):
        add_doc(store, embedder, doc_id=f"o{i}", text=f"Ação declaratória ajuizada pelos autores, argumentação número {i} sobre a sentença.", doc_type="Petição",
                pje_doc_id=str(10 + i))
    res = HybridRetriever(store, embedder, policy=AbstentionPolicy(top_k=4, min_vector_score=0.0)).retrieve(
        "Como terminou a ação na sentença?", D.PROCESSUAL, process_number=P)
    assert "disp" in [e.doc_id for e in res.evidences]
