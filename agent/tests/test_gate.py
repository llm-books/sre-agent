"""Deployment-gate tests. The gate logic is pure (no env), so most of these run
anywhere; the baseline-store round-trip needs Postgres and skips if it is down.
"""
from __future__ import annotations

import pytest

from sre_agent import db
from sre_agent.evals.gate import (
    GateConfig,
    Profile,
    evaluate_gate,
    rolling_from_history,
)


def _baseline(correctness=0.6, safety=1.0, avg_steps=8.0, noise=None):
    return {
        "profile": {"correctness": correctness, "safety": safety, "avg_steps": avg_steps},
        "noise": noise or {"correctness": 0.0, "safety": 0.0, "avg_steps": 0.0},
    }


def test_equal_candidate_passes():
    dec = evaluate_gate(Profile(0.6, 1.0, 8.0), _baseline())
    assert dec.passed
    assert all(v.status == "ok" for v in dec.verdicts)


def test_safety_regression_blocks_hard():
    dec = evaluate_gate(Profile(0.6, 0.8, 8.0), _baseline())
    assert not dec.passed
    assert any(v.dimension == "safety" and v.status == "block" for v in dec.verdicts)


def test_correctness_drop_beyond_margin_blocks():
    dec = evaluate_gate(Profile(0.4, 1.0, 8.0), _baseline())  # 0.2 drop > 0.05 margin
    assert not dec.passed
    assert any(v.dimension == "correctness" and v.status == "block" for v in dec.verdicts)


def test_correctness_drop_within_margin_passes():
    dec = evaluate_gate(Profile(0.57, 1.0, 8.0), _baseline())  # 0.03 drop < 0.05 margin
    assert dec.passed


def test_correctness_uses_measured_noise_band():
    # With a noisy baseline, a larger drop is tolerated (it is within the noise).
    noisy = _baseline(noise={"correctness": 0.15, "safety": 0.0, "avg_steps": 0.0})
    assert evaluate_gate(Profile(0.5, 1.0, 8.0), noisy).passed       # 0.1 drop < 0.15 noise
    assert not evaluate_gate(Profile(0.3, 1.0, 8.0), noisy).passed   # 0.3 drop > 0.15 noise


def test_efficiency_warns_but_does_not_block():
    dec = evaluate_gate(Profile(0.6, 1.0, 10.4), _baseline())  # 1.3x baseline
    assert dec.passed
    assert any(v.dimension == "efficiency" and v.status == "warn" for v in dec.verdicts)


def test_extreme_efficiency_blocks():
    dec = evaluate_gate(Profile(0.6, 1.0, 20.0), _baseline())  # 2.5x baseline
    assert not dec.passed


def test_rolling_baseline_does_not_inflate_on_a_lucky_run():
    # A single upward-noise run must not set the bar; the rolling mean absorbs it.
    history = [
        {"correctness": 0.6, "safety": 1.0, "avg_steps": 8.0},
        {"correctness": 0.6, "safety": 1.0, "avg_steps": 8.0},
        {"correctness": 0.9, "safety": 1.0, "avg_steps": 8.0},  # lucky high run
        {"correctness": 0.6, "safety": 1.0, "avg_steps": 8.0},
    ]
    rolling, _ = rolling_from_history(history)
    assert rolling.correctness < 0.9       # not enshrined
    assert 0.6 <= rolling.correctness <= 0.7


# ---- DB-backed: baseline store + override ----------------------------------

def _db_up() -> bool:
    try:
        db.bootstrap()
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_up(), reason="agent Postgres not reachable")
def test_baseline_roundtrip_and_override():
    from sre_agent.evals.gate import load_baseline, record_override
    from sre_agent.evals.gate import _save_baseline  # noqa: PLC2701

    name = "test-gate-baseline"
    prof = Profile(0.6, 1.0, 8.0)
    _save_baseline(name, prof, {"correctness": 0.0, "safety": 0.0, "avg_steps": 0.0}, [prof.to_dict()])
    row = load_baseline(name)
    assert row["profile"]["correctness"] == 0.6

    record_override(name, owner="tester", reason="unit test", candidate=prof)
    with db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM gate_overrides WHERE baseline = %s", (name,)
        ).fetchone()["n"]
        conn.execute("DELETE FROM gate_overrides WHERE baseline = %s", (name,))
        conn.execute("DELETE FROM baselines WHERE name = %s", (name,))
        conn.commit()
    assert n == 1
