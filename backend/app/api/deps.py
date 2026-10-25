"""FastAPI dependencies: services from the container, and who is asking.

Identity arrives in one of two headers. `Authorization: Bearer <token>` is
a signed-in user's session; `X-Guest-Id: gst_...` is the id a browser made
up for itself. A request carrying neither is refused, because every owned
row needs an owner.
"""

from __future__ import annotations

from fastapi import Depends, Header, Request

from app.core.container import Container
from app.domain.models import Principal
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.ingestion_service import IngestionService

GUEST_HEADER = "X-Guest-Id"


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_ingestion(request: Request) -> IngestionService:
    return get_container(request).ingestion


def get_chat(request: Request) -> ChatService:
    return get_container(request).chat


def get_collections(request: Request) -> CollectionService:
    return get_container(request).collections


def get_auth(request: Request) -> AuthService:
    return get_container(request).auth


def bearer_token(authorization: str | None = Header(default=None)) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def guest_header(x_guest_id: str | None = Header(default=None, alias=GUEST_HEADER)) -> str | None:
    return x_guest_id.strip() if x_guest_id else None


async def get_principal(
    auth: AuthService = Depends(get_auth),
    token: str | None = Depends(bearer_token),
    guest_id: str | None = Depends(guest_header),
) -> Principal:
    return await auth.resolve(bearer=token, guest_id=guest_id)
