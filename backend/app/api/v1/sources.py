from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.api.deps import get_chat, get_ingestion
from app.api.schemas import ConversationResponse, CreateSourceRequest, SourceResponse
from app.core.exceptions import ValidationError
from app.services.chat_service import ChatService
from app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_source(
    body: CreateSourceRequest, ingestion: IngestionService = Depends(get_ingestion)
):
    """Register a URL and ingest it in the background. Poll GET /sources/{id} until `ready`."""
    return SourceResponse.from_domain(await ingestion.submit(body.url))


@router.post("/upload", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_source(
    file: UploadFile = File(...), ingestion: IngestionService = Depends(get_ingestion)
):
    """Upload a document or audio file instead of linking to one."""
    if not file.filename:
        raise ValidationError("The upload has no filename.")
    payload = await file.read()
    return SourceResponse.from_domain(await ingestion.submit_upload(file.filename, payload))


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    limit: int = Query(default=50, ge=1, le=200),
    ingestion: IngestionService = Depends(get_ingestion),
):
    return [SourceResponse.from_domain(s) for s in ingestion.list(limit)]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str, ingestion: IngestionService = Depends(get_ingestion)):
    return SourceResponse.from_domain(ingestion.get(source_id))


@router.post(
    "/{source_id}/reingest", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED
)
async def reingest_source(source_id: str, ingestion: IngestionService = Depends(get_ingestion)):
    return SourceResponse.from_domain(await ingestion.reingest(source_id))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: str, ingestion: IngestionService = Depends(get_ingestion)):
    await ingestion.delete(source_id)


@router.get("/{source_id}/conversations", response_model=list[ConversationResponse])
async def list_source_conversations(source_id: str, chat: ChatService = Depends(get_chat)):
    return [ConversationResponse.from_domain(c) for c in chat.list_conversations(source_id)]
