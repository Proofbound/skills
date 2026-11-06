"""Margin check: where the ink on each rendered page actually sits.

KDP rejects an interior page by page from the ink, not from the configured
geometry: a table wider than the text block crosses into the gutter even when
``inner=`` is right (Somalis in America, pages 298 and 314, 2026-10-31). This
module measures each page's ink bounding box with pdfplumber (MIT; pymupdf is
AGPL and must not run server-side) and classifies it with
``proofbound_portable.interior_margins.classify_page_insets``.

Ink is the union of non-whitespace characters, painted rects, lines and curves
(stroke width included; white-only fills and unpainted paths excluded) and
placed images. Annotations (links) are not ink. Pages with no ink are skipped.

Shared by the render pipeline (cc-template-api) and the KDP publish skill's
local mode. Stdlib-importable: ``pdfplumber`` is imported inside
:func:`measure_margins`, so only callers that measure need it installed.

Advisory only: the result is persisted beside ``render_warnings`` and never
fails a render. A measurement that cannot finish (unreadable PDF, over the time
budget) reports ``None`` — "not measured" — rather than a partial answer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from proofbound_portable.interior_margins import (
    OUTER_IN,
    SEVERITY_COSMETIC,
    SEVERITY_KDP,
    MarginViolation,
    classify_page_insets,
    kdp_floors_in,
)

logger = logging.getLogger(__name__)

# Measured cost is ~23 ms/page on an M4 (596 pages in 13.5 s); KDP's 828-page
# ceiling on a slower droplet core stays well inside this.
TIME_BUDGET_S = 120.0
# Cosmetic entries kept in the stored report; KDP entries are never dropped.
MAX_COSMETIC_ENTRIES = 200
# Pages named per warning line before "and N more".
MAX_PAGES_IN_WARNING = 10
REPORT_VERSION = 1

_PT_PER_IN = 72.0
_PATH_OBJECTS = ("rects", "lines", "curves")
_EDGES = ("inside", "outside", "top", "bottom")

COULD_NOT_MEASURE = (
    "Margin check could not measure the PDF; check the margins before "
    "submitting to KDP."
)


class MarginCheckTimeout(RuntimeError):
    """The measurement ran past :data:`TIME_BUDGET_S`."""


@dataclass(frozen=True)
class MarginReport:
    """Every page's measured margins against KDP's floors and the planned block."""

    page_count: int
    page_size_in: tuple[float, float]
    planned_inside_in: float | None
    planned_outside_in: float | None
    violations: tuple[MarginViolation, ...]

    @property
    def kdp_violations(self) -> tuple[MarginViolation, ...]:
        return tuple(v for v in self.violations if v.severity == SEVERITY_KDP)

    @property
    def cosmetic_violations(self) -> tuple[MarginViolation, ...]:
        return tuple(v for v in self.violations if v.severity == SEVERITY_COSMETIC)

    def as_row(self) -> dict:
        """Value for ``book_projects.margin_violations`` (see docs/DATABASE.md)."""
        cosmetic = self.cosmetic_violations
        kept = self.kdp_violations + cosmetic[:MAX_COSMETIC_ENTRIES]
        planned = (
            {"inside": self.planned_inside_in, "outside": self.planned_outside_in}
            if self.planned_inside_in is not None
            else None
        )
        return {
            "version": REPORT_VERSION,
            "page_count": self.page_count,
            "page_size_in": [round(x, 3) for x in self.page_size_in],
            "floors_in": kdp_floors_in(self.page_count),
            "planned_in": planned,
            "violations": [
                v.as_dict() for v in sorted(kept, key=lambda v: (v.page, v.edge))
            ],
            "truncated": len(cosmetic) > MAX_COSMETIC_ENTRIES,
        }

    def warnings(self) -> tuple[str, ...]:
        """Human-readable notes for ``render_warnings``: KDP lines first."""
        notes: list[str] = []
        for edge in _EDGES:
            hits = [v for v in self.kdp_violations if v.edge == edge]
            if hits:
                pages = _page_list(
                    hits, lambda v: f"{v.page} ({v.limit_in - v.inset_in:.2f}in over)"
                )
                notes.append(
                    f"Content crosses KDP's {hits[0].limit_in:g}in "
                    f"{edge}-margin minimum on pages {pages}; KDP will "
                    "reject this interior. Fix the overflowing table or figure "
                    "and re-render."
                )
        for edge in _EDGES:
            hits = [v for v in self.cosmetic_violations if v.edge == edge]
            if hits:
                pages = _page_list(
                    hits, lambda v: f"{v.page} ({v.limit_in - v.inset_in:.2f}in)"
                )
                notes.append(
                    "Content extends past the planned text block into the "
                    f"{edge} margin on pages {pages}; KDP will accept "
                    "it, but check the layout."
                )
        return tuple(notes)


def _page_list(hits: list[MarginViolation], fmt) -> str:
    shown = ", ".join(fmt(v) for v in hits[:MAX_PAGES_IN_WARNING])
    extra = len(hits) - MAX_PAGES_IN_WARNING
    return f"{shown} and {extra} more" if extra > 0 else shown


def _is_white(color) -> bool:
    """Gray 1, RGB (1, 1, 1) or CMYK (0, 0, 0, 0): paint that leaves no ink."""
    if isinstance(color, (int, float)):
        return float(color) == 1.0
    if isinstance(color, (list, tuple)):
        values = [float(v) for v in color if isinstance(v, (int, float))]
        if len(values) != len(color):
            return False  # pattern / named colour: assume it paints
        if len(values) in (1, 3):
            return all(v == 1.0 for v in values)
        if len(values) == 4:
            return all(v == 0.0 for v in values)
    return False


def _path_box(o) -> tuple[float, float, float, float] | None:
    """Painted extent of a path object, or ``None`` when it leaves no ink.

    pdfplumber's box is the path geometry; a stroke paints half its line width
    beyond it on every side. White paint (a page or cell background, a white
    outline) and an unpainted path are not ink.
    """
    stroked = bool(o.get("stroke")) and not _is_white(o.get("stroking_color"))
    filled = bool(o.get("fill")) and not _is_white(o.get("non_stroking_color"))
    if not (stroked or filled):
        return None
    # pdfminer reports 0 for a width the PDF never set (the spec default is
    # 1pt); half a point is below every tolerance here, so that is harmless.
    pad = float(o.get("linewidth") or 0) / 2 if stroked else 0.0
    return (o["x0"] - pad, o["top"] - pad, o["x1"] + pad, o["bottom"] + pad)


def _ink_bbox(page) -> tuple[float, float, float, float] | None:
    """``(x0, top, x1, bottom)`` in points (top-left origin) of the page's ink."""
    boxes = [
        (c["x0"], c["top"], c["x1"], c["bottom"])
        for c in page.chars
        if c.get("text", "").strip()
    ]
    for kind in _PATH_OBJECTS:
        for o in getattr(page, kind):
            box = _path_box(o)
            if box is not None:
                boxes.append(box)
    # Images count by their placed box: padding inside the bitmap is not
    # distinguishable from content without rasterizing.
    boxes.extend((o["x0"], o["top"], o["x1"], o["bottom"]) for o in page.images)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def measure_margins(
    pdf_path: Path,
    *,
    planned_inside_in: float | None,
    planned_outside_in: float | None = OUTER_IN,
    time_budget_s: float = TIME_BUDGET_S,
    clock=time.monotonic,
) -> MarginReport:
    """Measure every page of *pdf_path*.

    *planned_inside_in* is the managed inner margin the PDF was rendered with,
    ``None`` for author-set geometry (then only KDP's floors are checked).
    Raises on an unreadable PDF and :class:`MarginCheckTimeout` past the budget,
    checked page by page.
    """
    if planned_inside_in is None:
        planned_outside_in = None
    import pdfplumber  # lazy: this module must import without it

    started = clock()
    violations: list[MarginViolation] = []
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise ValueError(f"{pdf_path.name} has no pages")
        first = pdf.pages[0]
        page_size_in = (
            float(first.width) / _PT_PER_IN,
            float(first.height) / _PT_PER_IN,
        )
        for index, page in enumerate(pdf.pages):
            elapsed = clock() - started
            if elapsed > time_budget_s:
                logger.info(
                    "📏 Margin check stopped at the %gs budget: measured %d of %d "
                    "pages in %.1fs",
                    time_budget_s,
                    index,
                    page_count,
                    elapsed,
                )
                raise MarginCheckTimeout(
                    f"margin check exceeded {time_budget_s:g}s after {index} of "
                    f"{page_count} pages"
                )
            try:
                bbox = _ink_bbox(page)
                if bbox is not None:
                    width, height = float(page.width), float(page.height)
                    x0, top, x1, bottom = bbox
                    violations.extend(
                        classify_page_insets(
                            index + 1,
                            left_in=x0 / _PT_PER_IN,
                            right_in=(width - x1) / _PT_PER_IN,
                            top_in=top / _PT_PER_IN,
                            bottom_in=(height - bottom) / _PT_PER_IN,
                            page_count=page_count,
                            planned_inside_in=planned_inside_in,
                            planned_outside_in=planned_outside_in,
                        )
                    )
            finally:
                page.close()
    logger.info(
        "📏 Margin check measured %d pages in %.1fs: %d KDP, %d cosmetic",
        page_count,
        clock() - started,
        sum(v.severity == SEVERITY_KDP for v in violations),
        sum(v.severity == SEVERITY_COSMETIC for v in violations),
    )
    return MarginReport(
        page_count=page_count,
        page_size_in=page_size_in,
        planned_inside_in=planned_inside_in,
        planned_outside_in=planned_outside_in,
        violations=tuple(violations),
    )
