"""FastAPI application: POST /chat, GET /health."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings
from app.container import Container, build_container
from app.models.schemas import ChatRequest, ChatResponse
from app.security.auth import Principal, get_principal, require_merchant, require_operator
from app.services.guardrails import check_input
from app.state.tickets import TicketConflict, TicketNotFound

STATIC = Path(__file__).resolve().parent / "static"
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
          "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class ResolveRequest(BaseModel):
    decision: Literal["approve_draft", "reply", "reject"]
    reply: str | None = Field(None, max_length=2000)
from app.services.observability import setup_logging

logger = logging.getLogger("api")


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        setup_logging(settings.log_level)
        app.state.container = container or build_container(settings)
        yield
        await app.state.container.http.aclose()

    app = FastAPI(title="Getnet Multi-Agent Support System", version="1.0.0", lifespan=lifespan,
                  description="Router → Knowledge (RAG + web tools) / Customer Support (tools) / Escalation → Personality")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = UI_CSP
            response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    app.mount("/ui", StaticFiles(directory=STATIC, html=True), name="ui")

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, request: Request, principal: Principal = Depends(require_merchant)) -> ChatResponse:
        return await request.app.state.container.chat.handle(req, principal)

    @app.get("/me")
    async def me(principal: Principal = Depends(get_principal)) -> dict:
        return {"subject": principal.subject, "role": principal.role, "merchant_id": principal.merchant_id,
                "token_id": principal.token_id}

    @app.get("/conversations")
    async def list_conversations(request: Request, principal: Principal = Depends(require_merchant)) -> dict:
        return {"conversations": request.app.state.container.conversations.list(principal.merchant_id)}

    @app.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request,
                               principal: Principal = Depends(require_merchant)) -> dict:
        store = request.app.state.container.conversations
        conv = store.get(conversation_id, principal.merchant_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"conversation": conv, "messages": store.messages(conversation_id)}

    @app.get("/health")
    async def health(request: Request) -> dict:
        c: Container = request.app.state.container
        llm_ok = await c.llm.ping() if hasattr(c.llm, "ping") else True
        return {"status": "ok", "llm": {"provider": c.llm.provider, "model": c.llm.model, "reachable": llm_ok},
                "vector_store": {"collection": c.settings.chroma_collection, "chunks": c.store.count()}}

    # ---------------- operator (human-in-the-loop) ----------------
    @app.get("/operator/tickets")
    async def list_tickets(request: Request, status: str | None = Query(None, pattern="^(open|claimed|resolved|rejected)$"),
                           _: Principal = Depends(require_operator)) -> dict:
        return {"tickets": request.app.state.container.tickets.list(status)}

    @app.get("/operator/tickets/{ticket_id}")
    async def get_ticket(ticket_id: str, request: Request, _: Principal = Depends(require_operator)) -> dict:
        t = request.app.state.container.tickets.get(ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return t

    @app.post("/operator/tickets/{ticket_id}/claim")
    async def claim_ticket(ticket_id: str, request: Request, principal: Principal = Depends(require_operator),
                           idempotency_key: str | None = Header(None, max_length=100)) -> dict:
        try:
            return request.app.state.container.tickets.claim(ticket_id, principal.subject, idempotency_key)
        except TicketNotFound:
            raise HTTPException(status_code=404, detail="Ticket not found")
        except TicketConflict as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/operator/tickets/{ticket_id}/resolve")
    async def resolve_ticket(ticket_id: str, body: ResolveRequest, request: Request,
                             principal: Principal = Depends(require_operator),
                             idempotency_key: str | None = Header(None, max_length=100)) -> dict:
        reply = check_input(body.reply).sanitized if body.reply else None  # mask PII before persistence
        try:
            return request.app.state.container.tickets.resolve(ticket_id, principal.subject, body.decision, reply,
                                                               idempotency_key)
        except TicketNotFound:
            raise HTTPException(status_code=404, detail="Ticket not found")
        except TicketConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal server error"})

    return app


app = create_app()
