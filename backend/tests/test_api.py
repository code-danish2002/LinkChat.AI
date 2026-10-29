"""End-to-end API tests with fake embeddings, an in-memory database and a fake model pool."""

from __future__ import annotations

import json
import time
from datetime import timedelta

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
from app.providers.email.senders import ConsoleEmailSender
from app.providers.llm.health import BreakerRegistry
from app.providers.llm.registry import ModelRegistry, ModelSpec, ProviderSpec
from app.providers.llm.router import ModelRouter
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.maintenance import MaintenanceService
from app.services.retrieval_service import RetrievalService
from tests.fakes import (
    EchoLLMClient,
    FakeLoader,
    FakeTranscriber,
    HashEmbeddings,
    MemoryCollectionRepository,
    MemoryConversationRepository,
    MemoryDocumentRepository,
    MemoryIdentityRepository,
    MemorySourceRepository,
    MemoryStore,
    MemoryVectorStore,
)

GUEST_A = "gst_test_guest_aaaa"
GUEST_B = "gst_test_guest_bbbb"


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
    store = MemoryStore()
    loader = FakeLoader(sections=60)
    email = ConsoleEmailSender()

    def build(_settings):
        embeddings = HashEmbeddings()
        documents_repo = MemoryDocumentRepository(store)
        sources_repo = MemorySourceRepository(store)
        conversations_repo = MemoryConversationRepository(store)
        collections_repo = MemoryCollectionRepository(store)
        identity_repo = MemoryIdentityRepository(store)
        vector_store = MemoryVectorStore(store, embedding_model=embeddings.model_name)
        processor = PayloadProcessor(transcriber=FakeTranscriber(), min_chars=10)
        uploads = UploadStore(tmp_path / "uploads")
        # 60 sections render to well over the 400-token window, so modes take several passes.
        resolver = SourceResolver([loader, UploadLoader(uploads, processor)])
        registry = ModelRegistry(
            {"fake": ProviderSpec(name="fake", base_url="http://fake", api_key_env="FAKE_KEY")},
            [ModelSpec(provider="fake", model="m1", display_name="Fake 1")],
        )
        router = ModelRouter(registry, BreakerRegistry(), client_double, temperature=0, max_tokens=64)
        ingestion = IngestionService(
            resolver=resolver,
            chunker=Chunker(300, 50),
            embeddings=embeddings,
            vector_store=vector_store,
            documents=documents_repo,
            sources=sources_repo,
            upload_store=uploads,
        )
        retrieval = RetrievalService(embeddings, vector_store, top_k=3, per_source_k=2)
        document_service = DocumentService(router, max_input_tokens=1100, max_windows=8, tokens_per_minute=0)
        collections = CollectionService(collections_repo, sources_repo)
        chat = ChatService(
            sources=sources_repo,
            conversations=conversations_repo,
            collections=collections_repo,
            retrieval=retrieval,
            documents=document_service,
            router=router,
            transcriber=FakeTranscriber(),
            max_voice_bytes=1024,
        )
        auth = AuthService(
            identity_repo, email, secret="test-secret", guest_ttl=timedelta(hours=24), reveal_codes=True
        )
        return Container(
            settings=settings,
            http=httpx.AsyncClient(),
            db=None,
            registry=registry,
            router=router,
            embeddings=embeddings,
            vector_store=vector_store,
            resolver=resolver,
            ingestion=ingestion,
            chat=chat,
            collections=collections,
            auth=auth,
            maintenance=MaintenanceService(identity_repo, guest_ttl=timedelta(hours=24), interval=timedelta(hours=1)),
            transcriber=FakeTranscriber(),
        )

    monkeypatch.setattr(Container, "build", staticmethod(build))
    with TestClient(create_app(settings)) as test_client:
        test_client.headers["X-Guest-Id"] = GUEST_A
        test_client.store = store  # type: ignore[attr-defined]
        test_client.loader = loader  # type: ignore[attr-defined]
        test_client.email = email  # type: ignore[attr-defined]
        yield test_client, client_double


@pytest.fixture
def client(harness):
    return harness[0]


def as_guest(guest_id: str) -> dict[str, str]:
    return {"X-Guest-Id": guest_id}


def wait_ready(client, source_id, headers=None):
    for _ in range(150):
        body = client.get(f"/api/v1/sources/{source_id}", headers=headers).json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("source never finished ingesting")


def add_source(client, url="https://example.com/page", headers=None):
    created = client.post("/api/v1/sources", json={"url": url}, headers=headers)
    assert created.status_code == 202, created.text
    return wait_ready(client, created.json()["id"], headers)


def stream(client, conversation_id, content="", mode="qa", headers=None, spoken=False):
    """Send a message and collect the SSE events by name."""
    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": content, "mode": mode, "spoken": spoken},
        headers=headers,
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in "".join(response.iter_text()).splitlines() if line]
    return parse_sse(lines)


def speak(client, conversation_id, audio=b"RIFF-fake-wav-bytes", mode="qa", filename="question.wav"):
    """Send a spoken question and collect the SSE events by name."""
    with client.stream(
        "POST",
        f"/api/v1/conversations/{conversation_id}/voice",
        files={"audio": (filename, audio, "audio/wav")},
        data={"mode": mode},
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream"), response.read()
        lines = [line for line in "".join(response.iter_text()).splitlines() if line]
    return parse_sse(lines)


def parse_sse(lines):
    events: dict[str, list] = {}
    for index, line in enumerate(lines):
        if line.startswith("event:"):
            name = line[6:].strip()
            payload = json.loads(lines[index + 1][5:].strip())
            events.setdefault(name, []).append(payload)
    return events


def sign_in(client, email="ada@example.com", name="Ada", guest=GUEST_A) -> dict:
    """Request a code as the guest, verify it, return the session body."""
    requested = client.post(
        "/api/v1/auth/request-code", json={"email": email, "display_name": name}, headers=as_guest(guest)
    )
    assert requested.status_code == 200, requested.text
    code = requested.json()["debug_code"]
    assert code and len(code) == 6
    verified = client.post("/api/v1/auth/verify", json={"email": email, "code": code}, headers=as_guest(guest))
    assert verified.status_code == 200, verified.text
    return verified.json()


def as_user(session: dict) -> dict[str, str]:
    # The guest header stays on the client; a bearer token takes precedence.
    return {"Authorization": f"Bearer {session['token']}"}


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
    assert caps["email_login"] == "console"
    assert caps["guest_ttl_hours"] == 24


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


# ---------------------------------------------------------------- identity


def test_requests_without_identity_are_refused(client):
    client.headers.pop("X-Guest-Id")
    response = client.get("/api/v1/sources")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_required"
    # Public endpoints keep working for anyone.
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/modes").status_code == 200


def test_malformed_guest_ids_are_refused(client):
    response = client.get("/api/v1/sources", headers=as_guest("not-a-guest-id"))
    assert response.status_code == 401


def test_guests_only_see_their_own_sources(client):
    mine = add_source(client, "https://example.com/mine")
    assert [s["id"] for s in client.get("/api/v1/sources").json()] == [mine["id"]]

    assert client.get("/api/v1/sources", headers=as_guest(GUEST_B)).json() == []
    assert client.get(f"/api/v1/sources/{mine['id']}", headers=as_guest(GUEST_B)).status_code == 404
    assert client.delete(f"/api/v1/sources/{mine['id']}", headers=as_guest(GUEST_B)).status_code == 404
    # The other guest's attempt changed nothing.
    assert client.get(f"/api/v1/sources/{mine['id']}").status_code == 200

    conversation = client.post("/api/v1/conversations", json={"source_id": mine["id"]}).json()
    assert client.get(f"/api/v1/conversations/{conversation['id']}", headers=as_guest(GUEST_B)).status_code == 404


def test_same_url_is_ingested_once_and_shared(client):
    first = add_source(client, "https://example.com/shared")
    second = add_source(client, "https://example.com/shared", headers=as_guest(GUEST_B))

    assert first["id"] != second["id"], "each owner gets their own handle"
    assert first["document_id"] == second["document_id"], "both point at one document"
    assert client.loader.loads == 1, "the content was fetched and embedded once"
    assert len(client.store.documents) == 1

    # Deleting one owner's source keeps the document for the other.
    assert client.delete(f"/api/v1/sources/{first['id']}").status_code == 204
    assert len(client.store.documents) == 1
    still = client.get(f"/api/v1/sources/{second['id']}", headers=as_guest(GUEST_B)).json()
    assert still["status"] == "ready"

    # Deleting the last one drops the document and its chunks.
    assert client.delete(f"/api/v1/sources/{second['id']}", headers=as_guest(GUEST_B)).status_code == 204
    assert client.store.documents == {} and client.store.chunks == {}


def test_email_code_login_moves_guest_data_into_the_account(client):
    guest_source = add_source(client, "https://example.com/keep-me")
    conversation = client.post("/api/v1/conversations", json={"source_id": guest_source["id"]}).json()
    stream(client, conversation["id"], "What is fact 1?")

    me = client.get("/api/v1/auth/me").json()
    assert me["kind"] == "guest" and me["owner_id"] == GUEST_A

    session = sign_in(client)
    assert session["user"]["email"] == "ada@example.com"
    assert session["user"]["display_name"] == "Ada"
    assert client.email.sent[0]["to"] == "ada@example.com"

    me = client.get("/api/v1/auth/me", headers=as_user(session)).json()
    assert me["kind"] == "user" and me["user"]["display_name"] == "Ada"

    # Everything the guest made now belongs to the account...
    mine = client.get("/api/v1/sources", headers=as_user(session)).json()
    assert [s["id"] for s in mine] == [guest_source["id"]]
    history = client.get(f"/api/v1/conversations/{conversation['id']}/messages", headers=as_user(session)).json()
    assert len(history) == 2
    # ...and the old guest id holds nothing anymore.
    assert client.get("/api/v1/sources").json() == []

    # Signing in again on another device needs no name and reuses the account.
    requested = client.post("/api/v1/auth/request-code", json={"email": "ADA@example.com"}, headers=as_guest(GUEST_B))
    assert requested.status_code == 200
    verified = client.post(
        "/api/v1/auth/verify",
        json={"email": "ada@example.com", "code": requested.json()["debug_code"]},
        headers=as_guest(GUEST_B),
    ).json()
    assert verified["user"]["id"] == session["user"]["id"]
    assert [s["id"] for s in client.get("/api/v1/sources", headers=as_user(verified)).json()] == [guest_source["id"]]


def test_login_merges_duplicate_sources_instead_of_keeping_two(client):
    session = sign_in(client, guest=GUEST_B)
    as_account = as_user(session)
    kept = add_source(client, "https://example.com/dup", headers=as_account)

    # A guest adds the same URL, chats about it, then signs in to the same account.
    guest_copy = add_source(client, "https://example.com/dup")
    assert guest_copy["document_id"] == kept["document_id"]
    conversation = client.post("/api/v1/conversations", json={"source_id": guest_copy["id"]}).json()

    requested = client.post("/api/v1/auth/request-code", json={"email": "ada@example.com"}).json()
    merged = client.post(
        "/api/v1/auth/verify", json={"email": "ada@example.com", "code": requested["debug_code"]}
    ).json()
    sources = client.get("/api/v1/sources", headers=as_user(merged)).json()
    assert [s["id"] for s in sources] == [kept["id"]], "one handle per document per owner"
    moved = client.get(f"/api/v1/conversations/{conversation['id']}", headers=as_user(merged)).json()
    assert moved["source_id"] == kept["id"], "the guest's chat now hangs off the account's copy"


def test_new_accounts_need_a_name_and_wrong_codes_are_rejected(client):
    nameless = client.post("/api/v1/auth/request-code", json={"email": "new@example.com"})
    assert nameless.status_code == 422
    assert nameless.json()["error"]["details"]["needs_name"] is True

    client.post("/api/v1/auth/request-code", json={"email": "new@example.com", "display_name": "New"})
    wrong = client.post("/api/v1/auth/verify", json={"email": "new@example.com", "code": "000000"})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["details"]["attempts_left"] == 4

    bad_token = client.get("/api/v1/sources", headers={"Authorization": "Bearer nope"})
    assert bad_token.status_code == 401
    assert bad_token.json()["error"]["code"] == "session_expired"


def test_logout_and_account_deletion(client):
    session = sign_in(client)
    add_source(client, "https://example.com/account-only", headers=as_user(session))

    assert client.post("/api/v1/auth/logout", headers=as_user(session)).status_code == 204
    assert client.get("/api/v1/sources", headers=as_user(session)).status_code == 401

    again = sign_in(client, guest=GUEST_B)
    assert client.delete("/api/v1/auth/account", headers=as_user(again)).status_code == 204
    assert client.store.users == {} and client.store.sources == {} and client.store.documents == {}


def test_forget_guest_deletes_everything_now(client):
    add_source(client, "https://example.com/temporary")
    assert client.delete("/api/v1/auth/guest").status_code == 204
    assert client.get("/api/v1/sources").json() == []
    assert client.store.documents == {}


def test_expired_guests_are_purged_by_maintenance(client):
    add_source(client, "https://example.com/old")
    shared = add_source(client, "https://example.com/both")
    add_source(client, "https://example.com/both", headers=as_guest(GUEST_B))

    client.store.guests[GUEST_A] = client.store.guests[GUEST_A] - timedelta(hours=25)
    import asyncio

    removed = asyncio.run(client.app.state.container.maintenance.run_once())
    assert removed["guests"] == 1
    assert client.get("/api/v1/sources").json() == [], "the expired guest's sources are gone"
    # The document the other guest also holds survives; the one only the expired guest had does not.
    assert set(client.store.documents) == {shared["document_id"]}


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

    # Another owner uploading the same bytes under another name shares the document.
    other = client.post(
        "/api/v1/sources/upload", files={"file": ("renamed.md", payload, "text/markdown")}, headers=as_guest(GUEST_B)
    ).json()
    assert other["id"] != source["id"] and other["document_id"] == source["document_id"]


def test_upload_rejects_unsupported_and_empty_files(client):
    bad = client.post("/api/v1/sources/upload", files={"file": ("virus.exe", b"MZ\x00", "application/exe")})
    assert bad.status_code == 422
    assert "Unsupported file type" in bad.json()["error"]["message"]

    empty = client.post("/api/v1/sources/upload", files={"file": ("a.txt", b"", "text/plain")})
    assert empty.status_code == 422


def test_uploaded_audio_is_transcribed_with_timestamps(client):
    created = client.post(
        "/api/v1/sources/upload", files={"file": ("talk.mp3", b"ID3fake-audio-bytes", "audio/mpeg")}
    )
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

    # Another owner cannot see it, nor build a collection out of my sources.
    assert client.get(f"/api/v1/collections/{body['id']}", headers=as_guest(GUEST_B)).status_code == 404
    stolen = client.post(
        "/api/v1/collections", json={"title": "Theft", "source_ids": [first["id"]]}, headers=as_guest(GUEST_B)
    )
    assert stolen.status_code == 404


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


def test_login_code_requests_are_rate_limited(client):
    statuses = [
        client.post("/api/v1/auth/request-code", json={"email": "a@example.com", "display_name": "A"}).status_code
        for _ in range(7)
    ]
    assert statuses[:5] == [200] * 5 and 429 in statuses[5:]


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
    accepted = [client.post("/api/v1/sources", json={"url": f"https://example.com/page-{i}"}) for i in range(burst)]
    assert all(r.status_code == 202 for r in accepted), "submissions are accepted immediately"

    for response in accepted:
        wait_ready(client, response.json()["id"])
    statuses = [client.get(f"/api/v1/sources/{r.json()['id']}").json()["status"] for r in accepted]
    assert statuses == ["ready"] * burst, "queueing must not drop any of them"


# ---------------------------------------------------------------- voice


def test_health_advertises_voice(client):
    assert client.get("/api/v1/health").json()["capabilities"]["voice"] is True


def test_spoken_question_is_transcribed_then_answered_for_the_ear(harness):
    client, llm = harness
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()

    events = speak(client, conversation["id"])
    heard = events["transcript"][0]
    assert heard["text"].startswith("spoken line 0 about the subject spoken line 1")
    assert heard["language"] == "en"
    assert events["citations"] and events["token"], "the transcript was answered like a typed question"
    assert events["done"][0]["language"] == "English"

    # the model was told the answer will be read aloud
    assert "read aloud" in llm.calls[-1]["system"]
    assert "no bullet points" in llm.calls[-1]["system"]

    # the spoken words were persisted as the user's turn
    messages = client.get(f"/api/v1/conversations/{conversation['id']}/messages").json()
    assert messages[0]["role"] == "user" and messages[0]["content"] == heard["text"]
    assert messages[1]["role"] == "assistant"


def test_typed_question_can_ask_for_a_spoken_answer(harness):
    client, llm = harness
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    stream(client, conversation["id"], "What is fact 1?", spoken=True)
    assert "read aloud" in llm.calls[-1]["system"]
    stream(client, conversation["id"], "What is fact 2?")
    assert "read aloud" not in llm.calls[-1]["system"]


def test_voice_rejects_empty_and_oversized_recordings(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    assert "error" in speak(client, conversation["id"], audio=b"")
    too_big = speak(client, conversation["id"], audio=b"x" * 2048)
    assert "capped" in too_big["error"][0]
    assert "transcript" not in too_big


def test_voice_is_refused_for_someone_elses_conversation(client):
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/voice",
        files={"audio": ("q.wav", b"RIFF", "audio/wav")},
        headers=as_guest(GUEST_B),
    )
    assert response.status_code == 404


def test_voice_needs_a_transcriber(client):
    client.app.state.container.chat._transcriber = FakeTranscriber(available=False)
    assert client.get("/api/v1/health").json()["capabilities"]["voice"] is False
    source = add_source(client)
    conversation = client.post("/api/v1/conversations", json={"source_id": source["id"]}).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/voice", files={"audio": ("q.wav", b"RIFF", "audio/wav")}
    )
    assert response.status_code == 422 and "GROQ_API_KEY" in response.json()["error"]["message"]
