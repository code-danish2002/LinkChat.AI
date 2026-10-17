"""SQLite repositories for sources, collections, conversations and messages.

Uses the standard library driver in WAL mode behind a lock. Calls are short
single-row operations, so they are served synchronously; moving to Postgres
means re-implementing the repository ports and nothing else.

`_migrate` brings an existing database up to the current shape, so adding a
column never requires the user to delete their data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from app.domain.models import (
    ChatMode,
    Citation,
    Collection,
    Conversation,
    Message,
    MessageRole,
    Source,
    SourceKind,
    SourceStatus,
    utcnow,
)
from app.domain.ports import CollectionRepository, ConversationRepository, SourceRepository

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- the gallery lists newest first, which is a scan without this
CREATE INDEX IF NOT EXISTS idx_sources_created ON sources(created_at DESC);
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_sources (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, source_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
    collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_source ON conversations(source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_collection ON conversations(collection_id, created_at);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    mode TEXT NOT NULL DEFAULT 'qa',
    citations TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self._migrate()
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self._conn.close()

    # ---- migrations ----

    def _tables(self) -> set[str]:
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row["name"] for row in rows}

    def _columns(self, table: str) -> dict[str, sqlite3.Row]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"]: row for row in rows}

    def _migrate(self) -> None:
        tables = self._tables()
        if "conversations" not in tables:
            return  # fresh database: _SCHEMA creates the current shape

        messages = self._columns("messages")
        if messages and "mode" not in messages:
            log.info("migrating messages: adding mode column")
            self._conn.execute("ALTER TABLE messages ADD COLUMN mode TEXT NOT NULL DEFAULT 'qa'")

        conversations = self._columns("conversations")
        if conversations and "collection_id" not in conversations:
            log.info("migrating conversations: adding collection_id column")
            self._conn.execute("ALTER TABLE conversations ADD COLUMN collection_id TEXT")
            conversations = self._columns("conversations")

        # A collection-scoped conversation has no source_id, so the old
        # NOT NULL constraint has to go. SQLite needs a table rebuild for that.
        source_column = conversations.get("source_id")
        if source_column is not None and source_column["notnull"]:
            log.info("migrating conversations: relaxing source_id to nullable")
            self._conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN;
                CREATE TABLE conversations_new (
                    id TEXT PRIMARY KEY,
                    source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
                    collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO conversations_new (id, source_id, collection_id, title, created_at, updated_at)
                    SELECT id, source_id, collection_id, title, created_at, updated_at FROM conversations;
                DROP TABLE conversations;
                ALTER TABLE conversations_new RENAME TO conversations;
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteSourceRepository(SourceRepository):
    def __init__(self, db: Database):
        self._db = db

    def add(self, source: Source) -> Source:
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO sources (id,url,kind,title,status,error,chunk_count,metadata,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    source.id,
                    source.url,
                    source.kind.value,
                    source.title,
                    source.status.value,
                    source.error,
                    source.chunk_count,
                    json.dumps(source.metadata, ensure_ascii=False),
                    source.created_at.isoformat(),
                    source.updated_at.isoformat(),
                ),
            )
        return source

    def update(self, source: Source) -> Source:
        source.updated_at = utcnow()
        with self._db.lock:
            self._db.conn.execute(
                "UPDATE sources SET kind=?, title=?, status=?, error=?, chunk_count=?, metadata=?,"
                " updated_at=? WHERE id=?",
                (
                    source.kind.value,
                    source.title,
                    source.status.value,
                    source.error,
                    source.chunk_count,
                    json.dumps(source.metadata, ensure_ascii=False),
                    source.updated_at.isoformat(),
                    source.id,
                ),
            )
        return source

    def get(self, source_id: str) -> Source | None:
        with self._db.lock:
            row = self._db.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return self._to_source(row) if row else None

    def get_by_url(self, url: str) -> Source | None:
        with self._db.lock:
            row = self._db.conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()
        return self._to_source(row) if row else None

    def list(self, limit: int = 50) -> list[Source]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM sources ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_source(r) for r in rows]

    def delete(self, source_id: str) -> None:
        with self._db.lock:
            self._db.conn.execute("DELETE FROM sources WHERE id=?", (source_id,))

    @staticmethod
    def _to_source(row: sqlite3.Row) -> Source:
        return Source(
            id=row["id"],
            url=row["url"],
            kind=SourceKind(row["kind"]),
            title=row["title"],
            status=SourceStatus(row["status"]),
            error=row["error"],
            chunk_count=row["chunk_count"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )


class SqliteCollectionRepository(CollectionRepository):
    def __init__(self, db: Database):
        self._db = db

    def add(self, collection: Collection) -> Collection:
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO collections (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                (
                    collection.id,
                    collection.title,
                    collection.created_at.isoformat(),
                    collection.updated_at.isoformat(),
                ),
            )
            self._write_members(collection)
        return collection

    def update(self, collection: Collection) -> Collection:
        collection.updated_at = utcnow()
        with self._db.lock:
            self._db.conn.execute(
                "UPDATE collections SET title=?, updated_at=? WHERE id=?",
                (collection.title, collection.updated_at.isoformat(), collection.id),
            )
            self._db.conn.execute(
                "DELETE FROM collection_sources WHERE collection_id=?", (collection.id,)
            )
            self._write_members(collection)
        return collection

    def get(self, collection_id: str) -> Collection | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM collections WHERE id=?", (collection_id,)
            ).fetchone()
            if row is None:
                return None
            members = self._member_ids(collection_id)
        return self._to_collection(row, members)

    def list(self, limit: int = 50) -> list[Collection]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM collections ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._to_collection(row, self._member_ids(row["id"])) for row in rows]

    def delete(self, collection_id: str) -> None:
        with self._db.lock:
            self._db.conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))

    # ---- internals (callers hold the lock) ----

    def _write_members(self, collection: Collection) -> None:
        self._db.conn.executemany(
            "INSERT OR IGNORE INTO collection_sources (collection_id,source_id,position) "
            "VALUES (?,?,?)",
            [(collection.id, sid, i) for i, sid in enumerate(collection.source_ids)],
        )

    def _member_ids(self, collection_id: str) -> list[str]:
        rows = self._db.conn.execute(
            "SELECT cs.source_id FROM collection_sources cs "
            "JOIN sources s ON s.id = cs.source_id "
            "WHERE cs.collection_id=? ORDER BY cs.position",
            (collection_id,),
        ).fetchall()
        return [row["source_id"] for row in rows]

    @staticmethod
    def _to_collection(row: sqlite3.Row, source_ids: list[str]) -> Collection:
        return Collection(
            id=row["id"],
            title=row["title"],
            source_ids=source_ids,
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )


class SqliteConversationRepository(ConversationRepository):
    def __init__(self, db: Database):
        self._db = db

    def add(self, conversation: Conversation) -> Conversation:
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO conversations (id,source_id,collection_id,title,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    conversation.id,
                    conversation.source_id,
                    conversation.collection_id,
                    conversation.title,
                    conversation.created_at.isoformat(),
                    conversation.updated_at.isoformat(),
                ),
            )
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return self._to_conversation(row) if row else None

    def list_for_source(self, source_id: str) -> list[Conversation]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM conversations WHERE source_id=? ORDER BY created_at DESC",
                (source_id,),
            ).fetchall()
        return [self._to_conversation(r) for r in rows]

    def list_for_collection(self, collection_id: str) -> list[Conversation]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM conversations WHERE collection_id=? ORDER BY created_at DESC",
                (collection_id,),
            ).fetchall()
        return [self._to_conversation(r) for r in rows]

    def delete(self, conversation_id: str) -> None:
        with self._db.lock:
            self._db.conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def add_message(self, message: Message) -> Message:
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
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO messages (id,conversation_id,role,content,model,mode,citations,"
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    message.id,
                    message.conversation_id,
                    message.role.value,
                    message.content,
                    message.model,
                    message.mode.value,
                    json.dumps(citations, ensure_ascii=False),
                    message.created_at.isoformat(),
                ),
            )
            self._db.conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (message.created_at.isoformat(), message.conversation_id),
            )
        return message

    def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        query = "SELECT rowid AS rid, * FROM messages WHERE conversation_id=? ORDER BY rid ASC"
        params: tuple = (conversation_id,)
        if limit is not None:  # newest N, returned in chronological order
            query = (
                "SELECT * FROM (SELECT rowid AS rid, * FROM messages WHERE conversation_id=? "
                "ORDER BY rid DESC LIMIT ?) ORDER BY rid ASC"
            )
            params = (conversation_id, limit)
        with self._db.lock:
            rows = self._db.conn.execute(query, params).fetchall()
        return [self._to_message(r) for r in rows]

    @staticmethod
    def _to_conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            source_id=row["source_id"],
            collection_id=row["collection_id"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _to_message(row: sqlite3.Row) -> Message:
        citations = [
            Citation(
                index=c["index"],
                chunk_id=c["chunk_id"],
                text=c["text"],
                score=c["score"],
                metadata=c.get("metadata") or {},
            )
            for c in json.loads(row["citations"] or "[]")
        ]
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            model=row["model"],
            mode=ChatMode(row["mode"] or "qa"),
            citations=citations,
            created_at=_dt(row["created_at"]),
        )
