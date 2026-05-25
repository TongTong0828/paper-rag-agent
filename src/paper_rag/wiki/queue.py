"""In-process async queue for wiki updates.

Goal: keep the main ingest path snappy. After a paper is indexed, we want to
fire concept extraction + create/patch flow without blocking the user. A
single background daemon thread drains the queue.

For multi-process / multi-machine setup, replace this with Redis + RQ; the
public API (`submit_paper_indexed`, `wait_drained`) stays the same.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from ..utils.logger import get_logger

log = get_logger("wiki.queue")

_QUEUE: queue.Queue[str] | None = None
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()


def _worker_loop() -> None:
    assert _QUEUE is not None
    while True:
        paper_id = _QUEUE.get()
        if paper_id is None:  # poison pill
            _QUEUE.task_done()
            return
        try:
            # Resolve fresh on every call so tests can monkey-patch
            # paper_rag.wiki.triggers.on_paper_indexed.
            from . import triggers as _t

            _t.on_paper_indexed(paper_id)
        except Exception as e:
            log.warning(f"async wiki update failed for {paper_id}: {e}")
        finally:
            _QUEUE.task_done()


def _ensure_started() -> None:
    global _QUEUE, _WORKER
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = queue.Queue()
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(
                target=_worker_loop, name="wiki-queue", daemon=True
            )
            _WORKER.start()
            log.info("wiki async worker started")


def submit_paper_indexed(paper_id: str) -> None:
    """Enqueue a paper for async wiki update. Non-blocking."""
    _ensure_started()
    assert _QUEUE is not None
    _QUEUE.put(paper_id)


def wait_drained(timeout: float | None = None) -> bool:
    """Block until queue is empty (for tests / shutdown). True if drained, False on timeout."""
    if _QUEUE is None:
        return True
    if timeout is None:
        _QUEUE.join()
        return True
    end = threading.Event()

    def _waiter():
        _QUEUE.join()
        end.set()

    threading.Thread(target=_waiter, daemon=True).start()
    return end.wait(timeout)


def _stats() -> dict[str, Any]:
    return {
        "started": _QUEUE is not None,
        "alive": bool(_WORKER and _WORKER.is_alive()),
        "qsize": _QUEUE.qsize() if _QUEUE else 0,
    }
