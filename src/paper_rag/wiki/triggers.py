"""Triggers: hook called after a paper finishes ingest."""

from __future__ import annotations

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("wiki.triggers")


def on_paper_indexed(paper_id: str) -> dict:
    """Run extraction + create/patch flow for a freshly indexed paper.

    Returns a small report. No-op if wiki.enabled=false. All sqlmodel /
    Qdrant imports are deferred so this module imports cleanly even when
    optional deps are missing (e.g. CI / unit tests).
    """
    from sqlmodel import Session, select  # local import for optional dep
    c = cfg.load().wiki
    if not c.enabled:
        return {"skipped": "wiki disabled"}

    from ..embed import bge_m3
    from ..store.sqlite_store import Chunk, Paper, get_engine
    from . import store as wstore
    from .concept_extractor import extract_concepts
    from .flow import create_entry, patch_entry
    from .normalize import find_match

    engine = get_engine()
    with Session(engine) as s:
        paper = s.get(Paper, paper_id)
        if paper is None:
            return {"error": f"paper not found: {paper_id}"}
        rows = list(s.exec(select(Chunk).where(Chunk.paper_id == paper_id)))
    chunks = [
        {"chunk_id": r.chunk_id, "section": None, "text": r.text}
        for r in rows
        if r.modality == "text"
    ][:30]
    if not chunks:
        return {"skipped": "no text chunks"}

    concepts = extract_concepts(title=paper.title, chunks=chunks)
    created, patched, skipped = 0, 0, 0
    for c in concepts:
        evidence_ids = set(c.get("evidence_chunk_ids") or [])
        evidence_chunks = [ch for ch in chunks if ch["chunk_id"] in evidence_ids] or chunks[:5]

        match_id = find_match(c["name"])
        if match_id:
            existing = wstore.get_entry(match_id)
            if existing is None:
                skipped += 1
                continue
            updated = patch_entry(
                existing=existing,
                paper_id=paper_id,
                paper_title=paper.title,
                chunks=evidence_chunks,
            )
            if updated:
                wstore.upsert_entry(updated, reason=f"patched from {paper_id}")
                try:
                    vec = bge_m3.encode_one(f"{updated.name}\n{updated.definition}")
                    wstore.upsert_qdrant(updated, vec)
                except Exception as e:
                    log.warning(f"qdrant mirror skipped: {e}")
                patched += 1
            else:
                skipped += 1
        else:
            entry = create_entry(
                name=c["name"],
                category=c["category"],
                paper_id=paper_id,
                paper_title=paper.title,
                chunks=evidence_chunks,
            )
            if entry:
                wstore.upsert_entry(entry, reason=f"created from {paper_id}")
                try:
                    vec = bge_m3.encode_one(f"{entry.name}\n{entry.definition}")
                    wstore.upsert_qdrant(entry, vec)
                except Exception as e:
                    log.warning(f"qdrant mirror skipped: {e}")
                created += 1
            else:
                skipped += 1

    log.info(f"wiki update for {paper_id}: created={created} patched={patched} skipped={skipped}")
    return {"paper_id": paper_id, "created": created, "patched": patched, "skipped": skipped}
