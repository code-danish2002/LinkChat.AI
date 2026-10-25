"""The real Postgres adapters against a real database.

Skipped unless TEST_DATABASE_URL is set, because the rest of the suite must
run offline. Point it at a scratch Neon branch, never at production:

    TEST_DATABASE_URL=postgresql://... .venv/bin/python -m pytest tests/test_postgres.py

Every row this test writes carries a unique run id and is removed at the end.
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from app.core.exceptions import StaleIndexError
from app.domain.models import (
    Chunk,
    Collection,
    Conversation,
    Document,
    Message,
    MessageRole,
    Session,
    Source,
    SourceKind,
    SourceStatus,
    User,
    utcnow,
)
from app.storage.postgres.database import Database
from app.storage.postgres.repositories import (
    PostgresCollectionRepository,
    PostgresConversationRepository,
    PostgresDocumentRepository,
    PostgresIdentityRepository,
    PostgresSourceRepository,
)
from app.storage.postgres.vector_store import PostgresVectorStore

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL not set")
SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.postgres.sql"


@pytest.fixture
async def db():
    database = Database(DATABASE_URL, min_size=1, max_size=2)
    await database.open()
    await database.migrate(SCHEMA)
    yield database
    await database.close()


@pytest.fixture
def run():
    """A unique suffix so parallel or aborted runs never collide."""
    return secrets.token_hex(4)


async def cleanup(db: Database, run: str) -> None:
    async with db.connection() as conn:
        for table in ("sources", "collections", "conversations"):
            await conn.execute(f"DELETE FROM {table} WHERE owner_id LIKE %s", (f"%{run}",))  # noqa: S608
        await conn.execute("DELETE FROM documents WHERE content_key LIKE %s", (f"%{run}%",))
        await conn.execute("DELETE FROM guests WHERE id LIKE %s", (f"%{run}",))
        await conn.execute("DELETE FROM users WHERE email LIKE %s", (f"%{run}@example.com",))


async def test_shared_documents_hybrid_search_and_orphan_cleanup(db, run):
    documents = PostgresDocumentRepository(db)
    sources = PostgresSourceRepository(db)
    store = PostgresVectorStore(db, embedding_model="test-model", vector_candidates=3, keyword_candidates=3)
    guest_a, guest_b = f"gst_a_{run}", f"gst_b_{run}"
    try:
        document = await documents.add(
            Document(
                id=f"doc_{run}",
                content_key=f"https://example.com/{run}",
                url=f"https://example.com/{run}",
                kind=SourceKind.WEB,
                title="Shared page",
                status=SourceStatus.READY,
            )
        )
        chunks = [
            Chunk(id=f"chk_{run}_1", document_id=document.id, index=0, text="The Panic Monster wakes near deadlines."),
            Chunk(id=f"chk_{run}_2", document_id=document.id, index=1, text="Instant gratification monkey steers."),
            Chunk(id=f"chk_{run}_3", document_id=document.id, index=2, text="Product SKU X-9000 costs 49 dollars."),
        ]
        await store.upsert(document.id, chunks, np.eye(3, dtype=np.float32))
        assert await store.exists(document.id)
        refreshed = await documents.get(document.id)
        assert refreshed.embedding_model == "test-model" and refreshed.chunk_count == 3

        # keyword hit dominates even when the vector points elsewhere
        results = await store.search(document.id, np.array([0, 1, 0], dtype=np.float32), "SKU X-9000 price", top_k=2)
        ids = [r.chunk.id for r in results]
        assert f"chk_{run}_3" in ids and f"chk_{run}_2" in ids
        assert any(r.keyword_score for r in results)
        assert [c.index for c in await store.all_chunks(document.id)] == [0, 1, 2]

        # two owners, one document
        a = await sources.add(Source.for_document(f"src_a_{run}", guest_a, document))
        b = await sources.add(Source.for_document(f"src_b_{run}", guest_b, document))
        assert (await sources.get(guest_a, a.id)).title == "Shared page"
        assert await sources.get(guest_b, a.id) is None, "owner scoping"
        assert (await sources.get_by_document(guest_b, document.id)).id == b.id
        assert [s.id for s in await sources.list(guest_a)] == [a.id]

        # a different embedding model must refuse to serve the index
        other = PostgresVectorStore(db, embedding_model="other-model")
        assert not await other.exists(document.id)
        with pytest.raises(StaleIndexError):
            await other.search(document.id, np.array([1, 0, 0], dtype=np.float32), "monster", top_k=1)

        # the document outlives one owner and dies with the last
        await sources.delete(guest_a, a.id)
        assert await documents.get(document.id) is not None
        await sources.delete(guest_b, b.id)
        assert await documents.get(document.id) is None
        assert await store.all_chunks(document.id) == []
    finally:
        await cleanup(db, run)


async def test_collections_conversations_and_guest_transfer(db, run):
    documents = PostgresDocumentRepository(db)
    sources = PostgresSourceRepository(db)
    collections = PostgresCollectionRepository(db)
    conversations = PostgresConversationRepository(db)
    identity = PostgresIdentityRepository(db)
    guest, user_id = f"gst_{run}", f"usr_{run}"
    try:
        await identity.touch_guest(guest)
        user = await identity.add_user(User(id=user_id, email=f"{run}@example.com", display_name="Test"))

        shared = await documents.add(
            Document(id=f"doc_s_{run}", content_key=f"k-s-{run}", url="https://x/s", kind=SourceKind.WEB, title="S")
        )
        only_guest = await documents.add(
            Document(id=f"doc_g_{run}", content_key=f"k-guest-{run}", url="https://x/g", kind=SourceKind.WEB, title="G")
        )
        user_copy = await sources.add(Source.for_document(f"src_u_{run}", user.id, shared))
        guest_copy = await sources.add(Source.for_document(f"src_gs_{run}", guest, shared))
        guest_only = await sources.add(Source.for_document(f"src_gg_{run}", guest, only_guest))

        collection = await collections.add(
            Collection(id=f"col_{run}", owner_id=guest, title="Mine", source_ids=[guest_copy.id, guest_only.id])
        )
        assert (await collections.get(guest, collection.id)).source_ids == [guest_copy.id, guest_only.id]
        assert await collections.get(user.id, collection.id) is None

        conversation = await conversations.add(
            Conversation(id=f"cnv_{run}", owner_id=guest, title="chat", source_id=guest_copy.id)
        )
        await conversations.add_message(
            Message(id=f"msg_{run}_1", conversation_id=conversation.id, role=MessageRole.USER, content="hi")
        )
        await conversations.add_message(
            Message(id=f"msg_{run}_2", conversation_id=conversation.id, role=MessageRole.ASSISTANT, content="hello")
        )
        assert [m.content for m in await conversations.list_messages(conversation.id, limit=1)] == ["hello"]

        # sessions
        session = await identity.add_session(
            Session(id=f"ses_{run}", user_id=user.id, token_hash=f"hash_{run}", expires_at=utcnow() + timedelta(days=1))
        )
        found = await identity.get_session(f"hash_{run}")
        assert found is not None and found[0].id == session.id and found[1].email == user.email

        # the guest signs in: duplicates collapse, the rest moves
        await identity.transfer_ownership(guest, user.id)
        mine = await sources.list(user.id)
        assert {s.document_id for s in mine} == {shared.id, only_guest.id}
        assert len(mine) == 2, "one handle per document"
        moved = await conversations.get(user.id, conversation.id)
        assert moved is not None and moved.source_id == user_copy.id
        merged = await collections.get(user.id, collection.id)
        assert set(merged.source_ids) == {user_copy.id, guest_only.id}
        assert await sources.list(guest) == []

        # deleting the account removes everything, including shared documents nobody else holds
        await identity.delete_user(user.id)
        assert await identity.get_user(user.id) is None
        assert await identity.get_session(f"hash_{run}") is None
        assert await documents.get(shared.id) is None and await documents.get(only_guest.id) is None
    finally:
        await cleanup(db, run)
