"""Public collector — single entry point for all event recording.

Used by:
  - gateway router POST /api/paper_rag/feedback
  - LangChain `paper_feedback_tool` (M11.5, optional)
  - offline scripts (judge_score after batch evaluation)

Adds a tiny rate-limit guard (per-user-per-day cap) per ADR-0017 risk
mitigation. Intended for in-process use only — distributed rate limiting
would belong in nginx / API gateway, not here.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Any

from . import store
from .events import make_event

log = logging.getLogger(__name__)


# Per-user daily cap to thwart abusive auto-submission. Tunable via env var
# in the future; hardcoded for v1 because this is in-process.
_DAILY_CAP_PER_USER = 200

# Threadsafe in-memory per-user counter; resets every UTC day boundary.
_lock = Lock()
_counter: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))  # user -> (day_id, count)


def _today_id() -> int:
    """UTC day id (epoch_days)."""
    return int(time.time() // 86400)


def _check_rate_limit(user_id: str) -> None:
    today = _today_id()
    with _lock:
        d, n = _counter[user_id]
        if d != today:
            n = 0
        if n >= _DAILY_CAP_PER_USER:
            raise PermissionError(
                f"feedback rate limit exceeded for user_id={user_id} "
                f"({_DAILY_CAP_PER_USER}/day)"
            )
        _counter[user_id] = (today, n + 1)


def record_event(
    user_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    trace_id: str | None = None,
    conversation_id: str | None = None,
) -> int:
    """Validate, rate-limit, and persist a feedback event. Returns row id."""
    _check_rate_limit(user_id)
    ev = make_event(
        user_id=user_id,
        event_type=event_type,
        payload=payload or {},
        trace_id=trace_id,
        conversation_id=conversation_id,
    )
    rid = store.write(ev)
    log.info(
        "feedback recorded: user=%s type=%s trace=%s id=%s",
        user_id, event_type, trace_id, rid,
    )
    return rid


def recent_events(user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return store.list_recent(user_id, limit=limit)


def user_stats(user_id: str) -> dict[str, Any]:
    return store.aggregate_user(user_id)


__all__ = ["recent_events", "record_event", "user_stats"]
