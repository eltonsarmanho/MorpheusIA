from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.llm.client import LLMClient, LLMUnavailableError, ToolCall
from app.llm.tools import SAVE_LEAD_INFO_TOOL, build_system_prompt
from app.storage.repository import ContactInfo, LeadRepository, MessageRepository

logger = logging.getLogger(__name__)

# Canned PT-BR reply used when the LLM provider is unreachable (spec AC
# CHAT-09): apology plus the WhatsApp handoff, never a stuck conversation.
FALLBACK_REPLY = (
    "Desculpe, estou com uma instabilidade técnica e não consegui responder "
    "agora. Você pode falar direto com a equipe da Morpheus IA pelo WhatsApp."
)

# The provider returns either a text reply or a tool call, so a turn that
# extracts the lead usually carries no text of its own.
LEAD_SAVED_REPLY = (
    "Perfeito, registrei a sua necessidade! Se preferir, é só continuar o "
    "atendimento com a nossa equipe pelo WhatsApp."
)

# Used when a tool call could not be turned into a lead and the provider sent
# no text either - the turn degrades to plain conversation and extraction is
# retried on the next turn (spec Edge Cases).
NO_TEXT_REPLY = "Certo! Pode me contar um pouco mais sobre o que você precisa?"

_WHATSAPP_FALLBACK_TEXT = (
    "Olá! Vim pelo site da Morpheus IA e gostaria de falar com a equipe."
)


@dataclass
class TurnResult:
    """The outcome of one chat turn, as returned to the API layer."""

    reply: str
    lead_captured: bool
    whatsapp_url: str | None = None


def _parse_lead_arguments(tool_call: ToolCall | None) -> dict[str, Any] | None:
    """Decode a `save_lead_info` call's arguments, or None if unusable.

    Malformed JSON or missing required fields yield None so the caller can
    treat the turn as plain conversational text instead of crashing.
    """
    if tool_call is None:
        return None
    try:
        arguments = json.loads(tool_call.arguments)
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None
    for required in ("category", "need_summary"):
        value = arguments.get(required)
        if not isinstance(value, str) or not value:
            return None
    return arguments


class ConversationService:
    """Orchestrates a single chat turn end-to-end."""

    def __init__(
        self,
        llm: LLMClient,
        lead_repo: LeadRepository,
        message_repo: MessageRepository,
        whatsapp_number: str,
    ) -> None:
        self._llm = llm
        self._lead_repo = lead_repo
        self._message_repo = message_repo
        self._whatsapp_number = whatsapp_number

    async def handle_turn(self, session_id: str, user_message: str) -> TurnResult:
        history = await self._message_repo.get_history(session_id)

        # Contact is asked at most once per session (AC CHAT-05). Derived from
        # persisted state rather than the model's own memory: the ask
        # instruction only goes out while the session has no assistant turn yet.
        contact_already_asked = any(message.role == "assistant" for message in history)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(contact_already_asked)}
        ]
        messages.extend(
            {"role": message.role, "content": message.content} for message in history
        )
        messages.append({"role": "user", "content": user_message})

        await self._message_repo.append_message(session_id, "user", user_message)

        try:
            result = await self._llm.complete(messages, [SAVE_LEAD_INFO_TOOL])
        except LLMUnavailableError as exc:
            logger.warning(
                "chat turn fell back to canned reply: session_id=%s error=%s",
                session_id,
                exc,
            )
            await self._message_repo.append_message(
                session_id, "assistant", FALLBACK_REPLY
            )
            return TurnResult(
                reply=FALLBACK_REPLY,
                lead_captured=False,
                whatsapp_url=self._whatsapp_url(_WHATSAPP_FALLBACK_TEXT),
            )

        lead_fields = _parse_lead_arguments(result.tool_call)

        if lead_fields is None:
            if result.tool_call is not None:
                logger.warning(
                    "malformed save_lead_info arguments, treating turn as plain "
                    "text: session_id=%s",
                    session_id,
                )
            reply = result.text or NO_TEXT_REPLY
            await self._message_repo.append_message(session_id, "assistant", reply)
            return TurnResult(reply=reply, lead_captured=False)

        reply = result.text or LEAD_SAVED_REPLY
        try:
            lead = await self._lead_repo.upsert_lead(
                session_id=session_id,
                category=lead_fields["category"],
                need_summary=lead_fields["need_summary"],
                contact=ContactInfo(
                    name=lead_fields.get("contact_name"),
                    phone=lead_fields.get("contact_phone"),
                    email=lead_fields.get("contact_email"),
                ),
            )
        except Exception as exc:
            # The visitor keeps their reply even if the lead write fails
            # (design.md Error Handling Strategy); the failure is logged.
            logger.error(
                "lead persistence failed: session_id=%s error=%s", session_id, exc
            )
            await self._message_repo.append_message(session_id, "assistant", reply)
            return TurnResult(reply=reply, lead_captured=False)

        await self._message_repo.append_message(session_id, "assistant", reply)
        return TurnResult(
            reply=reply,
            lead_captured=True,
            whatsapp_url=self._whatsapp_url(
                "Olá! Vim pelo site da Morpheus IA. Resumo do meu interesse: "
                f"{lead.need_summary}"
            ),
        )

    def _whatsapp_url(self, text: str) -> str:
        return f"https://wa.me/{self._whatsapp_number}?text={quote(text)}"
