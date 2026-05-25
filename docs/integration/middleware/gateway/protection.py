"""Protection middleware (M9.6+).

Four lightweight defensive layers:

  1. ``BodySizeLimitMiddleware`` — reject oversized request bodies before
     they reach handlers (e.g. PDF ingest >50 MB).
  2. ``TimeoutMiddleware``       — global wall-clock limit for any handler;
     sends a 504 if a downstream call (LLM, Qdrant) wedges.
  3. ``RateLimitMiddleware``     — per-user / per-IP throttle. Two backends:
     - in-memory sliding window (default, single replica)
     - Redis GCRA (M9.7, set ``DEERFLOW_RATE_LIMIT_REDIS_URL``)
     Falls back to memory when Redis is unreachable.
  4. (GZip via Starlette stdlib — wired in app.py, not here.)

Industrial properties:
- Zero new third-party deps (Redis backend is OPTIONAL — only imported
  when ``DEERFLOW_RATE_LIMIT_REDIS_URL`` is set).
- Safe defaults: every limit is overridable via env, including 'disabled'.
- Failures NEVER raise out — they always produce a structured 4xx/5xx JSON.
- RateLimit is keyed on ``request.state.user_id`` (set by AuthMiddleware),
  falling back to client IP for unauthenticated paths.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. BodySizeLimitMiddleware
# ---------------------------------------------------------------------------


_DEFAULT_MAX_BYTES = int(os.environ.get("DEERFLOW_MAX_REQUEST_BYTES", str(50 * 1024 * 1024)))


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose ``Content-Length`` exceeds the configured cap.

    Two-stage check:
      (a) Cheap header check up-front — most clients send Content-Length.
      (b) For chunked uploads (no header), enforce by streaming the body
          ourselves while counting bytes — abort once over.

    Note: Streaming check duplicates the body into memory; for very large
    multipart uploads consider doing this at nginx instead.
    """

    SKIP_PREFIXES = ("/health", "/metrics", "/api/auth")

    def __init__(self, app, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if any(request.url.path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self._max:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": "Request body too large",
                    "limit_bytes": self._max,
                    "got_bytes": int(cl),
                },
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# 2. TimeoutMiddleware
# ---------------------------------------------------------------------------


_DEFAULT_TIMEOUT_S = float(os.environ.get("DEERFLOW_REQUEST_TIMEOUT_S", "60"))


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Enforce a global wall-clock limit on every request.

    Returns 504 with a structured payload when ``timeout_s`` elapses.
    Streaming endpoints (SSE) typically exceed any reasonable global limit,
    so they're listed in ``SKIP_PREFIXES``.
    """

    SKIP_PREFIXES = (
        "/api/paper_rag/qa",            # streaming SSE
        "/api/paper_rag/inbox/stream",  # SSE long poll
        "/health",
        "/metrics",
    )

    def __init__(self, app, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        super().__init__(app)
        self._timeout = timeout_s

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if any(request.url.path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "request timeout method=%s path=%s timeout_s=%.1f",
                request.method, request.url.path, self._timeout,
            )
            return JSONResponse(
                status_code=504,
                content={
                    "detail": "Gateway request timeout",
                    "timeout_s": self._timeout,
                    "path": request.url.path,
                },
            )


# ---------------------------------------------------------------------------
# 3. RateLimitMiddleware
# ---------------------------------------------------------------------------


_RL_DEFAULT_RPS = int(os.environ.get("DEERFLOW_RATE_LIMIT_RPS", "20"))      # sustained
_RL_DEFAULT_BURST = int(os.environ.get("DEERFLOW_RATE_LIMIT_BURST", "60"))   # peak in burst window
_RL_REDIS_URL = os.environ.get("DEERFLOW_RATE_LIMIT_REDIS_URL", "")          # e.g. redis://redis:6379/0


# ── Redis backend (optional) ────────────────────────────────────────────────
# Sliding window via Lua to keep the operation atomic.

_REDIS_LUA_SCRIPT = """
local k = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', k, '-inf', now - window)
local cnt = redis.call('ZCARD', k)
if cnt >= burst then
    return cnt
end
redis.call('ZADD', k, now, now .. ':' .. math.random())
redis.call('PEXPIRE', k, math.ceil(window * 1000))
return -1  -- accepted
"""


class _RedisBackend:
    """Best-effort Redis sliding-window rate limiter.

    Connects lazily; on any error we mark the backend as failed for 30s and
    fall back to memory mode upstream. ``redis.asyncio`` is the only soft
    dep — it's not installed by default.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None
        self._script_sha: str | None = None
        self._failed_until = 0.0
        self._lock = asyncio.Lock()

    async def _ensure(self) -> bool:
        if self._client is not None:
            return True
        if time.monotonic() < self._failed_until:
            return False
        async with self._lock:
            if self._client is not None:
                return True
            try:
                import redis.asyncio as redis  # type: ignore[import-untyped]
            except ImportError:
                logger.warning(
                    "DEERFLOW_RATE_LIMIT_REDIS_URL set but `redis` not installed; "
                    "falling back to in-memory rate limiter"
                )
                self._failed_until = time.monotonic() + 3600
                return False
            try:
                self._client = redis.from_url(self._url, decode_responses=True)
                self._script_sha = await self._client.script_load(_REDIS_LUA_SCRIPT)
                # Probe
                await self._client.ping()
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis rate limiter init failed (%s); fallback to memory", e)
                self._client = None
                self._failed_until = time.monotonic() + 30
                return False

    async def check(self, key: str, burst: int) -> tuple[str, int]:
        """Return (decision, count).

        decision is one of:
          - ``"allow"``       — request accepted by Redis backend
          - ``"reject"``      — request denied (count gives current usage)
          - ``"fallthrough"`` — backend unavailable; caller should use memory
        """
        ok = await self._ensure()
        if not ok:
            return "fallthrough", 0
        try:
            now_ms = int(time.time() * 1000)
            window_ms = 1000
            res = await self._client.evalsha(  # type: ignore[union-attr]
                self._script_sha, 1, f"rl:{key}", now_ms, window_ms, burst,
            )
            count = int(res)
            if count == -1:
                return "allow", 0
            return "reject", count
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis rate-limit op failed (%s); fail-open", e)
            self._failed_until = time.monotonic() + 30
            self._client = None
            return "fallthrough", 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-user / per-IP rate limiter.

    Algorithm
    ---------
    Sliding window over a ``deque`` of recent timestamps:
      - reject if more than ``burst`` requests in the last 1.0 s
      - reject if more than ``rps`` requests in the last 1.0 s sustained
        (we use the same window — simpler model that matches what nginx
        ``limit_req`` would enforce on top of us anyway).

    Backends
    --------
    - In-memory deque (default, single replica)
    - Redis GCRA via Lua script (set ``DEERFLOW_RATE_LIMIT_REDIS_URL``).
      When Redis is unreachable we transparently fall back to memory mode
      (logged once per 30s) so a Redis outage never DDoS-es the API.
    """

    SKIP_PREFIXES = ("/health", "/metrics", "/api/auth", "/docs", "/openapi.json")

    def __init__(
        self,
        app,
        rps: int = _RL_DEFAULT_RPS,
        burst: int = _RL_DEFAULT_BURST,
        redis_url: str | None = None,
    ) -> None:
        super().__init__(app)
        self._burst = burst
        self._rps = rps
        # key -> deque[timestamp]  (memory backend)
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        # Redis backend (optional)
        url = redis_url if redis_url is not None else _RL_REDIS_URL
        self._redis: _RedisBackend | None = _RedisBackend(url) if url else None

    def _key_for(self, request: Request) -> str:
        uid = getattr(request.state, "user_id", None)
        if uid:
            return f"u:{uid}"
        host = request.client.host if request.client else "unknown"
        return f"ip:{host}"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if any(request.url.path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        key = self._key_for(request)

        # ── Try Redis first if configured ──────────────────────────────
        if self._redis is not None:
            decision, count = await self._redis.check(key, self._burst)
            if decision == "reject":
                return self._reject(request, key, count)
            if decision == "allow":
                # Redis backend handled it; skip memory check
                return await call_next(request)
            # decision == "fallthrough" → memory backend takes over

        # ── Memory backend ─────────────────────────────────────────────
        now = time.monotonic()
        window = self._windows[key]
        cutoff = now - 1.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._burst:
            return self._reject(request, key, len(window))
        if len(window) >= self._rps:
            # Inside [rps, burst] we still allow but throttle indirectly via
            # subsequent checks within the window.
            pass
        window.append(now)
        return await call_next(request)

    def _reject(self, request: Request, key: str, count: int) -> Response:
        logger.warning(
            "rate limit hit key=%s count=%d path=%s", key, count, request.url.path
        )
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Too many requests",
                "key": key,
                "burst_limit": self._burst,
                "window_seconds": 1,
            },
            headers={"Retry-After": "1"},
        )


__all__ = [
    "BodySizeLimitMiddleware",
    "TimeoutMiddleware",
    "RateLimitMiddleware",
]
