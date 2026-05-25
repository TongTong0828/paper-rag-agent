"""paper_rag.deliver — produce ready-to-use artifacts (Markdown survey, PPT,
Word, LaTeX bib).

Module layout
-------------
- ``dispatch(format, paper_ids, title=None, options=None)`` — public entry
  point used by both the gateway router and the LangChain tool.
- ``survey_md`` — Markdown survey (cross-paper synthesis).
- ``pptx``      — 12-slide reading-group deck (uses python-pptx).
- ``docx``      — formatted Word doc (uses python-docx).
- ``latex_bib`` — zip of references.bib + related_work.tex.

Each generator returns a :class:`DeliverableResult`:

    {
        "format": str,
        "filename": str,
        "content_bytes": bytes,
        "content_type": str,
        "metadata": {
            "n_papers": int,
            "n_citations": int,
            "abstain_decisions": list[dict],
            "trace_id": str,
        },
    }

ADR-0016 records the rationale.
"""

from __future__ import annotations

from .dispatch import DeliverableResult, dispatch

__all__ = ["DeliverableResult", "dispatch"]
