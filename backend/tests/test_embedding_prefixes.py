"""Asymmetric-search models need query and document prefixes.

fastembed does not apply these itself, and getting them wrong does not raise:
it just retrieves worse. These tests pin the behaviour with a stub model, so no
multi-gigabyte download is needed.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.providers.embeddings.fastembed_provider import (
    PREFIX_SCHEMES,
    FastEmbedProvider,
    prefix_scheme_for,
)


class StubModel:
    """Records every text it is asked to embed."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.embedded: list[str] = []
        self.query_embedded: list[str] = []

    def embed(self, texts, batch_size=None, **kwargs):
        texts = list(texts)
        self.embedded.extend(texts)
        for index, _ in enumerate(texts):
            vector = np.zeros(self.dim, dtype=np.float32)
            vector[index % self.dim] = 1.0
            yield vector

    def query_embed(self, texts, **kwargs):
        texts = list(texts)
        self.query_embedded.extend(texts)
        for _ in texts:
            yield np.ones(self.dim, dtype=np.float32)


def make_provider(monkeypatch, tmp_path, model_name: str) -> tuple[FastEmbedProvider, StubModel]:
    stub = StubModel()
    monkeypatch.setattr(
        FastEmbedProvider,
        "_load_first_available",
        staticmethod(lambda candidates, cache_dir: (stub, model_name)),
    )
    provider = FastEmbedProvider([model_name], tmp_path)
    stub.embedded.clear()  # drop the dimension probe
    return provider, stub


async def test_embeddinggemma_gets_both_prefixes(monkeypatch, tmp_path):
    provider, stub = make_provider(monkeypatch, tmp_path, "google/embeddinggemma-300m")
    assert provider.prefixes is not None

    await provider.embed_documents(["the panic monster appears near deadlines"])
    assert stub.embedded == ["title: none | text: the panic monster appears near deadlines"]

    stub.embedded.clear()
    await provider.embed_query("what is the panic monster")
    assert stub.embedded == ["task: search result | query: what is the panic monster"]
    # the query must not go through query_embed, which would skip the prefix
    assert stub.query_embedded == []


async def test_e5_gets_query_and_passage_prefixes(monkeypatch, tmp_path):
    provider, stub = make_provider(monkeypatch, tmp_path, "intfloat/multilingual-e5-large")
    await provider.embed_documents(["a document about hybrid search"])
    assert stub.embedded == ["passage: a document about hybrid search"]

    stub.embedded.clear()
    await provider.embed_query("hybrid search")
    assert stub.embedded == ["query: hybrid search"]


async def test_the_default_model_is_left_alone(monkeypatch, tmp_path):
    """bge-small does not want prefixes, so nothing must be added."""
    provider, stub = make_provider(monkeypatch, tmp_path, "BAAI/bge-small-en-v1.5")
    assert provider.prefixes is None

    await provider.embed_documents(["plain text"])
    assert stub.embedded == ["plain text"]

    await provider.embed_query("plain query")
    assert stub.query_embedded == ["plain query"], "unprefixed models keep using query_embed"


async def test_vectors_are_normalised_either_way(monkeypatch, tmp_path):
    for model in ("google/embeddinggemma-300m", "BAAI/bge-small-en-v1.5"):
        provider, _ = make_provider(monkeypatch, tmp_path, model)
        documents = await provider.embed_documents(["one", "two"])
        assert np.allclose(np.linalg.norm(documents, axis=1), 1.0)
        query = await provider.embed_query("q")
        assert np.isclose(np.linalg.norm(query), 1.0)


@pytest.mark.parametrize("model_name", sorted(PREFIX_SCHEMES))
def test_every_scheme_has_a_text_placeholder(model_name):
    scheme = prefix_scheme_for(model_name)
    assert scheme is not None
    assert scheme.for_query("ASK") .endswith("ASK")
    assert "ASK" in scheme.for_query("ASK")
    assert "DOC" in scheme.for_document("DOC")
    # the two forms must differ, otherwise the model cannot tell them apart
    assert scheme.for_query("x") != scheme.for_document("x")


def test_prefix_schemes_match_fastembed_names():
    """A typo in a model name would silently disable its prefixes."""
    from fastembed import TextEmbedding

    known = {m["model"] for m in TextEmbedding.list_supported_models()}
    unknown = sorted(set(PREFIX_SCHEMES) - known)
    assert not unknown, f"prefix schemes reference models fastembed cannot load: {unknown}"
