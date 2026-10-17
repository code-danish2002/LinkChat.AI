"""The catalogue of things LinkChat can do with a source.

Every mode is the same retrieval and generation machinery with a different
task instruction, so adding one is a single entry here plus nothing else.

Two scopes exist. `retrieval` modes answer from the top matching excerpts
and need a typed question. `document` modes read the whole source and need
no input at all, which is what makes one-click summaries possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.exceptions import ValidationError
from app.domain.models import ChatMode

Scope = Literal["retrieval", "document"]


@dataclass(frozen=True)
class ModeSpec:
    id: ChatMode
    label: str
    description: str
    scope: Scope
    instruction: str
    needs_input: bool = False
    input_label: str | None = None
    input_placeholder: str | None = None
    # Shown in the transcript as the user's turn when the mode takes no input.
    default_prompt: str = ""
    max_tokens: int | None = None
    temperature: float | None = None

    @property
    def is_document_scope(self) -> bool:
        return self.scope == "document"


_MODES: tuple[ModeSpec, ...] = (
    ModeSpec(
        id=ChatMode.QA,
        label="Ask",
        description="Answer a question from the most relevant passages.",
        scope="retrieval",
        needs_input=True,
        input_label="Question",
        input_placeholder="Ask anything about this source…",
        instruction="Answer the question using the excerpts.",
    ),
    ModeSpec(
        id=ChatMode.SUMMARY,
        label="Summarise",
        description="A short summary of the whole source.",
        scope="document",
        default_prompt="Summarise this source.",
        instruction=(
            "Write a summary of the source. Open with a single sentence saying what it is and what "
            "it covers, then give two or three short paragraphs of substance. Name the specifics: "
            "people, numbers, conclusions. Do not pad."
        ),
    ),
    ModeSpec(
        id=ChatMode.KEY_POINTS,
        label="Key points",
        description="The main takeaways as a bullet list.",
        scope="document",
        default_prompt="What are the key points?",
        instruction=(
            "List the key points as a Markdown bullet list, most important first, at most eight "
            "bullets. Each bullet is one sentence that carries a concrete fact, not a topic label."
        ),
    ),
    ModeSpec(
        id=ChatMode.OUTLINE,
        label="Outline",
        description="A section or chapter outline, with timestamps when the source is timed.",
        scope="document",
        default_prompt="Give me an outline of this source.",
        instruction=(
            "Produce an outline of the source in order. Use a Markdown bullet per section. When the "
            "excerpts carry timestamps, begin each bullet with the timestamp in bold, for example "
            "**6:39**, taken from the excerpt where that section starts. Give each section a short "
            "title and one sentence of description."
        ),
        max_tokens=1800,
    ),
    ModeSpec(
        id=ChatMode.ACTION_ITEMS,
        label="Action items",
        description="Concrete tasks, decisions and deadlines.",
        scope="document",
        default_prompt="What are the action items?",
        instruction=(
            "Extract every action item, decision and deadline as a Markdown checklist using `- [ ] `. "
            "Name the owner and the date when the source states them. If the source contains none, "
            "say so in one sentence instead of inventing any."
        ),
    ),
    ModeSpec(
        id=ChatMode.EXTRACT,
        label="Extract data",
        description="Pull structured fields into a table.",
        scope="document",
        needs_input=False,
        input_label="Fields to extract",
        input_placeholder="e.g. price, specs, dates, people (leave empty to decide for me)",
        default_prompt="Extract the structured data from this source.",
        instruction=(
            "Extract the structured data into a single Markdown table. If the user named fields, use "
            "exactly those as columns. Otherwise choose the columns the source actually supports, such "
            "as names, prices, specifications, dates or metrics. Copy values verbatim, never round or "
            "reformat them, and write `not stated` where the source is silent. Add nothing after the "
            "table except a one-line note about anything you could not find."
        ),
        max_tokens=1800,
    ),
    ModeSpec(
        id=ChatMode.FLASHCARDS,
        label="Flashcards",
        description="Question and answer pairs for study.",
        scope="document",
        default_prompt="Make flashcards from this source.",
        instruction=(
            "Write between eight and twelve flashcards covering the material. Format each as a "
            "Markdown bullet: `**Q:** question` then a nested bullet `**A:** answer`. Each answer must "
            "be one or two sentences and come straight from the source."
        ),
        max_tokens=1800,
    ),
    ModeSpec(
        id=ChatMode.QUIZ,
        label="Quiz me",
        description="Multiple-choice questions with an answer key.",
        scope="document",
        default_prompt="Quiz me on this source.",
        instruction=(
            "Write five multiple-choice questions about the source. Number them, give four options "
            "labelled A to D, and make the wrong options plausible. After all five, add a `### Answers` "
            "heading with the correct letter and a one-line reason for each."
        ),
        max_tokens=1800,
    ),
    ModeSpec(
        id=ChatMode.SIMPLIFY,
        label="Explain simply",
        description="Plain language, no jargon.",
        scope="document",
        default_prompt="Explain this source in simple terms.",
        instruction=(
            "Explain the source so a curious twelve-year-old follows it. Short sentences, everyday "
            "words, and an analogy where one genuinely helps. Keep every fact accurate and do not "
            "talk down to the reader."
        ),
    ),
    ModeSpec(
        id=ChatMode.TRANSLATE,
        label="Translate",
        description="Translate a summary into another language.",
        scope="document",
        needs_input=True,
        input_label="Target language",
        input_placeholder="e.g. Hindi, Spanish, French",
        default_prompt="Translate this source.",
        instruction=(
            "Summarise the source and write the entire answer in the language the user named. "
            "Translate the content rather than transliterating it, and keep proper nouns, figures "
            "and units intact."
        ),
    ),
    ModeSpec(
        id=ChatMode.FACT_CHECK,
        label="Fact-check",
        description="Check a claim against what the source actually says.",
        scope="retrieval",
        needs_input=True,
        input_label="Claim",
        input_placeholder="Paste the claim to check against this source…",
        instruction=(
            "Decide whether the excerpts support the user's claim. Begin with exactly one of "
            "**Supported**, **Contradicted**, or **Not addressed** in bold. Then explain in two or "
            "three sentences, quoting the decisive wording. Judge only against the excerpts: if they "
            "are silent, the verdict is **Not addressed**, however plausible the claim sounds."
        ),
    ),
    ModeSpec(
        id=ChatMode.BLOG_POST,
        label="Blog post",
        description="Rewrite the source as an article.",
        scope="document",
        default_prompt="Turn this source into a blog post.",
        instruction=(
            "Rewrite the material as a blog post: a title as an `#` heading, a one-paragraph hook, "
            "three or four `##` sections with substance, and a short closing paragraph. Keep every "
            "fact faithful to the source and invent no examples."
        ),
        max_tokens=2000,
        temperature=0.4,
    ),
)

MODES: dict[ChatMode, ModeSpec] = {spec.id: spec for spec in _MODES}


def all_modes() -> list[ModeSpec]:
    return list(_MODES)


def get_mode(mode: ChatMode | str) -> ModeSpec:
    try:
        key = ChatMode(mode)
    except ValueError as exc:
        raise ValidationError(f"Unknown mode: {mode!r}") from exc
    return MODES[key]
