from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.api.schemas import (
    CapabilitiesResponse,
    HealthResponse,
    ModelPoolResponse,
    ModelStatusResponse,
    ModeResponse,
)
from app.core.container import Container
from app.ingestion.extractors import SUPPORTED_UPLOAD_EXTENSIONS
from app.services.modes import all_modes

router = APIRouter(tags=["system"])


def _capabilities(container: Container) -> CapabilitiesResponse:
    renderer = container.renderer
    return CapabilitiesResponse(
        uploads=True,
        audio_transcription=bool(container.transcriber and container.transcriber.available),
        javascript_rendering=renderer is not None and renderer.unavailable_reason is None,
        collections=True,
        upload_extensions=list(SUPPORTED_UPLOAD_EXTENSIONS),
        max_upload_mb=container.settings.max_upload_bytes // (1024 * 1024),
        email_login=container.auth.email_provider,
        guest_ttl_hours=container.settings.guest_ttl_hours,
        session_ttl_hours=container.settings.session_ttl_hours,
    )


@router.get("/health", response_model=HealthResponse)
async def health(container: Container = Depends(get_container)):
    database_ok = await container.db.ping() if container.db is not None else True
    return HealthResponse(
        status="ok" if database_ok else "degraded",
        version=container.settings.app_version,
        embedding_model=container.embeddings.model_name,
        active_models=container.router.active_count(),
        database="ok" if database_ok else "unreachable",
        supported_sources=container.resolver.kinds,
        capabilities=_capabilities(container),
    )


@router.get("/modes", response_model=list[ModeResponse])
async def modes():
    """Everything LinkChat can do with a source, for building the UI."""
    return [ModeResponse.from_domain(spec) for spec in all_modes()]


@router.get("/models", response_model=ModelPoolResponse)
async def models(container: Container = Depends(get_container)):
    statuses = container.router.snapshot()
    return ModelPoolResponse(
        active_count=sum(1 for s in statuses if s.active),
        total_count=len(statuses),
        models=[ModelStatusResponse.from_domain(s) for s in statuses],
    )
