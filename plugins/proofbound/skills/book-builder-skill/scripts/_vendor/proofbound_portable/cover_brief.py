"""Client-side validation of cover briefs (stdlib only).

Mirrors ``proofbound_shared.cover_brief.CoverBrief`` so a skill can fail fast on a
bad brief file before spending a request. The server stays authoritative: it also
checks ``style_key`` against the cover-style catalog, which is deliberately NOT
duplicated here (a shared test pins the rest of this module to the model).
"""

from __future__ import annotations

from typing import Any

MAX_COVER_CONCEPTS = 8

APPROACHES = (
    "literal_scene",
    "symbolic_object",
    "typographic",
    "abstract",
    "map_or_diagram",
    "pattern",
    "period_pastiche",
    "portrait_or_figure",
)

#: Free-text brief fields and their max length (min length is 1 after strip).
TEXT_LIMITS = {
    "concept": 300,
    "scene": 1200,
    "composition": 600,
    "palette": 300,
    "typography": 300,
    "rationale": 500,
}

#: Every required field, in schema order.
REQUIRED_FIELDS = ("approach", *TEXT_LIMITS, "style_key")


def _validate_brief(index: int, brief: Any) -> list[str]:
    label = f"brief {index}"
    if not isinstance(brief, dict):
        return [f"{label}: must be a JSON object, got {type(brief).__name__}"]
    errors: list[str] = []
    for name in sorted(set(brief) - set(REQUIRED_FIELDS)):
        errors.append(f"{label}: unknown field {name!r}")
    for name in REQUIRED_FIELDS:
        if name not in brief:
            errors.append(f"{label}: field {name!r} is required")
            continue
        value = brief[name]
        if not isinstance(value, str):
            errors.append(f"{label}: field {name!r} must be a string")
            continue
        text = value.strip()
        if name == "approach":
            if text not in APPROACHES:
                errors.append(
                    f"{label}: approach {value!r} must be one of {', '.join(APPROACHES)}"
                )
        elif not text:
            errors.append(f"{label}: field {name!r} must not be empty")
        elif name in TEXT_LIMITS and len(text) > TEXT_LIMITS[name]:
            errors.append(
                f"{label}: field {name!r} is {len(text)} characters (max {TEXT_LIMITS[name]})"
            )
    return errors


def validate_briefs(raw: Any) -> list[str]:
    """Return human-readable problems with *raw* (empty list means valid)."""
    if not isinstance(raw, list):
        return [f"briefs must be a JSON list, got {type(raw).__name__}"]
    if not 1 <= len(raw) <= MAX_COVER_CONCEPTS:
        return [f"expected 1 to {MAX_COVER_CONCEPTS} briefs, got {len(raw)}"]
    errors: list[str] = []
    for index, brief in enumerate(raw):
        errors.extend(_validate_brief(index, brief))
    return errors
