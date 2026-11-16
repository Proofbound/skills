"""Pre-render checks for pipe tables.

``find_wide_tables`` is advisory. The renderer already fits columns to their widest
token (``pb-table-width.lua`` + ``\\pbTableFit``), so it reports only what it cannot
rescue: a table past the column cap, or one whose summed column floors exceed the
text width even set at the wide size. The authoritative floor is measured in TeX;
the estimate here is deliberately coarse and errs toward silence.

``find_table_problems`` is the structural TABLE_RULES lint: the width findings above
plus a missing ``{#tbl-}`` caption and long header labels. ``find_ragged_rows`` finds
body rows whose cell count differs from the header's. There is no ``$`` logic here:
whether a ``$`` pair reads as math depends on the effective reader, so the render
preflight asks real Pandoc (``render_preflight.table_parse_warnings``).
Run it as ``python -m proofbound_portable.table_check FILE... [--trim 6x9]``.
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
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
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


def _lines(text: str) -> list[str]:
    """Split on ``\\n`` only: ``str.splitlines`` also splits on U+2028 and friends,
    which Pandoc does not, so line numbers and offsets would drift."""
    return text.replace("\r\n", "\n").split("\n")


def _cells(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return _CELL_SPLIT.split(row)


def _cell_count(row: str) -> int:
    """Cells in ``row`` as Pandoc counts them: a ``|`` inside a code span does not
    split a cell. Structural only; ``$`` math is the reader's business."""
    code = [(m.start(), m.end() - 1) for m in _CODE_SPAN.finditer(row)]
    return len(_cells(_unpiped(row, code)))


def _longest_token(cell: str) -> int:
    plain = _MARKUP.sub("", _LINK.sub(r"\1", cell))
    return max((len(t) for t in re.split(r"[\s/]+", plain) if t), default=0)


# A line that opens a block whose indented continuation is content, not code:
# list items (bullet, numbered, #., (1), a., i., (@)), footnotes, definitions.
_CONTAINER_OPENER = re.compile(
    r"^\s*(?:[-*+]|\(?(?:\d+|#|@[\w-]*|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]"
    r"|\[\^[^\]]+\]:|[:~])(?:\s|$)"
)
_INDENTED = re.compile(r"^( {4}|\t)")


def _code_lines(lines: list[str]) -> frozenset[int]:
    """0-based indices of lines Pandoc reads as code: fenced blocks (fence lines
    included) and indented code blocks (4+ spaces or a tab after a blank line).

    Inside a list item, footnote or definition, indented lines are that block's
    content, and the container lasts until an unindented line after a blank line.
    When unsure this leans toward *not* masking: a missed mask is a false blocker
    the author fixes by fencing the example, a false mask hides a broken table.
    """
    code: set[int] = set()
    fence = ""  # the opening marker while inside a fenced block
    in_indented = in_container = False
    for k, ln in enumerate(lines):
        m = _FENCE.match(ln)
        if fence:
            code.add(k)
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and not m.group(2).strip()
            ):
                fence = ""
            continue
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            code.add(k)
            continue
        if not ln.strip():
            continue
        prev_blank = k == 0 or not lines[k - 1].strip()
        indented = bool(_INDENTED.match(ln))
        if _CONTAINER_OPENER.match(ln) and not in_indented:
            in_container = True
            continue
        if indented and not in_container and (in_indented or prev_blank):
            in_indented = True
            code.add(k)
            continue
        in_indented = False
        if not indented and prev_blank:
            in_container = False
    return frozenset(code)


def _tables(text: str):
    """Yield (header_line_no, header_row, body_rows) for each pipe table outside code."""
    lines = _lines(text)
    for start, end in _table_spans(lines, _code_lines(lines)):
        yield start + 1, lines[start], lines[start + 2 : end]


def _table_spans(lines: list[str], code: frozenset[int]):
    """Yield (header_index, end_index) for each pipe table outside ``code`` (0-based, end exclusive)."""
    i = 0
    while i < len(lines):
        if (
            i not in code
            and "|" in lines[i]
            and i + 1 < len(lines)
            and "-" in lines[i + 1]
            and _SEPARATOR.match(lines[i + 1].strip())
            and len(_cells(lines[i + 1])) == _cell_count(lines[i])
        ):
            j = i + 2
            while (
                j < len(lines)
                and j not in code
                and "|" in lines[j]
                and lines[j].strip()
            ):
                j += 1
            yield i, j
            i = j
            continue
        i += 1


def find_wide_tables(text: str, trim_size: str = DEFAULT_TRIM_SIZE) -> list[WideTable]:
    return _wide_tables(_tables(text), trim_size)


def _wide_tables(tables, trim_size: str) -> list[WideTable]:
    cap = TABLE_MAX_COLUMNS[trim_size]
    width = text_width_pt(trim_size)
    found = []
    for line_no, header, body in tables:
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


# --- Full TABLE_RULES lint ---------------------------------------------------------

HEADER_MAX_WORDS = 3  # TABLE_RULES: "Header labels are 3 words or fewer"

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A code span closes at a backtick run of exactly the opening length; a backslash
# escapes the first backtick.
_CODE_SPAN = re.compile(r"(?<![`\\])(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_PAREN = re.compile(r"\([^)]*\)")
_TBL_CAPTION = re.compile(r"^:\s.*\{#tbl-[\w-]+")


@dataclass(frozen=True)
class TableProblem:
    line: int  # 1-based
    kind: str  # "caption" | "header" | "columns" | "floors" | "html"
    severity: str  # "error" | "warning"
    message: str


def _blank_out(text: str, pattern: re.Pattern) -> str:
    """Replace matches with spaces, keeping newlines so offsets and line numbers hold."""
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _unpiped(row: str, spans: list[tuple[int, int]]) -> str:
    """``row`` with the ``|`` inside each ``(start, end)`` span (offsets) blanked."""
    chars = list(row)
    for a, b in spans:
        for k in range(a, min(b + 1, len(chars))):
            if chars[k] == "|":
                chars[k] = " "
    return "".join(chars)


def _has_caption(lines: list[str], start: int, end: int) -> bool:
    after = end
    while after < len(lines) and not lines[after].strip():
        after += 1
    before = start - 1
    while before >= 0 and not lines[before].strip():
        before -= 1
    return any(
        0 <= k < len(lines) and _TBL_CAPTION.match(lines[k].strip())
        for k in (after, before)
    )


_HTML_TABLE = re.compile(r"<table\b", re.IGNORECASE)

BARE_HTML_TABLE_MESSAGE = (
    "bare <table> is not a table in the PDF: Pandoc reads each tag as separate raw "
    "HTML, so the table is dropped and its cell text runs together. Rewrite it as a "
    "pipe table, or put the whole table inside a ```{=html} fenced block"
)


def find_bare_html_tables(text: str) -> list[int]:
    """1-based lines of a ``<table`` outside code, comments and ``{=html}`` fences."""
    blanked = _blank_out(_blank_out(text, _COMMENT), _CODE_SPAN)
    lines = _lines(blanked)
    code = _code_lines(lines)
    return [
        k + 1 for k, ln in enumerate(lines) if k not in code and _HTML_TABLE.search(ln)
    ]


def find_table_problems(
    text: str, trim_size: str = DEFAULT_TRIM_SIZE
) -> list[TableProblem]:
    """Every structural TABLE_RULES finding in one chapter, sorted by line."""
    lines = _lines(text)
    code = _code_lines(lines)
    spans = list(_table_spans(lines, code))
    problems: list[TableProblem] = []

    tables = ((s + 1, lines[s], lines[s + 2 : e]) for s, e in spans)
    for table in _wide_tables(tables, trim_size):
        problems.append(
            TableProblem(
                table.line, table.reason, "warning", describe(table, trim_size)
            )
        )

    for start, end in spans:
        if not _has_caption(lines, start, end):
            problems.append(
                TableProblem(
                    start + 1,
                    "caption",
                    "warning",
                    "table has no caption; add `: Caption text {#tbl-slug}` after it "
                    "and cite it as @tbl-slug",
                )
            )
        for cell in _cells(lines[start]):
            label = _PAREN.sub("", _MARKUP.sub("", cell)).strip()
            if len(label.split()) > HEADER_MAX_WORDS:
                problems.append(
                    TableProblem(
                        start + 1,
                        "header",
                        "warning",
                        f"header '{cell.strip()}' is over {HEADER_MAX_WORDS} words",
                    )
                )

    for line_no in find_bare_html_tables(text):
        problems.append(
            TableProblem(line_no, "html", "warning", BARE_HTML_TABLE_MESSAGE)
        )

    return sorted(problems, key=lambda p: (p.line, p.kind))


@dataclass(frozen=True)
class RaggedRow:
    line: int  # 1-based line of the body row
    cells: int  # cells the row has
    columns: int  # cells the header has


def find_ragged_rows(text: str) -> list[RaggedRow]:
    """Pipe-table body rows whose cell count differs from the header's.

    Structural only (no ``$`` logic): Pandoc pads a short row with empty cells and
    silently drops a long row's extra cells, so neither shows in the parsed table.
    A ``|`` inside a code span does not split a cell, matching Pandoc. A ``|``
    inside ``$`` math is counted: with ``$`` math on, Pandoc keeps ``$P(A|B)$`` in
    one cell, so a caller that knows the reader filters those rows itself. A
    table inside an HTML comment is not rendered, so it is not checked.
    """
    lines = _lines(_blank_out(text, _COMMENT))
    found: list[RaggedRow] = []
    for start, end in _table_spans(lines, _code_lines(lines)):
        columns = _cell_count(lines[start])
        for k in range(start + 2, end):
            cells = _cell_count(lines[k])
            if cells != columns:
                found.append(RaggedRow(k + 1, cells, columns))
    return found


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Lint pipe tables against TABLE_RULES.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--trim", default=DEFAULT_TRIM_SIZE, choices=sorted(TRIM_SIZES))
    ap.add_argument("--errors-only", action="store_true", help="report only errors")
    args = ap.parse_args(argv)

    errors = 0
    for name in args.files:
        with open(name, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for p in find_table_problems(text, args.trim):
            if args.errors_only and p.severity != "error":
                continue
            errors += p.severity == "error"
            print(f"{name}:{p.line}: {p.severity}: {p.message}", file=sys.stdout)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
