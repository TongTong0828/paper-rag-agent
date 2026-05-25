"""paper_rag.feedback — user behavior telemetry & data-loop layer (M11 / ADR-0017).

Module layout
-------------
- ``events`` — typed event schema + validators
- ``store``  — SQLite-backed feedback_events table (separate file from papers.sqlite)
- ``collector`` — single entry point used by gateway router + LangChain tool

Public API:
    record_event(user_id, event_type, payload, *, trace_id=None,
                 conversation_id=None) -> int      # event_id
    recent_events(user_id, limit=20) -> list[dict]
    user_stats(user_id) -> dict                    # aggregated counts

ADR-0017 records the rationale.
"""

from __future__ import annotations

from .collector import recent_events, record_event, user_stats
from .events import EVENT_TYPES, FeedbackEvent, validate_payload

__all__ = [
    "FeedbackEvent",
    "EVENT_TYPES",
    "validate_payload",
    "record_event",
    "recent_events",
    "user_stats",
]
