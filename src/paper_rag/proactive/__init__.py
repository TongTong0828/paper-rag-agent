"""paper_rag.proactive — proactive Agent layer (M9 / ADR-0018).

Three concerns:
  1. ``subscriptions`` — user keyword/topic subscriptions
  2. ``inbox``         — system → user notifications (digest, sub_match, stale, auto_ingest)
  3. ``paper_access``  — user-paper access timestamps (used by stale detector)

Each concern has its own SQLite-backed store in
``feedback.sqlite`` (same DB file as M11 feedback_events — both are
behavior/state metadata, distinct from the papers main DB).

Public surface
--------------
    from paper_rag.proactive import (
        subscriptions, inbox, paper_access,        # store modules
        digest, stale, matcher, auto_ingest_hook,  # behavior modules
        cron_runner,                                # M9.5 standalone scheduler
    )

ADR-0018 records the rationale.
"""

from __future__ import annotations

from . import (
    auto_ingest_hook,
    cron_runner,
    digest,
    inbox,
    matcher,
    paper_access,
    stale,
    subscriptions,
    webhook,
)

__all__ = [
    "auto_ingest_hook",
    "cron_runner",
    "digest",
    "inbox",
    "matcher",
    "paper_access",
    "stale",
    "subscriptions",
    "webhook",
]
