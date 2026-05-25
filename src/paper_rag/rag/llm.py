"""Tiny OpenAI-compatible chat client.

Reads base_url/api_key/model from config. Returns plain string content.
"""

from __future__ import annotations

from .. import config as cfg
from ..utils.logger import get_logger


log = get_logger("rag.llm")


def chat(messages: list[dict], *, model: str | None = None, temperature: float = 0.2,
         max_tokens: int = 1024) -> str:
    c = cfg.load().llm
    if not (c.base_url and c.api_key):
        raise RuntimeError(
            "LLM config missing. Set OPENAI_BASE_URL / OPENAI_API_KEY / CHAT_MODEL env vars."
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai package not installed. Run: pip install openai") from e

    client = OpenAI(base_url=c.base_url, api_key=c.api_key)
    chosen = model or c.chat_model
    if not chosen:
        raise RuntimeError("CHAT_MODEL env / config.llm.chat_model not set")
    resp = client.chat.completions.create(
        model=chosen,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""
