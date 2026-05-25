"""Markdown survey generator (M10 / ADR-0016).

Pipeline
--------
1. For each paper_id → fetch metadata + run `qa_agentic.answer()` for a
   200-word deep-dive summary (motivation / method / results / limitations).
2. One cross-paper synthesis LLM call → outline + per-section paragraphs.
3. Render Markdown from a Jinja-style template (no Jinja dep — string.Template
   suffices for our needs).
4. Append References section mapping chunk_id → paper title + arxiv link.

Cost
----
N + 1 LLM calls per survey (N = number of papers). Each summary ≈ 30s on
Qwen3.5-plus → 10 papers ≈ 5 min. Caching the summaries (qa_cache) makes
repeated surveys near-instant.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config as cfg
from ._common import (
    PaperBundle,
    aggregate_citations,
    collect_metadata,
    fetch_paper_bundle,
)
from .dispatch import DeliverableResult

log = logging.getLogger(__name__)


_SYNTHESIS_PROMPT_TEMPLATE = """You are an academic writer producing a literature survey.

Below are deep-dive summaries of {n} papers. Synthesize them into a coherent
survey with three sections: Introduction, Methods Comparison, Open Problems.

CRITICAL CITATION RULES:
- Use ONLY [chunk:<id>] format that already appears in the summaries.
- DO NOT invent new chunk_ids. DO NOT use numeric [1], [2] or (Author Year).
- If you can't substantiate a claim with an existing [chunk:<id>], rephrase or omit.

Per-paper summaries:
---
{summaries}
---

Write the survey in Markdown. Each section should be 2-4 paragraphs. Total
target length ~{max_words} words. Output ONLY the section bodies, prefixed
with these exact headings:

## Introduction

## Methods Comparison

## Open Problems
"""


_REF_LINE_TEMPLATE = "- `[chunk:{chunk_id}]` — {title}{arxiv_part}{year_part}"


def _ensure_jinja_safe(s: str) -> str:
    """Escape backslashes so Markdown rendering is stable."""
    return s.replace("\\", "\\\\")


def _format_summaries_for_prompt(bundles: list[PaperBundle]) -> str:
    parts = []
    for b in bundles:
        if not b.summary:
            continue
        parts.append(
            f"Paper: {b.title} ({b.paper_id})\n"
            f"Summary:\n{b.summary}\n"
        )
    return "\n---\n".join(parts)


def _synthesize_outline(bundles: list[PaperBundle], max_words: int) -> str:
    """One LLM call → 3-section Markdown body."""
    summaries = _format_summaries_for_prompt(bundles)
    if not summaries.strip():
        return (
            "## Introduction\n\n"
            "(No papers were successfully retrieved. Refer to the abstain "
            "metadata for details.)\n\n"
            "## Methods Comparison\n\n(No content.)\n\n"
            "## Open Problems\n\n(No content.)\n"
        )
    n = sum(1 for b in bundles if b.summary)
    prompt = _SYNTHESIS_PROMPT_TEMPLATE.format(
        n=n, summaries=summaries, max_words=max_words
    )
    try:
        from ..rag.llm import chat
        body = chat(
            [
                {"role": "system",
                 "content": "You are a precise academic writer. Strict citation discipline."},
                {"role": "user", "content": prompt},
            ],
            temperature=cfg.load().llm.temperatures.survey,
            max_tokens=4096,
        )
    except Exception as e:
        log.warning("synthesis LLM call failed: %s; falling back to stitched summaries", e)
        # Fallback: just stitch the summaries together. Better than nothing.
        body = (
            "## Introduction\n\n"
            "Synthesis LLM unavailable; the following are individual paper summaries.\n\n"
            "## Methods Comparison\n\n"
            + "\n\n---\n\n".join(b.summary for b in bundles if b.summary)
            + "\n\n## Open Problems\n\n"
            "(LLM unavailable for synthesis — review individual summaries above.)\n"
        )
    return body


def _render_references(bundles: list[PaperBundle]) -> str:
    """Render the References section.

    Two-tier rendering:
    1. Per-paper bibliographic entry (one line per paper)
    2. Per-chunk citation map (chunk:xxx → which paper) for full traceability
    """
    lines = ["## References", ""]
    # Tier 1: papers
    lines.append("### Papers")
    for i, b in enumerate(bundles, 1):
        authors_str = (", ".join(b.authors[:3]) + (" et al." if len(b.authors) > 3 else "")) if b.authors else "Unknown authors"
        year_str = f" ({b.year})" if b.year else ""
        arxiv_str = f". arXiv:{b.arxiv_id}" if b.arxiv_id else ""
        lines.append(f"{i}. {authors_str}{year_str}. *{b.title}*{arxiv_str}")
    lines.append("")
    # Tier 2: chunk citation map
    lines.append("### Citation Map (chunk_id → paper)")
    cite_map = aggregate_citations(bundles)
    if not cite_map:
        lines.append("(no citations were emitted)")
    for cid, info in cite_map.items():
        arxiv_part = f" arXiv:{info['arxiv_id']}" if info.get("arxiv_id") else ""
        year_part = f" ({info['year']})" if info.get("year") else ""
        lines.append(_REF_LINE_TEMPLATE.format(
            chunk_id=cid,
            title=info["paper_title"],
            arxiv_part=arxiv_part,
            year_part=year_part,
        ))
    lines.append("")
    return "\n".join(lines)


def _render_header(title: str, n_papers: int, n_citations: int) -> str:
    today = dt.date.today().isoformat()
    return (
        f"# {title}\n\n"
        f"> Generated by paper_rag on {today}. "
        f"{n_papers} papers, {n_citations} citations.\n\n"
    )


def generate(
    paper_ids: list[str],
    *,
    title: str | None = None,
    max_words: int = 5000,
    **_unused,
) -> DeliverableResult:
    """Generate a Markdown survey deliverable."""
    title = title or "Literature Survey"

    bundles = [fetch_paper_bundle(pid) for pid in paper_ids]

    body = _synthesize_outline(bundles, max_words=max_words)
    refs = _render_references(bundles)

    n_cites = sum(len(b.citations) for b in bundles)
    md = _render_header(title, len(bundles), n_cites) + body + "\n\n" + refs

    md = _ensure_jinja_safe(md)
    today = dt.date.today().isoformat()
    safe_title = title.lower().replace(" ", "_").replace("/", "_")[:60]

    return DeliverableResult(
        format="markdown_survey",
        filename=f"{safe_title}_{today}.md",
        content_bytes=md.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
        metadata=collect_metadata(bundles),
    )


__all__ = ["generate"]
