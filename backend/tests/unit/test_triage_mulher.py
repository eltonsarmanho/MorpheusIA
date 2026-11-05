"""Triagem da Ouvidoria da Mulher: enumerações fechadas, leitura defensiva e contatos (OVM-09, OVM-15, OVM-25)."""
import sqlite3
from datetime import time

from app.application.chat import mulher as ovm
from app.domain.triage_mulher import CHOICES, NAO_INFORMADO, STEPS, OuvidoriaMulher, Triage, mentions_violence
from app.infrastructure.mulher_file import load_ouvidoria_mulher
from app.infrastructure.sqlite.operational_store import OperationalStore


def test_so_aceita_valores_da_enumeracao():
    t = Triage()
    assert t.set("assunto", "fisica") and t.assunto == "fisica"
    assert not t.set("assunto", "inventado") and t.assunto == "fisica"
    assert not t.set("campo_inexistente", "sim")


def test_ida_e_volta_em_json_preserva_o_relato_byte_a_byte():
    t = Triage(step="resumo", perigo="sim", assunto="moral", relato="  Texto   com\n quebras 🙂 ", numero_processo="0000001-02.2026.8.14.0001")
    back = Triage.from_json(t.to_json())
    assert back.relato == t.relato and back.perigo == "sim" and back.step == "resumo" and back.assunto == "moral"


def test_valor_fora_da_enumeracao_e_descartado_ao_ler():  # OVM-25
    raw = '{"step": "assunto", "assunto": "ataque", "perigo": "sim", "numero_processo": "123", "contato": 7}'
    t = Triage.from_json(raw)
    assert t.assunto is None and t.perigo == "sim" and t.numero_processo is None and t.contato is None
    assert Triage.from_json("não é json") is None and Triage.from_json(None) is None and Triage.from_json("[1]") is None


def test_atributos_do_chatwoot_nunca_levam_relato_nem_processo():
    t = Triage(perigo="sim", assunto="fisica", ambiente="familiar", para_quem="terceiro", relato="SEGREDO", numero_processo="0000001-02.2026.8.14.0001")
    attrs = t.attributes()
    assert set(attrs) == {"servico", "assunto", "ambiente", "urgencia", "para_quem"}
    assert "SEGREDO" not in str(attrs) and "0000001" not in str(attrs) and attrs["urgencia"] == "imediata"


def test_resumo_ao_usuario_nao_repete_o_relato():
    assert "SEGREDO" not in Triage(relato="SEGREDO").summary_for_user()


def test_perguntas_nao_pedem_dados_proibidos():  # OVM-15
    text = " ".join(ovm.QUESTIONS.values()).lower()
    for banned in ("cpf", "endereço", "rg ", "nome do", "agressor"):
        assert banned not in text


def test_toda_etapa_tem_uma_saida():  # OVM-09
    for step in ("perigo", "para_quem", "ambiente", "processo", "contato"):
        assert ovm.OPT_SKIP in ovm.STEP_OPTIONS[step]
    assert ovm.ANSWERS[("assunto", "outro")] in ovm.STEP_OPTIONS["assunto"]
    assert ovm.OPT_SKIP_STEP in ovm.STEP_OPTIONS["numero"] and ovm.OPT_SKIP_STEP in ovm.STEP_OPTIONS["relato"]


def test_etapas_e_enumeracoes_cobrem_as_opcoes():
    assert STEPS[0] == "perigo" and STEPS[-1] == "resumo"
    for (field, value) in ovm.ANSWERS:
        assert value in CHOICES[field]


def test_titulos_cabem_nos_limites_do_whatsapp():
    def size(title):
        return len(title.encode("utf-16-le")) // 2

    for step, opts in ovm.STEP_OPTIONS.items():
        limit = 20 if len(opts) <= 3 else 24
        assert all(size(o.title) <= limit for o in opts), step
    assert all(size(o.title) <= 24 for o in ovm.all_options())
    assert len(ovm.orient_options(ovm.OPT_SEND)) <= 10 and len({o.title for o in ovm.all_options()}) == len(ovm.all_options())


def test_deteccao_de_violencia_so_oferece_em_relatos_claros():
    assert mentions_violence("meu marido me bate")
    assert mentions_violence("fui agredida ontem")
    assert mentions_violence("quero pedir medida protetiva")
    assert not mentions_violence("quando e a audiencia do processo")
    assert not mentions_violence("o que e tutela de urgencia")


def test_horario_da_ouvidoria():
    o = OuvidoriaMulher()
    assert o.is_open(time(8, 0)) and o.is_open(time(13, 59)) and not o.is_open(time(14, 0)) and not o.is_open(time(7, 59))
    assert o.hours_text == "8h às 14h"


def test_contatos_so_do_arquivo_e_campo_invalido_e_descartado(tmp_path):
    f = tmp_path / "m.yaml"
    f.write_text('ouvidoria_mulher:\n  telefone: "123"\n  email: "sem-arroba"\n  abre: "09:00"\n  fecha: "08:00"\n', encoding="utf-8")
    o = load_ouvidoria_mulher(f)
    assert o.phone is None and o.email is None and (o.opens, o.closes) == (time(8, 0), time(14, 0))
    assert load_ouvidoria_mulher(tmp_path / "nao_existe.yaml") == OuvidoriaMulher()


def test_arquivo_de_contatos_do_repositorio_e_valido():
    from app.config import get_settings

    o = load_ouvidoria_mulher(get_settings().mulher_file)
    assert o.phone == "(91) 3289-7182" and o.email == "ouvidoria.mulher@tjpa.jus.br" and o.source


def test_banco_anterior_sem_a_coluna_triage_migra_sem_perder_dados(tmp_path):  # OVM-25
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE conversations(key TEXT PRIMARY KEY, handoff_state TEXT NOT NULL DEFAULT 'bot_active', profile TEXT NOT NULL DEFAULT 'indefinido',"
                      " process_number TEXT, failed_retrievals INTEGER NOT NULL DEFAULT 0, offer_pending INTEGER NOT NULL DEFAULT 0, handoff_attempts INTEGER NOT NULL DEFAULT 0,"
                      " last_domain TEXT, handoff_team TEXT, handoff_reason TEXT, last_reply_hash TEXT, updated_at TEXT);"
                      "INSERT INTO conversations(key, process_number) VALUES('1:5','antigo');")
    con.commit()
    con.close()
    ops = OperationalStore(path)
    st = ops.get("1:5")
    assert st.process_number == "antigo" and st.triage is None
    st.triage = Triage(assunto="fisica").to_json()
    ops.save(st)
    assert Triage.from_json(ops.get("1:5").triage).assunto == "fisica"
    assert NAO_INFORMADO in CHOICES["assunto"]
