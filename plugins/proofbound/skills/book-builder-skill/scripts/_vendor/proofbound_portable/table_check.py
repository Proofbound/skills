"""Pre-render check for pipe tables too wide for the page.

Advisory only. The renderer already fits columns to their widest token
(``pb-table-width.lua`` + ``\\pbTableFit``), so this reports only what it cannot
rescue: a table past the column cap, or one whose summed column floors exceed the
text width even set at the wide size. The authoritative floor is measured in TeX;
the estimate here is deliberately coarse and errs toward silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .interior_margins import DEFAULT_INNER_IN, OUTER_IN
from .trim import DEFAULT_TRIM_SIZE, TRIM_SIZES

# Columns a pipe table may have, by trim. TABLE_RULES (the writing guidance) renders
# these same numbers, so the guidance and this check cannot disagree.
TABLE_MAX_COLUMNS: dict[str, int] = {"6x9": 5, "a5": 4, "digest": 4}
assert set(TABLE_MAX_COLUMNS) == set(TRIM_SIZES)

# MIRRORED: packages/cc-template/templates/quarto-book/includes/table-style.tex
# (\pbTableWideSize = \footnotesize, \pbTableWideColSep = 2pt). Edit together.
WIDE_COLSEP_PT = 2.0
# Average advance of one character at \footnotesize (8pt) in the body font.
AVG_CHAR_WIDTH_PT = 4.4

_PT_PER_IN = 72.27
_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"[*_`\\]")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class WideTable:
    line: int  # 1-based line of the header row
    columns: int
    reason: str  # "columns" or "floors"


def text_width_pt(trim_size: str) -> float:
    return (
        TRIM_SIZES[trim_size]["width_in"] - OUTER_IN - DEFAULT_INNER_IN
    ) * _PT_PER_IN


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return _CELL_SPLIT.split(row)


def _longest_token(cell: str) -> int:
    plain = _MARKUP.sub("", _LINK.sub(r"\1", cell))
    return max((len(t) for t in re.split(r"[\s/]+", plain) if t), default=0)


def _tables(text: str):
    """Yield (header_line_no, header_row, body_rows) for each pipe table outside code."""
    lines = text.splitlines()
    in_fence = False
    i = 0
    while i < len(lines):
        if _FENCE.match(lines[i]):
            in_fence = not in_fence
        elif (
            not in_fence
            and "|" in lines[i]
            and i + 1 < len(lines)
            and "-" in lines[i + 1]
            and _SEPARATOR.match(lines[i + 1].strip())
            and len(_cells(lines[i + 1])) == len(_cells(lines[i]))
        ):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            yield i + 1, lines[i], lines[i + 2 : j]
            i = j
            continue
        i += 1


def find_wide_tables(text: str, trim_size: str = DEFAULT_TRIM_SIZE) -> list[WideTable]:
    cap = TABLE_MAX_COLUMNS[trim_size]
    width = text_width_pt(trim_size)
    found = []
    for line_no, header, body in _tables(text):
        rows = [_cells(header)] + [_cells(r) for r in body]
        ncols = len(rows[0])
        if ncols > cap:
            found.append(WideTable(line_no, ncols, "columns"))
            continue
        floor = sum(
            max(_longest_token(r[c]) for r in rows if c < len(r)) * AVG_CHAR_WIDTH_PT
            + 2 * WIDE_COLSEP_PT
            for c in range(ncols)
        )
        if floor > width:
            found.append(WideTable(line_no, ncols, "floors"))
    return found


def describe(table: WideTable, trim_size: str = DEFAULT_TRIM_SIZE) -> str:
    cap = TABLE_MAX_COLUMNS[trim_size]
    why = (
        f"more than the {cap}-column limit at {trim_size}"
        if table.reason == "columns"
        else f"its columns cannot fit the {trim_size} text width even in small type"
    )
    return (
        f"{table.columns}-column table ({why}); consider transposing it "
        "so the long dimension runs down the rows"
    )
