import numpy as np

from app.domain.models import Chunk
from app.storage.vector.local_store import LocalHybridVectorStore


async def test_hybrid_search_round_trip(tmp_path):
    store = LocalHybridVectorStore(tmp_path, embedding_model="test-model", vector_candidates=3, keyword_candidates=3)
    chunks = [
        Chunk(id="c1", source_id="s", index=0, text="The Panic Monster wakes up near deadlines."),
        Chunk(id="c2", source_id="s", index=1, text="Instant gratification monkey steers the wheel."),
        Chunk(id="c3", source_id="s", index=2, text="Product SKU X-9000 costs 49 dollars."),
    ]
    vectors = np.eye(3, dtype=np.float32)
    await store.upsert("s", chunks, vectors)
    assert await store.exists("s")

    # keyword hit dominates even when the vector points elsewhere
    results = await store.search("s", np.array([0, 1, 0], dtype=np.float32), "SKU X-9000 price", top_k=2)
    ids = [r.chunk.id for r in results]
    assert "c3" in ids and "c2" in ids
    assert results[0].keyword_score is not None

    # survives a cold cache (fresh instance reads from disk)
    fresh = LocalHybridVectorStore(tmp_path, embedding_model="test-model")
    again = await fresh.search("s", np.array([1, 0, 0], dtype=np.float32), "monster", top_k=1)
    assert again[0].chunk.id == "c1"

    await store.delete("s")
    assert not await store.exists("s")


async def test_switching_embedding_model_is_detected_not_silently_ignored(tmp_path):
    """Vectors from two models are incomparable, so a stale index must say so."""
    import pytest

    from app.core.exceptions import StaleIndexError

    chunks = [Chunk(id="c1", source_id="s", index=0, text="hybrid search beats either alone")]
    old = LocalHybridVectorStore(tmp_path, embedding_model="old-model")
    await old.upsert("s", chunks, np.eye(1, 3, dtype=np.float32))
    assert await old.exists("s")

    # A server configured with a different model must not serve this index.
    fresh = LocalHybridVectorStore(tmp_path, embedding_model="new-model")
    assert not await fresh.exists("s"), "a stale index must not count as present"

    with pytest.raises(StaleIndexError, match="Re-read"):
        await fresh.search("s", np.array([1, 0, 0], dtype=np.float32), "search", top_k=1)
    with pytest.raises(StaleIndexError):
        await fresh.all_chunks("s")

    # Rebuilding under the new model clears it.
    await fresh.upsert("s", chunks, np.eye(1, 3, dtype=np.float32))
    assert await fresh.exists("s")
    assert len(await fresh.all_chunks("s")) == 1


async def test_missing_index_is_absent_rather_than_stale(tmp_path):
    store = LocalHybridVectorStore(tmp_path, embedding_model="m")
    assert not await store.exists("never_built")
    assert await store.search("never_built", np.array([1.0], dtype=np.float32), "q", top_k=1) == []
    assert await store.all_chunks("never_built") == []
