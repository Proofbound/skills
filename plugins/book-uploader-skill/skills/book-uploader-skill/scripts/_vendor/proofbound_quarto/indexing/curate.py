"""Turning extracted candidates into the vocabulary that gets indexed.

Two halves. :func:`statistical_fallback` takes the extractor's own ranking at
face value — no model, no network, always available. :func:`curate_vocabulary`
grounds a model's curation against the candidates it was given and returns the
fallback whenever that grounding does not produce a usable index.

**The grounding gate is the safety property of the whole feature.** Curation is
expressed in candidate IDs, not strings: the model returns ``cid``s, and every
surface form a ``\\index{}`` marker is ever inserted for comes from the
candidate the ``cid`` names. A model that invents a term therefore cannot cause
a marker for it — the invented ``cid`` does not resolve, and the entry is
dropped. ``display`` is the one string the model supplies, and it is
constrained too: it is what a reader *sees* printed in the index, so free text
there would let an invented label ride on grounded locators, which no reader
could detect.

Pure functions, no I/O. The network call lives in the orchestration step, which
treats every failure — HTTP, timeout, unparseable JSON — as ``None`` here.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import (
    BOOK_INDEX_MAX_ENTRIES,
    BOOK_INDEX_MIN_ENTRIES,
)
from .types import IndexCandidate, IndexEntry, IndexVocabulary

logger = logging.getLogger(__name__)


def statistical_fallback(
    candidates: Sequence[IndexCandidate],
    *,
    max_entries: int = BOOK_INDEX_MAX_ENTRIES,
) -> IndexVocabulary:
    """Build an uncurated vocabulary from the top-ranked *candidates*.

    One entry per candidate, in the extractor's deterministic ``(-score,
    term)`` order, truncated to *max_entries*. No synonym merging, no
    subentries and no cross-references — those are exactly the value curation
    adds, and inventing them here without a model would be guesswork.

    ``sort_key`` is deliberately left empty: :func:`~.markers.index_argument`
    derives the accent-folded key from ``key`` when it is, so storing a
    precomputed copy would just be a second place for it to drift.
    """
    entries = tuple(
        IndexEntry(
            key=candidate.display,
            surface_forms=candidate.surface_forms,
            source_cids=(candidate.cid,),
        )
        for candidate in candidates[:max_entries]
    )
    return IndexVocabulary(entries=entries, source="statistical")


def candidate_payload(
    candidates: Sequence[IndexCandidate],
) -> list[dict[str, Any]]:
    """Serialize *candidates* for the curation request.

    ``surface_forms`` is deliberately omitted. The literal spellings are what
    marker insertion matches on; shipping them would invite the model to echo
    one back as a term, which is the one thing the ``cid`` contract exists to
    make impossible.
    """
    return [
        {
            "cid": candidate.cid,
            "term": candidate.term,
            "display": candidate.display,
            "entity_type": candidate.entity_type,
            "count": candidate.count,
            "chapter_spread": candidate.chapter_spread,
            "score": candidate.score,
        }
        for candidate in candidates
    ]


def curate_vocabulary(
    candidates: Sequence[IndexCandidate],
    raw_entries: Sequence[Mapping[str, Any]] | None,
    *,
    max_entries: int = BOOK_INDEX_MAX_ENTRIES,
    min_entries: int = BOOK_INDEX_MIN_ENTRIES,
) -> IndexVocabulary:
    """Ground *raw_entries* against *candidates*, or fall back.

    *raw_entries* is the curation response exactly as the model returned it, or
    ``None`` when there was no curation to be had — an unavailable provider, a
    timeout, unparseable output, or a content source with no model behind it.

    The fallback triggers on **too few** grounded entries, not merely zero. A
    pass that yields three usable entries for a 200-page book has failed even
    though it technically succeeded, and the uncurated statistical vocabulary
    is a better index than a three-line one. ``min_entries`` is the same
    threshold the caller applies before writing anything, so "too few" means
    one thing everywhere.
    """
    if raw_entries is None:
        return statistical_fallback(candidates, max_entries=max_entries)

    grounded = ground_entries(raw_entries, candidates, max_entries=max_entries)
    if len(grounded) < min_entries:
        logger.warning(
            "book index curation grounded too few entries; falling back",
            extra={
                "grounded": len(grounded),
                "returned": len(raw_entries),
                "min_entries": min_entries,
            },
        )
        return statistical_fallback(candidates, max_entries=max_entries)

    return IndexVocabulary(entries=grounded, source="curated")


# ---------------------------------------------------------------------------
# The grounding gate
# ---------------------------------------------------------------------------


def _resolve_conflicts(parsed: Sequence[_Parsed], max_entries: int) -> list[_Parsed]:
    """Settle contested ``cid``s and apply the cap, back in the model's order.

    Conflict resolution runs in score order so the better entry keeps a
    contested cid; emission runs in the model's own order so the output is a
    deterministic function of the response.
    """
    claimed: set[str] = set()
    kept: list[_Parsed] = []
    for entry in sorted(parsed, key=lambda e: (-e.score, e.position)):
        if entry.claim(claimed):
            kept.append(entry)

    kept.sort(key=lambda e: (-e.score, e.position))
    del kept[max_entries:]
    kept.sort(key=lambda e: e.position)
    return kept


def _cross_references(
    entry: _Parsed, target_display: Mapping[str, str]
) -> tuple[str | None, tuple[str, ...]]:
    """Resolve one entry's ``see`` / ``see_also`` against the surviving set."""
    see = target_display.get(entry.see or "")
    if see == entry.display:
        see = None
    if entry.see and see is None:
        logger.info(
            "dropped a dangling see cross-reference",
            extra={"entry": entry.display, "target": entry.see},
        )

    see_also = tuple(
        dict.fromkeys(
            display
            for cid in entry.see_also
            if (display := target_display.get(cid)) and display != entry.display
        )
    )
    return see, see_also


def _emit(entry: _Parsed, target_display: Mapping[str, str]) -> list[IndexEntry]:
    """The head entry for *entry*, followed by its subentries."""
    see, see_also = _cross_references(entry, target_display)
    emitted = [
        IndexEntry(
            key=entry.display,
            surface_forms=entry.surface_forms,
            source_cids=tuple(entry.cids),
            # A ``see`` is a replacement reference: the entry carries no
            # locators, so its subentries have nowhere to hang. Dropping them
            # is the only coherent reading.
            see=see,
            see_also=() if see else see_also,
        )
    ]
    if see:
        return emitted

    emitted.extend(
        IndexEntry(
            key=entry.display,
            subentry=sub.display,
            surface_forms=sub.surface_forms,
            source_cids=tuple(sub.cids),
        )
        for sub in entry.subentries
    )
    return emitted


def ground_entries(
    raw_entries: Sequence[Mapping[str, Any]],
    candidates: Sequence[IndexCandidate],
    *,
    max_entries: int = BOOK_INDEX_MAX_ENTRIES,
) -> tuple[IndexEntry, ...]:
    """Resolve a curation response into entries that are safe to index.

    Nothing survives that cannot be traced back to a candidate in
    *candidates*:

    * an unknown ``cid`` is dropped, and an entry left with no known ``cid`` is
      dropped whole;
    * a ``cid`` claimed by two entries is a merge conflict — the higher-scoring
      entry keeps it;
    * ``display`` must match a referenced candidate's display form up to case,
      whitespace and ``Surname, Given`` inversion; anything else is replaced by
      the highest-scoring referenced candidate's own display form;
    * ``see`` / ``see_also`` are ``cid``s resolved in a **second pass** against
      the entries that actually survived, so a cross-reference can never dangle
      into an entry that was itself dropped.
    """
    by_cid = {candidate.cid: candidate for candidate in candidates}
    order = {candidate.cid: position for position, candidate in enumerate(candidates)}

    parsed = [
        _parse(raw, position, by_cid, order)
        for position, raw in enumerate(raw_entries)
        if isinstance(raw, Mapping)
    ]
    kept = _resolve_conflicts(
        [entry for entry in parsed if entry is not None], max_entries
    )

    # Second pass: cross-reference targets must name an entry that survived.
    target_display = {cid: entry.display for entry in kept for cid in entry.cids}

    return tuple(emitted for entry in kept for emitted in _emit(entry, target_display))


class _Parsed:
    """One curation entry, resolved to candidates but not yet claimed."""

    __slots__ = ("position", "resolved", "display", "see", "see_also", "subentries")

    def __init__(
        self,
        position: int,
        resolved: list[IndexCandidate],
        display: str,
        see: str | None,
        see_also: list[str],
        subentries: list[_Parsed],
    ) -> None:
        self.position = position
        self.resolved = resolved
        self.display = display
        self.see = see
        self.see_also = see_also
        self.subentries = subentries

    @property
    def score(self) -> float:
        """Best score among the referenced candidates — the merge's weight."""
        return max(candidate.score for candidate in self.resolved)

    @property
    def cids(self) -> list[str]:
        return [candidate.cid for candidate in self.resolved]

    @property
    def surface_forms(self) -> tuple[str, ...]:
        """Every referenced candidate's spellings, deduped, candidate order."""
        return tuple(
            dict.fromkeys(
                surface
                for candidate in self.resolved
                for surface in candidate.surface_forms
            )
        )

    def claim(self, claimed: set[str]) -> bool:
        """Take the ``cid``s not already spoken for. False if none are left."""
        surviving = [c for c in self.resolved if c.cid not in claimed]
        if len(surviving) != len(self.resolved):
            logger.info(
                "dropped duplicate candidate claims from an index entry",
                extra={
                    "entry": self.display,
                    "dropped": len(self.resolved) - len(surviving),
                },
            )
        if not surviving:
            return False
        self.resolved = surviving
        claimed.update(candidate.cid for candidate in surviving)

        self.subentries = [sub for sub in self.subentries if sub.claim(claimed)]
        return True


def _parse(
    raw: Mapping[str, Any],
    position: int,
    by_cid: Mapping[str, IndexCandidate],
    order: Mapping[str, int],
    *,
    allow_subentries: bool = True,
) -> _Parsed | None:
    """Resolve one raw entry, or ``None`` if nothing in it is groundable."""
    cids = raw.get("cids")
    if not isinstance(cids, list):
        return None

    seen: dict[str, IndexCandidate] = {}
    for cid in cids:
        candidate = by_cid.get(cid) if isinstance(cid, str) else None
        if candidate is not None:
            seen[candidate.cid] = candidate
    if not seen:
        logger.info("dropped an index entry with no resolvable candidates")
        return None

    resolved = sorted(seen.values(), key=lambda c: order[c.cid])
    display = _ground_display(raw.get("display"), resolved)

    subentries: list[_Parsed] = []
    if allow_subentries:
        raw_subs = raw.get("subentries")
        if isinstance(raw_subs, list):
            for sub_position, raw_sub in enumerate(raw_subs):
                if not isinstance(raw_sub, Mapping):
                    continue
                sub = _parse(
                    raw_sub, sub_position, by_cid, order, allow_subentries=False
                )
                if sub is not None:
                    subentries.append(sub)

    see = raw.get("see")
    raw_see_also = raw.get("see_also")
    see_also = (
        [cid for cid in raw_see_also if isinstance(cid, str)]
        if isinstance(raw_see_also, list)
        else []
    )

    return _Parsed(
        position=position,
        resolved=resolved,
        display=display,
        see=see if isinstance(see, str) else None,
        see_also=see_also,
        subentries=subentries,
    )


def _ground_display(display: Any, resolved: Sequence[IndexCandidate]) -> str:
    """Return a display string that is provably one of the candidates'.

    The model may restyle: change case, collapse whitespace, invert a personal
    name to ``Surname, Given`` (or back), or choose one of the candidate's own
    literal surface forms. It may not reword, expand, or
    annotate — those would put a string in front of the reader that the
    manuscript never contained, with grounded page numbers vouching for it.
    """
    best = max(resolved, key=lambda c: c.score)
    if not isinstance(display, str) or not display.strip():
        return best.display

    normalized = _normalize(display)
    for candidate in resolved:
        if normalized in _restylings(candidate.display):
            return display.strip()
        # A literal spelling from the prose is as grounded as the display:
        # the extractor drops a leading article and a possessive from displays,
        # and this is how a curator restores "The Hague" or "Macy's".
        if normalized in {_normalize(form) for form in candidate.surface_forms}:
            return display.strip()

    logger.info(
        "replaced an ungrounded index display form",
        extra={"proposed": display[:100], "used": best.display},
    )
    return best.display


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _restylings(display: str) -> frozenset[str]:
    """The normalized forms a candidate's display may legitimately take."""
    forms = {_normalize(display)}

    parts = display.split()
    if len(parts) >= 2:
        forms.add(_normalize(f"{parts[-1]}, {' '.join(parts[:-1])}"))

    head, comma, tail = display.partition(",")
    if comma and head.strip() and tail.strip():
        forms.add(_normalize(f"{tail.strip()} {head.strip()}"))

    return frozenset(forms)
