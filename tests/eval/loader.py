"""Load eval items from jsonl."""

from __future__ import annotations

import json
from pathlib import Path

from .schema import EvalItem


def load_jsonl(path: str | Path) -> list[EvalItem]:
    items: list[EvalItem] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"line {i}: invalid JSON: {e}") from e
            items.append(EvalItem.model_validate(obj))
    return items
