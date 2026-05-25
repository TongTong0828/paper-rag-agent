"""RecursionGuardMiddleware (M11.3).

Complements LoopDetectionMiddleware (which detects *identical* tool calls)
by capping the **total step count** of a single agent run. This catches
"agent is doing useful but interminable work" — e.g. iterating through 1000
chunks one at a time, exploring an infinite filesystem tree, or a chat that
keeps re-summarizing.

Two thresholds:
  - ``soft_limit`` — inject a wrap-up system message ("you have used N steps,
    please conclude")
  - ``hard_limit`` — strip tool_calls from the response, forcing the agent
    to produce a final text answer

Step is defined as one LLM call (counted in after_model). Counting is
per-thread — same scope choice as LoopDetectionMiddleware.

Both limits are env-overridable so deployments can tune without code change:
  - ``DEERFLOW_RECURSION_SOFT_LIMIT``  (default 30)
  - ``DEERFLOW_RECURSION_HARD_LIMIT``  (default 50)
"""
from __future__ import annotations

import logging
import os
import threading
from copy import deepcopy
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


_DEFAULT_SOFT = int(os.environ.get("DEERFLOW_RECURSION_SOFT_LIMIT", "30"))
_DEFAULT_HARD = int(os.environ.get("DEERFLOW_RECURSION_HARD_LIMIT", "50"))


class RecursionGuardMiddleware(AgentMiddleware):
    """Cap total LLM steps per run; warn at soft_limit, hard-stop at hard_limit."""

    def __init__(
        self,
        soft_limit: int = _DEFAULT_SOFT,
        hard_limit: int = _DEFAULT_HARD,
    ) -> None:
        super().__init__()
        if hard_limit < soft_limit:
            raise ValueError("hard_limit must be >= soft_limit")
        self._soft = soft_limit
        self._hard = hard_limit
        # thread-id -> step_count
        self._steps: dict[int, int] = {}
        # thread-id -> bool (avoid duplicate soft warning per run)
        self._warned: dict[int, bool] = {}
        self._lock = threading.Lock()

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._count_and_maybe_intervene(state)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._count_and_maybe_intervene(state)

    def _count_and_maybe_intervene(self, state: AgentState) -> dict | None:
        tid = threading.get_ident()
        with self._lock:
            count = self._steps.get(tid, 0) + 1
            self._steps[tid] = count
            already_warned = self._warned.get(tid, False)

        # Hard limit: strip tool_calls from the last AI message and inject
        # a system message forcing wrap-up. Reset state so a future run on
        # the same thread starts fresh.
        if count >= self._hard:
            messages = state.get("messages", [])
            if not messages:
                return None
            last = messages[-1]
            new_msgs = list(messages)
            modified = False
            if hasattr(last, "tool_calls") and last.tool_calls:
                stripped = deepcopy(last)
                # Pydantic message: replace tool_calls
                try:
                    stripped.tool_calls = []
                except Exception:  # noqa: BLE001
                    # Some impls require object_construct
                    return None
                stripped.content = (
                    str(getattr(last, "content", "") or "") + "\n\n"
                    f"[recursion guard] reached hard limit of {self._hard} steps; "
                    "produce a final answer with current context."
                )
                new_msgs[-1] = stripped
                modified = True
            with self._lock:
                # Reset for next run on this thread
                self._steps.pop(tid, None)
                self._warned.pop(tid, None)
            logger.error(
                "RecursionGuard HARD-STOP: step_count=%d (limit=%d); "
                "stripped %d tool_calls",
                count, self._hard,
                len(getattr(last, "tool_calls", []) or []) if modified else 0,
            )
            return {"messages": new_msgs} if modified else None

        # Soft limit: inject a one-shot wrap-up nudge (HumanMessage so it
        # surfaces in the prompt next turn).
        if count >= self._soft and not already_warned:
            with self._lock:
                self._warned[tid] = True
            warning = HumanMessage(
                content=(
                    f"[recursion guard] you have used {count} steps "
                    f"(soft limit {self._soft}). Please consolidate findings "
                    "and produce a final answer in the next 1-2 steps."
                ),
            )
            messages = list(state.get("messages", []))
            messages.append(warning)
            logger.warning(
                "RecursionGuard SOFT-WARN: step_count=%d (limit=%d)",
                count, self._soft,
            )
            return {"messages": messages}

        return None

    # Optional: hook into agent end to clear state. Not strictly needed
    # because agents typically run to completion or hit a safety limit.
    def reset(self, thread_id: int | None = None) -> None:
        """Clear step counter (use in tests)."""
        with self._lock:
            if thread_id is None:
                self._steps.clear()
                self._warned.clear()
            else:
                self._steps.pop(thread_id, None)
                self._warned.pop(thread_id, None)
