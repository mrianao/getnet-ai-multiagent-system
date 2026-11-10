"""Deterministic grounding checks for generated answers.

What this module CAN establish deterministically:
  * citation validity — every `[n]` marker points to an evidence chunk that exists;
  * numeric support   — every number / amount / date in a claim appears in the evidence
                        (or in the question). A number that is not in the evidence is
                        unsupported by construction: this is the strongest signal we have
                        against fabricated prices, limits, deadlines and contradictions;
  * semantic proximity — the best cosine similarity between a claim and any evidence line,
                        using the same local multilingual embedder as retrieval.

What it CANNOT establish: logical entailment. Embedding similarity is a proxy, not proof, so
`weak_support` is reported as a flag and is never, on its own, a reason to delete text. Only
deterministic failures (unsupported numbers, invalid citations) are acted upon by the runtime
policy in `app/agents/knowledge.py`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Callable, Sequence

import numpy as np

EmbedFn = Callable[[list[str]], list[list[float]]]

_CITE = re.compile(r"\[(\d{1,2})\]")
_MD = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*•]\s+|^\s*\d+[.)]\s+)", re.M)
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9\"'(])|\n+")
# boilerplate that carries no factual claim about the evidence
_NON_FACTUAL = re.compile(
    r"(simulated demo data|contact (getnet|the getnet|our)|central de relacionamento|could not find|couldn't find|"
    r"não encontrei|nao encontrei|let me know|feel free|i hope|espero ter|if you have (any )?(other|further|more) questions|"
    r"se precisar|happy to help|posso ajudar|please note that this|for more information)", re.I)
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_MONTHS |= {m: i for i, m in enumerate(
    "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro".split(), 1)}
_STOP = set("""a o e de da do das dos em no na nos nas um uma para por com que se ao aos the of and to in is for on with
what how do does can my i you your it this that are be as at or an from by its will""".split())


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _iso_dates(text: str) -> set[str]:
    out = set()
    for d, m, y in re.findall(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        out.add(f"{y}-{int(m):02d}-{int(d):02d}")
    for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        out.add(f"{y}-{m}-{d}")
    low = fold(text)
    for mon, d, y in re.findall(r"\b([a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b", low):
        if mon in _MONTHS:
            out.add(f"{y}-{_MONTHS[mon]:02d}-{int(d):02d}")
    for d, mon, y in re.findall(r"\b(\d{1,2}) (?:de )?([a-z]+)(?: de)? (\d{4})\b", low):
        if mon in _MONTHS:
            out.add(f"{y}-{_MONTHS[mon]:02d}-{int(d):02d}")
    return out


def numbers(text: str) -> set[str]:
    """Normalised numeric facts: dates → ISO, '79,90'/'79.90' → '79.90', '20mil' → '20', '48x' → '48'."""
    t = _CITE.sub(" ", text)
    dates = _iso_dates(t)
    t = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", " ", t)
    months = "|".join(_MONTHS)
    t = fold(t)
    t = re.sub(rf"\b({months}) \d{{1,2}}(st|nd|rd|th)?,? \d{{4}}\b|\b\d{{1,2}} (de )?({months})( de)? \d{{4}}\b", " ", t)
    out = set(dates)
    for raw in re.findall(r"\d+(?:[.,]\d+)*", t):
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):          # 20.000 (pt thousands)
            n = raw.replace(".", "")
        elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):         # 20,000 (en thousands)
            n = raw.replace(",", "")
        else:
            n = raw.replace(",", ".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    # "20 mil" / "20mil" ≡ 20000
    for k in re.findall(r"(\d+)\s*mil\b", fold(text)):
        out.add(str(int(k) * 1000))
    return {n for n in out if n}


def _numbers_supported(claim_nums: set[str], evidence_nums: set[str]) -> set[str]:
    missing = set()
    for n in claim_nums:
        if n in evidence_nums:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n):
            missing.add(n)
            continue
        # tolerate trivial small integers used as list numbering / generic counts ("2 options")
        if re.fullmatch(r"[0-9]", n):
            continue
        if n.isdigit() and str(int(n) * 1000) in evidence_nums:  # "20" when evidence says "20 mil"
            continue
        if n.isdigit() and len(n) >= 4 and str(int(n) // 1000) in evidence_nums and int(n) % 1000 == 0:
            continue
        missing.add(n)
    return missing


def split_claims(answer: str) -> list[str]:
    text = _MD.sub("", answer)
    parts = [p.strip(" \t-–•:") for p in _SPLIT.split(text)]
    return [p for p in parts if len(p) >= 12]


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", fold(_CITE.sub(" ", text))) if len(w) > 3 and w not in _STOP}


def is_factual(claim: str) -> bool:
    if _NON_FACTUAL.search(claim):
        return False
    return bool(numbers(claim)) or len(_content_words(claim)) >= 4


@dataclass
class ClaimCheck:
    text: str
    factual: bool
    citations: list[int] = field(default_factory=list)
    invalid_citations: list[int] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    best_similarity: float | None = None
    supported: bool | None = None  # None = not a factual claim


@dataclass
class GroundingReport:
    claims: list[ClaimCheck]
    factual_claims: int
    supported_claims: int
    ratio: float | None                 # supported / factual (None when no factual claims)
    invalid_citations: int
    unsupported_number_claims: int
    weak_support_claims: int
    cited_claims: int
    verdict: str                        # grounded | weak | ungrounded | no_claims
    sim_threshold: float

    def to_dict(self, include_claims: bool = True) -> dict:
        d = asdict(self)
        if not include_claims:
            d.pop("claims")
        return d


def _evidence_lines(evidence: Sequence[dict]) -> list[str]:
    lines = []
    for e in evidence:
        for ln in re.split(r"\n| \| ", e.get("text", "")):
            ln = ln.strip()
            if len(ln) >= 12:
                lines.append(ln)
    return lines


def check_grounding(answer: str, evidence: Sequence[dict], question: str = "", embed: EmbedFn | None = None,
                    sim_threshold: float = 0.5) -> GroundingReport:
    """Check an answer against the exact evidence chunks that were given to the generator."""
    claims = split_claims(answer)
    ev_text = "\n".join(e.get("text", "") for e in evidence)
    ev_nums = numbers(ev_text) | numbers(question)
    lines = _evidence_lines(evidence)
    ev_vecs = None
    if embed and lines:
        ev_vecs = np.asarray(embed(lines))
        ev_vecs = ev_vecs / (np.linalg.norm(ev_vecs, axis=1, keepdims=True) + 1e-9)
    factual_idx = [i for i, c in enumerate(claims) if is_factual(c)]
    sims: dict[int, float] = {}
    if ev_vecs is not None and factual_idx:
        cv = np.asarray(embed([_CITE.sub("", claims[i]) for i in factual_idx]))
        cv = cv / (np.linalg.norm(cv, axis=1, keepdims=True) + 1e-9)
        best = (cv @ ev_vecs.T).max(axis=1)
        sims = {i: float(round(s, 4)) for i, s in zip(factual_idx, best)}
    ev_words = _content_words(ev_text)
    out: list[ClaimCheck] = []
    for i, c in enumerate(claims):
        cites = [int(n) for n in _CITE.findall(c)]
        invalid = [n for n in cites if n < 1 or n > len(evidence)]
        chk = ClaimCheck(text=c, factual=i in factual_idx, citations=cites, invalid_citations=invalid)
        if chk.factual:
            chk.unsupported_numbers = sorted(_numbers_supported(numbers(c), ev_nums))
            if i in sims:
                chk.best_similarity = sims[i]
                semantic_ok = sims[i] >= sim_threshold
            else:  # no embedder: lexical fallback
                cw = _content_words(c)
                semantic_ok = bool(cw) and len(cw & ev_words) / len(cw) >= 0.3
            chk.supported = not chk.unsupported_numbers and semantic_ok
        out.append(chk)
    factual = [c for c in out if c.factual]
    supported = sum(1 for c in factual if c.supported)
    ratio = round(supported / len(factual), 4) if factual else None
    unsupported_num = sum(1 for c in factual if c.unsupported_numbers)
    weak = sum(1 for c in factual if not c.unsupported_numbers and not c.supported)
    if ratio is None:
        verdict = "no_claims"
    elif unsupported_num == 0 and ratio >= 0.75:
        verdict = "grounded"
    elif ratio >= 0.5:
        verdict = "weak"
    else:
        verdict = "ungrounded"
    return GroundingReport(claims=out, factual_claims=len(factual), supported_claims=supported, ratio=ratio,
                           invalid_citations=sum(len(c.invalid_citations) for c in out),
                           unsupported_number_claims=unsupported_num, weak_support_claims=weak,
                           cited_claims=sum(1 for c in factual if c.citations), verdict=verdict,
                           sim_threshold=sim_threshold)
