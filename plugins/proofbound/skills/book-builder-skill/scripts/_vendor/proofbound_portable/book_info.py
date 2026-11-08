"""Book-info vocabulary: pure data shared by the server and the skills.

The server re-exports these from ``proofbound_shared.constants``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

# Structural book kinds (how a book is shaped; tone is ``writing_style``). The DB
# CHECK on book_projects.book_type enforces the same keys. The OpenAPI book_type enum
# (which generates the connector's render_book schema) is pinned to this tuple by
# cc-template-api's test_book_compose_spec_parity.py.
DEFAULT_BOOK_TYPE = "nonfiction_practical"
BOOK_TYPES: tuple[str, ...] = (
    "nonfiction_practical",
    "nonfiction_narrative",
    "nonfiction_reference",
    "memoir",
    "fiction",
    "childrens",
)

# Optional back-cover facts a caller may explicitly decline to supply
# (BookComposeRequest.declined_fields / cc-config.yml ``declined:``). book_type is
# deliberately NOT declinable. Mirrored in openapi/components/schemas/book-upload.yml.
DECLINABLE_BOOK_FIELDS: tuple[str, ...] = ("description", "book_blurb", "author_bio")

# Where a project keeps its back-cover blurb, in precedence order: its own
# ``blurb.md`` (book-builder, BYO contract), else the top-level ``blurb:`` key of the
# cover-generator's ``covers/cover-config.yml``.
BLURB_FILE = "blurb.md"
COVER_CONFIG_FILE = "covers/cover-config.yml"
COVER_CONFIG_BLURB_KEY = "blurb"


def find_blurb(
    project_path: Path, load_yaml: Callable[[Path], Mapping]
) -> tuple[str, str]:
    """The project's raw blurb text and the relative path it came from.

    Returns the first non-blank source in precedence order (``BLURB_FILE``, then
    ``COVER_CONFIG_BLURB_KEY`` in ``COVER_CONFIG_FILE``), or ``("", "")``. The text
    is raw; callers normalize it. ``load_yaml`` is injected (path -> mapping, ``{}``
    on any error) so this package stays stdlib-only. An unreadable ``blurb.md`` and
    a non-string ``blurb:`` both read as absent.
    """
    try:
        raw = (project_path / BLURB_FILE).read_bytes()
    except OSError:
        raw = b""
    text = raw.decode("utf-8", errors="replace")
    if text.strip():
        return text, BLURB_FILE
    config_path = project_path / COVER_CONFIG_FILE
    if config_path.is_file():
        value = load_yaml(config_path).get(COVER_CONFIG_BLURB_KEY)
        if isinstance(value, str) and value.strip():
            return value, COVER_CONFIG_FILE
    return "", ""
