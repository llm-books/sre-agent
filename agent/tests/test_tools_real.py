"""Contract tests against the REAL upstreams.

These call the live synthetic environment and validate that the actual responses
still match the contracts the tools assume. This is the half that catches drift:
a fake backend returns the old shape forever, so only a real call sees the world
change. Run on a schedule, not every commit. Skips if the environment is down.

They assert on SHAPE (a valid, usable result), not on content, so they pass even
when, say, Loki has no matching logs yet or Tempo is empty.
"""
from __future__ import annotations

import pytest
import requests

from sre_agent.executor.tools import PROM_URL, deploy_history, log_search, promql_query, runbook_search


def _env_up() -> bool:
    try:
        requests.get(f"{PROM_URL}/-/ready", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _env_up(), reason="synthetic environment not reachable")


def test_promql_real_shape():
    res = promql_query({"query": "up"})
    assert res.ok
    assert "samples" in res.data


def test_log_search_real_shape():
    # Valid shape even if zero streams match in the window.
    res = log_search({"service": "orders"})
    assert res.ok
    assert "streams" in res.data


def test_deploy_history_real_shape():
    res = deploy_history({"service": "api-gateway"})
    assert res.ok
    assert isinstance(res.data["deploys"], list)


def test_runbook_search_real_shape():
    res = runbook_search({"query": "payments"})
    assert res.ok
    assert isinstance(res.data, list)
