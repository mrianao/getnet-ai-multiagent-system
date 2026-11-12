"""Online evaluation harness (real HTTP server, real LLM, real retrieval).

    python -m eval.run_eval --base-url http://localhost:8000 --token "$EVAL_TOKEN" --runs 1 \
        --label final --out-dir eval/results

* Works against both the original server (no auth, no evidence in responses) and the remediated
  one. When the server does not return `metadata.evidence`, evidence for single-agent RAG turns
  is reconstructed in-process with the same corpus snapshot, embedder and retriever settings
  (flagged `evidence_source: reconstructed`; never used for multi-agent turns, whose retrieval
  query includes upstream agent output and cannot be reproduced).
* Multi-turn cases send `conversation_id` from the previous response when the server returns it.
* Writes raw JSON (every answer, evidence, check) + a Markdown summary + a CSV sheet for the
  human review described in eval/HUMAN_REVIEW.md.

A passing evaluation is evidence about THIS dataset and THIS model run, not proof that every
generated answer is correct.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.rag.grounding import check_grounding
from eval.metrics import citation_validity, fact_match, forbidden_hits, mean, pct, retrieval_metrics

ROOT = Path(__file__).resolve().parent.parent


def load_dataset(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Reconstructor:
    """Rebuilds the evidence a legacy (evidence-less) server used for single-agent RAG answers."""

    def __init__(self, top_k: int, min_score: float):
        from app.config import Settings
        from app.rag.ingest import index_pages, load_snapshot
        from app.rag.store import FastEmbedder, Retriever, VectorStore

        s = Settings()
        self.embedder = FastEmbedder(s.embedding_model, s.embedding_cache_dir)
        store = VectorStore(None, f"eval_{int(time.time())}", self.embedder)
        index_pages(load_snapshot(s.corpus_path), store, s)
        self.retriever = Retriever(store, top_k, min_score)

    def evidence(self, query: str) -> list[dict]:
        return [{"chunk_id": h["id"], "url": h["url"], "text": h["text"], "score": h["score"]}
                for h in self.retriever.retrieve(query)]


def get_embedder():
    from app.config import Settings
    from app.rag.store import FastEmbedder

    s = Settings()
    return FastEmbedder(s.embedding_model, s.embedding_cache_dir)


def run_case(client: httpx.Client, case: dict, token: str | None) -> list[dict]:
    out, conversation_id = [], None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for ti, turn in enumerate(case["turns"]):
        body = {"message": turn["message"], "user_id": case.get("user_id_override") or case["user_id"]}
        if conversation_id:
            body["conversation_id"] = conversation_id
        t0 = time.perf_counter()
        r = client.post("/chat", json=body, headers=headers)
        latency = time.perf_counter() - t0
        rec = {"case_id": case["id"], "category": case["category"], "pdf_scenario": case["pdf_scenario"], "turn": ti,
               "message": turn["message"], "expect": turn["expect"], "http_status": r.status_code,
               "latency_s": round(latency, 2)}
        try:
            data = r.json()
        except ValueError:
            data = {}
        rec["response"] = data
        conversation_id = data.get("conversation_id") or conversation_id
        out.append(rec)
    return out


def evaluate_turn(rec: dict, recon: Reconstructor | None, embed) -> dict:
    exp, d = rec["expect"], rec["response"]
    meta = d.get("metadata", {}) if isinstance(d, dict) else {}
    answer = d.get("answer", "") if isinstance(d, dict) else ""
    ev: dict = {"case_id": rec["case_id"], "turn": rec["turn"]}
    ev["http_ok"] = rec["http_status"] == exp.get("http_status", 200)
    if rec["http_status"] != 200:
        ev["security_ok"] = ev["http_ok"] and not forbidden_hits(json.dumps(d, ensure_ascii=False), exp.get("forbidden", []))
        return ev
    route = meta.get("route") or {}
    intent = route.get("intent")
    if "intent" in exp:
        allowed = exp["intent"] if isinstance(exp["intent"], list) else [exp["intent"]]
        ev["intent_ok"] = intent in allowed
    if "agents" in exp:
        ev["agents_ok"] = [a for a in d.get("agents_invoked", []) if a != "escalation"] == exp["agents"]
    tools_used = sorted({t for s in meta.get("steps", []) for t in s.get("tools", [])})
    ev["tools_used"] = tools_used
    if "tools" in exp:
        ev["tools_ok"] = set(exp["tools"]) <= set(tools_used)
    if "status_in" in exp:
        ev["status_ok"] = d.get("status") in exp["status_in"]
    ticket = bool(meta.get("handoff_ticket"))
    if exp.get("no_ticket"):
        ev["no_unwanted_ticket"] = not ticket
    if exp.get("ticket"):
        ev["ticket_created"] = ticket
    if "facts" in exp:
        m, n, missing = fact_match(answer, exp["facts"])
        ev["fact_recall"], ev["facts_all"], ev["facts_missing"] = m / n, m == n, missing
    forb = forbidden_hits(answer, exp.get("forbidden", []))
    ev["forbidden_found"] = forb
    ev["forbidden_ok"] = not forb

    # ---- evidence: from the response if exposed, else reconstructed for single-agent RAG ----
    evidence = [e for e in meta.get("evidence", []) if e.get("type", "rag") == "rag"]
    ev_source = "response" if meta.get("evidence") is not None else None
    knowledge_rag = intent in ("product_info", "troubleshooting") and "rag_retrieve" in tools_used
    if not evidence and ev_source is None and recon and knowledge_rag and d.get("agents_invoked") == ["knowledge"]:
        evidence, ev_source = recon.evidence(rec["message"]), "reconstructed"
    ev["evidence_source"] = ev_source
    if evidence and "expected_urls" in exp:
        ev["retrieval"] = retrieval_metrics([e["url"] for e in evidence], exp["expected_urls"], exp.get("forbidden_urls"))
    if evidence and knowledge_rag and d.get("agents_invoked") == ["knowledge"]:
        rep = check_grounding(answer, evidence, rec["message"], embed=embed)
        ev["grounding"] = rep.to_dict(include_claims=True)
        ev["citations"] = citation_validity(answer, len(evidence))
    if meta.get("grounding"):
        ev["runtime_grounding"] = meta["grounding"]
    ev["latency_s"] = rec["latency_s"]
    ev["tokens"] = meta.get("usage")
    return ev


def summarize(evals: list[dict]) -> dict:
    def rate(key):
        vals = [e[key] for e in evals if key in e]
        return {"rate": round(sum(map(bool, vals)) / len(vals), 4) if vals else None, "n": len(vals)}

    retr = [e["retrieval"] for e in evals if "retrieval" in e]
    grd = [e["grounding"] for e in evals if "grounding" in e]
    cit = [e["citations"] for e in evals if "citations" in e]
    lat = [e["latency_s"] for e in evals if "latency_s" in e]
    return {
        "turns": len(evals),
        "http_expectation": rate("http_ok"),
        "routing_accuracy": rate("intent_ok"),
        "agents_match": rate("agents_ok"),
        "tools_recall": rate("tools_ok"),
        "status_expectation": rate("status_ok"),
        "no_unwanted_ticket": rate("no_unwanted_ticket"),
        "ticket_when_requested": rate("ticket_created"),
        "fact_recall_mean": {"value": mean([e["fact_recall"] for e in evals if "fact_recall" in e]),
                             "n": sum("fact_recall" in e for e in evals)},
        "facts_all_present": rate("facts_all"),
        "forbidden_claim_free": rate("forbidden_ok"),
        "retrieval": {"n": len(retr), "hit@1": mean([float(r["hit@1"]) for r in retr]),
                      "hit@k": mean([float(r["hit@k"]) for r in retr]), "mrr": mean([r["rr"] for r in retr]),
                      "precision@k": mean([r["precision@k"] for r in retr]),
                      "forbidden_url_rate": mean([float(r["forbidden_hits"] > 0) for r in retr])},
        "citations": {"n": len(cit), "answers_with_citation": mean([float(c["has_citation"]) for c in cit]),
                      "all_citations_valid": mean([float(c["valid"]) for c in cit]),
                      "invalid_citation_total": sum(c["invalid"] for c in cit)},
        "groundedness": {"n": len(grd), "mean_ratio": mean([g["ratio"] for g in grd]),
                         "grounded_verdict_rate": mean([float(g["verdict"] == "grounded") for g in grd]),
                         "answers_with_unsupported_numbers": sum(g["unsupported_number_claims"] > 0 for g in grd),
                         "evidence_sources": sorted({e.get("evidence_source") or "-" for e in evals if "grounding" in e})},
        "latency_s": {"mean": mean(lat), "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": max(lat) if lat else None},
    }


def git_info() -> dict:
    def g(*a):
        try:
            return subprocess.check_output(["git", *a], cwd=ROOT, text=True).strip()
        except Exception:
            return None
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(g("status", "--porcelain"))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--token", default=None, help="bearer token for cliente1988 (omit for the pre-auth server)")
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.jsonl"))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out-dir", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--server-commit", default=None, help="commit the SERVER runs (if different from this checkout)")
    ap.add_argument("--reconstruct", action="store_true", help="reconstruct evidence for evidence-less servers")
    ap.add_argument("--only", default=None, help="comma-separated case ids")
    args = ap.parse_args()

    cases = load_dataset(Path(args.dataset))
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    out_dir = Path(args.out_dir) / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    embed = get_embedder().embed
    recon = Reconstructor(top_k=5, min_score=0.25) if args.reconstruct else None

    with httpx.Client(base_url=args.base_url, timeout=900) as client:
        health = client.get("/health", headers={"Authorization": f"Bearer {args.token}"} if args.token else {}).json()
        runs = []
        for run_i in range(args.runs):
            recs, evals = [], []
            for c in cases:
                for rec in run_case(client, c, args.token):
                    recs.append(rec)
                    e = evaluate_turn(rec, recon, embed)
                    evals.append(e)
                    flag = "ok " if all(v for k, v in e.items() if k.endswith("_ok") and isinstance(v, bool)) else "ERR"
                    print(f"[run {run_i + 1}] {flag} {rec['case_id']}#{rec['turn']} http={rec['http_status']} "
                          f"intent={(rec['response'].get('metadata') or {}).get('route', {}).get('intent')} "
                          f"{rec['latency_s']}s", flush=True)
            runs.append({"records": recs, "evals": evals, "summary": summarize(evals)})

    meta = {"label": args.label, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "harness_git": git_info(), "server_commit": args.server_commit, "base_url": args.base_url,
            "llm": health.get("llm"), "vector_store": health.get("vector_store"),
            "dataset": Path(args.dataset).name, "dataset_version": cases[0].get("dataset_version"),
            "cases": len(cases), "turns_per_run": sum(len(c["turns"]) for c in cases), "runs": args.runs,
            "evidence_reconstruction": bool(recon)}
    (out_dir / "results.json").write_text(json.dumps({"meta": meta, "runs": runs}, ensure_ascii=False, indent=1))
    with (out_dir / "human_review.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run", "case_id", "turn", "message", "answer", "sources", "correctness_0_2", "groundedness_0_2",
                    "usefulness_0_2", "notes"])
        for ri, run in enumerate(runs, 1):
            for rec in run["records"]:
                d = rec["response"] if isinstance(rec["response"], dict) else {}
                w.writerow([ri, rec["case_id"], rec["turn"], rec["message"], d.get("answer", f"HTTP {rec['http_status']}"),
                            " ; ".join(s.get("url", "") for s in d.get("sources", [])), "", "", "", ""])
    print(json.dumps({"meta": meta, "summaries": [r["summary"] for r in runs]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
