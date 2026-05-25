"""High-level build_chunks orchestrator.

Input: paper_id, parsed markdown path, title.
Output: (sections, chunks) ready to upsert into SQLite + Qdrant.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..utils.logger import get_logger
from . import multimodal_chunker as mm
from .contextual import with_context
from .section_splitter import split_sections
from .text_chunker import chunk_text


log = get_logger("chunk.builder")


def _chunk_id(paper_id: str, section_idx: int, kind: str, ord_: int) -> str:
    base = f"{paper_id}::{section_idx}::{kind}::{ord_}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


def _section_id(paper_id: str, idx: int) -> str:
    return hashlib.sha1(f"{paper_id}::sec::{idx}".encode("utf-8")).hexdigest()[:16]


def build_chunks(paper_id: str, parsed_dir: Path, *, title: str) -> tuple[list[dict], list[dict]]:
    md_path = parsed_dir / "paper.md"
    md = md_path.read_text(encoding="utf-8")

    sections: list[dict] = []
    chunks: list[dict] = []

    for raw_sec in split_sections(md):
        sec_id = _section_id(paper_id, raw_sec.idx)
        sections.append(
            {
                "section_id": sec_id,
                "paper_id": paper_id,
                "idx": raw_sec.idx,
                "name": raw_sec.name,
            }
        )

        for i, tc in enumerate(chunk_text(raw_sec.body)):
            ch_id = _chunk_id(paper_id, raw_sec.idx, "text", i)
            chunks.append(
                {
                    "chunk_id": ch_id,
                    "paper_id": paper_id,
                    "section_id": sec_id,
                    "section": raw_sec.name,
                    "section_idx": raw_sec.idx,
                    "modality": "text",
                    "page": None,
                    "text": tc.text,
                    "context_text": with_context(tc.text, title=title, section=raw_sec.name),
                    "title": title,
                    "neighbors": [],
                }
            )

        for kind, items in (
            ("figure", mm.extract_figures(raw_sec.body)),
            ("table", mm.extract_tables(raw_sec.body)),
            ("formula", mm.extract_formulas(raw_sec.body)),
        ):
            for j, mmc in enumerate(items):
                ch_id = _chunk_id(paper_id, raw_sec.idx, kind, j)
                chunks.append(
                    {
                        "chunk_id": ch_id,
                        "paper_id": paper_id,
                        "section_id": sec_id,
                        "section": raw_sec.name,
                        "section_idx": raw_sec.idx,
                        "modality": mmc.modality,
                        "page": None,
                        "text": mmc.text,
                        "context_text": with_context(mmc.text, title=title, section=raw_sec.name),
                        "title": title,
                        "neighbors": [],
                    }
                )

    log.info(f"built {len(sections)} sections, {len(chunks)} chunks for {paper_id}")
    return sections, chunks
