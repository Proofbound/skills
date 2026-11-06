"""Print preflight for the fonts embedded in a PDF, run before a KDP/Lulu upload.

Read-only. Reports every font a print converter is known to mangle.

Two conditions are reported:

* **A CFF (OpenType/PostScript-outline) font whose glyph grid is not 1000 units.**
  KDP's converter re-embeds such a font declaring a 1000-unit grid while leaving
  the outlines on the original one, so the glyphs print about twice the size their
  spacing allows and pile up. Inter's ``.otf`` files (2048 units) do this; the
  ``.ttf`` files the production image installs do not. A PDF viewer honours the
  font's own grid, so the file looks correct everywhere except in the proof.
* **A font that is not embedded at all.** The printer substitutes its own. This
  covers a ``/FontDescriptor`` with no font program and a simple font (Type1,
  MMType1, TrueType, CIDFontType0/2) with no descriptor at all, such as a bare
  standard-14 ``/BaseFont /Helvetica``. Type0 fonts delegate to their descendants
  and Type3 fonts draw their own glyphs, so neither is ever flagged.

pypdf and fontTools are imported lazily so the module imports without them
(skills vendor this tree).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

STANDARD_SCALE = 0.001  # a 1000-unit glyph grid
IDENTITY_SCALE = 1.0  # an FDArray matrix that defers to the top-level one
TOLERANCE = 1e-6

FONT_FILE_KEYS = ("/FontFile", "/FontFile2", "/FontFile3")

# Font subtypes that carry their own descriptor and so must embed a program.
_DESCRIBED_SUBTYPES = frozenset(
    {"/Type1", "/MMType1", "/TrueType", "/CIDFontType0", "/CIDFontType2"}
)


@dataclass(frozen=True)
class FontProblem:
    font: str
    problem: str


def _is_standard(scale: float) -> bool:
    return (
        abs(scale - STANDARD_SCALE) < TOLERANCE
        or abs(scale - IDENTITY_SCALE) < TOLERANCE
    )


def _cff_scales(data: bytes) -> list[float]:
    """Every FontMatrix x-scale a bare CFF font program declares."""
    from fontTools.cffLib import CFFFontSet

    fonts = CFFFontSet()
    fonts.decompile(io.BytesIO(data), None)
    top = fonts[0]
    matrices = [getattr(top, "FontMatrix", None)]
    matrices += [getattr(fd, "FontMatrix", None) for fd in getattr(top, "FDArray", [])]
    return [float(m[0]) for m in matrices if m is not None]


def _descriptor_problem(descriptor) -> str | None:
    """Why this font would not survive a print converter, or None if it would."""
    present = [key for key in FONT_FILE_KEYS if key in descriptor]
    if not present:
        return "not embedded"
    if present != ["/FontFile3"]:
        return None  # Type 1 or TrueType program: no CFF grid to get wrong

    stream = descriptor["/FontFile3"].get_object()
    data = stream.get_data()
    if stream.get("/Subtype") == "/OpenType":
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(data))
        if "CFF " not in font:
            return None
        units = font["head"].unitsPerEm
        return None if units == 1000 else f"CFF outlines on a {units}-unit grid"

    odd = [scale for scale in _cff_scales(data) if not _is_standard(scale)]
    if not odd:
        return None
    return f"CFF outlines on a {round(1 / odd[0])}-unit grid"


def check_pdf(path: str) -> list[FontProblem]:
    """Every font in the PDF that fails the preflight, sorted by font name."""
    from pypdf import PdfReader
    from pypdf.generic import DictionaryObject

    reader = PdfReader(path)
    failures: dict[str, str] = {}
    for number in range(1, reader.trailer["/Size"]):
        obj = reader.get_object(number)
        if not isinstance(obj, DictionaryObject):
            continue
        kind = obj.get("/Type")
        if kind == "/FontDescriptor":
            problem = _descriptor_problem(obj)
            name = obj.get("/FontName")
        elif (
            kind == "/Font"
            and obj.get("/Subtype") in _DESCRIBED_SUBTYPES
            and "/FontDescriptor" not in obj
        ):
            problem = "not embedded"
            name = obj.get("/BaseFont")
        else:
            continue
        if problem:
            failures[str(name or "(unnamed)").lstrip("/")] = problem
    return [FontProblem(font, failures[font]) for font in sorted(failures)]
