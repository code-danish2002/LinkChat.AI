from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_chat
from app.api.schemas import (
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
)
from app.api.sse import format_event, sse_response
from app.core.exceptions import AppError
from app.services.chat_service import ChatService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest, chat: ChatService = Depends(get_chat)
):
    """Open a thread against one source or one collection."""
    conversation = chat.create_conversation(
        source_id=body.source_id, title=body.title, collection_id=body.collection_id
    )
    return ConversationResponse.from_domain(conversation)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str, chat: ChatService = Depends(get_chat)):
    return ConversationResponse.from_domain(chat.get_conversation(conversation_id))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, chat: ChatService = Depends(get_chat)):
    chat.delete_conversation(conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(conversation_id: str, chat: ChatService = Depends(get_chat)):
    return [MessageResponse.from_domain(m) for m in chat.list_messages(conversation_id)]


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str, body: SendMessageRequest, chat: ChatService = Depends(get_chat)
):
    """Stream the answer as Server-Sent Events.

    Events: `citations` (excerpts used), `progress` (status during multi-pass
    work), `model` (which pool member answered), `token` (text delta), then
    `done` or `error`.
    """
    # Validate up front so 404 and 409 stay real HTTP errors rather than SSE events.
    conversation = chat.get_conversation(conversation_id)

    async def generate():
        try:
            async for event in chat.stream_answer(conversation.id, body.content, body.mode):
                yield format_event(event.type, event.data)
        except AppError as exc:
            yield format_event("error", exc.message)

    return sse_response(generate())
