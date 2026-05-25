"""Wiki entry schema (pydantic logical model + helpers).

The SQLModel row class lives in `wiki.store` to keep this module
sqlmodel-free (lets us run pure-logic tests without DB deps).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


WikiCategory = Literal["concept", "method", "task", "dataset", "metric"]


class Variant(BaseModel):
    name: str
    summary: str
    paper_id: str | None = None


class WikiEntry(BaseModel):
    entry_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    category: WikiCategory = "concept"
    definition: str = ""
    key_papers: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    evidence_chunks: list[str] = Field(default_factory=list)
    version: int = 1
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    lock_until: datetime | None = None


def normalize_name(name: str) -> str:
    return "".join(c.lower() for c in name if c.isalnum())


def make_entry_id(name: str) -> str:
    return f"concept:{normalize_name(name)}"
