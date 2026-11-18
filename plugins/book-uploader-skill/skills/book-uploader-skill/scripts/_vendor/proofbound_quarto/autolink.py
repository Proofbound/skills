r"""Turn bare ``http(s)://`` URLs in manuscript prose into Pandoc autolinks.

A bare URL reaches LaTeX as an ordinary ``Str``. Inside a ``\footnote{}`` at
6x9 that is an unbreakable token wider than the footnote measure, so it runs
off the right margin however much ``\emergencystretch`` is allowed. The same
URL written ``<https://...>`` becomes a Pandoc autolink, renders through
``\url{}``, and therefore picks up the break points ``xurl`` installs. The
printed text is unchanged -- a citation still shows its whole address.

**This rewrite is persisted** on the render path (``project_dir`` is what gets
uploaded back as ``src/``), so a wrong extent is silent manuscript corruption,
not a cosmetic slip. Two successive character-class drafts each shipped
instances of it: ``.../file_`` truncated to ``.../file``, ``?tags[]=wool``
truncated to ``?tags``, ``a).Next`` swallowed whole. A character class cannot
tell a delimiter from a path character. So the widest plausible token is
matched and its extent is then resolved from context, and anything still
ambiguous is left alone rather than guessed at.

**Scope limitations, both deliberate.**

* ``_https://example.com/x_`` is never rewritten: ``_`` is ``\w``, so the
  lookbehind suppresses the match outright. An emphasis-wrapped bare URL is far
  rarer than an underscore-bearing path, and no rule can tell them apart.
* Only ``http`` and ``https``. ``mailto:`` and ``ftp:`` are not this defect and
  carry their own Pandoc quirks.
"""

from __future__ import annotations

import re

from .indexing.markers import protected_ranges

__all__ = ["autolink_bare_urls"]


#: Widest plausible token. ``(`` and ``[`` are deliberately absent from the
#: lookbehind: ``protected_ranges`` already claims ``](url)`` destinations, and
#: excluding them here made ``(https://...)`` and ``^[https://...]`` -- both
#: real shapes in citation notes -- silently unmatchable.
_CANDIDATE = re.compile(r"(?<![\w@/<.-])https?://\S+")

#: Typographic quotes and guillemets, spelled as escapes so the source stays
#: unambiguous. A manuscript quotes a URL with these, not with ASCII: without
#: them ``\u201chttps://example.com/a\u201d`` wraps its closing quote into the
#: destination, which is a different and usually broken URL.
_SMART_QUOTES = (
    "\u2018\u2019\u201a\u201b"  # single: ' ' , '
    "\u201c\u201d\u201e\u201f"  # double: " " „ ‟
    "\u00ab\u00bb\u2039\u203a"  # guillemets
)

#: A resolved candidate still holding one of these is left unchanged. ``\S+``
#: admits them and trailing trim cannot reach one sitting mid-token, so
#: ``https://example.com/a",next`` would otherwise resolve whole and pass every
#: other post-condition. Rejecting beats truncating, same as the unclosed-opener
#: bail-out.
_REJECT_CHARS = "\"'`\\" + _SMART_QUOTES

#: Stripped from the tail, one per pass, to a fixed point. ``_`` and ``*`` are
#: absent on purpose -- both are legal path characters (``.../file_``,
#: ``.../a*b``). ``*`` is handled separately, and only when mirrored.
_TRIM_CHARS = ".,;:!?\"'`" + _SMART_QUOTES

_CLOSER_FOR = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = frozenset(_CLOSER_FOR.values())

#: Rules out ``https://`` alone, a schemeless host, and a dotless authority.
_WELL_FORMED = re.compile(r"^https?://[^\s/]+\.[^\s/]")

_MIN_LENGTH = 12


def autolink_bare_urls(text: str) -> tuple[str, int]:
    """Wrap bare ``http(s)://`` URLs in *text* in angle brackets.

    Pure. Returns the rewritten text and the number of URLs wrapped.

    Only prose is touched: every candidate's **whole resolved span** must be
    disjoint from ``protected_ranges(text, notes_are_prose=True)``. Testing the
    span rather than its start position is load-bearing -- a candidate can begin
    in prose and run into markup, which is exactly what
    ``See https://example.com/a`\\index{cleaning}`{=latex}`` does.

    Idempotent: an existing ``<https://...>`` is excluded twice over, by the
    ``<`` in the lookbehind and by the angle-autolink scanner.
    """
    protected = protected_ranges(text, notes_are_prose=True)
    replacements: list[tuple[int, int, str]] = []

    for match in _CANDIDATE.finditer(text):
        start = match.start()
        url = _resolve_extent(match.group(), _mirrored_stars(text, start))
        if url is None:
            continue
        end = start + len(url)
        if _overlaps(start, end, protected):
            continue
        replacements.append((start, end, f"<{url}>"))

    # Right to left, so an earlier replacement's offsets stay valid.
    out = text
    for start, end, wrapped in reversed(replacements):
        out = out[:start] + wrapped + out[end:]
    return out, len(replacements)


def _resolve_extent(token: str, star_budget: int) -> str | None:
    """Resolve how much of *token* is actually the URL, or ``None`` to skip.

    Left-to-right balanced-delimiter scan, then a trailing trim. This is what
    separates ``?tags[]=wool`` (balanced, kept whole) from ``path.]Next`` and
    ``a).Next`` (a foreign closer, truncated there).
    """
    stack: list[str] = []
    cut = len(token)
    for i, ch in enumerate(token):
        if ch in _CLOSER_FOR:
            stack.append(_CLOSER_FOR[ch])
        elif ch in _CLOSERS:
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                # Nothing to match: a foreign delimiter, not part of the URL.
                cut = i
                break

    if stack:
        # An opener left hanging -- `https://example.com/a(b` could go either
        # way. Never link a guessed substring.
        return None

    url = _trim_tail(token[:cut], star_budget)

    if any(ch in url for ch in _REJECT_CHARS):
        return None
    if not _WELL_FORMED.match(url):
        return None
    if len(url) < _MIN_LENGTH:
        return None
    if any(ch in url for ch in "<> \t"):
        return None
    return url


def _trim_tail(url: str, star_budget: int) -> str:
    """Strip trailing sentence punctuation, and mirrored ``*`` only."""
    while url:
        if url[-1] in _TRIM_CHARS:
            url = url[:-1]
        elif url[-1] == "*" and star_budget > 0:
            url = url[:-1]
            star_budget -= 1
        else:
            break
    return url


def _mirrored_stars(text: str, start: int) -> int:
    r"""Width of the ``*`` run that is still *open* where the URL begins.

    ``*`` is a legal path character, so a trailing one is only ever stripped
    against an opener that is actually waiting to be closed:
    ``*https://example.com/x*`` is emphasis, ``https://example.com/a*b`` is a
    path, and ``https://example.com/q?x=foo*`` keeps its star.

    Two things this has to get right, both of which a naive read gets wrong:

    * Emphasis routinely opens several words before the URL --
      ``*See https://example.com/a*``. Reading only the character immediately
      before the URL scores that as budget zero and folds the closing ``*``
      into the destination, breaking the link and the emphasis together.
    * An earlier *closed* span is not an opener. ``*Slate* then
      https://example.com/q?x=foo*`` must keep its trailing star. Hence the
      parity test: an odd number of ``*`` runs ahead of the URL on this line
      means one is still open, an even number means they all matched.
    """
    line_start = text.rfind("\n", 0, start) + 1
    runs = re.findall(r"\*+", text[line_start:start])
    if len(runs) % 2 == 0:
        return 0
    return len(runs[-1])


def _overlaps(start: int, end: int, protected: list[tuple[int, int]]) -> bool:
    return any(start < p_end and p_start < end for p_start, p_end in protected)
