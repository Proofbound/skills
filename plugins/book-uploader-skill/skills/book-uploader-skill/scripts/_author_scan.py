"""Author file detection for the pre-upload scan.

Reports a JSON-friendly scan dict of ``author.md`` / ``author.{jpg,png}``. The
policy it applies (bio and photo limits, accepted image formats, soft-break
folding) is the server's own, imported from ``proofbound_portable.author``
vendored beside these scripts, so this pre-flight cannot disagree with what the
upload does. ``scan_author`` itself is the skill's reporting layer: it decides
which situations block and which merely warn.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))

from proofbound_portable.author import (  # noqa: E402
    MAX_AUTHOR_BIO_CHARS,
    MAX_PHOTO_BYTES,
    detect_image_format,
    fold_soft_breaks,
)


def scan_author(project_path: Path) -> dict:
    """Return a JSON-friendly dict describing author files in *project_path*.

    Schema::

        {
            "bio_found": bool,
            "bio_size_chars": int | None,     # codepoints after UTF-8 decode-with-replace
            "photo_found": "author.jpg" | "author.png" | None,
            "photo_size_bytes": int | None,
            "conflicts": list[str],
            "blockers": list[str],
            "warnings": list[str],
        }
    """
    bio_path = project_path / "author.md"
    jpg_path = project_path / "author.jpg"
    png_path = project_path / "author.png"

    bio_found = bio_path.is_file()
    bio_size_chars: int | None = None
    blockers: list[str] = []
    warnings: list[str] = []
    conflicts: list[str] = []

    if bio_found:
        try:
            raw = bio_path.read_bytes()
        except OSError as exc:
            blockers.append(f"could not read author.md: {exc}")
        else:
            text = raw.decode("utf-8", errors="replace")
            text = fold_soft_breaks(text.replace("\r\n", "\n").replace("\r", "\n")).rstrip()
            bio_size_chars = len(text)
            if bio_size_chars > MAX_AUTHOR_BIO_CHARS:
                warnings.append(
                    f"author.md is {bio_size_chars} chars; will be truncated to {MAX_AUTHOR_BIO_CHARS} on upload"
                )

    photo_found: str | None = None
    photo_size_bytes: int | None = None

    if jpg_path.is_file() and png_path.is_file():
        conflicts.append("both_jpg_and_png")
        blockers.append("both author.jpg and author.png present; only one is allowed")
    else:
        photo_path = jpg_path if jpg_path.is_file() else (png_path if png_path.is_file() else None)
        if photo_path is not None:
            try:
                photo_bytes = photo_path.read_bytes()
            except OSError as exc:
                blockers.append(f"could not read {photo_path.name}: {exc}")
            else:
                photo_found = photo_path.name
                photo_size_bytes = len(photo_bytes)
                if photo_size_bytes == 0:
                    blockers.append(f"{photo_path.name} is empty")
                elif photo_size_bytes > MAX_PHOTO_BYTES:
                    mb = photo_size_bytes / (1024 * 1024)
                    blockers.append(f"{photo_path.name} is {mb:.2f} MB; max {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
                else:
                    detected = detect_image_format(photo_bytes[:8])
                    if detected is None:
                        blockers.append(f"{photo_path.name} is not a valid JPEG or PNG (magic byte mismatch)")
                    elif photo_path.suffix.lstrip(".").lower() != detected:
                        warnings.append(
                            f"{photo_path.name} contains {detected.upper()} data; will store as author.{detected}"
                        )

    return {
        "bio_found": bio_found,
        "bio_size_chars": bio_size_chars,
        "photo_found": photo_found,
        "photo_size_bytes": photo_size_bytes,
        "conflicts": conflicts,
        "blockers": blockers,
        "warnings": warnings,
    }
