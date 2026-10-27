"""Roteiro TJPA: validações puras (CNJ, CPF), cadastro de unidades, persona e migração do estado."""
import sqlite3
from pathlib import Path

from app.application import persona
from app.application.answering.prompts import SYSTEM_PROMPT, build_user_prompt
from app.application.chat import flow
from app.domain.handoff import HandoffState as S, can_transition
from app.domain.models import KnowledgeDomain, Profile
from app.domain.policies import cnj_check_digits_ok, cpf_is_valid, extract_cpf, mask_cpf
from app.domain.units import Unit, UnitDirectory, valid_br_phone, valid_official_url, whatsapp_link
from app.infrastructure.sqlite.operational_store import ConversationState, OperationalStore
from app.infrastructure.units_file import load_unit_directory

REPO = Path(__file__).resolve().parents[3]


# ----------------------------------------------------------------------------------------------- CNJ e CPF
def test_digitos_verificadores_cnj_do_acervo_conferem_e_numero_alterado_falha():
    numbers = [p.stem for p in (REPO / "docs" / "processos").glob("*.pdf")]
    assert numbers and all(cnj_check_digits_ok(n) for n in numbers)
    assert not cnj_check_digits_ok("6080680-33.2025.8.03.0001")  # um dígito verificador trocado
    assert not cnj_check_digits_ok("1234567-89.2026")  # incompleto


def test_cpf_validacao_extracao_e_mascara():
    assert cpf_is_valid("529.982.247-25") and cpf_is_valid("52998224725")
    assert not cpf_is_valid("529.982.247-24") and not cpf_is_valid("111.111.111-11") and not cpf_is_valid("123")
    assert extract_cpf("meu cpf é 529.982.247-25, obrigado") == "52998224725"
    assert mask_cpf("CPF 529.982.247-25 no processo 6080680-32.2025.8.03.0001") == "CPF [CPF] no processo 6080680-32.2025.8.03.0001"
    assert mask_cpf("número 529.982.247-24") == "número 529.982.247-24"  # dígitos inválidos não são CPF


# --------------------------------------------------------------------------------------------- unidades
def test_validacao_de_links_e_telefones_nao_aceita_dado_improvisado():
    assert valid_official_url("https://www.tjpa.jus.br/balcao/unidade-x") is not None
    assert valid_official_url("http://www.tjpa.jus.br/x") is None  # sem HTTPS
    assert valid_official_url("https://tjpa.jus.br.golpe.com/x") is None and valid_official_url("https://exemplo.com") is None
    assert valid_br_phone("(91) 3205-0000") == "559132050000" and valid_br_phone("+55 91 98888-7777") == "5591988887777"
    assert valid_br_phone("1234") is None and valid_br_phone("(01) 3205-0000") is None
    link = whatsapp_link("5591988887777", "Processo: 1 & diligência")
    assert link.startswith("https://wa.me/5591988887777?text=") and " " not in link and "&" not in link.split("?text=")[1]


def test_busca_de_unidade_exige_nome_ou_cidade_e_nao_escolhe_por_semelhanca():
    d = UnitDirectory([Unit("u1", "1ª Vara Cível de Teste", "Cidade Teste", aliases=("primeira civel",)),
                       Unit("u2", "2ª Vara Cível de Teste", "Cidade Teste")])
    assert [u.id for u in d.find("quero a 1ª vara cível de teste")] == ["u1"]
    assert [u.id for u in d.find("primeira civel")] == ["u1"]
    assert len(d.find("Cidade Teste")) == 2  # só a cidade: ambíguo, quem escolhe é o usuário
    assert d.find("vara civel") == []  # semelhança parcial não seleciona


def test_cadastro_versionado_carrega_vazio_de_unidades_e_com_a_pagina_geral_declarada():
    d = load_unit_directory(REPO / "backend" / "config" / "units.yaml")
    assert d.units == [] and d.contacts.telejudiciario is None  # nenhum dado inventado
    assert d.contacts.balcao_general_url and "844287-balcao-virtual" in d.contacts.balcao_general_url and d.rejected == []
    assert load_unit_directory(None).units == [] and load_unit_directory("/nao/existe.yaml").units == []


def test_carga_descarta_campos_invalidos_sem_corrigir(tmp_path):
    f = tmp_path / "u.yaml"
    f.write_text("unidades:\n  - id: t1\n    nome: Unidade Teste\n    cidade: Cidade\n    telefone: '123'\n"
                 "    balcao_virtual_url: 'https://site-qualquer.com'\n  - id: t2\n    nome: Inativa\n    valido: false\n", encoding="utf-8")
    d = load_unit_directory(f)
    assert [u.id for u in d.units] == ["t1"] and d.units[0].phone is None and d.units[0].balcao_url is None
    assert set(d.rejected) == {"t1.telefone", "t1.balcao_virtual_url"}


# --------------------------------------------------------------------------------------------- estado
def test_migracao_de_banco_existente_preserva_dados_e_acrescenta_colunas(tmp_path):
    db = tmp_path / "op.db"
    con = sqlite3.connect(db)  # esquema da versão anterior, sem as colunas do roteiro
    con.executescript("""CREATE TABLE conversations (key TEXT PRIMARY KEY, handoff_state TEXT NOT NULL, profile TEXT NOT NULL DEFAULT 'indefinido',
      process_number TEXT, failed_retrievals INTEGER NOT NULL DEFAULT 0, offer_pending INTEGER NOT NULL DEFAULT 0,
      handoff_attempts INTEGER NOT NULL DEFAULT 0, last_domain TEXT, handoff_team TEXT, handoff_reason TEXT, last_reply_hash TEXT, updated_at TEXT);
      INSERT INTO conversations(key, handoff_state, profile, process_number) VALUES('1:9', 'human_active', 'advogado', '6080680-32.2025.8.03.0001');""")
    con.commit(); con.close()
    ops = OperationalStore(db)
    st = ops.get("1:9")
    assert st.handoff_state is S.HUMAN_ACTIVE and st.process_number == "6080680-32.2025.8.03.0001" and st.diligence is None
    st.diligence, st.profile_locked, st.unit_label = "Texto  com  espaços\n e acentuação 🙂 ", True, "Unidade"
    ops.save(st)
    again = OperationalStore(db).get("1:9")  # reabrir não repete a migração nem perde o texto
    assert again.diligence == "Texto  com  espaços\n e acentuação 🙂 " and again.profile_locked and again.unit_label == "Unidade"


def test_troca_de_canal_apos_falha_e_a_unica_transicao_nova():
    assert can_transition(S.HANDOFF_REQUESTED, S.AUTOMATION_RESUMED)
    assert not can_transition(S.HUMAN_ACTIVE, S.AUTOMATION_RESUMED)  # atendimento humano ativo continua bloqueando o bot


def test_limpar_pedido_remove_diligencia_e_escolhas():
    st = ConversationState("k", diligence="x", request_channel="balcao", request_reason="diligencia", choices="[]", request_note_id=5)
    st.clear_request()
    assert (st.diligence, st.request_channel, st.request_reason, st.choices, st.request_note_id) == (None,) * 5


# --------------------------------------------------------------------------------------------- persona
def test_identidade_da_julia_esta_centralizada_e_no_prompt_do_llm():
    assert persona.NAME in SYSTEM_PROMPT and persona.SIGNATURE in SYSTEM_PROMPT and "não uma pessoa" in SYSTEM_PROMPT
    assert "Júlia" in flow.profile_menu_text() and "assistente virtual" in flow.profile_menu_text()
    assert persona.SIGNATURE in flow.closing_text("TKT-1", "2026-10-27T12:00:00+00:00", "2026-10-27T13:00:00+00:00", "usuario")


def test_prompt_adapta_a_linguagem_ao_perfil_escolhido():
    simple, _ = build_user_prompt("q", [], Profile.PARTE_TESTEMUNHA, KnowledgeDomain.PROCESSUAL, 1000)
    tech, _ = build_user_prompt("q", [], Profile.ADVOGADO, KnowledgeDomain.PROCESSUAL, 1000)
    assert "linguagem simples" in simple and "terminologia jurídica" in tech


def test_textos_ao_publico_sem_jargao_tecnico():
    public = [v for k, v in vars(flow).items() if k.isupper() and isinstance(v, str)]
    public += list(flow.ID_MENU_TEXT.values()) + [flow.profile_menu_text(), flow.handoff_notice("secretaria", "TKT-1", True)]
    for text in public:
        low = text.lower()
        assert not any(w in low for w in ("rag", "handoff", "pipeline", "orquestrador", "abstenção", "ticket", "entrada inválida")), text
