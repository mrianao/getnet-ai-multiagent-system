"""Composition root: wires settings → clients → agents → router (dependency injection by hand)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.agents.customer_support import CustomerSupportAgent
from app.agents.escalation import EscalationAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.personality import Personality
from app.agents.router import RouterAgent
from app.config import Settings
from app.models.schemas import AgentName
from app.rag.ingest import index_pages, load_snapshot
from app.rag.store import Embedder, Retriever, VectorStore, build_vector_store
from app.security.auth import TokenStore
from app.services.chat import ChatService
from app.services.llm import LLMClient, build_llm
from app.state.conversations import ConversationStore
from app.state.db import Database
from app.state.tickets import TicketStore
from app.state.traces import TraceStore
from app.tools.customer import CUSTOMERS
from app.tools.web import USER_AGENT, WebTools

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    llm: LLMClient
    store: VectorStore
    http: httpx.AsyncClient
    router: RouterAgent
    db: Database
    tokens: TokenStore
    conversations: ConversationStore
    chat: ChatService
    tickets: TicketStore
    traces: TraceStore


def build_container(settings: Settings, *, llm: LLMClient | None = None, embedder: Embedder | None = None,
                    http: httpx.AsyncClient | None = None, store: VectorStore | None = None,
                    db: Database | None = None) -> Container:
    llm = llm or build_llm(settings)
    store = store or build_vector_store(settings, embedder)
    if settings.auto_ingest and store.count() == 0:
        pages = load_snapshot(settings.corpus_path)
        n = index_pages(pages, store, settings)
        logger.info("auto-ingest: indexed %d chunks from %d pages", n, len(pages))
    http = http or httpx.AsyncClient(timeout=settings.http_timeout_s, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT})
    web = WebTools(http, settings.tavily_api_key)
    retriever = Retriever(store, settings.rag_top_k, settings.rag_min_score, dedupe=settings.rag_dedupe,
                          lexical_gate=settings.rag_lexical_gate)
    db = db or Database(settings.state_db_path)
    conversations = ConversationStore(db)
    tickets = TicketStore(db)
    agents = {
        AgentName.KNOWLEDGE: KnowledgeAgent(llm, retriever, web, sim_threshold=settings.grounding_sim_threshold),
        AgentName.CUSTOMER_SUPPORT: CustomerSupportAgent(llm),
        AgentName.ESCALATION: EscalationAgent(tickets, conversations),
    }
    router = RouterAgent(llm, agents, Personality(llm), replan_budget_s=settings.replan_budget_s)
    tokens = TokenStore(db, known_merchants=set(CUSTOMERS))
    traces = TraceStore(db)
    chat = ChatService(router, conversations, settings.history_window, tickets, traces)
    return Container(settings, llm, store, http, router, db, tokens, conversations, chat, tickets, traces)
