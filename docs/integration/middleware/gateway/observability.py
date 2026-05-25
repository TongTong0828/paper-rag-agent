"""Observability middleware (M9.6+).

Three orthogonal concerns wired as separate Starlette middlewares so they
can be enabled / disabled independently:

  1. ``RequestIdMiddleware``  — auto-generates / propagates ``X-Request-ID``
                                  and stashes it on ``request.state.request_id``
                                  so downstream code can log it.
  2. ``AccessLogMiddleware``  — single JSON-line per request (method, path,
                                  status, latency_ms, user_id, request_id).
  3. ``PrometheusMiddleware`` — writes per-route latency + status counters
                                  into ``paper_rag.observability.metrics``
                                  so the existing /metrics endpoint exposes
                                  gateway numbers automatically.

Design rules:
- All three middlewares are **idempotent**: missing dependencies (logging,
  metrics module) degrade silently — they never raise.
- No new heavy deps: stdlib + the existing paper_rag.observability.metrics.
- The metrics middleware uses route ``path_template`` (e.g. ``/api/paper_rag/wiki/{paper_id}``)
  rather than the literal path so high-cardinality URLs do not blow up labels.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("gateway.access")


# ---------------------------------------------------------------------------
# 1. RequestIdMiddleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject ``X-Request-ID`` (or generate one) on ``request.state.request_id``.

    Echos the same id back in the response header so a client / log aggregator
    can correlate. UUID4 if the client did not supply one.
    """

    HEADER = "X-Request-ID"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get(self.HEADER) or uuid.uuid4().hex
        # Trim to 64 chars to defend against malicious / oversized headers
        rid = rid[:64]
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[self.HEADER] = rid
        return response


# ---------------------------------------------------------------------------
# 2. AccessLogMiddleware
# ---------------------------------------------------------------------------


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Single JSON-line access log per request.

    Output goes through the standard logging stack (logger name
    ``gateway.access``) so operators can route it to stdout / a file /
    fluent-bit by tweaking logging.yaml — no extra plumbing needed.
    """

    SKIP_PATHS = ("/health", "/metrics")  # zero-value to log

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            entry = {
                "ts": round(time.time(), 3),
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "latency_ms": latency_ms,
                "user_id": getattr(request.state, "user_id", None),
                "request_id": getattr(request.state, "request_id", None),
                "client": (request.client.host if request.client else None),
            }
            try:
                logger.info(json.dumps(entry, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                pass
        return response


# ---------------------------------------------------------------------------
# 3. PrometheusMiddleware
# ---------------------------------------------------------------------------


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Per-route latency histogram + status counter.

    Reuses ``paper_rag.observability.metrics`` so gateway numbers show up at
    the existing ``/metrics`` endpoint. Falls back to a no-op if the module
    cannot be imported (e.g. paper_rag package missing).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            self._record(request, status, time.perf_counter() - t0)

    @staticmethod
    def _record(request: Request, status: int, elapsed_s: float) -> None:
        try:
            from paper_rag.observability.metrics import counter, histogram
        except Exception:  # noqa: BLE001
            return
        # Use the route's path template instead of the literal URL so we do
        # not explode label cardinality on /wiki/{paper_id} etc.
        route = request.scope.get("route")
        path_label = getattr(route, "path", request.url.path)
        labels = {
            "method": request.method,
            "path": path_label,
            "status": str(status),
        }
        try:
            counter("gateway_http_requests_total", labels).inc()
            histogram("gateway_http_request_duration_seconds", labels).observe(elapsed_s)
        except Exception:  # noqa: BLE001 — metrics never block requests
            pass


__all__ = [
    "RequestIdMiddleware",
    "AccessLogMiddleware",
    "PrometheusMiddleware",
]
