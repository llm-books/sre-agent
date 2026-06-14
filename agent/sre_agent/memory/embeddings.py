"""Embeddings for the memory store.

The default `LocalHashEmbedder` is deterministic and needs no API key, so the
memory demo runs offline just like the scripted planner. It hashes tokens into a
fixed-dimension vector and L2-normalizes, so symptom texts that share words land
near each other under cosine similarity. It is not a semantic model, it is enough
to demonstrate similarity search.

For a real deployment, swap in a proper embedding model (OpenAI, Voyage, a local
sentence-transformer) behind the same Embedder interface, and a real vector
database. The book treats both as replaceable; the memory logic above this layer
does not change.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

DIM = 64


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalHashEmbedder:
    def __init__(self, dim: int = DIM):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in _tokens(text):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # both are L2-normalized


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]
