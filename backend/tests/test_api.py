"""End-to-end API tests with fake embeddings and a fake model pool (no network)."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.container import Container
from app.ingestion.chunker import Chunker
from app.ingestion.processor import PayloadProcessor
from app.ingestion.resolver import SourceResolver
from app.ingestion.upload import UploadLoader, UploadStore
from app.main import create_app
from app.providers.llm.health import BreakerRegistry
from app.providers.llm.registry import ModelRegistry, ModelSpec, ProviderSpec
from app.providers.llm.router import ModelRouter
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.retrieval_service import RetrievalService
from app.storage.db.sqlite import (
    Database,
    SqliteCollectionRepository,
    SqliteConversationRepository,
    SqliteSourceRepository,
)
from app.storage.vector.local_store import LocalHybridVectorStore
from tests.fakes import EchoLLMClient, FakeLoader, FakeTranscriber, HashEmbeddings


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "x")
    settings = Settings(
        data_dir=tmp_path,
        cors_origins="http://localhost:3000",
        validate_model_pool=False,
        mode_tokens_per_minute=0,  # no real waiting in tests
        mode_max_input_tokens=1100,  # minus the prompt reserve this leaves the 400-token floor
        chunk_size=300,
        chunk_overlap=50,
    )
    client_double = EchoLLMClient()

    def build(_settings):
        embeddings = HashEmbeddings()
        db = Database(tmp_path / "db.sqlite3")
        sources_repo = SqliteSourceRepository(db)
        conversations_repo = SqliteConversationRepository(db)
        collections_repo = SqliteCollectionRepository(db)
        store = LocalHybridVectorStore(tmp_path / "vectors", embedding_model=embeddings.model_name)
        processor = PayloadProcessor(transcriber=FakeTranscriber(), min_chars=10)
        uploads = UploadStore(tmp_path / "uploads")
        # 60 sections render to well over the 400-token window, so modes take several passes.
        resolver = SourceResolver([FakeLoader(sections=60), UploadLoader(uploads, processor)])
        registry = ModelRegistry(
            {"fake": ProviderSpec(name="fake", base_url="http://fake", api_key_env="FAKE_KEY")},
            [ModelSpec(provider="fake", model="m1", display_name="Fake 1")],
        )
        router = ModelRouter(registry, BreakerRegistry(), client_double, temperature=0, max_tokens=64)
        ingestion = IngestionService(
            resolver=resolver,
            chunker=Chunker(300, 50),
            embeddings=embeddings,
            vector_store=store,
            sources=sources_repo,
            upload_store=uploads,
        )
        retrieval = RetrievalService(embeddings, store, top_k=3, per_source_k=2)
        documents = DocumentService(router, max_input_tokens=1100, max_windows=8, tokens_per_minute=0)
        collections = CollectionService(collections_repo, sources_repo)
        chat = ChatService(
            sources=sources_repo,
            conversations=conversations_repo,
            collections=collections_repo,
            retrieval=retrieval,
            documents=documents,
            router=router,
        )
        return Container(
            settings=settings,
            http=httpx.AsyncClient(),
            db=db,
            registry=registry,
            router=router,
            embeddings=embeddings,
            vector_store=store,
            resolver=resolver,
            ingestion=ingestion,
            chat=chat,
            collections=collections,
            transcriber=FakeTranscriber(),
        )

    monkeypatch.setattr(Container, "build", staticmethod(build))
    with TestClient(create_app(settings)) as test_client:
        yield test_client, client_double


@pytest.fixture
def client(harness):
    return harness[0]


def wait_ready(client, source_id):
    for _ in range(150):
        body = client.get(f"/api/v1/sources/{source_id}").json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("source never finished ingesting")


def add_source(client, url="https://example.com/page"):
    created = client.post("/api/v1/sources", json={"url": url})
    assert created.status_code == 202
    return wait_ready(client, created.json()["id"])


def stream(client, conversation_id, content="", mode="qa"):
    """Send a message and collect the SSE events by name."""
    import json

    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": content, "mode": mode},
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in "".join(response.iter_text()).splitlines() if line]

    events: dict[str, list] = {}
    for index, line in enumerate(lines):
        if line.startswith("event:"):
            name = line[6:].strip()
            payload = json.loads(lines[index + 1][5:].strip())
            events.setdefault(name, []).append(payload)
    return events


# ---------------------------------------------------------------- basics


def test_health_reports_capabilities(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["active_models"] == 1
    caps = body["capabilities"]
    assert caps["uploads"] is True
    assert caps["audio_transcription"] is True
    assert caps["javascript_rendering"] is False  # renderer disabled by default
    assert ".docx" in caps["upload_extensions"]


def test_modes_endpoint_describes_the_catalogue(client):
    modes = client.get("/api/v1/modes").json()
    by_id = {m["id"]: m for m in modes}
    assert by_id["qa"]["needs_input"] is True and by_id["qa"]["scope"] == "retrieval"
    assert by_id["summary"]["needs_input"] is False and by_id["summary"]["scope"] == "document"
    assert by_id["translate"]["needs_input"] is True


def test_source_lifecycle_and_deduplication(client):
    source = add_source(client, "example.com/page?utm_source=t")
    assert source["chunk_count"] > 1
    assert source["url"] == "https://example.com/page"
    assert source["title"] == "Example page"

    again = client.post("/api/v1/sources", json={"url": "https://example.com/page"}).json()
    assert again["id"] == source["id"]

    unsupported = client.post("/api/v1/sources", json={"url": "https://other.org"})
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "unsupported_source"

    assert client.delete(f"/api/v1/sources/{source['id']}").status_code == 204
    assert client.get(f"/api/v1/sources/{source['id']}").status_code == 404


# ---------------------------------------------------------------- chat


def test_question_answering_cites_and_persists(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()

    events = stream(client, conversation["id"], "What is fact 1?")
    assert len(events["citations"][0]) == 3
    assert events["model"][0] == "fake/m1"
    assert "".join(events["token"]).strip().startswith("The answer is 7")
    assert events["done"][0]["mode"] == "qa"

    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["model"] == "fake/m1"
    assert messages[1]["mode"] == "qa"
    assert len(messages[1]["citations"]) == 3


def test_document_mode_needs_no_question_and_maps_citations(harness):
    client, llm = harness
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()

    events = stream(client, conversation["id"], "", mode="summary")
    assert "progress" in events, "a long document should report multi-pass progress"
    assert events["done"][0]["mode"] == "summary"
    # [1] in the fake answer maps back to the first chunk
    assert len(events["citations"][0]) == 1
    assert events["citations"][0][0]["index"] == 1

    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()
    assert messages[0]["content"] == "Summarise this source."
    assert messages[0]["mode"] == "summary"

    # the condense step ran before the final answer, and the task instruction differs
    systems = [call["system"] for call in llm.calls]
    assert any("Condense this part" in s for s in systems)
    assert any("Write a summary of the source" in s for s in systems)


def test_mode_requiring_input_rejects_empty_content(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    events = stream(client, conversation["id"], "", mode="translate")
    assert "error" in events and "Target language" in events["error"][0]


def test_unknown_mode_is_rejected(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "hi", "mode": "nonsense"},
    )
    assert response.status_code == 422


def test_conversation_needs_exactly_one_target(client):
    source = add_source(client)
    both = client.post("/api/v1/conversations", json={"source_id": source["id"], "collection_id": "col_x"})
    assert both.status_code == 422
    neither = client.post("/api/v1/conversations", json={})
    assert neither.status_code == 422


# ---------------------------------------------------------------- uploads


def test_upload_is_ingested_and_deduplicated_by_content(client):
    payload = (
        b"# Uploaded notes\n\nLinkChat can read uploaded files. "
        b"The project deadline is March 3rd and the owner is the platform team.\n"
    )
    first = client.post("/api/v1/sources/upload", files={"file": ("notes.md", payload, "text/markdown")})
    assert first.status_code == 202
    source = wait_ready(client, first.json()["id"])
    assert source["status"] == "ready"
    assert source["kind"] == "text"
    assert source["url"].startswith("upload://")
    assert source["metadata"]["uploaded"] is True

    same = client.post("/api/v1/sources/upload", files={"file": ("notes.md", payload, "text/markdown")})
    assert same.json()["id"] == source["id"]


def test_upload_rejects_unsupported_and_empty_files(client):
    bad = client.post("/api/v1/sources/upload", files={"file": ("virus.exe", b"MZ\x00", "application/exe")})
    assert bad.status_code == 422
    assert "Unsupported file type" in bad.json()["error"]["message"]

    empty = client.post("/api/v1/sources/upload", files={"file": ("a.txt", b"", "text/plain")})
    assert empty.status_code == 422


def test_uploaded_audio_is_transcribed_with_timestamps(client):
    created = client.post("/api/v1/sources/upload", files={"file": ("talk.mp3", b"ID3fake-audio-bytes", "audio/mpeg")})
    assert created.status_code == 202
    source = wait_ready(client, created.json()["id"])
    assert source["status"] == "ready"
    assert source["kind"] == "audio"
    assert source["metadata"]["transcription_model"] == "fake/whisper"

    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    events = stream(client, conversation["id"], "What is discussed?")
    citation = events["citations"][0][0]
    assert "timestamp" in citation["metadata"]


# ---------------------------------------------------------------- collections


def test_collection_chat_searches_every_member(client):
    first = add_source(client, "https://example.com/one")
    second = add_source(client, "https://example.com/two")

    collection = client.post(
        "/api/v1/collections",
        json={"title": "Research", "source_ids": [first["id"], second["id"]]},
    )
    assert collection.status_code == 201
    body = collection.json()
    assert body["source_count"] == 2 and body["ready_count"] == 2

    conversation = client.post("/api/v1/conversations", json={"collection_id": body["id"]}).json()
    assert conversation["source_id"] is None

    events = stream(client, conversation["id"], "What is fact 3?")
    citations = events["citations"][0]
    assert citations, "a collection search should return excerpts"
    assert all("source_title" in c["metadata"] for c in citations)
    assert {c["metadata"]["source_id"] for c in citations} <= {first["id"], second["id"]}

    listed = client.get(f"/api/v1/collections/{body['id']}/sources").json()
    assert {s["id"] for s in listed} == {first["id"], second["id"]}


def test_collection_membership_edits(client):
    first = add_source(client, "https://example.com/one")
    second = add_source(client, "https://example.com/two")
    collection = client.post("/api/v1/collections", json={"title": "Edits", "source_ids": [first["id"]]}).json()

    added = client.post(f"/api/v1/collections/{collection['id']}/sources/{second['id']}").json()
    assert added["source_count"] == 2
    removed = client.delete(f"/api/v1/collections/{collection['id']}/sources/{first['id']}").json()
    assert removed["source_ids"] == [second["id"]]

    renamed = client.patch(f"/api/v1/collections/{collection['id']}", json={"title": "Renamed"}).json()
    assert renamed["title"] == "Renamed"

    missing = client.post("/api/v1/collections", json={"title": "Bad", "source_ids": ["src_nope"]})
    assert missing.status_code == 404

    assert client.delete(f"/api/v1/collections/{collection['id']}").status_code == 204
    assert client.get(f"/api/v1/collections/{collection['id']}").status_code == 404


# ---------------------------------------------------------------- pool


def test_model_pool_reports_usage(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    stream(client, conversation["id"], "What is fact 1?")

    pool = client.get("/api/v1/models").json()
    assert pool["active_count"] == 1
    assert pool["models"][0]["successes"] >= 1
    assert pool["models"][0]["available_upstream"] is None  # validation disabled in tests


def test_language_rule_targets_the_latest_message(harness):
    """A conversation that switches language must not stay stuck on the old one."""
    client, llm = harness
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    stream(client, conversation["id"], "¿Qué es esto?")
    stream(client, conversation["id"], "What is fact 2?")

    system = llm.calls[-1]["system"]
    assert "LATEST message" in system
    # The nearest instruction is the one obeyed, so it is repeated after the
    # question itself; the system prompt alone is not enough between two
    # Latin-script languages.
    # The language is named outright, because "the same language as the question"
    # loses to earlier turns between two Latin-script languages.
    user = llm.calls[-1]["user"]
    assert "Write your entire answer in English" in user
    assert user.index("What is fact 2?") < user.index("Write your entire answer")

    stream(client, conversation["id"], "RAG के नुकसान क्या हैं?")
    assert "Write your entire answer in Hindi" in llm.calls[-1]["user"]


def test_rate_limiting_rejects_a_flood_and_says_when_to_retry(harness):
    client, _ = harness
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()

    # The chat bucket defaults to 20 a minute; the 21st must be refused.
    statuses = []
    for _ in range(22):
        response = client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": "What is fact 1?", "mode": "qa"},
        )
        statuses.append(response.status_code)

    assert statuses.count(429) >= 1, "a flood must eventually be refused"
    refused = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "again", "mode": "qa"},
    )
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1


def test_health_is_never_rate_limited(harness):
    """Monitoring must keep working while a caller is being throttled."""
    client, _ = harness
    for _ in range(150):
        assert client.get("/api/v1/health").status_code == 200


def test_successful_responses_advertise_the_remaining_quota(harness):
    client, _ = harness
    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    assert int(response.headers["X-RateLimit-Limit"]) > 0
    assert "X-RateLimit-Remaining" in response.headers


def test_ingestion_is_queued_rather_than_started_all_at_once(harness):
    """A burst of submissions must not start every ingestion simultaneously,
    because each in-flight document holds memory while it is embedded."""
    client, _ = harness
    container = client.app.state.container
    assert container.ingestion.max_concurrent >= 1

    # Stay under the ingest rate limit, which is exercised by its own test.
    burst = 8
    accepted = [
        client.post("/api/v1/sources", json={"url": f"https://example.com/page-{i}"})
        for i in range(burst)
    ]
    assert all(r.status_code == 202 for r in accepted), "submissions are accepted immediately"

    for response in accepted:
        wait_ready(client, response.json()["id"])
    statuses = [client.get(f"/api/v1/sources/{r.json()['id']}").json()["status"] for r in accepted]
    assert statuses == ["ready"] * burst, "queueing must not drop any of them"
