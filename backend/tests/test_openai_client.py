"""Protocol test: the streaming client against an in-process fake OpenAI-compatible server."""

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.domain.models import ChatMessage
from app.providers.llm.health import FailureKind
from app.providers.llm.openai_compatible import OpenAICompatibleClient, ProviderError
from app.providers.llm.registry import ResolvedProvider

SEEN: dict = {}


async def completions(request: Request):
    body = await request.json()
    SEEN["body"] = body
    SEEN["auth"] = request.headers.get("authorization")
    model = body["model"]
    if model == "rate-limited":
        return JSONResponse({"error": {"message": "slow down"}}, status_code=429)
    if model == "bad-key":
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    if model == "missing":
        return JSONResponse({"error": {"message": "model not found"}}, status_code=404)

    async def gen():
        yield ": keep-alive\n\n"
        for i, word in enumerate(["Hello", ",", " world"]):
            chunk = {
                "id": "x",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": word, **({"reasoning": "hmm"} if i == 0 else {})},
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield 'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


app = Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"])])


@pytest.fixture
def client():
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")
    return OpenAICompatibleClient(http, timeout_seconds=5)


provider = ResolvedProvider(name="fake", base_url="http://fake/v1", api_key="secret", extra_headers={"X-Title": "t"})
messages = [ChatMessage("system", "sys"), ChatMessage("user", "hi")]


async def test_streams_content_deltas(client):
    out = [d async for d in client.stream_chat(provider, "good", messages, temperature=0.1, max_tokens=5)]
    assert "".join(out) == "Hello, world"
    assert SEEN["auth"] == "Bearer secret"
    assert SEEN["body"]["stream"] is True and SEEN["body"]["messages"][0] == {
        "role": "system",
        "content": "sys",
    }


@pytest.mark.parametrize(
    "model,kind,status",
    [
        ("rate-limited", FailureKind.RATE_LIMITED, 429),
        ("bad-key", FailureKind.HARD, 401),
        ("missing", FailureKind.HARD, 404),
    ],
)
async def test_error_classification(client, model, kind, status):
    with pytest.raises(ProviderError) as info:
        async for _ in client.stream_chat(provider, model, messages, temperature=0, max_tokens=5):
            pass
    assert info.value.kind is kind and info.value.status_code == status
    assert "fake" in info.value.message
