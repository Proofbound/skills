"""One-line warning when a local PDF carries fonts KDP's print converter garbles.

A warning only, never a blocker: the user may upload the local PDF anyway. The
check itself lives in proofbound-portable, vendored beside these scripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
from proofbound_portable.print_fonts import check_pdf  # noqa: E402


def font_warning(pdf_path: Path | str) -> str | None:
    """None when the PDF's fonts are print-safe, else one line saying what to do.

    If the check itself cannot run (unreadable or encrypted PDF, missing
    dependency), the line says the fonts could not be checked and why.
    """
    name = Path(pdf_path).name
    try:
        problems = check_pdf(str(pdf_path))
    except Exception as exc:  # - a failed check must never stop an upload
        return f"could not check the fonts in {name} ({type(exc).__name__}: {exc}); verify it before printing"
    if not problems:
        return None
    listed = "; ".join(f"{p.font} ({p.problem})" for p in problems)
    return (
        f"{name} has fonts KDP's print converter garbles: {listed}. "
        "Remove the local PDF so the server renders the book with print-safe fonts, "
        "or re-render locally with TrueType fonts."
    )
