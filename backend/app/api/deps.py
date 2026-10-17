"""FastAPI dependencies that hand out services from the container."""

from __future__ import annotations

from fastapi import Request

from app.core.container import Container
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.ingestion_service import IngestionService


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_ingestion(request: Request) -> IngestionService:
    return get_container(request).ingestion


def get_chat(request: Request) -> ChatService:
    return get_container(request).chat


def get_collections(request: Request) -> CollectionService:
    return get_container(request).collections
