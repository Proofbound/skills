#!/usr/bin/env python3
"""Validate that a folder is ready to upload to ProofBound.

Reports what's present, what's missing, and whether the folder is upload-ready.
Used as Phase 1 of the book-uploader-skill workflow.

Usage:
    uv run python scripts/validate_folder.py --project-path /path/to/folder
    uv run python scripts/validate_folder.py --project-path /path/to/folder --json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _author_scan import scan_author  # noqa: E402
from _print_fonts import font_warning  # noqa: E402
from _quarto_output import (  # noqa: E402
    AmbiguousPdfError,
    QuartoOutputDirError,
    configured_output_dir,
    describe_output_dirs,
    find_pdf as find_output_pdf,
    output_dir_names,
)
from _upload_plan import SymlinkEscapeError, iter_upload_files  # noqa: E402
from proofbound_portable.book_info import BOOK_TYPES, DECLINABLE_BOOK_FIELDS  # noqa: E402
from proofbound_portable.table_check import describe, find_wide_tables  # noqa: E402
from proofbound_portable.trim import DEFAULT_TRIM_SIZE, TRIM_SIZES  # noqa: E402

REQUIRED_FILES = ["_quarto.yml", "cc-config.yml"]
OPTIONAL_FILES = ["index.qmd", "summary.qmd", "references.qmd", "colophon.qmd", "references.bib"]
SPECIAL_QMD = {"index.qmd", "summary.qmd", "references.qmd", "colophon.qmd", "intro.qmd"}

# Top-level dir names that are normal in a ProofBound book project — anything
# else over a few MB is worth surfacing as a warning before upload.
BASE_KNOWN_TOP_DIRS = {"chapters", "covers", "_book", "_freeze", ".git", ".quarto", "docs"}
LARGE_DIR_THRESHOLD_MB = 5.0


def discover_chapters(project_path: Path) -> list[str]:
    """Find chapter .qmd files. Prefer chapters/ subdir, fall back to root."""
    chapters_dir = project_path / "chapters"
    if chapters_dir.is_dir():
        files = sorted(chapters_dir.glob("*.qmd"))
        return [str(p.relative_to(project_path)) for p in files]

    root_qmds = sorted(p for p in project_path.glob("*.qmd") if p.name not in SPECIAL_QMD)
    return [str(p.relative_to(project_path)) for p in root_qmds]


def find_pdf(project_path: Path) -> str | None:
    """Locate a built PDF in the project's Quarto output dir."""
    pdf = find_output_pdf(project_path)
    return str(pdf.relative_to(project_path)) if pdf else None


def known_top_dirs(project_path: Path) -> set[str]:
    """Top-level dirs that are normal here, including a custom ``output-dir``."""
    return BASE_KNOWN_TOP_DIRS | {name.split("/", 1)[0] for name in output_dir_names(project_path)}


def simulate_upload(project_path: Path, pdf_path: Path | None) -> dict:
    """Size exactly what ``upload.py`` would zip: the same files, PDF included.

    *pdf_path* is the PDF the upload adds back (``None`` on the render path).
    Returns the file count + total size that would actually ship, plus any
    unexpectedly-large top-level directories (e.g. a stray ``_resources/`` full of
    assets from a different project) so callers can flag them before paying the
    upload. Raises :class:`SymlinkEscapeError` as the upload would.
    """
    file_count = 0
    total_bytes = 0
    top_dir_bytes: dict[str, int] = {}

    for path, rel in iter_upload_files(project_path, pdf_path):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        file_count += 1
        total_bytes += size
        if len(rel.parts) > 1:
            top_dir_bytes[rel.parts[0]] = top_dir_bytes.get(rel.parts[0], 0) + size

    large_dirs = [
        {"name": name, "size_mb": round(size / (1024 * 1024), 2)}
        for name, size in sorted(top_dir_bytes.items(), key=lambda kv: -kv[1])
        if size / (1024 * 1024) >= LARGE_DIR_THRESHOLD_MB and name not in known_top_dirs(project_path)
    ]

    return {
        "upload_file_count": file_count,
        "upload_total_size_mb": round(total_bytes / (1024 * 1024), 2),
        "large_dirs": large_dirs,
    }


def check_covers(project_path: Path) -> dict[str, bool]:
    covers = project_path / "covers"
    return {
        "artwork": (covers / "cover_front_artwork.png").exists(),
        "kdp_pdf": (covers / "kdp" / "cover.pdf").exists(),
        "lulu_pdf": (covers / "lulu" / "cover.pdf").exists(),
    }


def _yaml_mapping(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def check_book_info(project_path: Path, author: dict) -> tuple[list[str], list[str]]:
    """Check the cc-config.yml facts the server's completeness gate reads.

    Returns ``(blockers, warnings)``. A missing or unknown ``book_type`` is only a
    warning here because ``autofix_folder.py`` writes one; a malformed ``declined:``
    would be rejected by the server, so it blocks. Whether a field is supplied
    mirrors the server's readers: description from ``outline.yml`` (else
    ``cc-config.yml``), the blurb from ``blurb.md``, the bio from ``author.md``.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    cc = _yaml_mapping(project_path / "cc-config.yml")

    book_type = str(cc.get("book_type") or "").strip()
    if not book_type:
        warnings.append(
            "cc-config.yml has no book_type — the server requires one; run autofix_folder.py "
            "or add one of: " + ", ".join(BOOK_TYPES)
        )
    elif book_type not in BOOK_TYPES:
        warnings.append(f"cc-config.yml book_type '{book_type}' is not one of: {', '.join(BOOK_TYPES)}")

    declined = cc.get("declined")
    if declined is None:
        return blockers, warnings
    if not isinstance(declined, list):
        blockers.append("cc-config.yml `declined:` must be a list of field names")
        return blockers, warnings
    names = [str(n) for n in declined]
    allowed = ", ".join(DECLINABLE_BOOK_FIELDS)
    for name in names:
        if name not in DECLINABLE_BOOK_FIELDS:
            blockers.append(f"cc-config.yml declined: '{name}' is not declinable (allowed: {allowed})")
    for name in sorted({n for n in names if names.count(n) > 1}):
        blockers.append(f"cc-config.yml declined: lists '{name}' more than once")

    outline_book = _yaml_mapping(project_path / "outline.yml").get("book")
    outline_description = outline_book.get("description") if isinstance(outline_book, dict) else None
    blurb_file = project_path / "blurb.md"
    supplied = {
        "description": bool(str(outline_description or cc.get("description") or "").strip()),
        "book_blurb": blurb_file.is_file() and bool(blurb_file.read_text(encoding="utf-8", errors="replace").strip()),
        "author_bio": bool(author["bio_found"]) and bool(author["bio_size_chars"]),
    }
    for name in DECLINABLE_BOOK_FIELDS:
        if name in names and supplied[name]:
            blockers.append(f"{name} is both supplied and listed in cc-config.yml declined: — remove one")
    return blockers, warnings


def check_wide_tables(project_path: Path, chapters: list[str]) -> list[str]:
    """Advisory warnings for pipe tables the renderer cannot fit (never blockers)."""
    trim = _yaml_mapping(project_path / "cc-config.yml").get("trim_size")
    warnings: list[str] = []
    if trim is None:
        trim = DEFAULT_TRIM_SIZE
    elif not isinstance(trim, str) or trim not in TRIM_SIZES:
        warnings.append(
            f"cc-config.yml trim_size {trim!r} is not one of {sorted(TRIM_SIZES)}; "
            f"checking tables against {DEFAULT_TRIM_SIZE}"
        )
        trim = DEFAULT_TRIM_SIZE
    for chapter in chapters:
        try:
            text = (project_path / chapter).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # advisory only: unreadable/dangling chapters are reported elsewhere
        for table in find_wide_tables(text, trim):
            warnings.append(f"{chapter}:{table.line}: {describe(table, trim)}")
    return warnings


def build_report(project_path: Path) -> dict:
    config_files = {f: (project_path / f).exists() for f in REQUIRED_FILES}
    optional_files = {f: (project_path / f).exists() for f in OPTIONAL_FILES}
    chapters = discover_chapters(project_path)
    # Surfaced as a blocker rather than swallowed: an escaping output-dir means
    # the PDF this project renders is not one we can safely go find.
    output_dir_error: str | None = None
    try:
        configured_output_dir(project_path)
    except QuartoOutputDirError as exc:
        output_dir_error = str(exc)
    pdf_search_desc = describe_output_dirs(project_path)
    pdf_error: str | None = None
    try:
        pdf_path = find_pdf(project_path)
    except AmbiguousPdfError as exc:
        pdf_path, pdf_error = None, str(exc)
    covers = check_covers(project_path)
    author = scan_author(project_path)
    symlink_error: str | None = None
    try:
        upload_sim = simulate_upload(project_path, project_path / pdf_path if pdf_path else None)
    except SymlinkEscapeError as exc:
        symlink_error = str(exc)
        upload_sim = {"upload_file_count": None, "upload_total_size_mb": None, "large_dirs": []}

    blockers: list[str] = []
    for f, present in config_files.items():
        if not present:
            blockers.append(f"missing {f}")
    if not chapters:
        blockers.append("no chapter .qmd files found in chapters/ or root")
    if pdf_error:
        blockers.append(pdf_error)
    elif pdf_path is None:
        blockers.append(f"no PDF in {pdf_search_desc}")
    if output_dir_error:
        blockers.append(output_dir_error)
    if symlink_error:
        blockers.append(symlink_error)
    blockers.extend(author["blockers"])
    info_blockers, info_warnings = check_book_info(project_path, author)
    blockers.extend(info_blockers)

    warnings: list[str] = []
    for f, present in optional_files.items():
        if not present:
            warnings.append(f"no {f}")
    if not any(covers.values()):
        warnings.append("no covers/ directory (or it is empty)")
    warnings.extend(author["warnings"])
    warnings.extend(info_warnings)
    warnings.extend(check_wide_tables(project_path, chapters))
    if pdf_path:
        fonts = font_warning(project_path / pdf_path)
        if fonts:
            warnings.append(fonts)
    for d in upload_sim["large_dirs"]:
        warnings.append(f"unexpected top-level dir '{d['name']}/' is {d['size_mb']} MB — will be uploaded")

    return {
        "project_path": str(project_path),
        "ready_to_upload": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "chapters": chapters,
        "config_files": config_files,
        "optional_files": optional_files,
        "pdf_path": pdf_path,
        "covers": covers,
        "author": {
            "bio_found": author["bio_found"],
            "bio_size_chars": author["bio_size_chars"],
            "photo_found": author["photo_found"],
            "photo_size_bytes": author["photo_size_bytes"],
            "conflicts": author["conflicts"],
        },
        "upload_simulation": upload_sim,
    }


def format_text(report: dict) -> str:
    lines = [f"📂 {report['project_path']}", ""]

    lines.append(f"Chapters: {len(report['chapters'])}")
    for c in report["chapters"][:10]:
        lines.append(f"  - {c}")
    if len(report["chapters"]) > 10:
        lines.append(f"  … and {len(report['chapters']) - 10} more")
    lines.append("")

    lines.append("Required files:")
    for f, present in report["config_files"].items():
        mark = "✓" if present else "✗"
        lines.append(f"  {mark} {f}")
    lines.append("")

    lines.append("Optional files:")
    for f, present in report["optional_files"].items():
        mark = "✓" if present else "·"
        lines.append(f"  {mark} {f}")
    lines.append("")

    lines.append(f"PDF: {report['pdf_path'] or '(none — render with `quarto render --to pdf`)'}")
    lines.append("")

    lines.append("Covers:")
    for k, v in report["covers"].items():
        mark = "✓" if v else "·"
        lines.append(f"  {mark} {k}")
    lines.append("")

    author = report["author"]
    lines.append("Author:")
    if author["bio_found"]:
        lines.append(f"  ✓ author.md ({author['bio_size_chars']} chars)")
    else:
        lines.append("  · author.md")
    if author["photo_found"]:
        kb = (author["photo_size_bytes"] or 0) / 1024
        lines.append(f"  ✓ {author['photo_found']} ({kb:.1f} KB)")
    else:
        lines.append("  · author.jpg / author.png")
    lines.append("")

    if report["ready_to_upload"]:
        lines.append("✅ Ready to upload.")
    else:
        lines.append("❌ Not ready. Blockers:")
        for b in report["blockers"]:
            lines.append(f"  - {b}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a folder for ProofBound upload")
    parser.add_argument("--project-path", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    if not args.project_path.exists():
        print(f"❌ Path does not exist: {args.project_path}", file=sys.stderr)
        return 2
    if not args.project_path.is_dir():
        print(f"❌ Path is not a directory: {args.project_path}", file=sys.stderr)
        return 2

    report = build_report(args.project_path.resolve())

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text(report))

    return 0 if report["ready_to_upload"] else 1


if __name__ == "__main__":
    sys.exit(main())
