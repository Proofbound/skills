r"""The managed ``header-includes`` block, and the marker that identifies it.

Single home for a pair that used to exist twice — once in the retrofit CLI and
once in ``projects_index``, each carrying a comment saying it must match the
other. Both now import from here, so the pair cannot drift.

The block itself is still duplicated from ProofBound's server-side scaffold
template, which is the source of truth for a *generated* book and reaches it
only through Jinja — there is no importable Python constant to share. That
remaining pair is pinned server-side by ``test_book_index_preamble_drift``, the
only place where both this module and the sibling template are visible.
"""

from __future__ import annotations

#: Marker opening the managed preamble block. Revert matches on it, which is
#: why the block below is emitted as ONE block scalar rather than a per-line
#: list — a per-line list would be unremovable.
PREAMBLE_MARKER = "% pb:book-index:begin"

#: Locator makeindex records for a cross-reference, in place of a real page.
#:
#: ``\\see`` / ``\\seealso`` are ordinary makeindex *encapsulators*: they occupy a
#: locator slot and sort by page number among the entry's other locators. Two
#: different encaps for one key on one page is a makeindex ``Conflicting
#: entries`` **warning** — which Quarto's ``kIndexErrorRegex`` (``/^\\s\\s\\s--\\s/``)
#: cannot tell from an error, so it fails the whole render
#: (``proofbound_monorepo-rmj7``). Writing the cross-reference against a
#: sentinel page higher than any real one puts it alone on its own page, which
#: removes the collision *and* sorts it after every locator unconditionally.
#: The number is never typeset: ``\\see``/``\\seealso`` take two arguments and
#: discard the second, which is the locator.
BOOK_INDEX_XREF_SENTINEL_PAGE = "999999"

#: The managed ``header-includes`` block, verbatim.
#:
#: Kept as ONE block scalar (invariant #6). ``imakeidx``'s add-to-TOC option is
#: deliberately unset (it yields a double TOC entry) and the heading command is
#: a no-op because ``book-index.qmd`` supplies its own.
#:
#: ``\\pbIndexXRef`` is ``\\index`` with two deliberate differences, and it is what
#: :func:`~.markers.cross_reference_block` emits. It writes **immediately**
#: rather than at the shipout of its own page, so it cannot lose its entry to
#: the page-parity trap a ``\\clearpage`` reintroduces; and it writes
#: :data:`BOOK_INDEX_XREF_SENTINEL_PAGE` rather than ``\\thepage``. ``\\@sanitize``
#: is applied exactly as ``\\index`` applies it, so an escaped key reaches the
#: ``.idx`` byte-identically either way.
BOOK_INDEX_PREAMBLE = """\
% pb:book-index:begin
\\usepackage{imakeidx}
\\makeindex
\\newcommand{\\pbIndexNoHeading}[1]{}
\\indexsetup{level=\\pbIndexNoHeading, noclearpage}
\\makeatletter
\\newcommand{\\pbIndexXRef}{\\@bsphack\\begingroup\\@sanitize\\pb@indexxref}
\\def\\pb@indexxref#1{\\endgroup\\immediate\\write\\csname\\imki@jobname @idxfile\\endcsname{\\string\\indexentry\\@charlb#1\\@charrb{999999}}\\@esphack}
\\makeatother
% pb:book-index:end
"""
