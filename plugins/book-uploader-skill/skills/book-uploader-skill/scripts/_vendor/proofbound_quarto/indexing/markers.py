r"""Marker insertion, key escaping, and the generated index page.

Three choke points live here, and everything else in the engine routes through
them:

* :func:`escape_index_key` — the ONLY place a term becomes a makeindex key.
  A single unescaped ``!``, ``@``, ``|`` or ``"`` silently restructures the
  index; a single unescaped LaTeX special hard-fails XeLaTeX at 95% progress.
* :func:`protected_ranges` — the ONLY place the engine decides what part of a
  ``.qmd`` is prose. A naive ``re.sub`` over the file will happily inject
  ``\index{}`` inside inline code, a link's URL, a math expression, a citation
  key, or a raw ``{=html}`` block.
* :func:`insert_markers` — inserts inside unprotected ranges only, immediately
  after the matched term.

**Markers go inline, abutting the matched term — never at paragraph end.** A
paragraph-end marker gives the wrong page locator whenever a long paragraph
crosses a page break. That is a correctness contract, not a style preference.
"""

from __future__ import annotations

import bisect
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence

from ..index_page import BOOK_INDEX_PAGE_CONTENT
from .constants import (
    BOOK_INDEX_MAX_LOCATORS_PER_ENTRY_PER_CHAPTER,
    BOOK_INDEX_MAX_MARKERS_PER_CHAPTER,
)
from .types import IndexEntry, IndexVocabulary

logger = logging.getLogger(__name__)

Range = tuple[int, int]


# ---------------------------------------------------------------------------
# Key escaping — choke point 1
# ---------------------------------------------------------------------------

#: Single-pass character map. A left-to-right pass over the *input* is what
#: makes this safe: a sequence of ``str.replace`` calls would re-process the
#: backslashes and braces it had just inserted.
_ESCAPES: dict[str, str] = {
    # LaTeX specials. These would be typeset as markup, or fail the run.
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "^": r"\^{}",
    "~": r"\textasciitilde{}",
    # makeindex's own quote character. ``\"`` would reach LaTeX as an umlaut
    # accent, so the literal glyph is spelled out instead.
    '"': r"\textquotedbl{}",
    # makeindex control characters: subentry, sort-key, and encapsulation
    # separators. Quoting with ``"`` is makeindex's escape, not LaTeX's.
    "!": '"!',
    "@": '"@',
    "|": '"|',
}


def escape_index_key(text: str) -> str:
    r"""Escape *text* for use inside ``\index{...}``.

    Handles both alphabets at once — LaTeX specials (escaped the LaTeX way) and
    makeindex controls ``! @ | "`` (quoted the makeindex way). The result is
    safe to concatenate with the structural separators ``!``, ``@`` and ``|``
    that :func:`index_argument` adds itself.
    """
    return "".join(_ESCAPES.get(ch, ch) for ch in text)


def make_sort_key(display: str) -> str:
    r"""Return the ``sortkey@display`` pair for *display*, or just the display.

    Accent folding is what puts *Zürich* under Z rather than after Z: makeindex
    sorts on raw bytes, so ``ü`` collates outside the Latin alphabet entirely.
    The ``@`` separator is emitted raw — it is structure, not content — while
    both halves are escaped.
    """
    folded = _fold(display)
    escaped_display = escape_index_key(display)
    if folded == display:
        return escaped_display
    return f"{escape_index_key(folded)}@{escaped_display}"


def _fold(text: str) -> str:
    """Strip combining marks: ``Zürich`` -> ``Zurich``, ``Ægis`` -> ``Ægis``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def index_argument(entry: IndexEntry, *, cross_references: bool = True) -> str:
    r"""Assemble the complete argument of one ``\index{...}`` command.

    Shape: ``sort@display!sub-sort@sub-display|see{target}``. Every content
    fragment goes through :func:`escape_index_key`; only the separators are
    raw.

    *cross_references* is False for the markers placed in the prose. A
    ``|seealso{...}`` encapsulation **replaces** that occurrence's page number,
    so carrying it into every chapter marker would trade away the locators the
    entry exists for. Cross-references are declared exactly once, by
    :func:`cross_reference_block`; the inline markers only contribute locators.
    """
    if entry.sort_key:
        argument = f"{escape_index_key(entry.sort_key)}@{escape_index_key(entry.key)}"
    else:
        argument = make_sort_key(entry.key)

    if entry.subentry:
        argument = f"{argument}!{make_sort_key(entry.subentry)}"

    if not cross_references:
        return argument

    if entry.see:
        argument = f"{argument}|see{{{escape_index_key(entry.see)}}}"
    elif entry.see_also:
        # makeindex allows one encapsulation per entry, so multiple targets
        # share a single ``\seealso`` rather than colliding as duplicates.
        targets = ", ".join(escape_index_key(t) for t in entry.see_also)
        argument = f"{argument}|seealso{{{targets}}}"

    return argument


def marker_for(entry: IndexEntry) -> str:
    r"""Return the raw-inline marker for *entry*.

    Pandoc's raw-attribute syntax for an **inline** raw span is a code span
    followed by the attribute — ``` `\index{x}`{=latex} ```. A bare
    ``\index{x}{=latex}`` is not a raw span at all: it would be typeset as
    literal text in the PDF and leak ``{=latex}`` into the EPUB.
    """
    return f"`\\index{{{index_argument(entry, cross_references=False)}}}`{{=latex}}"


# ---------------------------------------------------------------------------
# Protected ranges — choke point 2
# ---------------------------------------------------------------------------

_FRONT_MATTER_FENCE = re.compile(r"^---\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(\s|$)")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)\s*$")
_FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^[^\]]+\]:")
_LINK_REF_DEF = re.compile(r"^ {0,3}\[[^\]^]+\]:\s*\S")
_INDENTED_CODE = re.compile(r"^(?: {4}|\t)")
_DIV_FENCE = re.compile(r"^ {0,3}:{3,}")
_DISPLAY_MATH_FENCE = re.compile(r"^ {0,3}\$\$\s*$")
_LATEX_COMMAND = re.compile(r"\\[a-zA-Z@]+\*?")
_CITATION = re.compile(r"@[a-zA-Z0-9_][a-zA-Z0-9_:.#$%&+?<>~/-]*")
_AUTOLINK = re.compile(r"<[a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]*>|<[^<>\s@]+@[^<>\s]+>")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")

#: Scripts with no whitespace word boundaries, where Python's ``\b`` mis-fires.
_NO_BOUNDARY_RANGES = (
    (0x2E80, 0x9FFF),  # CJK radicals through CJK Unified Ideographs
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0x0E00, 0x0E7F),  # Thai
)


class _BlockState:
    """The line-mode state machine's memory: which multi-line block is open.

    Only one can be open at a time, and the order the two methods are called
    in — continuation before opening — is what keeps a ``$$`` inside a fenced
    block from being read as math.
    """

    def __init__(self) -> None:
        self.fence_char: str | None = None
        self.fence_len = 0
        self.in_math = False
        self.in_html_comment = False

    def continues_open_block(self, line: str) -> bool:
        """True if *line* is inside an open block, closing it if it ends one."""
        if self.fence_char is not None:
            closing = re.match(
                rf"^ {{0,3}}{re.escape(self.fence_char)}{{{self.fence_len},}}\s*$", line
            )
            if closing:
                self.fence_char = None
            return True

        if self.in_math:
            if _DISPLAY_MATH_FENCE.match(line):
                self.in_math = False
            return True

        if self.in_html_comment:
            if "-->" in line:
                self.in_html_comment = False
            return True

        return False

    def opens_block(self, line: str) -> bool:
        """True if *line* opens a code fence or a display-math block."""
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            self.fence_char, self.fence_len = marker[0], len(marker)
            return True

        if _DISPLAY_MATH_FENCE.match(line):
            self.in_math = True
            return True

        return False


def _front_matter_range(
    qmd: str, lines: Sequence[str], offsets: Sequence[int]
) -> tuple[Range | None, int]:
    """The YAML front matter span and the line to resume scanning from.

    First block only. A ``---`` further down the file is a thematic break or a
    setext underline, not front matter. An unterminated block swallows the rest
    of the document, which is what a reader of the raw file would conclude too.
    """
    if not lines or not _FRONT_MATTER_FENCE.match(lines[0]):
        return None, 0

    total = len(lines)
    close = next(
        (j for j in range(1, total) if _FRONT_MATTER_FENCE.match(lines[j])), None
    )
    if close is None:
        return (0, len(qmd)), total
    return (0, offsets[close] + len(lines[close])), close + 1


def _is_protected_block_line(line: str) -> bool:
    """True for constructs that protect their whole line."""
    return bool(
        _ATX_HEADING.match(line)
        or _DIV_FENCE.match(line)
        or _FOOTNOTE_DEF.match(line)
        or _LINK_REF_DEF.match(line)
        or _INDENTED_CODE.match(line)
        # Any line carrying a pipe is treated as a table row. Prose almost
        # never contains one, and a marker injected into a cell breaks the
        # column alignment the whole table depends on.
        or "|" in line
    )


def _setext_heading_ranges(
    lines: Sequence[str], offsets: Sequence[int], index: int
) -> list[Range]:
    """The underline and the heading text above it are both heading."""
    start = offsets[index]
    ranges = [(start, start + len(lines[index]))]
    if index > 0 and lines[index - 1].strip():
        previous = offsets[index - 1]
        ranges.append((previous, previous + len(lines[index - 1])))
    return ranges


def protected_ranges(qmd: str, *, notes_are_prose: bool = False) -> list[Range]:
    """Return sorted, merged ``(start, end)`` offsets that must not be touched.

    A single left-to-right pass with an explicit mode, rather than one large
    regex alternation: the constructs nest and are context-dependent (a fence
    inside front matter, a ``$`` inside inline code, a ``|`` table row inside a
    fenced block), which alternation cannot express — and a fifteen-branch
    regex is unreadable at the exact place correctness matters most.

    Protected: YAML front matter, fenced and indented code, inline code spans,
    math, link and image destinations, reference definitions, raw attribute
    blocks and spans, existing LaTeX commands, footnote definitions and inline
    footnotes, citation keys, headings, HTML comments and tags, and table rows.

    *notes_are_prose* opens the **interior** of an inline ``^[...]`` footnote to
    the other scanners, so a caller that rewrites prose — ``autolink_bare_urls``
    — can reach a bare URL sitting inside a citation note. Markup nested in the
    note (code spans, links, raw spans) is still claimed normally; only the note
    wrapper stops being a wall. Indexing must never pass it: a marker inside a
    note reports whatever page the note lands on, not the discussion.

    **Scope limitation, deliberate.** The flag covers inline ``^[...]`` notes
    only. Reference-style ``[^id]:`` definitions stay fully protected under it,
    because their continuation lines are claimed by ``_INDENTED_CODE`` — which
    cannot simply be suppressed inside a note, since ``_FENCE`` matches 0–3
    leading spaces and so a four-space-indented fenced block inside a definition
    has ``_INDENTED_CODE`` as its only guard. Scanning relative to a note's own
    base indentation is real CommonMark block logic; see
    ``proofbound_monorepo-jl45.7``. The half-covered middle — a definition whose
    first line rewrites and whose continuation silently does not — is what this
    scope exists to avoid.

    Default behaviour is byte-identical to the flagless call.
    """
    scanners = _NOTES_AS_PROSE_SCANNERS if notes_are_prose else _INLINE_SCANNERS
    ranges: list[Range] = []
    lines = qmd.splitlines(keepends=True)
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line)

    total = len(lines)
    front_matter, index = _front_matter_range(qmd, lines, offsets)
    if front_matter is not None:
        ranges.append(front_matter)

    state = _BlockState()

    while index < total:
        line = lines[index]
        start = offsets[index]
        end = start + len(line)
        index += 1

        if state.continues_open_block(line) or state.opens_block(line):
            ranges.append((start, end))
            continue

        if not line.strip():
            continue

        if _is_protected_block_line(line):
            ranges.append((start, end))
            continue

        if _SETEXT_UNDERLINE.match(line):
            ranges.extend(_setext_heading_ranges(lines, offsets, index - 1))
            continue

        # An HTML comment opening mid-line protects the rest of the line, and
        # every line after it until the close.
        opening_comment = line.find("<!--")
        if opening_comment != -1 and "-->" not in line[opening_comment:]:
            ranges.append((start + opening_comment, end))
            state.in_html_comment = True
            continue

        ranges.extend(_inline_protected(line, start, scanners))

    return _merge(ranges)


# ---------------------------------------------------------------------------
# Inline scanning — one scanner per mode
# ---------------------------------------------------------------------------
#
# Each ``_scan_*`` inspects the character the dispatch loop stopped on and
# returns ``(span, next_i)``: the protected span it claims — in offsets
# *relative to the line* — or ``None`` when the character opened no mode, plus
# the offset the loop resumes from. Returning ``(None, i + 1)`` is how a
# scanner says "not mine after all"; every scanner must return ``next_i > i``
# so the loop cannot stall.
#
# Splitting by mode rather than by regex alternation is deliberate: the modes
# nest and are context-dependent, and the dispatch order below is itself a
# correctness contract (see ``_scan_code_span``).

#: A claimed span within the line, or ``None``, plus the next scan offset.
_Scan = tuple[Range | None, int]


def _scan_code_span(text: str, i: int, n: int) -> _Scan:
    """Inline code, including multi-backtick spans.

    Dispatched FIRST so a ``$`` or ``|`` inside code can never open another
    mode. An unterminated run protects nothing but is still skipped whole.
    """
    run_end = i
    while run_end < n and text[run_end] == "`":
        run_end += 1
    run = run_end - i
    close = _find_backtick_run(text, run_end, run)
    if close is None:
        return None, run_end
    end = close + run
    # A raw-attribute span binds to the code span it follows.
    if end < n and text[end] == "{":
        brace = _match_delimiter(text, end, "{", "}")
        if brace is not None:
            end = brace + 1
    return (i, end), end


def _scan_backslash(text: str, i: int, n: int) -> _Scan:
    r"""``\(..\)`` and ``\[..\]`` math, a LaTeX command, or an escaped char."""
    for opener, closer in (("\\(", "\\)"), ("\\[", "\\]")):
        if text.startswith(opener, i):
            close = text.find(closer, i + 2)
            if close != -1:
                return (i, close + 2), close + 2
    command = _LATEX_COMMAND.match(text, i)
    if command:
        end = _consume_latex_arguments(text, command.end())
        return (i, end), end
    # An escaped punctuation character: skip both so the escape cannot be
    # split by an insertion.
    return None, (i + 2 if i + 1 < n else i + 1)


def _scan_dollar_math(text: str, i: int, n: int) -> _Scan:
    """``$$..$$`` display math, or a ``$..$`` inline span."""
    if text.startswith("$$", i):
        close = text.find("$$", i + 2)
        if close == -1:
            return None, i + 2
        return (i, close + 2), close + 2
    close = _find_unescaped(text, "$", i + 1)
    if close is None:
        return None, i + 1
    return (i, close + 1), close + 1


def _scan_inline_footnote(text: str, i: int, n: int) -> _Scan:
    """Inline footnote ``^[...]``.

    Indexing inside one would place the locator on whatever page the note
    lands on, not on the discussion.
    """
    if not text.startswith("^[", i):
        return None, i + 1
    close = _match_delimiter(text, i + 1, "[", "]")
    if close is None:
        return None, i + 1
    return (i, close + 1), close + 1


def _scan_inline_footnote_as_prose(text: str, i: int, n: int) -> _Scan:
    """The ``notes_are_prose=True`` counterpart: step over ``^[`` and go on.

    Claiming nothing hands the note's interior to the other scanners, which is
    the point. Skipping the ``[`` as well is what keeps the loop from re-reading
    it as a link opener; the closing ``]`` opens no mode on its own, so the note
    simply dissolves into the surrounding prose.
    """
    if not text.startswith("^[", i):
        return None, i + 1
    return None, i + 2


def _scan_angle(text: str, i: int, n: int) -> _Scan:
    """An autolink, an HTML tag, or an HTML comment opening mid-line."""
    match = _AUTOLINK.match(text, i) or _HTML_TAG.match(text, i)
    if text.startswith("<!--", i):
        close = text.find("-->", i + 4)
        end = (close + 3) if close != -1 else n
        return (i, end), end
    if match:
        return (i, match.end()), match.end()
    return None, i + 1


def _scan_bracket(text: str, i: int, n: int) -> _Scan:
    """A link, image, citation, or footnote reference — never plain brackets."""
    close = _match_delimiter(text, i, "[", "]")
    if close is None:
        return None, i + 1
    inner = text[i + 1 : close]
    end = close + 1
    trailing = text[end] if end < n else ""
    if trailing == "(":
        paren = _match_delimiter(text, end, "(", ")")
        if paren is not None:
            end = paren + 1
    elif trailing == "{":
        brace = _match_delimiter(text, end, "{", "}")
        if brace is not None:
            end = brace + 1
    elif not inner.startswith(("@", "-@", "^")):
        # Plain brackets in prose. Not a link, cite, or footnote ref.
        return None, i + 1
    # An image's leading ``!`` belongs to the span it introduces.
    start = i - 1 if i > 0 and text[i - 1] == "!" else i
    return (start, end), end


def _scan_citation(text: str, i: int, n: int) -> _Scan:
    """A bare ``@key``, but only where it starts a word."""
    if i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_@"):
        return None, i + 1
    citation = _CITATION.match(text, i)
    if citation is None:
        return None, i + 1
    return (i, citation.end()), citation.end()


def _scan_brace(text: str, i: int, n: int) -> _Scan:
    """A raw attribute block such as ``{=html}`` standing on its own."""
    close = _match_delimiter(text, i, "{", "}")
    if close is None:
        return None, i + 1
    return (i, close + 1), close + 1


#: Dispatch table for :func:`_inline_protected`. Keyed on the character that
#: opens each mode; anything absent is prose. ``` ` ``` is listed first for
#: readability only — a dict lookup, not a chain of ``if``, is what actually
#: guarantees code wins over ``$`` and ``|``, because a character opens at
#: most one mode.
_INLINE_SCANNERS: dict[str, Callable[[str, int, int], _Scan]] = {
    "`": _scan_code_span,
    "\\": _scan_backslash,
    "$": _scan_dollar_math,
    "^": _scan_inline_footnote,
    "<": _scan_angle,
    "[": _scan_bracket,
    "@": _scan_citation,
    "{": _scan_brace,
}

#: The ``notes_are_prose=True`` table. One entry differs; sharing every other
#: scanner by construction is what keeps the two modes from drifting apart.
_NOTES_AS_PROSE_SCANNERS: dict[str, Callable[[str, int, int], _Scan]] = {
    **_INLINE_SCANNERS,
    "^": _scan_inline_footnote_as_prose,
}


def _inline_protected(
    text: str,
    base: int,
    scanners: dict[str, Callable[[str, int, int], _Scan]] = _INLINE_SCANNERS,
) -> list[Range]:
    """Scan one prose line, emitting spans on mode transitions."""
    spans: list[Range] = []
    i = 0
    n = len(text)

    while i < n:
        scanner = scanners.get(text[i])
        if scanner is None:
            i += 1
            continue
        span, i = scanner(text, i, n)
        if span is not None:
            spans.append((base + span[0], base + span[1]))

    return spans


def _find_backtick_run(text: str, start: int, length: int) -> int | None:
    """Index of the next backtick run of exactly *length*, or ``None``."""
    i = start
    n = len(text)
    while i < n:
        if text[i] != "`":
            i += 1
            continue
        run_end = i
        while run_end < n and text[run_end] == "`":
            run_end += 1
        if run_end - i == length:
            return i
        i = run_end
    return None


def _find_unescaped(text: str, char: str, start: int) -> int | None:
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == char:
            return i
        i += 1
    return None


def _match_delimiter(text: str, start: int, open_ch: str, close_ch: str) -> int | None:
    """Index of the balanced closer for the delimiter at *start*, or ``None``."""
    if start >= len(text) or text[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _consume_latex_arguments(text: str, start: int) -> int:
    r"""Extend past any ``{...}`` / ``[...]`` groups following a command name."""
    i = start
    while i < len(text) and text[i] in "{[":
        closer = "}" if text[i] == "{" else "]"
        close = _match_delimiter(text, i, text[i], closer)
        if close is None:
            break
        i = close + 1
    return i


def _merge(ranges: Iterable[Range]) -> list[Range]:
    ordered = sorted(ranges)
    merged: list[Range] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------------------
# Marker insertion — choke point 3
# ---------------------------------------------------------------------------


def insert_markers(
    qmd: str,
    entries: Sequence[IndexEntry],
    *,
    max_locators_per_entry: int = BOOK_INDEX_MAX_LOCATORS_PER_ENTRY_PER_CHAPTER,
    max_markers: int = BOOK_INDEX_MAX_MARKERS_PER_CHAPTER,
    chapter_label: str = "",
) -> tuple[str, int]:
    r"""Insert ``\index`` markers into one chapter, returning ``(qmd, count)``.

    At most one marker per entry per paragraph, placed immediately after the
    first whole-word occurrence of the entry's longest matching surface form.
    Insertion happens only outside :func:`protected_ranges`.

    Idempotent: a paragraph already carrying an entry's marker is skipped, so
    ``f(f(x)) == f(x)``. Both caps are applied in *entries* order — which is
    score order — so which markers survive a cap is reproducible.
    """
    if not entries:
        return qmd, 0

    protected = protected_ranges(qmd)
    starts = [start for start, _ in protected]

    placeable = [
        (entry, _surface_patterns(entry), marker_for(entry))
        for entry in entries
        if entry.has_locators and entry.surface_forms
    ]

    insertions: list[tuple[int, str]] = []
    per_entry: dict[int, int] = {}
    total = 0

    for para_start, para_end in _paragraph_spans(qmd):
        paragraph = qmd[para_start:para_end]
        for position, (_entry, patterns, marker) in enumerate(placeable):
            if total >= max_markers:
                logger.warning(
                    "book index hit the per-chapter marker cap",
                    extra={"chapter": chapter_label, "cap": max_markers},
                )
                break
            if per_entry.get(position, 0) >= max_locators_per_entry:
                continue
            if marker in paragraph:
                # Already indexed here — a re-run, not a new locator.
                continue
            offset = _first_free_match(
                paragraph, para_start, patterns, protected, starts
            )
            if offset is None:
                continue
            insertions.append((offset, marker))
            per_entry[position] = per_entry.get(position, 0) + 1
            total += 1
        if total >= max_markers:
            break

    if not insertions:
        return qmd, 0

    result = qmd
    for offset, marker in sorted(insertions, reverse=True):
        result = result[:offset] + marker + result[offset:]
    return result, len(insertions)


def _surface_patterns(entry: IndexEntry) -> list[re.Pattern[str]]:
    """Compile surface forms longest-first, so ``gut microbiome`` beats ``gut``."""
    forms = sorted(set(entry.surface_forms), key=lambda f: (-len(f), f))
    return [_boundary_pattern(form) for form in forms]


def _boundary_pattern(surface: str) -> re.Pattern[str]:
    r"""Whole-word matcher for *surface*, Unicode-aware.

    Python's ``\b`` is Unicode-aware on ``str``, but it mis-fires against
    scripts that have no word boundaries at all: every CJK character is a word
    character, so ``\b`` would refuse a match that is in fact a whole word.
    Those terms match as plain substrings instead — extraction is English-only,
    but a non-English *term* inside English prose is still indexable.
    """
    escaped = re.escape(surface)
    if _lacks_word_boundaries(surface):
        return re.compile(escaped)
    return re.compile(rf"(?<![\w-]){escaped}(?![\w-])")


def _lacks_word_boundaries(surface: str) -> bool:
    return any(
        any(low <= ord(ch) <= high for low, high in _NO_BOUNDARY_RANGES)
        for ch in surface
    )


def _paragraph_spans(qmd: str) -> list[Range]:
    """Split on blank lines, returning ``(start, end)`` of each non-blank run."""
    spans: list[Range] = []
    offset = 0
    start: int | None = None
    for line in qmd.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = offset
            end = offset + len(line)
        else:
            if start is not None:
                spans.append((start, end))
                start = None
        offset += len(line)
    if start is not None:
        spans.append((start, offset))
    return spans


def _first_free_match(
    paragraph: str,
    base: int,
    patterns: Sequence[re.Pattern[str]],
    protected: Sequence[Range],
    starts: Sequence[int],
) -> int | None:
    """Absolute offset just past the first match lying wholly in free text."""
    for pattern in patterns:
        for match in pattern.finditer(paragraph):
            absolute_start = base + match.start()
            absolute_end = base + match.end()
            if _overlaps(absolute_start, absolute_end, protected, starts):
                continue
            if _covers(absolute_end, protected, starts):
                continue
            return absolute_end
    return None


def _overlaps(
    start: int, end: int, protected: Sequence[Range], starts: Sequence[int]
) -> bool:
    position = bisect.bisect_right(starts, start) - 1
    for index in (position, position + 1):
        if 0 <= index < len(protected):
            p_start, p_end = protected[index]
            if start < p_end and p_start < end:
                return True
    return False


def _covers(offset: int, protected: Sequence[Range], starts: Sequence[int]) -> bool:
    """True when *offset* sits strictly inside a protected span."""
    position = bisect.bisect_right(starts, offset) - 1
    if 0 <= position < len(protected):
        p_start, p_end = protected[position]
        return p_start < offset < p_end
    return False


# ---------------------------------------------------------------------------
# The generated index page
# ---------------------------------------------------------------------------


def build_index_page(vocabulary: IndexVocabulary) -> str:
    r"""Render ``book-index.qmd`` for *vocabulary*.

    The page shape is pinned by the Phase 0 spike and lives in
    :data:`BOOK_INDEX_PAGE_CONTENT`: the ``pb-generated`` front-matter
    sentinel, an ``{.unnumbered}`` heading (a heading-less page renders as an
    empty *numbered* chapter), a PDF-gated ``\printindex``, and the
    ``unless-format=pdf`` sentence the EPUB's Index page shows instead.

    The page is the same for every vocabulary. It deliberately carries **no**
    ``\index`` commands of its own: ``\index`` writes to the ``.idx`` through a
    deferred ``\write`` that only runs when the page carrying it is shipped
    out, and ``imakeidx`` runs ``makeindex`` at ``\printindex`` — i.e. before
    that page ships. Anything declared here reaches the ``.tex`` and never
    reaches the index. Cross-references therefore live in the last indexed
    chapter instead; see :func:`cross_reference_block`.
    """
    return BOOK_INDEX_PAGE_CONTENT


def cross_reference_block(entries: Sequence[IndexEntry]) -> str:
    r"""Return the raw-LaTeX block declaring every ``see`` / ``see also``.

    ``\see`` and ``\seealso`` are ordinary makeindex *encapsulators*: they
    occupy a locator slot and sort with the entry's other locators by page
    number. The cross-reference must therefore carry a page at least as high as
    the entry's last ordinary marker, or it interleaves with the numbers
    instead of printing after them —
    ``ribosome, 12--14, see also proteasome`` is the conventional shape.

    It gets that page from :data:`~.preamble.BOOK_INDEX_XREF_SENTINEL_PAGE`,
    not from where the block happens to sit, which is why the commands here are
    ``\pbIndexXRef`` rather than ``\index``
    (``proofbound_monorepo-rmj7``). Deriving the page from the block's own
    position is a trap in both directions: sharing a page with an ordinary
    marker of the same key gives makeindex two encaps for one page, whose
    ``Conflicting entries`` **warning** Quarto cannot distinguish from an error
    and fails the render over; while forcing the block onto a page of its own
    with ``\clearpage`` makes the entry's survival depend on the page *parity*
    where the chapter ends, and it is lost silently when it does not ship
    before ``\printindex``. A sentinel locator higher than any real page
    escapes both: nothing else is ever on that page, and ``\pbIndexXRef``
    writes immediately rather than at a shipout.

    The block still rides the **last chapter that received markers**. That is
    now only a requirement to execute before ``\printindex`` runs makeindex —
    it no longer carries the ordering — and the last indexed chapter satisfies
    it with the least surprise.

    A pure ``see`` entry places no markers at all
    (:attr:`~.types.IndexEntry.has_locators`), so this block is the only place
    it is ever mentioned. Either way the locator is swallowed: ``\see`` and
    ``\seealso`` take two arguments and typeset only the first, so the
    sentinel page is never printed.

    Returns ``""`` when nothing cross-references, so a vocabulary without them
    leaves the chapter byte-identical to the marker-only result.
    """
    commands = [
        f"\\pbIndexXRef{{{index_argument(entry)}}}"
        for entry in entries
        if entry.see or entry.see_also
    ]
    if not commands:
        return ""
    body = "\n".join(commands)
    return f"```{{=latex}}\n{body}\n```\n"


def append_cross_references(qmd: str, entries: Sequence[IndexEntry]) -> str:
    r"""Return *qmd* with :func:`cross_reference_block` appended, idempotently.

    A raw *block* (fenced, not the inline code-span form the prose markers use)
    because it stands alone between paragraphs rather than abutting a term. The
    EPUB writer drops it whole, exactly as it drops the inline markers.
    """
    block = cross_reference_block(entries)
    if not block or block in qmd:
        return qmd
    return f"{qmd.rstrip()}\n\n{block}"
