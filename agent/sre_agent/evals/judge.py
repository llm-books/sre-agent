"""Judging open-ended outcomes.

Only the diagnosis-equivalence dimension genuinely needs a judge; safety and
efficiency are deterministic checks done elsewhere. Don't judge what you can
assert.

The default EmbeddingJudge is deterministic and offline: it scores equivalence by
cosine similarity between the agent's diagnosis and the reference, using the same
local embedder as the memory store. It is crude compared to a real LLM judge, so
it is VALIDATED against human labels before it is trusted, exactly as the chapter
insists. Swap in LLMJudge (Anthropic) for production accuracy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

import re

from ..memory.embeddings import LocalHashEmbedder, cosine

_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "is", "are", "was", "were", "it",
    "its", "this", "that", "with", "for", "on", "in", "no", "not", "has", "have",
    "had", "by", "as", "which", "than", "then", "but", "so", "we", "you", "i",
}


def _content_tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOP}


def _dice(a: str, b: str) -> float:
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


@dataclass
class Verdict:
    equivalent: bool
    score: float
    rationale: str = ""


class Judge(Protocol):
    def judge(self, candidate: str, reference: str) -> Verdict: ...


class EmbeddingJudge:
    """Offline judge. Scores equivalence by the stronger of two crude signals:
    cosine over a local embedding, and Dice overlap of content words. The blend
    is more robust to paraphrase than either alone, which is what lets it clear
    the validation bar. It is still no LLM judge; swap in LLMJudge for accuracy."""

    def __init__(self, threshold: float = 0.35):
        self.threshold = threshold
        self._embedder = LocalHashEmbedder()

    def judge(self, candidate: str, reference: str) -> Verdict:
        cos = cosine(self._embedder.embed(candidate or ""), self._embedder.embed(reference or ""))
        dice = _dice(candidate, reference)
        s = max(cos, dice)
        return Verdict(equivalent=s >= self.threshold, score=round(s, 3),
                       rationale=f"max(cos {cos:.2f}, dice {dice:.2f}) vs {self.threshold}")


class LLMJudge:
    """Judge with a real model. Optional; needs ANTHROPIC_API_KEY."""

    SYSTEM = (
        "You score whether an agent's incident diagnosis is EQUIVALENT in meaning "
        "to a reference diagnosis (same root cause), not identical in wording. "
        'Reply ONLY with JSON: {"equivalent": true|false, "rationale": "..."}.'
    )

    def __init__(self, model: str = "claude-sonnet-4-5"):
        import anthropic  # raises if missing

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("LLMJudge needs ANTHROPIC_API_KEY set")
        self._client = anthropic.Anthropic()
        self._model = model

    def judge(self, candidate: str, reference: str) -> Verdict:
        msg = self._client.messages.create(
            model=self._model, max_tokens=256, system=self.SYSTEM,
            messages=[{"role": "user", "content":
                       f"Reference:\n{reference}\n\nAgent diagnosis:\n{candidate}"}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        data = json.loads(text)
        return Verdict(equivalent=bool(data["equivalent"]), score=1.0 if data["equivalent"] else 0.0,
                       rationale=data.get("rationale", ""))


# A small human-labeled set to validate a judge before trusting its numbers.
# (candidate, reference, human_says_equivalent)
LABELED: list[tuple[str, str, bool]] = [
    ("orders has a slow database query and no recent deploy",
     "the orders service has a slow query, not a code change", True),
    ("the payment provider got slow and the timeout is too aggressive",
     "provider latency rose; slow successes are treated as failures by an aggressive timeout", True),
    ("the api-gateway config push broke authentication",
     "a recent config push to the api-gateway introduced auth flakiness", True),
    ("restart everything and hope it clears",
     "the inventory service has a memory leak that cascades to other services", False),
    ("the database is completely down",
     "the orders service has a slow query, not an outage", False),
]


def validate_judge(judge: Judge, labeled: list[tuple[str, str, bool]] | None = None) -> float:
    """Return the judge's agreement rate with the human labels. A judge you have
    not validated produces numbers that look like data and are not."""
    rows = labeled if labeled is not None else LABELED
    if not rows:
        return 1.0
    agree = sum(1 for cand, ref, human in rows if judge.judge(cand, ref).equivalent == human)
    return round(agree / len(rows), 3)


def default_judge() -> Judge:
    if os.environ.get("AGENT_JUDGE", "embedding").lower() == "llm":
        return LLMJudge()
    return EmbeddingJudge()
