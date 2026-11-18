"""Provenance sentinel for the generated back-of-book index page.

``book-index.qmd`` is the only index artifact that ships with a book, so it is
also the only place a *persistent* ownership marking can live: the transaction
directory ``.pb-book-index-txn/`` is dot-prefixed and is therefore dropped
wholesale by ``proofbound_shared.storage``'s project-tree filter, never
surviving a source upload or a ``.zip`` export.

The marking is a single unknown key in the page's own YAML front matter, which
Pandoc ignores and never renders. It survives source upload, ``.zip`` export,
R2 round-trips, and re-render — exactly where the finalizer and a later
customization job need to read it.

Everything here is pure: no I/O beyond reading the one file, no dependency on
the Phase 6 ``BookIndexMixin``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .frontmatter import split_frontmatter

#: Filename of the generated index page. ``index.qmd`` is the book's *intro*
#: page — the names are not interchangeable.
BOOK_INDEX_PAGE_FILENAME = "book-index.qmd"

#: Front-matter key/value pair that marks the page as Proofbound-generated.
BOOK_INDEX_SENTINEL_KEY = "pb-generated"
BOOK_INDEX_SENTINEL_VALUE = "book-index-v1"

#: The generated page, verbatim. Shape pinned by the Phase 0 spike (ichb.1):
#: an ``{.unnumbered}`` heading is required — a heading-less page renders as an
#: empty *numbered* chapter — and ``\printindex`` must be PDF-gated, since the
#: EPUB and HTML writers drop raw ``{=latex}`` but would still emit the div.
#: The ``unless-format`` sentence is what the EPUB's Index page says instead.
BOOK_INDEX_PAGE_CONTENT = f"""\
---
{BOOK_INDEX_SENTINEL_KEY}: {BOOK_INDEX_SENTINEL_VALUE}
---

# Index {{.unnumbered}}

::: {{.content-visible when-format="pdf"}}

```{{=latex}}
\\printindex
```

:::

::: {{.content-visible unless-format="pdf"}}

The index with page references is available in the print and PDF editions of
this book.

:::
"""


def is_pb_generated_index_page(path: Path) -> bool:
    """Return True only if *path* is a Proofbound-generated index page.

    Reads the front matter and nothing else. Returns False for a missing file,
    a file with no front matter, malformed YAML, or front matter written by
    someone else — a user-authored ``book-index.qmd`` is never claimed.
    Never raises.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    header_raw, _body = split_frontmatter(text)
    if not header_raw:
        return False

    lines = header_raw.splitlines()
    # split_frontmatter guarantees an opening and closing "---" fence.
    inner = "\n".join(lines[1:-1])
    try:
        parsed = yaml.safe_load(inner)
    except yaml.YAMLError:
        return False
    if not isinstance(parsed, dict):
        return False
    return parsed.get(BOOK_INDEX_SENTINEL_KEY) == BOOK_INDEX_SENTINEL_VALUE
