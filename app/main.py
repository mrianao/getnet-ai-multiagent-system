"""FastAPI application: POST /chat, GET /health."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.container import Container, build_container
from app.models.schemas import ChatRequest, ChatResponse
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
    async def chat(req: ChatRequest, request: Request) -> ChatResponse:
        return await request.app.state.container.router.handle(req.message, req.user_id)

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
