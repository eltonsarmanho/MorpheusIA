from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.storage.models import Lead, Message
from app.storage.models import engine as default_engine


@dataclass
class ContactInfo:
    """Optional contact channel gathered during the conversation."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None


class LeadRepository:
    """Persistence for `Lead` records - one row per `session_id` (upserted)."""

    def __init__(self, engine=default_engine):
        self._engine = engine

    async def upsert_lead(
        self,
        session_id: str,
        category: str,
        need_summary: str,
        contact: ContactInfo | None = None,
    ) -> Lead:
        contact = contact or ContactInfo()
        has_contact = bool(contact.phone or contact.email)
        now = datetime.now(timezone.utc)

        with Session(self._engine) as session:
            existing = session.exec(
                select(Lead).where(Lead.session_id == session_id)
            ).first()

            if existing is None:
                lead = Lead(
                    session_id=session_id,
                    category=category,
                    need_summary=need_summary,
                    contact_name=contact.name,
                    contact_phone=contact.phone,
                    contact_email=contact.email,
                    has_contact=has_contact,
                    created_at=now,
                    updated_at=now,
                )
            else:
                lead = existing
                lead.category = category
                lead.need_summary = need_summary
                lead.contact_name = contact.name
                lead.contact_phone = contact.phone
                lead.contact_email = contact.email
                lead.has_contact = has_contact
                lead.updated_at = now

            session.add(lead)
            session.commit()
            session.refresh(lead)
            return lead

    async def list_leads(self) -> list[Lead]:
        with Session(self._engine) as session:
            return list(
                session.exec(select(Lead).order_by(Lead.updated_at.desc())).all()
            )


class MessageRepository:
    """Persistence for per-session chat transcripts."""

    def __init__(self, engine=default_engine):
        self._engine = engine

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        with Session(self._engine) as session:
            message = Message(
                session_id=session_id,
                role=role,
                content=content,
                created_at=datetime.now(timezone.utc),
            )
            session.add(message)
            session.commit()

    async def get_history(self, session_id: str) -> list[Message]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.asc())
                ).all()
            )
