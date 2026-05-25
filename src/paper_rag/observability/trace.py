"""Trace id generation."""

from __future__ import annotations

import secrets


def new_trace_id() -> str:
    """16-char hex string. Cheap to generate, easy to grep."""
    return secrets.token_hex(8)
