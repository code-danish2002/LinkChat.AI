"""Cleaning what speech recognition heard before it becomes a question.

Whisper on short or noisy audio (an echo of the app's own voice through
laptop speakers, a cough, silence) has two failure modes: it repeats a
fragment over and over, and it invents a stock phrase such as "Thank you
for watching". Both would otherwise be sent to the model as a question.
"""

from __future__ import annotations

import re

#: Whole transcripts that are almost always invented rather than heard.
_HALLUCINATIONS = {
    "thank you",
    "thanks",
    "thank you for watching",
    "thanks for watching",
    "thank you so much for watching",
    "please subscribe",
    "subtitles by the amara.org community",
    "you",
    "bye",
    "okay",
    "ok",
    "hmm",
    "um",
    "uh",
}

_WORD_RE = re.compile(r"\S+")


def collapse_repeats(text: str, *, max_ngram: int = 12) -> str:
    """Remove immediate repetitions of a phrase: "what is what is what is this" -> "what is this".

    Works on word n-grams from the longest down, so a repeated sentence is
    caught before its individual words are. Only consecutive repeats are
    removed; "the cat saw the cat" stays as it is.
    """
    words = _WORD_RE.findall(text)
    changed = True
    while changed:
        changed = False
        for size in range(min(max_ngram, len(words) // 2), 0, -1):
            i = 0
            out: list[str] = []
            while i < len(words):
                gram = words[i : i + size]
                if len(gram) == size and _normalised(gram) == _normalised(words[i + size : i + 2 * size]):
                    # Skip every further immediate copy of this gram.
                    j = i + size
                    while _normalised(words[j : j + size]) == _normalised(gram):
                        j += size
                    out.extend(gram)
                    i = j
                    changed = True
                else:
                    out.append(words[i])
                    i += 1
            words = out
    return " ".join(words)


def _normalised(gram: list[str]) -> tuple[str, ...]:
    # Case-insensitive but punctuation-sensitive: a looping transcript repeats
    # the fragment verbatim, while "What is this page? What is this page about?"
    # is two different questions and must survive.
    return tuple(w.lower() for w in gram)


def clean_transcript(text: str) -> str:
    """The transcript as a question, or an empty string when nothing real was heard."""
    collapsed = collapse_repeats(" ".join((text or "").split()))
    bare = collapsed.lower().strip(" .,!?…\"'")
    if not bare or bare in _HALLUCINATIONS:
        return ""
    return collapsed
