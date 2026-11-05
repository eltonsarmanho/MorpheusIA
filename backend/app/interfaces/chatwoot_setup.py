"""Preparação idempotente do Chatwoot: equipes, etiquetas, Agent Bot e vínculo com a inbox (ETAPA 7).

Sem --apply apenas mostra o plano (dry-run). Nunca remove nem renomeia recursos existentes e não duplica nomes
já presentes (comparação sem diferenciar maiúsculas; o Chatwoot grava nomes de equipes em minúsculas).

  python -m app.interfaces.chatwoot_setup                      # plano
  python -m app.interfaces.chatwoot_setup --apply --webhook-url http://tjpa_backend:8300/webhooks/chatwoot
"""

from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.infrastructure.chatwoot.client import ChatwootClient, ChatwootError

TEAMS = {
    "Triagem e Orquestração": "Recebe o que o assistente não conseguiu classificar e acompanha falhas do bot.",
    "Informações Processuais": "Dúvidas sobre documentos de processos do acervo de demonstração.",
    "Informações Institucionais": "Balcão Virtual, unidades, contatos e serviços do tribunal.",
    "Informações Jurídico-Institucionais": "Conceitos e procedimentos jurídicos gerais documentados.",
    "Atendimento Humano Geral": "Pedidos de atendimento humano sem equipe específica.",
    "secretaria": "Roteiro TJPA: diligências, providências e atendimento humano da unidade (padrão).",
    "gabinete": "Roteiro TJPA: diligências dirigidas ao juiz ou urgentes.",
    "ouvidoria_mulher": "Ouvidoria da Mulher: pedidos triados pela Júlia (equipe da Ouvidoria; sem caixa de entrada própria).",
}
LABELS = {
    "ia_orquestrador": ("Conversa tratada pelo orquestrador do assistente.", "#1F93FF"),
    "ia_rag": ("Resposta gerada com recuperação de documentos (RAG).", "#2BB673"),
    "ia_falha": ("Falha técnica do assistente ou da transferência; requer atenção humana.", "#E5484D"),
    "humano": ("Conversa encaminhada para atendimento humano.", "#F5A623"),
    "consulta_processual": ("Intenção: consulta sobre documentos processuais.", "#7B61FF"),
    "duvida_institucional": ("Intenção: dúvida institucional ou jurídica geral.", "#00A3A3"),
    "ouvidoria_mulher": ("Serviço Ouvidoria da Mulher (triagem pela Júlia).", "#B03060"),
    "urgencia_imediata": ("Ouvidoria da Mulher: a pessoa informou perigo imediato; conferir 190 e 180.", "#C0392B"),
}
# Atributos de conversa da Ouvidoria da Mulher: só enumerações; relato e número do processo ficam na nota privada.
ATTRIBUTES = {
    "servico": "Serviço", "assunto": "Assunto (Ouvidoria da Mulher)", "ambiente": "Ambiente (Ouvidoria da Mulher)",
    "urgencia": "Urgência (Ouvidoria da Mulher)", "para_quem": "Para quem (Ouvidoria da Mulher)",
}
BOT_NAME = "Assistente Virtual TJPA (piloto)"


def plan(c: ChatwootClient, webhook_url: str | None, inbox_id: int | None) -> dict:
    teams = {t["name"].casefold(): t for t in c.list_teams()}
    labels = {l["title"].casefold() for l in c.list_labels()}
    try:
        attributes: set | None = {a.get("attribute_key") for a in c.list_conversation_attribute_definitions()}
    except ChatwootError:  # versão sem essa API: o plano declara e segue sem os atributos
        attributes = None
    bots = c.list_agent_bots()
    inboxes = c.list_inboxes()
    bot = next((b for b in bots if b["name"] == BOT_NAME), None)
    return {
        "teams_to_create": [n for n in TEAMS if n.casefold() not in teams],
        "teams_existing": [t["name"] for t in teams.values()],
        "labels_to_create": [n for n in LABELS if n.casefold() not in labels],
        "attributes_to_create": [k for k in ATTRIBUTES if k not in attributes] if attributes is not None else [],
        "attributes_status": "ok" if attributes is not None else "indisponível nesta versão do Chatwoot (atributos não serão criados)",
        "inboxes": [(i["id"], i["name"], i["channel_type"]) for i in inboxes],
        "agent_bot": "existente" if bot else ("a criar" if webhook_url else "não criado (informe --webhook-url)"),
        "inbox_binding": inbox_id if (webhook_url and inbox_id) else None,
    }


def apply(c: ChatwootClient, webhook_url: str | None, inbox_id: int | None) -> dict:
    result: dict = {"created_teams": [], "created_labels": [], "created_attributes": [], "agent_bot": None}
    p = plan(c, webhook_url, inbox_id)
    for name in p["teams_to_create"]:
        c.create_team(name, TEAMS[name])
        result["created_teams"].append(name)
    for name in p["labels_to_create"]:
        desc, color = LABELS[name]
        c.create_label(name, desc, color)
        result["created_labels"].append(name)
    for key in p["attributes_to_create"]:
        c.create_conversation_attribute_definition(key, ATTRIBUTES[key])
        result["created_attributes"].append(key)
    if webhook_url:
        bot = next((b for b in c.list_agent_bots() if b["name"] == BOT_NAME), None)
        if bot is None:
            bot = c.create_agent_bot(BOT_NAME, webhook_url, "Assistente do piloto: responde com base em fontes aprovadas e encaminha a humanos.")
            result["agent_bot"] = {"id": bot["id"], "created": True}
        else:
            result["agent_bot"] = {"id": bot["id"], "created": False}
            if bot.get("outgoing_url") != webhook_url:
                c.update_agent_bot(bot["id"], webhook_url)
                result["agent_bot"]["url_updated"] = True
        if inbox_id:
            c.set_inbox_agent_bot(inbox_id, bot["id"])
            result["inbox_bound"] = inbox_id
        if bot.get("access_token"):
            result["bot_access_token"] = bot["access_token"]  # exibido uma vez; guarde em CHATWOOT_BOT_TOKEN
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--webhook-url", help="URL pública do webhook do backend, sem o ?token=")
    ap.add_argument("--inbox-id", type=int, help="inbox que passa a ser atendida pelo bot")
    a = ap.parse_args()
    s = get_settings()
    c = ChatwootClient(s.chatwoot_base_url, s.chatwoot_account_id, api_token=s.chatwoot_api_token)
    url = f"{a.webhook_url}?token={s.chatwoot_webhook_secret}" if a.webhook_url else None
    out = apply(c, url, a.inbox_id) if a.apply else plan(c, url, a.inbox_id)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
