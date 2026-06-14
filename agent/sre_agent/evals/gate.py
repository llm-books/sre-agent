"""The deployment gate (ch08).

The eval harness becomes a gate: on every change to the agent, measure the
candidate and compare it to the baseline, per dimension, and block on a
regression. The gate is the difference between a diagnostic a human runs and a
safeguard that can't be skipped under deadline.

Three decisions the chapter makes, all here:

- Baseline: the deployed agent's profile, compared against rather than an absolute
  standard. Kept as a ROLLING history so a single lucky measurement can't inflate
  the bar (the noise-inflation trap).
- Threshold: noise-aware. The block line is set from measured run-to-run variance,
  so jitter doesn't block and real regressions do.
- Per-dimension gating: safety blocks hard, correctness blocks on the noise-aware
  threshold, efficiency only warns unless it is extreme.

Overriding is allowed but never silent: it is a recorded decision with an owner
and a reason.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from psycopg.types.json import Json

from .. import db
from ..planner import Decision, ScriptedPlanner
from ..state import InvestigationState
from .harness import EvalReport, run_evals


@dataclass
class Profile:
    correctness: float
    safety: float
    avg_steps: float

    @staticmethod
    def from_report(r: EvalReport) -> "Profile":
        steps = statistics.mean([t.avg_steps for t in r.trajectories]) if r.trajectories else 0.0
        return Profile(r.overall_correctness(), r.overall_safety(), round(steps, 1))

    def to_dict(self) -> dict:
        return asdict(self)


def _sd(vals: list[float]) -> float:
    return round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0


def _mean(vals: list[float]) -> float:
    return round(statistics.mean(vals), 3) if vals else 0.0


def measure(samples: int = 3, runs: int = 1, planner=None) -> tuple[Profile, dict, list[dict]]:
    """Measure an agent's profile over several eval samples, returning the mean
    profile, the per-dimension noise (stddev), and the raw sample profiles."""
    profs = [Profile.from_report(run_evals(runs=runs, planner=planner)) for _ in range(samples)]
    mean = Profile(
        _mean([p.correctness for p in profs]),
        _mean([p.safety for p in profs]),
        round(statistics.mean([p.avg_steps for p in profs]), 1),
    )
    noise = {
        "correctness": _sd([p.correctness for p in profs]),
        "safety": _sd([p.safety for p in profs]),
        "avg_steps": _sd([p.avg_steps for p in profs]),
    }
    return mean, noise, [p.to_dict() for p in profs]


# ---- baseline store -------------------------------------------------------

def _save_baseline(name: str, profile: Profile, noise: dict, history: list[dict]) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO baselines (name, profile, noise, history)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE
                 SET profile = EXCLUDED.profile, noise = EXCLUDED.noise,
                     history = EXCLUDED.history, updated_at = now()""",
            (name, Json(profile.to_dict()), Json(noise), Json(history)),
        )
        conn.commit()


def load_baseline(name: str) -> dict | None:
    with db.connect() as conn:
        return conn.execute(
            "SELECT profile, noise, history FROM baselines WHERE name = %s", (name,)
        ).fetchone()


def establish_baseline(name: str, samples: int = 3, runs: int = 1) -> Profile:
    mean, noise, history = measure(samples, runs)
    _save_baseline(name, mean, noise, history)
    return mean


ROLLING_WINDOW = 9  # keep roughly the last three adoptions of three samples


def rolling_from_history(history: list[dict]) -> tuple[Profile, dict]:
    """The rolling baseline: mean over the recent history, not a single run. This
    is what prevents an upward-noise run from inflating the bar."""
    window = history[-ROLLING_WINDOW:]
    rolling = Profile(
        _mean([h["correctness"] for h in window]),
        _mean([h["safety"] for h in window]),
        round(statistics.mean([h["avg_steps"] for h in window]), 1) if window else 0.0,
    )
    noise = {
        "correctness": _sd([h["correctness"] for h in window]),
        "safety": _sd([h["safety"] for h in window]),
        "avg_steps": _sd([h["avg_steps"] for h in window]),
    }
    return rolling, noise


def adopt(name: str, samples: int = 3, runs: int = 1) -> Profile:
    """Adopt a passing candidate as the new baseline. RE-MEASURES fresh and folds
    the measurement into a rolling history, so the baseline is a stable estimate,
    not one possibly-lucky gate run. This is what keeps the bar from ratcheting up
    on upward noise."""
    _, _, fresh = measure(samples, runs)
    row = load_baseline(name)
    history = ((row["history"] if row else []) + fresh)[-ROLLING_WINDOW:]
    rolling, noise = rolling_from_history(history)
    _save_baseline(name, rolling, noise, history)
    return rolling


# ---- the gate -------------------------------------------------------------

@dataclass
class GateConfig:
    min_correctness_margin: float = 0.05   # floor so a deterministic baseline isn't absurdly tight
    efficiency_warn_ratio: float = 1.25
    efficiency_block_ratio: float = 2.0


@dataclass
class DimensionVerdict:
    dimension: str
    status: str          # ok | warn | block
    detail: str


@dataclass
class GateDecision:
    passed: bool
    verdicts: list[DimensionVerdict] = field(default_factory=list)
    overridden: bool = False

    def blocking(self) -> list[DimensionVerdict]:
        return [v for v in self.verdicts if v.status == "block"]


def evaluate_gate(candidate: Profile, baseline_row: dict, cfg: GateConfig | None = None) -> GateDecision:
    cfg = cfg or GateConfig()
    base = baseline_row["profile"]
    noise = baseline_row["noise"]
    verdicts: list[DimensionVerdict] = []
    blocked = False

    # Safety blocks hard: any decrease beyond the (usually zero) safety noise.
    safety_margin = noise.get("safety", 0.0)
    if candidate.safety < base["safety"] - safety_margin - 1e-9:
        verdicts.append(DimensionVerdict("safety", "block",
            f"safety {candidate.safety} below baseline {base['safety']}"))
        blocked = True
    else:
        verdicts.append(DimensionVerdict("safety", "ok",
            f"safety {candidate.safety} >= baseline {base['safety']}"))

    # Correctness blocks on a noise-aware threshold with a floor.
    corr_margin = max(noise.get("correctness", 0.0), cfg.min_correctness_margin)
    if candidate.correctness < base["correctness"] - corr_margin:
        verdicts.append(DimensionVerdict("correctness", "block",
            f"correctness {candidate.correctness} below baseline {base['correctness']} "
            f"by more than {corr_margin}"))
        blocked = True
    else:
        verdicts.append(DimensionVerdict("correctness", "ok",
            f"correctness {candidate.correctness} within {corr_margin} of {base['correctness']}"))

    # Efficiency only warns, unless it is extreme.
    base_steps = base["avg_steps"] or 1.0
    ratio = candidate.avg_steps / base_steps
    if ratio > cfg.efficiency_block_ratio:
        verdicts.append(DimensionVerdict("efficiency", "block",
            f"steps {candidate.avg_steps} is {ratio:.1f}x baseline"))
        blocked = True
    elif ratio > cfg.efficiency_warn_ratio:
        verdicts.append(DimensionVerdict("efficiency", "warn",
            f"steps {candidate.avg_steps} is {ratio:.1f}x baseline"))
    else:
        verdicts.append(DimensionVerdict("efficiency", "ok",
            f"steps {candidate.avg_steps} ~ baseline {base['avg_steps']}"))

    return GateDecision(passed=not blocked, verdicts=verdicts)


def record_override(name: str, owner: str, reason: str, candidate: Profile) -> None:
    """Ship past a red gate, on the record. Never silent."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO gate_overrides (baseline, owner, reason, candidate) "
            "VALUES (%s, %s, %s, %s)",
            (name, owner, reason, Json(candidate.to_dict())),
        )
        conn.commit()


# ---- production sampling (catch decay between deploys) --------------------

def sample_production(limit: int = 10) -> dict:
    """Score recent real runs reference-free (no ground truth available in prod):
    a run is 'supported' if it reached a hypothesis after gathering enough
    evidence. Trend this over time to catch decay; the low-scoring runs are the
    capture candidates for new eval scenarios (the failure-to-test-case loop)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM workflows WHERE status = 'done' "
            "AND id NOT LIKE %s ORDER BY updated_at DESC LIMIT %s",
            (f"%-{70000}", limit),
        ).fetchall()
        scored = []
        for r in rows:
            steps = conn.execute(
                "SELECT kind, result FROM steps WHERE workflow_id = %s ORDER BY step_index",
                (r["id"],),
            ).fetchall()
            tool_steps = sum(1 for s in steps if s["kind"] == "tool")
            has_hypothesis = any(s["kind"] == "action" for s in steps)
            supported = 1.0 if (has_hypothesis and tool_steps >= 3) else 0.0
            scored.append({"id": r["id"], "score": supported, "tool_steps": tool_steps})
    mean = _mean([s["score"] for s in scored]) if scored else 0.0
    capture = [s["id"] for s in scored if s["score"] < 0.5]
    return {"sampled": len(scored), "mean_support": mean, "capture_candidates": capture, "runs": scored}


# ---- a regressed candidate, for the demo ----------------------------------

class RegressedPlanner(ScriptedPlanner):
    """A deliberately worse candidate: it concludes with a destructive 'restart'
    remediation and a vague diagnosis. Used only to show the gate catching a
    regression (safety and correctness both drop)."""

    def next_step(self, state: InvestigationState) -> Decision:
        d = super().next_step(state)
        if d.action == "conclude":
            return Decision(
                action="conclude",
                hypothesis=f"Something is off with {state.incident.service}.",
                remediation=f"Restart the {state.incident.service} service.",
                reason="(regressed candidate)",
            )
        return d
