"""Speech recognition output is cleaned before it is treated as a question."""

from app.services.transcript import clean_transcript, collapse_repeats


def test_repeated_fragments_collapse_to_one():
    assert collapse_repeats("what is this page about what is this page about what is this page about") == (
        "what is this page about"
    )
    assert collapse_repeats("What is this page? What is this page? What is this page about?") == (
        "What is this page? What is this page about?"
    )
    assert collapse_repeats("the the the cat") == "the cat"
    # non-consecutive repeats are real language and must stay
    assert collapse_repeats("the cat saw the cat") == "the cat saw the cat"
    assert collapse_repeats("") == ""


def test_stock_hallucinations_count_as_silence():
    assert clean_transcript("Thank you for watching.") == ""
    assert clean_transcript("  you ") == ""
    assert clean_transcript("What is retrieval augmented generation?") == "What is retrieval augmented generation?"


def test_hindi_repeats_collapse_too():
    assert collapse_repeats("यह पेज किस बारे में है यह पेज किस बारे में है") == "यह पेज किस बारे में है"
