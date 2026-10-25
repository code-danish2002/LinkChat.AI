from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, collections, conversations, sources, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(sources.router)
api_router.include_router(collections.router)
api_router.include_router(conversations.router)
