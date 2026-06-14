"""Task state for an incident investigation.

This is the in-memory working state, reconstructed by replaying the durable log
on every run. It is NOT the source of truth; the `steps` table is. Chapter 5
makes the distinction between task state, conversation state, and long-term
memory rigorous; at ch04 there is only task state, and it is a pure function of
the recorded steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Incident:
    """What triggered the investigation: a single alert."""
    alert: str
    service: str
    scenario: str | None = None  # the matching chaos scenario, if known

    def to_dict(self) -> dict:
        return {"alert": self.alert, "service": self.service, "scenario": self.scenario}

    @staticmethod
    def from_dict(d: dict) -> "Incident":
        return Incident(alert=d["alert"], service=d["service"], scenario=d.get("scenario"))


@dataclass
class InvestigationState:
    """Reconstructed from the durable log; the planner reads this to decide."""
    incident: Incident
    evidence: list[dict[str, Any]] = field(default_factory=list)  # gathered findings
    hypothesis: str | None = None
    proposed_remediation: str | None = None
    done: bool = False
    # ch05: relevant past incidents recalled from long-term memory, and the
    # service's current version, used to judge whether a recollection is stale.
    recalled: list[Any] = field(default_factory=list)
    service_version: str | None = None

    def add_evidence(self, kind: str, summary: str, data: Any = None) -> None:
        self.evidence.append({"kind": kind, "summary": summary, "data": data})

    @property
    def step_count(self) -> int:
        return len(self.evidence)
