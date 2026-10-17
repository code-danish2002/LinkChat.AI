"""Caption track selection.

Which track is chosen decides what language the whole index ends up in, so the
policy is pinned here with fake tracks rather than live videos.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.ingestion.youtube import select_track

PREFERRED = ["en", "en-US", "en-GB"]


@dataclass
class FakeTrack:
    language_code: str
    is_generated: bool = False
    is_translatable: bool = False
    translate_fails: bool = False
    translated_into: list[str] = field(default_factory=list)

    def translate(self, code: str) -> FakeTrack:
        if self.translate_fails:
            raise RuntimeError("translation unavailable")
        return FakeTrack(language_code=code, is_generated=self.is_generated)


def test_uploader_english_captions_win():
    tracks = [FakeTrack("es"), FakeTrack("en"), FakeTrack("ja")]
    track, translated, why = select_track(tracks, PREFERRED)
    assert track.language_code == "en" and not translated
    assert "uploader captions in a preferred language (en)" in why


def test_regional_english_counts_as_english():
    track, _, _ = select_track([FakeTrack("fr"), FakeTrack("en-GB")], PREFERRED)
    assert track.language_code == "en-GB"


def test_generated_english_beats_other_written_languages():
    """Tim Urban's talk: generated English plus dozens of community translations."""
    tracks = [FakeTrack("ar"), FakeTrack("hy"), FakeTrack("bg"), FakeTrack("en", is_generated=True)]
    track, translated, why = select_track(tracks, PREFERRED)
    assert track.language_code == "en" and not translated
    assert "auto-generated" in why


def test_korean_only_video_keeps_korean():
    """Gangnam Style: one Korean auto-generated track, not translatable."""
    track, translated, why = select_track([FakeTrack("ko", is_generated=True)], PREFERRED)
    assert track.language_code == "ko"
    assert not translated, "the original must not be machine translated"
    assert "spoken language" in why


def test_uploader_captions_preferred_over_generated_for_the_same_language():
    """A French video with both: the uploader's own text is cleaner."""
    generated = FakeTrack("fr", is_generated=True)
    written = FakeTrack("fr")
    track, translated, why = select_track([generated, written], PREFERRED)
    assert track is written and not translated
    assert "uploader captions in the spoken language (fr)" in why


def test_original_language_is_kept_rather_than_translated():
    """Translating would add errors, and the models read Arabic directly."""
    arabic = FakeTrack("ar", is_generated=True, is_translatable=True)
    track, translated, _ = select_track([arabic], PREFERRED)
    assert track.language_code == "ar"
    assert not translated


def test_an_arbitrary_language_is_never_chosen():
    """With no spoken-language hint, the first *written* track is used, not any track."""
    tracks = [FakeTrack("da"), FakeTrack("ga"), FakeTrack("fr")]
    track, _, why = select_track(tracks, PREFERRED)
    assert track.language_code == "da"
    assert "no spoken-language track" in why


def test_translation_is_the_last_resort_only():
    """Nothing written and nothing generated: translate whatever is offered."""
    odd = FakeTrack("und", is_generated=False, is_translatable=True)
    odd.is_generated = True  # generated, so it is treated as the spoken language
    track, translated, why = select_track([odd], PREFERRED)
    assert not translated, "a generated track is the original and is kept"
    assert track.language_code == "und"


def test_untranslatable_single_track_is_still_returned():
    only = FakeTrack("zz", is_generated=True, is_translatable=False)
    track, translated, _ = select_track([only], PREFERRED)
    assert track is only and not translated


def test_no_tracks_at_all():
    track, translated, why = select_track([], PREFERRED)
    assert track is None and not translated and why == "no tracks"


@pytest.mark.parametrize("preferred", [["fr"], ["ar", "en"], ["da"], ["en", "ar"]])
def test_the_preferred_list_is_honoured_in_order(preferred):
    """The first entry is the first choice, not whichever track is listed first."""
    tracks = [FakeTrack("en"), FakeTrack("fr"), FakeTrack("ar"), FakeTrack("da")]
    track, _, _ = select_track(tracks, preferred)
    assert track.language_code == preferred[0]


def test_a_later_preference_is_used_when_the_first_is_absent():
    tracks = [FakeTrack("en"), FakeTrack("fr")]
    track, _, _ = select_track(tracks, ["da", "fr", "en"])
    assert track.language_code == "fr"
