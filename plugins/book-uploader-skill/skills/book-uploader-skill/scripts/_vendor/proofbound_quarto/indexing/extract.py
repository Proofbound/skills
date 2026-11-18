"""Deterministic candidate extraction from chapter prose.

No AI, no network, no I/O beyond loading the pinned spaCy pipeline. Given the
chapters of a manuscript, produce a scored, deterministically ordered list of
:class:`IndexCandidate`s with stable ``cid``s.

**Determinism is a contract, not a nicety.** The same manuscript must yield
byte-identical candidates — including ``cid``s — across processes with
different hash seeds, because Phase 7's curation contract is expressed in
``cid``s and the golden tests compare rendered output. Every aggregation here
therefore sorts explicitly rather than trusting set or dict iteration order.

**Extraction is English-only in v1.** ``en_core_web_sm`` is an English
pipeline: its lemmatizer, tagger and NER produce plausible-looking noise on
anything else. Unicode-safe *marker insertion* does not make *extraction*
multilingual — the two are unrelated concerns, and it would be easy to mistake
one for the other. A non-English manuscript raises
:class:`IndexExtractionSkipped`; a mixed-language book with English prose is
indexed normally, since the gate is on the dominant language.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from .constants import (
    BOOK_INDEX_MAX_CANDIDATES,
    BOOK_INDEX_MAX_PARAGRAPH_COVERAGE,
    BOOK_INDEX_MAX_TERM_CHARS,
    BOOK_INDEX_MAX_TERM_WORDS,
    BOOK_INDEX_MIN_COUNT,
    BOOK_INDEX_MIN_LATIN_RATIO,
    BOOK_INDEX_MIN_STOPWORD_RATIO,
    BOOK_INDEX_MIN_TERM_CHARS,
    BOOK_INDEX_SCORE_PRECISION,
    BOOK_INDEX_SCORE_WEIGHT_ENTITY,
    BOOK_INDEX_SCORE_WEIGHT_SPREAD,
    BOOK_INDEX_SCORE_WEIGHT_TFIDF,
    BOOK_INDEX_SCORE_WEIGHT_YAKE,
    BOOK_INDEX_SPACY_MODEL,
)
from .markers import protected_ranges
from .types import IndexCandidate, IndexExtractionSkipped

logger = logging.getLogger(__name__)

#: Entity labels worth indexing. Deliberately excludes DATE, TIME, PERCENT,
#: MONEY, QUANTITY, ORDINAL and CARDINAL — a back-of-book index of numbers is
#: noise, not navigation.
INDEXABLE_ENTITY_LABELS: tuple[str, ...] = (
    "PERSON",
    "ORG",
    "GPE",
    "LOC",
    "NORP",
    "EVENT",
    "PRODUCT",
    "WORK_OF_ART",
    "LAW",
)

#: Ranked so a term seen as both a chunk and an entity gets one stable label.
_ENTITY_PRIORITY = {label: rank for rank, label in enumerate(INDEXABLE_ENTITY_LABELS)}

#: Suffixes that mark a personal name's tail as a suffix, not a surname, so
#: "Martin Luther King Jr." inverts to "King, Martin Luther Jr." rather than
#: "Jr., Martin Luther King".
_NAME_SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"})

#: A small closed-class sample, used only to decide whether the manuscript is
#: dominantly English. Not a stopword filter — spaCy handles that.
_ENGLISH_MARKERS = frozenset(
    """the of and to in a is that it for as with was on are this be by an or from
    at not have has but they their which we you all can will more been would
    when there if about into than them these its our who what""".split()
)

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

#: A trailing possessive ("Society's", "Society\u2019s"). The plural possessive
#: ``s'`` needs no rule here: :func:`_clean_surface` already trims the apostrophe.
_POSSESSIVE = re.compile(r"(?<=\w)['\u2019\u02bc]s$", re.IGNORECASE)
_POSSESSIVE_TOKENS = frozenset({"'s", "\u2019s", "\u02bcs"})

#: A leading English article. The whitespace is required, so "A. J. Smith",
#: "A-Team" and a bare "The" are untouched.
_LEADING_ARTICLE = re.compile(r"^(?:the|an?)\s+", re.IGNORECASE)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


@lru_cache(maxsize=1)
def _load_nlp() -> Any:
    """Load the pinned spaCy pipeline once per process.

    Deferred and cached: importing spaCy costs a few hundred milliseconds and
    loading the model costs more, and the vast majority of builds never enable
    the index. The model is a wheel dependency, so this touches no network.
    """
    import spacy

    return spacy.load(BOOK_INDEX_SPACY_MODEL)


@dataclass
class _Observations:
    """Everything the aggregation pass learned about one manuscript.

    Plain dicts and sets, read out only through explicit sorts — see the
    determinism contract in the module docstring.
    """

    #: term -> number of mentions across the whole manuscript
    counts: dict[str, int] = field(default_factory=dict)
    #: term -> chapter indices it was mentioned in
    chapters_seen: dict[str, set[int]] = field(default_factory=dict)
    #: term -> surface spelling -> count
    surfaces: dict[str, dict[str, int]] = field(default_factory=dict)
    #: term -> printed form -> count
    displays: dict[str, dict[str, int]] = field(default_factory=dict)
    #: term -> the highest-ranking entity type observed for it
    entity_types: dict[str, str] = field(default_factory=dict)
    #: term -> the (chapter, paragraph) pairs it appeared in
    paragraphs_seen: dict[str, set[tuple[int, int]]] = field(default_factory=dict)
    #: chapter index -> total mentions, the denominator of the tf term
    chapter_totals: dict[int, int] = field(default_factory=dict)
    total_paragraphs: int = 0

    def record(
        self,
        term: str,
        display: str,
        surface: str,
        entity_type: str | None,
        chapter_index: int,
        paragraph_index: int,
    ) -> None:
        """Fold one mention into every aggregate."""
        self.counts[term] = self.counts.get(term, 0) + 1
        self.chapter_totals[chapter_index] = (
            self.chapter_totals.get(chapter_index, 0) + 1
        )
        self.chapters_seen.setdefault(term, set()).add(chapter_index)
        self.paragraphs_seen.setdefault(term, set()).add(
            (chapter_index, paragraph_index)
        )
        forms = self.surfaces.setdefault(term, {})
        forms[surface] = forms.get(surface, 0) + 1
        printed = self.displays.setdefault(term, {})
        printed[display] = printed.get(display, 0) + 1
        if entity_type and _outranks(entity_type, self.entity_types.get(term)):
            self.entity_types[term] = entity_type

    def admitted(self) -> list[str]:
        """The sorted terms that survive the frequency and coverage filters."""
        return sorted(
            term
            for term in self.counts
            if _admissible(
                term,
                self.counts[term],
                len(self.paragraphs_seen[term]),
                self.total_paragraphs,
            )
        )


def _observe(prose: Sequence[str], nlp: Any) -> _Observations:
    """Run the pipeline over every paragraph, aggregating each mention."""
    observations = _Observations()
    for chapter_index, text in enumerate(prose):
        paragraphs = [p for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
        observations.total_paragraphs += len(paragraphs)
        for paragraph_index, paragraph in enumerate(paragraphs):
            doc = nlp(paragraph)
            for term, display, surface, entity_type in _mentions(doc):
                observations.record(
                    term, display, surface, entity_type, chapter_index, paragraph_index
                )
    return observations


class _Normalizers:
    """The corpus-wide constants each component score is normalized against.

    Computed once over the admitted set, because every component is relative:
    a term's YAKE score means nothing except against the range the rest of the
    manuscript occupies.
    """

    def __init__(
        self,
        admitted: Sequence[str],
        tfidf: dict[str, float],
        yake_scores: dict[str, float],
        n_chapters: int,
    ) -> None:
        self.tfidf = tfidf
        self.yake_scores = yake_scores
        self.n_chapters = n_chapters
        self.max_tfidf = max(tfidf.values()) if tfidf else 0.0
        observed = [
            value
            for value in (yake_scores.get(term) for term in admitted)
            if value is not None
        ]
        self.max_yake = max(observed) if observed else 0.0
        self.min_yake = min(observed) if observed else 0.0

    def score(self, term: str, spread: int, entity_type: str | None) -> float:
        """The blended, rounded score for one admitted term."""
        tfidf_n = (self.tfidf[term] / self.max_tfidf) if self.max_tfidf else 0.0

        # YAKE is lower-is-better, and its scores are bounded away from zero.
        # Min-max inversion over the OBSERVED range is what keeps the component
        # discriminating; ``1 - v/max`` would compress everything into a narrow
        # band near the top. The degenerate case is max == min (every candidate
        # scored identically), not max == 0.
        raw_yake = self.yake_scores.get(term, self.max_yake)
        yake_n = (
            ((self.max_yake - raw_yake) / (self.max_yake - self.min_yake))
            if self.max_yake > self.min_yake
            else 0.0
        )

        spread_n = (spread / self.n_chapters) if self.n_chapters else 0.0
        entity_n = 1.0 if entity_type else 0.0

        # Rounding is load-bearing: it collapses float jitter that would
        # otherwise reorder near-ties unpredictably between runs.
        return round(
            BOOK_INDEX_SCORE_WEIGHT_TFIDF * tfidf_n
            + BOOK_INDEX_SCORE_WEIGHT_YAKE * yake_n
            + BOOK_INDEX_SCORE_WEIGHT_SPREAD * spread_n
            + BOOK_INDEX_SCORE_WEIGHT_ENTITY * entity_n,
            BOOK_INDEX_SCORE_PRECISION,
        )


def _display_form(printed: dict[str, int]) -> str:
    """Most frequent spelling wins.

    Ties prefer the lower-cased form — index convention is a lowercase common
    noun — and then break lexicographically, so the display string cannot drift
    between runs.
    """
    return sorted(
        printed.items(),
        key=lambda item: (-item[1], item[0] != item[0].lower(), item[0]),
    )[0][0]


def extract_candidates(chapter_texts: Sequence[str]) -> list[IndexCandidate]:
    """Return scored, ordered index candidates for a manuscript.

    *chapter_texts* is the raw ``.qmd`` source of each chapter, in book order.
    Non-prose is stripped through :func:`protected_ranges` — the same choke
    point marker insertion uses, so the extractor can never propose a term that
    only exists in a place a marker may not go.

    Raises :class:`IndexExtractionSkipped` when there is nothing to index or
    the manuscript is not dominantly English.
    """
    prose = [_strip_non_prose(text) for text in chapter_texts]
    prose = [text for text in prose if text.strip()]
    if not prose:
        raise IndexExtractionSkipped("no_prose", "no indexable prose in the manuscript")

    corpus = "\n\n".join(prose)
    _require_english(corpus)

    n_chapters = len(prose)
    observations = _observe(prose, _load_nlp())

    admitted = observations.admitted()
    if not admitted:
        raise IndexExtractionSkipped("no_candidates", "no term survived the filters")

    normalizers = _Normalizers(
        admitted,
        _tfidf(
            admitted,
            observations.chapters_seen,
            observations.counts,
            observations.chapter_totals,
            n_chapters,
        ),
        _yake_scores(corpus),
        n_chapters,
    )

    scored: list[IndexCandidate] = []
    for term in admitted:
        entity_type = observations.entity_types.get(term)
        spread = len(observations.chapters_seen[term])
        scored.append(
            IndexCandidate(
                cid="",
                term=term,
                display=_display_form(observations.displays[term]),
                surface_forms=tuple(sorted(observations.surfaces[term])),
                entity_type=entity_type,
                count=observations.counts[term],
                chapter_spread=spread,
                score=normalizers.score(term, spread, entity_type),
            )
        )

    # Ties break on ``term``, making the sort total and ``cid`` assignment
    # stable. The cap is applied AFTER the sort, so which candidates survive it
    # is reproducible.
    scored.sort(key=lambda candidate: (-candidate.score, candidate.term))
    return [
        replace(candidate, cid=f"c{position:04d}")
        for position, candidate in enumerate(scored[:BOOK_INDEX_MAX_CANDIDATES])
    ]


# ---------------------------------------------------------------------------
# Prose extraction
# ---------------------------------------------------------------------------


def _strip_non_prose(qmd: str) -> str:
    """Blank out every protected range, preserving offsets-irrelevant structure.

    Protected spans are replaced with a space rather than deleted so words on
    either side of a stripped code span do not fuse into a phantom compound.
    Newlines inside a stripped span are preserved so paragraph boundaries — and
    therefore the coverage filter — stay intact.
    """
    pieces: list[str] = []
    cursor = 0
    for start, end in protected_ranges(qmd):
        pieces.append(qmd[cursor:start])
        pieces.append("".join(ch if ch == "\n" else " " for ch in qmd[start:end]))
        cursor = end
    pieces.append(qmd[cursor:])
    return "".join(pieces)


#: ``(term, display, surface, entity_type)`` for one mention.
#:
#: *display* is how the entry is printed; *surface* is the literal spelling as
#: it appears in the prose. They diverge for inverted personal names, and
#: conflating them is not cosmetic — marker insertion matches on surfaces, so a
#: ``Knight, Rob`` surface would never match anything in the manuscript and the
#: entry would silently collect no locators.
_Mention = tuple[str, str, str, str | None]


def _entity_mentions(doc: Any) -> tuple[list[_Mention], set[int]]:
    """Recognised entities, plus the token offsets they claim."""
    found: list[_Mention] = []
    claimed: set[int] = set()

    for entity in doc.ents:
        if entity.label_ not in _ENTITY_PRIORITY:
            continue
        surface = _clean_surface(entity.text)
        if not surface:
            continue
        if entity.label_ == "PERSON":
            display = _invert_person_name(_headword(surface, article=False))
        elif entity.label_ == "WORK_OF_ART":
            # A title keeps its article ("The Great Gatsby").
            display = _headword(surface, article=False)
        else:
            display = _headword(surface)
        if not display:
            continue
        found.append((_canonical(display), display, surface, entity.label_))
        claimed.update(range(entity.start, entity.end))

    return found, claimed


def _chunk_tokens(chunk: Any) -> list[Any]:
    """The content tokens of a noun chunk, or ``[]`` if it has none.

    Leading stopwords ("the gut microbiome" -> "gut microbiome") are dropped,
    but an internal one is kept: "war of the roses".
    """
    tokens = [
        token
        for token in chunk
        if token.pos_ not in ("DET", "PRON", "PUNCT") and not token.is_space
    ]
    while tokens and tokens[0].is_stop:
        tokens = tokens[1:]
    if tokens and tokens[-1].text.casefold() in _POSSESSIVE_TOKENS:
        tokens = tokens[:-1]
    return tokens


def _noun_chunk_mentions(doc: Any, claimed: set[int]) -> list[_Mention]:
    """Noun chunks that no entity already claimed."""
    found: list[_Mention] = []

    for chunk in doc.noun_chunks:
        tokens = _chunk_tokens(chunk)
        if not tokens or len(tokens) > BOOK_INDEX_MAX_TERM_WORDS:
            continue
        if any(token.i in claimed for token in tokens):
            continue
        if all(token.is_stop or token.like_num for token in tokens):
            continue
        display = _clean_surface(doc[tokens[0].i : tokens[-1].i + 1].text)
        if not display:
            continue
        # Lemmatize so plural and singular mentions merge into one entry.
        term = _canonical(" ".join(token.lemma_.lower() for token in tokens).strip())
        if not term:
            continue
        found.append((term, display, display, None))

    return found


def _mentions(doc: Any) -> list[_Mention]:
    """Every mention in one paragraph's doc, entities first.

    Entities are collected first and their token spans claimed, so a noun chunk
    overlapping a recognised entity does not double-count it as a second,
    lower-quality candidate.
    """
    entities, claimed = _entity_mentions(doc)
    return entities + _noun_chunk_mentions(doc, claimed)


def _clean_surface(text: str) -> str:
    """Normalize whitespace and strip surrounding punctuation from a mention."""
    collapsed = " ".join(text.split())
    return collapsed.strip("\"'“”‘’.,;:()[]{}").strip()


def _headword(text: str, *, article: bool = True) -> str:
    """The form an index entry sorts and prints under.

    Drops a trailing possessive always, and a leading English article unless
    *article* is False (personal names and titles keep theirs). Display and merge
    key only: the literal prose spelling stays in the surface forms, which is
    what marker insertion matches. Returns *text* unchanged if stripping would
    leave nothing.
    """
    headword = _POSSESSIVE.sub("", text.strip())
    if article:
        headword = _LEADING_ARTICLE.sub("", headword)
    headword = _clean_surface(headword)
    return headword or text


def _canonical(text: str) -> str:
    """NFC-normalized, case-folded merge key.

    NFC matters because composed and decomposed spellings of the same accented
    word are different strings but the same term — the repo already handles
    non-Latin manuscripts, so this is not hypothetical.
    """
    return unicodedata.normalize("NFC", " ".join(text.split())).casefold()


def _invert_person_name(name: str) -> str:
    """``John Smith`` -> ``Smith, John``; single-token names are left alone."""
    parts = name.split()
    if len(parts) < 2:
        return name
    suffix = ""
    if parts[-1].casefold() in _NAME_SUFFIXES and len(parts) > 2:
        suffix = f" {parts[-1]}"
        parts = parts[:-1]
    if len(parts) < 2:
        return f"{parts[0]}{suffix}".strip()
    return f"{parts[-1]}, {' '.join(parts[:-1])}{suffix}"


def _outranks(candidate: str, current: str | None) -> bool:
    if current is None:
        return True
    return _ENTITY_PRIORITY[candidate] < _ENTITY_PRIORITY[current]


# ---------------------------------------------------------------------------
# Filters and scoring
# ---------------------------------------------------------------------------


def _admissible(term: str, count: int, paragraphs: int, total_paragraphs: int) -> bool:
    if count < BOOK_INDEX_MIN_COUNT:
        return False
    if not BOOK_INDEX_MIN_TERM_CHARS <= len(term) <= BOOK_INDEX_MAX_TERM_CHARS:
        return False
    if len(term.split()) > BOOK_INDEX_MAX_TERM_WORDS:
        return False
    if not _WORD.search(term):
        # Purely numeric or punctuation: a locator for "2019" navigates nothing.
        return False
    if (
        total_paragraphs
        and paragraphs / total_paragraphs > BOOK_INDEX_MAX_PARAGRAPH_COVERAGE
    ):
        # The book's own subject, not an index entry.
        return False
    return True


def _tfidf(
    terms: Sequence[str],
    chapters_seen: dict[str, set[int]],
    counts: dict[str, int],
    chapter_totals: dict[int, int],
    n_chapters: int,
) -> dict[str, float]:
    """Hand-rolled cross-chapter TF-IDF, chapters as documents.

    ``idf`` uses ``log(1 + n/df)`` so a term present in every chapter still
    scores above zero — a book-wide term of art is a weaker entry than a
    chapter-specific one, not a worthless one.
    """
    scores: dict[str, float] = {}
    largest_chapter = max(chapter_totals.values()) if chapter_totals else 1
    for term in terms:
        document_frequency = len(chapters_seen[term]) or 1
        idf = math.log(1 + n_chapters / document_frequency)
        term_frequency = counts[term] / largest_chapter
        scores[term] = term_frequency * idf
    return scores


def _yake_scores(corpus: str) -> dict[str, float]:
    """Return YAKE keyword scores keyed by canonical term (lower is better).

    A YAKE failure degrades the ranking rather than the build: the component
    normalizes to zero for every candidate and the other three still order the
    index.
    """
    try:
        import yake

        extractor = yake.KeywordExtractor(
            lan="en",
            n=BOOK_INDEX_MAX_TERM_WORDS,
            dedupLim=0.9,
            top=BOOK_INDEX_MAX_CANDIDATES * 4,
            features=None,
        )
        return {
            _canonical(keyword): score
            for keyword, score in extractor.extract_keywords(corpus)
        }
    except Exception:  # noqa: BLE001 — ranking quality, never a build failure
        logger.warning("YAKE scoring failed; ranking on TF-IDF alone", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# The English-only gate
# ---------------------------------------------------------------------------


def _require_english(corpus: str) -> None:
    """Raise unless the manuscript's dominant language is English.

    Two cheap signals rather than a language-ID dependency: how much of the
    alphabetic content is Latin script, and how much of the token stream is
    English closed-class vocabulary. Together they separate English from both
    non-Latin scripts and Latin-script European languages, and neither can be
    fooled by an English book that quotes Greek or Japanese.
    """
    letters = [ch for ch in corpus if ch.isalpha()]
    if not letters:
        raise IndexExtractionSkipped("no_prose", "manuscript contains no words")

    latin = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    latin_ratio = latin / len(letters)

    tokens = [token.casefold() for token in _WORD.findall(corpus)]
    marker_ratio = (
        sum(1 for token in tokens if token in _ENGLISH_MARKERS) / len(tokens)
        if tokens
        else 0.0
    )

    if (
        latin_ratio < BOOK_INDEX_MIN_LATIN_RATIO
        or marker_ratio < BOOK_INDEX_MIN_STOPWORD_RATIO
    ):
        raise IndexExtractionSkipped(
            "unsupported_language",
            f"latin_ratio={latin_ratio:.2f} english_marker_ratio={marker_ratio:.2f}",
        )
