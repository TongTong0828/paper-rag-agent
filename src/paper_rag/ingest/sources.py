"""Abstract paper source interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .schema import FetchResult


class PaperSource(ABC):
    name: str = "abstract"

    @abstractmethod
    def fetch(self, identifier: str) -> FetchResult:
        """Fetch metadata + PDF for `identifier`.

        `identifier` semantics depend on source (arxiv id, DOI, local path, URL).
        """
        raise NotImplementedError
