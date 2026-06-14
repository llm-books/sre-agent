"""Conversation-state tests: the thread is regenerated from the durable task-state
log, so it survives a Redis eviction and never drifts from what happened. Skip if
Redis is not reachable."""
from __future__ import annotations

import pytest

from sre_agent.conversation import ConversationStore

STEPS = [
    {"kind": "decide", "request": {}, "result": {"action": "tool", "tool": "promql_query", "reason": "check latency"}},
    {"kind": "tool", "request": {"tool": "promql_query"}, "result": {"data": {}}},
    {"kind": "decide", "request": {}, "result": {"action": "conclude", "reason": "enough evidence"}},
    {"kind": "action", "request": {}, "result": {"action": {"remediation": "restore index"}}},
]


@pytest.fixture
def conv():
    c = ConversationStore()
    if not c.available:
        pytest.skip("redis not reachable")
    c.clear("test-conv-1")
    yield c
    c.clear("test-conv-1")


def test_regenerate_reflects_task_state(conv):
    turns = conv.regenerate_from_steps("test-conv-1", STEPS)
    text = " ".join(t["text"] for t in turns)
    assert "promql_query" in text
    assert "restore index" in text


def test_regenerate_survives_eviction(conv):
    turns = conv.regenerate_from_steps("test-conv-1", STEPS)
    # Simulate Redis evicting the ephemeral conversation.
    conv.clear("test-conv-1")
    assert conv.history("test-conv-1") == []
    # Rebuilding from the same task-state log reproduces the exact thread.
    again = conv.regenerate_from_steps("test-conv-1", STEPS)
    assert again == turns
