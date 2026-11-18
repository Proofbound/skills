"""Tunable constants for the back-of-book index engine (epic ``proofbound_monorepo-ichb``).

These live beside the engine rather than in ``proofbound_shared.constants``
because the engine ships to customers' machines inside the distributable
``book-index`` skill, where ``proofbound_shared`` is not installed. A caller
that wants one of these values imports it from here; ``ai-clients`` does
exactly that for :data:`BOOK_INDEX_MAX_ENTRIES`.

Every value is tunable without a code change, which is why the scoring weights
live beside the caps rather than inside the extractor.
"""

from __future__ import annotations

#: Hard cap on candidates carried out of extraction, applied after the
#: deterministic ``(-score, term)`` sort so which candidates survive is
#: reproducible.
BOOK_INDEX_MAX_CANDIDATES = 300

#: Hard cap on entries in the final vocabulary.
BOOK_INDEX_MAX_ENTRIES = 250

#: Below this many entries the index is skipped entirely — a twelve-entry index
#: looks broken next to a professionally typeset book.
BOOK_INDEX_MIN_ENTRIES = 20

#: A term appearing in more than this fraction of paragraphs is a topic of the
#: book, not an index entry ("microbiome" in a microbiome book).
BOOK_INDEX_MAX_PARAGRAPH_COVERAGE = 0.6

#: Candidate admissibility filters.
BOOK_INDEX_MIN_COUNT = 2
BOOK_INDEX_MIN_TERM_CHARS = 3
BOOK_INDEX_MAX_TERM_CHARS = 60
BOOK_INDEX_MAX_TERM_WORDS = 4

#: At most this many page locators for any ONE entry within ONE chapter, so a
#: term of art discussed throughout a chapter does not produce a locator run
#: like ``foo, 12, 13, 14, 15, 16``. This is per entry, NOT per chapter.
BOOK_INDEX_MAX_LOCATORS_PER_ENTRY_PER_CHAPTER = 3

#: Separate TOTAL safety cap on markers in one chapter, purely to bound
#: pathological output. It should never bind on a normal book; log at WARNING
#: when it does.
BOOK_INDEX_MAX_MARKERS_PER_CHAPTER = 400

#: Scoring weights. Each component is normalized to [0, 1] over the surviving
#: candidate set before weighting — YAKE is lower-is-better and is min-max
#: INVERTED first, so summing these never ranks the worst keywords highest.
BOOK_INDEX_SCORE_WEIGHT_TFIDF = 0.45
BOOK_INDEX_SCORE_WEIGHT_YAKE = 0.25
BOOK_INDEX_SCORE_WEIGHT_SPREAD = 0.20
BOOK_INDEX_SCORE_WEIGHT_ENTITY = 0.10

#: Scores are rounded to this many places before sorting. Load-bearing: it
#: collapses float jitter that would otherwise reorder near-ties between runs.
BOOK_INDEX_SCORE_PRECISION = 6

#: spaCy pipeline. Pinned exactly in pyproject — a model bump changes lemmas and
#: NER spans, so it is a golden-test-invalidating dependency and must not float.
BOOK_INDEX_SPACY_MODEL = "en_core_web_sm"

#: Extraction is English-only in v1: ``en_core_web_sm`` is an English pipeline
#: and produces plausible-looking noise on anything else. The gate is on the
#: DOMINANT language — a mixed-language book (English prose quoting Greek or
#: CJK) is still indexed.
BOOK_INDEX_MIN_LATIN_RATIO = 0.6
BOOK_INDEX_MIN_STOPWORD_RATIO = 0.05
