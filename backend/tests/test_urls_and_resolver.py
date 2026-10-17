import pytest

from app.core.exceptions import ValidationError
from app.core.urls import normalize_url
from app.ingestion.youtube import extract_video_id


def test_normalize_adds_scheme_and_strips_tracking():
    assert normalize_url("Example.com/path?utm_source=x&id=1#frag") == "https://example.com/path?id=1"


def test_normalize_rejects_private_hosts():
    with pytest.raises(ValidationError):
        normalize_url("http://127.0.0.1:8000/admin")
    with pytest.raises(ValidationError):
        normalize_url("http://localhost/")
    with pytest.raises(ValidationError):
        normalize_url("ftp://example.com/file")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=10",
        "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(url):
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_rejects_non_video():
    assert extract_video_id("https://www.youtube.com/@somechannel") is None


def test_resolver_reports_what_it_accepts():
    from app.domain.models import SourceKind
    from app.domain.ports import SourceLoader
    from app.ingestion.resolver import SourceResolver

    class Specific(SourceLoader):
        kind = SourceKind.YOUTUBE

        def supports(self, url):
            return "youtube" in url

        async def load(self, url):  # pragma: no cover - not exercised here
            raise NotImplementedError

    class Detecting(SourceLoader):
        kind = SourceKind.TEXT
        label = "upload"

        def supports(self, url):
            return url.startswith("upload://")

        async def load(self, url):  # pragma: no cover - not exercised here
            raise NotImplementedError

    resolver = SourceResolver([Specific(), Detecting()])
    # a loader that detects the kind at runtime reports its label, not the placeholder
    assert resolver.kinds == ["youtube", "upload"]
    assert isinstance(resolver.resolve("upload://abc/x.txt"), Detecting)


def test_resolver_prefers_the_first_matching_loader():
    from app.domain.models import SourceKind
    from app.domain.ports import SourceLoader
    from app.ingestion.resolver import SourceResolver

    class Any(SourceLoader):
        def __init__(self, kind, name):
            self.kind = kind
            self.label = name

        def supports(self, url):
            return True

        async def load(self, url):  # pragma: no cover - not exercised here
            raise NotImplementedError

    first = Any(SourceKind.YOUTUBE, "first")
    resolver = SourceResolver([first, Any(SourceKind.WEB, "second")])
    assert resolver.resolve("https://anything") is first

    late = Any(SourceKind.PDF, "late")
    resolver.register(late, first=True)
    assert resolver.resolve("https://anything") is late
