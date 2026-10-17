"""Server-Sent Events helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.responses import StreamingResponse


def format_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
