"""Prompt construction for grounded answers.

Everything the model sees is assembled here: the system prompt describing
the target, the numbered excerpts it must cite, and the task instruction
supplied by the active mode.
"""

from __future__ import annotations

from app.domain.models import ChatMessage, Chunk, Citation, Message, SourceKind
from app.services.language import detect_language

_BASE_RULES = """You are LinkChat, an assistant that answers strictly from source material the user shared.

{target}

Rules:
1. Use only the numbered excerpts below. Never add facts from your own knowledge.
2. Cite the excerpts you use inline with plain ASCII square brackets, for example [2]
   (never full-width brackets or footnote symbols). Cite every factual claim.
3. If the excerpts do not contain what is needed, say so plainly and say what the source does
   cover instead. Never guess or fill gaps.
4. {kind_hint}
5. Write Markdown: short paragraphs, bullet lists for enumerations, code blocks for code.
6. Reply in the language of the user's LATEST message, even when earlier turns in this
   conversation used a different language. Switching language mid-conversation is normal and
   you follow the current question, not the previous one.

Your task: {instruction}"""

_KIND_HINTS = {
    SourceKind.YOUTUBE: (
        "Excerpts are transcript segments carrying timestamps; cite the timestamp, for example "
        "'around 12:40', whenever it helps the user find the moment."
    ),
    SourceKind.AUDIO: (
        "Excerpts are transcript segments carrying timestamps; cite the timestamp, for example "
        "'around 12:40', whenever it helps the user find the moment."
    ),
    SourceKind.PDF: "Excerpts come from a PDF; refer to sections or headings when they are visible.",
    SourceKind.DOCX: "Excerpts come from a Word document; preserve its headings and wording.",
    SourceKind.PPTX: "Excerpts come from a slide deck; refer to slide numbers when they are visible.",
    SourceKind.XLSX: (
        "Excerpts are spreadsheet rows with pipe-separated cells; quote figures exactly and never "
        "recompute them."
    ),
    SourceKind.EPUB: "Excerpts come from a book; refer to chapters when they are visible.",
    SourceKind.WEB: (
        "Excerpts come from a web page; quote product names, specifications and prices exactly as "
        "written."
    ),
    SourceKind.TEXT: "Excerpts come from a plain text document.",
}

#: Repeated immediately after the question. The system prompt says the same, but
#: a conversation that switches between two Latin-script languages will otherwise
#: keep answering in the earlier one: the nearest instruction is the one obeyed.
LANGUAGE_REMINDER = (
    "(Write your answer in the same language as the question directly above, "
    "regardless of the language used earlier in this conversation.)"
)


def language_reminder(question: str) -> str:
    """Name the language when it can be detected; otherwise stay generic.

    Naming it is far more reliable than "the same language as the question",
    which earlier turns in another language routinely override.
    """
    language = detect_language(question)
    if language is None:
        return LANGUAGE_REMINDER
    return (
        f"(Write your entire answer in {language}, regardless of the language used earlier "
        "in this conversation.)"
    )

_CONDENSE_INSTRUCTION = (
    "Condense this part of a longer source into dense notes for a colleague who cannot see the "
    "original. Keep every specific: names, numbers, dates, prices, conclusions and terminology. "
    "Keep the [n] markers attached to the facts they came from, so the notes stay traceable. "
    "Drop filler, repetition and pleasantries. Use compact bullets, no preamble."
)


def estimate_tokens(text: str) -> int:
    """Rough token count. Four characters per token is close enough for budgeting."""
    return max(1, len(text) // 4)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _target_line(title: str, kind: SourceKind, source_count: int) -> str:
    if source_count > 1:
        return (
            f'Target: a collection of {source_count} sources called "{title}". Each excerpt names the '
            "source it came from; say which source supports which claim."
        )
    return f'Source type: {kind.value}\nSource title: {title}'


def build_system_prompt(
    *, title: str, kind: SourceKind, instruction: str, source_count: int = 1
) -> str:
    return _BASE_RULES.format(
        target=_target_line(title, kind, source_count),
        kind_hint=_KIND_HINTS.get(kind, _KIND_HINTS[SourceKind.WEB]),
        instruction=instruction,
    )


def _excerpt_header(citation: Citation) -> str:
    parts = [f"[{citation.index}]"]
    origin = citation.metadata.get("source_title")
    if origin:
        parts.append(f'from "{origin}"')
    start = citation.metadata.get("start")
    if start is not None:
        parts.append(f"(at {format_timestamp(float(start))})")
    return " ".join(parts)


def format_excerpts(citations: list[Citation]) -> str:
    if not citations:
        return "(no relevant excerpts were found)"
    return "\n\n".join(f"{_excerpt_header(c)}\n{c.text}" for c in citations)


def render_chunks(chunks: list[Chunk], *, source_titles: dict[str, str] | None = None) -> str:
    """Whole-document text with [n] markers so answers stay citable.

    Markers are positions in `chunks`, not per-source indexes, so they stay
    unique when several sources are rendered together.
    """
    blocks: list[str] = []
    for position, chunk in enumerate(chunks, start=1):
        header = [f"[{position}]"]
        if source_titles and len(source_titles) > 1:
            title = source_titles.get(chunk.document_id)
            if title:
                header.append(f'from "{title}"')
        start = chunk.metadata.get("start")
        if start is not None:
            header.append(f"(at {format_timestamp(float(start))})")
        blocks.append(f"{' '.join(header)} {chunk.text}")
    return "\n\n".join(blocks)


def build_retrieval_messages(
    *,
    title: str,
    kind: SourceKind,
    instruction: str,
    history: list[Message],
    question: str,
    citations: list[Citation],
    source_count: int = 1,
) -> list[ChatMessage]:
    messages = [
        ChatMessage(
            "system",
            build_system_prompt(
                title=title, kind=kind, instruction=instruction, source_count=source_count
            ),
        )
    ]
    messages.extend(ChatMessage(past.role.value, past.content) for past in history)
    messages.append(
        ChatMessage(
            "user",
            f"Excerpts from the source:\n\n{format_excerpts(citations)}\n\n"
            f"Question: {question}\n\n{language_reminder(question)}",
        )
    )
    return messages


def build_document_messages(
    *,
    title: str,
    kind: SourceKind,
    instruction: str,
    document: str,
    user_input: str | None = None,
    source_count: int = 1,
) -> list[ChatMessage]:
    request = f"Source contents:\n\n{document}"
    if user_input:
        request += (
            f"\n\nAdditional instruction from the user: {user_input}"
            f"\n\n{language_reminder(user_input)}"
        )
    return [
        ChatMessage(
            "system",
            build_system_prompt(
                title=title, kind=kind, instruction=instruction, source_count=source_count
            ),
        ),
        ChatMessage("user", request),
    ]


def build_condense_messages(*, title: str, kind: SourceKind, window: str) -> list[ChatMessage]:
    return [
        ChatMessage(
            "system",
            build_system_prompt(
                title=title, kind=kind, instruction=_CONDENSE_INSTRUCTION, source_count=1
            ),
        ),
        ChatMessage("user", f"Part of the source:\n\n{window}"),
    ]
