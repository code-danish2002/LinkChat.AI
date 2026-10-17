from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_chat, get_collections
from app.api.schemas import (
    CollectionResponse,
    ConversationResponse,
    CreateCollectionRequest,
    SourceResponse,
    UpdateCollectionRequest,
)
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService

router = APIRouter(prefix="/collections", tags=["collections"])


def _response(service: CollectionService, collection) -> CollectionResponse:
    return CollectionResponse.from_domain(collection, service.ready_count(collection))


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CreateCollectionRequest, service: CollectionService = Depends(get_collections)
):
    """Group sources so they can be searched and chatted with as one corpus."""
    return _response(service, service.create(body.title, body.source_ids))


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    limit: int = Query(default=50, ge=1, le=200),
    service: CollectionService = Depends(get_collections),
):
    return [_response(service, c) for c in service.list(limit)]


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection(collection_id: str, service: CollectionService = Depends(get_collections)):
    return _response(service, service.get(collection_id))


@router.patch("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: str,
    body: UpdateCollectionRequest,
    service: CollectionService = Depends(get_collections),
):
    updated = service.update(collection_id, title=body.title, source_ids=body.source_ids)
    return _response(service, updated)


@router.post("/{collection_id}/sources/{source_id}", response_model=CollectionResponse)
async def add_collection_source(
    collection_id: str, source_id: str, service: CollectionService = Depends(get_collections)
):
    return _response(service, service.add_source(collection_id, source_id))


@router.delete("/{collection_id}/sources/{source_id}", response_model=CollectionResponse)
async def remove_collection_source(
    collection_id: str, source_id: str, service: CollectionService = Depends(get_collections)
):
    return _response(service, service.remove_source(collection_id, source_id))


@router.get("/{collection_id}/sources", response_model=list[SourceResponse])
async def list_collection_sources(
    collection_id: str, service: CollectionService = Depends(get_collections)
):
    return [SourceResponse.from_domain(s) for s in service.members(collection_id)]


@router.get("/{collection_id}/conversations", response_model=list[ConversationResponse])
async def list_collection_conversations(collection_id: str, chat: ChatService = Depends(get_chat)):
    return [
        ConversationResponse.from_domain(c) for c in chat.list_collection_conversations(collection_id)
    ]


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str, service: CollectionService = Depends(get_collections)
):
    service.delete(collection_id)
