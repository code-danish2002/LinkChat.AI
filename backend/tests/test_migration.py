"""An existing v1 database must survive the upgrade with its rows intact."""

from __future__ import annotations

import sqlite3

from app.domain.models import ChatMode, Collection, Conversation, Message, MessageRole
from app.storage.db.sqlite import (
    Database,
    SqliteCollectionRepository,
    SqliteConversationRepository,
)

_V1_SCHEMA = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, title TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, chunk_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, model TEXT,
    citations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
"""


def _seed_v1(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_SCHEMA)
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO sources VALUES ('src_1','https://x.test/a','web','Old page','ready',NULL,3,'{}',?,?)",
        (now, now),
    )
    conn.execute("INSERT INTO conversations VALUES ('cnv_1','src_1','Old chat',?,?)", (now, now))
    conn.execute("INSERT INTO messages VALUES ('msg_1','cnv_1','user','old question',NULL,'[]',?)", (now,))
    conn.commit()
    conn.close()


def test_v1_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.sqlite3"
    _seed_v1(path)

    db = Database(path)
    try:
        columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(messages)")}
        assert "mode" in columns
        conversation_columns = {row["name"]: row for row in db.conn.execute("PRAGMA table_info(conversations)")}
        assert "collection_id" in conversation_columns
        assert not conversation_columns["source_id"]["notnull"], "source_id must become nullable"

        version = db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert version["value"] == "2"

        # existing rows survived, and old messages default to the qa mode
        conversations = SqliteConversationRepository(db)
        existing = conversations.get("cnv_1")
        assert existing is not None
        assert existing.source_id == "src_1"
        assert existing.collection_id is None
        messages = conversations.list_messages("cnv_1")
        assert [m.content for m in messages] == ["old question"]
        assert messages[0].mode is ChatMode.QA

        # and the new features work on the migrated database
        collections = SqliteCollectionRepository(db)
        collections.add(Collection(id="col_1", title="Group", source_ids=["src_1"]))
        assert collections.get("col_1").source_ids == ["src_1"]

        conversations.add(Conversation(id="cnv_2", title="Across", collection_id="col_1"))
        conversations.add_message(
            Message(
                id="msg_2",
                conversation_id="cnv_2",
                role=MessageRole.ASSISTANT,
                content="summary",
                mode=ChatMode.SUMMARY,
            )
        )
        assert [c.id for c in conversations.list_for_collection("col_1")] == ["cnv_2"]
        assert conversations.list_messages("cnv_2")[0].mode is ChatMode.SUMMARY
    finally:
        db.close()


def test_migrating_twice_is_a_no_op(tmp_path):
    path = tmp_path / "old.sqlite3"
    _seed_v1(path)
    Database(path).close()
    db = Database(path)
    try:
        assert SqliteConversationRepository(db).get("cnv_1") is not None
    finally:
        db.close()


def test_collection_membership_follows_source_deletion(tmp_path):
    db = Database(tmp_path / "fresh.sqlite3")
    try:
        now = "2026-01-01T00:00:00+00:00"
        for sid in ("src_a", "src_b"):
            db.conn.execute(
                "INSERT INTO sources VALUES (?,?,'web','T','ready',NULL,1,'{}',?,?)",
                (sid, f"https://x.test/{sid}", now, now),
            )
        collections = SqliteCollectionRepository(db)
        collections.add(Collection(id="col_1", title="G", source_ids=["src_a", "src_b"]))
        db.conn.execute("DELETE FROM sources WHERE id='src_a'")
        assert collections.get("col_1").source_ids == ["src_b"]
    finally:
        db.close()
