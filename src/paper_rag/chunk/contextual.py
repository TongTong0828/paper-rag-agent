"""Add context prefix to chunks for retrieval-time embedding."""

from __future__ import annotations

from .. import config as cfg


def with_context(text: str, *, title: str, section: str) -> str:
    template = cfg.load().chunk.context_prefix
    prefix = template.format(title=title or "", section=section or "")
    return f"{prefix}{text}"
