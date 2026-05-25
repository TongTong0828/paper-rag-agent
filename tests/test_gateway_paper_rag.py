"""Tests for the gateway service-ization layer (M8 / ADR-0015).

These tests exercise the auth middleware, paper_rag router, and metrics
endpoint without touching the rest of the DeerFlow gateway (which has hard
deps on Python 3.12 features). We build a minimal FastAPI app and use
TestClient for end-to-end HTTP coverage.

Run:
    PAPER_RAG_CONFIG=config/local.yaml python -m pytest tests/test_gateway_paper_rag.py -v

Or, for environments without pytest, the file's main() runs all tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# Discover the deer-flow repo root by walking up from this file.
# tests/test_gateway_paper_rag.py → paper_rag/ → deer-flow/.
# Override via env DEER_FLOW_ROOT=/abs/path if the layout differs (e.g. when
# paper_rag is published as a standalone repo without the deerflow sibling).
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("DEER_FLOW_ROOT", _DEFAULT_ROOT))


def _load(mod_name: str, path: Path):
    """Load a module by file path (avoids importing the whole backend pkg)."""
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_app():
    """Build a minimal FastAPI app with paper_rag router + auth middleware."""
    sys.path.insert(0, str(ROOT / "paper_rag" / "src"))
    auth = _load("auth_mod", ROOT / "backend/app/gateway/middleware/auth.py")
    pr = _load("pr_mod", ROOT / "backend/app/gateway/routers/paper_rag.py")
    sys.modules["app.gateway.routers.paper_rag"] = pr
    metrics = _load("metrics_mod", ROOT / "backend/app/gateway/routers/metrics.py")

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(pr.router)
    app.include_router(metrics.router)
    app.add_middleware(auth.BetterAuthMiddleware)
    return app, auth


def test_routes_registered():
    """Verify all 5 paper_rag endpoints + /metrics are registered."""
    app, _ = _make_app()
    paths = {r.path for r in app.routes}
    assert "/metrics" in paths
    for p in (
        "/api/paper_rag/qa",
        "/api/paper_rag/qa/sync",
        "/api/paper_rag/papers",
        "/api/paper_rag/papers/ingest",
        "/api/paper_rag/wiki/{paper_id}",
    ):
        assert p in paths, f"missing route: {p}"


def test_metrics_endpoint_bypasses_auth():
    """/metrics must NOT require a session cookie (ops endpoint)."""
    from fastapi.testclient import TestClient

    app, auth = _make_app()
    auth._AUTH_DISABLED = False
    c = TestClient(app)
    r = c.get("/metrics")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:100]}"


def test_openapi_endpoint_bypasses_auth():
    """/openapi.json must be accessible (so /docs UI works pre-login)."""
    from fastapi.testclient import TestClient

    app, auth = _make_app()
    auth._AUTH_DISABLED = False
    c = TestClient(app)
    r = c.get("/openapi.json")
    assert r.status_code == 200


def test_paper_rag_endpoints_require_auth():
    """All paper_rag endpoints return 401 without a session cookie."""
    from fastapi.testclient import TestClient

    app, auth = _make_app()
    auth._AUTH_DISABLED = False
    c = TestClient(app)
    r = c.get("/api/paper_rag/papers")
    assert r.status_code == 401
    assert "Missing session" in r.text or "Authentication" in r.text


def test_paper_rag_papers_dev_mode():
    """In DEERFLOW_AUTH_DISABLED mode, /papers returns 200 with system user_id."""
    os.environ["PAPER_RAG_CONFIG"] = str(ROOT / "paper_rag" / "config" / "local.yaml")
    from fastapi.testclient import TestClient

    app, auth = _make_app()
    auth._AUTH_DISABLED = True
    auth._DEV_USER_ID = "system"
    c = TestClient(app)
    r = c.get("/api/paper_rag/papers")
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert isinstance(data, list)
    if data:
        # If there are papers, verify schema
        sample = data[0]
        for required in ("paper_id", "title", "n_chunks"):
            assert required in sample, f"missing field in response: {required}"


def test_touch_paper_access_extracts_unique_ids(tmp_path=None):
    """P0-1 / ADR-0018: _touch_paper_access dedups paper_ids and survives errors."""
    import tempfile

    # Isolate the SQLite file: paper_access._resolve_path delegates to
    # feedback.store._resolve_path, so we monkey-patch THAT one — and restore.
    from paper_rag.feedback import store as feedback_store
    from paper_rag.proactive import paper_access

    tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp_db.close()
    original_resolve = feedback_store._resolve_path
    feedback_store._resolve_path = lambda: Path(tmp_db.name)
    try:
        _, _ = _make_app()
        pr = sys.modules["pr_mod"]
        chunks = [
            {"paper_id": "arxiv:1111", "chunk_id": "c1"},
            {"paper_id": "arxiv:1111", "chunk_id": "c2"},  # dup paper, kept once
            {"paper_id": "arxiv:2222", "chunk_id": "c3"},
            {"paper_id": None, "chunk_id": "c4"},          # None tolerated
            {},                                             # missing key tolerated
        ]
        pr._touch_paper_access("alice", chunks)

        rows = paper_access.stale_for_user("alice", older_than_days=-1)  # all
        pids = sorted(r.get("paper_id") for r in rows)
        assert pids == ["arxiv:1111", "arxiv:2222"], pids
    finally:
        feedback_store._resolve_path = original_resolve
        os.unlink(tmp_db.name)


def test_touch_paper_access_noop_on_missing_inputs():
    """Empty user_id or empty chunks must not raise and must not write."""
    _, _ = _make_app()
    pr = sys.modules["pr_mod"]
    pr._touch_paper_access("", [{"paper_id": "x"}])  # no user_id
    pr._touch_paper_access("alice", [])              # no chunks
    pr._touch_paper_access("alice", None)            # type-tolerant
    # If we got here, no exception was raised. Pass.


def test_proactive_endpoints_require_auth():
    """P0-3: every /subscriptions /inbox /proactive endpoint must 401 unauthed."""
    from fastapi.testclient import TestClient

    app, auth = _make_app()
    auth._AUTH_DISABLED = False
    c = TestClient(app)
    cases = [
        ("GET",    "/api/paper_rag/subscriptions"),
        ("POST",   "/api/paper_rag/subscriptions"),
        ("DELETE", "/api/paper_rag/subscriptions/1"),
        ("PATCH",  "/api/paper_rag/subscriptions/1"),
        ("GET",    "/api/paper_rag/inbox"),
        ("GET",    "/api/paper_rag/inbox/stream"),
        ("POST",   "/api/paper_rag/inbox/1/read"),
        ("POST",   "/api/paper_rag/inbox/1/dismiss"),
        ("POST",   "/api/paper_rag/proactive/digest/run"),
        ("POST",   "/api/paper_rag/proactive/stale/run"),
    ]
    for method, path in cases:
        r = c.request(method, path, json={})
        assert r.status_code == 401, f"{method} {path} got {r.status_code}: {r.text[:80]}"


def test_proactive_user_isolation_in_dev_mode():
    """P0-3: in dev mode, two distinct user_ids never see each other's subs."""
    import tempfile

    from paper_rag.feedback import store as feedback_store

    tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp_db.close()
    original = feedback_store._resolve_path
    feedback_store._resolve_path = lambda: Path(tmp_db.name)
    try:
        from fastapi.testclient import TestClient

        app, auth = _make_app()
        auth._AUTH_DISABLED = True

        # Round 1: alice adds a subscription
        auth._DEV_USER_ID = "alice"
        c = TestClient(app)
        r = c.post(
            "/api/paper_rag/subscriptions",
            json={"kind": "keyword", "value": "retrieval", "strength": "high"},
        )
        assert r.status_code in (200, 201), f"add: {r.status_code} {r.text[:120]}"
        r = c.get("/api/paper_rag/subscriptions")
        assert r.status_code == 200
        alice_subs = r.json()
        assert len(alice_subs) == 1
        assert alice_subs[0]["value"] == "retrieval"

        # Round 2: bob lists -> should be empty
        auth._DEV_USER_ID = "bob"
        c2 = TestClient(app)
        r = c2.get("/api/paper_rag/subscriptions")
        assert r.status_code == 200
        bob_subs = r.json()
        assert bob_subs == [], f"user_id leak! bob saw: {bob_subs}"
    finally:
        feedback_store._resolve_path = original
        os.unlink(tmp_db.name)


def main():
    """Run all tests without pytest."""
    tests = [
        test_routes_registered,
        test_metrics_endpoint_bypasses_auth,
        test_openapi_endpoint_bypasses_auth,
        test_paper_rag_endpoints_require_auth,
        test_paper_rag_papers_dev_mode,
        test_touch_paper_access_extracts_unique_ids,
        test_touch_paper_access_noop_on_missing_inputs,
        test_proactive_endpoints_require_auth,
        test_proactive_user_isolation_in_dev_mode,
    ]
    ok = 0
    fail = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: AssertionError: {e}")
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
