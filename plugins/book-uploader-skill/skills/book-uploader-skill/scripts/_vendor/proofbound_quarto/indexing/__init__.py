"""Back-of-book index engine (epic ``proofbound_monorepo-ichb``).

Pure functions only — no AI, no orchestration, no I/O beyond loading the spaCy
model. Extraction turns chapter prose into scored :class:`IndexCandidate`s;
:mod:`markers` turns a curated :class:`IndexVocabulary` back into inline
``\\index{...}{=latex}`` markers and the generated index page.

Page numbers come from LaTeX, so they are always correct; nothing here computes
or guesses a locator.
"""

from .curate import (
    candidate_payload,
    curate_vocabulary,
    ground_entries,
    statistical_fallback,
)
from .extract import extract_candidates
from .markers import (
    append_cross_references,
    build_index_page,
    escape_index_key,
    insert_markers,
    make_sort_key,
    protected_ranges,
)
from .types import (
    IndexCandidate,
    IndexEntry,
    IndexExtractionSkipped,
    IndexVocabulary,
)

__all__ = [
    "IndexCandidate",
    "IndexEntry",
    "IndexExtractionSkipped",
    "IndexVocabulary",
    "append_cross_references",
    "build_index_page",
    "candidate_payload",
    "curate_vocabulary",
    "extract_candidates",
    "ground_entries",
    "escape_index_key",
    "insert_markers",
    "make_sort_key",
    "protected_ranges",
    "statistical_fallback",
]
