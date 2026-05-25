"""LLM-based intent classifier.

Maps a user question to one of:
  - factual:   single-fact lookup, single hop, topK=5
  - reasoning: multi-aspect or comparison, multi-hop, topK=10, max_iter=2
  - explore:   broad survey/landscape, topK=15, max_iter=3 + paper-graph expansion

Returns a dict; never raises (LLM unavailable -> returns 'reasoning' default).
"""

from __future__ import annotations

import json
import re

from ..utils.logger import get_logger
from .llm import chat


log = get_logger("rag.intent")

_PROMPT = """You classify research questions into one of three intents:

- "factual": one specific fact (definition, number, single-paper detail).
- "reasoning": comparison, analysis, multi-aspect (e.g. "how do X and Y differ").
- "explore": broad landscape/survey (e.g. "what are recent advances in ...").

Return ONLY a JSON object: {"intent": "...", "reason": "..."}.

Question: {q}
"""


_DEFAULTS = {
    "factual":   {"top_k": 5,  "max_iter": 1, "rrf_k": 60},
    "reasoning": {"top_k": 10, "max_iter": 2, "rrf_k": 60},
    "explore":   {"top_k": 15, "max_iter": 3, "rrf_k": 60},
}


def classify(question: str) -> dict:
    try:
        raw = chat(
            [{"role": "user", "content": _PROMPT.replace("{q}", question)}],
            temperature=0,
            max_tokens=120,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {"intent": "reasoning"}
        intent = data.get("intent", "reasoning")
    except Exception as e:
        log.warning(f"intent classify failed: {e}; default reasoning")
        intent = "reasoning"
    if intent not in _DEFAULTS:
        intent = "reasoning"
    out = {"intent": intent, **_DEFAULTS[intent]}
    log.info(f"intent={intent} cfg={out}")
    return out
