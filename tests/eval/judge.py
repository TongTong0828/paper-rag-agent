"""LLM-as-a-judge for answer quality (optional)."""

from __future__ import annotations

import json
import re

from paper_rag.rag.llm import chat
from paper_rag.utils.logger import get_logger

log = get_logger("eval.judge")

_PROMPT = """You are evaluating a research-assistant's answer.

Score on three dimensions, each 1 (worst) to 5 (best). Return ONLY JSON:

  "faithful":   does every claim follow from the cited evidence?
  "complete":   does it cover the gold answer's key points?
  "concise":    is it appropriately compact, no padding?

Question:
{q}

Gold reference (may be partial):
{gold}

Candidate answer:
{ans}

Return: {"faithful": int, "complete": int, "concise": int, "reason": "..."}.
"""


def judge(question: str, gold: str | None, candidate: str) -> dict:
    if not gold:
        return {"skipped": True}
    try:
        raw = chat(
            [{"role": "user", "content": _PROMPT
                .replace("{q}", question)
                .replace("{gold}", gold)
                .replace("{ans}", candidate)}],
            temperature=0,
            max_tokens=300,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        return {
            "faithful": int(data.get("faithful", 0)),
            "complete": int(data.get("complete", 0)),
            "concise": int(data.get("concise", 0)),
            "reason": data.get("reason", ""),
        }
    except Exception as e:
        log.warning(f"judge failed: {e}")
        return {"error": str(e)}
