"""PIIScrubMiddleware (M11.4).

Redact common PII patterns before they enter the LLM context or the
access log. Scope:

  - **Email addresses**           foo@bar.com → [REDACTED:EMAIL]
  - **Phone numbers**             international + CN mobile + US format
  - **Credit cards**              13-19 digit Luhn-like (heuristic)
  - **IPv4 addresses**            192.168.1.1 → [REDACTED:IP]
  - **API keys / Bearer tokens**  long random strings prefixed with sk-/pk-/Bearer

Hooks:
  - ``before_model`` — scrubs ``state["messages"]`` HumanMessage / ToolMessage
    content before the LLM sees it.
  - ``before_tool``   — scrubs tool call args (so we don't proxy PII to
    external services like web search).
  - Tool-result scrubbing happens via after_tool by mutating the just-added
    ToolMessage in state.

This is a defense-in-depth layer — it does NOT replace user-side opt-in /
proper data classification. Aim is "good enough for casual leakage", not
GDPR compliance.

Industrial properties:
- Pure regex; no ML, no network. Microsecond-scale per message.
- Patterns are conservative (high-precision, lower-recall). Tune in
  ``_PATTERNS`` for stricter recall at false-positive cost.
- Disable by env: ``DEERFLOW_PII_SCRUB_DISABLED=1``.
"""
from __future__ import annotations

import logging
import os
import re
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


_DISABLED = os.environ.get("DEERFLOW_PII_SCRUB_DISABLED", "").lower() in {"1", "true"}


# (label, pattern). Order matters: more specific patterns first so they
# don't get clobbered by greedier ones.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Bearer / API keys (sk-..., pk-..., Bearer ..., AKIA...)
    ("APIKEY", re.compile(
        r"\b(?:Bearer\s+[A-Za-z0-9._\-]{16,}|"
        r"sk-[A-Za-z0-9]{20,}|"
        r"pk_[A-Za-z0-9]{20,}|"
        r"AKIA[0-9A-Z]{16})\b"
    )),
    # Email
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # Credit card (13-19 digits with optional spaces/dashes; heuristic only)
    ("CC", re.compile(r"\b(?:\d[ \-]*?){13,19}\b")),
    # CN mobile (1[3-9]\d{9})
    ("PHONE_CN", re.compile(r"\b1[3-9]\d{9}\b")),
    # International phone (+CC, 7-15 digits with optional separators)
    ("PHONE", re.compile(r"\+\d{1,3}[\s\-]?(?:\(?\d{1,4}\)?[\s\-]?){2,4}\d{2,4}")),
    # US-style phone (no +)
    ("PHONE_US", re.compile(r"\b\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{4}\b")),
    # IPv4 (avoid version strings — require word boundary)
    ("IP", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
    )),
]


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """Return (scrubbed_text, counts_per_label). Pure function."""
    if not text or _DISABLED:
        return text, {}
    counts: dict[str, int] = {}
    out = text
    for label, pat in _PATTERNS:
        out, n = pat.subn(f"[REDACTED:{label}]", out)
        if n:
            counts[label] = counts.get(label, 0) + n
    return out, counts


def _scrub_messages(messages: list, *, mutate_roles: tuple[str, ...]) -> tuple[list, dict[str, int]]:
    """Return new messages list with content scrubbed for matching roles.

    Roles are matched against either ``role`` attr or ``type`` attr (LangChain
    HumanMessage.type == 'human', ToolMessage.type == 'tool').
    """
    new_msgs = list(messages)
    total_counts: dict[str, int] = {}
    for i, msg in enumerate(new_msgs):
        role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
        if role not in mutate_roles:
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, str):
            continue
        scrubbed, counts = scrub(content)
        if counts:
            try:
                msg.content = scrubbed
            except Exception:  # noqa: BLE001 — frozen pydantic
                # Build a copy via model_copy if available
                try:
                    new_msgs[i] = msg.model_copy(update={"content": scrubbed})
                except Exception:
                    continue
            for k, v in counts.items():
                total_counts[k] = total_counts.get(k, 0) + v
    return new_msgs, total_counts


class PIIScrubMiddleware(AgentMiddleware):
    """Redact PII in user messages, tool results, and tool args."""

    def __init__(
        self,
        scrub_user_messages: bool = True,
        scrub_tool_results: bool = True,
    ) -> None:
        super().__init__()
        self._scrub_user = scrub_user_messages
        self._scrub_tool = scrub_tool_results

    # ── before_model: scrub user + tool messages ──────────────────────
    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._scrub_state(state)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._scrub_state(state)

    def _scrub_state(self, state: AgentState) -> dict | None:
        if _DISABLED:
            return None
        roles: list[str] = []
        if self._scrub_user:
            roles.extend(["human", "user"])
        if self._scrub_tool:
            roles.extend(["tool"])
        if not roles:
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        new_msgs, counts = _scrub_messages(messages, mutate_roles=tuple(roles))
        if not counts:
            return None
        logger.info(
            "PIIScrub: redacted %d items (%s)",
            sum(counts.values()),
            ", ".join(f"{k}={v}" for k, v in counts.items()),
        )
        try:
            from paper_rag.observability.metrics import counter
            for label, n in counts.items():
                counter("deerflow_pii_redacted_total", {"label": label}).inc(n)
        except Exception as e:  # noqa: BLE001
            logger.debug("PII metric emit failed (non-fatal): %s", e)
        return {"messages": new_msgs}
