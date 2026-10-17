"""Shared doubles so tests never touch the network or load real models."""

from __future__ import annotations

import numpy as np

from app.domain.models import LoadedDocument, Segment, SourceKind
from app.domain.ports import EmbeddingProvider, SourceLoader, Transcriber


class HashEmbeddings(EmbeddingProvider):
    """Deterministic pseudo-embeddings keyed by text, so retrieval is repeatable."""

    model_name = "hash-test"
    dimension = 16

    def _vector(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) % (2**32)
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(self.dimension).astype(np.float32)
        return vector / np.linalg.norm(vector)

    async def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vector(t) for t in texts])

    async def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


class FakeLoader(SourceLoader):
    kind = SourceKind.WEB

    def __init__(self, sections: int = 20, host: str = "example.com"):
        self._sections = sections
        self._host = host

    def supports(self, url: str) -> bool:
        return self._host in url

    async def load(self, url: str) -> LoadedDocument:
        text = "\n\n".join(
            f"Section {i}. LinkChat lets you chat with any URL. Fact {i}: the answer is {i * 7}."
            for i in range(self._sections)
        )
        return LoadedDocument(
            title="Example page",
            kind=self.kind,
            segments=[Segment(text=text)],
            metadata={"site_name": "Example"},
        )


class EchoLLMClient:
    """Streams back a fixed answer and records what it was asked."""

    def __init__(self, answer: str = "The answer is 7 [1]."):
        self.answer = answer
        self.calls: list[dict] = []

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        self.calls.append(
            {
                "model": model,
                "system": messages[0].content,
                "user": messages[-1].content,
                "roles": [m.role for m in messages],
                "reminder_after_history": any(
                    m.role == "system" and "same language as the question" in m.content
                    for m in messages[1:]
                ),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        for word in self.answer.split(" "):
            yield word + " "


class FakeTranscriber(Transcriber):
    def __init__(self, available: bool = True):
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, payload: bytes, filename: str):
        segments = [
            Segment(text=f"spoken line {i} about the subject", start=float(i * 5), end=float(i * 5 + 5))
            for i in range(12)
        ]
        return segments, {"transcription_model": "fake/whisper", "language": "en", "title": filename}
