"""Tiny OpenAI-compatible chat client.

Reads base_url/api_key/model from config. Returns plain string content.

The OpenAI client is cached as a module-level singleton — constructing it
involves TLS/HTTPX setup that is wasteful to repeat on every call. The cache
is invalidated automatically when the config (base_url / api_key) changes,
so live config reload still works.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("rag.llm")

# Module-level singleton state.
_CLIENT: Any | None = None
_CLIENT_KEY: tuple[str, str] | None = None  # (base_url, api_key) used to build _CLIENT
_LOCK = Lock()


def get_client():
    """Return a process-shared OpenAI client.

    Built lazily on first use. Re-created if the config's base_url or api_key
    changes between calls (e.g. test monkey-patch, hot config reload).
    """
    global _CLIENT, _CLIENT_KEY  # noqa: PLW0603 — module-level cache by design

    c = cfg.load().llm
    if not (c.base_url and c.api_key):
        raise RuntimeError(
            "LLM config missing. Set OPENAI_BASE_URL / OPENAI_API_KEY / CHAT_MODEL env vars."
        )
    key = (c.base_url, c.api_key)

    # Fast path — already built and config hasn't changed.
    if _CLIENT is not None and _CLIENT_KEY == key:
        return _CLIENT

    with _LOCK:
        if _CLIENT is not None and _CLIENT_KEY == key:
            return _CLIENT
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            ) from e
        _CLIENT = OpenAI(base_url=c.base_url, api_key=c.api_key)
        _CLIENT_KEY = key
        log.debug("OpenAI client (re)built for base_url=%s", c.base_url)
        return _CLIENT


def reset_client_for_test() -> None:
    """Drop the cached client. Used by tests that monkey-patch config."""
    global _CLIENT, _CLIENT_KEY  # noqa: PLW0603
    with _LOCK:
        _CLIENT = None
        _CLIENT_KEY = None


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    c = cfg.load().llm
    chosen = model or c.chat_model
    if not chosen:
        raise RuntimeError("CHAT_MODEL env / config.llm.chat_model not set")
    resp = get_client().chat.completions.create(
        model=chosen,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


__all__ = ["chat", "get_client", "reset_client_for_test"]
