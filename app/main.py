"""FastAPI application: POST /chat, GET /health."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.container import Container, build_container
from app.models.schemas import ChatRequest, ChatResponse
from app.security.auth import Principal, get_principal, require_merchant
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

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": "Internal server error"})

    return app


app = create_app()
