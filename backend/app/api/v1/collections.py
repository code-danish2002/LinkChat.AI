from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_chat, get_collections, get_principal
from app.api.schemas import (
    CollectionResponse,
    ConversationResponse,
    CreateCollectionRequest,
    SourceResponse,
    UpdateCollectionRequest,
)
from app.domain.models import Collection, Principal
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService

router = APIRouter(prefix="/collections", tags=["collections"])


async def _response(service: CollectionService, collection: Collection) -> CollectionResponse:
    return CollectionResponse.from_domain(collection, await service.ready_count(collection))


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CreateCollectionRequest,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    """Group sources so they can be searched and chatted with as one corpus."""
    return await _response(service, await service.create(principal.owner_id, body.title, body.source_ids))


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    return [await _response(service, c) for c in await service.list(principal.owner_id, limit)]


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection(
    collection_id: str,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    return await _response(service, await service.get(principal.owner_id, collection_id))


@router.patch("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: str,
    body: UpdateCollectionRequest,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    updated = await service.update(principal.owner_id, collection_id, title=body.title, source_ids=body.source_ids)
    return await _response(service, updated)


@router.post("/{collection_id}/sources/{source_id}", response_model=CollectionResponse)
async def add_collection_source(
    collection_id: str,
    source_id: str,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    return await _response(service, await service.add_source(principal.owner_id, collection_id, source_id))


@router.delete("/{collection_id}/sources/{source_id}", response_model=CollectionResponse)
async def remove_collection_source(
    collection_id: str,
    source_id: str,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    return await _response(service, await service.remove_source(principal.owner_id, collection_id, source_id))


@router.get("/{collection_id}/sources", response_model=list[SourceResponse])
async def list_collection_sources(
    collection_id: str,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    return [SourceResponse.from_domain(s) for s in await service.members(principal.owner_id, collection_id)]


@router.get("/{collection_id}/conversations", response_model=list[ConversationResponse])
async def list_collection_conversations(
    collection_id: str,
    principal: Principal = Depends(get_principal),
    chat: ChatService = Depends(get_chat),
):
    conversations = await chat.list_collection_conversations(principal.owner_id, collection_id)
    return [ConversationResponse.from_domain(c) for c in conversations]


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str,
    principal: Principal = Depends(get_principal),
    service: CollectionService = Depends(get_collections),
):
    await service.delete(principal.owner_id, collection_id)
