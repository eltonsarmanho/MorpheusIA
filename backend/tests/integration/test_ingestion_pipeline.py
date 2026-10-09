"""Pipeline de ingestão com dublês de PDF/OCR (ING-01..ING-10, CUR-01..CUR-03, SEC-01)."""
import hashlib
from pathlib import Path

from app.application.ingestion.service import IngestionConfig, IngestionService
from app.domain.models import KnowledgeDomain, ReviewState
from tests.conftest import FakeExtractor, FakeOcr, make_pdf_pages, pje_footer


def make_service(store, embedder, pages=None, ocr=None, authorized=True):
    cfg = IngestionConfig(corpus_authorized=authorized, authorization_basis="teste")
    return IngestionService(store, FakeExtractor(pages or make_pdf_pages()), ocr, embedder, cfg, page_counter=lambda p: 5)


def write_pdf(tmp_path: Path, name="1234567-89.2026.8.03.0001.pdf", content=b"%PDF-fake-1") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_inventario_calcula_sha256_e_marca_duplicata_exata(store, embedder, tmp_path):  # ING-01
    write_pdf(tmp_path, "a.pdf", b"mesmo")
    write_pdf(tmp_path, "b.pdf", b"mesmo")
    inv = make_service(store, embedder).inventory(tmp_path)
    assert inv[0]["sha256"] == hashlib.sha256(b"mesmo").hexdigest() and inv[0]["duplicate_of"] is None
    assert inv[1]["duplicate_of"] == "a.pdf"


def test_ingestao_gera_documentos_metadados_e_estados(store, embedder, tmp_path):
    pdf = write_pdf(tmp_path)
    before = pdf.read_bytes()
    rep = make_service(store, embedder).ingest_file(pdf)
    assert rep.status == "ok" and rep.process_number == "1234567-89.2026.8.03.0001"
    assert pdf.read_bytes() == before  # PDF original intocado
    docs = {d.pje_doc_id: d for d in store.list_documents(limit=50)}
    dec = docs["1000002"]
    assert dec.doc_type == "Decisão" and dec.doc_date == "2026-05-14" and dec.page_start == 3 and dec.source_file == pdf.name
    assert dec.review_state is ReviewState.APPROVED and dec.indexed_at and dec.content_hash  # ING-06/ING-07
    assert docs["1000003"].review_state is ReviewState.PENDING_REVIEW  # CUR-02: contracheque
    assert rep.documents_pending >= 1


def test_so_documentos_aprovados_sao_indexados_e_pendentes_nao_aparecem_na_busca(store, embedder, tmp_path):  # CUR-01, RAG-03
    make_service(store, embedder).ingest_file(write_pdf(tmp_path))
    hits = store.search_lexical("contracheque remuneração", KnowledgeDomain.PROCESSUAL, 10)
    assert hits == []
    assert store.search_lexical("audiência conciliação", KnowledgeDomain.PROCESSUAL, 10)


def test_dados_pessoais_mascarados_no_indice(store, embedder, tmp_path):  # SEC-01
    rep = make_service(store, embedder).ingest_file(write_pdf(tmp_path))
    texts = " ".join(r["text"] for r in store._exec("select text from chunks").fetchall())
    assert "111.222.333-44" not in texts and "68900-000" not in texts and "[CPF]" in texts
    assert rep.pii_counts.get("cpf") == 1 and rep.pii_counts.get("cep") == 1


def test_reingestao_pula_arquivo_inalterado_e_reprocessa_se_mudou(store, embedder, tmp_path):  # ING-09
    pdf = write_pdf(tmp_path)
    svc = make_service(store, embedder)
    assert svc.ingest_file(pdf).status == "ok"
    assert svc.ingest_file(pdf).status == "skipped_unchanged"
    pdf.write_bytes(b"%PDF-fake-2")
    assert svc.ingest_file(pdf).status == "ok"
    assert len(store.list_documents(limit=100)) == 5  # substituiu, não duplicou


def test_ocr_somente_em_paginas_sem_corpo_e_registra_confianca(store, embedder, tmp_path):  # ING-03
    pages = make_pdf_pages()
    pages[2] = pje_footer("1000002", 1, "JUIZ TESTE", "14/05/2026 20:43:39")  # página só com rodapé
    ocr = FakeOcr("Decido. Defiro o pedido de tutela de urgência formulado pela autora.", 91.0)
    rep = make_service(store, embedder, pages, ocr).ingest_file(write_pdf(tmp_path))
    assert ocr.calls == [3] and rep.pages_ocr == 1
    row = store._exec("select method, ocr_conf, status from pages where pdf_page=3").fetchone()
    assert (row["method"], row["ocr_conf"], row["status"]) == ("ocr", 91.0, "ok")
    assert store.search_lexical("tutela urgência", KnowledgeDomain.PROCESSUAL, 5)


def test_ocr_de_baixa_confianca_vai_para_revisao_e_fica_fora_do_indice(store, embedder, tmp_path):  # ING-04
    pages = make_pdf_pages()
    pages[2] = pje_footer("1000002", 1)
    ocr = FakeOcr("xq zzv kkl texto ilegível sobre tutela borrada", 31.0)
    rep = make_service(store, embedder, pages, ocr).ingest_file(write_pdf(tmp_path))
    assert rep.pages_needs_review == 1
    assert store.search_lexical("ilegível borrada", KnowledgeDomain.PROCESSUAL, 5) == []


def test_chunks_repetidos_no_mesmo_processo_sao_marcados_como_duplicata(store, embedder, tmp_path):  # ING-08
    pages = make_pdf_pages()
    pages.append("Decido. Designo audiência de conciliação para 20/07/2026 às 10h. Intimem-se." + pje_footer("1000004", 2, "JUIZ TESTE", "01/06/2026 11:00:10"))
    rep = make_service(store, embedder, pages).ingest_file(write_pdf(tmp_path))
    assert rep.chunks_duplicate == 1


def test_decisao_humana_e_preservada_quando_o_conteudo_nao_muda(store, embedder, tmp_path):  # CUR-03
    pdf = write_pdf(tmp_path)
    svc = make_service(store, embedder)
    svc.ingest_file(pdf)
    doc_id = "proc-1234567-89.2026.8.03.0001:1000003"
    store.set_review(doc_id, ReviewState.APPROVED, reviewer="Revisora", reason="documento sem dado sensível")
    pdf.write_bytes(b"%PDF-fake-3")
    svc.ingest_file(pdf)
    d = store.get_document(doc_id)
    assert d.review_state is ReviewState.APPROVED and "Revisora" in d.review_reason


def test_acervo_sem_autorizacao_nao_aprova_nada(store, embedder, tmp_path):  # CUR-01
    make_service(store, embedder, authorized=False).ingest_file(write_pdf(tmp_path))
    assert all(d.review_state is ReviewState.PENDING_REVIEW for d in store.list_documents(limit=50))
    assert store.indexed_chunk_count() == 0


def test_erro_de_extracao_nao_derruba_os_demais_arquivos(store, embedder, tmp_path):  # ING-10 + edge case
    class Boom(FakeExtractor):
        def extract_pages(self, path):
            if path.name.startswith("bad"):
                raise RuntimeError("pdf corrompido")
            return super().extract_pages(path)

    write_pdf(tmp_path, "bad-1.pdf", b"x1")
    write_pdf(tmp_path, "1234567-89.2026.8.03.0001.pdf", b"x2")
    svc = IngestionService(store, Boom(make_pdf_pages()), None, embedder, IngestionConfig(corpus_authorized=True), page_counter=lambda p: 1)
    summary = svc.ingest_dir(tmp_path, report_dir=tmp_path / "rep")
    assert summary["totals"]["errors"] == 1 and summary["totals"]["documents"] == 5
    assert Path(summary["report_path"]).exists()


def test_inventario_e_relatorio_trazem_paginas_e_totais(store, embedder, tmp_path):  # ING-01, ING-10
    write_pdf(tmp_path)
    svc = make_service(store, embedder)
    assert svc.inventory(tmp_path)[0]["pages"] == 5  # contagem de páginas do arquivo
    summary = svc.ingest_dir(tmp_path, report_dir=tmp_path / "rep")
    totals = summary["totals"]
    assert totals["pages"] == 5 and totals["documents"] == 5 and totals["errors"] == 0 and summary["duration_s"] >= 0
    for key in ("pages_ocr", "pages_needs_review", "chunks_duplicate", "chunks_indexed", "documents_pending"):
        assert key in totals
