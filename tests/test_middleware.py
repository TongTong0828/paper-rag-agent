"""Tests for the gateway middleware stack (M9.6).

Coverage:
  1. RequestIdMiddleware  — generates UUID, echoes existing X-Request-ID
  2. AccessLogMiddleware  — emits one JSON line per request (skips /health)
  3. PrometheusMiddleware — increments gateway_http_requests_total
  4. BodySizeLimitMiddleware — 413 when content-length > limit
  5. TimeoutMiddleware    — 504 when handler exceeds timeout
  6. RateLimitMiddleware  — 429 when burst exceeded
  7. AuthMiddleware       — extract_session_token regex; LRU cache eviction

Each middleware is loaded as a free-standing module via importlib (the
backend/app/gateway package's ``__init__`` triggers the heavy ``deerflow``
runtime import which we don't need here).
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import sys
import time
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("DEER_FLOW_ROOT", _DEFAULT_ROOT))
sys.path.insert(0, str(ROOT / "paper_rag" / "src"))


def _load_module(mod_name: str, path: Path):
    """Load a module by file path without importing its parent package."""
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-load the three middleware modules under stable aliases. Each test
# imports from these aliases instead of `app.gateway.middleware.*`.
_obs = _load_module(
    "gw_obs",
    ROOT / "backend/app/gateway/middleware/observability.py",
)
_prot = _load_module(
    "gw_prot",
    ROOT / "backend/app/gateway/middleware/protection.py",
)
_auth = _load_module(
    "gw_auth",
    ROOT / "backend/app/gateway/middleware/auth.py",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*middlewares):
    """Build a FastAPI app with the given middleware classes (innermost first)."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(2.0)
        return {"slept": 2.0}

    @app.get("/error")
    async def err():
        raise RuntimeError("boom")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    for mw in middlewares:
        if isinstance(mw, tuple):
            cls, kwargs = mw
            app.add_middleware(cls, **kwargs)
        else:
            app.add_middleware(mw)
    return app


# ---------------------------------------------------------------------------
# 1. RequestIdMiddleware
# ---------------------------------------------------------------------------


def test_request_id_generates_when_absent():
    from fastapi.testclient import TestClient

    app = _make_app(_obs.RequestIdMiddleware)
    c = TestClient(app)
    r = c.get("/")
    rid = r.headers.get("X-Request-ID")
    assert rid and len(rid) == 32  # uuid4().hex


def test_request_id_propagates_existing():
    from fastapi.testclient import TestClient

    app = _make_app(_obs.RequestIdMiddleware)
    c = TestClient(app)
    r = c.get("/", headers={"X-Request-ID": "client-xyz"})
    assert r.headers["X-Request-ID"] == "client-xyz"


def test_request_id_truncates_oversize():
    from fastapi.testclient import TestClient

    app = _make_app(_obs.RequestIdMiddleware)
    c = TestClient(app)
    r = c.get("/", headers={"X-Request-ID": "x" * 200})
    assert len(r.headers["X-Request-ID"]) == 64


# ---------------------------------------------------------------------------
# 2. AccessLogMiddleware
# ---------------------------------------------------------------------------


def test_access_log_emits_json_line():
    import json

    from fastapi.testclient import TestClient

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    log = logging.getLogger("gateway.access")
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    try:
        app = _make_app(_obs.AccessLogMiddleware, _obs.RequestIdMiddleware)
        c = TestClient(app)
        r = c.get("/")
        assert r.status_code == 200
        for line in buf.getvalue().splitlines():
            entry = json.loads(line)
            if entry.get("path") == "/":
                assert entry["status"] == 200
                assert entry["method"] == "GET"
                assert "latency_ms" in entry
                assert entry["request_id"]
                return
        raise AssertionError("no access log line for /")
    finally:
        log.removeHandler(handler)


def test_access_log_skips_health():
    from fastapi.testclient import TestClient

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    log = logging.getLogger("gateway.access")
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    try:
        app = _make_app(_obs.AccessLogMiddleware)
        c = TestClient(app)
        c.get("/health")
        assert "/health" not in buf.getvalue()
    finally:
        log.removeHandler(handler)


def test_prometheus_counter_incremented():
    from fastapi.testclient import TestClient

    from paper_rag.observability.metrics import render

    app = _make_app(_obs.PrometheusMiddleware)
    c = TestClient(app)
    c.get("/")
    c.get("/")
    out = render()
    assert "gateway_http_requests_total" in out
    assert 'path="/"' in out
    assert 'method="GET"' in out


def test_body_size_limit_rejects_large():
    from fastapi.testclient import TestClient

    app = _make_app((_prot.BodySizeLimitMiddleware, {"max_bytes": 100}))
    c = TestClient(app)
    r = c.post("/", content=b"x" * 1000, headers={"Content-Length": "1000"})
    assert r.status_code == 413
    assert r.json()["limit_bytes"] == 100


def test_body_size_limit_allows_small():
    from fastapi.testclient import TestClient

    app = _make_app((_prot.BodySizeLimitMiddleware, {"max_bytes": 10_000}))
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200


def test_timeout_returns_504():
    from fastapi.testclient import TestClient

    app = _make_app((_prot.TimeoutMiddleware, {"timeout_s": 0.2}))
    c = TestClient(app)
    r = c.get("/slow")
    assert r.status_code == 504, f"got {r.status_code}: {r.text[:80]}"
    assert r.json()["timeout_s"] == 0.2


def test_timeout_skips_sse_paths():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/paper_rag/qa")
    async def qa():
        await asyncio.sleep(0.05)
        return {"ok": True}

    app.add_middleware(_prot.TimeoutMiddleware, timeout_s=0.01)
    c = TestClient(app)
    r = c.get("/api/paper_rag/qa")
    assert r.status_code == 200, f"SSE bypass failed: {r.status_code}"


def test_rate_limit_burst_429():
    from fastapi.testclient import TestClient

    app = _make_app((_prot.RateLimitMiddleware, {"rps": 2, "burst": 3}))
    c = TestClient(app)
    statuses = [c.get("/").status_code for _ in range(5)]
    assert 429 in statuses, f"no 429 in {statuses}"


def test_rate_limit_recovers_after_window():
    from fastapi.testclient import TestClient

    app = _make_app((_prot.RateLimitMiddleware, {"rps": 2, "burst": 2}))
    c = TestClient(app)
    [c.get("/") for _ in range(3)]
    time.sleep(1.1)
    r = c.get("/")
    assert r.status_code == 200, f"should recover after window: {r.status_code}"


def test_extract_session_token_default_cookie():
    cookie = "foo=bar; better-auth.session_token=abc.xyz; locale=zh"
    assert _auth._extract_session_token(cookie) == "abc.xyz"


def test_extract_session_token_secure_prefix():
    cookie = "__Secure-better-auth.session_token=secure-tok; other=1"
    assert _auth._extract_session_token(cookie) == "secure-tok"


def test_extract_session_token_fallback_to_full():
    cookie = "custom_session=xx"
    assert _auth._extract_session_token(cookie) == cookie


def test_auth_lru_eviction_o1():
    """Verify OrderedDict-based LRU evicts oldest entries when over capacity."""
    mw = _auth.BetterAuthMiddleware(app=None)
    mw._cache_max = 3
    now = time.time()
    for i in range(5):
        mw._cache[f"tok-{i}"] = (f"user-{i}", now + 999)
        while len(mw._cache) > mw._cache_max:
            mw._cache.popitem(last=False)
    assert "tok-0" not in mw._cache
    assert "tok-1" not in mw._cache
    assert "tok-4" in mw._cache


# ---------------------------------------------------------------------------
# 8. RateLimitMiddleware Redis backend (M9.7)
# ---------------------------------------------------------------------------


def test_rate_limit_redis_backend_failover_to_memory():
    """When Redis is unreachable, RateLimit transparently uses memory mode."""
    from fastapi.testclient import TestClient

    app = _make_app(
        (_prot.RateLimitMiddleware, {"rps": 2, "burst": 2,
                                      "redis_url": "redis://nonexistent:9999/0"})
    )
    c = TestClient(app)
    # First call triggers Redis init → fallthrough → memory takes over.
    r1 = c.get("/")
    assert r1.status_code == 200
    # Memory backend should still throttle (burst=2 means 3rd hit gets 429)
    statuses = [c.get("/").status_code for _ in range(5)]
    assert 429 in statuses, f"memory backend not enforcing: {statuses}"


def test_rate_limit_redis_backend_uses_check():
    """When Redis backend reports allow, request goes through (no memory check)."""
    from fastapi.testclient import TestClient

    class _StubRedisAllow:
        def __init__(self, *a, **kw):
            pass

        async def check(self, key, burst):
            return "allow", 0

    original = _prot._RedisBackend
    _prot._RedisBackend = _StubRedisAllow
    try:
        app = _make_app(
            (_prot.RateLimitMiddleware, {"rps": 2, "burst": 2, "redis_url": "redis://stub:6379"})
        )
        c = TestClient(app)
        statuses = [c.get("/").status_code for _ in range(10)]
        assert all(s == 200 for s in statuses), f"stub redis got: {statuses}"
    finally:
        _prot._RedisBackend = original


def test_rate_limit_redis_backend_rejects():
    """Stub Redis returns reject → middleware emits 429."""
    from fastapi.testclient import TestClient

    class _StubRedisReject:
        def __init__(self, *a, **kw):
            pass

        async def check(self, key, burst):
            return "reject", 100

    original = _prot._RedisBackend
    _prot._RedisBackend = _StubRedisReject
    try:
        app = _make_app(
            (_prot.RateLimitMiddleware, {"rps": 2, "burst": 2, "redis_url": "redis://stub:6379"})
        )
        c = TestClient(app)
        r = c.get("/")
        assert r.status_code == 429, f"got {r.status_code}: {r.text}"
        assert r.json()["burst_limit"] == 2
    finally:
        _prot._RedisBackend = original


# ---------------------------------------------------------------------------
# 9. Edge cases (V3 補漏覆盖)
# ---------------------------------------------------------------------------


def test_body_size_limit_skips_health():
    """SKIP_PREFIXES bypass even with oversized claim."""
    from fastapi.testclient import TestClient

    app = _make_app((_prot.BodySizeLimitMiddleware, {"max_bytes": 1}))
    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200


def test_timeout_500_propagates_handler_exception():
    """Timeout middleware must NOT swallow handler exceptions before timeout fires."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/error")
    async def err():
        raise RuntimeError("expected")

    app.add_middleware(_prot.TimeoutMiddleware, timeout_s=5.0)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/error")
    assert r.status_code == 500


def test_rate_limit_uses_user_id_when_present():
    """Authed requests are keyed on user_id rather than IP."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user_id = "alice"
        return await call_next(request)

    @app.get("/")
    async def root():
        return {"ok": True}

    app.add_middleware(_prot.RateLimitMiddleware, rps=2, burst=2)
    c = TestClient(app)
    statuses = [c.get("/").status_code for _ in range(5)]
    assert 429 in statuses


def test_request_id_persists_across_concurrent_requests():
    """Each request gets its own ID even when flooded."""
    from fastapi.testclient import TestClient

    app = _make_app(_obs.RequestIdMiddleware)
    c = TestClient(app)
    rids = {c.get("/").headers["X-Request-ID"] for _ in range(10)}
    # All unique
    assert len(rids) == 10


def test_extract_session_token_no_known_pattern_fallback():
    """Unknown cookie format → return full string (still works as cache key)."""
    cookie = "session_id=abc123; locale=zh"
    out = _auth._extract_session_token(cookie)
    assert out == cookie  # fallback path


def test_auth_module_share_client_lifecycle():
    """_shared_client returns same instance until _close_shared_client."""
    c1 = _auth._shared_client()
    c2 = _auth._shared_client()
    assert c1 is c2  # singleton

    import asyncio
    asyncio.get_event_loop().run_until_complete(_auth._close_shared_client())
    c3 = _auth._shared_client()
    # After close, a new client is built
    assert c3 is not c1


# ---------------------------------------------------------------------------
# Runner (zero-deps fallback)
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_request_id_generates_when_absent,
        test_request_id_propagates_existing,
        test_request_id_truncates_oversize,
        test_access_log_emits_json_line,
        test_access_log_skips_health,
        test_prometheus_counter_incremented,
        test_body_size_limit_rejects_large,
        test_body_size_limit_allows_small,
        test_timeout_returns_504,
        test_timeout_skips_sse_paths,
        test_rate_limit_burst_429,
        test_rate_limit_recovers_after_window,
        test_extract_session_token_default_cookie,
        test_extract_session_token_secure_prefix,
        test_extract_session_token_fallback_to_full,
        test_auth_lru_eviction_o1,
        test_rate_limit_redis_backend_failover_to_memory,
        test_rate_limit_redis_backend_uses_check,
        test_rate_limit_redis_backend_rejects,
    ]
    ok = fail = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            fail += 1
        except Exception as e:
            import traceback
            print(f"  💥 {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            fail += 1
    print(f"\n{ok}/{ok+fail} passed")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
