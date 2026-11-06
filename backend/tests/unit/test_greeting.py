import pytest

from app.application.answering.intent import classify_intent
from app.application.chat import flow
from app.domain.greeting import is_greeting
from app.domain.models import Intent


@pytest.mark.parametrize("text", [
    "Olá Julia !!", "ola julia", "Oi, tudo bem?", "oii", "Boa tarde, EscutAI!", "bom dia Júlia, tudo bem?",
    "Olá, boa noite", "menu", "ajuda", "Opa", "e aí", "Oi Julia, como vai?", "olaaa",
])
def test_saudacao_abre_atendimento(text):
    assert is_greeting(text)
    assert flow.parse(text, []).kind is flow.Kind.MENU
    assert classify_intent(text).intent is Intent.SAUDACAO


@pytest.mark.parametrize("text", [
    "Olá, quero saber do meu processo", "oi preciso falar com atendente", "bom dia, qual o horário do fórum?",
    "0001234-56.2020.8.14.0001", "", "!!", "julia", "obrigado", "ola " + "tudo bem " * 5,
])
def test_mensagem_com_conteudo_nao_e_saudacao(text):
    assert not is_greeting(text)
    assert flow.parse(text, []).kind is not flow.Kind.MENU
