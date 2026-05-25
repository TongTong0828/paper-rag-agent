"""Observability primitives: metrics + trace ids.

Public API:
    from paper_rag.observability import counter, histogram, render, new_trace_id
"""

from .metrics import counter, histogram, render, reset, snapshot
from .trace import new_trace_id

__all__ = ["counter", "histogram", "render", "reset", "snapshot", "new_trace_id"]
