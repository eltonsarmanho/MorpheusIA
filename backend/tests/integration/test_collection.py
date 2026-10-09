"""Coletores B/C e curadoria (COL-01..COL-09)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.application.collection.service import CollectionService
from app.application.collection.sources import CollectionError, SourceRegistry
from app.application.retrieval.hybrid import HybridRetriever
from app.domain.models import KnowledgeDomain as D, ReviewState as R
from app.domain.policies import AbstentionPolicy
from app.infrastructure.web.fetcher import FetchResult, HttpFetcher

REG = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
BV = "https://centralservicos.tjpa.jus.br/bv/balcao.php"
CPC = "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm"

HTML_BV = ("<html><head><title>Balcão Virtual</title></head><body><nav>menu</nav><main><h1>Balcão Virtual</h1>"
           "<p>Publicado em 18/05/2021. O horário de funcionamento do Balcão Virtual é de segunda à sexta-feira, das 08h às 14h. " + "Atendimento. " * 60 + "</p></main></body></html>").encode()
HTML_CPC = ("<html><head><title>Lei 13.105</title></head><body><p>Código de Processo Civil</p>"
            "<p>Art. 1º O processo civil será ordenado, disciplinado e interpretado conforme os valores e as normas fundamentais estabelecidos na Constituição da República Federativa do Brasil. " + "texto " * 30 + "</p>"
            "<p>Art. 2º O processo começa por iniciativa da parte e se desenvolve por impulso oficial, salvo as exceções previstas em lei. " + "texto " * 30 + "</p>"
            "<p>Art. 3º Não se excluirá da apreciação jurisdicional ameaça ou lesão a direito. " + "texto " * 30 + "</p></body></html>").encode()


class FakeFetcher:
    def __init__(self, pages: dict[str, bytes]):
        self.pages, self.calls = pages, []

    def fetch(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise CollectionError("404")
        return FetchResult(url, 200, "text/html; charset=utf-8", self.pages[url])


@pytest.fixture
def svc(store, embedder):
    return CollectionService(store, SourceRegistry.from_file(REG), FakeFetcher({BV: HTML_BV, CPC: HTML_CPC}), embedder)


def search(store, embedder, q, domain):
    return HybridRetriever(store, embedder, policy=AbstentionPolicy(min_vector_score=0.0)).retrieve(q, domain)


def test_fonte_nao_autorizada_e_recusada_e_registrada(svc, store):  # COL-01
    with pytest.raises(CollectionError):
        svc.collect_url(D.INSTITUCIONAL, "https://site-qualquer.com/pagina")
    with pytest.raises(CollectionError):
        svc.collect_url(D.INSTITUCIONAL, "http://centralservicos.tjpa.jus.br/bv/balcao.php")  # sem TLS
    with pytest.raises(CollectionError):
        svc.collect_url(D.JURIDICO, BV)  # host autorizado, mas para outro domínio
    assert svc.fetcher.calls == [] and [r["outcome"] for r in store.collection_log_rows()] == ["recusada"] * 3


def test_coleta_registra_proveniencia_e_fica_pendente(svc, store):  # COL-02
    r = svc.collect_url(D.INSTITUCIONAL, BV)
    d = store.get_document(r["doc_id"])
    assert d.source_url == BV and d.issuing_body.startswith("Tribunal de Justiça do Estado do Pará")
    assert d.review_state is R.PENDING_REVIEW and d.collected_at and d.content_hash and d.published_at == "2021-05-18"
    assert d.extra["topic"] == "balcao_virtual"


def test_nada_coletado_e_consultavel_sem_aprovacao(svc, store, embedder):  # COL-03
    svc.collect_url(D.INSTITUCIONAL, BV)
    assert not search(store, embedder, "horário do Balcão Virtual", D.INSTITUCIONAL).sufficient


def test_aprovado_passa_a_ser_consultavel_e_rejeitado_some(svc, store, embedder):  # COL-03, COL-08
    r = svc.collect_url(D.INSTITUCIONAL, BV)
    store.set_review(r["doc_id"], R.APPROVED, reviewer="Curadora", reason="conferido na página oficial")
    store.index_pending(embedder, doc_id=r["doc_id"])
    res = search(store, embedder, "horário do Balcão Virtual", D.INSTITUCIONAL)
    assert res.sufficient and res.evidences[0].citation["url"] == BV
    store.set_review(r["doc_id"], R.REJECTED, reviewer="Curadora", reason="norma revogada")
    assert not search(store, embedder, "horário do Balcão Virtual", D.INSTITUCIONAL).sufficient


def test_conteudo_duplicado_de_outra_url_e_marcado(svc, store):  # COL-04
    svc.fetcher.pages["https://centralservicos.tjpa.jus.br/bv/agendamento.php"] = HTML_BV
    a = svc.collect_url(D.INSTITUCIONAL, BV)
    b = svc.collect_url(D.INSTITUCIONAL, "https://centralservicos.tjpa.jus.br/bv/agendamento.php")
    assert store.get_document(a["doc_id"]).review_state is R.PENDING_REVIEW
    d = store.get_document(b["doc_id"])
    assert d.review_state is R.REJECTED and a["doc_id"] in d.review_reason


def test_recoleta_com_conteudo_igual_preserva_aprovacao_e_com_conteudo_novo_exige_revisao(svc, store, embedder):
    r = svc.collect_url(D.INSTITUCIONAL, BV)
    store.set_review(r["doc_id"], R.APPROVED, reviewer="Curadora", reason="ok")
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "approved"
    svc.fetcher.pages[BV] = HTML_BV.replace("08h às 14h".encode(), "09h às 13h".encode())
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "pending_review"


def test_informacao_aprovada_e_antiga_vira_stale_e_sai_das_respostas(svc, store, embedder):  # COL-05
    r = svc.collect_url(D.INSTITUCIONAL, BV)
    store.set_review(r["doc_id"], R.APPROVED, reviewer="Curadora", reason="ok")
    store.index_pending(embedder, doc_id=r["doc_id"])
    future = datetime.now(timezone.utc) + timedelta(days=400)
    assert svc.refresh_staleness(now=future) == [r["doc_id"]]
    assert store.get_document(r["doc_id"]).review_state is R.STALE
    assert not search(store, embedder, "horário do Balcão Virtual", D.INSTITUCIONAL).sufficient


def test_divergencia_entre_fontes_do_mesmo_tema_volta_para_revisao(svc, store):  # COL-06
    a = svc.collect_url(D.INSTITUCIONAL, BV)
    other = "https://centralservicos.tjpa.jus.br/bv/agendamento.php"
    svc.fetcher.pages[other] = HTML_BV.replace("08h às 14h".encode(), "07h às 13h".encode())
    b = svc.collect_url(D.INSTITUCIONAL, other)
    # mesmo tema declarado por fontes distintas
    for doc_id, source in ((a["doc_id"], "tjpa-central-servicos"), (b["doc_id"], "outra-fonte")):
        store._exec("update documents set extra=json_set(extra,'$.topic','horario_bv','$.source_id',?) where doc_id=?", (source, doc_id))
        store.set_review(doc_id, R.APPROVED, reviewer="Cur", reason="ok")
    pairs = svc.detect_conflicts()
    assert len(pairs) == 1 and set(pairs[0]) == {a["doc_id"], b["doc_id"]}
    for doc_id in (a["doc_id"], b["doc_id"]):
        assert store.get_document(doc_id).review_state is R.NEEDS_REVIEW and "divergência" in store.get_document(doc_id).review_reason


def test_norma_preserva_texto_original_por_artigo_e_referencia(svc, store, embedder):  # COL-09
    r = svc.collect_url(D.JURIDICO, CPC)
    d = store.get_document(r["doc_id"])
    assert d.extra["norm_reference"].startswith("Lei nº 13.105/2015") and d.extra["summary"] is None and d.extra["original_text_preserved"]
    ctxs = [row["ctx"] for row in store._exec("select ctx from chunks where doc_id=?", (r["doc_id"],))]
    assert any(c.endswith("Art. 2º") for c in ctxs)
    texts = " ".join(row["text"] for row in store._exec("select text from chunks where doc_id=?", (r["doc_id"],)))
    assert "O processo começa por iniciativa da parte" in texts


def test_dominio_processual_nao_aceita_coleta_web(svc):
    with pytest.raises(CollectionError):
        svc.collect_url(D.PROCESSUAL, BV)


def test_pagina_com_pouco_texto_vai_para_revisao(svc, store):
    svc.fetcher.pages[BV] = b"<html><head><title>x</title></head><body><p>curto</p></body></html>"
    assert svc.collect_url(D.INSTITUCIONAL, BV)["state"] == "needs_review"


def test_robots_bloqueia_e_robots_inacessivel_suspende_a_coleta():  # COL-07
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /privado/")
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    f = HttpFetcher(SourceRegistry.from_file(REG).settings, transport=httpx.MockTransport(handler))
    assert f.fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php").status == 200
    with pytest.raises(CollectionError, match="robots"):
        f.fetch("https://centralservicos.tjpa.jus.br/privado/x")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede")

    g = HttpFetcher(SourceRegistry.from_file(REG).settings, transport=httpx.MockTransport(down))
    with pytest.raises(CollectionError, match="robots.txt"):
        g.fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php")


def test_redirecionamento_para_outro_host_e_recusado():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "centralservicos.tjpa.jus.br":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(302, headers={"location": "https://malicioso.example/x"})
        return httpx.Response(200, text="<html>x</html>", headers={"content-type": "text/html"})

    f = HttpFetcher(SourceRegistry.from_file(REG).settings, transport=httpx.MockTransport(handler))
    with pytest.raises(CollectionError, match="outro host"):
        f.fetch("https://centralservicos.tjpa.jus.br/bv/balcao.php")
