from app.application.ingestion.pje_parser import (
    build_pages, parse_cover, parse_footer, segment_documents, strip_footers,
)
from tests.conftest import COVER, make_pdf_pages, pje_footer


def test_footer_extrai_documento_pagina_e_assinatura():  # ING-02
    f = parse_footer("texto" + pje_footer("28338236", 3, "DAVI IVA", "12/05/2026 10:14:55"))
    assert (f.doc_id, f.pje_page, f.signer, f.signed_at) == ("28338236", 3, "DAVI IVA", "2026-05-12T10:14:55")


def test_footer_com_assinaturas_intercaladas():
    page = ("texto\n Assinado eletronicamente por: JUIZ A - 14/05/2026 20:43:39, JUIZ A - 14/05/2026\n"
            "                 Num.20:43:39\n  28423388 - Pág. 2\n https://pje/x\n Número do documento: 123\n")
    f = parse_footer(page)
    assert f.doc_id == "28423388" and f.pje_page == 2


def test_footer_aninhado_usa_o_mais_externo():
    page = pje_footer("361115434", 110, "PF") + pje_footer("22872011", 11, "TJ")
    assert parse_footer(page).doc_id == "22872011"


def test_strip_footers_remove_rodape_sem_tocar_no_corpo():
    body = strip_footers("Corpo da decisão.\n" + pje_footer("1000001", 1))
    assert "Assinado" not in body and "Número do documento" not in body and "Corpo da decisão." in body


def test_capa_e_tabela_de_documentos():
    cover = parse_cover(COVER)
    assert cover.number == "1234567-89.2026.8.03.0001"
    assert cover.secrecy == "NAO" and cover.free_justice == "SIM"
    assert cover.distribution_date == "2026-05-12" and cover.subjects == "Indenização por dano moral"
    assert [r.doc_id for r in cover.rows] == ["1000001", "1000002", "1000003", "1000004"]
    assert cover.rows[2].doc_type == "Documento de Comprovação" and cover.rows[1].date == "2026-05-14"
    assert {"nome": "MARIA EXEMPLO DA SILVA", "papel": "AUTOR"} in cover.parties


def test_metadado_ausente_fica_desconhecido():  # ING-07
    cover = parse_cover("Tribunal X\nNúmero: 1\n")
    assert cover.process_class == "desconhecido" and cover.secrecy == "desconhecido" and cover.rows == []


def test_segmentacao_une_paginas_por_documento_e_metadados_da_capa():
    pages = build_pages(make_pdf_pages())
    cover = parse_cover("\n".join(p.raw_text for p in pages if p.doc_id is None))
    segs = {s.pje_doc_id: s for s in segment_documents(pages, cover)}
    assert set(segs) == {"capa", "1000001", "1000002", "1000003", "1000004"}
    assert segs["1000002"].doc_type == "Decisão" and segs["1000002"].doc_date == "2026-05-14"
    assert segs["1000002"].doc_date_source == "tabela_da_capa" and segs["1000002"].signer == "JUIZ TESTE"


def test_pagina_sem_rodape_herda_documento_anterior():
    texts = make_pdf_pages()
    texts.insert(3, "página escaneada sem rodapé legível")
    pages = build_pages(texts)
    assert pages[3].footer is None and pages[3].inherited_doc_id == "1000002"
