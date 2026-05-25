"""BibTeX export tool.

Given a list of paper_ids, produce a BibTeX block that can be appended to a
LaTeX or Markdown report. Uses metadata from SQLite — never hits external
APIs at call time, so it's fast and offline.

Cite key strategy:
    arxiv:2310.11511      -> arxiv2310_11511
    doi:10.1109/abc.2024  -> doi_10_1109_abc_2024
    sha1:deadbeef...      -> sha_deadbeef
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class BibtexExportInput(BaseModel):
    paper_ids: list[str] = Field(..., description="Paper ids to export")


_NON_KEY_RE = re.compile(r"[^A-Za-z0-9_]+")


def _cite_key(paper_id: str) -> str:
    return _NON_KEY_RE.sub("_", paper_id).strip("_")


def _entry_type(paper_id: str, year: int | None) -> str:
    if paper_id.startswith("arxiv:"):
        return "misc"
    return "article"


def _bibtex_one(paper) -> str:
    pid = paper.paper_id
    key = _cite_key(pid)
    typ = _entry_type(pid, paper.year)
    fields = []
    if paper.title:
        fields.append(f'  title     = {{{_escape_bibtex(paper.title)}}}')
    import json

    authors = json.loads(paper.authors_json or "[]")
    if authors:
        fields.append(f'  author    = {{{ " and ".join(_escape_bibtex(a) for a in authors)}}}')
    if paper.year:
        fields.append(f'  year      = {{{paper.year}}}')
    if paper.venue:
        fields.append(f'  journal   = {{{_escape_bibtex(paper.venue)}}}')
    if paper.doi:
        fields.append(f'  doi       = {{{paper.doi}}}')
    if paper.arxiv_id:
        full_arxiv = paper.arxiv_id + (paper.arxiv_version or "")
        fields.append(f'  eprint    = {{{full_arxiv}}}')
        fields.append('  archivePrefix = {arXiv}')
        fields.append(f'  url       = {{https://arxiv.org/abs/{paper.arxiv_id}}}')
    body = ",\n".join(fields)
    return f"@{typ}{{{key},\n{body}\n}}"


def _escape_bibtex(s: str) -> str:
    return s.replace("{", r"\{").replace("}", r"\}")


def export_bibtex(input: BibtexExportInput) -> dict:
    """Return a BibTeX string + skipped paper_ids (not found)."""
    from sqlmodel import Session

    from ..store.sqlite_store import Paper, get_engine

    engine = get_engine()
    found, missing = [], []
    with Session(engine) as s:
        for pid in input.paper_ids:
            p = s.get(Paper, pid)
            if p is None:
                missing.append(pid)
            else:
                found.append(p)

    blocks = [_bibtex_one(p) for p in found]
    return {
        "bibtex": "\n\n".join(blocks),
        "n_exported": len(found),
        "missing": missing,
    }
