"""Dedup helper.

For now: query SQLite by paper_id; if exists with status='done', return True.
Title-normalization fallback is left as TODO (needs a `papers.title_norm`
column index).
"""

from __future__ import annotations

import re

from ..utils.logger import get_logger


log = get_logger("ingest.dedup")


_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_title(title: str) -> str:
    """Lowercase + strip punctuation/spaces, used for fallback dedup."""
    return _PUNCT_RE.sub("", title.lower())


def is_done(paper_id: str) -> bool:
    """Return True if the paper has already been ingested to status=done."""
    from ..store.sqlite_store import get_paper

    row = get_paper(paper_id)
    return bool(row and row.status == "done")
