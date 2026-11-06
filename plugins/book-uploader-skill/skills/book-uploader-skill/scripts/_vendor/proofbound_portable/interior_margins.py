"""Interior (gutter) margin policy: one source of truth for KDP-safe page geometry.

KDP rejects an interior whose inside margin is below the tier for its page count
("Set Trim Size, Bleed, and Margins",
https://kdp.amazon.com/en_US/help/topic/GVBQ3CMEQW3W2VL6, transcribed in
``packages/kdp-publish-skill/references/kdp-help-facts.md`` section 4). LaTeX
``geometry`` defaults give roughly 0.72in, which fails every book over 500 pages.

The page count is not known before the render, so it is estimated from the
manuscript's word count. Calibration: *Somalis in America*, 125k source words
(``*.qmd`` plus ``*.bib``) rendered to 602 pages, about 208 words per page
(measured 2026-10-30). ``WORDS_PER_PAGE`` is deliberately a little below that
and ``ESTIMATE_SAFETY`` rounds the estimate up.

Pure standard library: the uploader skill vendors this package.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# (max page count, minimum inside margin in inches), ascending.
KDP_GUTTER_TIERS: tuple[tuple[int, float], ...] = (
    (150, 0.375),
    (300, 0.5),
    (500, 0.625),
    (700, 0.75),
    (828, 0.875),
)
MAX_GUTTER_IN = 0.875
DEFAULT_INNER_IN = 0.75
OUTER_IN = 0.75
WORDS_PER_PAGE = 200
ESTIMATE_SAFETY = 1.15

_SKIP_DIRS = frozenset({"_book", ".quarto", "_freeze", ".git"})
_SOURCE_SUFFIXES = frozenset({".qmd", ".bib"})

MANAGED_KEYS = frozenset({"paperwidth", "paperheight", "inner", "outer"})

_TO_INCHES = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54, "pt": 1 / 72.27}


def required_gutter_in(page_count: int) -> float:
    """Minimum KDP inside margin (inches) for a book of *page_count* pages."""
    if page_count < 1:
        raise ValueError(f"page_count must be >= 1, got {page_count}")
    for upper, gutter in KDP_GUTTER_TIERS:
        if page_count <= upper:
            return gutter
    return MAX_GUTTER_IN


def estimate_page_count(source_words: int) -> int:
    """Estimated rendered page count for *source_words* words of qmd+bib source."""
    return max(1, math.ceil(source_words / WORDS_PER_PAGE * ESTIMATE_SAFETY))


def inner_margin_for(estimated_pages: int) -> float:
    """Inner margin to request: the default, widened for long books."""
    return max(DEFAULT_INNER_IN, required_gutter_in(estimated_pages))


def count_source_words(project_dir: Path) -> int:
    """Whitespace-split word count over the project's ``*.qmd`` and ``*.bib`` files."""
    total = 0
    for path in project_dir.rglob("*"):
        if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
            continue
        if _SKIP_DIRS.intersection(path.relative_to(project_dir).parts[:-1]):
            continue
        total += len(path.read_text(encoding="utf-8", errors="ignore").split())
    return total


GeometryValue = str | list[str] | None


def parse_geometry(geometry: GeometryValue) -> list[tuple[str, str | None]]:
    """Split a geometry value into ordered ``(key, value)`` pairs.

    Quarto accepts ``geometry`` either as one comma-separated string or as a
    YAML list of option strings; both are handled. A bare flag (``twoside``)
    has value ``None``.
    """
    if not geometry:
        return []
    items: list[str] = []
    for chunk in geometry if isinstance(geometry, list) else [geometry]:
        items.extend(str(chunk).split(","))
    parts: list[tuple[str, str | None]] = []
    for raw in items:
        item = raw.strip()
        if not item:
            continue
        if "=" in item:
            key, _, value = item.partition("=")
            parts.append((key.strip(), value.strip()))
        else:
            parts.append((item, None))
    return parts


def format_geometry(parts: Iterable[tuple[str, str | None]]) -> str:
    """Inverse of :func:`parse_geometry`: comma-joined, no spaces."""
    return ",".join(key if value is None else f"{key}={value}" for key, value in parts)


def format_length_in(x: float) -> str:
    """Render inches as ``6.0in`` / ``5.83in`` / ``0.875in``.

    Keeps the float's repr so ``6.0`` stays ``6.0in``, matching the server's
    historical ``paperwidth=6.0in`` strings. The uploader skill used to emit
    ``6in``; LaTeX geometry treats both identically.
    """
    return f"{float(x)!r}in"


def parse_length_in(s: str) -> float | None:
    """Parse ``0.75in``, ``0.75 in``, ``19.05mm`` (also cm, pt) to inches."""
    text = s.strip().lower()
    for unit, factor in _TO_INCHES.items():
        if text.endswith(unit):
            try:
                return float(text[: -len(unit)].strip()) * factor
            except ValueError:
                return None
    return None


@dataclass(frozen=True)
class GeometryPlan:
    """Outcome of :func:`plan_geometry`.

    ``estimated_pages`` is filled in by callers that derived *inner_in* from a
    manuscript word count (``None`` when the margin was given explicitly).
    """

    geometry: str
    managed: bool
    inner_in: float | None
    note: str | None
    estimated_pages: int | None = None


@dataclass(frozen=True)
class ConfiguredGutter:
    """What a project's existing ``geometry`` says about its inside margin.

    ``managed`` mirrors :func:`plan_geometry`: only paper size and inner/outer
    margins are present, so ``inner_in`` *is* the gutter LaTeX was told to use.
    With author-set options (``bindingoffset=``, ``margin=``, ...) the effective
    gutter is not knowable from ``inner=`` alone, so ``inner_in`` is ``None``.
    """

    managed: bool
    inner_in: float | None


def configured_gutter(geometry: GeometryValue) -> ConfiguredGutter:
    """Read the configured inside margin from an existing geometry value."""
    if geometry is not None and not isinstance(geometry, (str, list)):
        return ConfiguredGutter(managed=False, inner_in=None)
    parts = parse_geometry(geometry)
    if any(k not in MANAGED_KEYS for k, _ in parts):
        return ConfiguredGutter(managed=False, inner_in=None)
    raw = next((v for k, v in parts if k == "inner"), None)
    return ConfiguredGutter(
        managed=True, inner_in=parse_length_in(raw) if raw else None
    )


def plan_geometry(
    existing: GeometryValue, trim_spec: Mapping[str, object], inner_in: float
) -> GeometryPlan:
    """Decide the geometry string for *trim_spec* with at least *inner_in* gutter.

    Geometry is *managed* when it holds only paper size and inner/outer margins;
    then it is rewritten and an existing wider inner margin is never shrunk.
    Any author-set option (``margin=``, ``twoside``, ...) leaves it untouched.
    """
    if existing is not None and not isinstance(existing, (str, list)):
        return GeometryPlan(
            geometry=str(existing),
            managed=False,
            inner_in=None,
            note=(
                f"geometry is a {type(existing).__name__}, not a string or list; "
                "left untouched — KDP gutter not managed"
            ),
        )
    parts = parse_geometry(existing)
    keys = [k for k, _ in parts]
    existing_inner_raw = next((v for k, v in parts if k == "inner"), None)
    existing_inner = parse_length_in(existing_inner_raw) if existing_inner_raw else None

    custom = sorted({k for k in keys if k not in MANAGED_KEYS})
    if custom:
        return GeometryPlan(
            geometry=format_geometry(parts),
            managed=False,
            inner_in=existing_inner,
            note=(
                f"geometry has author-set options ({', '.join(custom)}); "
                "left untouched — KDP gutter not managed"
            ),
        )

    inner = inner_in
    note: str | None = None
    if existing_inner is not None and existing_inner > inner_in:
        inner = existing_inner
        note = f"kept existing wider gutter {existing_inner:g}in"
    geometry = format_geometry(
        [
            ("paperwidth", format_length_in(float(trim_spec["width_in"]))),  # type: ignore[arg-type]
            ("paperheight", format_length_in(float(trim_spec["height_in"]))),  # type: ignore[arg-type]
            ("inner", format_length_in(inner)),
            ("outer", format_length_in(OUTER_IN)),
        ]
    )
    return GeometryPlan(geometry=geometry, managed=True, inner_in=inner, note=note)


# ---------------------------------------------------------------------------
# Measured margins: where the ink on a rendered page actually sits.
#
# KDP checks the ink on each page, not the configured geometry: a table wider
# than the text block crosses into the gutter even when ``inner=`` is correct.
# The PDF extraction lives with the caller (it needs a PDF library); this is the
# rule that turns one page's measured insets into violations.
# ---------------------------------------------------------------------------

# KDP's minimum for the outside, top and bottom margins of a no-bleed interior.
KDP_EDGE_FLOOR_IN = 0.25
# Glyph side bearings overhang the text block by a few thousandths of an inch;
# without this a book whose planned gutter equals its KDP floor fails every page.
FLOOR_TOLERANCE_IN = 0.01
# Ink further than this outside the planned text block is a (cosmetic) overflow.
# It separates glyph side-bearing and italic overhang (measured up to 0.0346in on
# one 584pp book) from real overflow (recorded down to 0.06in); 3pt = 0.0417in.
TEXT_BLOCK_TOLERANCE_IN = 3 / 72

SEVERITY_KDP = "kdp"
SEVERITY_COSMETIC = "cosmetic"


@dataclass(frozen=True)
class MarginViolation:
    """Ink on *page* (1-based, physical) sits *inset_in* from *edge*, past *limit_in*.

    ``severity`` is ``"kdp"`` when KDP will reject the interior, ``"cosmetic"``
    when the ink only leaves the planned text block.
    """

    page: int
    edge: str
    inset_in: float
    limit_in: float
    severity: str

    def as_dict(self) -> dict[str, object]:
        return {
            "page": self.page,
            "edge": self.edge,
            "inset_in": round(self.inset_in, 3),
            "limit_in": round(self.limit_in, 3),
            "severity": self.severity,
        }


def kdp_floors_in(page_count: int) -> dict[str, float]:
    """KDP's minimum inset per edge for a no-bleed interior of *page_count* pages."""
    return {
        "inside": required_gutter_in(page_count),
        "outside": KDP_EDGE_FLOOR_IN,
        "top": KDP_EDGE_FLOOR_IN,
        "bottom": KDP_EDGE_FLOOR_IN,
    }


def classify_page_insets(
    page_no: int,
    *,
    left_in: float,
    right_in: float,
    top_in: float,
    bottom_in: float,
    page_count: int,
    planned_inside_in: float | None,
    planned_outside_in: float | None,
) -> list[MarginViolation]:
    """Violations for one page whose ink sits at the given insets from each edge.

    Odd (recto) pages bind on the left, so their inside inset is *left_in*; even
    (verso) pages bind on the right. KDP floors apply to every edge. The
    cosmetic text-block check is horizontal only and runs only for a planned
    (managed) geometry: top and bottom margins hold running heads and folios,
    and their planned size is a LaTeX default this policy does not record.
    """
    recto = page_no % 2 == 1
    insets = {
        "inside": left_in if recto else right_in,
        "outside": right_in if recto else left_in,
        "top": top_in,
        "bottom": bottom_in,
    }
    planned = {"inside": planned_inside_in, "outside": planned_outside_in}
    violations: list[MarginViolation] = []
    for edge, floor in kdp_floors_in(page_count).items():
        inset = insets[edge]
        if inset < floor - FLOOR_TOLERANCE_IN:
            violations.append(
                MarginViolation(page_no, edge, inset, floor, SEVERITY_KDP)
            )
            continue
        limit = planned.get(edge)
        if limit is not None and inset < limit - TEXT_BLOCK_TOLERANCE_IN:
            violations.append(
                MarginViolation(page_no, edge, inset, limit, SEVERITY_COSMETIC)
            )
    return violations
