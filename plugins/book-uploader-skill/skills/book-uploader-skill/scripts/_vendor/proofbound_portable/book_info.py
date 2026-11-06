"""Book-info vocabulary: pure data shared by the server and the skills.

The server re-exports these from ``proofbound_shared.constants``.
"""

from __future__ import annotations

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
