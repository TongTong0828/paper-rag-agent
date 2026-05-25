"""SQLite storage via sqlmodel."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("store.sqlite")


class Paper(SQLModel, table=True):
    paper_id: str = Field(primary_key=True)
    title: str = ""
    authors_json: str = "[]"
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    arxiv_version: str | None = None  # e.g. "v2"; paper_id keeps the version-stripped form
    abstract: str | None = None
    title_norm: str | None = Field(default=None, index=True)
    status: str = Field(default="created", index=True)
    parsed_with: str | None = None
    error: str | None = None
    # ADR-0015: user-scoped library. "system" = shared/public papers visible
    # to everyone. NULL is treated as "system" for backward compatibility
    # with M0-M7 single-user data.
    user_id: str | None = Field(default="system", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Section(SQLModel, table=True):
    section_id: str = Field(primary_key=True)
    paper_id: str = Field(index=True)
    idx: int
    name: str
    page_start: int | None = None
    page_end: int | None = None


class Chunk(SQLModel, table=True):
    chunk_id: str = Field(primary_key=True)
    paper_id: str = Field(index=True)
    section_id: str | None = Field(default=None, index=True)
    modality: str = "text"
    page: int | None = None
    text: str = ""
    context_text: str = ""
    neighbors_json: str = "[]"


class IngestRun(SQLModel, table=True):
    """One row per pipeline step. Use to debug failed ingests."""

    __tablename__ = "ingest_runs"
    id: int | None = Field(default=None, primary_key=True)
    paper_id: str = Field(index=True)
    step: str = ""           # fetched | parsed | chunked | embedded | indexed | done | failed
    status: str = "ok"       # ok | error
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    error: str | None = None


_ENGINE = None


def _apply_pragmas(dbapi_conn, _connection_record) -> None:
    """Set per-connection PRAGMAs for safer concurrent writes.

    - WAL: readers don't block writers, writers don't block readers.
    - busy_timeout: 5s wait instead of immediate `database is locked`.
    - synchronous=NORMAL: WAL-safe, ~2x faster than FULL.
    - foreign_keys=ON: enforce referential integrity (off by default).
    """
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        cur.close()


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import event

        c = cfg.load()
        Path(c.paths.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{c.paths.sqlite_path}"
        _ENGINE = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        event.listen(_ENGINE, "connect", _apply_pragmas)
        SQLModel.metadata.create_all(_ENGINE)
        log.info(f"sqlite engine ready at {c.paths.sqlite_path} (WAL+busy_timeout)")
    return _ENGINE


def upsert_paper(meta: dict[str, Any], status: str = "created") -> None:
    from .. import ingest  # for typing only
    _ = ingest

    engine = get_engine()
    with Session(engine) as s:
        existing = s.get(Paper, meta["paper_id"])
        if existing is None:
            extra = meta.get("extra") or {}
            row = Paper(
                paper_id=meta["paper_id"],
                title=meta.get("title", "") or "",
                authors_json=json.dumps(meta.get("authors", []), ensure_ascii=False),
                year=meta.get("year"),
                venue=meta.get("venue"),
                doi=meta.get("doi"),
                arxiv_id=meta.get("arxiv_id"),
                arxiv_version=extra.get("arxiv_version"),
                abstract=meta.get("abstract"),
                status=status,
            )
            from ..ingest.dedup import normalize_title

            row.title_norm = normalize_title(row.title) if row.title else None
            s.add(row)
        else:
            extra = meta.get("extra") or {}
            for k in ("title", "year", "venue", "doi", "arxiv_id", "abstract"):
                v = meta.get(k)
                if v is not None:
                    setattr(existing, k, v)
            if "authors" in meta:
                existing.authors_json = json.dumps(meta["authors"], ensure_ascii=False)
            if extra.get("arxiv_version"):
                existing.arxiv_version = extra["arxiv_version"]
            existing.status = status
            existing.updated_at = datetime.utcnow()
            s.add(existing)
        s.commit()


def set_status(paper_id: str, status: str, error: str | None = None,
               parsed_with: str | None = None) -> None:
    engine = get_engine()
    with Session(engine) as s:
        row = s.get(Paper, paper_id)
        if row is None:
            log.warning(f"set_status: paper not found {paper_id}")
            return
        row.status = status
        if error is not None:
            row.error = error
        if parsed_with is not None:
            row.parsed_with = parsed_with
        row.updated_at = datetime.utcnow()
        s.add(row)
        s.commit()


def get_paper(paper_id: str) -> Paper | None:
    engine = get_engine()
    with Session(engine) as s:
        return s.get(Paper, paper_id)


def record_ingest_step(paper_id: str, step: str, *, status: str = "ok",
                       error: str | None = None) -> int:
    """Insert an `ingest_runs` row. Returns row id (so callers can update finished_at)."""
    engine = get_engine()
    with Session(engine) as s:
        row = IngestRun(paper_id=paper_id, step=step, status=status, error=error)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id  # type: ignore[return-value]


def finish_ingest_step(run_id: int, *, status: str = "ok", error: str | None = None) -> None:
    engine = get_engine()
    with Session(engine) as s:
        row = s.get(IngestRun, run_id)
        if row is None:
            return
        row.finished_at = datetime.utcnow()
        row.status = status
        if error is not None:
            row.error = error
        s.add(row)
        s.commit()


def find_existing_paper(*, doi: str | None = None, arxiv_id: str | None = None,
                        title_norm: str | None = None) -> Paper | None:
    """Cross-source dedup probe.

    Resolution order: DOI > arxiv_id > (title_norm, first_author).
    Returns the first match or None.
    """
    engine = get_engine()
    with Session(engine) as s:
        if doi:
            rows = list(s.exec(select(Paper).where(Paper.doi == doi)))
            if rows:
                return rows[0]
        if arxiv_id:
            rows = list(s.exec(select(Paper).where(Paper.arxiv_id == arxiv_id)))
            if rows:
                return rows[0]
        if title_norm:
            rows = list(s.exec(select(Paper).where(Paper.title_norm == title_norm)))
            if rows:
                return rows[0]
    return None


def upsert_sections_and_chunks(paper_id: str, sections: list[dict], chunks: list[dict]) -> None:
    engine = get_engine()
    with Session(engine) as s:
        for sec in sections:
            existing = s.get(Section, sec["section_id"])
            if existing:
                continue
            s.add(Section(**sec))
        for ch in chunks:
            existing = s.get(Chunk, ch["chunk_id"])
            payload = dict(ch)
            payload.setdefault("neighbors_json", json.dumps(payload.pop("neighbors", []), ensure_ascii=False))
            if existing:
                continue
            s.add(Chunk(**payload))
        s.commit()


def list_chunks_for_papers(paper_ids: list[str]) -> list[Chunk]:
    engine = get_engine()
    with Session(engine) as s:
        stmt = select(Chunk).where(Chunk.paper_id.in_(paper_ids))
        return list(s.exec(stmt))


def get_chunk(chunk_id: str) -> Chunk | None:
    engine = get_engine()
    with Session(engine) as s:
        return s.get(Chunk, chunk_id)
