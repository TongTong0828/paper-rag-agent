"""In-process metrics collector (Prometheus-compatible export).

Light-weight: counters + histograms in plain Python dicts, lock-protected.
No prometheus_client dependency — we render the wire format ourselves.

Why not prometheus_client?
--------------------------
- We want zero hard runtime deps.
- Our deployment is single-process gateway + an APScheduler sidecar; we
  do NOT need multiprocess mode, exemplars, or process_collector.
- ``render()`` produces the standard text exposition any scraper consumes.

If the deployment ever needs the prometheus_client features (multiproc,
exemplars), the public ``counter / histogram / render`` API is small
enough to swap in a wrapper around ``prometheus_client.Counter`` etc.
without touching call sites. See ADR-0020 for the original decision.

Usage
-----
    from paper_rag.observability.metrics import counter, histogram, render

    counter("paper_rag_qa_total", labels={"intent": "factual", "stop": "answered"}).inc()
    histogram("paper_rag_qa_latency_seconds").observe(2.31)

    print(render())   # -> Prometheus text format
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterable

_LOCK = threading.Lock()
_COUNTERS: dict[tuple[str, frozenset], float] = {}
_HISTOGRAMS: dict[tuple[str, frozenset], list[float]] = {}

_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)


def _label_key(labels: dict | None) -> frozenset:
    return frozenset((labels or {}).items())


class _Counter:
    def __init__(self, name: str, labels: dict | None = None):
        self._key = (name, _label_key(labels))

    def inc(self, n: float = 1.0) -> None:
        with _LOCK:
            _COUNTERS[self._key] = _COUNTERS.get(self._key, 0.0) + n


class _Histogram:
    def __init__(self, name: str, labels: dict | None = None):
        self._key = (name, _label_key(labels))

    def observe(self, value: float) -> None:
        with _LOCK:
            _HISTOGRAMS.setdefault(self._key, []).append(float(value))

    @contextmanager
    def time(self):
        t0 = time.time()
        try:
            yield
        finally:
            self.observe(time.time() - t0)


def counter(name: str, labels: dict | None = None) -> _Counter:
    return _Counter(name, labels)


def histogram(name: str, labels: dict | None = None) -> _Histogram:
    return _Histogram(name, labels)


def reset() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def snapshot() -> dict:
    """Return a JSON-serializable dump (for tests / debugging)."""
    with _LOCK:
        return {
            "counters": [
                {"name": n, "labels": dict(lbl), "value": v}
                for (n, lbl), v in _COUNTERS.items()
            ],
            "histograms": [
                {
                    "name": n,
                    "labels": dict(lbl),
                    "count": len(samples),
                    "sum": sum(samples),
                    "p50": _quantile(samples, 0.50),
                    "p95": _quantile(samples, 0.95),
                    "p99": _quantile(samples, 0.99),
                }
                for (n, lbl), samples in _HISTOGRAMS.items()
            ],
        }


def render(buckets: Iterable[float] = _DEFAULT_BUCKETS) -> str:
    """Render Prometheus text exposition format."""
    lines: list[str] = []
    with _LOCK:
        for (name, lbl), v in sorted(_COUNTERS.items()):
            label_part = _fmt_labels(lbl)
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{label_part} {v}")
        for (name, lbl), samples in sorted(_HISTOGRAMS.items()):
            lines.append(f"# TYPE {name} histogram")
            cum = 0
            for b in buckets:
                cum = sum(1 for s in samples if s <= b)
                lbl_with_le = dict(lbl, le=str(b))
                lines.append(f"{name}_bucket{_fmt_labels(frozenset(lbl_with_le.items()))} {cum}")
            lbl_inf = dict(lbl, le="+Inf")
            lines.append(f"{name}_bucket{_fmt_labels(frozenset(lbl_inf.items()))} {len(samples)}")
            lines.append(f"{name}_sum{_fmt_labels(lbl)} {sum(samples)}")
            lines.append(f"{name}_count{_fmt_labels(lbl)} {len(samples)}")
    return "\n".join(lines) + "\n"


def _fmt_labels(lbl: frozenset) -> str:
    if not lbl:
        return ""
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(lbl))
    return "{" + parts + "}"


def _quantile(samples: list[float], q: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = min(len(s) - 1, int(q * len(s)))
    return s[idx]
