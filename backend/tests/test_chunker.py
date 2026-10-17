from app.domain.models import LoadedDocument, Segment, SourceKind
from app.ingestion.chunker import Chunker


def test_split_text_respects_size_and_overlaps():
    chunker = Chunker(chunk_size=300, chunk_overlap=60)
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 30).strip() + "." for i in range(12))
    pieces = chunker.split_text(text)
    assert len(pieces) > 3
    assert all(len(p) <= 300 for p in pieces)
    # every paragraph survives somewhere
    for i in range(12):
        assert any(f"Paragraph {i}." in p for p in pieces)


def test_split_text_handles_oversized_tokens():
    chunker = Chunker(chunk_size=250, chunk_overlap=20)
    text = "a" * 900  # no separators at all
    pieces = chunker.split_text(text)
    assert pieces and all(len(p) <= 250 for p in pieces)
    assert "".join(pieces).count("a") >= 900


def test_timed_segments_keep_timestamps():
    segments = [
        Segment(text=f"caption number {i} with some words", start=float(i * 4), end=float(i * 4 + 4)) for i in range(60)
    ]
    doc = LoadedDocument(title="t", kind=SourceKind.YOUTUBE, segments=segments)
    chunks = Chunker(chunk_size=400, chunk_overlap=80).chunk(doc, "src_1")
    assert len(chunks) > 2
    assert chunks[0].metadata["start"] == 0.0
    assert all("start" in c.metadata and "end" in c.metadata for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # overlap: consecutive chunks share text
    assert chunks[1].text.split(" ")[0:2] in [
        chunks[0].text.split(" ")[i : i + 2] for i in range(len(chunks[0].text.split(" ")))
    ]
