"""Authentication middleware for the DeerFlow gateway.

Validates BetterAuth session tokens (issued by the Next.js frontend at
``/api/auth/...``) and injects ``request.state.user_id`` into downstream
routers. Routers that need auth depend on
``app.gateway.routers.paper_rag.get_current_user_id`` which reads this
state.

Strategy
--------
We call the frontend's ``/api/auth/get-session`` HTTP endpoint with the
incoming Cookie header. This is the most schema-stable choice — direct
SQLite access is faster but the BetterAuth schema is undocumented and
may change between minor versions.

Performance tips:
- 60s LRU cache (true OrderedDict-based, O(1) eviction).
- Cache key is the session-token cookie value only (other cookies
  changing should NOT invalidate the entry).
- Async httpx client reused across requests; explicitly closed on shutdown.

Bypass
------
Auth is skipped for:
- ``/health``, ``/metrics``  (ops endpoints, internal-only)
- ``/api/auth/*``            (BetterAuth's own routes, served by frontend)
- ``OPTIONS`` requests        (CORS preflight, handled by nginx)

Configuration via env vars:
- ``DEERFLOW_AUTH_DISABLED=1``      Skip auth entirely (dev only)
- ``DEERFLOW_AUTH_SESSION_URL``     Default ``http://frontend:3000/api/auth/get-session``
- ``DEERFLOW_AUTH_CACHE_TTL``       Default ``60`` (seconds)
- ``DEERFLOW_AUTH_CACHE_SIZE``      Default ``10000`` (LRU max entries)
- ``DEERFLOW_AUTH_DEV_USER_ID``     If auth is disabled, inject this user_id
                                     for all requests (default ``"system"``)
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections import OrderedDict
from typing import Awaitable, Callable

import httpx
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


_DEFAULT_SESSION_URL = os.environ.get(
    "DEERFLOW_AUTH_SESSION_URL",
    "http://frontend:3000/api/auth/get-session",
)
_CACHE_TTL = int(os.environ.get("DEERFLOW_AUTH_CACHE_TTL", "60"))
_CACHE_SIZE = int(os.environ.get("DEERFLOW_AUTH_CACHE_SIZE", "10000"))
_AUTH_DISABLED = os.environ.get("DEERFLOW_AUTH_DISABLED", "").lower() in {"1", "true", "yes"}
_DEV_USER_ID = os.environ.get("DEERFLOW_AUTH_DEV_USER_ID", "system")

# Endpoints that never require auth (ops + auth-itself + preflight)
_BYPASS_PREFIXES = ("/health", "/metrics", "/api/auth", "/docs", "/openapi.json", "/redoc")

# BetterAuth session cookie names (covers default + custom prefix layouts)
_SESSION_COOKIE_PATTERNS = (
    re.compile(r"(?:^|;\s*)better-auth\.session_token=([^;]+)"),
    re.compile(r"(?:^|;\s*)better-auth\.session=([^;]+)"),
    re.compile(r"(?:^|;\s*)__Secure-better-auth\.session_token=([^;]+)"),
)


# Module-level shared httpx client. The first middleware instance creates it;
# the FastAPI shutdown hook closes it. Allows multiple BaseHTTPMiddleware
# instances (Starlette can reinstantiate) to share connection pooling.
_HTTP_CLIENT: httpx.AsyncClient | None = None


def _shared_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=2.0)
    return _HTTP_CLIENT


async def _close_shared_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        try:
            await _HTTP_CLIENT.aclose()
        except Exception as e:  # noqa: BLE001
            logger.debug("auth shared client close failed: %s", e)
    _HTTP_CLIENT = None


def _extract_session_token(cookie_header: str) -> str | None:
    """Pull the BetterAuth session token out of a Cookie header.

    Falls back to the full header if no known pattern matches — this still
    works (just less efficient cache key) and avoids breaking deployments
    that use a custom cookie name.
    """
    if not cookie_header:
        return None
    for pat in _SESSION_COOKIE_PATTERNS:
        m = pat.search(cookie_header)
        if m:
            return m.group(1)
    return cookie_header  # fallback


class BetterAuthMiddleware(BaseHTTPMiddleware):
    """Validate BetterAuth sessions and inject ``user_id`` into request state."""

    def __init__(self, app, session_url: str | None = None) -> None:
        super().__init__(app)
        self._session_url = session_url or _DEFAULT_SESSION_URL
        # token -> (user_id, expires_at_epoch). OrderedDict for O(1) LRU.
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._cache_max = _CACHE_SIZE
        # Reuse a module-level shared client so shutdown can close it without
        # us needing to pass the middleware instance into the lifespan hook.
        self._client = _shared_client()

    async def aclose(self) -> None:
        """Release the httpx client. Wired into FastAPI lifespan in app.py."""
        await _close_shared_client()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Bypass: ops + auth + preflight
        path = request.url.path
        if request.method == "OPTIONS" or any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await call_next(request)

        # Dev mode: skip auth, set fixed user_id
        if _AUTH_DISABLED:
            request.state.user_id = _DEV_USER_ID
            return await call_next(request)

        cookie = request.headers.get("cookie") or ""
        if not cookie:
            return _unauthorized("Missing session cookie")

        user_id = await self._resolve_user_id(cookie)
        if not user_id:
            return _unauthorized("Invalid or expired session")

        request.state.user_id = user_id
        return await call_next(request)

    async def _resolve_user_id(self, cookie_header: str) -> str | None:
        """Lookup user_id for a cookie, with TTL + LRU cache."""
        token = _extract_session_token(cookie_header)
        if not token:
            return None
        now = time.time()
        cached = self._cache.get(token)
        if cached and cached[1] > now:
            # Touch for LRU
            self._cache.move_to_end(token)
            return cached[0]

        try:
            resp = await self._client.get(
                self._session_url,
                headers={"Cookie": cookie_header},
            )
        except httpx.HTTPError as e:
            logger.warning("BetterAuth session lookup failed: %s", e)
            return None

        if resp.status_code != 200:
            return None
        try:
            data = resp.json() or {}
        except ValueError:
            return None
        # BetterAuth `getSession` returns {session: {userId, ...}, user: {...}}
        # We accept either layout.
        user = (data.get("user") or {})
        session = (data.get("session") or {})
        user_id = user.get("id") or session.get("userId") or data.get("userId")
        if not user_id:
            return None

        self._cache[token] = (str(user_id), now + _CACHE_TTL)
        self._cache.move_to_end(token)
        # O(1) eviction at the front (oldest)
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return str(user_id)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": detail})


def install_auth_lifecycle(app: FastAPI, middleware: BetterAuthMiddleware | None = None) -> None:
    """Wire shared httpx client cleanup into FastAPI shutdown.

    `middleware` arg is kept for backward compat / explicit binding; the
    actual client is module-level so the call works without it too.
    """

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - lifecycle hook
        await _close_shared_client()

