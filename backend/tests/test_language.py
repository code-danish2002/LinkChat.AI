"""Language detection, which decides what the prompt tells the model to reply in.

Naming the wrong language is worse than naming none, because the model obeys it.
So these tests check two things: that real questions are named correctly, and
that ambiguous input returns None rather than a guess.
"""

from __future__ import annotations

import pytest

from app.services.language import detect_language
from app.services.prompts import LANGUAGE_REMINDER, language_reminder

# One natural question per language, all asking the same thing.
QUESTIONS: dict[str, str] = {
    # scripts used by a single language
    "Hebrew": "מה המועד האחרון לפרויקט?",
    "Greek": "Ποια είναι η προθεσμία του έργου;",
    "Thai": "กำหนดส่งโครงการคือเมื่อไหร่",
    "Japanese": "プロジェクトの期限はいつですか",
    "Korean": "프로젝트 기한은 언제입니까",
    "Chinese": "这个项目的截止日期是什么时候",
    "Hindi": "परियोजना की समय सीमा क्या है?",
    "Bengali": "প্রকল্পের সময়সীমা কী?",
    "Tamil": "திட்டத்தின் காலக்கெடு என்ன?",
    # shared scripts, separated by distinctive letters
    "Arabic": "ما هو الموعد النهائي للمشروع؟",
    "Urdu": "منصوبے کی آخری تاریخ کیا ہے؟",
    "Russian": "Какой срок сдачи проекта?",
    "Ukrainian": "Який термін виконання проекту?",
    # Latin script
    "English": "What is the deadline of the project?",
    "Spanish": "¿Cuál es la fecha límite del proyecto?",
    "French": "Quelle est la date limite du projet ?",
    "German": "Was ist die Frist des Projekts?",
    "Portuguese": "Qual é o prazo do projeto?",
    "Italian": "Qual è la scadenza del progetto?",
    "Dutch": "Wat is de deadline van het project?",
    "Danish": "Hvad er projektets frist?",
    "Norwegian": "Hva er prosjektets frist?",
    "Swedish": "Vad är projektets deadline?",
    "Finnish": "Mikä on projektin määräaika?",
    "Irish": "Cad é spriocdháta an tionscadail?",
    "Polish": "Jaki jest termin projektu?",
    "Czech": "Jaký je termín projektu?",
    "Romanian": "Care este termenul limită al proiectului?",
    "Turkish": "Projenin son teslim tarihi nedir?",
    "Vietnamese": "Thời hạn của dự án là khi nào?",
    "Indonesian": "Kapan tenggat waktu proyek ini?",
    "Swahili": "Ni lini tarehe ya mwisho ya mradi?",
}


@pytest.mark.parametrize("language,question", sorted(QUESTIONS.items()))
def test_detects_the_question_language(language, question):
    assert detect_language(question) == language


def test_no_language_is_ever_misdetected_as_another():
    """A wrong name is worse than no name, so this is the property that matters."""
    wrong = {
        language: detect_language(question)
        for language, question in QUESTIONS.items()
        if detect_language(question) not in (language, None)
    }
    assert not wrong, f"misdetected: {wrong}"


@pytest.mark.parametrize(
    "pair",
    [
        ("Russian", "Ukrainian"),  # both Cyrillic
        ("Arabic", "Urdu"),  # both Arabic script
        ("Danish", "Norwegian"),  # nearly identical languages
        ("Spanish", "Portuguese"),  # close relatives
        ("Czech", "Polish"),
    ],
)
def test_confusable_pairs_stay_apart(pair):
    first, second = pair
    assert detect_language(QUESTIONS[first]) == first
    assert detect_language(QUESTIONS[second]) == second


@pytest.mark.parametrize("text", ["", "ok", "X-9000", "42", "RAG", "SKU X-9000 49", "   "])
def test_returns_none_when_there_is_nothing_to_judge(text):
    assert detect_language(text) is None


def test_ambiguous_latin_text_is_not_guessed():
    assert detect_language("Panic Monster deadline report") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Now summarise that in one sentence.", "English"),
        ("And the price?", "English"),
        ("Give me three bullet points.", "English"),
        ("Explain again, briefly.", "English"),
    ],
)
def test_short_follow_ups_are_still_detected(text, expected):
    """Follow-ups are short, and getting them wrong is what broke live runs."""
    assert detect_language(text) == expected


def test_a_single_marker_needs_no_competition():
    assert detect_language("Rework that") == "English"


def test_devanagari_wins_even_with_latin_words_mixed_in():
    assert detect_language("RAG के नुकसान क्या हैं?") == "Hindi"


def test_reminder_names_the_language_when_known():
    reminder = language_reminder("¿Cuáles son las limitaciones de RAG?")
    assert "in Spanish" in reminder
    assert "regardless of the language used earlier" in reminder


def test_reminder_falls_back_to_generic_wording():
    assert language_reminder("X-9000") == LANGUAGE_REMINDER
