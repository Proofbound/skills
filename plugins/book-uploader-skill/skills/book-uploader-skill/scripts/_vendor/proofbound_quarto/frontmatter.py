"""Text-preserving frontmatter split/assemble for Quarto ``.qmd`` files.

The write path never parses YAML — it concatenates the verbatim header string
with the body — so the frontmatter block round-trips byte-for-byte, preserving
comments, quoting, and key order.

``parse_header_title`` is the only function that parses YAML, and it is used
only to extract a read-only ``chapter_title`` convenience field for API
responses. Its output never touches the stored file.
"""

from __future__ import annotations

import yaml

_FENCE = "---"


def split_frontmatter(qmd_text: str) -> tuple[str, str]:
    """Split ``qmd_text`` into ``(header_raw, body)``.

    ``header_raw`` is the verbatim frontmatter block including both ``---``
    fence lines plus the trailing newline that follows the closing fence (if
    present). If the file has no frontmatter, ``header_raw`` is ``""`` and
    ``body`` is the original text.
    """
    if not qmd_text.startswith(_FENCE):
        return "", qmd_text

    # The opening fence line must be exactly "---" followed by a newline (or
    # the end of file, which is unusual but handled). If the leading "---"
    # is followed by other characters on the same line, this is not a
    # frontmatter block.
    first_newline = qmd_text.find("\n")
    if first_newline == -1:
        return "", qmd_text
    if qmd_text[:first_newline].rstrip("\r") != _FENCE:
        return "", qmd_text

    # Find the closing "---" fence on a line of its own.
    search_from = first_newline + 1
    while search_from < len(qmd_text):
        next_newline = qmd_text.find("\n", search_from)
        line_end = next_newline if next_newline != -1 else len(qmd_text)
        line = qmd_text[search_from:line_end].rstrip("\r")
        if line == _FENCE:
            close_end = line_end + 1 if next_newline != -1 else line_end
            return qmd_text[:close_end], qmd_text[close_end:]
        if next_newline == -1:
            break
        search_from = next_newline + 1

    # No closing fence found — treat the whole file as body.
    return "", qmd_text


def assemble_qmd(header_raw: str, body: str) -> str:
    """Concatenate ``header_raw`` and ``body`` without reformatting either.

    Guarantees:
    - If ``header_raw`` is empty, the result is ``body`` with a trailing
      newline appended if it does not already end in one.
    - Otherwise, the result is ``header_raw`` followed by ``body`` with a
      single newline between them (added only if ``header_raw`` does not
      already end in a newline) and a trailing newline on ``body``.

    No YAML parsing is performed. ``header_raw`` is preserved byte-for-byte.
    """
    if body and not body.endswith("\n"):
        body = body + "\n"
    elif not body:
        body = ""

    if not header_raw:
        return body

    if header_raw.endswith("\n"):
        return header_raw + body
    return header_raw + "\n" + body


def parse_header_title(header_raw: str) -> str:
    """Best-effort extract the ``title`` field from ``header_raw``.

    Returns ``""`` if the header is empty, missing the fence, missing a
    ``title`` key, or malformed. Never raises.
    """
    if not header_raw:
        return ""
    inner = _strip_fences(header_raw)
    if inner is None:
        return ""
    try:
        parsed = yaml.safe_load(inner)
    except yaml.YAMLError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    title = parsed.get("title", "")
    return title if isinstance(title, str) else ""


def _strip_fences(header_raw: str) -> str | None:
    """Remove the opening and closing ``---`` fences, returning the inner
    YAML text, or ``None`` if the fences are not present as expected."""
    lines = header_raw.splitlines()
    if len(lines) < 2:
        return None
    if lines[0].rstrip("\r") != _FENCE:
        return None
    # Find closing fence from the end (frontmatter is small; this is cheap).
    for i in range(len(lines) - 1, 0, -1):
        if lines[i].rstrip("\r") == _FENCE:
            return "\n".join(lines[1:i])
    return None
