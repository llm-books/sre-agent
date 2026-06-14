"""Load and enforce the chapter 3 scope boundary.

The agent reads `agent/scope.yaml` at startup. This module turns that config into
checks the orchestrator and executor consult: is this service in scope, is this
action core (autonomous), frontier (propose), wilderness (escalate), or outright
forbidden. At the ch04 checkpoint the boundary is consulted but every remediation
is still shadow-only; ch11 enforces it against hostile input and ch12 graduates
frontier actions out of the gated set.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

SCOPE_PATH = Path(__file__).resolve().parents[1].parent / "agent" / "scope.yaml"
# When running from the repo root, scope.yaml sits at agent/scope.yaml. When the
# package is installed elsewhere, AGENT_SCOPE can override.
import os

if os.environ.get("AGENT_SCOPE"):
    SCOPE_PATH = Path(os.environ["AGENT_SCOPE"])
elif not SCOPE_PATH.exists():
    # Fallback: walk up for an agent/scope.yaml.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "scope.yaml"
        if candidate.exists():
            SCOPE_PATH = candidate
            break


class Scope:
    def __init__(self, data: dict):
        self.in_scope_services = set(data.get("in_scope_services", []))
        self.core = set(data.get("core_actions", []))
        self.frontier = set(data.get("frontier_actions", []))
        self.wilderness = set(data.get("wilderness_actions", []))
        self.forbidden = {f["id"] for f in data.get("forbidden_actions", [])}
        self.default = data.get("default", "escalate")

    def service_in_scope(self, service: str) -> bool:
        return service in self.in_scope_services

    def disposition(self, action: str) -> str:
        """Return one of: core, frontier, wilderness, forbidden, or the default."""
        if action in self.forbidden:
            return "forbidden"
        if action in self.core:
            return "core"
        if action in self.frontier:
            return "frontier"
        if action in self.wilderness:
            return "wilderness"
        return self.default

    def is_autonomous(self, action: str) -> bool:
        return self.disposition(action) == "core"


@functools.lru_cache(maxsize=1)
def load() -> Scope:
    data = yaml.safe_load(SCOPE_PATH.read_text())
    return Scope(data)
