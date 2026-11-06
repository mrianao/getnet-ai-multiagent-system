"""Input guardrails (bonus): prompt-injection, unsafe content, PII masking.

Deliberately rule-based: deterministic, auditable and zero-latency. An LLM-based
classifier (e.g. Llama Guard) is listed as a future enhancement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (the )?(system|previous) (prompt|instructions)",
    r"(reveal|show|print) (your|the) (system prompt|instructions)",
    r"you are now (dan|developer mode)",
    r"ignore as instru[cç][oõ]es",
]
UNSAFE_PATTERNS = [
    r"\b(how to )?(make|build) (a )?(bomb|explosive|weapon)",
    r"\b(clone|skim|steal) (a )?(credit )?cards?\b",
    r"\bcarding\b",
    r"\b(launder|lavagem de dinheiro|money laundering)\b",
    r"\bbypass (the )?(fraud|antifraud|3ds|kyc)\b",
]
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")


@dataclass
class GuardrailResult:
    allowed: bool
    sanitized: str
    flags: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def pii_masked(self) -> bool:
        return any(f.startswith("pii:") for f in self.flags)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
        alt = not alt
    return total % 10 == 0


def check_input(message: str) -> GuardrailResult:
    low = message.lower()
    for p in INJECTION_PATTERNS:
        if re.search(p, low):
            return GuardrailResult(False, message, ["prompt_injection"], "Possible prompt-injection attempt.")
    for p in UNSAFE_PATTERNS:
        if re.search(p, low):
            return GuardrailResult(False, message, ["unsafe_content"], "Request involves unsafe or fraudulent activity.")

    flags: list[str] = []
    sanitized = message

    def _mask_card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            flags.append("pii:card")
            return f"[CARD ****{digits[-4:]}]"
        return m.group(0)

    sanitized = CARD_RE.sub(_mask_card, sanitized)
    if CNPJ_RE.search(sanitized):
        flags.append("pii:cnpj")
        sanitized = CNPJ_RE.sub("[CNPJ]", sanitized)
    if CPF_RE.search(sanitized):
        flags.append("pii:cpf")
        sanitized = CPF_RE.sub("[CPF]", sanitized)
    return GuardrailResult(True, sanitized, flags)
