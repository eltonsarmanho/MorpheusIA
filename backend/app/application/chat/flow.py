"""Fluxo guiado do WhatsApp: menus com botões (até 3 opções) e listas (até 10), emojis e texto livre só quando necessário.

O Chatwoot entrega o clique como uma mensagem de texto com o título da opção; por isso o reconhecimento é feito pelo título
normalizado (sem emoji nem pontuação). Limites do WhatsApp: título de botão 20 caracteres, de linha de lista 24.
Os textos seguem a voz da Júlia (app/application/persona.py); o que cada opção faz é decidido pelo handler, não pelo LLM.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from app.application import persona
from app.domain.models import Option, Profile
from app.domain.policies import find_process_numbers

BELEM = timezone(timedelta(hours=-3))  # Belém não tem horário de verão


class Kind(StrEnum):
    MENU = "menu"
    PROCESS_ASK = "process_ask"  # "Consultar processo" / "Sei o número": pergunta qual processo
    SELECT_PROCESS = "select_process"
    ASK = "ask"  # executa uma pergunta pronta no RAG
    PROMPT = "prompt"  # pede texto livre
    INST_MENU = "inst_menu"
    LEGAL_MENU = "legal_menu"
    HUMAN = "human"
    CLOSE = "close"
    CLOSE_HINT = "close_hint"  # digitou "encerrar": só orienta, não encerra
    FREE = "free"
    # roteiro TJPA
    PROFILE = "profile"  # escolha do perfil (arg: valor de Profile)
    PROFILE_MENU = "profile_menu"  # trocar de perfil
    ID_CPF = "id_cpf"
    ID_NAME = "id_name"  # "não sei o número nem o CPF": busca pelo nome
    GERAL_MENU = "geral_menu"
    GERAL_ITEM = "geral_item"  # endereço | telefone | horario
    PROC_MENU = "proc_menu"  # menu de informações do processo
    DILIGENCE = "diligence"  # arg: diligencia (campo livre do advogado) | providencia (parte/testemunha)
    CHANNEL = "channel"  # arg: whatsapp | balcao
    RETRY_CHANNEL = "retry_channel"
    OTHER_CHANNEL = "other_channel"
    UNIT_PHONE = "unit_phone"
    VARA_YES = "vara_yes"
    VARA_NO = "vara_no"
    PICK = "pick"  # arg: posição (1..5) na lista oferecida


@dataclass(frozen=True)
class Action:
    kind: Kind
    arg: str = ""


# ------------------------------------------------------------------------------------------------ opções
# O WhatsApp limita o título do botão a 20 caracteres: "Retornar ao menu principal" (26) não cabe; esta é a forma mais próxima que cabe.
OPT_MENU = Option("↩️ Retornar ao menu", "menu")
LEGACY_MENU_TITLE = "📋 Menu principal"  # botões antigos ainda na conversa continuam funcionando
OPT_PROCESS = Option("📄 Consultar processo", "processos")
OPT_OTHER_PROC = Option("📄 Outro processo", "outro_processo")
OPT_ASK = Option("🔎 Fazer pergunta", "fazer_pergunta")
OPT_INST = Option("🏛️ Balcão Virtual", "institucional")
OPT_LEGAL = Option("⚖️ Termos jurídicos", "juridico")
OPT_HUMAN_LIST = Option("🙋 Falar com atendente", "humano")
OPT_CLOSE_LIST = Option("✅ Encerrar atendimento", "encerrar")
OPT_HUMAN = Option("🙋 Atendente", "humano")
OPT_CLOSE = Option("✅ Encerrar", "encerrar")

# 1. abertura e perfil
OPT_PARTE = Option("👤 Sou parte/testemunha", "parte_testemunha")
OPT_ADV = Option("⚖️ Sou advogado(a)", "advogado")
OPT_GERAL = Option("ℹ️ Informações gerais", "geral")
OPT_SWITCH_PROFILE = Option("🔄 Trocar perfil", "trocar_perfil")
PROFILE_MENU = [OPT_PARTE, OPT_ADV, OPT_GERAL, OPT_HUMAN_LIST, OPT_CLOSE_LIST]
MAIN_MENU = PROFILE_MENU  # o menu principal passa a ser a escolha de perfil do roteiro
LEGACY_MAIN_MENU = [OPT_PROCESS, OPT_INST, OPT_LEGAL, OPT_HUMAN_LIST, OPT_CLOSE_LIST]  # títulos antigos continuam reconhecidos

# 2. informações gerais
OPT_G_ADDRESS = Option("📍 Endereço da unidade", "g_endereco")
OPT_G_PHONE = Option("📞 Telefones e contatos", "g_telefone")
OPT_G_HOURS = Option("🕒 Horário de atendimento", "g_horario")
OPT_G_OTHER = Option("💬 Outra informação", "g_outra")
GERAL_MENU = [OPT_G_ADDRESS, OPT_G_PHONE, OPT_G_HOURS, OPT_INST, OPT_G_OTHER, OPT_LEGAL, OPT_PROCESS, OPT_HUMAN_LIST, OPT_SWITCH_PROFILE,
              OPT_CLOSE_LIST]

# 3. identificação do processo
OPT_ID_NUMBER = Option("🔢 Sei o número", "id_numero")
OPT_ID_CPF = Option("🪪 Sei o CPF", "id_cpf")
OPT_ID_NONE = Option("❓ Não sei nenhum", "id_nome")
ID_MENU = [OPT_ID_NUMBER, OPT_ID_CPF, OPT_ID_NONE, OPT_GERAL, OPT_HUMAN_LIST, OPT_SWITCH_PROFILE, OPT_CLOSE_LIST]
OPT_VARA_YES = Option("👍 Sei a Vara", "vara_sim")
OPT_VARA_NO = Option("🤷 Não sei a Vara", "vara_nao")
VARA_OPTIONS = [OPT_VARA_YES, OPT_VARA_NO, OPT_MENU]
MAX_PICK = 5
PICK_OPTIONS = [Option(f"Opção {i}", f"opcao_{i}") for i in range(1, MAX_PICK + 1)]

# 4 e 5. processo localizado
OPT_DILIGENCE = Option("📝 Pedir diligência", "diligencia")
OPT_PROVIDENCE = Option("📝 Pedir providência", "providencia")
OPT_VIEW_PROC = Option("🔎 Ver o processo", "ver_processo")
OPT_MORE_PROC = Option("📄 Mais sobre o processo", "mais_processo")
OPT_HEARINGS = Option("🗓️ Audiências", "audiencias")
OPT_STATUS_SIMPLE = Option("📌 Como está o processo", "situacao")
OPT_STATUS_TECH = Option("📌 Situação atual", "situacao")
OPT_CHRONO = Option("🗓️ Cronologia", "cronologia")
ADV_MENU = [OPT_DILIGENCE, OPT_VIEW_PROC, OPT_MENU]


def process_menu(profile: Profile) -> list[Option]:
    """Menu de informações do processo (seção 5), com rótulos técnicos para advogado e simples para os demais."""
    tech = profile is Profile.ADVOGADO
    return [OPT_HEARINGS, OPT_STATUS_TECH if tech else OPT_STATUS_SIMPLE, OPT_DILIGENCE if tech else OPT_PROVIDENCE, OPT_ASK, OPT_CHRONO,
            OPT_HUMAN_LIST, OPT_OTHER_PROC, OPT_CLOSE_LIST, OPT_MENU]


PROCESS_MENU = process_menu(Profile.INDEFINIDO)

# 6. canal humano. "🏛️ Balcão Virtual" é o mesmo título do menu institucional: na escolha de canal, o handler o lê como canal.
OPT_CH_WHATSAPP = Option("💬 WhatsApp", "canal_whatsapp")
OPT_CH_BALCAO = OPT_INST
OPT_RETRY = Option("🔁 Tentar novamente", "tentar_novamente")
OPT_OTHER_CHANNEL = Option("🔀 Outro canal", "outro_canal")
OPT_UNIT_PHONE = Option("📞 Telefone unidade", "telefone_unidade")

AFTER_ANSWER = [OPT_MENU, OPT_HUMAN, OPT_CLOSE]
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
    # curtas de propósito: termos extras reduzem a cobertura lexical e a busca abstém; a regra "só o que consta" está no prompt do sistema
    "audiencias": "Há audiência designada no processo {n}? Qual a data e o horário?",
    "situacao": "Qual é a situação atual do processo {n}? Qual o último documento e a data?",
}
# Pergunta ao RAG institucional quando não há unidade cadastrada para responder pelo cadastro.
GERAL_FALLBACK_QUESTIONS = {
    "endereco": "Quais são as unidades e os contatos do Balcão Virtual?",
    "telefone": "Quais são as unidades e os contatos do Balcão Virtual?",
    "horario": "Qual o horário de funcionamento do Balcão Virtual?",
}

PROMPTS = {
    "outra_proc": "Claro! 😊 Pode escrever a sua pergunta sobre o processo *{n}*.\nPor exemplo: _Qual foi a última decisão?_ ou _Quem são as partes?_",
    "inst_outra": "✍️ Escreva sua dúvida sobre o Tribunal ou o Balcão Virtual.\nExemplo: _Quem pode usar o Balcão Virtual?_",
    "jur_outro": "✍️ Escreva o termo ou a dúvida jurídica.\nExemplo: _O que significa trânsito em julgado?_",
}

_BY_TITLE: dict[str, Action] = {}
_TITLES: set[str] = set()  # títulos exatos (com emoji): só um toque no botão escapa de um campo livre


def clean(text: str) -> str:
    """Minúsculas, sem acento, emoji nem pontuação: "✅ Encerrar atendimento" -> "encerrar atendimento"."""
    folded = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9/ ]+", " ", folded)).strip()


def _register() -> None:
    def add(opt: Option, action: Action) -> None:
        _BY_TITLE[clean(opt.title)] = action
        _TITLES.add(opt.title)

    add(OPT_MENU, Action(Kind.MENU))
    add(Option(LEGACY_MENU_TITLE, "menu"), Action(Kind.MENU))
    add(OPT_PROCESS, Action(Kind.PROCESS_ASK))
    add(OPT_OTHER_PROC, Action(Kind.PROCESS_ASK))
    add(OPT_INST, Action(Kind.INST_MENU))
    add(OPT_LEGAL, Action(Kind.LEGAL_MENU))
    add(OPT_HUMAN_LIST, Action(Kind.HUMAN))
    add(OPT_HUMAN, Action(Kind.HUMAN))
    add(OPT_CLOSE_LIST, Action(Kind.CLOSE))
    add(OPT_CLOSE, Action(Kind.CLOSE))
    add(OPT_CHRONO, Action(Kind.ASK, "cronologia"))
    add(OPT_HEARINGS, Action(Kind.ASK, "audiencias"))
    add(OPT_STATUS_SIMPLE, Action(Kind.ASK, "situacao"))
    add(OPT_STATUS_TECH, Action(Kind.ASK, "situacao"))
    add(OPT_ASK, Action(Kind.PROMPT, "outra_proc"))
    for opt, question in INST_ITEMS + LEGAL_ITEMS:
        add(opt, Action(Kind.ASK, question))
    add(OPT_INST_OTHER, Action(Kind.PROMPT, "inst_outra"))
    add(OPT_LEGAL_OTHER, Action(Kind.PROMPT, "jur_outro"))
    # roteiro TJPA
    add(OPT_PARTE, Action(Kind.PROFILE, Profile.PARTE_TESTEMUNHA.value))
    add(OPT_ADV, Action(Kind.PROFILE, Profile.ADVOGADO.value))
    add(OPT_GERAL, Action(Kind.GERAL_MENU))
    add(OPT_SWITCH_PROFILE, Action(Kind.PROFILE_MENU))
    add(OPT_G_ADDRESS, Action(Kind.GERAL_ITEM, "endereco"))
    add(OPT_G_PHONE, Action(Kind.GERAL_ITEM, "telefone"))
    add(OPT_G_HOURS, Action(Kind.GERAL_ITEM, "horario"))
    add(OPT_G_OTHER, Action(Kind.PROMPT, "inst_outra"))
    add(OPT_ID_NUMBER, Action(Kind.PROCESS_ASK))
    add(OPT_ID_CPF, Action(Kind.ID_CPF))
    add(OPT_ID_NONE, Action(Kind.ID_NAME))
    add(OPT_VARA_YES, Action(Kind.VARA_YES))
    add(OPT_VARA_NO, Action(Kind.VARA_NO))
    for i, opt in enumerate(PICK_OPTIONS, start=1):
        add(opt, Action(Kind.PICK, str(i)))
    add(OPT_DILIGENCE, Action(Kind.DILIGENCE, "diligencia"))
    add(OPT_PROVIDENCE, Action(Kind.DILIGENCE, "providencia"))
    add(OPT_VIEW_PROC, Action(Kind.PROC_MENU))
    add(OPT_MORE_PROC, Action(Kind.PROC_MENU))
    add(OPT_CH_WHATSAPP, Action(Kind.CHANNEL, "whatsapp"))
    add(OPT_RETRY, Action(Kind.RETRY_CHANNEL))
    add(OPT_OTHER_CHANNEL, Action(Kind.OTHER_CHANNEL))
    add(OPT_UNIT_PHONE, Action(Kind.UNIT_PHONE))


_register()
_GREETING = re.compile(r"^(oi+|ola+|bom dia|boa tarde|boa noite|menu|inicio|iniciar|ajuda|opcoes|e ai|tudo bem)$")
_CLOSE_WORDS = frozenset({"encerrar", "encerrar atendimento", "finalizar", "finalizar atendimento", "sair", "tchau", "fechar"})


def is_option_click(text: str) -> bool:
    """O texto é exatamente o título de uma opção (toque no botão), e não algo digitado."""
    return text.strip() in _TITLES


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
            "Guarde este número: ele identifica este atendimento. Não é protocolo de processo judicial.")


def closing_text(ticket_id: str, opened_at: str, closed_at: str, closed_by: str) -> str:
    who = {"usuario": "a seu pedido", "atendente": "pelo atendente", "sistema": "por inatividade (mais de 23 horas sem mensagens)"}.get(closed_by, "")
    return (f"✅ *Atendimento encerrado* {who}\nProtocolo: *{ticket_id}*\n🕒 Aberto em {fmt_time(opened_at)}\n"
            f"🕒 Encerrado em {fmt_time(closed_at)} (horário de Belém)\n\n"
            f"Obrigada por conversar comigo. Quando precisar, é só enviar uma nova mensagem. 💙\n_{persona.SIGNATURE}_")


DEMO_NOTE = "ℹ️ As respostas sobre processos usam documentos de demonstração, não a consulta em tempo real ao PJe."
PROFILE_MENU_TEXT = "{intro}\n\nPara eu orientar você do jeito certo, me conta: qual é o seu caso? Escolha uma opção 👇\n\n" + DEMO_NOTE
MAIN_MENU_TEXT = PROFILE_MENU_TEXT
CLOSE_HINT_TEXT = "Para encerrar o atendimento, toque em *✅ Encerrar*. Se foi sem querer, siga normalmente ou volte ao menu 👇"
CLOSE_HINT_OPTIONS = [OPT_CLOSE, OPT_MENU]
AFTER_ANSWER_TEXT = ("Isso esclareceu sua dúvida? Posso ajudar com mais alguma coisa? 😊 Escreva a próxima pergunta ou, se preferir, "
                     "fale com o atendimento humano 👇")
INST_MENU_TEXT = "🏛️ *Balcão Virtual e informações do Tribunal*\nEscolha um assunto 👇"
LEGAL_MENU_TEXT = "⚖️ *Termos e conceitos jurídicos*\nEscolha um tema ou digite o seu 👇"
PROCESS_ASK_TEXT = ("Claro, posso ajudar com isso! 😊\nSobre *qual processo* você quer falar? Digite o número do processo, com 20 dígitos, "
                    "por exemplo: _6080680-32.2025.8.03.0001_.")
PROCESS_NOT_FOUND_TEXT = ("Hmm, não encontrei esse número no acervo de demonstração deste piloto 🤔 Eu não consulto o PJe em tempo real. "
                          "Confira se digitou certinho (formato 0000000-00.0000.0.00.0000) e tente de novo, ou volte ao menu 👇")
PROCESS_BAD_DIGITS_TEXT = ("Hmm, não encontrei esse número, e os dígitos de verificação dele não conferem 🤔 Talvez algum número tenha "
                           "sido trocado. Pode conferir e digitar de novo? O formato é 0000000-00.0000.0.00.0000.")
PROCESS_BAD_FORMAT_TEXT = ("Não consegui reconhecer um número de processo completo nessa mensagem. Ele tem 20 dígitos, no formato "
                           "0000000-00.0000.0.00.0000. Pode conferir e enviar de novo?")
PROCESS_NEXT_TEXT = "O que você gostaria de saber sobre este processo? ✍️ Escreva sua pergunta ou escolha uma opção 👇"
PROCESS_PICK_TEXT = "Sobre qual processo é a sua pergunta? 🤔 Digite o número do processo."

ID_MENU_TEXT = {
    Profile.PARTE_TESTEMUNHA: "Combinado! Vou explicar tudo de forma simples. 😊\nPara localizar o seu processo, escolha uma opção 👇",
    Profile.ADVOGADO: "Certo. Para localizar os autos, escolha como deseja identificar o processo 👇",
}
GERAL_MENU_TEXT = "Claro! 😊 O que você deseja consultar? Escolha uma opção 👇"
GERAL_UNIT_ASK_TEXT = "Para eu procurar a informação certa, informe a *cidade* e, se souber, o *nome da unidade*. ✍️"
GERAL_NO_REGISTRY_TEXT = ("Ainda não tenho o cadastro oficial de endereços, telefones e horários das unidades neste atendimento. "
                          "Vou procurar nas informações institucionais aprovadas do Tribunal. 🔎")
UNIT_NOT_FOUND_TEXT = "Não localizei essa unidade no cadastro deste atendimento. Você pode tentar com outro nome ou escolher outra opção 👇"
UNIT_NOT_FOUND_SEARCHING_TEXT = ("Não localizei essa unidade no cadastro deste atendimento. Vou procurar nas informações institucionais aprovadas "
                              "do Tribunal. 🔎")
UNIT_MANY_TEXT = "Encontrei mais de uma unidade com essa descrição:\n{lines}\n\nQual delas? Escreva o nome da unidade. ✍️"
CPF_ASK_TEXT = ("Informe o CPF de uma das partes, só com os números. 🔒 Vou usá-lo apenas para esta busca e não vou repeti-lo nas "
                "mensagens.")
CPF_NOT_FOUND_TEXT = "Não consegui identificar um CPF com 11 dígitos nessa mensagem. Pode enviar de novo, só com os números?"
CPF_INVALID_TEXT = "Os dígitos desse CPF não conferem. Pode conferir e enviar de novo?"
CPF_UNAVAILABLE_TEXT = ("Obrigada. No momento, a busca de processos por CPF não está disponível neste atendimento: o piloto não tem "
                        "acesso ao sistema processual do Tribunal. Para não deixar você sem resposta, você pode informar o número do "
                        "processo, buscar pelo nome de uma das partes ou falar com o atendimento humano 👇")
NAME_ASK_TEXT = "Tudo bem! 😊 Informe o *nome completo* de uma das partes do processo. ✍️"
NAME_SHORT_TEXT = "Para buscar com segurança, preciso do *nome completo* (nome e sobrenome) de uma das partes. Pode enviar de novo?"
NAME_PICK_TEXT = ("Encontrei {n} processo(s) com esse nome no acervo de demonstração do piloto (documentos de outro tribunal, não a base "
                  "do TJPA):\n{lines}\n\nQual deles é o seu? Toque na opção correspondente 👇")
NAME_MANY_TEXT = ("Encontrei muitos processos com esse nome. Para eu não escolher o errado, informe a *cidade* ou a *unidade* (Vara) "
                  "onde o processo tramita. ✍️")
PICK_INVALID_TEXT = "Não consegui identificar a opção escolhida. Toque em uma das opções da lista, por favor 👇"
VARA_ASK_TEXT = ("Não consegui identificar o processo com segurança nas informações disponíveis. Você sabe em qual *Vara* ou *unidade* "
                 "o processo tramita?")
VARA_UNIT_ASK_TEXT = "Informe a *cidade* e o *nome da Vara ou unidade*. ✍️"
VARA_UNIT_NOTED_TEXT = ("Obrigada! Vou considerar a unidade que você informou: *{unit}*. O processo não foi localizado por aqui, então "
                        "a equipe vai precisar dos dados que você tiver.")
TELEJUDICIARIO_TEXT = "Você pode procurar o TeleJudiciário: {contact}"
NO_TELEJUDICIARIO_TEXT = ("Ainda não tenho o contato do TeleJudiciário cadastrado neste atendimento. Posso encaminhar você para o "
                          "atendimento humano, que vai ajudar a localizar a unidade 👇")

ADV_LOCATED_TEXT = "Localizei o processo *{n}*{unit} no acervo de demonstração. Como deseja prosseguir? 👇"
DILIGENCE_ASK_TEXT = ("Descreva a diligência que deseja solicitar à secretaria. ✍️ Na próxima etapa, você escolherá o canal de "
                      "atendimento humano.")
DILIGENCE_EMPTY_TEXT = "Não recebi o texto da diligência. Pode escrever o que deseja solicitar à secretaria?"
DILIGENCE_TOO_LONG_TEXT = ("A descrição passou de {n} caracteres. Para eu guardar o texto completo, sem cortes, pode enviar uma versão "
                           "mais curta?")
CHANNEL_ASK_TEXT = "Posso encaminhar sua solicitação para a equipe responsável.\nComo deseja ser atendido pela *{unit}*? 👇"
CHANNEL_NONE_TEXT = ("No momento, não tenho um canal de atendimento humano disponível para essa unidade. Mantive as informações que "
                     "você passou. Você pode tentar mais tarde ou voltar ao menu 👇")
WHATSAPP_LINK_TEXT = ("Você escolheu atendimento pelo WhatsApp oficial da *{unit}*.{proc}\n\n👉 Abra o WhatsApp pelo link abaixo e "
                      "*toque em enviar* na mensagem já preenchida para concluir o encaminhamento. Apenas abrir a conversa não envia a "
                      "solicitação.\n{link}")
WHATSAPP_NO_NUMBER_TEXT = ("Não tenho o WhatsApp oficial da *{unit}* cadastrado, então não consigo abrir essa conversa. Mantive o texto "
                           "que você informou. Você prefere escolher outro canal? 👇")
BALCAO_NO_LINK_TEXT = ("O endereço do Balcão Virtual da *{unit}* não está disponível no cadastro deste atendimento. Mantive o texto que "
                       "você informou. Você prefere escolher outro canal? 👇")
HANDOFF_CHANNEL_FAILED_TEXT = ("Não consegui confirmar o envio da sua solicitação. Mantive o texto que você informou. Você prefere tentar "
                               "novamente ou escolher outro canal? 👇")
UNIT_PHONE_TEXT = "📞 Telefone da *{unit}*: {phone}{hours}"
CONTINUE_TEXT = "Como deseja continuar? 👇"


def profile_menu_text(first_name: str = "") -> str:
    return PROFILE_MENU_TEXT.format(intro=persona.intro(first_name))


def continue_options(has_process: bool) -> list[Option]:
    """Menu de continuidade (seção 7)."""
    head = [OPT_MORE_PROC, OPT_OTHER_PROC] if has_process else [OPT_PROCESS]
    return head + [OPT_GERAL, OPT_HUMAN_LIST, OPT_CLOSE_LIST]


def inst_options() -> list[Option]:
    return [o for o, _ in INST_ITEMS] + [OPT_INST_OTHER, OPT_MENU]


def legal_options() -> list[Option]:
    return [o for o, _ in LEGAL_ITEMS] + [OPT_LEGAL_OTHER, OPT_MENU]


def pick_lines(candidates) -> str:
    return "\n".join(f"{i}. {c.process_number} — {c.process_class} — participação: {c.participation}"
                     for i, c in enumerate(candidates, start=1))


def balcao_text(unit: str, url: str, direct: bool, process_number: str | None, hours: str | None, has_diligence: bool) -> str:
    proc = f", responsável pelo processo *{process_number}*" if process_number else ""
    lines = [f"Você escolheu atendimento pelo Balcão Virtual da *{unit}*{proc}.", "", f"🏛️ Acesse: {url}"]
    if not direct:
        lines.append("Este é o endereço geral do Balcão Virtual do TJPA, onde você escolhe a unidade. Ainda não tenho o link direto "
                     f"da {unit}.")
    if hours:
        lines.append(f"🕒 Horário de atendimento: {hours}")
    if has_diligence:
        lines += ["", "Sua diligência informada está na próxima mensagem. Para copiar, toque e segure nela e escolha *Copiar*. Ao entrar "
                      "no Balcão Virtual, informe o número do processo e apresente essa solicitação ao atendente."]
    lines += ["", "ℹ️ O Balcão Virtual não está integrado a este atendimento: a solicitação só chega à equipe quando você a apresentar lá."]
    return "\n".join(lines)


def _money(raw: str) -> str:
    return raw if raw and raw != "desconhecido" else "não informado"


def _parties_text(parties: list[dict], limit: int = 200) -> str:
    """"NOME (PAPEL); NOME (PAPEL)" em caixa alta, sem cortar uma parte ao meio."""
    items = [f"{p['nome']} ({p['papel']})".upper() for p in parties[:4]]
    text = ""
    for item in items:
        candidate = f"{text}; {item}" if text else item
        if len(candidate) > limit:
            break
        text = candidate
    if not text and items:
        text = items[0][:limit]  # uma única parte já passa do limite: corta só nesse caso
    return text or "não informadas"


def handoff_notice(team: str, ticket_id: str | None = None, request: bool = False) -> str:
    """Aviso ao usuário depois que a transferência para a equipe foi confirmada."""
    protocol = f"\n🎫 Protocolo: {ticket_id}" if ticket_id else ""
    caveat = "\nℹ️ O encaminhamento não significa que a diligência ou a providência já foi realizada." if request else ""
    return (f"Aguarde um momento, por favor. 😊 Sua conversa foi encaminhada para nossa equipe de {team}, e em breve uma pessoa da "
            f"equipe dará continuidade ao seu atendimento por aqui.{protocol}{caveat}\nObrigada pela paciência!")


def process_summary(info: dict, documents: int) -> str:
    """Resumo da capa (dentro do limite de 1024 caracteres do WhatsApp), só com dados lidos do PDF."""
    def d(v: str) -> str:
        return v if v and v != "desconhecido" else "não informado"

    dist = d(info.get("distribution_date", ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dist):
        dist = f"{dist[8:]}/{dist[5:7]}/{dist[:4]}"
    parties = _parties_text(info.get("parties") or [])
    text = (f"📄 *Processo {info['process_number']}*\n"
            f"⚖️ Classe: {d(info.get('process_class', ''))}\n🏛️ Órgão: {d(info.get('court_unit', ''))}\n"
            f"📅 Distribuição: {dist}\n💰 Valor da causa: {_money(info.get('case_value', ''))}\n"
            f"📌 Assuntos: {d(info.get('subjects', ''))[:120]}\n👥 Partes Interessadas: {parties}\n"
            f"🗂️ {documents} documentos no acervo\n\nℹ️ Resumo da capa do PDF (acervo de demonstração; não é consulta ao PJe).")
    return text[:950]
