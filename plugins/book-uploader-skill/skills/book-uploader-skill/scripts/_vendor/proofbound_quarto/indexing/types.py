"""Value types for the back-of-book index engine.

Everything here is frozen. The engine's contract is that a given manuscript
yields byte-identical candidates on every run, and mutable state shared between
extraction, curation and marker insertion is the easiest way to lose that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: Vocabulary provenance. ``curated`` means an LLM pass shaped the entries
#: (Phase 7); ``statistical`` means the extractor's own ranking was used
#: directly, which is also the fallback whenever curation fails.
VocabularySource = Literal["curated", "statistical"]


class IndexExtractionSkipped(Exception):
    """Raised when a manuscript cannot be indexed at all.

    Carries a machine-readable *reason* so the orchestration step can record a
    distinguishable ``index_skipped`` outcome rather than a generic failure.
    An index is an opt-in enhancement: skipping is always preferable to
    emitting a plausible-looking wrong index.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class IndexCandidate:
    """One term the extractor considers worth indexing.

    ``cid`` is a **stable candidate ID** (``c0000``, ``c0001``, …) assigned
    after the deterministic sort, so it is reproducible for a given manuscript.
    The Phase 7 curation contract is expressed in ``cid``s rather than strings:
    string round-tripping cannot distinguish a legitimate synonym merge from a
    hallucinated variant, and it breaks on whitespace, punctuation or accent
    drift in the model's echo of a term.
    """

    cid: str
    #: Canonical lower-cased lemma form — the merge key, not a display string.
    term: str
    #: How the term is printed in the index.
    display: str
    #: Every literal spelling seen in the prose. Marker insertion matches only
    #: against these, never against a curator-supplied string.
    surface_forms: tuple[str, ...]
    #: spaCy entity label (``PERSON``, ``ORG``, …) or ``None`` for a noun chunk.
    entity_type: str | None
    #: Total occurrences across the manuscript.
    count: int
    #: Number of distinct chapters the term appears in.
    chapter_spread: int
    #: Weighted, normalized, rounded composite score. See ``extract.py``.
    score: float


@dataclass(frozen=True)
class IndexEntry:
    """One line of the finished index.

    A subentry is modelled as a separate entry carrying its parent in ``key``,
    matching makeindex's own ``main!sub`` flattening — it keeps marker
    insertion uniform, since a subentry needs its own locators.
    """

    key: str
    subentry: str | None = None
    #: makeindex sort key for ``key`` (accent-folded). Empty means "sort as
    #: printed", the common case for plain ASCII entries.
    sort_key: str = ""
    #: Literal spellings to match in the prose, longest-first at insertion time.
    surface_forms: tuple[str, ...] = ()
    #: Which candidates this entry merged, for traceability through curation.
    source_cids: tuple[str, ...] = ()
    #: ``see`` target — a *replacement* cross-reference, so an entry with a
    #: ``see`` gets no locators of its own.
    see: str | None = None
    #: ``see also`` targets — supplementary, so locators are still placed.
    see_also: tuple[str, ...] = ()

    @property
    def has_locators(self) -> bool:
        """False for a pure ``see`` entry, which must never place markers."""
        return self.see is None


@dataclass(frozen=True)
class IndexVocabulary:
    """The finished set of entries plus where they came from."""

    entries: tuple[IndexEntry, ...] = field(default_factory=tuple)
    source: VocabularySource = "statistical"
