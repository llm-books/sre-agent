"""Load the rollout matrix (agent/rollout.yaml)."""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_PATH = Path(__file__).resolve().parents[2] / "rollout.yaml"
if os.environ.get("AGENT_ROLLOUT"):
    _PATH = Path(os.environ["AGENT_ROLLOUT"])


@dataclass
class Remediation:
    action_id: str
    service: str
    mode: str            # autonomous | assisted | gated
    stakes: str          # low | moderate | high
    reversible: object   # True | False | "partial"
    command: str
    env_effect: str      # reset | simulated | none
    graduation: str


@functools.lru_cache(maxsize=1)
def _data() -> dict:
    return yaml.safe_load(_PATH.read_text())


def all_remediations() -> dict[str, Remediation]:
    out = {}
    for action_id, r in _data()["remediations"].items():
        out[action_id] = Remediation(action_id=action_id, **r)
    return out


def get(action_id: str) -> Remediation | None:
    return all_remediations().get(action_id)


def for_service(service: str) -> str | None:
    return _data().get("service_remediation", {}).get(service)


def mode_for(action_id: str) -> str:
    r = get(action_id)
    return r.mode if r else "gated"
