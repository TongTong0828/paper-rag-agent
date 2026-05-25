"""Shared helpers used by all deliverable generators.

All four generators (survey_md, pptx, docx, latex_bib) need:
  - Per-paper deep-dive summary (motivation / method / results / limitations)
  - Paper metadata (title, authors, arxiv_id, year)
  - Aggregate citation list with chunk_id → human-readable mapping

This module factors that out so generators stay single-responsibility.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class PaperBundle:
    """Everything a deliverable generator needs about one paper."""

    paper_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    arxiv_id: str | None = None
    summary: str = ""                 # motivation/method/results/limitations
    citations: list[str] = field(default_factory=list)
    abstain: dict[str, Any] = field(default_factory=dict)


_SUMMARY_PROMPT = (
    "Summarize this paper in <=200 words covering, in this order: "
    "(1) motivation, (2) main method, (3) key results, (4) limitations. "
    "Use ONLY the provided evidence; if some aspect isn't covered by the "
    "evidence, say so explicitly. After every factual statement add "
    "[chunk:<id>] citing the supporting chunk."
)


def fetch_paper_meta(paper_id: str) -> dict[str, Any]:
    """Read paper row from sqlite_store. Returns {} on miss."""
    try:
        from ..store import sqlite_store
        row = sqlite_store.get_paper(paper_id)
    except Exception as e:
        log.warning("fetch_paper_meta(%s) failed: %s", paper_id, e)
        return {}
    if row is None:
        return {}

    # SQLModel row → dict (handles both dict-like and attr-like)
    if hasattr(row, "model_dump"):
        d = row.model_dump()
    else:
        d = dict(row) if isinstance(row, dict) else vars(row)

    # authors_json → list
    authors = []
    raw = d.get("authors_json")
    if isinstance(raw, str) and raw:
        try:
            authors = json.loads(raw)
        except json.JSONDecodeError:
            authors = []
    return {
        "paper_id": d.get("paper_id", paper_id),
        "title": d.get("title", "") or "",
        "authors": authors if isinstance(authors, list) else [],
        "year": d.get("year"),
        "arxiv_id": d.get("arxiv_id"),
        "abstract": d.get("abstract"),
    }


def fetch_paper_bundle(paper_id: str, *, run_qa: bool = True) -> PaperBundle:
    """Build a complete :class:`PaperBundle` for one paper.

    `run_qa=True` triggers a per-paper qa_agentic call to produce the deep-dive
    summary; pass False if you only need metadata (e.g. latex_bib).
    """
    meta = fetch_paper_meta(paper_id)
    if not meta:
        # Paper missing: return a stub bundle so the deliverable generator
        # can still emit a "paper not found" placeholder rather than crash.
        return PaperBundle(paper_id=paper_id, title=f"(unknown: {paper_id})")

    bundle = PaperBundle(
        paper_id=meta["paper_id"],
        title=meta["title"],
        authors=meta["authors"],
        year=meta.get("year"),
        arxiv_id=meta.get("arxiv_id"),
    )
    if not run_qa:
        return bundle

    try:
        from ..rag.qa_agentic import answer
        out = answer(_SUMMARY_PROMPT, paper_ids=[paper_id])
    except Exception as e:
        log.warning("paper summary failed for %s: %s", paper_id, e)
        return bundle

    bundle.summary = out.get("answer", "")
    bundle.citations = list(out.get("citations", []))
    bundle.abstain = (out.get("trace") or {}).get("abstain") or {}
    return bundle


def aggregate_citations(bundles: list[PaperBundle]) -> dict[str, dict[str, Any]]:
    """Build a chunk_id → {paper_id, paper_title, arxiv_id} map.

    Used by all generators to render the References section. Iterates
    bundles in order so chunk_ids dedup to the first paper that cited them.
    """
    out: dict[str, dict[str, Any]] = {}
    for b in bundles:
        for cid in b.citations:
            if cid in out:
                continue
            out[cid] = {
                "chunk_id": cid,
                "paper_id": b.paper_id,
                "paper_title": b.title,
                "arxiv_id": b.arxiv_id,
                "year": b.year,
            }
    return out


def collect_metadata(bundles: list[PaperBundle], trace_id: str = "") -> dict[str, Any]:
    """Build the metadata block returned to API callers."""
    abstain_decisions = [
        {"paper_id": b.paper_id, **b.abstain}
        for b in bundles
        if b.abstain  # only papers that hit the abstain gate
    ]
    return {
        "n_papers": len(bundles),
        "n_citations": sum(len(b.citations) for b in bundles),
        "papers_skipped": [b.paper_id for b in bundles if not b.summary],
        "abstain_decisions": abstain_decisions,
        "trace_id": trace_id,
    }


__all__ = [
    "PaperBundle",
    "aggregate_citations",
    "collect_metadata",
    "fetch_paper_bundle",
    "fetch_paper_meta",
]
