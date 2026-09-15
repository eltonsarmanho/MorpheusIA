from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, leads
from app.core.config import Settings
from app.storage.models import init_db


@asynccontextmanager
async def _lifespan(application: FastAPI):
    init_db()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the configured application: routers, CORS, and request logging."""
    settings = settings or Settings()

    # Basic structured logging so each chat request's outcome and session id
    # reach stdout (spec AC OBS-02).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    application = FastAPI(title="Morpheus IA Chatbot Backend", lifespan=_lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list(),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Body validation fails before the route handler runs, so the rejected
    # request is logged from an app-level handler instead.
    application.add_exception_handler(
        RequestValidationError, chat.validation_exception_handler
    )
    application.include_router(chat.router)
    application.include_router(leads.router)

    @application.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe for the container healthcheck and the reverse proxy."""
        return {"status": "ok"}

    return application


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Resolve `app.main:app` for uvicorn without building it at import time.

    Settings are read only when the app is actually requested, so importing
    this module does not require a fully populated .env.
    """
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
