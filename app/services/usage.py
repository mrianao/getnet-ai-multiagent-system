"""Per-request LLM usage accounting (contextvars: isolated per asyncio request task)."""
from __future__ import annotations

from contextvars import ContextVar

_calls: ContextVar[list[dict] | None] = ContextVar("llm_calls", default=None)


def start() -> None:
    _calls.set([])


def record(provider: str, model: str, tokens_in: int | None, tokens_out: int | None, latency_ms: int, ok: bool = True) -> None:
    calls = _calls.get()
    if calls is not None:
        calls.append({"provider": provider, "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                      "latency_ms": latency_ms, "ok": ok})


def mark() -> int:
    calls = _calls.get()
    return len(calls) if calls is not None else 0


def totals(since: int = 0) -> dict:
    calls = (_calls.get() or [])[since:]
    known_in = [c["tokens_in"] for c in calls if c["tokens_in"] is not None]
    known_out = [c["tokens_out"] for c in calls if c["tokens_out"] is not None]
    return {"llm_calls": len(calls),
            "tokens_in": sum(known_in) if known_in else None,     # None = provider did not report usage
            "tokens_out": sum(known_out) if known_out else None,
            "llm_latency_ms": sum(c["latency_ms"] for c in calls)}
