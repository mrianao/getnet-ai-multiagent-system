"""LIVE validation (not part of the mocked CI suite).

Runs the 10 PDF scenarios through the real stack: real LLM (Ollama by default), real
embeddings + ChromaDB index, real public APIs. Writes a Markdown report.

    python -m scripts.live_check [--out docs/live-validation.md] [--base-url http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import httpx

SCENARIOS = [
    "What's the difference between the Get Clássica and the Get Smart?",
    "What's the weather forecast in Porto Alegre tomorrow?",
    "When will the money from yesterday's sales be deposited?",
    "Do I need a bank account to receive my sales via Pix?",
    "My card machine won't connect to the internet, what should I do?",
    "How does receivables advance (antecipação) work with Getnet?",
    "What's the euro exchange rate today?",
    "My card machine is showing a transaction decline error.",
    "How many installments can I split a sale into with the crediário?",
    "Can I sell through WhatsApp using the Payment Link?",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--out", default="docs/live-validation.md")
    ap.add_argument("--user-id", default="cliente1988")
    ap.add_argument("--token", default=None, help="merchant bearer token (python -m scripts.issue_token ...)")
    args = ap.parse_args()
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    health = httpx.get(f"{args.base_url}/health", timeout=10).json()
    lines = [
        "# Live validation report (real LLM + real RAG + real APIs)",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- LLM: `{health['llm']['provider']}` / `{health['llm']['model']}` (reachable: {health['llm']['reachable']})",
        f"- Vector store: {health['vector_store']['chunks']} chunks",
        "- Hardware: see README *Performance* (this run's latency reflects the machine it ran on)",
        "",
        "| # | Message | Route (method) | Agents | Tools | Status | Latency |",
        "|---|---------|----------------|--------|-------|--------|---------|",
    ]
    details = []
    for i, msg in enumerate(SCENARIOS, 1):
        t = time.perf_counter()
        r = httpx.post(f"{args.base_url}/chat", json={"message": msg, "user_id": args.user_id}, headers=headers, timeout=600)
        dt = time.perf_counter() - t
        d = r.json()
        m = d["metadata"]
        tools = sorted({tool for s in m.get("steps", []) for tool in s["tools"]})
        lines.append(f"| {i} | {msg} | {m['route']['intent']} ({m['route']['method']}) | {', '.join(d['agents_invoked'])} | "
                     f"{', '.join(tools)} | {d['status']} | {dt:.1f}s |")
        src = "\n".join(f"  - [{s['type']}] {s['title']} — {s['url']}" + (f" (score {s['score']})" if s.get("score") else "")
                        for s in d["sources"]) or "  - (none)"
        details.append(f"### {i}. {msg}\n\n**Answer** ({d['status']}, primary agent `{d['agent']}`):\n\n"
                       f"> " + d["answer"].replace("\n", "\n> ") + f"\n\n**Sources:**\n{src}\n")
        print(f"[{i}/10] {d['status']:8s} {m['route']['intent']:17s} {dt:5.1f}s  {msg}", flush=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n## Answers\n\n" + "\n".join(details))
    print(f"report written to {args.out}")


if __name__ == "__main__":
    main()
