"""paper_rag HTTP router (M8 service-ization, ADR-0015).

Exposes the standalone `paper_rag` package as first-class HTTP endpoints on
the DeerFlow gateway. Re-uses the same underlying functions (`qa_agentic`,
`qa_stream`, `paper_index`) that lead_agent's LangChain tools call — only
the outer adapter differs (router vs @tool).

Endpoints
---------
POST   /api/paper_rag/qa                Streaming SSE Q&A
POST   /api/paper_rag/qa/sync           Synchronous Q&A
GET    /api/paper_rag/papers            List user's papers
POST   /api/paper_rag/papers/ingest     Ingest by arxiv id / pdf url
GET    /api/paper_rag/wiki/{paper_id}   Wiki entry for a paper

Auth
----
All endpoints require a valid BetterAuth session (see middleware/auth.py).
The middleware injects ``request.state.user_id`` which routers consume via
``Depends(get_current_user_id)``.

Notes
-----
- paper_rag is a *separate* Python package not in the backend monorepo. We
  use the same path-discovery trick as the LangChain tool adapter (env
  PAPER_RAG_HOME, then walk up to find sibling paper_rag/src).
- Heavy imports (qa_agentic, qdrant) are lazy — keeps gateway start fast.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper_rag", tags=["paper_rag"])


# ---------------------------------------------------------------------------
# Lazy import of the standalone paper_rag package
# ---------------------------------------------------------------------------


def _ensure_paper_rag_importable() -> None:
    """Make the standalone paper_rag package importable.

    Resolution order:
      1. Already importable (e.g. pip install -e ..) -> done.
      2. Env var PAPER_RAG_HOME -> use that path (must contain src/paper_rag).
      3. Walk up parents from this file to find sibling paper_rag/src/paper_rag.
    """
    try:
        import paper_rag  # noqa: F401

        return
    except ImportError:
        pass

    home = os.environ.get("PAPER_RAG_HOME")
    if home:
        candidate = Path(home).expanduser().resolve() / "src"
        if not candidate.is_dir():
            # PAPER_RAG_HOME may already point at src/
            candidate = Path(home).expanduser().resolve()
    else:
        here = Path(__file__).resolve()
        candidate = None
        for parent in here.parents:
            maybe = parent / "paper_rag" / "src" / "paper_rag"
            if maybe.is_dir():
                candidate = parent / "paper_rag" / "src"
                break

    if candidate and candidate.is_dir():
        sys.path.insert(0, str(candidate))
    else:
        logger.warning(
            "paper_rag package not found on PYTHONPATH or via PAPER_RAG_HOME — "
            "router endpoints will return 503"
        )


# ---------------------------------------------------------------------------
# Auth dependency (will be backed by BetterAuth middleware in middleware/auth.py)
# ---------------------------------------------------------------------------


def get_current_user_id(request: Request) -> str:
    """Pull the authenticated user_id from request state.

    The auth middleware sets request.state.user_id after validating a
    BetterAuth session token. If missing -> 401.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _touch_paper_access(user_id: str, chunks: list[dict] | None) -> None:
    """Record paper-access timestamps for stale-card scanning (M9 / ADR-0018).

    Best-effort: any failure is logged at DEBUG and swallowed — paper_access is
    metadata, never blocking. Extracts unique paper_ids from `chunks`
    (whatever subset the QA pipeline actually used as evidence) and forwards
    them to ``proactive.paper_access.touch_many``.
    """
    if not user_id or not chunks:
        return
    try:
        from paper_rag.proactive import paper_access  # type: ignore
    except Exception:  # noqa: BLE001 — package may be unavailable in tests
        return
    paper_ids: list[str] = []
    seen: set[str] = set()
    for c in chunks:
        pid = c.get("paper_id") if isinstance(c, dict) else None
        if pid and pid not in seen:
            seen.add(pid)
            paper_ids.append(str(pid))
    if not paper_ids:
        return
    try:
        paper_access.touch_many(user_id, paper_ids)
    except Exception as e:  # noqa: BLE001 — never block QA on metadata
        logger.debug("paper_access touch_many failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class QARequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    paper_ids: list[str] | None = Field(default=None, description="Filter to these papers; null = whole library")
    conversation_id: str | None = Field(default=None, description="Multi-turn conversation thread id")


class QASyncResponse(BaseModel):
    answer: str
    citations: list[str]
    abstain: dict[str, Any]
    trace_id: str
    n_chunks: int


class IngestRequest(BaseModel):
    arxiv_id: str | None = Field(default=None, description="e.g. 2310.11511 or 2310.11511v3")
    pdf_url: str | None = Field(default=None, description="Direct PDF URL")
    title_hint: str | None = Field(default=None, description="Title hint when only PDF URL is given")


class IngestResponse(BaseModel):
    paper_id: str
    title: str | None
    n_chunks: int
    status: str  # "ingested" | "already_exists"


class PaperRow(BaseModel):
    paper_id: str
    title: str | None
    arxiv_id: str | None
    n_chunks: int
    ingested_at: str | None


class WikiResponse(BaseModel):
    paper_id: str
    summary: str
    last_updated: str | None
    word_count: int


class DeliverRequest(BaseModel):
    format: str = Field(..., description="One of: markdown_survey | pptx | docx | latex_bib")
    paper_ids: list[str] = Field(..., min_length=1)
    title: str | None = Field(default=None, max_length=200)
    options: dict[str, Any] | None = Field(default=None)


class DeliverResponse(BaseModel):
    format: str
    filename: str
    content_base64: str
    content_type: str
    size_bytes: int
    metadata: dict[str, Any]


class FeedbackRequest(BaseModel):
    event_type: str = Field(..., description="One of: thumbs_up, thumbs_down, copy_answer, follow_up_question, abandon, abstain_followup_ingest, judge_score")
    trace_id: str | None = Field(default=None)
    conversation_id: str | None = Field(default=None)
    payload: dict[str, Any] | None = Field(default=None)


class FeedbackResponse(BaseModel):
    id: int
    status: str
    user_id: str


class SubscriptionRequest(BaseModel):
    kind: str = Field("keyword", description="keyword | topic_vector | arxiv_category")
    value: str = Field(..., min_length=1, max_length=120)
    strength: str = Field("normal", description="low | normal | high")


class SubscriptionToggle(BaseModel):
    enabled: bool


class InboxItemAction(BaseModel):
    item_id: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/qa")
async def qa_stream(
    body: QARequest,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """Streaming Q&A — yields SSE events from paper_rag.rag.qa_stream.

    Event types: intent / rewrite / retrieved / reflect / abstain /
    answer_chunk / done / error. The protocol is unchanged from
    qa_stream.stream_answer() — see ADR-0014 for abstain event.
    """
    _ensure_paper_rag_importable()
    try:
        from paper_rag.rag.qa_stream import stream_answer
    except ImportError as e:
        raise HTTPException(503, f"paper_rag package unavailable: {e}")

    async def _gen() -> AsyncGenerator[dict[str, str], None]:
        loop = asyncio.get_running_loop()
        # stream_answer is a SYNCHRONOUS generator; iterate it in a thread
        # so the gateway event loop is never blocked. Each yielded dict is
        # serialized as one SSE event.
        gen = stream_answer(body.question, paper_ids=body.paper_ids)
        touched_paper_ids: list[str] = []
        try:
            while True:
                evt = await loop.run_in_executor(None, _safe_next, gen)
                if evt is _SENTINEL_DONE:
                    break
                # M9 / ADR-0018: capture paper_ids on `done` for stale tracking
                if evt.get("event") == "done":
                    pids = evt.get("data", {}).get("paper_ids") or []
                    if isinstance(pids, list):
                        touched_paper_ids = [str(p) for p in pids if p]
                yield {"event": evt["event"], "data": json.dumps(evt["data"], ensure_ascii=False)}
        except Exception as e:
            logger.exception("paper_rag qa stream failed")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
        finally:
            # Fire-and-forget paper_access.touch_many — failures stay in DEBUG
            if touched_paper_ids:
                try:
                    chunks_view = [{"paper_id": pid} for pid in touched_paper_ids]
                    await loop.run_in_executor(None, _touch_paper_access, user_id, chunks_view)
                except Exception:  # noqa: BLE001
                    pass

    return EventSourceResponse(_gen())


_SENTINEL_DONE = object()


def _safe_next(gen):
    try:
        return next(gen)
    except StopIteration:
        return _SENTINEL_DONE


@router.post("/qa/sync", response_model=QASyncResponse)
async def qa_sync(
    body: QARequest,
    user_id: str = Depends(get_current_user_id),
) -> QASyncResponse:
    """Non-streaming Q&A — convenient for curl / scripts / 3rd-party integrations."""
    _ensure_paper_rag_importable()
    try:
        from paper_rag.rag.qa_agentic import answer
    except ImportError as e:
        raise HTTPException(503, f"paper_rag package unavailable: {e}")

    loop = asyncio.get_running_loop()
    out = await loop.run_in_executor(
        None,
        lambda: answer(
            body.question,
            paper_ids=body.paper_ids,
            conversation_id=body.conversation_id,
        ),
    )
    # M9 / ADR-0018: record paper_access for stale-card scanning. Run in the
    # same executor (off-loop) so it does not delay the response.
    chunks_used = out.get("chunks", []) or []
    if chunks_used:
        await loop.run_in_executor(None, _touch_paper_access, user_id, chunks_used)
    return QASyncResponse(
        answer=out.get("answer", ""),
        citations=out.get("citations", []),
        abstain=out.get("trace", {}).get("abstain", {}),
        trace_id=out.get("trace", {}).get("trace_id", ""),
        n_chunks=len(out.get("chunks", [])),
    )


@router.get("/papers", response_model=list[PaperRow])
async def list_papers(
    user_id: str = Depends(get_current_user_id),
    limit: int = 100,
) -> list[PaperRow]:
    """List the user's ingested papers (plus the shared 'system' library)."""
    _ensure_paper_rag_importable()
    try:
        # paper_rag.store.sqlite_store keeps the canonical paper list
        from paper_rag.store import sqlite_store
    except ImportError as e:
        raise HTTPException(503, f"paper_rag unavailable: {e}")

    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _list_papers_for_user, sqlite_store, user_id, limit)
    return [PaperRow(**r) for r in rows]


def _list_papers_for_user(store, user_id: str, limit: int) -> list[dict]:
    """Read papers visible to user_id (their own + 'system' shared library).

    Schema-agnostic: tries to read user_id column if present, falls back to
    listing all papers (legacy path before ADR-0015 schema migration).
    """
    import sqlite3

    sqlite_path = store.SQLITE_PATH if hasattr(store, "SQLITE_PATH") else _resolve_sqlite_path()
    if not Path(sqlite_path).exists():
        return []
    con = sqlite3.connect(str(sqlite_path))
    con.row_factory = sqlite3.Row
    try:
        # SQLModel uses singular table names by default: paper / chunk / section.
        # Detect which table name exists at runtime — robust across both styles.
        table_names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        papers_table = "paper" if "paper" in table_names else "papers"
        chunks_table = "chunk" if "chunk" in table_names else "chunks"

        cols = {r[1] for r in con.execute(f"PRAGMA table_info({papers_table})")}
        if "user_id" in cols:
            cur = con.execute(
                f"SELECT paper_id, title, arxiv_id, created_at FROM {papers_table} "
                "WHERE user_id = ? OR user_id = 'system' OR user_id IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            cur = con.execute(
                f"SELECT paper_id, title, arxiv_id, created_at FROM {papers_table} "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = []
        for r in cur:
            paper_id = r["paper_id"]
            cnt = con.execute(
                f"SELECT COUNT(*) AS n FROM {chunks_table} WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()["n"]
            rows.append({
                "paper_id": paper_id,
                "title": r["title"],
                "arxiv_id": r["arxiv_id"],
                "n_chunks": int(cnt or 0),
                "ingested_at": str(r["created_at"]) if r["created_at"] is not None else None,
            })
        return rows
    finally:
        con.close()


def _resolve_sqlite_path() -> str:
    """Find papers.sqlite via paper_rag config."""
    from paper_rag import config as cfg

    return cfg.load().paths.sqlite_path


@router.post("/papers/ingest", response_model=IngestResponse)
async def ingest_paper(
    body: IngestRequest,
    user_id: str = Depends(get_current_user_id),
) -> IngestResponse:
    """Ingest a paper by arxiv id (preferred) or direct PDF url."""
    if not body.arxiv_id and not body.pdf_url:
        raise HTTPException(400, "Provide either arxiv_id or pdf_url")
    _ensure_paper_rag_importable()
    try:
        from paper_rag.tools.paper_index import ingest as ingest_tool
    except ImportError as e:
        raise HTTPException(503, f"paper_rag unavailable: {e}")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: ingest_tool({
            "arxiv_id": body.arxiv_id,
            "pdf_url": body.pdf_url,
            "title_hint": body.title_hint,
            "user_id": user_id,
        }),
    )
    return IngestResponse(
        paper_id=result.get("paper_id", ""),
        title=result.get("title"),
        n_chunks=int(result.get("n_chunks", 0)),
        status=result.get("status", "ingested"),
    )


@router.get("/wiki/{paper_id}", response_model=WikiResponse)
async def get_wiki(
    paper_id: str,
    user_id: str = Depends(get_current_user_id),
) -> WikiResponse:
    """Return the auto-generated wiki entry for a paper, if available."""
    _ensure_paper_rag_importable()
    try:
        from paper_rag.wiki import store as wiki_store
    except ImportError as e:
        raise HTTPException(503, f"paper_rag unavailable: {e}")

    loop = asyncio.get_running_loop()
    entry = await loop.run_in_executor(None, wiki_store.get, paper_id)
    if not entry:
        raise HTTPException(404, f"No wiki entry for paper_id={paper_id}")
    summary = entry.get("summary", "") if isinstance(entry, dict) else getattr(entry, "summary", "")
    return WikiResponse(
        paper_id=paper_id,
        summary=summary,
        last_updated=str(entry.get("last_updated") if isinstance(entry, dict) else getattr(entry, "last_updated", "")),
        word_count=len(summary.split()),
    )


@router.post("/deliver", response_model=DeliverResponse)
async def deliver(
    body: DeliverRequest,
    user_id: str = Depends(get_current_user_id),
) -> DeliverResponse:
    """Generate a deliverable artifact (Markdown survey / PPT / Word / LaTeX bib).

    See ADR-0016 for the design. The artifact is returned as base64-encoded
    bytes — small enough for survey-class outputs (typical < 200 KB). For
    large multi-paper PPT decks the response can grow; clients should stream
    via /api/paper_rag/deliver/stream when added later.
    """
    import base64

    _ensure_paper_rag_importable()
    try:
        from paper_rag.deliver import dispatch
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.deliver unavailable: {e}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: dispatch(
                body.format,
                body.paper_ids,
                title=body.title,
                options=body.options,
                user_id=user_id,
            ),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        # python-pptx / python-docx missing
        raise HTTPException(503, str(e))

    return DeliverResponse(
        format=result.format,
        filename=result.filename,
        content_base64=base64.b64encode(result.content_bytes).decode("ascii"),
        content_type=result.content_type,
        size_bytes=len(result.content_bytes),
        metadata=result.metadata,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(
    body: FeedbackRequest,
    user_id: str = Depends(get_current_user_id),
) -> FeedbackResponse:
    """Record a user-behavior feedback event (M11 / ADR-0017).

    Idempotent at minute granularity: re-submitting the same
    (user_id, trace_id, event_type) within the same minute returns the
    existing row id rather than creating a duplicate.
    """
    _ensure_paper_rag_importable()
    try:
        from paper_rag.feedback import record_event
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.feedback unavailable: {e}")

    loop = asyncio.get_running_loop()
    try:
        eid = await loop.run_in_executor(
            None,
            lambda: record_event(
                user_id=user_id,
                event_type=body.event_type,
                payload=body.payload or {},
                trace_id=body.trace_id,
                conversation_id=body.conversation_id,
            ),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(429, str(e))
    return FeedbackResponse(id=eid, status="recorded", user_id=user_id)


@router.get("/feedback/recent")
async def list_recent_feedback(
    user_id: str = Depends(get_current_user_id),
    limit: int = 20,
) -> list[dict[str, Any]]:
    _ensure_paper_rag_importable()
    try:
        from paper_rag.feedback import recent_events
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.feedback unavailable: {e}")

    loop = asyncio.get_running_loop()
    out = await loop.run_in_executor(None, recent_events, user_id, limit)
    return out


@router.get("/feedback/stats")
async def feedback_stats(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    try:
        from paper_rag.feedback import user_stats
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.feedback unavailable: {e}")

    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, user_stats, user_id)
    return stats


# ---------------------------------------------------------------------------
# M9 / ADR-0018 — Proactive Agent: subscriptions + inbox + cron triggers
# ---------------------------------------------------------------------------


@router.get("/subscriptions")
async def list_subscriptions(
    user_id: str = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    _ensure_paper_rag_importable()
    try:
        from paper_rag.proactive import subscriptions
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.proactive unavailable: {e}")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, subscriptions.list_for_user, user_id)


@router.post("/subscriptions")
async def add_subscription(
    body: SubscriptionRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    try:
        from paper_rag.proactive import subscriptions
    except ImportError as e:
        raise HTTPException(503, f"paper_rag.proactive unavailable: {e}")
    loop = asyncio.get_running_loop()
    try:
        sid = await loop.run_in_executor(
            None,
            lambda: subscriptions.add(user_id, body.kind, body.value, strength=body.strength),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": sid, "status": "subscribed"}


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: int,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import subscriptions

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: subscriptions.delete(sub_id, user_id=user_id)
    )
    if not ok:
        raise HTTPException(404, f"subscription {sub_id} not found for user")
    return {"id": sub_id, "status": "deleted"}


@router.patch("/subscriptions/{sub_id}")
async def toggle_subscription(
    sub_id: int,
    body: SubscriptionToggle,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import subscriptions

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: subscriptions.toggle(sub_id, enabled=body.enabled, user_id=user_id)
    )
    if not ok:
        raise HTTPException(404, f"subscription {sub_id} not found for user")
    return {"id": sub_id, "enabled": body.enabled}


@router.get("/inbox")
async def list_inbox(
    user_id: str = Depends(get_current_user_id),
    unread_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import inbox

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(
        None, lambda: inbox.list_for_user(user_id, unread_only=unread_only, limit=limit)
    )
    unread = await loop.run_in_executor(None, inbox.unread_count, user_id)
    return {"items": items, "unread_count": unread}


@router.post("/inbox/{item_id}/read")
async def mark_inbox_read(
    item_id: int,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import inbox

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: inbox.mark_read(item_id, user_id=user_id)
    )
    return {"id": item_id, "marked_read": ok}


@router.get("/inbox/stream")
async def stream_inbox(
    user_id: str = Depends(get_current_user_id),
    poll_seconds: float = 5.0,
) -> EventSourceResponse:
    """P3-12 / ADR-0018: SSE long-poll for inbox unread items.

    Pushes events of two kinds:
      - ``ping``: heartbeat every poll_seconds (keeps NAT/proxy connections alive)
      - ``inbox``: a single new unread item (one event per item)

    Frontend should subscribe with EventSource and `addEventListener('inbox', ...)`.
    Client closes connection -> server stops the loop.

    This is a poll-based pseudo-push (not full duplex). It avoids needing a
    pub/sub broker while still feeling instant (5s latency upper bound).
    """
    _ensure_paper_rag_importable()
    from paper_rag.proactive import inbox

    poll_seconds = max(1.0, min(float(poll_seconds), 30.0))

    async def _gen() -> AsyncGenerator[dict[str, str], None]:
        loop = asyncio.get_running_loop()
        seen_ids: set[int] = set()
        # Prime: skip everything currently unread (we only push NEW items)
        try:
            existing = await loop.run_in_executor(
                None, lambda: inbox.list_for_user(user_id, unread_only=True, limit=200)
            )
            seen_ids = {int(it["id"]) for it in existing if it.get("id")}
        except Exception:  # noqa: BLE001
            pass

        while True:
            try:
                items = await loop.run_in_executor(
                    None, lambda: inbox.list_for_user(user_id, unread_only=True, limit=50)
                )
            except Exception as e:  # noqa: BLE001 — never crash the stream
                logger.warning("inbox/stream poll failed: %s", e)
                items = []
            for it in items:
                iid = it.get("id")
                if iid is None or int(iid) in seen_ids:
                    continue
                seen_ids.add(int(iid))
                yield {
                    "event": "inbox",
                    "data": json.dumps({
                        "id": iid,
                        "kind": it.get("kind"),
                        "title": it.get("title"),
                        "created_at": it.get("created_at"),
                    }, ensure_ascii=False),
                }
            yield {"event": "ping", "data": json.dumps({"ts": asyncio.get_running_loop().time()})}
            await asyncio.sleep(poll_seconds)

    return EventSourceResponse(_gen())


@router.post("/inbox/{item_id}/dismiss")
async def dismiss_inbox(
    item_id: int,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import inbox

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: inbox.dismiss(item_id, user_id=user_id)
    )
    return {"id": item_id, "dismissed": ok}


@router.post("/proactive/digest/run")
async def run_digest_now(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Manually trigger digest for the calling user (dev / opt-in)."""
    _ensure_paper_rag_importable()
    from paper_rag.proactive import digest

    loop = asyncio.get_running_loop()
    item_id = await loop.run_in_executor(
        None, lambda: digest.daily_digest_for_user(user_id)
    )
    return {"user_id": user_id, "inbox_item_id": item_id, "wrote": bool(item_id)}


@router.post("/proactive/stale/run")
async def run_stale_now(
    user_id: str = Depends(get_current_user_id),
    days: int = 30,
) -> dict[str, Any]:
    _ensure_paper_rag_importable()
    from paper_rag.proactive import stale

    loop = asyncio.get_running_loop()
    n = await loop.run_in_executor(
        None, lambda: stale.stale_scan_for_user(user_id, older_than_days=days)
    )
    return {"user_id": user_id, "n_cards": n}
