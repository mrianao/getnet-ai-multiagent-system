"""Metric functions shared by the online (HTTP) and offline (retrieval) evaluators."""
from __future__ import annotations

import re
import statistics

from app.rag.grounding import fold

_CITE = re.compile(r"\[(\d{1,2})\]")


def norm_url(u: str) -> str:
    return u.rstrip("/").lower()


def fact_match(answer: str, groups: list[list[str]]) -> tuple[int, int, list[list[str]]]:
    """Returns (matched_groups, total_groups, missing_groups)."""
    a = fold(answer)
    missing = []
    for g in groups:
        ok = False
        for alt in g:
            if alt.startswith("re:"):
                ok = re.search(alt[3:], a) is not None
            else:
                ok = fold(alt) in a
            if ok:
                break
        if not ok:
            missing.append(g)
    return len(groups) - len(missing), len(groups), missing


def forbidden_hits(answer: str, forbidden: list[str]) -> list[str]:
    a = fold(answer)
    return [f for f in forbidden if fold(f) in a]


def citation_validity(answer: str, n_evidence: int) -> dict:
    cites = [int(c) for c in _CITE.findall(answer)]
    invalid = [c for c in cites if c < 1 or c > n_evidence]
    return {"citations": len(cites), "invalid": len(invalid), "has_citation": bool(cites),
            "valid": bool(cites) and not invalid}


def retrieval_metrics(ranked_urls: list[str], expected: list[str], forbidden: list[str] | None = None) -> dict:
    exp = {norm_url(u) for u in expected}
    forb = {norm_url(u) for u in (forbidden or [])}
    ranked = [norm_url(u) for u in ranked_urls]
    first = next((i for i, u in enumerate(ranked, 1) if u in exp), None)
    return {
        "k": len(ranked),
        "hit@1": bool(ranked) and ranked[0] in exp,
        "hit@k": first is not None,
        "rr": 1.0 / first if first else 0.0,
        "precision@k": (sum(u in exp for u in ranked) / len(ranked)) if ranked else 0.0,
        "forbidden_hits": sum(u in forb for u in ranked),
    }


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 3)


def mean(values: list[float]) -> float | None:
    vals = [x for x in values if x is not None]
    return round(statistics.fmean(vals), 4) if vals else None
