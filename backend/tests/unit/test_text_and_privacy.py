from app.application.ingestion.text_processing import chunk_text, content_hash, normalize_text
from app.domain.privacy import mask_pii


def test_normalizacao_une_hifenizacao_sem_alterar_numeros():  # ING-05
    t = normalize_text("A importância de R$ 14.166,48 foi proto-\ncolada em 12/05/2026,  conforme art. 98.")
    assert "protocolada" in t and "R$ 14.166,48" in t and "12/05/2026" in t and "  " not in t


def test_chunks_respeitam_limite_e_preservam_conteudo():  # ING-06
    text = "\n\n".join(f"Parágrafo {i} " + "palavra " * 40 for i in range(12))
    chunks = chunk_text(text, 900)
    assert all(len(c) <= 900 for c in chunks) and len(chunks) > 1
    assert "Parágrafo 0" in chunks[0] and "Parágrafo 11" in chunks[-1]


def test_hash_ignora_espacos_e_caixa():
    assert content_hash("Olá   Mundo\n") == content_hash("olá mundo")


def test_mascara_dados_pessoais_e_conta_ocorrencias():  # SEC-01
    t, n = mask_pii("CPF 111.222.333-44, e-mail ana@exemplo.com, tel (91) 98888-7777, CEP 68900-000, RG: 1234567.")
    assert "111.222.333-44" not in t and "ana@exemplo.com" not in t and "98888-7777" not in t and "68900-000" not in t
    assert n["cpf"] == 1 and n["email"] == 1 and n["telefone"] == 1 and n["cep"] == 1 and n["rg"] == 1


def test_nao_mascara_numero_de_processo_nem_valores():
    t, n = mask_pii("Processo 6035625-24.2026.8.03.0001, valor R$ 280.544,74, documento 28338236.")
    assert t == "Processo 6035625-24.2026.8.03.0001, valor R$ 280.544,74, documento 28338236." and n == {}


def test_repara_mojibake_sem_tocar_em_texto_correto():
    from app.application.ingestion.text_processing import fix_mojibake
    assert fix_mojibake("apÃ³s o voto. HonorÃ¡rios; NÂº 5") == "após o voto. Honorários; Nº 5"
    assert fix_mojibake("AÇÃO, AMAPÁ, São Paulo, CERTIDÃO") == "AÇÃO, AMAPÁ, São Paulo, CERTIDÃO"


def test_mascara_variantes_encontradas_no_acervo():  # achado do portão de dados pessoais
    t, n = mask_pii("inscrito no CPF sob nº 3665.209.668-45, residente em Santa Rita, CEP-68901-283, Macapá; CPF: 11122233344")
    assert "3665.209.668-45" not in t and "68901-283" not in t and "11122233344" not in t
    assert n["cpf"] == 2 and n["cep"] == 1


def test_mascara_rg_cnh_telefone_sem_formato_e_numero_de_11_digitos():
    t, n = mask_pii("RG: 441664 e CNH 03499494265; Fone: (96)9634221164; lista 07912646531 ELIVAN; valor 1.234.567,89; doc 28338236")
    for leaked in ("441664", "03499494265", "9634221164", "07912646531"):
        assert leaked not in t
    assert "28338236" in t and "1.234.567,89" in t


def test_mascara_rg_quebrado_por_quebra_de_linha_do_pdf():
    t, _ = mask_pii("portador da Cédula de Identidade 1.372. 732-X, inscrito; RG 1234 5678 20, casado")
    assert "732-X" not in t and "5678" not in t and "inscrito" in t and "casado" in t


def test_mascara_telefone_com_espacos_apos_rotulo_sem_tocar_em_datas():
    t, n = mask_pii("RG nº 391653 e CPF nº 238.583.032-91, contato 96 9 8406-6014, e-mail x@gmail.com; audiência em 20/07/2026, doc 28338236")
    assert "8406-6014" not in t and "391653" not in t and "20/07/2026" in t and "28338236" in t
