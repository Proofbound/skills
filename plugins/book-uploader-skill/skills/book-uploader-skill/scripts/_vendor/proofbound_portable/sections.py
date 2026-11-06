"""Section-beat helpers for the two-pass outline/chapter pipeline.

The server (ai-clients ``intelligent_proxy``: outline expansion, chapter
generation, summary) and book-builder's Script Mode both plan sections through
these, so the section spine and word-budget math have one definition.

``normalize_sections`` mirrors cc-template-api's ``normalize_outline_chapter``
legacy-compat rule; that one lives on a Pydantic model and also strips
``chapter_ideas``, so the two are kept in lockstep by hand.
"""

import logging

logger = logging.getLogger(__name__)

# Floor for an unweighted beat's word target, matching cc-template-api's
# WORD_TARGET_MIN. A beat must never be asked for 0 words: the 80% undersize
# check in generate_section_with_validation would accept a one-liner.
MIN_SECTION_WORDS = 100


class MalformedSectionsError(ValueError):
    """A chapter's ``sections`` is present but not a list of beats.

    The message names only the offending type, never its content, so callers
    may surface it.
    """


def normalize_sections(chapter: dict) -> list[dict]:
    """Return a chapter's section beats as a list of dicts.

    Tolerates the legacy ``chapter_ideas: [str]`` shape (each idea becomes a
    minimal ``{"heading": idea}`` beat) so in-flight and legacy TOCs still
    generate during the deploy window:

    - non-empty list ``sections`` -> returned as-is (each item coerced to a dict);
    - non-empty ``sections`` that is not a list -> ``MalformedSectionsError``;
    - elif legacy ``chapter_ideas`` -> one ``{"heading": idea}`` beat per idea;
    - else -> ``[]``.
    """
    sections = chapter.get("sections")
    if sections:
        if not isinstance(sections, list):
            raise MalformedSectionsError(
                f"'sections' must be a list of section beats, got {type(sections).__name__}"
            )
        return [s if isinstance(s, dict) else {"heading": str(s)} for s in sections]
    return [{"heading": str(idea)} for idea in (chapter.get("chapter_ideas") or [])]


def _coerce_word_target(value: object) -> int | None:
    """A beat's explicit word target as a positive int, or None if unusable.

    Accepts an int, an integral float, or a digit string. Anything else (a
    range like ``"500-700"``, 0, a negative, a bool) is ignored with a warning:
    the target is advisory, so one bad value must not fail a paid chapter.
    """
    if value is None or value == "":
        return None
    target: int | None = None
    if isinstance(value, bool):
        target = None
    elif isinstance(value, int):
        target = value
    elif isinstance(value, float) and value.is_integer():
        target = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        target = int(value.strip())
    if target is None or target <= 0:
        logger.warning("Ignoring unusable section word_target %r", value)
        return None
    return target


def compute_section_word_targets(
    sections: list[dict], words_per_chapter: int
) -> list[int]:
    """Per-section word-count targets aligned 1:1 with ``sections``.

    Honors a valid explicit ``word_target`` on a beat (see
    ``_coerce_word_target``); splits the remaining budget evenly across the
    other beats, never below ``MIN_SECTION_WORDS``. With no weighted beats this
    is an even ``words_per_chapter // len(sections)`` split.
    """
    if not sections:
        return []
    explicit = [
        _coerce_word_target(s.get("word_target")) if isinstance(s, dict) else None
        for s in sections
    ]
    weighted_total = sum(t for t in explicit if t)
    unweighted_count = sum(1 for t in explicit if not t)
    remainder = max(0, words_per_chapter - weighted_total)
    per_unweighted = (
        max(MIN_SECTION_WORDS, remainder // unweighted_count) if unweighted_count else 0
    )
    return [t if t else per_unweighted for t in explicit]
