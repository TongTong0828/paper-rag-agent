"""Format dispatcher for paper_rag.deliver (M10 / ADR-0016).

Routes a deliver request to the right generator. Public entry point used by
both the gateway router and the LangChain tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_FORMATS = ("markdown_survey", "pptx", "docx", "latex_bib", "pdf")


@dataclass
class DeliverableResult:
    """Result of a successful deliver call."""

    format: str
    filename: str
    content_bytes: bytes
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DeliverError(ValueError):
    """Raised when the deliver request is invalid (bad format, no papers, etc.)."""


def dispatch(
    format: str,
    paper_ids: list[str],
    *,
    title: str | None = None,
    options: dict[str, Any] | None = None,
    user_id: str = "system",
) -> DeliverableResult:
    """Generate the deliverable artifact.

    Parameters
    ----------
    format : one of SUPPORTED_FORMATS
    paper_ids : non-empty list
    title : optional human-readable title
    options : format-specific options (see PRD § 5.1)
    user_id : invoker (forwarded to filename + retrieval scoping later)
    """
    if format not in SUPPORTED_FORMATS:
        raise DeliverError(
            f"unsupported format {format!r}; expected one of {SUPPORTED_FORMATS}"
        )
    if not paper_ids:
        raise DeliverError("paper_ids must be non-empty")

    options = options or {}

    if format == "markdown_survey":
        from .survey_md import generate as gen
        return gen(paper_ids, title=title, **options)
    if format == "pptx":
        from .pptx import generate as gen
        return gen(paper_ids, title=title, **options)
    if format == "docx":
        from .docx import generate as gen
        return gen(paper_ids, title=title, **options)
    if format == "latex_bib":
        from .latex_bib import generate as gen
        return gen(paper_ids, title=title, **options)
    if format == "pdf":
        from .pdf import generate as gen
        return gen(paper_ids, title=title, **options)
    raise DeliverError(f"unreachable: {format}")  # pragma: no cover


__all__ = ["dispatch", "DeliverableResult", "DeliverError", "SUPPORTED_FORMATS"]
