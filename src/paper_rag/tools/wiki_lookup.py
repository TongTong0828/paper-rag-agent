"""wiki_lookup tool: concept -> wiki entry (or near misses)."""

from __future__ import annotations

from ..utils.logger import get_logger
from ..wiki import store as wstore
from ..wiki.normalize import find_match
from ._schema import WikiLookupInput


log = get_logger("tool.wiki_lookup")


def wiki_lookup(input: WikiLookupInput) -> dict:
    direct = wstore.get_by_name(input.concept)
    if direct:
        return {"hit": True, "entry": direct.model_dump(mode="json")}

    matched = find_match(input.concept)
    if matched:
        e = wstore.get_entry(matched)
        if e:
            return {"hit": True, "entry": e.model_dump(mode="json"), "via": "alias_or_semantic"}

    try:
        from ..embed import bge_m3

        vec = bge_m3.encode_one(input.concept)
        candidates = wstore.search_qdrant(vec, top_k=5)
    except Exception as e:
        log.warning(f"wiki_lookup near miss skipped: {e}")
        candidates = []
    return {"hit": False, "near_misses": candidates}
