"""Wiki SQLite + Qdrant store helpers (sqlmodel-bound)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field as SQLField, Session, SQLModel, select

from .. import config as cfg
from ..utils.logger import get_logger
from .schema import Variant, WikiEntry, normalize_name


log = get_logger("wiki.store")


class WikiEntryRow(SQLModel, table=True):
    __tablename__ = "wiki_entries"
    entry_id: str = SQLField(primary_key=True)
    name: str
    name_norm: str = SQLField(index=True)
    category: str = "concept"
    definition: str = ""
    aliases_json: str = "[]"
    key_papers_json: str = "[]"
    variants_json: str = "[]"
    related_json: str = "[]"
    open_problems_json: str = "[]"
    evidence_chunks_json: str = "[]"
    version: int = 1
    updated_at: datetime = SQLField(default_factory=datetime.utcnow)
    lock_until: datetime | None = None


class WikiVersionRow(SQLModel, table=True):
    __tablename__ = "wiki_versions"
    id: int | None = SQLField(default=None, primary_key=True)
    entry_id: str = SQLField(index=True)
    version: int
    content_json: str = ""
    reason: str = ""
    created_at: datetime = SQLField(default_factory=datetime.utcnow)


def _to_row(entry: WikiEntry) -> WikiEntryRow:
    return WikiEntryRow(
        entry_id=entry.entry_id,
        name=entry.name,
        name_norm=normalize_name(entry.name),
        category=entry.category,
        definition=entry.definition,
        aliases_json=json.dumps(entry.aliases, ensure_ascii=False),
        key_papers_json=json.dumps(entry.key_papers, ensure_ascii=False),
        variants_json=json.dumps([v.model_dump() for v in entry.variants], ensure_ascii=False),
        related_json=json.dumps(entry.related, ensure_ascii=False),
        open_problems_json=json.dumps(entry.open_problems, ensure_ascii=False),
        evidence_chunks_json=json.dumps(entry.evidence_chunks, ensure_ascii=False),
        version=entry.version,
        updated_at=entry.updated_at,
        lock_until=entry.lock_until,
    )


def _from_row(row: WikiEntryRow) -> WikiEntry:
    return WikiEntry(
        entry_id=row.entry_id,
        name=row.name,
        aliases=json.loads(row.aliases_json or "[]"),
        category=row.category,  # type: ignore[arg-type]
        definition=row.definition,
        key_papers=json.loads(row.key_papers_json or "[]"),
        variants=[Variant(**v) for v in json.loads(row.variants_json or "[]")],
        related=json.loads(row.related_json or "[]"),
        open_problems=json.loads(row.open_problems_json or "[]"),
        evidence_chunks=json.loads(row.evidence_chunks_json or "[]"),
        version=row.version,
        updated_at=row.updated_at,
        lock_until=row.lock_until,
    )


def _engine():
    from ..store.sqlite_store import get_engine

    return get_engine()


def get_entry(entry_id: str) -> WikiEntry | None:
    with Session(_engine()) as s:
        row = s.get(WikiEntryRow, entry_id)
        return _from_row(row) if row else None


def get_by_name(name: str) -> WikiEntry | None:
    norm = normalize_name(name)
    with Session(_engine()) as s:
        rows = list(s.exec(select(WikiEntryRow).where(WikiEntryRow.name_norm == norm)))
        return _from_row(rows[0]) if rows else None


def list_all() -> list[WikiEntry]:
    with Session(_engine()) as s:
        rows = list(s.exec(select(WikiEntryRow)))
    return [_from_row(r) for r in rows]


def upsert_entry(entry: WikiEntry, *, reason: str = "") -> WikiEntry:
    with Session(_engine()) as s:
        existing = s.get(WikiEntryRow, entry.entry_id)
        if existing is None:
            row = _to_row(entry)
            s.add(row)
        else:
            entry.version = existing.version + 1
            entry.updated_at = datetime.utcnow()
            new = _to_row(entry)
            for col in (
                "name", "name_norm", "category", "definition",
                "aliases_json", "key_papers_json", "variants_json",
                "related_json", "open_problems_json", "evidence_chunks_json",
                "version", "updated_at", "lock_until",
            ):
                setattr(existing, col, getattr(new, col))
            s.add(existing)
        s.add(
            WikiVersionRow(
                entry_id=entry.entry_id,
                version=entry.version,
                content_json=json.dumps(entry.model_dump(mode="json"), ensure_ascii=False),
                reason=reason,
            )
        )
        s.commit()
    log.info(f"wiki upsert {entry.entry_id} v{entry.version} ({reason})")
    return entry


def upsert_qdrant(entry: WikiEntry, vector: list[float]) -> None:
    import hashlib
    from qdrant_client.http import models as qm

    from ..store.qdrant_store import get_client

    client = get_client()
    coll = cfg.load().qdrant.collection_wiki
    pid = int(hashlib.sha1(entry.entry_id.encode("utf-8")).hexdigest()[:16], 16)
    payload: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "name": entry.name,
        "category": entry.category,
        "version": entry.version,
        "definition_excerpt": (entry.definition or "")[:500],
    }
    client.upsert(
        collection_name=coll,
        points=[qm.PointStruct(id=pid, vector=vector, payload=payload)],
        wait=True,
    )


def search_qdrant(query_vec: list[float], top_k: int = 5) -> list[dict]:
    from ..store.qdrant_store import get_client

    client = get_client()
    coll = cfg.load().qdrant.collection_wiki
    if hasattr(client, "query_points"):
        qres = client.query_points(
            collection_name=coll,
            query=query_vec,
            limit=top_k,
            with_payload=True,
        )
        res = qres.points if hasattr(qres, "points") else qres
    else:
        res = client.search(
            collection_name=coll,
            query_vector=query_vec,
            limit=top_k,
            with_payload=True,
        )
    out = []
    for hit in res:
        d = dict(hit.payload or {})
        d["score"] = float(hit.score)
        out.append(d)
    return out
