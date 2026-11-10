"""Render baseline-vs-final comparison tables from eval result files (no numbers typed by hand).

    python -m eval.report --baseline eval/results/baseline/results.json \
        --final eval/results/final-run1/results.json \
        --retrieval-baseline eval/results/baseline/retrieval_offline.json \
        --retrieval-final eval/results/final-run1/retrieval_offline.json > eval/results/comparison.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _rate(x):
    if isinstance(x, dict):
        v = x.get("rate", x.get("value"))
        return "n/a" if v is None else f"{v:.2f} (n={x.get('n')})"
    return "n/a" if x is None else f"{x:.2f}" if isinstance(x, float) else str(x)


def online_table(b: dict, f: dict) -> str:
    rows = [("HTTP status as expected (incl. 403 cross-tenant)", "http_expectation"), ("Routing accuracy", "routing_accuracy"),
            ("Agents match", "agents_match"), ("Tools recall", "tools_recall"),
            ("Expected status (blocked / smalltalk / escalated)", "status_expectation"),
            ("No unwanted ticket", "no_unwanted_ticket"), ("Ticket when requested", "ticket_when_requested"),
            ("Required-fact recall (mean)", "fact_recall_mean"), ("All required facts present", "facts_all_present"),
            ("Forbidden-claim free", "forbidden_claim_free")]
    out = ["| Metric | Baseline (original code) | Final (v2) |", "|---|---|---|"]
    for label, k in rows:
        out.append(f"| {label} | {_rate(b.get(k))} | {_rate(f.get(k))} |")
    for label, k in [("hit@1", "hit@1"), ("hit@k", "hit@k"), ("MRR", "mrr"), ("precision@k", "precision@k"),
                     ("forbidden-URL rate", "forbidden_url_rate")]:
        out.append(f"| Retrieval {label} (online, n={b['retrieval']['n']} / {f['retrieval']['n']}) | "
                   f"{_rate(b['retrieval'][k])} | {_rate(f['retrieval'][k])} |")
    out.append(f"| RAG answers with ≥1 citation | {_rate(b['citations']['answers_with_citation'])} | {_rate(f['citations']['answers_with_citation'])} |")
    out.append(f"| RAG answers with all citations valid | {_rate(b['citations']['all_citations_valid'])} | {_rate(f['citations']['all_citations_valid'])} |")
    out.append(f"| Groundedness ratio (mean, proxy) | {_rate(b['groundedness']['mean_ratio'])} | {_rate(f['groundedness']['mean_ratio'])} |")
    out.append(f"| 'grounded' verdict rate | {_rate(b['groundedness']['grounded_verdict_rate'])} | {_rate(f['groundedness']['grounded_verdict_rate'])} |")
    out.append(f"| RAG answers with unsupported numbers (final answer) | {b['groundedness']['answers_with_unsupported_numbers']} | {f['groundedness']['answers_with_unsupported_numbers']} |")
    out.append(f"| Evidence source for groundedness | {', '.join(b['groundedness']['evidence_sources'])} | {', '.join(f['groundedness']['evidence_sources'])} |")
    for q in ("p50", "p95", "mean"):
        out.append(f"| Latency {q} (s) ⚠ see note | {_rate(b['latency_s'][q])} | {_rate(f['latency_s'][q])} |")
    return "\n".join(out)


def per_turn(bres: dict, fres: dict) -> str:
    def idx(res):
        return {(e["case_id"], e["turn"]): e for e in res["runs"][0]["evals"]}
    bi, fi = idx(bres), idx(fres)
    recs = {(r["case_id"], r["turn"]): r for r in fres["runs"][0]["records"]}
    brecs = {(r["case_id"], r["turn"]): r for r in bres["runs"][0]["records"]}
    out = ["| Case | Turn message | Baseline intent → final intent | Baseline checks | Final checks |", "|---|---|---|---|---|"]

    def checks(e):
        keys = [k for k in e if k.endswith("_ok") or k in ("no_unwanted_ticket", "ticket_created", "facts_all")]
        bad = [k for k in keys if e[k] is False]
        return "✅" if not bad else "❌ " + ", ".join(bad)

    def intent(r):
        d = r.get("response") or {}
        return ((d.get("metadata") or {}).get("route") or {}).get("intent") or f"HTTP {r['http_status']}"
    for key in fi:
        msg = recs[key]["message"][:60].replace("|", "/")
        out.append(f"| {key[0]}#{key[1]} | {msg} | {intent(brecs[key])} → {intent(recs[key])} | {checks(bi[key])} | {checks(fi[key])} |")
    return "\n".join(out)


def retrieval_table(b: dict, f: dict) -> str:
    bb = next(iter(b["results"].values()))
    ff = next(iter(f["results"].values()))
    out = ["| Offline retrieval (12 queries, real embedder) | Baseline (k=5) | Final (k=4, dedupe, lexical gate 0.10) |", "|---|---|---|"]
    for k in ("hit@1", "hit@k", "mrr", "precision@k", "forbidden_url_rate", "dup_rate"):
        out.append(f"| {k} | {bb[k]} | {ff[k]} |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--retrieval-baseline")
    ap.add_argument("--retrieval-final")
    a = ap.parse_args()
    b, f = json.loads(Path(a.baseline).read_text()), json.loads(Path(a.final).read_text())
    print("## Online evaluation (real model, HTTP)\n")
    print(online_table(b["runs"][0]["summary"], f["runs"][0]["summary"]))
    if a.retrieval_baseline and a.retrieval_final:
        print("\n## Offline retrieval\n")
        print(retrieval_table(json.loads(Path(a.retrieval_baseline).read_text()), json.loads(Path(a.retrieval_final).read_text())))
    print("\n## Per-turn checks\n")
    print(per_turn(b, f))


if __name__ == "__main__":
    main()
