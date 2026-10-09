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
    PROCESS_ASK = "process_ask"  # "Consultar processo": pergunta qual processo
    SELECT_PROCESS = "select_process"
    ASK = "ask"  # executa uma pergunta pronta no RAG
    PROMPT = "prompt"  # pede texto livre
    INST_MENU = "inst_menu"
    LEGAL_MENU = "legal_menu"
    HUMAN = "human"
    CLOSE = "close"
    CLOSE_HINT = "close_hint"  # digitou "encerrar": só orienta, não encerra
    FREE = "free"


@dataclass(frozen=True)
class Action:
    kind: Kind
    arg: str = ""


# ------------------------------------------------------------------------------------------------ opções
OPT_MENU = Option("📋 Menu principal", "menu")
OPT_PROCESS = Option("📄 Consultar processo", "processos")
OPT_OTHER_PROC = Option("📄 Outro processo", "outro_processo")
OPT_ASK = Option("🔎 Fazer pergunta", "fazer_pergunta")
OPT_INST = Option("🏛️ Balcão Virtual", "institucional")
OPT_LEGAL = Option("⚖️ Termos jurídicos", "juridico")
OPT_HUMAN_LIST = Option("🙋 Falar com atendente", "humano")
OPT_CLOSE_LIST = Option("✅ Encerrar atendimento", "encerrar")
OPT_HUMAN = Option("🙋 Atendente", "humano")
OPT_CLOSE = Option("✅ Encerrar", "encerrar")

MAIN_MENU = [OPT_PROCESS, OPT_INST, OPT_LEGAL, OPT_HUMAN_LIST, OPT_CLOSE_LIST]
AFTER_ANSWER = [OPT_MENU, OPT_HUMAN, OPT_CLOSE]
PROCESS_MENU = [Option("🗓️ Cronologia", "cronologia"), OPT_ASK, OPT_OTHER_PROC, OPT_HUMAN_LIST, OPT_CLOSE_LIST, OPT_MENU]
PROCESS_ASK_OPTIONS = [OPT_MENU]
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
    "outra_proc": "Claro! 😊 Pode escrever a sua pergunta sobre o processo *{n}*.\nPor exemplo: _Qual foi a última decisão?_ ou _Quem são as partes?_",
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
    add(OPT_PROCESS, Action(Kind.PROCESS_ASK))
    add(OPT_OTHER_PROC, Action(Kind.PROCESS_ASK))
    add(OPT_INST, Action(Kind.INST_MENU))
    add(OPT_LEGAL, Action(Kind.LEGAL_MENU))
    add(OPT_HUMAN_LIST, Action(Kind.HUMAN))
    add(OPT_HUMAN, Action(Kind.HUMAN))
    add(OPT_CLOSE_LIST, Action(Kind.CLOSE))
    add(OPT_CLOSE, Action(Kind.CLOSE))
    add(PROCESS_MENU[0], Action(Kind.ASK, "cronologia"))
    add(OPT_ASK, Action(Kind.PROMPT, "outra_proc"))
    for opt, question in INST_ITEMS + LEGAL_ITEMS:
        add(opt, Action(Kind.ASK, question))
    add(OPT_INST_OTHER, Action(Kind.PROMPT, "inst_outra"))
    add(OPT_LEGAL_OTHER, Action(Kind.PROMPT, "jur_outro"))


_register()
_GREETING = re.compile(r"^(oi+|ola+|bom dia|boa tarde|boa noite|menu|inicio|iniciar|ajuda|opcoes|e ai|tudo bem)$")
_CLOSE_WORDS = frozenset({"encerrar", "encerrar atendimento", "finalizar", "finalizar atendimento", "sair", "tchau", "fechar"})


def parse(text: str, known_processes: list[str]) -> Action:
    """Interpreta a mensagem do usuário: opção do menu, número de processo escolhido ou texto livre."""
    c = clean(text)
    if c in _BY_TITLE:
        act = _BY_TITLE[c]
        # o clique volta com o título (que traz o ✅); a palavra solta "encerrar" digitada não conta como clique
        if act.kind is Kind.CLOSE and "✅" not in text:
            return Action(Kind.CLOSE_HINT)
        return act
    if c in _CLOSE_WORDS:
        return Action(Kind.CLOSE_HINT)  # texto digitado pode ser sem intenção; encerrar só pelo menu
    if _GREETING.match(c):
        return Action(Kind.MENU)
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
    who = {"usuario": "a seu pedido", "atendente": "pelo atendente", "sistema": "por inatividade (mais de 23 horas sem mensagens)"}.get(closed_by, "")
    return (f"✅ *Atendimento encerrado* {who}\nProtocolo: *{ticket_id}*\n🕒 Aberto em {fmt_time(opened_at)}\n"
            f"🕒 Encerrado em {fmt_time(closed_at)} (horário de Belém)\n\n"
            "Obrigado por falar com o assistente do TJPA (piloto). Para um novo atendimento, é só enviar uma mensagem. 👋")


MAIN_MENU_TEXT = ("👋 Olá{name}! Sou o assistente virtual do piloto do TJPA e vou te ajudar.\n\n"
                  "Como posso ajudar hoje? Escolha uma opção abaixo 👇\n\n"
                  "ℹ️ As respostas sobre processos usam documentos de demonstração, não a consulta em tempo real ao PJe.")
CLOSE_HINT_TEXT = "Para encerrar o atendimento, toque em *✅ Encerrar*. Se foi sem querer, siga normalmente ou volte ao menu 👇"
CLOSE_HINT_OPTIONS = [OPT_CLOSE, OPT_MENU]
AFTER_ANSWER_TEXT = "Posso ajudar com mais alguma coisa? 😊 Escreva a sua próxima pergunta ou escolha uma opção 👇"
INST_MENU_TEXT = "🏛️ *Balcão Virtual e informações do Tribunal*\nEscolha um assunto 👇"
LEGAL_MENU_TEXT = "⚖️ *Termos e conceitos jurídicos*\nEscolha um tema ou digite o seu 👇"
PROCESS_ASK_TEXT = ("Claro, posso ajudar com isso! 😊\nSobre *qual processo* você quer falar? Digite o número do processo, por exemplo: _6080680-32.2025.8.03.0001_.")
PROCESS_NOT_FOUND_TEXT = ("Hmm, não encontrei esse número no acervo de demonstração 🤔\nConfira se digitou certinho (formato 0000000-00.0000.8.03.0000) "
                          "e tente de novo, ou volte ao menu 👇")
PROCESS_NO_NUMBER_TEXT = "Não consegui identificar um número de processo na sua mensagem 😅\nDigite o número completo do processo (0000000-00.0000.8.03.0000) ou volte ao menu 👇"
PROCESS_NEXT_TEXT = "O que você gostaria de saber sobre este processo? ✍️ Escreva sua pergunta ou escolha uma opção 👇"
PROCESS_PICK_TEXT = "Sobre qual processo é a sua pergunta? 🤔 Digite o número do processo."


def inst_options() -> list[Option]:
    return [o for o, _ in INST_ITEMS] + [OPT_INST_OTHER, OPT_MENU]


def legal_options() -> list[Option]:
    return [o for o, _ in LEGAL_ITEMS] + [OPT_LEGAL_OTHER, OPT_MENU]


def _money(raw: str) -> str:
    return raw if raw and raw != "desconhecido" else "não informado"


def process_summary(info: dict, documents: int) -> str:
    """Resumo da capa (dentro do limite de 1024 caracteres do WhatsApp), só com dados lidos do PDF."""
    def d(v: str) -> str:
        return v if v and v != "desconhecido" else "não informado"

    dist = d(info.get("distribution_date", ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dist):
        dist = f"{dist[8:]}/{dist[5:7]}/{dist[:4]}"
    parties = "; ".join(f"{p['nome'].title()} ({p['papel'].title()})" for p in (info.get("parties") or [])[:4]) or "não informadas"
    text = (f"📄 *Processo {info['process_number']}*\n"
            f"⚖️ Classe: {d(info.get('process_class', ''))}\n🏛️ Órgão: {d(info.get('court_unit', ''))}\n"
            f"📅 Distribuição: {dist}\n💰 Valor da causa: {_money(info.get('case_value', ''))}\n"
            f"📌 Assuntos: {d(info.get('subjects', ''))[:120]}\n👥 Partes: {parties[:200]}\n"
            f"🗂️ {documents} documentos no acervo\n\nℹ️ Resumo da capa do PDF (acervo de demonstração).")
    return text[:950]
