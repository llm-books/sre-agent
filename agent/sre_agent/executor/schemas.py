"""Tiny response-shape schemas, dependency-free.

A schema is just a predicate over the upstream's response. The wrapper checks it
BEFORE any field is read, so a drifted response (a renamed field, a changed type,
a missing list) becomes a caught failure instead of plausible garbage. This is the
single most important line in the tool layer: validate, then read. Never read,
then hope.

For richer needs, swap in jsonschema or pydantic behind the same `.validates`
interface; the wrapper does not care.
"""
from __future__ import annotations

from typing import Callable


class Schema:
    def __init__(self, name: str, check: Callable[[object], bool]):
        self.name = name
        self._check = check

    def validates(self, resp: object) -> bool:
        try:
            return bool(self._check(resp))
        except Exception:
            return False


def _is_list(x) -> bool:
    return isinstance(x, list)


# Prometheus instant query: data.result is a list of samples.
PROMQL = Schema("promql", lambda b: _is_list(b.get("data", {}).get("result")))

# Loki query_range: data.result is a list of streams.
LOKI = Schema("loki", lambda b: _is_list(b.get("data", {}).get("result")))

# Tempo search: a dict with a traces list (may be empty until ch09 emits traces).
TEMPO = Schema("tempo", lambda b: isinstance(b, dict) and _is_list(b.get("traces", [])))

# Deploy ledger fetch (local): a dict carrying a deploys list.
DEPLOYS = Schema("deploys", lambda b: _is_list(b.get("deploys")))

# Runbook search (local): a list of matches.
RUNBOOKS = Schema("runbooks", lambda b: _is_list(b))

# Anything (used by tools that validate internally, like scoped_kubectl).
ANY = Schema("any", lambda b: True)
