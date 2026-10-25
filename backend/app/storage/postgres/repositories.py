"""Postgres repositories: documents, sources, collections, conversations, identity.

Every owner-scoped query carries `owner_id` in its WHERE clause, so a row
is unreachable to anyone but its owner even when its id is known. Documents
and chunks are shared and queried by id alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.domain.models import (
    ChatMode,
    Citation,
    Collection,
    Conversation,
    Document,
    LoginCode,
    Message,
    MessageRole,
    Session,
    Source,
    SourceKind,
    SourceStatus,
    User,
    utcnow,
)
from app.domain.ports import (
    CollectionRepository,
    ConversationRepository,
    DocumentRepository,
    IdentityRepository,
    SourceRepository,
)
from app.storage.postgres.database import Database

# ---------------------------------------------------------------- documents


class PostgresDocumentRepository(DocumentRepository):
    def __init__(self, db: Database):
        self._db = db

    async def add(self, document: Document) -> Document:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO documents (id, content_key, url, kind, title, status, error, chunk_count,
                                       embedding_model, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document.id,
                    document.content_key,
                    document.url,
                    document.kind.value,
                    document.title,
                    document.status.value,
                    document.error,
                    document.chunk_count,
                    document.embedding_model,
                    Jsonb(document.metadata),
                    document.created_at,
                    document.updated_at,
                ),
            )
        return document

    async def update(self, document: Document) -> Document:
        document.updated_at = utcnow()
        async with self._db.connection() as conn:
            await conn.execute(
                """
                UPDATE documents
                   SET url = %s, kind = %s, title = %s, status = %s, error = %s, chunk_count = %s,
                       embedding_model = %s, metadata = %s, updated_at = %s
                 WHERE id = %s
                """,
                (
                    document.url,
                    document.kind.value,
                    document.title,
                    document.status.value,
                    document.error,
                    document.chunk_count,
                    document.embedding_model,
                    Jsonb(document.metadata),
                    document.updated_at,
                    document.id,
                ),
            )
        return document

    async def get(self, document_id: str) -> Document | None:
        async with self._db.connection() as conn:
            row = await (await conn.execute("SELECT * FROM documents WHERE id = %s", (document_id,))).fetchone()
        return _to_document(row) if row else None

    async def get_by_key(self, content_key: str) -> Document | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM documents WHERE content_key = %s", (content_key,))
            ).fetchone()
        return _to_document(row) if row else None


def _to_document(row: dict[str, Any]) -> Document:
    return Document(
        id=row["id"],
        content_key=row["content_key"],
        url=row["url"] or "",
        kind=SourceKind(row["kind"]),
        title=row["title"],
        status=SourceStatus(row["status"]),
        error=row["error"],
        chunk_count=row["chunk_count"],
        embedding_model=row["embedding_model"],
        metadata=row["metadata"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------- sources

_SOURCE_VIEW = """
    SELECT s.id, s.owner_id, s.document_id, s.created_at, s.updated_at,
           COALESCE(s.title, d.title) AS title,
           d.url, d.kind, d.status, d.error, d.chunk_count, d.metadata
      FROM sources s
      JOIN documents d ON d.id = s.document_id
"""


class PostgresSourceRepository(SourceRepository):
    def __init__(self, db: Database):
        self._db = db

    async def add(self, source: Source) -> Source:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO sources (id, owner_id, document_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (source.id, source.owner_id, source.document_id, None, source.created_at, source.updated_at),
            )
        return source

    async def get(self, owner_id: str, source_id: str) -> Source | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(_SOURCE_VIEW + " WHERE s.owner_id = %s AND s.id = %s", (owner_id, source_id))
            ).fetchone()
        return _to_source(row) if row else None

    async def get_many(self, owner_id: str, source_ids: list[str]) -> list[Source]:
        if not source_ids:
            return []
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    _SOURCE_VIEW + " WHERE s.owner_id = %s AND s.id = ANY(%s)", (owner_id, source_ids)
                )
            ).fetchall()
        by_id = {row["id"]: _to_source(row) for row in rows}
        return [by_id[sid] for sid in source_ids if sid in by_id]

    async def get_by_document(self, owner_id: str, document_id: str) -> Source | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    _SOURCE_VIEW + " WHERE s.owner_id = %s AND s.document_id = %s", (owner_id, document_id)
                )
            ).fetchone()
        return _to_source(row) if row else None

    async def list(self, owner_id: str, limit: int = 50) -> list[Source]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    _SOURCE_VIEW + " WHERE s.owner_id = %s ORDER BY s.created_at DESC LIMIT %s", (owner_id, limit)
                )
            ).fetchall()
        return [_to_source(row) for row in rows]

    async def delete(self, owner_id: str, source_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute("DELETE FROM sources WHERE owner_id = %s AND id = %s", (owner_id, source_id))


def _to_source(row: dict[str, Any]) -> Source:
    return Source(
        id=row["id"],
        owner_id=row["owner_id"],
        document_id=row["document_id"],
        url=row["url"] or "",
        kind=SourceKind(row["kind"]),
        title=row["title"],
        status=SourceStatus(row["status"]),
        error=row["error"],
        chunk_count=row["chunk_count"],
        metadata=row["metadata"] or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------- collections


class PostgresCollectionRepository(CollectionRepository):
    def __init__(self, db: Database):
        self._db = db

    async def add(self, collection: Collection) -> Collection:
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO collections (id, owner_id, title, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                (
                    collection.id,
                    collection.owner_id,
                    collection.title,
                    collection.created_at,
                    collection.updated_at,
                ),
            )
            await self._write_members(conn, collection)
        return collection

    async def update(self, collection: Collection) -> Collection:
        collection.updated_at = utcnow()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE collections SET title = %s, updated_at = %s WHERE id = %s AND owner_id = %s",
                (collection.title, collection.updated_at, collection.id, collection.owner_id),
            )
            await conn.execute("DELETE FROM collection_sources WHERE collection_id = %s", (collection.id,))
            await self._write_members(conn, collection)
        return collection

    async def get(self, owner_id: str, collection_id: str) -> Collection | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM collections WHERE owner_id = %s AND id = %s", (owner_id, collection_id)
                )
            ).fetchone()
            if row is None:
                return None
            members = await self._member_ids(conn, collection_id)
        return _to_collection(row, members)

    async def list(self, owner_id: str, limit: int = 50) -> list[Collection]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM collections WHERE owner_id = %s ORDER BY updated_at DESC LIMIT %s",
                    (owner_id, limit),
                )
            ).fetchall()
            if not rows:
                return []
            member_rows = await (
                await conn.execute(
                    """
                    SELECT cs.collection_id, cs.source_id
                      FROM collection_sources cs
                      JOIN sources s ON s.id = cs.source_id
                     WHERE cs.collection_id = ANY(%s)
                     ORDER BY cs.collection_id, cs.position
                    """,
                    ([row["id"] for row in rows],),
                )
            ).fetchall()
        members: dict[str, list[str]] = {}
        for member in member_rows:
            members.setdefault(member["collection_id"], []).append(member["source_id"])
        return [_to_collection(row, members.get(row["id"], [])) for row in rows]

    async def delete(self, owner_id: str, collection_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute("DELETE FROM collections WHERE owner_id = %s AND id = %s", (owner_id, collection_id))

    # ---- internals ----

    @staticmethod
    async def _write_members(conn, collection: Collection) -> None:
        if not collection.source_ids:
            return
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO collection_sources (collection_id, source_id, position) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(collection.id, sid, i) for i, sid in enumerate(collection.source_ids)],
            )

    @staticmethod
    async def _member_ids(conn, collection_id: str) -> list[str]:
        rows = await (
            await conn.execute(
                """
                SELECT cs.source_id FROM collection_sources cs
                  JOIN sources s ON s.id = cs.source_id
                 WHERE cs.collection_id = %s ORDER BY cs.position
                """,
                (collection_id,),
            )
        ).fetchall()
        return [row["source_id"] for row in rows]


def _to_collection(row: dict[str, Any], source_ids: list[str]) -> Collection:
    return Collection(
        id=row["id"],
        owner_id=row["owner_id"],
        title=row["title"],
        source_ids=source_ids,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------- conversations


class PostgresConversationRepository(ConversationRepository):
    def __init__(self, db: Database):
        self._db = db

    async def add(self, conversation: Conversation) -> Conversation:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO conversations (id, owner_id, source_id, collection_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    conversation.id,
                    conversation.owner_id,
                    conversation.source_id,
                    conversation.collection_id,
                    conversation.title,
                    conversation.created_at,
                    conversation.updated_at,
                ),
            )
        return conversation

    async def get(self, owner_id: str, conversation_id: str) -> Conversation | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM conversations WHERE owner_id = %s AND id = %s", (owner_id, conversation_id)
                )
            ).fetchone()
        return _to_conversation(row) if row else None

    async def list_for_source(self, owner_id: str, source_id: str) -> list[Conversation]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM conversations WHERE owner_id = %s AND source_id = %s ORDER BY created_at DESC",
                    (owner_id, source_id),
                )
            ).fetchall()
        return [_to_conversation(row) for row in rows]

    async def list_for_collection(self, owner_id: str, collection_id: str) -> list[Conversation]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM conversations WHERE owner_id = %s AND collection_id = %s "
                    "ORDER BY created_at DESC",
                    (owner_id, collection_id),
                )
            ).fetchall()
        return [_to_conversation(row) for row in rows]

    async def delete(self, owner_id: str, conversation_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE owner_id = %s AND id = %s", (owner_id, conversation_id)
            )

    async def add_message(self, message: Message) -> Message:
        citations = [
            {
                "index": c.index,
                "chunk_id": c.chunk_id,
                "text": c.text,
                "score": c.score,
                "metadata": c.metadata,
            }
            for c in message.citations
        ]
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, model, mode, citations, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message.id,
                    message.conversation_id,
                    message.role.value,
                    message.content,
                    message.model,
                    message.mode.value,
                    Jsonb(citations),
                    message.created_at,
                ),
            )
            await conn.execute(
                "UPDATE conversations SET updated_at = %s WHERE id = %s",
                (message.created_at, message.conversation_id),
            )
        return message

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        if limit is None:
            query = "SELECT * FROM messages WHERE conversation_id = %s ORDER BY created_at, id"
            params: tuple = (conversation_id,)
        else:  # newest N, returned in chronological order
            query = (
                "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s) newest ORDER BY created_at, id"
            )
            params = (conversation_id, limit)
        async with self._db.connection() as conn:
            rows = await (await conn.execute(query, params)).fetchall()
        return [_to_message(row) for row in rows]


def _to_conversation(row: dict[str, Any]) -> Conversation:
    return Conversation(
        id=row["id"],
        owner_id=row["owner_id"],
        title=row["title"],
        source_id=row["source_id"],
        collection_id=row["collection_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_message(row: dict[str, Any]) -> Message:
    citations = [
        Citation(
            index=c["index"],
            chunk_id=c["chunk_id"],
            text=c["text"],
            score=c["score"],
            metadata=c.get("metadata") or {},
        )
        for c in (row["citations"] or [])
    ]
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=MessageRole(row["role"]),
        content=row["content"],
        model=row["model"],
        mode=ChatMode(row["mode"] or "qa"),
        citations=citations,
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------- identity

_OWNED_TABLES = ("sources", "collections", "conversations")


class PostgresIdentityRepository(IdentityRepository):
    def __init__(self, db: Database):
        self._db = db

    # ---- guests ----

    async def touch_guest(self, guest_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO guests (id, created_at, last_seen_at) VALUES (%s, now(), now())
                ON CONFLICT (id) DO UPDATE SET last_seen_at = now()
                """,
                (guest_id,),
            )

    async def delete_owner(self, owner_id: str) -> None:
        async with self._db.transaction() as conn:
            await self._delete_owned(conn, [owner_id])
            await conn.execute("DELETE FROM guests WHERE id = %s", (owner_id,))

    async def purge_expired_guests(self, before: datetime) -> int:
        async with self._db.transaction() as conn:
            rows = await (
                await conn.execute("SELECT id FROM guests WHERE last_seen_at < %s", (before,))
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return 0
            await self._delete_owned(conn, ids)
            await conn.execute("DELETE FROM guests WHERE id = ANY(%s)", (ids,))
        return len(ids)

    @staticmethod
    async def _delete_owned(conn, owner_ids: list[str]) -> None:
        for table in _OWNED_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE owner_id = ANY(%s)", (owner_ids,))  # noqa: S608

    # ---- users ----

    async def get_user(self, user_id: str) -> User | None:
        async with self._db.connection() as conn:
            row = await (await conn.execute("SELECT * FROM users WHERE id = %s", (user_id,))).fetchone()
        return _to_user(row) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        async with self._db.connection() as conn:
            row = await (await conn.execute("SELECT * FROM users WHERE email = %s", (email.lower(),))).fetchone()
        return _to_user(row) if row else None

    async def add_user(self, user: User) -> User:
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO users (id, email, display_name, created_at, last_login_at) VALUES (%s, %s, %s, %s, %s)",
                (user.id, user.email.lower(), user.display_name, user.created_at, user.last_login_at),
            )
        return user

    async def record_login(self, user_id: str, at: datetime) -> None:
        async with self._db.connection() as conn:
            await conn.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (at, user_id))

    async def delete_user(self, user_id: str) -> None:
        async with self._db.transaction() as conn:
            await self._delete_owned(conn, [user_id])
            await conn.execute("DELETE FROM users WHERE id = %s", (user_id,))  # sessions cascade

    async def transfer_ownership(self, from_owner: str, to_owner: str) -> None:
        async with self._db.transaction() as conn:
            # Conversations on a guest source whose document the user already
            # holds move to the user's copy of that source.
            await conn.execute(
                """
                UPDATE conversations c
                   SET owner_id = %(to)s, source_id = u.id
                  FROM sources g
                  JOIN sources u ON u.document_id = g.document_id AND u.owner_id = %(to)s
                 WHERE c.source_id = g.id AND g.owner_id = %(from)s
                """,
                {"from": from_owner, "to": to_owner},
            )
            # Same for collection memberships, unless the user's copy is already in.
            await conn.execute(
                """
                UPDATE collection_sources cs
                   SET source_id = u.id
                  FROM sources g
                  JOIN sources u ON u.document_id = g.document_id AND u.owner_id = %(to)s
                 WHERE cs.source_id = g.id AND g.owner_id = %(from)s
                   AND NOT EXISTS (
                       SELECT 1 FROM collection_sources x
                        WHERE x.collection_id = cs.collection_id AND x.source_id = u.id
                   )
                """,
                {"from": from_owner, "to": to_owner},
            )
            # Drop the guest's duplicates; the shared document stays via the user's copy.
            await conn.execute(
                """
                DELETE FROM sources g
                 USING sources u
                 WHERE g.owner_id = %(from)s AND u.owner_id = %(to)s AND u.document_id = g.document_id
                """,
                {"from": from_owner, "to": to_owner},
            )
            for table in _OWNED_TABLES:
                await conn.execute(
                    f"UPDATE {table} SET owner_id = %(to)s WHERE owner_id = %(from)s",  # noqa: S608
                    {"from": from_owner, "to": to_owner},
                )
            await conn.execute("DELETE FROM guests WHERE id = %s", (from_owner,))

    # ---- login codes ----

    async def add_login_code(self, code: LoginCode) -> LoginCode:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO login_codes (id, email, display_name, code_hash, attempts, expires_at, consumed_at,
                                         created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    code.id,
                    code.email.lower(),
                    code.display_name,
                    code.code_hash,
                    code.attempts,
                    code.expires_at,
                    code.consumed_at,
                    code.created_at,
                ),
            )
        return code

    async def latest_login_code(self, email: str) -> LoginCode | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM login_codes WHERE email = %s AND consumed_at IS NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (email.lower(),),
                )
            ).fetchone()
        return _to_login_code(row) if row else None

    async def record_code_attempt(self, code_id: str) -> int:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "UPDATE login_codes SET attempts = attempts + 1 WHERE id = %s RETURNING attempts", (code_id,)
                )
            ).fetchone()
        return int(row["attempts"]) if row else 0

    async def consume_login_code(self, code_id: str, at: datetime) -> None:
        async with self._db.connection() as conn:
            await conn.execute("UPDATE login_codes SET consumed_at = %s WHERE id = %s", (at, code_id))

    async def purge_login_codes(self, before: datetime) -> int:
        async with self._db.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM login_codes WHERE expires_at < %s OR consumed_at IS NOT NULL", (before,)
            )
            return cur.rowcount

    # ---- sessions ----

    async def add_session(self, session: Session) -> Session:
        async with self._db.connection() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    session.id,
                    session.user_id,
                    session.token_hash,
                    session.created_at,
                    session.expires_at,
                    session.last_seen_at,
                ),
            )
        return session

    async def get_session(self, token_hash: str) -> tuple[Session, User] | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT s.id AS session_id, s.user_id, s.token_hash, s.created_at AS session_created_at,
                           s.expires_at, s.last_seen_at,
                           u.id, u.email, u.display_name, u.created_at, u.last_login_at
                      FROM sessions s JOIN users u ON u.id = s.user_id
                     WHERE s.token_hash = %s
                    """,
                    (token_hash,),
                )
            ).fetchone()
        if row is None:
            return None
        session = Session(
            id=row["session_id"],
            user_id=row["user_id"],
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            created_at=row["session_created_at"],
            last_seen_at=row["last_seen_at"],
        )
        return session, _to_user(row)

    async def touch_session(self, session_id: str, seen_at: datetime, expires_at: datetime) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET last_seen_at = %s, expires_at = %s WHERE id = %s",
                (seen_at, expires_at, session_id),
            )

    async def delete_session(self, session_id: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    async def purge_sessions(self, before: datetime) -> int:
        async with self._db.connection() as conn:
            cur = await conn.execute("DELETE FROM sessions WHERE expires_at < %s", (before,))
            return cur.rowcount


def _to_user(row: dict[str, Any]) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


def _to_login_code(row: dict[str, Any]) -> LoginCode:
    return LoginCode(
        id=row["id"],
        email=row["email"],
        code_hash=row["code_hash"],
        expires_at=row["expires_at"],
        display_name=row["display_name"],
        attempts=row["attempts"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
    )
