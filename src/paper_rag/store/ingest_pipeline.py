"""End-to-end ingest pipeline.

State machine:
    created -> fetched -> parsed -> chunked -> embedded -> indexed -> done
                        \\                                            ^
                         +----- failed (with `error` recorded) -------+

Each step is recorded in `ingest_runs` for debuggability.

Cross-source dedup: before insert we probe (DOI > arxiv_id > title_norm).
Hits return the existing paper_id back to the caller (signalled via the
`merged_into` field of the result), and we skip re-ingesting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..chunk.builder import build_chunks
from ..embed import bge_m3
from ..ingest.dedup import normalize_title
from ..ingest.schema import FetchResult
from ..parse.dispatcher import parse_pdf
from ..utils.logger import get_logger
from . import qdrant_store, sqlite_store


log = get_logger("store.ingest")


def _step(paper_id: str, name: str, fn) -> Any:
    """Run a pipeline step, record start/finish to ingest_runs, raise on failure."""
    run_id = sqlite_store.record_ingest_step(paper_id, name)
    try:
        out = fn()
    except Exception as e:
        sqlite_store.finish_ingest_step(run_id, status="error", error=str(e))
        sqlite_store.set_status(paper_id, "failed", error=f"{name}: {e}")
        raise
    sqlite_store.finish_ingest_step(run_id, status="ok")
    return out


def _resolve_dedup(meta) -> str | None:
    """Return existing paper_id if this paper already exists under another id."""
    title_norm = normalize_title(meta.title) if meta.title else None
    existing = sqlite_store.find_existing_paper(
        doi=meta.doi,
        arxiv_id=meta.arxiv_id,
        title_norm=title_norm,
    )
    if existing and existing.paper_id != meta.paper_id:
        return existing.paper_id
    return None


def ingest(result: FetchResult, *, force: bool = False) -> dict[str, Any]:
    paper_id = result.meta.paper_id

    # Cross-source dedup probe (doesn't trip on same paper_id)
    merged_into = _resolve_dedup(result.meta)
    if merged_into and not force:
        log.info(f"dedup: {paper_id} already exists as {merged_into}, skip")
        return {
            "paper_id": paper_id,
            "status": "skipped",
            "merged_into": merged_into,
            "reason": "dedup",
        }

    if not force:
        existing = sqlite_store.get_paper(paper_id)
        if existing and existing.status == "done":
            log.info(f"{paper_id} already done, skip")
            return {"paper_id": paper_id, "status": "skipped", "reason": "done"}

    sqlite_store.upsert_paper(result.meta.model_dump(mode="json"), status="fetched")

    parsed, parser_name = _step(
        paper_id, "parse",
        lambda: parse_pdf(paper_id, result.pdf_path),
    )
    sqlite_store.set_status(paper_id, "parsed", parsed_with=parser_name)

    sections, chunks = _step(
        paper_id, "chunk",
        lambda: build_chunks(paper_id, Path(parsed), title=result.meta.title),
    )
    sqlite_store.upsert_sections_and_chunks(paper_id, sections, chunks)

    # Section-completeness grading. Tag parsed_with with the quality so we
    # can filter problematic parses later (e.g. "mineru+broken").
    try:
        from ..chunk.sanity import grade_sections

        quality = grade_sections([sec.get("name", "") for sec in sections])
        sqlite_store.set_status(
            paper_id, "parsed",
            parsed_with=f"{parser_name}+{quality}",
        )
        log.info(f"section quality for {paper_id}: {quality}")
    except Exception as e:
        log.warning(f"section grading skipped: {e}")
    sqlite_store.set_status(paper_id, "chunked")

    if not chunks:
        sqlite_store.set_status(paper_id, "failed", error="chunk: empty (parser produced no chunks)")
        return {"paper_id": paper_id, "status": "failed", "reason": "no_chunks"}

    vectors = _step(
        paper_id, "embed",
        lambda: bge_m3.encode([c["context_text"] for c in chunks]),
    )
    sqlite_store.set_status(paper_id, "embedded")

    _step(
        paper_id, "index",
        lambda: qdrant_store.upsert_chunks(chunks, vectors),
    )
    sqlite_store.set_status(paper_id, "indexed")
    sqlite_store.set_status(paper_id, "done")

    # Enqueue async wiki update (non-blocking). Wiki disabled in config? The
    # worker still runs, but `on_paper_indexed` short-circuits — cheap.
    try:
        from ..wiki.queue import submit_paper_indexed

        submit_paper_indexed(paper_id)
        wiki_report = {"queued": True}
    except Exception as e:
        log.warning(f"wiki enqueue failed (non-fatal): {e}")
        wiki_report = {"error": str(e)}

    return {"paper_id": paper_id, "status": "done", "chunks": len(chunks), "wiki": wiki_report}
