from __future__ import annotations

import json
import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from app.api.rate_limit import RateLimiter
from app.core.config import Settings
from app.domain.conversation import FALLBACK_REPLY, ConversationService
from app.llm.maritalk_client import build_maritalk_client
from app.storage.repository import LeadRepository, MessageRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Spec AC CHAT-04: a message of 1000 characters or more is rejected, so the
# longest accepted message is 999 characters.
MAX_MESSAGE_LENGTH = 999


class ChatMessageRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ChatMessageResponse(BaseModel):
    reply: str
    lead_captured: bool
    whatsapp_url: str | None = None


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    settings = Settings()
    return RateLimiter(
        per_minute=settings.RATE_LIMIT_PER_MINUTE,
        per_session=settings.RATE_LIMIT_PER_SESSION,
    )


@lru_cache(maxsize=1)
def get_conversation_service() -> ConversationService:
    settings = Settings()
    return ConversationService(
        llm=build_maritalk_client(settings),
        lead_repo=LeadRepository(),
        message_repo=MessageRepository(),
        whatsapp_number=settings.WHATSAPP_NUMBER,
    )


def _session_id_from_body(raw_body: bytes) -> str:
    try:
        body = json.loads(raw_body)
    except ValueError:
        return "unknown"
    if isinstance(body, dict) and isinstance(body.get("session_id"), str):
        return body["session_id"]
    return "unknown"


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log a rejected request (OBS-02), then return FastAPI's standard 422.

    Registered on the app itself (see `app/main.py`) because request-body
    validation fails before the route handler runs.
    """
    logger.info(
        "request rejected: outcome=validation-error session_id=%s",
        _session_id_from_body(await request.body()),
    )
    return await request_validation_exception_handler(request, exc)


@router.post("/message", response_model=ChatMessageResponse)
async def post_message(
    payload: ChatMessageRequest,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    service: ConversationService = Depends(get_conversation_service),
) -> ChatMessageResponse:
    if not rate_limiter.check(payload.session_id):
        logger.info(
            "chat request rejected: outcome=rate-limited session_id=%s",
            payload.session_id,
        )
        raise HTTPException(
            status_code=429,
            detail="Muitas mensagens em pouco tempo. Aguarde um instante.",
        )

    result = await service.handle_turn(payload.session_id, payload.message)

    outcome = "upstream-fallback" if result.reply == FALLBACK_REPLY else "success"
    logger.info("chat request handled: outcome=%s session_id=%s", outcome, payload.session_id)

    return ChatMessageResponse(
        reply=result.reply,
        lead_captured=result.lead_captured,
        whatsapp_url=result.whatsapp_url,
    )
