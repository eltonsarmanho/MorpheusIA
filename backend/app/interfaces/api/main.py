"""API FastAPI: webhook do Chatwoot, console de teste, curadoria e saúde."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.application.answering.orchestrator import QuestionError
from app.application.curation.service import CurationService
from app.config import REPO_ROOT, Settings, get_settings
from app.container import Container, build_container
from app.domain.models import KnowledgeDomain, ResponseKind, ReviewState

log = logging.getLogger(__name__)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")
    message: str = Field(min_length=1, max_length=1000)


class ReviewIn(BaseModel):
    decision: ReviewState
    reviewer: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=500)


class ResumeIn(BaseModel):
    actor: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=500)


class CollectIn(BaseModel):
    domain: KnowledgeDomain
    url: str = Field(max_length=2000)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {"container": container}

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if settings.inactivity_close_hours > 0:
            async def sweep() -> None:
                while True:
                    await asyncio.sleep(settings.inactivity_check_minutes * 60)
                    try:
                        c = get_container()
                        if c.handler is not None:
                            closed = await asyncio.to_thread(c.handler.close_inactive, settings.inactivity_close_hours)
                            if closed:
                                log.info("protocolos encerrados por inatividade: %s", closed)
                    except Exception:  # noqa: BLE001 - o varredor nunca pode derrubar a API
                        log.exception("falha no encerramento por inatividade")

            task = asyncio.create_task(sweep())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Atendimento TJPA - piloto", version="0.1.0", lifespan=lifespan)

    def get_container() -> Container:
        if state["container"] is None:
            state["container"] = build_container(settings)
        return state["container"]

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = settings.admin_api_token
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not expected or not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="token administrativo ausente ou inválido")

    # ------------------------------------------------------------------ saúde
    @app.get("/health")
    def health() -> dict:
        c = get_container()
        return {
            "status": "ok", "indexed_chunks": c.store.indexed_chunk_count(), "llm": c.llm is not None,
            "chatwoot_gateway": c.gateway is not None, "embedding_model": c.embedder.name,
        }

    # ---------------------------------------------------------------- Chatwoot
    @app.post("/webhooks/chatwoot")
    async def chatwoot_webhook(request: Request, background: BackgroundTasks, token: str = Query(default="")) -> dict:
        if not settings.chatwoot_webhook_secret or not hmac.compare_digest(token, settings.chatwoot_webhook_secret):
            raise HTTPException(status_code=401, detail="segredo do webhook inválido")
        c = get_container()
        if c.handler is None:
            raise HTTPException(status_code=503, detail="integração com o Chatwoot não configurada")
        payload = await request.json()
        kind, event_key = c.handler.claim(payload)
        if kind in ("duplicate", "ignored"):
            return {"status": kind}
        background.add_task(c.handler.handle, payload, event_key)  # responde 200 logo; o LLM pode demorar mais que o timeout do Chatwoot
        return {"status": "accepted"}

    # ------------------------------------------------------- console de teste
    def console_guard(authorization: Annotated[str | None, Header()] = None) -> None:
        if not settings.console_public:
            require_admin(authorization)  # evita que o console público gaste a cota do LLM

    @app.post("/api/chat", dependencies=[Depends(console_guard)])
    def chat(body: ChatIn) -> dict:
        """Console de teste do orquestrador. Não é um canal integrado: não faz transferência real."""
        c = get_container()
        key = f"console:{body.session_id}"
        st = c.ops.get(key)
        t0 = time.perf_counter()
        try:
            turn = c.orchestrator.respond(body.message, st)
        except QuestionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        reply = turn.reply
        text = reply.text
        if reply.kind is ResponseKind.HANDOFF:
            text = (f"Encaminhamento solicitado para a equipe {turn.handoff_team}. Este console de teste não está ligado ao Chatwoot, "
                    "então nenhuma transferência real foi feita.")
            turn.state.offer_pending = False
        c.ops.save(turn.state)
        return {
            "kind": reply.kind.value, "text": text, "domain": reply.domain.value if reply.domain else None,
            "intent": reply.intent.value if reply.intent else None, "abstain_reason": reply.abstain_reason,
            "handoff_team": turn.handoff_team, "labels": turn.labels, "latency_ms": int((time.perf_counter() - t0) * 1000),
            "citations": [c_.__dict__ for c_ in reply.citations], "trace": reply.trace,
        }

    # --------------------------------------------------------------- curadoria
    router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

    @router.get("/stats")
    def stats() -> dict:
        c = get_container()
        return {"documentos_por_estado": c.store.count_by_state(), "trechos_indexados": c.store.indexed_chunk_count(),
                "processos": c.store.known_process_numbers()}

    @router.get("/documents")
    def documents(state: ReviewState | None = None, domain: KnowledgeDomain | None = None, process: str | None = None,
                  limit: int = Query(100, le=500), offset: int = 0) -> list[dict]:
        c = get_container()
        docs = c.store.list_documents(domain=domain, state=state, limit=limit, offset=offset, process_number=process)
        return [
            {"doc_id": d.doc_id, "domain": d.domain.value, "title": d.title, "doc_type": d.doc_type, "process_number": d.process_number,
             "doc_date": d.doc_date, "state": d.review_state.value, "access": d.access_class.value, "reason": d.review_reason,
             "pages": [d.page_start, d.page_end], "source_file": d.source_file, "source_url": d.source_url, "pii": d.pii_counts,
             "issuing_body": d.issuing_body, "collected_at": d.collected_at}
            for d in docs
        ]

    @router.post("/documents/{doc_id}/review")
    def review(doc_id: str, body: ReviewIn) -> dict:
        c = get_container()
        if body.decision not in (ReviewState.APPROVED, ReviewState.REJECTED, ReviewState.PENDING_REVIEW):
            raise HTTPException(422, "decisão deve ser approved, rejected ou pending_review")
        try:
            indexed = CurationService(c.store, c.embedder).review(doc_id, body.decision, reviewer=body.reviewer, reason=body.reason)
        except KeyError as exc:
            raise HTTPException(404, "documento não encontrado") from exc
        return {"doc_id": doc_id, "state": body.decision.value, "chunks_indexed": indexed}

    @router.get("/audit")
    def audit(conversation: str | None = None, limit: int = Query(100, le=500)) -> list[dict]:
        return get_container().ops.audit_rows(conversation, limit)

    @router.post("/conversations/{account_id}/{conversation_id}/resume")
    def resume(account_id: int, conversation_id: int, body: ResumeIn) -> dict:
        c = get_container()
        if c.handler is None:
            raise HTTPException(503, "integração com o Chatwoot não configurada")
        from app.domain.handoff import InvalidTransition

        try:
            st = c.handler.resume_automation(account_id, conversation_id, body.actor, body.reason)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"state": st.handoff_state.value}

    @router.post("/conversations/{account_id}/{conversation_id}/retry-handoff")
    def retry_handoff(account_id: int, conversation_id: int, body: ResumeIn) -> dict:
        c = get_container()
        if c.handler is None:
            raise HTTPException(503, "integração com o Chatwoot não configurada")
        from app.domain.handoff import InvalidTransition

        try:
            res = c.handler.retry_handoff(account_id, conversation_id, body.actor, body.reason)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"outcome": res.outcome, "detail": res.detail}

    @router.get("/tickets")
    def tickets(status: str | None = Query(None, pattern="^(open|closed)$"), limit: int = Query(100, le=500)) -> list[dict]:
        return [t.__dict__ for t in get_container().ops.list_tickets(status, limit)]

    @router.get("/integration")
    def integration() -> dict:
        """Dados para cadastrar o webhook no painel do Chatwoot (Configurações > Integrações > Webhooks ou Bots)."""
        secret = settings.chatwoot_webhook_secret
        return {
            "webhook_url_publica": f"{settings.public_base_url}/webhooks/chatwoot?token={secret}",
            "webhook_url_interna": f"http://tjpa_backend:8300/webhooks/chatwoot?token={secret}",
            "eventos": ["message_created", "conversation_resolved", "conversation_opened"],
            "metodo": "POST (JSON)",
            "onde_cadastrar": "Chatwoot > Configurações > Integrações > Webhooks (URL pública) ou Configurações > Bots (URL interna)",
            "comandos_do_atendente": {"/encerrar": "nota privada que encerra o atendimento e marca a conversa como Resolvida"},
            "observacao": "Eventos repetidos são ignorados pelo id da mensagem; usar Webhook e Bot ao mesmo tempo não duplica respostas.",
        }

    @router.post("/collect")
    def collect(body: CollectIn) -> dict:
        from app.application.collection.service import CollectionError
        from app.container import build_collection

        c = get_container()
        try:
            record = build_collection(settings, c.store).collect_url(body.domain, body.url)
        except CollectionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return record

    app.include_router(router)

    # ------------------------------------------------------------ frontend
    frontend = REPO_ROOT / "frontend"
    if frontend.exists():
        app.mount("/console", StaticFiles(directory=frontend, html=True), name="console")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("erro não tratado")
        return JSONResponse({"detail": "erro interno"}, status_code=500)

    return app


app = create_app()
