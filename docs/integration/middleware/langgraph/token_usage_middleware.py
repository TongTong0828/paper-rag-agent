"""Token usage tracking middleware (M11.1).

Three concerns:
  1. **Logging** — single line per LLM call (input/output/total tokens).
  2. **Prometheus** — counter + histogram so /metrics shows per-model usage.
  3. **Cost estimation** — multiplies tokens by model-specific USD/1k prices
     to give a running cost counter (best-effort, prices are stale by
     definition; update ``_PRICE_TABLE`` periodically).

Industrial properties:
- Failures NEVER raise out of the middleware. Token tracking should never
  break agent flow.
- Prometheus metrics use ``paper_rag.observability.metrics`` so they show
  up at the existing /metrics endpoint with no extra wiring.
- Price table is mutable so deployments can inject custom prices via
  ``register_model_price(model, input_per_1k, output_per_1k)``.
"""
from __future__ import annotations

import logging
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# USD per 1000 tokens. Conservative defaults; not a substitute for billing.
# Override at runtime with register_model_price().
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # input, output
    "gpt-4o":            (0.0025, 0.010),
    "gpt-4o-mini":       (0.00015, 0.0006),
    "gpt-4-turbo":       (0.010, 0.030),
    "gpt-4":             (0.030, 0.060),
    "gpt-3.5-turbo":     (0.0005, 0.0015),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-haiku":    (0.00025, 0.00125),
    "claude-3-opus":     (0.015, 0.075),
    "qwen-plus":         (0.0008, 0.002),
    "qwen-max":          (0.0040, 0.0120),
    "qwen-turbo":        (0.0003, 0.0006),
    "deepseek-chat":     (0.00027, 0.00110),
}


def register_model_price(model: str, input_per_1k: float, output_per_1k: float) -> None:
    """Register / override prices for a model name. Idempotent."""
    _PRICE_TABLE[model] = (float(input_per_1k), float(output_per_1k))


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost estimate. Returns 0.0 if model is unknown."""
    if not model:
        return 0.0
    # Try exact match, then prefix match (e.g. "qwen-plus-1234" → "qwen-plus")
    p = _PRICE_TABLE.get(model)
    if p is None:
        for k, v in _PRICE_TABLE.items():
            if model.startswith(k):
                p = v
                break
    if p is None:
        return 0.0
    in_cost = (input_tokens / 1000.0) * p[0]
    out_cost = (output_tokens / 1000.0) * p[1]
    return round(in_cost + out_cost, 6)


def _safe_metric(fn, *args, **kwargs):
    """Call paper_rag.observability.metrics safely. Returns None on failure."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — metrics never block agent
        return None


class TokenUsageMiddleware(AgentMiddleware):
    """Logs + counts + costs LLM token usage on every after_model hook."""

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record(state, runtime)
        return None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._record(state, runtime)
        return None

    def _record(self, state: AgentState, runtime: Runtime) -> None:
        messages = state.get("messages", [])
        if not messages:
            return
        last = messages[-1]
        usage = getattr(last, "usage_metadata", None)
        if not usage:
            return

        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        total = int(usage.get("total_tokens", in_tok + out_tok) or 0)

        # Best-effort: pull model name from last message's response_metadata
        # (LangChain populates this for OpenAI/Anthropic providers).
        model = ""
        meta = getattr(last, "response_metadata", None) or {}
        if isinstance(meta, dict):
            model = meta.get("model_name") or meta.get("model") or ""

        cost = estimate_cost(model, in_tok, out_tok)

        logger.info(
            "LLM token usage: model=%s input=%d output=%d total=%d cost_usd=%.6f",
            model or "unknown", in_tok, out_tok, total, cost,
        )

        # Prometheus
        try:
            from paper_rag.observability.metrics import counter
            labels = {"model": model or "unknown"}
            _safe_metric(counter, "deerflow_llm_tokens_input_total", labels)
            tok_in_c = counter("deerflow_llm_tokens_input_total", labels)
            tok_out_c = counter("deerflow_llm_tokens_output_total", labels)
            calls_c = counter("deerflow_llm_calls_total", labels)
            cost_c = counter("deerflow_llm_cost_usd_total", labels)
            tok_in_c.inc(in_tok)
            tok_out_c.inc(out_tok)
            calls_c.inc()
            if cost > 0:
                cost_c.inc(cost)
        except Exception as e:  # noqa: BLE001
            logger.debug("token usage metrics emit failed (non-fatal): %s", e)
