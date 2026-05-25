"""Async-friendly entrypoints for the QA hot path.

The underlying retrieve / abstain / LLM call stack is synchronous (uses the
sync `openai` client + sync `qdrant_client.QdrantClient` + sync sqlite). A
full async rewrite would touch ~30 files; instead we offload the entire
sync call to a thread, which is exactly what `starlette.run_in_threadpool`
or `anyio.to_thread.run_sync` were designed for.

This module exposes:

    await answer_async(question, paper_ids=...)           # qa_agentic.answer
    async for ev in stream_answer_async(question, ...):   # qa_stream.stream_answer

so a FastAPI handler can `await answer_async(...)` and not block the event
loop. CPU-bound or IO-bound work happens on a worker thread, FastAPI keeps
serving other requests.

Why this is sufficient for the current deployment:
    - Single OpenAI client is shared (rag/llm.py P1) so HTTP connection
      reuse already kicks in.
    - Qdrant + SQLite are sub-millisecond local calls.
    - The dominant latency is the LLM round trip, which the OS schedules
      on the worker thread without holding the GIL.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import anyio


async def answer_async(
    question: str,
    *,
    paper_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Async wrapper around qa_agentic.answer.

    Runs the (sync) pipeline in a worker thread so the FastAPI event loop
    stays unblocked.
    """
    from .qa_agentic import answer

    return await anyio.to_thread.run_sync(
        lambda: answer(
            question,
            paper_ids=paper_ids,
            conversation_id=conversation_id,
        )
    )


async def stream_answer_async(
    question: str,
    *,
    paper_ids: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """Async wrapper around qa_stream.stream_answer.

    The underlying generator is sync; we drain it on a worker thread one
    event at a time. Each `next()` round-trip yields control back to the
    event loop, so other requests on the same FastAPI process keep
    progressing while we wait for the next LLM token.
    """
    from .qa_stream import stream_answer

    gen = await anyio.to_thread.run_sync(
        lambda: iter(stream_answer(question, paper_ids=paper_ids))
    )

    sentinel: Any = object()
    while True:
        ev = await anyio.to_thread.run_sync(lambda: next(gen, sentinel))
        if ev is sentinel:
            break
        yield ev


__all__ = ["answer_async", "stream_answer_async"]
