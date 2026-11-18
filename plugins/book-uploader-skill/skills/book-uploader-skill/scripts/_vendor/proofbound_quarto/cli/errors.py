"""The CLI's one error type.

``book_index`` re-exports it, so
``from proofbound_quarto.cli.book_index import CliError`` keeps working.
"""

from __future__ import annotations


class CliError(Exception):
    """A condition worth a clean message and a non-zero exit, not a traceback."""
