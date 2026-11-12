"""Persisted request traces + aggregate metrics (Prometheus text exposition, no extra dependency)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.state.db import Database


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


class TraceStore:
    def __init__(self, db: Database):
        self.db = db

    def record(self, resp, conversation_id: str | None, merchant_id: str | None) -> None:
        m = resp.metadata or {}
        route, use = m.get("route") or {}, m.get("usage") or {}
        g = (m.get("grounding") or {}).get("knowledge") or {}
        detail = {"steps": m.get("steps"), "replans": m.get("replans"), "conversation": m.get("conversation"),
                  "grounding": m.get("grounding"), "guardrails": m.get("guardrails"),
                  "evidence_refs": [{k: e.get(k) for k in ("type", "agent", "chunk_id", "url", "rank", "score", "tool",
                                                           "ticket_id")} for e in m.get("evidence") or []]}
        with self.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO request_traces (trace_id, conversation_id, merchant_id, created_at, status, "
                      "intent, route_method, agents, latency_ms, llm_calls, tokens_in, tokens_out, escalated, "
                      "grounding_ratio, grounding_verdict, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (m.get("trace_id") or f"held-{datetime.now(timezone.utc).timestamp()}", conversation_id, merchant_id,
                       datetime.now(timezone.utc).isoformat(), resp.status, route.get("intent"), route.get("method"),
                       ",".join(a.value if hasattr(a, "value") else str(a) for a in resp.agents_invoked),
                       int(m.get("latency_ms") or 0), use.get("llm_calls"), use.get("tokens_in"), use.get("tokens_out"),
                       int(resp.status == "escalated"), g.get("ratio"), g.get("verdict"),
                       json.dumps(detail, ensure_ascii=False, default=str)))

    def get(self, trace_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM request_traces WHERE trace_id = ?", (trace_id,))
        if r and r.get("detail_json"):
            r["detail"] = json.loads(r.pop("detail_json"))
        return r

    def prometheus(self, window: int = 1000) -> str:
        rows = self.db.query("SELECT * FROM request_traces ORDER BY created_at DESC LIMIT ?", (window,))
        out: list[str] = []

        def metric(name: str, kind: str, help_: str, samples: list[tuple[str, float | None]]):
            out.append(f"# HELP {name} {help_}")
            out.append(f"# TYPE {name} {kind}")
            for labels, v in samples:
                if v is not None:
                    out.append(f"{name}{labels} {round(float(v), 4)}")

        def count(field: str) -> dict[str, int]:
            d: dict[str, int] = {}
            for r in rows:
                d[str(r[field])] = d.get(str(r[field]), 0) + 1
            return d

        metric("getnet_requests", "gauge", f"Requests in the last {window} traces by status",
               [(f'{{status="{k}"}}', v) for k, v in sorted(count("status").items())])
        metric("getnet_route_method", "gauge", "Routing decisions by method (rules = LLM fallback/deterministic)",
               [(f'{{method="{k}"}}', v) for k, v in sorted(count("route_method").items()) if k != "None"])
        metric("getnet_intents", "gauge", "Requests by intent",
               [(f'{{intent="{k}"}}', v) for k, v in sorted(count("intent").items()) if k != "None"])
        metric("getnet_escalations", "gauge", "Requests that escalated to a human",
               [("", sum(r["escalated"] for r in rows))])
        lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
        metric("getnet_request_latency_ms", "summary", "End-to-end request latency",
               [('{quantile="0.5"}', _pct(lat, .5)), ('{quantile="0.95"}', _pct(lat, .95)), ("_count", len(lat))])
        metric("getnet_llm_tokens", "gauge", "LLM tokens (only providers that report usage)",
               [('{direction="in"}', sum(r["tokens_in"] or 0 for r in rows)),
                ('{direction="out"}', sum(r["tokens_out"] or 0 for r in rows))])
        metric("getnet_llm_calls", "gauge", "LLM calls", [("", sum(r["llm_calls"] or 0 for r in rows))])
        ratios = [r["grounding_ratio"] for r in rows if r["grounding_ratio"] is not None]
        metric("getnet_grounding_ratio_avg", "gauge", "Mean share of RAG claims supported (proxy, see docs)",
               [("", sum(ratios) / len(ratios) if ratios else None)])
        metric("getnet_grounding_verdict", "gauge", "RAG answers by grounding verdict",
               [(f'{{verdict="{k}"}}', v) for k, v in sorted(count("grounding_verdict").items()) if k != "None"])
        open_t = self.db.one("SELECT COUNT(*) AS n FROM tickets WHERE status IN ('open','claimed')")
        metric("getnet_tickets_unresolved", "gauge", "Escalation tickets waiting for a human", [("", open_t["n"])])
        return "\n".join(out) + "\n"
