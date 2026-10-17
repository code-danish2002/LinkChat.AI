"""Collections: group sources so they can be queried as one corpus."""

from __future__ import annotations

from app.core.exceptions import NotFoundError, ValidationError
from app.core.ids import new_id
from app.domain.models import Collection, Source, SourceStatus
from app.domain.ports import CollectionRepository, SourceRepository


class CollectionService:
    def __init__(
        self,
        collections: CollectionRepository,
        sources: SourceRepository,
        *,
        max_sources: int = 20,
    ):
        self._collections = collections
        self._sources = sources
        self._max_sources = max_sources

    def create(self, title: str, source_ids: list[str] | None = None) -> Collection:
        name = (title or "").strip()
        if not name:
            raise ValidationError("A collection needs a name.")
        members = self._validate(source_ids or [])
        collection = Collection(id=new_id("col"), title=name[:200], source_ids=members)
        return self._collections.add(collection)

    def get(self, collection_id: str) -> Collection:
        collection = self._collections.get(collection_id)
        if collection is None:
            raise NotFoundError("Collection not found.")
        return collection

    def list(self, limit: int = 50) -> list[Collection]:
        return self._collections.list(limit)

    def update(
        self, collection_id: str, *, title: str | None = None, source_ids: list[str] | None = None
    ) -> Collection:
        collection = self.get(collection_id)
        if title is not None:
            name = title.strip()
            if not name:
                raise ValidationError("A collection needs a name.")
            collection.title = name[:200]
        if source_ids is not None:
            collection.source_ids = self._validate(source_ids)
        return self._collections.update(collection)

    def add_source(self, collection_id: str, source_id: str) -> Collection:
        collection = self.get(collection_id)
        if source_id in collection.source_ids:
            return collection
        return self.update(collection_id, source_ids=[*collection.source_ids, source_id])

    def remove_source(self, collection_id: str, source_id: str) -> Collection:
        collection = self.get(collection_id)
        remaining = [sid for sid in collection.source_ids if sid != source_id]
        return self.update(collection_id, source_ids=remaining)

    def delete(self, collection_id: str) -> None:
        self.get(collection_id)
        self._collections.delete(collection_id)

    def members(self, collection_id: str) -> list[Source]:
        collection = self.get(collection_id)
        found = (self._sources.get(sid) for sid in collection.source_ids)
        return [source for source in found if source is not None]

    def ready_count(self, collection: Collection) -> int:
        ready = 0
        for source_id in collection.source_ids:
            source = self._sources.get(source_id)
            if source is not None and source.status is SourceStatus.READY:
                ready += 1
        return ready

    # ---- internals ----

    def _validate(self, source_ids: list[str]) -> list[str]:
        unique: list[str] = []
        for source_id in source_ids:
            if source_id in unique:
                continue
            if self._sources.get(source_id) is None:
                raise NotFoundError(f"Source {source_id} does not exist.")
            unique.append(source_id)
        if len(unique) > self._max_sources:
            raise ValidationError(f"A collection holds at most {self._max_sources} sources.")
        return unique
