"""Ouvidoria da Mulher no WhatsApp: opções, textos e nota de contexto (OVM-01 a OVM-22).

Só dados e textos, sem I/O nem dependência de `flow.py` (o `flow.py` registra estas opções). Os textos de orientação são
derivados da Cartilha da Ouvidoria da Mulher do TJPA, com a página citada; precisam de revisão da Ouvidoria antes do uso real.
O que cada opção faz é decidido pelo handler. Limites do WhatsApp: botão 20 caracteres, linha de lista 24.
"""

from __future__ import annotations

from app.domain.models import Option
from app.domain.triage_mulher import OuvidoriaMulher, Triage

SOURCE = "_Fonte: Cartilha da Ouvidoria da Mulher (TJPA), {pages}._"
TEAM_DEFAULT = "ouvidoria_mulher"
LABEL_SERVICE, LABEL_URGENT = "ouvidoria_mulher", "urgencia_imediata"

# ------------------------------------------------------------------------------------------------ opções
OPT_ENTRY = Option("💜 Ouvidoria da Mulher", "ouvidoria_mulher")
OPT_DANGER = Option("🚨 Perigo agora", "perigo_agora")
OPT_ORIENT = Option("📖 Orientações", "orientacoes")
OPT_TEAM = Option("📝 Falar com equipe", "falar_equipe")
OPT_SKIP = Option("Prefiro não dizer", "prefiro_nao_dizer")
OPT_SKIP_STEP = Option("⏭️ Pular esta etapa", "pular_etapa")
OPT_SEND = Option("✅ Encaminhar", "encaminhar")
OPT_REDO = Option("✏️ Refazer", "refazer")

# (campo, valor) -> opção; o título é o que volta do WhatsApp como mensagem do usuário
ANSWERS: dict[tuple[str, str], Option] = {
    ("perigo", "sim"): Option("🚨 Sim, há perigo", "perigo_sim"),
    ("perigo", "nao"): Option("Não há perigo agora", "perigo_nao"),
    ("para_quem", "propria"): Option("👤 Para mim", "para_mim"),
    ("para_quem", "terceiro"): Option("🤝 Outra pessoa", "outra_pessoa"),
    ("assunto", "fisica"): Option("Violência física", "a_fisica"),
    ("assunto", "sexual"): Option("Violência sexual", "a_sexual"),
    ("assunto", "psicologica"): Option("Violência psicológica", "a_psicologica"),
    ("assunto", "patrimonial"): Option("Violência patrimonial", "a_patrimonial"),
    ("assunto", "moral"): Option("Violência moral", "a_moral"),
    ("assunto", "online"): Option("Violência on-line", "a_online"),
    ("assunto", "perseguicao"): Option("Perseguição (stalking)", "a_perseguicao"),
    ("assunto", "assedio_importunacao"): Option("Assédio/importunação", "a_assedio"),
    ("assunto", "morosidade"): Option("Demora em processo", "a_morosidade"),
    ("assunto", "outro"): Option("Outro / não sei", "a_outro"),
    ("ambiente", "parceiro_ex"): Option("Parceiro(a) ou ex", "e_parceiro"),
    ("ambiente", "familiar"): Option("Familiar", "e_familiar"),
    ("ambiente", "trabalho"): Option("Trabalho", "e_trabalho"),
    ("ambiente", "espaco_publico"): Option("Espaço público", "e_publico"),
    ("ambiente", "internet"): Option("Internet / redes", "e_internet"),
    ("ambiente", "servico_publico"): Option("Serviço público", "e_servico"),
    ("ambiente", "outro"): Option("Outro lugar", "e_outro"),
    ("processo", "numero"): Option("📄 Tenho o número", "p_numero"),
    ("processo", "sem_numero"): Option("📄 Há, sem o número", "p_sem_numero"),
    ("processo", "nao"): Option("Não há processo", "p_nao"),
    ("processo", "nao_sei"): Option("Não sei informar", "p_nao_sei"),
    ("contato", "sim"): Option("✅ Sim, é seguro", "c_sim"),
    ("contato", "nao"): Option("⚠️ Não é seguro", "c_nao"),
    ("contato", "telefone"): Option("📞 Prefiro telefone", "c_telefone"),
}

STEP_OPTIONS: dict[str, list[Option]] = {
    "perigo": [ANSWERS[("perigo", "sim")], ANSWERS[("perigo", "nao")], OPT_SKIP],
    "para_quem": [ANSWERS[("para_quem", "propria")], ANSWERS[("para_quem", "terceiro")], OPT_SKIP],
    "assunto": [ANSWERS[("assunto", v)] for v in ("fisica", "sexual", "psicologica", "patrimonial", "moral", "online", "perseguicao",
                                                  "assedio_importunacao", "morosidade", "outro")],
    "ambiente": [ANSWERS[("ambiente", v)] for v in ("parceiro_ex", "familiar", "trabalho", "espaco_publico", "internet", "servico_publico",
                                                    "outro")] + [OPT_SKIP],
    "processo": [ANSWERS[("processo", v)] for v in ("numero", "sem_numero", "nao", "nao_sei")] + [OPT_SKIP],
    "contato": [ANSWERS[("contato", v)] for v in ("sim", "nao", "telefone")] + [OPT_SKIP],
    "numero": [OPT_SKIP_STEP],
    "relato": [OPT_SKIP_STEP],
}

# tema de orientação -> (opção, páginas da Cartilha)
TOPICS: dict[str, tuple[Option, str]] = {
    "ouvidoria": (Option("O que é a Ouvidoria", "o_ouvidoria"), "p. 4 e 19"),
    "formas": (Option("Formas de violência", "o_formas"), "p. 6 a 9"),
    "assedio": (Option("Assédio: diferenças", "o_assedio"), "p. 10 a 13"),
    "penha": (Option("Lei Maria da Penha", "o_penha"), "p. 15 e 16"),
    "feminicidio": (Option("Lei do Feminicídio", "o_feminicidio"), "p. 17"),
    "denunciar": (Option("Como denunciar", "o_denunciar"), "p. 21"),
    "telefones": (Option("Telefones úteis", "o_telefones"), ""),
}


def all_options() -> list[Option]:
    opts = [OPT_ENTRY, OPT_DANGER, OPT_ORIENT, OPT_TEAM, OPT_SKIP, OPT_SKIP_STEP, OPT_SEND, OPT_REDO]
    opts += list(ANSWERS.values()) + [o for o, _ in TOPICS.values()]
    return opts


ENTRY_OPTIONS = [OPT_DANGER, OPT_ORIENT, OPT_TEAM]
AFTER_EMERGENCY = [OPT_TEAM, OPT_ORIENT]


def orient_options(menu: Option) -> list[Option]:
    return [o for o, _ in TOPICS.values()] + [OPT_TEAM, menu]


def summary_options(menu: Option) -> list[Option]:
    return [OPT_SEND, OPT_REDO, menu]


# ------------------------------------------------------------------------------------------------ textos
EMERGENCY_TEXT = ("🚨 Se você ou alguém está em perigo agora, ligue *190* (Polícia Militar). O *180* (Central de Atendimento à Mulher) "
                  "atende 24 horas, é gratuito e confidencial. A Ouvidoria da Mulher não é um serviço de emergência.")
WELCOME_TEXT = ("💜 Você pode falar com calma por aqui. A *Ouvidoria da Mulher* do TJPA é um espaço de escuta, acolhimento e orientação "
                "para mulheres que se sintam vítimas de qualquer violência.\n\n" + EMERGENCY_TEXT + "\n\nComo posso ajudar? 👇")
OFFER_TEXT = ("Se isso está acontecendo com você ou com alguém que você conhece, não está só. 💜 Em perigo agora, ligue *190*. O *180* "
              "atende 24 horas. Se quiser, posso orientar ou encaminhar para a *Ouvidoria da Mulher* do TJPA 👇")
EMERGENCY_NEXT_TEXT = "Se quiser, posso também registrar um pedido para a equipe da Ouvidoria ou dar orientações 👇"
ORIENT_MENU_TEXT = "📖 Sobre o que você quer saber? Escolha um tema 👇"
ORIENT_AFTER_TEXT = "Posso ajudar com mais alguma orientação? 👇"

QUESTIONS = {
    "perigo": "Primeiro, uma pergunta importante: *alguém está em perigo agora* ou teme pela própria vida? 👇",
    "para_quem": "Obrigada por responder. O atendimento é para você ou para outra pessoa? 👇",
    "assunto": "Qual é o *assunto principal*? Escolha o que mais se aproxima; não precisa dar detalhes. 👇",
    "ambiente": "Onde ou com quem isso aconteceu? 👇",
    "processo": "Já existe *processo ou medida protetiva* sobre isso? 👇",
    "numero": ("Pode informar o número do processo? Ele tem 20 dígitos, como _0000000-00.0000.0.00.0000_. "
               "Se não tiver à mão, toque em pular. ✍️"),
    "contato": ("Posso continuar esta conversa por *este WhatsApp*? Pense se é seguro: alguém pode ver as mensagens neste "
                "celular. 👇"),
    "relato": ("Se quiser, conte em poucas palavras o que aconteceu. Você não precisa contar tudo agora e pode pular. ✍️"),
}
NUMBER_BAD_TEXT = ("Não consegui reconhecer um número de processo completo. Ele tem 20 dígitos, no formato 0000000-00.0000.0.00.0000. "
                   "Pode conferir e enviar de novo, ou tocar em pular?")
RELATO_EMPTY_TEXT = "Não recebi o texto. Pode escrever em poucas palavras ou tocar em pular? ✍️"
RELATO_LONG_TEXT = ("O texto passou de {n} caracteres. Para eu guardar tudo sem cortes, pode enviar uma versão mais curta? "
                    "A equipe ouvirá você com calma depois.")
STALE_TEXT = "Vamos continuar de onde paramos. 😊"
SUMMARY_TEXT = ("Este é o resumo do que você informou:\n{summary}\n\nPosso encaminhar para a equipe da *Ouvidoria da Mulher*? "
                "Ela atende das {hours} (horário de Belém). Ao encaminhar, a equipe verá o que você informou. 👇")
UNSAFE_TEXT = ("Entendi, e obrigada por avisar. Por segurança, a equipe não vai responder por este WhatsApp. Para falar com a "
               "Ouvidoria:\n{contacts}\n\nOu ligue *180*, que atende 24 horas. Se alguém puder ver este celular, considere apagar esta "
               "conversa depois. Se quiser, posso registrar o seu pedido para a equipe, *sem retorno por aqui*. Vou só fazer mais "
               "algumas perguntas. 💜")
SUMMARY_NORETURN_TEXT = ("Este é o resumo do que você informou:\n{summary}\n\nPosso registrar o seu pedido na *Ouvidoria da Mulher* "
                         "*sem retorno por este WhatsApp*? Como não sei se este contato é seguro, a equipe não vai responder por aqui. "
                         "Para falar com a Ouvidoria:\n{contacts}\n👇")
LEFT_TEXT = ("Tudo bem, saí do atendimento e não guardei o que você informou. Quando quiser, é só voltar. 💜 Em perigo agora, "
             "ligue *190*; o *180* atende 24 horas.")
NOT_ENABLED_TEXT = ""
FAILED_TEXT = ("Não consegui confirmar o envio para a Ouvidoria da Mulher. Mantive o que você informou. Você pode tentar de novo ou "
               "falar direto com a Ouvidoria:\n{contacts}\n\nEm perigo agora, ligue *190*. 👇")

HOURS_IN = "A equipe atende das {hours} (horário de Belém)."
HOURS_OUT = ("Agora estamos fora do horário de atendimento ({hours}, horário de Belém). A equipe responde quando retomar o "
             "atendimento.")


def handoff_notice(ticket_id: str | None, contacts: OuvidoriaMulher, open_now: bool) -> str:
    """Aviso depois da confirmação do Chatwoot (OVM-19, OVM-20): protocolo e orientação de guardá-lo para atendimento presencial."""
    hours = (HOURS_IN if open_now else HOURS_OUT).format(hours=contacts.hours_text)
    protocol = ""
    if ticket_id:
        protocol = (f"\n🎫 Protocolo: *{ticket_id}*\n📌 *Guarde este número*: ele identifica este atendimento e pode ser útil caso você "
                    "precise de atendimento presencial no futuro.")
    return ("Aguarde um momento, por favor. 💜 Sua conversa foi encaminhada para a equipe da *Ouvidoria da Mulher*, e uma pessoa da "
            f"equipe dará continuidade ao atendimento por aqui.{protocol}\n{hours}\nEm perigo agora, ligue *190*.\nObrigada pela paciência!")


def handoff_notice_no_return(ticket_id: str | None, contacts: OuvidoriaMulher) -> str:
    """Contato inseguro (OVM-16): registra, não promete continuidade por este WhatsApp e orienta o contato direto."""
    protocol = ""
    if ticket_id:
        protocol = (f"\n🎫 Protocolo: *{ticket_id}*\n📌 *Guarde este número*: ele identifica este atendimento e pode ser útil caso você "
                    "precise de atendimento presencial no futuro.")
    return ("Registrei o seu pedido na *Ouvidoria da Mulher*. 💜 Por segurança, a equipe *não vai responder por este WhatsApp*."
            f"{protocol}\nPara falar com a Ouvidoria:\n{contacts.contact_lines()}\nO *180* atende 24 horas. Em perigo agora, ligue *190*.")


def phones_text(contacts: OuvidoriaMulher) -> str:
    text = ("📞 *Telefones úteis*\n• Emergência: *190* (Polícia Militar)\n• Central de Atendimento à Mulher: *180* (24 horas, gratuito e "
            f"confidencial)\n• Ouvidoria da Mulher do TJPA:\n{contacts.contact_lines()}")
    return text + (f"\n_Fonte: {contacts.source}._" if contacts.source else "")


# ------------------------------------------------------------------------------------------------ orientações da Cartilha
_TEXTS = {
    "ouvidoria": (
        "📖 *O que é a Ouvidoria da Mulher*\nÉ um espaço seguro de escuta atenta, acolhimento e orientação para mulheres que se sintam "
        "vítimas de qualquer violência (doméstica, psicológica, patrimonial) ou que tenham sofrido assédio moral, sexual ou outra forma de "
        "violência.\n\nA Ouvidoria:\n• recebe denúncias sobre qualquer violência contra a mulher;\n• atua em casos de demora no andamento "
        "de processos no TJPA;\n• faz escuta ativa e colhe o depoimento;\n• orienta;\n• encaminha as demandas aos órgãos competentes.\n\n"
        "No TJPA, ela recebe e encaminha demandas sobre procedimentos judiciais de violência contra a mulher (Carta de Serviços do TJPA)."),
    "formas": (
        "📖 *Formas de violência contra a mulher*\n• *Física*: ofende a integridade ou a saúde do corpo; não é só o que deixa marcas "
        "(empurrar, beliscar, puxar os cabelos, sacudir, rasgar roupas).\n• *Sexual*: contato sexual não consentido, por intimidação, "
        "ameaça, força ou aproveitando a vulnerabilidade, inclusive em relação íntima.\n• *Psicológica*: causa dano emocional e diminui a "
        "autoestima (xingar, humilhar, ameaçar, intimidar, criticar sempre).\n• *Patrimonial*: objetos, ferramentas de trabalho, "
        "documentos ou bens retidos ou destruídos.\n• *Moral*: abala a honra (injúria, calúnia e difamação).\n• *On-line*: violência "
        "cometida ou agravada por tecnologia, como divulgar imagens íntimas sem consentimento ou vigiar pela internet.\n• *Perseguição "
        "(stalking)*: perseguir alguém repetidamente, ameaçando ou invadindo a liberdade e a privacidade (art. 147-A do Código Penal).\n"
        "• *Tráfico de mulheres*: levar pessoas de um lugar a outro por fraude ou engano, para exploração.\n• *Feminicídio*: assassinato "
        "de mulher pela condição de ser mulher."),
    "assedio": (
        "📖 *Assédio moral, assédio sexual e importunação sexual*\n• *Assédio moral*: conduta abusiva repetida e sistemática contra a "
        "dignidade ou a integridade de alguém, que ameaça o emprego ou degrada o clima de trabalho. Exige repetição, intenção, ser "
        "dirigida a uma pessoa ou grupo e acontecer ao longo do tempo.\n• *Assédio sexual*: condutas, palavras ou gestos de natureza "
        "sexual que causam desconforto e constrangimento. É crime (art. 216-A do Código Penal). Não precisa de contato físico e pode "
        "ocorrer fora do local de trabalho.\n• *Importunação sexual*: também é crime (art. 215-A do Código Penal), como passar a mão, "
        "roubar um beijo ou roçar partes íntimas em outra pessoa, comum em transportes públicos e praticada por desconhecidos."),
    "penha": (
        "📖 *Lei Maria da Penha*\nA Lei 11.340/2006 coíbe a violência doméstica e familiar contra a mulher e prevê prevenção, "
        "assistência e proteção, além da responsabilização de quem agride.\n\nPelo art. 5º, é violência doméstica e familiar qualquer "
        "ação ou omissão, baseada no gênero, que cause morte, lesão ou sofrimento físico, sexual ou psicológico, ou dano moral ou "
        "patrimonial, no ambiente doméstico, no ambiente familiar ou em qualquer relação íntima de afeto.\n\nA Lei 14.550/2023 prevê que "
        "a mulher tem direito à medida protetiva a partir da denúncia à polícia ou de alegações por escrito à Justiça, sem prazo "
        "definido: ela vale enquanto persistir o risco. Cada caso é analisado por quem decide o processo."),
    "feminicidio": (
        "📖 *Lei do Feminicídio*\nA Lei 13.104/2015 tornou crime específico, qualificado e hediondo o homicídio de mulher por razões da "
        "condição de sexo feminino (art. 121, § 2º, VI, do Código Penal). Isso ocorre quando o crime é praticado no âmbito da violência "
        "doméstica e familiar ou por menosprezo ou discriminação à condição de mulher."),
    "denunciar": (
        "📖 *Como denunciar*\nA Cartilha sugere:\n1. Contar o ocorrido a colegas, amigos e familiares.\n2. Relatar também na Ouvidoria e "
        "no setor de Recursos Humanos do local de trabalho.\n3. Reunir as provas possíveis, como bilhetes, presentes e testemunhas.\n4. "
        "Registrar o caso na Delegacia de Atendimento Especial à Mulher (DEAM) ou em qualquer delegacia.\n5. Comunicar o fato ao "
        "sindicato, à Delegacia Regional do Trabalho e ao Ministério Público do Trabalho.\n6. Ligar para o *180*: gratuito, "
        "confidencial, de qualquer lugar do Brasil, 24 horas.\n\n⚠️ Os passos 2 e 5 (RH, sindicato e Delegacia Regional do Trabalho) "
        "valem para assédio no trabalho.\n\nA Cartilha informa que as denúncias e informações são recebidas e tratadas com sigilo "
        "profissional."),
}


def topic_text(topic: str) -> str:
    _, pages = TOPICS[topic]
    return _TEXTS[topic] + (f"\n\n{SOURCE.format(pages=pages)}" if pages else "")


# ------------------------------------------------------------------------------------------------ nota para o atendente
def context_note(t: Triage, ticket_id: str | None, when: str, open_now: bool, contacts: OuvidoriaMulher) -> str:
    """Nota privada (OVM-21): só o que foi informado; processo marcado como informado e não verificado."""
    process = t.label("processo")
    if t.numero_processo:
        process += f" (nº {t.numero_processo}, informado pela pessoa, não verificado)"
    lines = [
        "ATENDIMENTO VIRTUAL — OUVIDORIA DA MULHER",
        *(["⚠️ NÃO RESPONDER POR ESTE WHATSAPP: a pessoa informou que o contato por aqui não é seguro. Pedido registrado SEM RETORNO por "
           "este canal; ela foi orientada a ligar ou escrever para a Ouvidoria."] if t.no_return else []),
        "Urgência: " + ("PERIGO IMEDIATO INFORMADO. Verificar 190 e 180 com a pessoa; este canal não é de emergência." if t.urgent
                       else t.label("perigo")),
        f"Para quem: {t.label('para_quem')}",
        f"Assunto: {t.label('assunto')}",
        f"Onde ou com quem: {t.label('ambiente')}",
        f"Processo ou medida protetiva: {process}",
        f"Contato por este WhatsApp: {t.label('contato')}",
        f"Data e horário: {when} (Belém)" + ("" if open_now else f" — fora do horário da Ouvidoria ({contacts.hours_text})"),
    ]
    if ticket_id:
        lines.append(f"Número de atendimento: {ticket_id} (interno; não é protocolo judicial)")
    lines.append("A pessoa foi orientada a guardar o protocolo para eventual atendimento presencial.")
    lines += ["", "Relato (texto integral):", t.relato] if t.relato else ["", "Relato: não incluído."]
    return "\n".join(lines)
