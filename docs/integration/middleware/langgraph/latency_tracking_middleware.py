"""LatencyTrackingMiddleware (M11.2).

Measures wall-clock latency of each LLM call (before_model → after_model)
and emits:
  - structured INFO log line (one per call)
  - Prometheus histogram ``deerflow_llm_latency_seconds{model=...}``
  - Long-tail warning log when a single call exceeds ``warn_threshold_s``

This is complementary to TokenUsageMiddleware: tokens tell you cost,
latency tells you UX. Tracking both lets you spot "expensive AND slow"
calls (often a sign of an over-long context window or a small_model
upgraded to a big_model by accident).

Stores per-thread start times in a small dict keyed by ``run_id``-like
attribute on Runtime; falls back to a global slot if Runtime doesn't
expose one (single-threaded dev case).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


_DEFAULT_WARN_S = float(5.0)        # warn if a single LLM call > 5s
_DEFAULT_CRITICAL_S = float(30.0)   # error log + ratchet metric


class LatencyTrackingMiddleware(AgentMiddleware):
    """Track per-LLM-call latency. Logs + Prometheus histogram."""

    def __init__(
        self,
        warn_threshold_s: float = _DEFAULT_WARN_S,
        critical_threshold_s: float = _DEFAULT_CRITICAL_S,
    ) -> None:
        super().__init__()
        self._warn = warn_threshold_s
        self._crit = critical_threshold_s
        # thread-id -> start_time. Best-effort because we don't have
        # access to a stable run-scoped id from langchain core. Threading
        # gives us correct accounting in the common single-call-per-thread case.
        self._starts: dict[int, float] = {}
        self._lock = threading.Lock()

    # ── before_model ──────────────────────────────────────────────────
    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._mark_start()
        return None

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._mark_start()
        return None

    def _mark_start(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            self._starts[tid] = time.perf_counter()

    # ── after_model ───────────────────────────────────────────────────
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record(state)
        return None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record(state)
        return None

    def _record(self, state: AgentState) -> None:
        tid = threading.get_ident()
        with self._lock:
            t0 = self._starts.pop(tid, None)
        if t0 is None:
            return  # no start recorded (initialization race), skip silently
        elapsed = time.perf_counter() - t0

        # Pull model name from the last message
        model = "unknown"
        messages = state.get("messages", [])
        if messages:
            meta = getattr(messages[-1], "response_metadata", None) or {}
            if isinstance(meta, dict):
                model = meta.get("model_name") or meta.get("model") or "unknown"

        if elapsed >= self._crit:
            logger.error(
                "LLM latency CRITICAL: model=%s elapsed=%.2fs (>%ss). "
                "Possible network stall or prompt too long.",
                model, elapsed, self._crit,
            )
        elif elapsed >= self._warn:
            logger.warning(
                "LLM latency long-tail: model=%s elapsed=%.2fs (>%ss).",
                model, elapsed, self._warn,
            )
        else:
            logger.info("LLM latency: model=%s elapsed=%.3fs", model, elapsed)

        # Prometheus histogram
        try:
            from paper_rag.observability.metrics import histogram
            histogram(
                "deerflow_llm_latency_seconds",
                {"model": model},
            ).observe(elapsed)
        except Exception as e:  # noqa: BLE001
            logger.debug("latency metric emit failed (non-fatal): %s", e)
