from __future__ import annotations

# Category labels mirror the four existing solution cards in
# data/content-pt.json (solutions.card1..4), copied here per design.md
# rather than read at runtime, plus "Outro" for anything that doesn't fit.
CATEGORY_LABELS: dict[str, str] = {
    "gestao_empresas": "Gestão Inteligente de Empresas",
    "whatsapp_atendimento": "Atendimento WhatsApp com IA",
    "analise_documentos": "Análise de Documentos Técnicos",
    "gerador_conteudo": "Gerador de Conteúdos",
    "outro": "Outro",
}

# save_lead_info function-calling schema, in the shape MariTalk's
# OpenAI-compatible Responses API expects for a `tools` entry: a flat
# {type, name, description, parameters} object (verified in design.md).
SAVE_LEAD_INFO_TOOL: dict = {
    "type": "function",
    "name": "save_lead_info",
    "description": (
        "Registra a necessidade do visitante, a categoria de solução da "
        "Morpheus IA correspondente, e o contato (se fornecido)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": list(CATEGORY_LABELS.keys()),
                "description": "Categoria de solução da Morpheus IA que melhor atende a necessidade.",
            },
            "need_summary": {
                "type": "string",
                "description": "Resumo curto, em português, da necessidade descrita pelo visitante.",
            },
            "contact_name": {
                "type": "string",
                "description": "Nome do visitante, se informado.",
            },
            "contact_phone": {
                "type": "string",
                "description": "Telefone do visitante, se informado.",
            },
            "contact_email": {
                "type": "string",
                "description": "E-mail do visitante, se informado.",
            },
        },
        "required": ["category", "need_summary"],
    },
}

_CONTACT_ASK_INSTRUCTION = (
    "Se você ainda não perguntou nesta conversa, pergunte UMA única vez, de "
    "forma natural, se a pessoa quer deixar um telefone ou e-mail para "
    "retorno. Não pergunte de novo se a pessoa já respondeu ou já ignorou o "
    "pedido."
)

_LGPD_CONSENT_LINE = (
    "Ao continuar, você concorda com o uso dos seus dados para retorno "
    "comercial da Morpheus IA."
)


def build_system_prompt(contact_already_asked: bool) -> str:
    """Build the chatbot's system prompt for the current turn.

    `contact_already_asked` tracks whether the assistant has already asked
    this session for a contact channel; when True, the "ask once" instruction
    is omitted so the assistant does not ask again (spec AC CHAT-05).
    """
    categories = ", ".join(CATEGORY_LABELS.values())
    lines = [
        "Você é o assistente virtual da Morpheus IA, uma empresa de soluções "
        "automatizadas com inteligência artificial.",
        "Responda sempre em português do Brasil, de forma cordial e objetiva.",
        "Seu objetivo é entender a necessidade da pessoa e classificá-la em "
        f"uma das categorias de solução da Morpheus IA: {categories}.",
        _LGPD_CONSENT_LINE,
    ]
    if not contact_already_asked:
        lines.append(_CONTACT_ASK_INSTRUCTION)
    lines.append(
        "Quando tiver informações suficientes sobre a necessidade da pessoa, "
        "use a ferramenta save_lead_info para registrar a categoria e um "
        "resumo da necessidade."
    )
    return "\n".join(lines)
