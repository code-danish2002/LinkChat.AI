"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.rate_limit import RateLimitMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.core.exceptions import AppError
from app.core.logging import configure_logging

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container = Container.build(settings)
        app.state.container = container
        await container.start()
        log.info("%s v%s ready", settings.app_name, settings.app_version)
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Paste a URL, chat with its content. Open-source models with automatic failover.",
        lifespan=lifespan,
    )
    # Outermost so a rejected request costs nothing downstream.
    app.add_middleware(
        RateLimitMiddleware,
        chat_per_minute=settings.rate_limit_chat_per_minute,
        ingest_per_minute=settings.rate_limit_ingest_per_minute,
        general_per_minute=settings.rate_limit_general_per_minute,
        auth_per_minute=settings.rate_limit_auth_per_minute,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api": "/api/v1",
        }

    return app


app = create_app()
