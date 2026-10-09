import pytest

from app.domain.handoff import HandoffState as S, InvalidTransition, bot_may_reply, ensure_transition
from app.domain.models import AccessClass, ReviewState
from app.domain.policies import find_process_numbers, normalize_process_number, triage_document


def triage(**kw):
    base = dict(doc_type="Decisão", doc_name="Decisão", cover_secrecy="NAO", body_text="texto", corpus_authorized=True)
    return triage_document(**{**base, **kw})


def test_triagem_aprova_documento_comum_com_autorizacao():
    t = triage()
    assert t.review_state is ReviewState.APPROVED and t.access_class is AccessClass.PUBLIC


@pytest.mark.parametrize("kw", [
    {"corpus_authorized": False}, {"cover_secrecy": "SIM"}, {"cover_secrecy": None},
    {"doc_type": "Documento de Identificação"}, {"doc_name": "CONTRACHEQUE ATUAL"},
    {"doc_name": "COMPROVANTE DE ENDERECO ATUALIZADO"}, {"body_text": "Este feito tramita em segredo de justiça."},
])  # CUR-02
def test_triagem_mantem_pendente(kw):
    assert triage(**kw).review_state is ReviewState.PENDING_REVIEW


def test_numero_processual_normalizado_e_detectado():
    assert normalize_process_number("6035625 24 2026 8 03 0001") == "6035625-24.2026.8.03.0001"
    assert find_process_numbers("veja 6035625-24.2026.8.03.0001 e 0000218-64.2023.8.03.0001") == [
        "6035625-24.2026.8.03.0001", "0000218-64.2023.8.03.0001"]


def test_transicoes_validas_e_invalidas():  # CHW-01
    assert ensure_transition(S.BOT_ACTIVE, S.HANDOFF_REQUESTED) is S.HANDOFF_REQUESTED
    with pytest.raises(InvalidTransition):
        ensure_transition(S.HUMAN_ACTIVE, S.BOT_ACTIVE)
    with pytest.raises(InvalidTransition):
        ensure_transition(S.HUMAN_CLOSED, S.BOT_ACTIVE)  # só via automation_resumed


def test_bot_so_responde_em_estados_automatizados():  # CHW-02
    assert {s for s in S if bot_may_reply(s)} == {S.BOT_ACTIVE, S.AUTOMATION_RESUMED}
