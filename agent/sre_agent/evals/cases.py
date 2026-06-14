"""Eval cases, loaded from the chaos scenarios.

A timed chaos scenario carries everything an eval case needs: the service and
alert that frame the incident, and the correct_diagnosis / correct_remediation /
forbidden_remediations ground truth. Fixture scenarios (the security test) have
no diagnosis to score, so they are skipped here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SCENARIO_DIR = Path(__file__).resolve().parents[3] / "env" / "scenarios"


@dataclass
class EvalCase:
    name: str
    difficulty: str
    service: str
    alert: str
    correct_diagnosis: str
    correct_remediation: str
    forbidden_remediations: list[str] = field(default_factory=list)

    def incident_args(self) -> dict:
        return {"alert": self.alert, "service": self.service}


def load_cases(scenario_dir: Path | None = None) -> list[EvalCase]:
    d = scenario_dir or SCENARIO_DIR
    cases: list[EvalCase] = []
    for path in sorted(d.glob("*.yaml")):
        s = yaml.safe_load(path.read_text())
        if not s or s.get("type") == "fixture" or "correct_diagnosis" not in s:
            continue
        inject = s.get("inject", {})
        service = inject.get("target")
        if not service:
            continue
        alerts = s.get("expected_alerts") or []
        alert = alerts[0] if alerts else f"Anomaly-{service}"
        cases.append(EvalCase(
            name=s["name"],
            difficulty=s.get("difficulty", "unknown"),
            service=service,
            alert=alert,
            correct_diagnosis=s["correct_diagnosis"].strip(),
            correct_remediation=s.get("correct_remediation", "").strip(),
            forbidden_remediations=[f.strip() for f in s.get("forbidden_remediations", [])],
        ))
    return cases
