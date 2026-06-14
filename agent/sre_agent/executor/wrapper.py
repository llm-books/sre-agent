"""The defensive wrapper. Every tool goes through it.

It applies the four defenses in a fixed order: a timeout, failure classification
(transient retry / auth refresh / permanent stop), response-schema validation
before reading, and a defined total-failure behavior (fallback, else honest
failure). Partial gathering across many sources has its own helper.

The wrapper deals in three exception categories. Real tools translate their
upstream's errors (requests timeouts, HTTP status codes, missing files) into
these; contract tests raise them directly to exercise each path.
"""
from __future__ import annotations

import time
from typing import Callable

import requests

from .results import ToolResult
from .schemas import Schema


class TransientError(Exception):
    """Worth retrying: a timeout, a 429, a 5xx, a dropped connection."""


class AuthError(Exception):
    """Credential went stale: a 401 or 403. Refresh once, then retry."""


class PermanentError(Exception):
    """Not worth retrying: a 400, a malformed request, a missing local file."""


def http_get_json(url: str, params: dict, timeout: float) -> dict:
    """A requests GET that translates failures into the wrapper's categories."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.Timeout as e:
        raise TransientError(f"timeout: {e}") from e
    except requests.ConnectionError as e:
        raise TransientError(f"connection: {e}") from e
    code = resp.status_code
    if code in (401, 403):
        raise AuthError(f"{code}")
    if code == 429 or code >= 500:
        raise TransientError(f"{code}")
    if code >= 400:
        raise PermanentError(f"{code}")
    return resp.json()


def _backoff(attempt: int) -> None:
    time.sleep(min(0.05 * (2 ** attempt), 1.0))


def defensive_call(
    op: Callable[[float], object],
    *,
    schema: Schema,
    timeout: float = 10.0,
    retries: int = 3,
    fallback: Callable[[], object] | None = None,
    on_auth: Callable[[], None] | None = None,
    sleep: Callable[[int], None] = _backoff,
) -> ToolResult:
    last = None
    auth_tried = False
    for attempt in range(retries):
        try:
            resp = op(timeout)
        except TransientError as e:
            last = e
            sleep(attempt)
            continue
        except AuthError as e:
            last = e
            if on_auth is not None and not auth_tried:
                auth_tried = True
                on_auth()
                continue
            return ToolResult.failure(f"auth failed: {e}")
        except PermanentError as e:
            return ToolResult.failure(f"permanent: {e}")
        if not schema.validates(resp):
            # The drift defense: a response that does not match the contract is a
            # failure, not something to parse into garbage.
            return ToolResult.failure(f"schema drift (expected {schema.name})")
        return ToolResult.ok_(resp)
    if fallback is not None:
        return ToolResult.degraded(fallback())
    return ToolResult.failure(f"exhausted after {retries} attempts: {last}")


def gather(items: list, op_for: Callable[[object], ToolResult]) -> ToolResult:
    """Run a tool across many items and return what succeeded.

    If everything succeeds, an ok result. If some fail, a partial result carrying
    the successes and a list of what was missing, so the agent can reason with
    incomplete information instead of failing wholesale. If nothing succeeds, a
    clean failure.
    """
    got = {}
    missing = []
    for item in items:
        res = op_for(item)
        if res.ok:
            got[item] = res.data
        else:
            missing.append(item)
    if not got:
        return ToolResult.failure(f"all {len(items)} sources failed")
    if missing:
        return ToolResult.partial(got, missing)
    return ToolResult.ok_(got)
