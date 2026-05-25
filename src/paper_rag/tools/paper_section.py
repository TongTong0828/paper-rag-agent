"""paper_section tool: fetch a section of a specific paper from SQLite."""

from __future__ import annotations

from ..utils.logger import get_logger
from ._schema import PaperSectionInput


log = get_logger("tool.paper_section")


def paper_section(input: PaperSectionInput) -> dict:
    from sqlmodel import Session, select

    from ..store.sqlite_store import Chunk, Section, get_engine

    engine = get_engine()
    target = input.section_name.strip().lower()
    with Session(engine) as s:
        secs = list(
            s.exec(
                select(Section).where(Section.paper_id == input.paper_id)
            )
        )
        match = next((sec for sec in secs if target in sec.name.lower()), None)
        if match is None:
            return {"paper_id": input.paper_id, "section": None, "chunks": []}
        chunks = list(
            s.exec(
                select(Chunk).where(Chunk.section_id == match.section_id).order_by(Chunk.chunk_id)
            )
        )

    return {
        "paper_id": input.paper_id,
        "section": {"name": match.name, "idx": match.idx},
        "chunks": [
            {"chunk_id": c.chunk_id, "modality": c.modality, "page": c.page, "text": c.text}
            for c in chunks
        ],
    }
