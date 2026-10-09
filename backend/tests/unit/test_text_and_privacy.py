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
