"""Names the render policy and the server's PDF header writer must agree on.

Stdlib only, so the book-uploader skill can vendor it. ``render_policy`` admits a
filter only if it is one of the files ProofBound writes itself, and the server's
``pdf_headers`` writes those files; both import the names from here so the list
has one owner.
"""

from __future__ import annotations

#: First-line prefix that marks a Lua file as written (and so overwritable) by us.
MANAGED_FILTER_MARKER = "-- pb-managed"

#: Project paths of the Lua filters ProofBound writes and registers.
PB_BIDI_FILTER_PATH = ".pb-bidi.lua"
PB_TABLE_HEAD_FILTER_PATH = ".pb-table-head.lua"
PB_TABLE_WIDTH_FILTER_PATH = ".pb-table-width.lua"
PB_ISOTOPE_FILTER_PATH = ".pb-isotope.lua"

#: PDF engines a render of caller content may use. LuaLaTeX runs Lua, latexmk reads
#: Perl ``latexmkrc`` files, tectonic fetches over the network.
PDF_ENGINES: tuple[str, ...] = ("xelatex", "pdflatex")
