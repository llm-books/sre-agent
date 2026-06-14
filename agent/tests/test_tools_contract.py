"""Contract tests against a FAKE backend.

These exercise the defensive wrapper's logic, deterministically, with no network:
each failure mode is injected and the wrapper's response asserted. This is the
constant, every-commit half of the contract-test story; test_tools_real.py is the
periodic real-upstream half that catches actual drift.
"""
from __future__ import annotations

from sre_agent.executor import schemas
from sre_agent.executor.tools import scoped_kubectl
from sre_agent.executor.wrapper import (
    AuthError,
    PermanentError,
    TransientError,
    defensive_call,
    gather,
)
from sre_agent.executor.results import ToolResult

NO_SLEEP = lambda _: None
GOOD = {"data": {"result": []}} # valid promql shape
DRIFTED = {"data": {}} # 'result' renamed/removed: schema drift


def test_valid_response_is_ok():
    res = defensive_call(lambda t: GOOD, schema=schemas.PROMQL, sleep=NO_SLEEP)
    assert res.status == "ok"


def test_schema_drift_is_clean_failure_not_garbage():
    res = defensive_call(lambda t: DRIFTED, schema=schemas.PROMQL, sleep=NO_SLEEP)
    assert res.status == "failure"
    assert "drift" in res.reason


def test_transient_then_success_retries():
    calls = {"n": 0}

    def op(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TransientError("429")
        return GOOD

    res = defensive_call(op, schema=schemas.PROMQL, sleep=NO_SLEEP)
    assert res.status == "ok"
    assert calls["n"] == 2


def test_transient_exhausted_is_failure():
    def op(t):
        raise TransientError("timeout")

    res = defensive_call(op, schema=schemas.PROMQL, retries=3, sleep=NO_SLEEP)
    assert res.status == "failure"
    assert "exhausted" in res.reason


def test_exhausted_with_fallback_is_degraded():
    res = defensive_call(
        lambda t: (_ for _ in ()).throw(TransientError("down")),
        schema=schemas.PROMQL, retries=2, sleep=NO_SLEEP,
        fallback=lambda: {"cached": True},
    )
    assert res.status == "degraded"
    assert res.data == {"cached": True}


def test_auth_refreshes_once_then_succeeds():
    calls = {"n": 0}
    refreshed = {"done": False}

    def op(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthError("401")
        return GOOD

    res = defensive_call(
        op, schema=schemas.PROMQL, sleep=NO_SLEEP,
        on_auth=lambda: refreshed.__setitem__("done", True),
    )
    assert res.status == "ok"
    assert refreshed["done"] is True


def test_auth_without_refresh_is_failure():
    res = defensive_call(
        lambda t: (_ for _ in ()).throw(AuthError("403")),
        schema=schemas.PROMQL, sleep=NO_SLEEP,
    )
    assert res.status == "failure"
    assert "auth" in res.reason


def test_permanent_does_not_retry():
    calls = {"n": 0}

    def op(t):
        calls["n"] += 1
        raise PermanentError("400")

    res = defensive_call(op, schema=schemas.PROMQL, retries=3, sleep=NO_SLEEP)
    assert res.status == "failure"
    assert calls["n"] == 1  # permanent failures are not retried


def test_gather_partial_when_some_sources_fail():
    def op_for(item):
        return ToolResult.ok_(item) if item != "payments" else ToolResult.failure("down")

    res = gather(["web", "orders", "payments"], op_for)
    assert res.status == "partial"
    assert res.missing == ["payments"]
    assert set(res.data) == {"web", "orders"}


def test_gather_all_fail_is_failure():
    res = gather(["a", "b"], lambda i: ToolResult.failure("down"))
    assert res.status == "failure"


# ---- scoped_kubectl enforcement (in the tool, not the prompt) --------------

def test_kubectl_read_is_autonomous():
    assert scoped_kubectl({"command": "get", "target": "orders"}).status == "ok"


def test_kubectl_restart_payments_is_forbidden():
    res = scoped_kubectl({"command": "restart", "target": "payments"})
    assert res.status == "failure"
    assert "forbidden" in res.reason


def test_kubectl_delete_is_forbidden():
    assert scoped_kubectl({"command": "delete", "target": "orders"}).status == "failure"


def test_kubectl_blind_all_is_forbidden():
    assert scoped_kubectl({"command": "rollout-restart", "target": "all"}).status == "failure"


def test_kubectl_write_needs_approval():
    gated = scoped_kubectl({"command": "rollout-restart", "target": "orders"})
    assert gated.status == "failure" and "approval" in gated.reason
    ok = scoped_kubectl({"command": "rollout-restart", "target": "orders", "approved": True})
    assert ok.status == "ok"


def test_kubectl_unknown_command_refused():
    assert scoped_kubectl({"command": "hack", "target": "orders"}).status == "failure"
