"""Best-effort language detection for the user's question.

Telling the model "reply in the same language as the question" is not reliable
when a conversation switches between two languages written in the same script:
the earlier turns win. Naming the language outright does work, so the language
is detected here and stated in the prompt.

Deliberately dependency-free and deterministic. Three signals, in order of
trust:

1. the writing system, which is exact for most languages;
2. characters that are near-unique to one language, which separate languages
   that share a script, such as Ukrainian from Russian or Urdu from Arabic;
3. common function words, which handle the Latin-script languages.

Naming the wrong language is worse than naming none, because the model will
obey it. So anything short of a clear winner returns None and the caller falls
back to generic wording.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

#: Scripts used by a single language, so the script alone settles it.
_SCRIPT_LANGUAGES: dict[str, str] = {
    "DEVANAGARI": "Hindi",
    "HEBREW": "Hebrew",
    "GREEK": "Greek",
    "THAI": "Thai",
    "HANGUL": "Korean",
    "HIRAGANA": "Japanese",
    "KATAKANA": "Japanese",
    "BENGALI": "Bengali",
    "TAMIL": "Tamil",
    "TELUGU": "Telugu",
    "GUJARATI": "Gujarati",
    "GURMUKHI": "Punjabi",
    "KANNADA": "Kannada",
    "MALAYALAM": "Malayalam",
    "SINHALA": "Sinhala",
    "ETHIOPIC": "Amharic",
    "ARMENIAN": "Armenian",
    "GEORGIAN": "Georgian",
    "CJK": "Chinese",
}

#: Scripts shared by several languages, resolved by distinctive letters below.
#: The default applies when none of the distinctive sets match.
_SHARED_SCRIPTS: dict[str, tuple[str, dict[str, str]]] = {
    "CYRILLIC": (
        "Russian",
        {"Ukrainian": "їієґ", "Belarusian": "ўі", "Serbian": "ћђјљњ", "Bulgarian": "ъ"},
    ),
    "ARABIC": (
        "Arabic",
        {"Urdu": "ےٹڈڑںھپچژگ", "Persian": "پچژگ"},
    ),
}

#: Letters that effectively identify one Latin-script language.
_DISTINCTIVE_LETTERS: dict[str, str] = {
    "Turkish": "ışğ",
    "Polish": "łżśźćęąń",
    "Czech": "řůěťď",
    "Romanian": "ășțăî",
    "Vietnamese": "ơưạảấầệộ",
    "Icelandic": "þðæ",
}

#: Common function words. Chosen to be frequent in a sentence and reasonably
#: distinctive; words shared across a family are deliberately left out.
_LATIN_MARKERS: dict[str, frozenset[str]] = {
    "English": frozenset(
        "the is are was were what which who whose how why does did do done of and or but"
        " this that these those with from about into there their they its you your not"
        " have has had will would should could any main now again more briefly one two"
        " three please give tell show list explain summarise summarize sentence words"
        " bullet points instead also next then deadline project".split()
    ),
    "Spanish": frozenset(
        "el los las una unos unas que cual cuales como por para pero con sin sobre son"
        " esta estan fue fueron tiene tienen hay cuando donde mas muy tambien"
        " principales limitaciones ventajas fecha limite proyecto".split()
    ),
    "French": frozenset(
        "le les une des du au aux est sont etait etaient qui quoi quel quelle comment"
        " pourquoi mais avec sans sur dans pour plus tres aussi quand leurs cette ces"
        " avantages inconvenients date limite projet".split()
    ),
    "German": frozenset(
        "der die das den dem ein eine einer ist sind war waren was wer wie warum aber"
        " mit ohne uber auf fur und oder nicht haben hat hatte werden wird sehr auch"
        " wann wo vorteile nachteile frist projekts projekt".split()
    ),
    "Portuguese": frozenset(
        "os um uma uns umas qual quais como por para mas com sem sobre sao esta estao"
        " foi foram tem quando onde mais muito tambem vantagens limitacoes nao prazo"
        " projeto do da dos das".split()
    ),
    "Italian": frozenset(
        "il lo gli gl gli gl una delle degli che cosa quale quali come perche ma con"
        " senza su nel nella per piu molto anche quando dove sono era erano hanno"
        " vantaggi svantaggi scadenza progetto del della".split()
    ),
    "Dutch": frozenset(
        "de het een van en of maar met zonder over voor zijn was waren wat wie hoe"
        " waarom niet heeft hebben zeer ook wanneer waar deze dit die voordelen"
        " nadelen deadline het project".split()
    ),
    "Danish": frozenset(
        "er hvad hvordan hvorfor hvem hvilken hvilke og eller men med uden om til fra"
        " den det de har havde bliver meget ogsa hvornar hvor ikke projektets frist"
        " fordele ulemper".split()
    ),
    "Norwegian": frozenset(
        "er hva hvordan hvorfor hvem hvilken hvilke og eller men med uten om til fra"
        " den det de har hadde blir mye ogsa nar hvor ikke prosjektets prosjekt"
        " fordeler ulemper".split()
    ),
    "Swedish": frozenset(
        "ar vad hur varfor vem vilken vilka och eller men med utan om till fran den"
        " det de har hade blir mycket ocksa nar var inte projektets projekt fordelar"
        " nackdelar".split()
    ),
    "Finnish": frozenset(
        "on mika mita miten miksi kuka kenen ja tai mutta kanssa ilman noin ovat oli"
        " olivat hyvin myos milloin missa ei projektin maaraaika edut haitat".split()
    ),
    "Irish": frozenset(
        "an na is cad conas cen ce agus no ach le gan faoi ar don tionscadail"
        " spriocdhata buntaisti mibhuntaisti ata bhfuil se si".split()
    ),
    "Polish": frozenset(
        "jest jaki jaka jakie co jak dlaczego kto i lub ale z bez o do od ten ta to"
        " ma mial bardzo takze kiedy gdzie nie projektu termin zalety wady".split()
    ),
    "Czech": frozenset(
        "je jaky jaka jake co jak proc kdo a nebo ale s bez o do od ten ta to ma mel"
        " velmi take kdy kde ne projektu termin vyhody nevyhody".split()
    ),
    "Romanian": frozenset(
        "este care ce cum de ce cine si sau dar cu fara despre la din acest acesta"
        " are avea foarte cand unde nu proiectului termenul limita avantajele".split()
    ),
    "Turkish": frozenset(
        "nedir ne nasil neden kim hangi ve veya ama ile olmadan hakkinda icin bu su"
        " var yok cok da de nerede ne zaman projenin son teslim tarihi avantajlari".split()
    ),
    "Vietnamese": frozenset(
        "la gi nao the nao tai sao ai va hoac nhung voi khong ve cho nay do co rat"
        " cung khi dau thoi han cua du an loi ich".split()
    ),
    "Indonesian": frozenset(
        "adalah apa bagaimana mengapa siapa yang dan atau tetapi dengan tanpa tentang"
        " untuk ini itu ada tidak sangat juga kapan dimana tenggat waktu proyek"
        " keuntungan".split()
    ),
    "Swahili": frozenset(
        "ni nini vipi kwanini nani ambayo na au lakini kwa bila kuhusu hii hiyo kuna"
        " hakuna sana pia lini wapi tarehe mwisho mradi faida".split()
    ),
}

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_MIN_HITS = 2
#: A distinctive letter is worth this many marker hits.
_LETTER_WEIGHT = 2


def _script_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        for prefix in (*_SCRIPT_LANGUAGES, *_SHARED_SCRIPTS):
            if prefix in name:
                counts[prefix] += 1
                break
        else:
            if "LATIN" in name:
                counts["LATIN"] += 1
    return counts


def _resolve_shared(script: str, text: str) -> str:
    default, variants = _SHARED_SCRIPTS[script]
    lowered = text.lower()
    best, best_hits = default, 0
    for language, letters in variants.items():
        hits = sum(1 for letter in letters if letter in lowered)
        if hits > best_hits:
            best, best_hits = language, hits
    return best


def _detect_latin(text: str) -> str | None:
    words = {word.lower() for word in _WORD.findall(text)}
    if not words:
        return None
    lowered = text.lower()

    scores: dict[str, int] = {}
    for language, markers in _LATIN_MARKERS.items():
        scores[language] = len(words & markers)
    for language, letters in _DISTINCTIVE_LETTERS.items():
        if any(letter in lowered for letter in letters):
            scores[language] = scores.get(language, 0) + _LETTER_WEIGHT

    best = max(scores, key=lambda language: scores[language])
    hits = scores[best]
    runner_up = max((count for language, count in scores.items() if language != best), default=0)
    if hits == 0 or hits == runner_up:
        return None  # nothing matched, or too close to call
    if hits < _MIN_HITS and runner_up > 0:
        return None  # a single hit is only trusted when nothing else matched
    return best


def detect_language(text: str) -> str | None:
    """A language name to put in a prompt, or None when unsure."""
    text = (text or "").strip()
    if len(text) < 3:
        return None

    counts = _script_counts(text)
    if not counts:
        return None
    script = counts.most_common(1)[0][0]
    if script in _SCRIPT_LANGUAGES:
        return _SCRIPT_LANGUAGES[script]
    if script in _SHARED_SCRIPTS:
        return _resolve_shared(script, text)
    return _detect_latin(text)
