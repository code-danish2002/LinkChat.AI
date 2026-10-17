"""Picks the loader for a URL.

Loaders are consulted in registration order; the first one whose
`supports()` returns True wins, so specific services (YouTube today, more
tomorrow) register before the generic HTTP catch-all. Adding a new service
means writing one SourceLoader and registering it in the container.
"""

from __future__ import annotations

from app.core.exceptions import UnsupportedSourceError
from app.domain.ports import SourceLoader


class SourceResolver:
    def __init__(self, loaders: list[SourceLoader]):
        if not loaders:
            raise ValueError("at least one loader is required")
        self._loaders = list(loaders)

    def register(self, loader: SourceLoader, *, first: bool = False) -> None:
        if first:
            self._loaders.insert(0, loader)
        else:
            self._loaders.append(loader)

    def resolve(self, url: str) -> SourceLoader:
        for loader in self._loaders:
            if loader.supports(url):
                return loader
        raise UnsupportedSourceError("No loader knows how to read that URL.")

    @property
    def kinds(self) -> list[str]:
        """What this server accepts, in the order loaders are consulted."""
        return [loader.label or loader.kind.value for loader in self._loaders]
