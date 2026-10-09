"""Fluxo guiado do WhatsApp: menus com botões (até 3 opções) e listas (até 10), emojis e texto livre só quando necessário.

O Chatwoot entrega o clique como uma mensagem de texto com o título da opção; por isso o reconhecimento é feito pelo título
normalizado (sem emoji nem pontuação). Limites do WhatsApp: título de botão 20 caracteres, de linha de lista 24.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from app.domain.models import Option
from app.domain.policies import find_process_numbers

BELEM = timezone(timedelta(hours=-3))  # Belém não tem horário de verão


class Kind(StrEnum):
    MENU = "menu"
    PROCESS_LIST = "process_list"
    SELECT_PROCESS = "select_process"
    ASK = "ask"  # executa uma pergunta pronta no RAG
    PROMPT = "prompt"  # pede texto livre
    INST_MENU = "inst_menu"
    LEGAL_MENU = "legal_menu"
    HUMAN = "human"
    CLOSE = "close"
    FREE = "free"


@dataclass(frozen=True)
class Action:
    kind: Kind
    arg: str = ""


# ------------------------------------------------------------------------------------------------ opções
OPT_MENU = Option("📋 Menu principal", "menu")
OPT_PROCESS = Option("📄 Consultar processo", "processos")
OPT_INST = Option("🏛️ Balcão Virtual", "institucional")
OPT_LEGAL = Option("⚖️ Termos jurídicos", "juridico")
OPT_HUMAN_LIST = Option("🙋 Falar com atendente", "humano")
OPT_CLOSE_LIST = Option("✅ Encerrar atendimento", "encerrar")
OPT_HUMAN = Option("🙋 Atendente", "humano")
OPT_CLOSE = Option("✅ Encerrar", "encerrar")

MAIN_MENU = [OPT_PROCESS, OPT_INST, OPT_LEGAL, OPT_HUMAN_LIST, OPT_CLOSE_LIST]
AFTER_ANSWER = [OPT_MENU, OPT_HUMAN, OPT_CLOSE]
PROCESS_MENU = [Option("📋 Dados da capa", "capa"), Option("🗓️ Cronologia", "cronologia"), Option("🔎 Outra pergunta", "outra_proc")]
INST_ITEMS = [
    (Option("🕒 Horário", "inst_horario"), "Qual o horário de funcionamento do Balcão Virtual?"),
    (Option("📍 Unidades e contatos", "inst_contatos"), "Quais são as unidades e os contatos do Balcão Virtual?"),
    (Option("📅 Agendamento", "inst_agenda"), "Como agendar o atendimento online no Balcão Virtual?"),
]
OPT_INST_OTHER = Option("💬 Outra dúvida", "inst_outra")
LEGAL_ITEMS = [
    (Option("⚖️ Justiça gratuita", "jur_gratuita"), "O que é a gratuidade da justiça e quem pode pedir?"),
    (Option("⚖️ Tutela de urgência", "jur_tutela"), "O que é tutela de urgência e quando pode ser concedida?"),
    (Option("⚖️ Citação e intimação", "jur_citacao"), "Qual a diferença entre citação e intimação?"),
    (Option("⚖️ Recursos", "jur_recursos"), "Quais são os recursos previstos no Código de Processo Civil?"),
]
OPT_LEGAL_OTHER = Option("💬 Outro termo", "jur_outro")

PROCESS_QUESTIONS = {
    "capa": "Quais são a classe, o órgão julgador, o valor da causa, os assuntos e as partes do processo {n}?",
    "cronologia": "Liste as datas e tipos dos documentos juntados ao processo {n}.",
}

PROMPTS = {
    "outra_proc": "✍️ Escreva sua pergunta sobre o processo *{n}*.\nExemplo: _Qual foi a última decisão?_",
    "inst_outra": "✍️ Escreva sua dúvida sobre o Tribunal ou o Balcão Virtual.\nExemplo: _Quem pode usar o Balcão Virtual?_",
    "jur_outro": "✍️ Escreva o termo ou a dúvida jurídica.\nExemplo: _O que significa trânsito em julgado?_",
}

_BY_TITLE: dict[str, Action] = {}


def clean(text: str) -> str:
    """Minúsculas, sem acento, emoji nem pontuação: "✅ Encerrar atendimento" -> "encerrar atendimento"."""
    folded = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]+", " ", folded)).strip()


def _register() -> None:
    def add(opt: Option, action: Action) -> None:
        _BY_TITLE[clean(opt.title)] = action

    for opt in (OPT_MENU,):
        add(opt, Action(Kind.MENU))
    add(OPT_PROCESS, Action(Kind.PROCESS_LIST))
    add(OPT_INST, Action(Kind.INST_MENU))
    add(OPT_LEGAL, Action(Kind.LEGAL_MENU))
    add(OPT_HUMAN_LIST, Action(Kind.HUMAN))
    add(OPT_HUMAN, Action(Kind.HUMAN))
    add(OPT_CLOSE_LIST, Action(Kind.CLOSE))
    add(OPT_CLOSE, Action(Kind.CLOSE))
    for opt in PROCESS_MENU:
        if opt.value in PROCESS_QUESTIONS:
            add(opt, Action(Kind.ASK, opt.value))
        else:
            add(opt, Action(Kind.PROMPT, opt.value))
    for opt, question in INST_ITEMS + LEGAL_ITEMS:
        add(opt, Action(Kind.ASK, question))
    add(OPT_INST_OTHER, Action(Kind.PROMPT, "inst_outra"))
    add(OPT_LEGAL_OTHER, Action(Kind.PROMPT, "jur_outro"))


_register()
_GREETING = re.compile(r"^(oi+|ola+|bom dia|boa tarde|boa noite|menu|inicio|iniciar|ajuda|opcoes|e ai|tudo bem)$")
_CLOSE_WORDS = frozenset({"encerrar", "encerrar atendimento", "finalizar", "finalizar atendimento", "sair"})


def parse(text: str, known_processes: list[str]) -> Action:
    """Interpreta a mensagem do usuário: opção do menu, número de processo escolhido ou texto livre."""
    c = clean(text)
    if c in _BY_TITLE:
        return _BY_TITLE[c]
    if c in _CLOSE_WORDS:
        return Action(Kind.CLOSE)
    if _GREETING.match(c):
        return Action(Kind.MENU)
    short = re.fullmatch(r"(?:📄\s*)?(\d{7}-\d{2}\.\d{4})", text.strip().lstrip("📄").strip())
    if short:  # título curto da lista: "6035625-24.2026"
        for number in known_processes:
            if number.startswith(short.group(1)):
                return Action(Kind.SELECT_PROCESS, number)
    numbers = find_process_numbers(text)
    if len(numbers) == 1 and len(re.sub(r"[\d.\- ]", "", text).strip()) == 0 and numbers[0] in known_processes:
        return Action(Kind.SELECT_PROCESS, numbers[0])  # só o número digitado
    return Action(Kind.FREE)


# ------------------------------------------------------------------------------------------------ textos
def fmt_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(BELEM).strftime("%d/%m/%Y %H:%M")


def opening_text(ticket_id: str, opened_at: str) -> str:
    return (f"🎫 *Atendimento aberto*\nProtocolo: *{ticket_id}*\n🕒 {fmt_time(opened_at)} (horário de Belém)\n\n"
            "Guarde este protocolo para acompanhar o seu atendimento.")


def closing_text(ticket_id: str, opened_at: str, closed_at: str, closed_by: str) -> str:
    who = {"usuario": "a seu pedido", "atendente": "pelo atendente", "sistema": "automaticamente"}.get(closed_by, "")
    return (f"✅ *Atendimento encerrado* {who}\nProtocolo: *{ticket_id}*\n🕒 Aberto em {fmt_time(opened_at)}\n"
            f"🕒 Encerrado em {fmt_time(closed_at)} (horário de Belém)\n\n"
            "Obrigado por falar com o assistente do TJPA (piloto). Para um novo atendimento, é só enviar uma mensagem. 👋")


MAIN_MENU_TEXT = ("👋 Olá! Sou o assistente virtual do piloto do TJPA.\n\n"
                  "Escolha uma opção abaixo 👇\n\n"
                  "ℹ️ As respostas sobre processos usam documentos de demonstração, não a consulta em tempo real ao PJe.")
AFTER_ANSWER_TEXT = "O que você quer fazer agora? 👇"
INST_MENU_TEXT = "🏛️ *Balcão Virtual e informações do Tribunal*\nEscolha um assunto 👇"
LEGAL_MENU_TEXT = "⚖️ *Termos e conceitos jurídicos*\nEscolha um tema ou digite o seu 👇"
PROCESS_MENU_TEXT = "✅ Processo selecionado: *{n}*\n{info}\n\nO que você quer ver? 👇"
PROCESS_LIST_TEXT = "📄 *Processos do acervo de demonstração*\n{lines}\n\nEscolha um processo na lista 👇 (ou digite o número)"
PROCESS_PICK_TEXT = "📄 Sobre qual processo é a sua pergunta? Escolha um na lista 👇 (ou digite o número)"


def process_options(numbers: list[str]) -> list[Option]:
    rows = [Option(f"📄 {n[:15]}", n) for n in numbers[:9]]
    if len(numbers) > 9:
        rows.append(OPT_MENU)
    else:
        rows = [Option(f"📄 {n[:15]}", n) for n in numbers[:10]]
    return rows


def inst_options() -> list[Option]:
    return [o for o, _ in INST_ITEMS] + [OPT_INST_OTHER, OPT_MENU]


def legal_options() -> list[Option]:
    return [o for o, _ in LEGAL_ITEMS] + [OPT_LEGAL_OTHER, OPT_MENU]
