from __future__ import annotations

import secrets
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.core.config import Settings
from app.storage.models import Lead
from app.storage.repository import LeadRepository

router = APIRouter(prefix="/api", tags=["leads"])


class LeadsResponse(BaseModel):
    leads: list[Lead]


@lru_cache(maxsize=1)
def get_admin_token() -> str:
    return Settings().ADMIN_API_TOKEN.get_secret_value()


@lru_cache(maxsize=1)
def get_lead_repository() -> LeadRepository:
    return LeadRepository()


def require_admin_token(
    authorization: str | None = Header(default=None),
    admin_token: str = Depends(get_admin_token),
) -> None:
    """Reject the request unless it carries `Authorization: Bearer <token>`."""
    expected = f"Bearer {admin_token}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Não autorizado")


@router.get(
    "/leads",
    response_model=LeadsResponse,
    dependencies=[Depends(require_admin_token)],
)
async def list_leads(
    lead_repo: LeadRepository = Depends(get_lead_repository),
) -> LeadsResponse:
    return LeadsResponse(leads=await lead_repo.list_leads())
