"""Post-retrieval reflection.

Asks the LLM to score evidence sufficiency and (if insufficient) propose a
follow-up query. The agentic loop uses this to decide whether to iterate.
"""

from __future__ import annotations

import json
import re

from ..utils.logger import get_logger
from .llm import chat

log = get_logger("rag.reflect")

_PROMPT = """You evaluate whether the retrieved evidence is sufficient to answer
a research question. Reply with a JSON object:

  "sufficiency": one of "sufficient" | "partial" | "insufficient"
  "missing":     short description of what is missing (empty string if sufficient)
  "follow_up":   a single follow-up search query (empty string if sufficient)
  "score":       float in [0,1]

Question: {q}

Evidence (truncated):
{evidence}

Return only JSON.
"""


def reflect(question: str, evidence: str) -> dict:
    truncated = evidence[:6000]
    try:
        raw = chat(
            [{"role": "user", "content": _PROMPT.replace("{q}", question).replace("{evidence}", truncated)}],
            temperature=0,
            max_tokens=300,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.warning(f"reflect failed: {e}; assume sufficient to avoid loops")
        return {"sufficiency": "sufficient", "missing": "", "follow_up": "", "score": 0.5}

    return {
        "sufficiency": data.get("sufficiency", "sufficient"),
        "missing": data.get("missing", ""),
        "follow_up": data.get("follow_up", ""),
        "score": float(data.get("score", 0.5)),
    }


__all__ = [
    "reflect",
]
