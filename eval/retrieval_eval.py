"""Offline retrieval evaluation (no LLM): real corpus snapshot + real multilingual embedder.

    python -m eval.retrieval_eval                 # current Retriever configuration
    python -m eval.retrieval_eval --compare       # baseline vs candidate configurations

Queries: every dataset turn with `expected_urls` whose retrieval query is the user message
(single-agent product questions). Metrics: hit@1, hit@k, MRR, precision@k, forbidden-URL rate,
duplicate-content rate in the top-k.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.metrics import mean, retrieval_metrics
from eval.run_eval import load_dataset

ROOT = Path(__file__).resolve().parent.parent


def queries(dataset: list[dict]) -> list[dict]:
    out = []
    for c in dataset:
        for i, t in enumerate(c["turns"]):
            e = t["expect"]
            if "expected_urls" in e and e.get("intent") == "product_info" and i == 0:
                out.append({"id": f"{c['id']}#{i}", "query": t["message"], "expected": e["expected_urls"],
                            "forbidden": e.get("forbidden_urls", [])})
    return out


def build_store():
    import time

    from app.config import Settings
    from app.rag.ingest import index_pages, load_snapshot
    from app.rag.store import FastEmbedder, VectorStore

    s = Settings()
    store = VectorStore(None, f"reval_{int(time.time() * 1000)}", FastEmbedder(s.embedding_model, s.embedding_cache_dir))
    index_pages(load_snapshot(s.corpus_path), store, s)
    return store


def dup_rate(hits: list[dict]) -> float:
    from app.rag.hybrid import tokenize

    sets = [set(tokenize(h["text"])) for h in hits]
    dups = 0
    for i in range(len(sets)):
        for j in range(i):
            inter = len(sets[i] & sets[j])
            if inter and inter / min(len(sets[i]), len(sets[j])) >= 0.6:
                dups += 1
                break
    return dups / len(hits) if hits else 0.0


def evaluate(retriever, qs: list[dict]) -> dict:
    rows = []
    for q in qs:
        hits = retriever.retrieve(q["query"])
        m = retrieval_metrics([h["url"] for h in hits], q["expected"], q["forbidden"])
        m["dup_rate"] = dup_rate(hits)
        m["id"] = q["id"]
        m["urls"] = [h["url"].replace("https://", "") for h in hits]
        rows.append(m)
    return {"n": len(rows), "hit@1": mean([float(r["hit@1"]) for r in rows]), "hit@k": mean([float(r["hit@k"]) for r in rows]),
            "mrr": mean([r["rr"] for r in rows]), "precision@k": mean([r["precision@k"] for r in rows]),
            "forbidden_url_rate": mean([float(r["forbidden_hits"] > 0) for r in rows]),
            "dup_rate": mean([r["dup_rate"] for r in rows]), "mean_k": mean([float(r["k"]) for r in rows]), "rows": rows}


def main() -> None:
    from app.config import Settings
    from app.rag.store import Retriever

    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "eval" / "dataset.jsonl"))
    ap.add_argument("--configs", default=None, help="JSON list of {name, kwargs} Retriever configurations")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    s = Settings()
    qs = queries(load_dataset(Path(args.dataset)))
    store = build_store()
    configs = json.loads(args.configs) if args.configs else [
        {"name": "current", "kwargs": {"top_k": s.rag_top_k, "min_score": s.rag_min_score}}]
    results = {}
    for cfg in configs:
        r = evaluate(Retriever(store, **cfg["kwargs"]), qs)
        results[cfg["name"]] = r
        print(f"{cfg['name']:28s} n={r['n']} hit@1={r['hit@1']} hit@k={r['hit@k']} MRR={r['mrr']} "
              f"P@k={r['precision@k']} forbidden={r['forbidden_url_rate']} dup={r['dup_rate']} k={r['mean_k']}")
    if args.out:
        Path(args.out).write_text(json.dumps({"configs": configs, "results": results}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
