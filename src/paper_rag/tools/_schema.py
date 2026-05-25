"""Pydantic schemas for tools exposed to LLM agents."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaperSearchInput(BaseModel):
    query: str = Field(..., description="Natural-language search query")
    top_k: int = Field(8, ge=1, le=30)
    year_min: int | None = None
    year_max: int | None = None


class PaperQAInput(BaseModel):
    question: str
    paper_ids: list[str] | None = None
    top_k: int = Field(8, ge=1, le=20)


class PaperSectionInput(BaseModel):
    paper_id: str
    section_name: str


class PaperCompareInput(BaseModel):
    paper_ids: list[str]
    dimensions: list[str] = Field(
        default_factory=lambda: ["motivation", "method", "results", "limitations"]
    )


class WikiLookupInput(BaseModel):
    concept: str
