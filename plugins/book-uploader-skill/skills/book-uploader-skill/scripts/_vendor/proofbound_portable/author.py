"""Author-metadata policy: bio length cap, photo limits and format sniffing, soft-break folding.

Shared by the server (``proofbound_shared.author_files``) and the book-uploader,
book-builder and cover-generator skills so one definition governs ``author.md``
and ``author.{jpg,png}`` everywhere.
"""

from __future__ import annotations

import re
from typing import Literal

MAX_AUTHOR_BIO_CHARS = 500  # Back cover author biography

MAX_PHOTO_BYTES = 5 * 1024 * 1024

PhotoExt = Literal["jpg", "png"]


def detect_image_format(magic: bytes) -> PhotoExt | None:
    """Return ``"jpg"`` for JPEG magic, ``"png"`` for PNG magic, else ``None``.

    Reads only the first 8 bytes of *magic*. Detection is by file content,
    not extension — a file named ``author.jpg`` containing PNG data is
    reported as ``"png"``.
    """
    if magic[:3] == b"\xff\xd8\xff":
        return "jpg"
    if magic[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def fold_soft_breaks(text: str) -> str:
    """Fold Markdown soft breaks: single newlines inside a paragraph become spaces.

    In Markdown a newline inside a paragraph is a soft break (rendered as a
    space); only a blank line starts a new paragraph. Consumers that render a bio
    treat every newline as a hard break (the cover renderer maps it to ``<br/>``),
    so a hand-wrapped ``author.md`` would print one line per source line.

    Applied by the callers that know their input is Markdown — never in the
    shared cover renderer, which also serves form-entered text where a hard break
    may well be intended. See :func:`normalize_bio` for that boundary.

    Blank-line paragraph separators are preserved, normalised to one blank line.
    """
    blocks = re.split(r"\n[ \t]*\n\s*", text)
    folded = []
    for block in blocks:
        joined = " ".join(line.strip() for line in block.split("\n"))
        joined = re.sub(r"[ \t]{2,}", " ", joined).strip()
        if joined:
            folded.append(joined)
    return "\n\n".join(folded)
