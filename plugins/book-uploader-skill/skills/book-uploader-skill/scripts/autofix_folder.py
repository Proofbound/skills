#!/usr/bin/env python3
"""Generate a minimal _quarto.yml and cc-config.yml from loose .qmd files.

Phase 2 of the book-uploader-skill. Light auto-fix only:
- Adds chapters list from existing .qmd files (skips special files like index/summary/references/colophon
  unless they're actually on disk).
- Emits a simpler PDF format than the canonical cc-template — uses scrbook + pdflatex defaults so
  it renders without XeLaTeX or Crimson Pro/Inter fonts. The platform applies full ProofBound styling
  on rebuild. Interior page geometry is derived from the --trim-size flag (default 6x9).
- Writes trim_size into cc-config.yml — the one cc-config key the server's staged render
  reads to size the page, the cover and the library row. Without it a project typeset for
  A5 quietly gets a 6x9 row and cover.
- Gives every chapter .qmd that has no title of its own (no front-matter ``title:`` and no
  top-level ``#`` heading) a ``title:`` front-matter block — from outline.yml when a chapter
  number matches, otherwise from the filename. The server only writes chapter titles on the
  compose paths; on upload/render a titleless chapter renders as an untitled chapter.
- Refuses to overwrite existing config files.

Usage:
    uv run python scripts/autofix_folder.py \
        --project-path /path/to/folder \
        --title "My Book" \
        --author "Author Name" \
        [--subtitle "Optional"] \
        [--trim-size 6x9|a5|digest] \
        [--book-type nonfiction_practical|...]

A cc-config.yml without a ``book_type`` gets one written (default
``nonfiction_practical``) and the output says so: the server requires it.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _quarto_output import output_dirs  # noqa: E402

# The exclusion/author/trim policy is shared with the server through
# proofbound-portable, vendored beside these scripts -- a real tree in the shipped
# zip, a symlink in a development checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))

from proofbound_portable.book_info import BOOK_TYPES, DEFAULT_BOOK_TYPE  # noqa: E402
from proofbound_portable.interior_margins import DEFAULT_INNER_IN, plan_geometry  # noqa: E402
from proofbound_portable.trim import DEFAULT_TRIM_SIZE, TRIM_SIZES  # noqa: E402

SPECIAL_FRONT_BACK = ["index.qmd", "summary.qmd", "references.qmd", "colophon.qmd"]

# Trim sizes are the server's own catalog (proofbound_portable.trim); only the
# interior page geometry is used here -- cover-template and Lulu-SKU selection
# happen server-side on rebuild.


def geometry_for(trim_size: str) -> str:
    """Return a Quarto/LaTeX ``geometry`` string for the given trim size key."""
    return plan_geometry(None, TRIM_SIZES[trim_size], DEFAULT_INNER_IN).geometry


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "book"


def _leading_number(name: str) -> tuple[int, str]:
    """Sort key: (number, full-name). Files without leading digits sort last."""
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 10**9, name)


def discover_chapters(project_path: Path) -> list[str]:
    """Return chapter file paths relative to project_path, ordered by leading number then alpha."""
    chapters_dir = project_path / "chapters"
    if chapters_dir.is_dir():
        files = list(chapters_dir.glob("*.qmd"))
    else:
        files = [p for p in project_path.glob("*.qmd") if p.name not in SPECIAL_FRONT_BACK]

    files.sort(key=lambda p: _leading_number(p.name))
    return [str(p.relative_to(project_path)) for p in files]


def build_quarto_yml(
    project_path: Path,
    title: str,
    author: str,
    subtitle: str | None,
    chapters: list[str],
    trim_size: str = DEFAULT_TRIM_SIZE,
) -> dict:
    """Return a Python dict ready to be yaml.dump'd as _quarto.yml.

    Uses Quarto/pdflatex-friendly defaults so the project renders without XeLaTeX or
    custom fonts. The platform's customization pipeline applies full ProofBound styling
    on server-side rebuild.
    """
    book_chapters: list[str] = []
    if (project_path / "index.qmd").exists():
        book_chapters.append("index.qmd")
    book_chapters.extend(chapters)
    if (project_path / "summary.qmd").exists():
        book_chapters.append("summary.qmd")
    if (project_path / "references.qmd").exists():
        book_chapters.append("references.qmd")
    if (project_path / "colophon.qmd").exists():
        book_chapters.append("colophon.qmd")

    book: dict = {
        "title": title,
        "author": author,
        "date": date.today().isoformat(),
        "search": True,
        "chapters": book_chapters,
    }
    if subtitle:
        book["subtitle"] = subtitle
    if (project_path / "covers" / "cover_front_artwork.png").exists():
        book["cover-image"] = "covers/cover_front_artwork.png"

    config: dict = {
        "project": {"type": "book"},
        "book": book,
        "format": {
            "html": {
                "theme": "cosmo",
                "toc": True,
                "number-sections": True,
                "number-depth": 1,
            },
            "pdf": {
                "output-basename": slugify(title),
                "documentclass": "scrbook",
                "papersize": "Letter",
                "geometry": geometry_for(trim_size),
                "toc": True,
                "number-sections": True,
                "number-depth": 1,
            },
        },
    }
    if (project_path / "references.bib").exists():
        config["bibliography"] = "references.bib"
        config["nocite"] = "@*"

    return config


def build_cc_config(
    title: str,
    author: str,
    subtitle: str | None,
    trim_size: str = DEFAULT_TRIM_SIZE,
    book_type: str = DEFAULT_BOOK_TYPE,
) -> dict:
    today = date.today().isoformat()
    config: dict = {
        "title": title,
        "author": author,
        "date": today,
        "year": today[:4],
        "page_count": 0,
    }
    if subtitle:
        config["subtitle"] = subtitle
    # The server's staged render reads trim_size from cc-config.yml (not from the
    # _quarto.yml geometry) to size the cover and the library row. Declare the same
    # trim the geometry above was built from, or the two silently disagree.
    config["trim_size"] = trim_size
    # The server requires a book_type and never picks one for the author.
    config["book_type"] = book_type
    config["page_footer"] = f"Copyright {today[:4]} {author}"
    config["publisher"] = "Proofbound"
    config["edition"] = "First Edition"
    config["printing"] = "First printing"
    config["isbn"] = "[To be assigned]"
    config["cover_designer"] = "Proofbound Press"
    return config


def ensure_book_type(cc_yml: Path, book_type: str = DEFAULT_BOOK_TYPE) -> bool:
    """Make sure an existing ``cc-config.yml`` declares a ``book_type``.

    The server rejects a book with no ``book_type`` rather than guessing one. A
    textual edit (not a YAML round trip) so the author's comments and ordering
    survive. Returns True when the file was changed; an existing non-blank value,
    a file that is not a block-style YAML mapping, or an edit that would not parse
    back to the intended value, leaves the file untouched.
    """
    text = cc_yml.read_text(encoding="utf-8")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        return False
    if str(loaded.get("book_type") or "").strip():
        return False
    line = f"book_type: {book_type}"
    if re.search(r"^book_type\s*:", text, flags=re.MULTILINE):
        text = re.sub(r"^book_type\s*:.*$", line, text, count=1, flags=re.MULTILINE)
    else:
        text = text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    # A layout the textual edit cannot extend (a flow mapping such as ``{a: 1}``)
    # would turn valid YAML into invalid YAML: verify, else leave the file alone.
    try:
        edited = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if not isinstance(edited, dict) or edited.get("book_type") != book_type:
        return False
    cc_yml.write_text(text, encoding="utf-8")
    return True


_FRONT_MATTER_WINDOW = 200


def _split_front_matter(content: str) -> tuple[dict, str] | None:
    """Return ``(front_matter, body)`` when *content* opens with a YAML block, else None."""
    lines = content.split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].rstrip() != "---":
        return None
    for end in range(start + 1, min(len(lines), start + 1 + _FRONT_MATTER_WINDOW)):
        if lines[end].rstrip() in ("---", "..."):
            try:
                parsed = yaml.safe_load("\n".join(lines[start + 1 : end]))
            except yaml.YAMLError:
                return None
            if parsed is None:
                parsed = {}
            return (parsed, "\n".join(lines[end + 1 :])) if isinstance(parsed, dict) else None
    return None


def _has_h1(text: str) -> bool:
    """True when *text* has a top-level ``# `` heading outside fenced code."""
    in_fence = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            return True
    return False


def chapter_has_title(content: str) -> bool:
    """True when a chapter file already declares a title Quarto will use."""
    split = _split_front_matter(content)
    if split is not None:
        meta, body = split
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return True
        return _has_h1(body)
    return _has_h1(content)


def outline_titles(project_path: Path) -> dict[int, str]:
    """``number → chapter_title`` from ``outline.yml``, or ``{}`` when absent/unusable."""
    path = project_path / "outline.yml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list):
        return {}
    titles: dict[int, str] = {}
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        number = ch.get("number", i + 1)
        title = ch.get("chapter_title", ch.get("title", ""))
        if isinstance(number, int) and isinstance(title, str) and title.strip():
            titles[number] = title.strip()
    return titles


def title_from_filename(name: str) -> str:
    """``chapter_03-getting-started.qmd`` → ``Getting Started``; ``chapter_03.qmd`` → ``Chapter 3``."""
    stem = Path(name).stem
    m = re.search(r"(\d+)", stem)
    number = int(m.group(1)) if m else None
    rest = re.sub(r"^[^a-zA-Z]*chapter[^a-zA-Z]*", "", stem, flags=re.IGNORECASE)
    rest = re.sub(r"\d+", " ", rest)
    words = [w for w in re.split(r"[-_\s]+", rest) if w]
    if words:
        return " ".join(w[:1].upper() + w[1:] for w in words)
    return f"Chapter {number}" if number is not None else stem


def retire_stale_pdfs(project_path: Path) -> list[str]:
    """Rename every rendered PDF in the output dirs to ``<name>.pdf.stale``; return them.

    Called only after chapter sources were changed. The uploader picks
    ``POST /books/upload`` whenever it finds a rendered PDF, so a PDF rendered
    BEFORE the titles were added would be persisted as the finished book — with
    the very untitled chapters this fix removed. Renaming (not deleting) keeps the
    old render recoverable and forces a fresh render or the server-side path.
    """
    retired: list[str] = []
    for out in output_dirs(project_path):
        if not out.is_dir():
            continue
        for pdf in sorted(out.glob("*.pdf")):
            target = pdf.with_name(pdf.name + ".stale")
            pdf.rename(target)
            retired.append(str(target.relative_to(project_path)))
    return retired


def backfill_chapter_titles(project_path: Path, chapters: list[str]) -> list[tuple[str, str]]:
    """Give each titleless chapter file ``title:`` front matter; return ``(file, title)`` pairs.

    A file that already carries a front-matter ``title:`` or a top-level ``#`` heading is
    left exactly as it is. The title comes from ``outline.yml`` when the file's leading
    number matches a chapter there, otherwise from the filename. Idempotent.
    """
    titles = outline_titles(project_path)
    fixed: list[tuple[str, str]] = []
    for rel in chapters:
        path = project_path / rel
        content = path.read_text(encoding="utf-8")
        if chapter_has_title(content):
            continue
        m = re.search(r"(\d+)", path.stem)
        title = titles.get(int(m.group(1))) if m else None
        if not title:
            title = title_from_filename(path.name)
        split = _split_front_matter(content)
        if split is None:
            # A JSON string literal is a valid YAML double-quoted scalar, escapes
            # included; a hand-rolled replace('"', '\\"') leaves backslashes to be
            # read as YAML escapes.
            content = f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\n---\n\n{content}"
        else:
            meta, body = split
            meta = {"title": title, **{k: v for k, v in meta.items() if k != "title"}}
            block = yaml.dump(meta, sort_keys=False, allow_unicode=True).rstrip("\n")
            content = f"---\n{block}\n---\n{body}"
        path.write_text(content, encoding="utf-8")
        fixed.append((rel, title))
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-fix a folder for ProofBound upload")
    parser.add_argument("--project-path", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--author", required=True)
    parser.add_argument("--subtitle", default=None)
    parser.add_argument(
        "--trim-size",
        choices=sorted(TRIM_SIZES),
        default=DEFAULT_TRIM_SIZE,
        help="Interior page trim size (default: %(default)s)",
    )
    parser.add_argument(
        "--book-type",
        choices=BOOK_TYPES,
        default=DEFAULT_BOOK_TYPE,
        help="Structural kind of book, written to cc-config.yml when absent (default: %(default)s)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files (off by default)")
    args = parser.parse_args()

    project = args.project_path.resolve()
    if not project.is_dir():
        print(f"❌ Not a directory: {project}", file=sys.stderr)
        return 2

    chapters = discover_chapters(project)
    if not chapters:
        print(f"❌ No chapter .qmd files found in {project}/chapters/ or {project}/*.qmd", file=sys.stderr)
        return 2

    quarto_yml = project / "_quarto.yml"
    cc_yml = project / "cc-config.yml"

    wrote: list[str] = []
    skipped: list[str] = []

    if quarto_yml.exists() and not args.force:
        skipped.append("_quarto.yml (already exists)")
    else:
        data = build_quarto_yml(project, args.title, args.author, args.subtitle, chapters, args.trim_size)
        quarto_yml.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        wrote.append("_quarto.yml")

    if cc_yml.exists() and not args.force:
        skipped.append("cc-config.yml (already exists)")
        if ensure_book_type(cc_yml, args.book_type):
            wrote.append(f"cc-config.yml book_type: {args.book_type} (it had none)")
    else:
        data = build_cc_config(args.title, args.author, args.subtitle, args.trim_size, args.book_type)
        cc_yml.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        wrote.append(f"cc-config.yml (book_type: {args.book_type})")

    titled = backfill_chapter_titles(project, chapters)
    retired = retire_stale_pdfs(project) if titled else []

    print(f"📂 {project}")
    print(f"📐 Trim size: {args.trim_size} ({geometry_for(args.trim_size)})")
    print(f"📚 Discovered {len(chapters)} chapter file(s)")
    for c in chapters[:5]:
        print(f"   - {c}")
    if len(chapters) > 5:
        print(f"   … and {len(chapters) - 5} more")

    if wrote:
        print(f"✓ Wrote: {', '.join(wrote)}")
    if skipped:
        print(f"· Skipped: {', '.join(skipped)}")
    if titled:
        print(f"✓ Added a title to {len(titled)} chapter file(s) that had none:")
        for rel, title in titled:
            print(f"   - {rel} → {title}")
        print("   (edit the title: line in each file if you want a different one)")
    if retired:
        print(f"⚠ Set aside {len(retired)} rendered PDF(s) that predate those titles:")
        for rel in retired:
            print(f"   - {rel}")
        print("   Re-render before uploading, or the server will render for you.")

    if not any(d.exists() for d in output_dirs(project)):
        print()
        print("Next: render the PDF before uploading:")
        print(f"  cd {project}")
        print("  quarto render --to pdf")

    return 0


if __name__ == "__main__":
    sys.exit(main())
