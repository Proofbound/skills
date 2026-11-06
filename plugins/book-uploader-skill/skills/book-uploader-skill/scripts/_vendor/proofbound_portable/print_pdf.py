"""Make a print copy of a PDF with ``/Link`` annotations removed.

KDP's Print Previewer flags link annotations as "non-printable markup". This
removes only ``/Link`` annotations and preserves everything else. pypdf is
imported lazily so the module imports without it (skills vendor this tree).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PrintCopyResult:
    pages: int
    links_removed: int
    annotations_remaining: int
    changed: bool  # False: dst is a byte copy of src


class PrintCopyError(Exception):
    """The print copy could not be made."""


class EncryptedPdfError(PrintCopyError):
    """The PDF is encrypted (user or owner password); it is never rewritten."""


class PageCountMismatchError(PrintCopyError):
    """The written copy does not have the source's page count."""


def _annotation_refs(page) -> list | None:
    """The page's ``/Annots`` entries, or None when absent or not an array."""
    annots = page.get("/Annots")
    annots = annots.get_object() if annots is not None else None
    return annots if isinstance(annots, list) else None


def _count_annotations(reader) -> tuple[int, int]:
    """Return ``(links, others)`` across all pages, resolving indirect refs.

    Null and dangling entries, which real PDFs carry, are not annotations.
    """
    links = others = 0
    for page in reader.pages:
        for ref in _annotation_refs(page) or []:
            annot = ref.get_object()
            if not isinstance(annot, dict):
                continue
            if annot.get("/Subtype") == "/Link":
                links += 1
            else:
                others += 1
    return links, others


def _drop_entries_pypdf_cannot_remove(writer) -> None:
    """Clear what makes ``remove_annotations`` raise on an otherwise sound PDF.

    It subscripts every entry, so a null or dangling one raises; and it nulls
    each link object as it goes, so a link shared by two pages raises on the
    second. Pages with neither keep their array untouched.
    """
    from pypdf.generic import ArrayObject, NameObject  # lazy, as in make_print_copy

    seen_links = set()
    for page in writer.pages:
        if "/Annots" not in page:
            continue
        refs = _annotation_refs(page)
        if refs is None:
            del page["/Annots"]
            continue
        kept = ArrayObject()
        for ref in refs:
            annot = ref.get_object()
            if not isinstance(annot, dict):
                continue
            if annot.get("/Subtype") == "/Link":
                if id(annot) in seen_links:
                    continue
                seen_links.add(id(annot))
            kept.append(ref)
        if len(kept) != len(refs):
            page[NameObject("/Annots")] = kept


def make_print_copy(src: Path, dst: Path) -> PrintCopyResult:
    from pypdf import PdfReader, PdfWriter  # lazy: must import without pypdf

    reader = PdfReader(src)
    # Owner-password-only PDFs open transparently, so check before any page.
    if reader.is_encrypted:
        raise EncryptedPdfError(f"{Path(src).name} is encrypted")
    pages = len(reader.pages)
    if pages == 0:
        raise PrintCopyError(f"{Path(src).name} has no pages")

    links, others = _count_annotations(reader)
    if links == 0:
        shutil.copyfile(src, dst)
        return PrintCopyResult(
            pages=pages, links_removed=0, annotations_remaining=others, changed=False
        )

    # No compress_identical_objects / remove_unreferenced: they exceed the
    # 512 MB sandbox address-space limit on a 100 MB input.
    writer = PdfWriter(clone_from=reader)
    _drop_entries_pypdf_cannot_remove(writer)
    # Tuple, not str (a bare string becomes a substring test). Never use
    # remove_links(): it removes every annotation.
    writer.remove_annotations(("/Link",))
    writer.pdf_header = reader.pdf_header
    # Written beside dst and moved in only once verified, so a failure at any
    # step never leaves a partial or unchecked file at dst.
    tmp = Path(f"{dst}.tmp")
    try:
        with open(tmp, "wb") as fh:
            writer.write(fh)

        out = PdfReader(tmp)
        if len(out.pages) != pages:
            raise PageCountMismatchError(
                f"print copy has {len(out.pages)} pages, source has {pages}"
            )
        links_left, remaining = _count_annotations(out)
        if links_left:
            raise PrintCopyError(f"print copy still has {links_left} link annotations")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return PrintCopyResult(
        pages=pages, links_removed=links, annotations_remaining=remaining, changed=True
    )
