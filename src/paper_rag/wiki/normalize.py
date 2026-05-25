"""Concept normalization: candidate name -> existing entry (or None)."""

from __future__ import annotations

from .. import config as cfg
from ..utils.logger import get_logger
from .schema import normalize_name


log = get_logger("wiki.normalize")


def find_match(name: str, *, embed_query: bool = True) -> str | None:
    """Return existing entry_id if the candidate matches an existing entry.

    Resolution order:
      1. exact normalized name
      2. alias match across existing entries
      3. semantic similarity in wiki Qdrant collection (>= threshold)
    """
    from . import store

    norm = normalize_name(name)
    direct = store.get_by_name(name)
    if direct:
        return direct.entry_id

    for entry in store.list_all():
        if any(normalize_name(a) == norm for a in entry.aliases):
            return entry.entry_id

    if not embed_query:
        return None
    threshold = cfg.load().wiki.similarity_threshold
    try:
        from ..embed import bge_m3

        vec = bge_m3.encode_one(name)
        hits = store.search_qdrant(vec, top_k=3)
        for h in hits:
            if h.get("score", 0.0) >= threshold:
                return h.get("entry_id")
    except Exception as e:
        log.warning(f"semantic match skipped: {e}")
    return None
