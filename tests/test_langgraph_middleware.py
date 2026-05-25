"""Tests for the deerflow langgraph middlewares (M11.1–M11.4).

Covers the 4 new/upgraded middlewares without spinning up a real LLM:
  - TokenUsageMiddleware     (logging + Prometheus + cost estimation)
  - LatencyTrackingMiddleware (before/after timing + Prom histogram)
  - RecursionGuardMiddleware  (soft/hard step limits)
  - PIIScrubMiddleware        (regex-based redaction)

We import the middleware modules directly via importlib so this test file
does not need the full `deerflow.runtime` chain.

Python 3.12 compatibility: deerflow uses ``from typing import override``
which only landed in 3.12. We patch ``typing`` with a no-op shim so the
modules load on 3.10/3.11 too.
"""
from __future__ import annotations

import importlib.util
import sys
import time
import typing as _typing
from pathlib import Path
from types import SimpleNamespace

# 3.10/3.11 shim for typing.override (introduced in 3.12).
if not hasattr(_typing, "override"):
    def _noop_override(fn):
        return fn

    _typing.override = _noop_override  # type: ignore[attr-defined]

import os  # noqa: E402

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("DEER_FLOW_ROOT", _DEFAULT_ROOT))
sys.path.insert(0, str(ROOT / "paper_rag" / "src"))


def _load(name: str, rel: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Stub external dependencies so the middleware modules can import.
def _install_stubs():
    """Install minimal langchain.agents / langchain.agents.middleware /
    langgraph.runtime / langchain_core.messages stubs."""
    import types

    # langchain.agents.AgentState
    if "langchain" not in sys.modules:
        sys.modules["langchain"] = types.ModuleType("langchain")
    if "langchain.agents" not in sys.modules:
        ag = types.ModuleType("langchain.agents")
        ag.AgentState = dict
        sys.modules["langchain.agents"] = ag
        sys.modules["langchain"].agents = ag

    if "langchain.agents.middleware" not in sys.modules:
        mw = types.ModuleType("langchain.agents.middleware")

        class _AgentMiddleware:
            def __init__(self, *a, **kw):
                pass

        mw.AgentMiddleware = _AgentMiddleware
        sys.modules["langchain.agents.middleware"] = mw
        sys.modules["langchain.agents"].middleware = mw

    if "langgraph" not in sys.modules:
        sys.modules["langgraph"] = types.ModuleType("langgraph")
    if "langgraph.runtime" not in sys.modules:
        rt = types.ModuleType("langgraph.runtime")
        rt.Runtime = object
        sys.modules["langgraph.runtime"] = rt
        sys.modules["langgraph"].runtime = rt

    if "langchain_core" not in sys.modules:
        sys.modules["langchain_core"] = types.ModuleType("langchain_core")
    if "langchain_core.messages" not in sys.modules:
        msgs = types.ModuleType("langchain_core.messages")

        class _HumanMessage:
            type = "human"

            def __init__(self, content: str = "", **kw):
                self.content = content
                self.role = "human"

        msgs.HumanMessage = _HumanMessage
        sys.modules["langchain_core.messages"] = msgs
        sys.modules["langchain_core"].messages = msgs


_install_stubs()


def _load_token():
    return _load(
        "tum",
        "backend/packages/harness/deerflow/agents/middlewares/token_usage_middleware.py",
    )


def _load_latency():
    return _load(
        "ltm",
        "backend/packages/harness/deerflow/agents/middlewares/latency_tracking_middleware.py",
    )


def _load_recursion():
    return _load(
        "rgm",
        "backend/packages/harness/deerflow/agents/middlewares/recursion_guard_middleware.py",
    )


def _load_pii():
    return _load(
        "pii",
        "backend/packages/harness/deerflow/agents/middlewares/pii_scrub_middleware.py",
    )


# ---------------------------------------------------------------------------
# 1. TokenUsageMiddleware
# ---------------------------------------------------------------------------


def test_estimate_cost_known_model():
    tum = _load_token()
    cost = tum.estimate_cost("qwen-plus", input_tokens=1000, output_tokens=500)
    # 1.0 * 0.0008 + 0.5 * 0.002 = 0.0008 + 0.001 = 0.0018
    assert abs(cost - 0.0018) < 1e-6, cost


def test_estimate_cost_unknown_model_returns_zero():
    tum = _load_token()
    assert tum.estimate_cost("unknown-model-xyz", 1000, 500) == 0.0


def test_estimate_cost_prefix_match():
    tum = _load_token()
    # qwen-plus prefix should match qwen-plus-1234
    cost = tum.estimate_cost("qwen-plus-1234", 1000, 0)
    assert cost > 0


def test_register_model_price_then_estimate():
    tum = _load_token()
    tum.register_model_price("custom-llm", 0.001, 0.002)
    cost = tum.estimate_cost("custom-llm", 1000, 1000)
    assert cost == 0.003


def test_token_usage_after_model_emits_metrics():
    """Inject a fake last message and verify Prom counters fire."""
    from paper_rag.observability.metrics import render

    tum = _load_token()
    mw = tum.TokenUsageMiddleware()
    fake_msg = SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        response_metadata={"model_name": "qwen-plus"},
    )
    state = {"messages": [fake_msg]}
    mw.after_model(state, runtime=None)
    out = render()
    assert "deerflow_llm_tokens_input_total" in out
    assert "deerflow_llm_tokens_output_total" in out
    assert "deerflow_llm_calls_total" in out
    assert 'model="qwen-plus"' in out


# ---------------------------------------------------------------------------
# 2. LatencyTrackingMiddleware
# ---------------------------------------------------------------------------


def test_latency_basic_path():
    ltm = _load_latency()
    mw = ltm.LatencyTrackingMiddleware()
    state = {"messages": [SimpleNamespace(response_metadata={"model_name": "test-llm"})]}
    mw.before_model(state, runtime=None)
    time.sleep(0.05)
    mw.after_model(state, runtime=None)
    # No assertion needed beyond "did not raise"; metric should be there
    from paper_rag.observability.metrics import render
    out = render()
    assert "deerflow_llm_latency_seconds" in out


def test_latency_after_without_before_silent():
    """If before_model never fired (e.g. swap-in mid-run), after_model is a no-op."""
    ltm = _load_latency()
    mw = ltm.LatencyTrackingMiddleware()
    state = {"messages": [SimpleNamespace(response_metadata={"model_name": "x"})]}
    # Should NOT raise
    mw.after_model(state, runtime=None)


# ---------------------------------------------------------------------------
# 3. RecursionGuardMiddleware
# ---------------------------------------------------------------------------


def test_recursion_soft_warn_injected():
    rgm = _load_recursion()
    mw = rgm.RecursionGuardMiddleware(soft_limit=2, hard_limit=5)
    state = {"messages": [SimpleNamespace(content="x", tool_calls=[])]}
    # First call: count=1, no warn
    r1 = mw.after_model(state, runtime=None)
    assert r1 is None
    # Second call: count=2 == soft, expect warning injection
    r2 = mw.after_model(state, runtime=None)
    assert r2 is not None
    assert "messages" in r2
    last = r2["messages"][-1]
    assert "soft limit" in last.content
    # Third call: still under hard, no further warn (one-shot)
    r3 = mw.after_model(state, runtime=None)
    assert r3 is None


def test_recursion_hard_strips_tool_calls():
    rgm = _load_recursion()
    mw = rgm.RecursionGuardMiddleware(soft_limit=1, hard_limit=2)
    fake_msg = SimpleNamespace(
        content="answer",
        tool_calls=[{"name": "x", "args": {}}],
    )
    state = {"messages": [fake_msg]}
    mw.after_model(state, runtime=None)        # count=1, soft
    out = mw.after_model(state, runtime=None)  # count=2, hard
    assert out is not None
    new_last = out["messages"][-1]
    assert new_last.tool_calls == []
    assert "hard limit" in new_last.content


def test_recursion_invalid_limits_raise():
    rgm = _load_recursion()
    try:
        rgm.RecursionGuardMiddleware(soft_limit=10, hard_limit=5)
    except ValueError:
        return
    raise AssertionError("expected ValueError for hard < soft")


# ---------------------------------------------------------------------------
# 4. PIIScrubMiddleware
# ---------------------------------------------------------------------------


def test_pii_scrub_email():
    pii = _load_pii()
    out, c = pii.scrub("contact me at alice@example.com please")
    assert "[REDACTED:EMAIL]" in out
    assert c.get("EMAIL") == 1


def test_pii_scrub_phone_cn_and_us():
    pii = _load_pii()
    out_cn, c1 = pii.scrub("我的手机号是 13812345678")
    assert "[REDACTED:PHONE_CN]" in out_cn
    out_us, c2 = pii.scrub("call me at (415) 555-1234")
    assert "[REDACTED:PHONE_US]" in out_us


def test_pii_scrub_apikey():
    pii = _load_pii()
    out, c = pii.scrub("token: sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    assert "[REDACTED:APIKEY]" in out
    assert c.get("APIKEY") == 1


def test_pii_scrub_ipv4():
    pii = _load_pii()
    out, c = pii.scrub("server at 192.168.1.100 is down")
    assert "[REDACTED:IP]" in out


def test_pii_scrub_no_pii_unchanged():
    pii = _load_pii()
    text = "the quick brown fox jumps over the lazy dog"
    out, c = pii.scrub(text)
    assert out == text
    assert c == {}


def test_pii_middleware_scrubs_human_messages():
    pii = _load_pii()
    mw = pii.PIIScrubMiddleware()
    msg = SimpleNamespace(type="human", content="email me at foo@bar.com", role="human")
    state = {"messages": [msg]}
    out = mw.before_model(state, runtime=None)
    assert out is not None
    assert "[REDACTED:EMAIL]" in out["messages"][0].content


def test_pii_middleware_skips_when_no_messages():
    pii = _load_pii()
    mw = pii.PIIScrubMiddleware()
    out = mw.before_model({"messages": []}, runtime=None)
    assert out is None


def test_pii_middleware_skips_when_no_pii_found():
    pii = _load_pii()
    mw = pii.PIIScrubMiddleware()
    msg = SimpleNamespace(type="human", content="just text, no pii", role="human")
    out = mw.before_model({"messages": [msg]}, runtime=None)
    assert out is None  # nothing changed → no state update


def test_pii_apikey_takes_priority_over_cc():
    """APIKEY pattern fires before CC, so sk-... is APIKEY not CC."""
    pii = _load_pii()
    out, c = pii.scrub("token sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 here")
    assert "[REDACTED:APIKEY]" in out
    assert c.get("CC", 0) == 0


def test_recursion_guard_state_resets_after_hard():
    """After hard-stop fires, the next call on the same thread starts fresh."""
    rgm = _load_recursion()
    mw = rgm.RecursionGuardMiddleware(soft_limit=2, hard_limit=3)
    fake_msg = SimpleNamespace(content="x", tool_calls=[{"name": "t", "args": {}}])
    state = {"messages": [fake_msg]}
    mw.after_model(state, runtime=None)  # 1
    mw.after_model(state, runtime=None)  # 2 → soft warn
    mw.after_model(state, runtime=None)  # 3 → hard, reset
    # next round should not be hard immediately (counter reset to 0)
    state2 = {"messages": [SimpleNamespace(content="y", tool_calls=[])]}
    out = mw.after_model(state2, runtime=None)
    # count=1 again, neither soft nor hard
    assert out is None


def test_token_usage_handles_missing_metadata():
    """No usage_metadata on message → no metric, no crash."""
    tum = _load_token()
    mw = tum.TokenUsageMiddleware()
    fake_msg = SimpleNamespace(usage_metadata=None, response_metadata={})
    mw.after_model({"messages": [fake_msg]}, runtime=None)


def test_token_usage_handles_unknown_model():
    """Cost=0 when model is unrecognized — should still emit token counts."""
    tum = _load_token()
    mw = tum.TokenUsageMiddleware()
    fake_msg = SimpleNamespace(
        usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        response_metadata={"model_name": "totally-unknown-llm"},
    )
    mw.after_model({"messages": [fake_msg]}, runtime=None)


def test_latency_under_warn_threshold_silent():
    """Fast LLM call shouldn't trigger warn-level log."""
    ltm = _load_latency()
    mw = ltm.LatencyTrackingMiddleware(warn_threshold_s=10.0)
    state = {"messages": [SimpleNamespace(response_metadata={"model_name": "fast-llm"})]}
    mw.before_model(state, runtime=None)
    mw.after_model(state, runtime=None)
    # No assertion needed; verifying no exception


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_estimate_cost_known_model,
        test_estimate_cost_unknown_model_returns_zero,
        test_estimate_cost_prefix_match,
        test_register_model_price_then_estimate,
        test_token_usage_after_model_emits_metrics,
        test_token_usage_handles_missing_metadata,
        test_token_usage_handles_unknown_model,
        test_latency_basic_path,
        test_latency_after_without_before_silent,
        test_latency_under_warn_threshold_silent,
        test_recursion_soft_warn_injected,
        test_recursion_hard_strips_tool_calls,
        test_recursion_invalid_limits_raise,
        test_recursion_guard_state_resets_after_hard,
        test_pii_scrub_email,
        test_pii_scrub_phone_cn_and_us,
        test_pii_scrub_apikey,
        test_pii_apikey_takes_priority_over_cc,
        test_pii_scrub_ipv4,
        test_pii_scrub_no_pii_unchanged,
        test_pii_middleware_scrubs_human_messages,
        test_pii_middleware_skips_when_no_messages,
        test_pii_middleware_skips_when_no_pii_found,
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
