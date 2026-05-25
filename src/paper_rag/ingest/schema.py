"""Common dataclasses for ingest pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PaperMeta(BaseModel):
    """Minimal metadata produced by an ingest source."""

    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    urls: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "unknown"
    extra: dict[str, Any] = Field(default_factory=dict)


class FetchResult(BaseModel):
    """Output of a PaperSource.fetch() call."""

    meta: PaperMeta
    pdf_path: str
