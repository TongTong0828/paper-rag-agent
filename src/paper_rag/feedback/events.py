"""Feedback event schema (M11 / ADR-0017).

Pure-logic typed schema for user-behavior events. No I/O — see store.py for
persistence, collector.py for the public record_event entry point.

Event types (v1)
----------------
- ``thumbs_up``                 user gave positive feedback on an answer
- ``thumbs_down``               user gave negative feedback (with reason enum)
- ``copy_answer``               user copied (part of) an answer to clipboard
- ``follow_up_question``        user asked a follow-up within the same convo
- ``abandon``                   no follow-up within 5min after answer
- ``abstain_followup_ingest``   abstain-rejected query, then user manually ingested a paper
- ``judge_score``               offline LLM-judge rating (faithful / complete / concise)

Privacy guards
--------------
``validate_payload`` rejects any field that smells like raw user free-text
(``comment`` -> stored as ``comment_length`` + ``keyword_hits``). See
ADR-0017 decision 5.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

EVENT_TYPES = (
    "thumbs_up",
    "thumbs_down",
    "copy_answer",
    "follow_up_question",
    "abandon",
    "abstain_followup_ingest",
    "judge_score",
)

# thumbs_down reason enum
THUMBS_DOWN_REASONS = (
    "hallucination",
    "irrelevant",
    "incomplete",
    "wrong_citation",
    "other",
)

# Keywords we DO want to capture (presence flag, not raw text)
_KEYWORD_PATTERNS = {
    "hallucination": re.compile(r"\b(hallucin|made\s*up|fabricat|invent)\w*", re.I),
    "wrong_paper": re.compile(r"\b(wrong\s*paper|different\s*paper|not\s*this\s*paper)\b", re.I),
    "outdated": re.compile(r"\b(outdated|old\s*version|deprecated)\b", re.I),
    "missing_context": re.compile(r"\b(missing|incomplete|partial|cut\s*off)\b", re.I),
}


@dataclass
class FeedbackEvent:
    """Typed event ready to be persisted by store.write()."""

    user_id: str
    event_type: str
    payload: dict[str, Any]
    trace_id: str | None = None
    conversation_id: str | None = None
    created_at: float = 0.0  # epoch seconds; 0 means "fill at write time"


def _hits_keywords(text: str) -> list[str]:
    """Return list of triggered keyword categories. Used to record signals
    from comments WITHOUT storing the raw comment."""
    if not text:
        return []
    return [name for name, pat in _KEYWORD_PATTERNS.items() if pat.search(text)]


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize / validate / strip-PII an incoming payload.

    Returns the cleaned payload. Raises ValueError on invalid data.

    Privacy contract:
      - ``comment`` (raw text) is NEVER persisted. It's converted to:
        comment_length: int
        comment_keywords: list[str]   # categorical hits
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type!r}")

    payload = dict(payload or {})

    # Strip raw comment, convert to length + keyword hits
    if "comment" in payload:
        raw = payload.pop("comment")
        if isinstance(raw, str):
            payload["comment_length"] = min(len(raw), 5000)
            payload["comment_keywords"] = _hits_keywords(raw)

    # Per-type required fields
    if event_type == "thumbs_down":
        reason = payload.get("reason")
        if reason not in THUMBS_DOWN_REASONS:
            raise ValueError(
                f"thumbs_down requires reason in {THUMBS_DOWN_REASONS}, got {reason!r}"
            )

    if event_type == "judge_score":
        for key in ("faithful", "complete", "concise"):
            v = payload.get(key)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"judge_score.{key} must be a float, got {v!r}")
            if not (0.0 <= f <= 5.0):
                raise ValueError(f"judge_score.{key}={f} out of [0, 5]")
            payload[key] = f

    if event_type == "copy_answer":
        n = payload.get("snippet_chars")
        if n is not None:
            try:
                payload["snippet_chars"] = max(0, int(n))
            except (TypeError, ValueError):
                raise ValueError(f"copy_answer.snippet_chars must be int, got {n!r}")

    if event_type == "abstain_followup_ingest":
        pid = payload.get("ingested_paper_id")
        if not isinstance(pid, str) or not pid:
            raise ValueError("abstain_followup_ingest requires ingested_paper_id")

    return payload


def make_event(
    user_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None = None,
    conversation_id: str | None = None,
) -> FeedbackEvent:
    """Build + validate a FeedbackEvent. Raises ValueError on invalid data."""
    if not user_id:
        raise ValueError("user_id is required")
    cleaned = validate_payload(event_type, payload)
    return FeedbackEvent(
        user_id=user_id,
        event_type=event_type,
        payload=cleaned,
        trace_id=trace_id,
        conversation_id=conversation_id,
        created_at=time.time(),
    )


__all__ = [
    "EVENT_TYPES",
    "THUMBS_DOWN_REASONS",
    "FeedbackEvent",
    "make_event",
    "validate_payload",
]
