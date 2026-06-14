"""Input guardrails: the probabilistic first layer.

Tool results carry external content (log lines contain customer-controlled
fields, and customers include attackers), so they are untrusted. These guardrails
scan that content for injection patterns, redact what they catch, and mark the
rest as data, not instructions. They will sometimes miss a cleverer payload,
which is exactly why they are not the only defense: permission scoping and action
gates contain whatever slips through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns that signal an attempt to turn data into instructions. Not exhaustive
# (no pattern list is); the deterministic layers are what hold against the rest.
PATTERNS = {
    "instruction_override": r"ignore (all )?(previous|prior|the above)|disregard (the|all|previous)|forget (the|all|previous)",
    "role_hijack": r"you are now|new instructions|system prompt|act as|pretend to be",
    "destructive": r"\b(delete|drop|truncate|rm -rf|kubectl delete)\b",
    "exfiltration": r"\b(email|send|exfiltrate|leak)\b.{0,40}\b(to|@|list|customers?)\b",
    "remote_fetch": r"\b(curl|wget)\b|https?://",
    "secret_grab": r"\b(password|secret|api[_ ]?key|token|credential)s?\b",
}

_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in PATTERNS.items()}


@dataclass
class GuardResult:
    flags: list[str]          # which injection patterns matched
    clean: str                # content with matched spans redacted
    marked: str               # the clean content wrapped as untrusted data

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def scan(text: str) -> list[str]:
    return [name for name, rx in _COMPILED.items() if rx.search(text or "")]


def neutralize(text: str) -> str:
    """Redact spans that match injection patterns, so they can't read as
    instructions even if the marking is ignored."""
    out = text or ""
    for rx in _COMPILED.values():
        out = rx.sub("[redacted: possible injection]", out)
    return out


def mark_untrusted(text: str) -> str:
    """Wrap content so a model is told explicitly that it is data to analyze, not
    instructions to follow."""
    return f"<untrusted_data>\n{text}\n</untrusted_data>"


def sanitize(text: str) -> GuardResult:
    flags = scan(text)
    clean = neutralize(text)
    return GuardResult(flags=flags, clean=clean, marked=mark_untrusted(clean))
