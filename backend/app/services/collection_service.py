"""Collections: group an owner's sources so they can be queried as one corpus."""

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

    async def create(self, owner_id: str, title: str, source_ids: list[str] | None = None) -> Collection:
        name = (title or "").strip()
        if not name:
            raise ValidationError("A collection needs a name.")
        members = await self._validate(owner_id, source_ids or [])
        collection = Collection(id=new_id("col"), owner_id=owner_id, title=name[:200], source_ids=members)
        return await self._collections.add(collection)

    async def get(self, owner_id: str, collection_id: str) -> Collection:
        collection = await self._collections.get(owner_id, collection_id)
        if collection is None:
            raise NotFoundError("Collection not found.")
        return collection

    async def list(self, owner_id: str, limit: int = 50) -> list[Collection]:
        return await self._collections.list(owner_id, limit)

    async def update(
        self,
        owner_id: str,
        collection_id: str,
        *,
        title: str | None = None,
        source_ids: list[str] | None = None,
    ) -> Collection:
        collection = await self.get(owner_id, collection_id)
        if title is not None:
            name = title.strip()
            if not name:
                raise ValidationError("A collection needs a name.")
            collection.title = name[:200]
        if source_ids is not None:
            collection.source_ids = await self._validate(owner_id, source_ids)
        return await self._collections.update(collection)

    async def add_source(self, owner_id: str, collection_id: str, source_id: str) -> Collection:
        collection = await self.get(owner_id, collection_id)
        if source_id in collection.source_ids:
            return collection
        return await self.update(owner_id, collection_id, source_ids=[*collection.source_ids, source_id])

    async def remove_source(self, owner_id: str, collection_id: str, source_id: str) -> Collection:
        collection = await self.get(owner_id, collection_id)
        remaining = [sid for sid in collection.source_ids if sid != source_id]
        return await self.update(owner_id, collection_id, source_ids=remaining)

    async def delete(self, owner_id: str, collection_id: str) -> None:
        await self.get(owner_id, collection_id)
        await self._collections.delete(owner_id, collection_id)

    async def members(self, owner_id: str, collection_id: str) -> list[Source]:
        collection = await self.get(owner_id, collection_id)
        return await self._sources.get_many(owner_id, collection.source_ids)

    async def ready_count(self, collection: Collection) -> int:
        members = await self._sources.get_many(collection.owner_id, collection.source_ids)
        return sum(1 for source in members if source.status is SourceStatus.READY)

    # ---- internals ----

    async def _validate(self, owner_id: str, source_ids: list[str]) -> list[str]:
        unique: list[str] = []
        for source_id in source_ids:
            if source_id not in unique:
                unique.append(source_id)
        if len(unique) > self._max_sources:
            raise ValidationError(f"A collection holds at most {self._max_sources} sources.")
        found = {source.id for source in await self._sources.get_many(owner_id, unique)}
        for source_id in unique:
            if source_id not in found:
                raise NotFoundError(f"Source {source_id} does not exist.")
        return unique
