"""Conversation state: the engineer-facing thread, in Redis, ephemeral.

This is the second of the three kinds of state. It is deliberately NOT a source
of truth. It is a presentation of the task's progress, and it can be regenerated
from the durable task-state log at any time. That is the whole defense against the
drift failure: because the conversation is derived from task state, the two can
never disagree on recovery. If Redis evicts it, we rebuild it from Postgres.

Losing conversation state is survivable; losing task state is not. So the
orchestrator treats Redis as best-effort: a Redis outage degrades the narration
but never breaks the investigation, which runs on the durable log.

At ch04/05 there is no live engineer dialogue yet (assisted-mode approvals are
ch12). The conversation here is the agent narrating its own progress, which is
enough to demonstrate the derive-from-task-state discipline.
"""
from __future__ import annotations

import json
import os

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover
    redis = None

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TTL_SECONDS = 60 * 60  # ephemeral: an hour is plenty for an incident


class ConversationStore:
    def __init__(self, url: str = REDIS_URL):
        self._client = None
        if redis is not None:
            try:
                self._client = redis.Redis.from_url(url, decode_responses=True)
                self._client.ping()
            except Exception:
                self._client = None  # degrade to no-op; task state is the truth

    @property
    def available(self) -> bool:
        return self._client is not None

    def _key(self, incident_id: str) -> str:
        return f"conv:{incident_id}"

    def append(self, incident_id: str, role: str, text: str) -> None:
        if not self._client:
            return
        try:
            key = self._key(incident_id)
            self._client.rpush(key, json.dumps({"role": role, "text": text}))
            self._client.expire(key, TTL_SECONDS)
        except Exception:
            pass  # non-essential; never break the investigation over narration

    def history(self, incident_id: str) -> list[dict]:
        if not self._client:
            return []
        try:
            raw = self._client.lrange(self._key(incident_id), 0, -1)
            return [json.loads(x) for x in raw]
        except Exception:
            return []

    def clear(self, incident_id: str) -> None:
        if not self._client:
            return
        try:
            self._client.delete(self._key(incident_id))
        except Exception:
            pass

    def regenerate_from_steps(self, incident_id: str, steps: list[dict]) -> list[dict]:
        """Rebuild the conversation from the authoritative task-state log.

        This is the recovery path: rather than trusting whatever survived in
        Redis, we derive the thread from the durable steps, so it always matches
        what actually happened. The narration is a presentation of the log.
        """
        self.clear(incident_id)
        turns: list[dict] = []
        for s in steps:
            if s["kind"] == "decide":
                d = s["result"]
                why = d.get("reason", "")
                if d.get("action") == "conclude":
                    turns.append(("agent", f"I have enough to conclude. {why}"))
                else:
                    turns.append(("agent", f"Next I'll {d.get('tool')}: {why}."))
            elif s["kind"] == "tool":
                turns.append(("agent", f"Result from {s['request'].get('tool')} recorded."))
            elif s["kind"] == "action":
                act = s["result"].get("action", {})
                turns.append(("agent", f"Proposed remediation: {act.get('remediation')}"))
        for role, text in turns:
            self.append(incident_id, role, text)
        return self.history(incident_id)
