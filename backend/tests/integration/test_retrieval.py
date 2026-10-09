"""Hybrid RAG: fusão, isolamento de domínios, filtros de acesso e abstenção (RAG-01..RAG-04, RAG-09)."""
from app.application.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from app.domain.models import AccessClass, KnowledgeDomain as D, ReviewState as R
from app.domain.policies import AbstentionPolicy
from tests.conftest import add_doc

P = "1234567-89.2026.8.03.0001"


def seed(store, embedder):
    add_doc(store, embedder, doc_id="d1", text="Decido. Defiro a gratuidade da justiça à parte autora e determino a citação da ré.")
    add_doc(store, embedder, doc_id="d2", text="Designo audiência de conciliação para o dia 20/07/2026 às 10h.", doc_type="Despacho", title="Despacho")
    add_doc(store, embedder, doc_id="d3", text="Certifico o decurso de prazo sem manifestação da ré.", doc_type="Certidão", title="Certidão",
            process_number="7654321-00.2025.8.03.0001")
    add_doc(store, embedder, doc_id="i1", text="O Balcão Virtual funciona de segunda a sexta, das 08h às 14h.", domain=D.INSTITUCIONAL,
            process_number=None, pje_doc_id=None, title="Balcão Virtual", doc_type="pagina_institucional", url="https://exemplo.tjpa.jus.br/bv")
    add_doc(store, embedder, doc_id="x_pend", text="Segredo: contracheque da autora com remuneração.", state=R.PENDING_REVIEW)
    add_doc(store, embedder, doc_id="x_rej", text="Texto rejeitado sobre remuneração e contracheque.", state=R.REJECTED)
    add_doc(store, embedder, doc_id="x_restr", text="Texto restrito sobre remuneração e contracheque.", access=AccessClass.RESTRICTED, index=False)


def retr(store, embedder, **kw):
    return HybridRetriever(store, embedder, policy=AbstentionPolicy(top_k=5), **kw)


def test_rrf_combina_listas_e_premia_consenso():  # RAG-01
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 2, 9]], k=60)
    assert fused[2] > fused[1] and fused[3] > fused[1] and fused[2] == fused[3] or fused[3] >= fused[2]
    assert abs(fused[1] - 1 / 61) < 1e-9


def test_busca_hibrida_traz_trecho_correto_com_ranks_lexical_e_vetorial(store, embedder):
    seed(store, embedder)
    res = retr(store, embedder).retrieve("Quando é a audiência de conciliação?", D.PROCESSUAL)
    assert res.sufficient and res.evidences[0].doc_id == "d2"
    assert res.evidences[0].lexical_rank and res.evidences[0].vector_rank


def test_dominios_sao_isolados(store, embedder):  # RAG-02
    seed(store, embedder)
    r = retr(store, embedder)
    proc = r.retrieve("horário de funcionamento do Balcão Virtual", D.PROCESSUAL)
    inst = r.retrieve("horário de funcionamento do Balcão Virtual", D.INSTITUCIONAL)
    assert all(e.domain is D.PROCESSUAL for e in proc.evidences)
    assert [e.doc_id for e in inst.evidences] == ["i1"] and inst.sufficient


def test_pendentes_rejeitados_e_restritos_nunca_aparecem(store, embedder):  # RAG-03, SEC
    seed(store, embedder)
    res = retr(store, embedder).retrieve("contracheque remuneração da autora", D.PROCESSUAL)
    assert not {"x_pend", "x_rej", "x_restr"} & {e.doc_id for e in res.evidences}


def test_documento_revogado_depois_da_indexacao_sai_da_busca(store, embedder):  # COL-08/CUR
    seed(store, embedder)
    store.set_review("d2", R.REJECTED, reviewer="Rev", reason="erro de extração")
    res = retr(store, embedder).retrieve("audiência de conciliação", D.PROCESSUAL)
    assert "d2" not in {e.doc_id for e in res.evidences}


def test_processo_citado_e_ausente_do_acervo_gera_abstencao(store, embedder):  # RAG-04
    seed(store, embedder)
    res = retr(store, embedder).retrieve("Qual a decisão do processo 9999999-99.2026.8.03.0001?", D.PROCESSUAL)
    assert res.abstain_reason == "processo_ausente_do_acervo" and res.evidences == []


def test_numero_de_processo_restringe_ao_processo(store, embedder):
    seed(store, embedder)
    res = retr(store, embedder).retrieve(f"certidão decurso de prazo do processo {P}", D.PROCESSUAL)
    assert all(e.citation["processo"] == P for e in res.evidences)


def test_pergunta_sem_correspondencia_abstem(store, embedder):  # RAG-04
    seed(store, embedder)
    res = retr(store, embedder).retrieve("receita de bolo de chocolate com cobertura", D.PROCESSUAL)
    assert not res.sufficient and res.abstain_reason in {"sem_resultados", "sem_correspondencia_lexical", "baixa_cobertura_dos_termos_da_pergunta", "evidencias_insuficientes"}


def test_reranker_reordena_candidatos(store, embedder):  # RAG-09
    seed(store, embedder)

    class Reverse:
        def rerank(self, query, evidences):
            return list(reversed(evidences))

    base = retr(store, embedder).retrieve("gratuidade da justiça e citação da ré", D.PROCESSUAL)
    rer = retr(store, embedder, reranker=Reverse()).retrieve("gratuidade da justiça e citação da ré", D.PROCESSUAL)
    assert rer.stages.get("reranked") is True
    assert [e.doc_id for e in rer.evidences] != [e.doc_id for e in base.evidences] or len(base.evidences) == 1


def test_falha_do_reranker_nao_derruba_a_recuperacao(store, embedder):
    seed(store, embedder)

    class Broken:
        def rerank(self, query, evidences):
            raise RuntimeError("modelo indisponível")

    res = retr(store, embedder, reranker=Broken()).retrieve("audiência de conciliação", D.PROCESSUAL)
    assert res.sufficient and res.stages["reranked"] is False
