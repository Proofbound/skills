"""Shared helpers for reading, normalizing, and rewriting a project's `_quarto.yml`.

This is the single home for *generic* Quarto-config mechanics — loading, shape
normalization (`format`, `header-includes`), chapter traversal, output discovery,
and atomic rewriting. Book-specific policy (what LaTeX to inject, which branding
to enforce) lives in `services/book_project/`, which builds on these primitives;
there must not be a second competing Quarto-YAML abstraction.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

DEFAULT_OUTPUT_DIR = "_book"

CHAPTER_FILE_SUFFIXES = (".qmd", ".md")

#: Front/back matter that is never the book's "first chapter" for preview purposes.
NON_CHAPTER_FILENAMES = frozenset(
    {"index.qmd", "summary.qmd", "references.qmd", "colophon.qmd"}
)


class QuartoOutputDirError(ValueError):
    """`project.output-dir` in `_quarto.yml` escapes the project directory."""


def load_quarto_config(project_dir: Path) -> tuple[Path, dict]:
    """Load a project's ``_quarto.yml``, returning ``(path, parsed config)``.

    Invalid YAML raises ``ValueError`` rather than propagating the raw
    ``yaml.YAMLError``, so callers fail before the expensive render with a
    message naming the offending file.
    """
    quarto_path = project_dir / "_quarto.yml"
    try:
        config = yaml.safe_load(quarto_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{quarto_path}: invalid YAML: {exc}") from exc
    return quarto_path, config


def atomic_yaml_dump(path: Path, config: dict) -> None:
    """Write ``config`` to ``path`` as YAML, atomically.

    Serializes with ``allow_unicode=True`` (titles/authors/manuscript metadata are
    UTF-8, not just ASCII) and ``sort_keys=False`` (preserve the meaningful key
    order the caller built). Writes to a tempfile created in ``path.parent`` (same
    filesystem, so the final ``os.replace`` is a single atomic rename — no window
    where a concurrent render could read a partially written file), flushes +
    fsyncs before the rename, and preserves the original file's mode.

    Rewriting the whole document from the parsed structure means YAML comments,
    anchors, and formatting in the source are lost on every write — an accepted
    tradeoff (these are server-generated configs, not hand-maintained ones), the
    same one the pre-existing colophon injection already made.
    """
    original_mode = path.stat().st_mode if path.exists() else None
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                config,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            f.flush()
            os.fsync(f.fileno())
        if original_mode is not None:
            os.chmod(tmp_name, original_mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def normalize_header_includes(raw: object) -> list:
    """Canonicalize a ``header-includes`` value to Quarto's list form.

    ``None``/absent -> ``[]``; a bare string -> ``[string]``; a single mapping
    (e.g. ``{text: ...}``) -> ``[mapping]`` (preserved, not flattened — Quarto's
    own mapping-entry form); a list is kept as-is. Anything else is malformed
    input we must not silently coerce -> ``ValueError``, so a broken
    ``_quarto.yml`` fails before the expensive render rather than mid-render.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return raw
    raise ValueError(
        f"header-includes must be a string, mapping, list, or absent; "
        f"got {type(raw).__name__}: {raw!r}"
    )


def header_entry_has_marker(entry: object, marker: str) -> bool:
    """Whether one ``header-includes``/``include-in-header`` entry carries *marker*.

    Marker scan tolerant of both entry shapes :func:`normalize_header_includes`
    yields: a raw string, or a mapping (e.g. ``{text: ...}``) whose string values
    are scanned. Anything else carries nothing.
    """
    if isinstance(entry, str):
        return marker in entry
    if isinstance(entry, dict):
        return any(isinstance(v, str) and marker in v for v in entry.values())
    return False


def normalize_format_map(config: dict) -> dict:
    """Normalize ``config["format"]`` to the nested ``{name: {...}}`` map shape.

    Quarto accepts several equivalent spellings of ``format`` in ``_quarto.yml``;
    this canonicalizes all of them so downstream code only ever deals with one
    shape. Mutates and returns ``config["format"]``. Fails fast (``ValueError``)
    on anything unparseable, *before* the expensive render — never silently drops
    or guesses at malformed input.

    - ``config`` itself must be a mapping (a parsed ``_quarto.yml`` root); else
      ``ValueError``.
    - ``format`` absent/``None`` -> ``{}``; a bare string -> ``{name: {}}``.
    - ``format`` as a list: string entries -> ``{name: {}}``; mapping entries are
      **merged** into the result (not dropped), so ``[pdf, {html: {...}}]`` ->
      ``{pdf: {}, html: {...}}``; any other entry type -> ``ValueError``.
    - Each format's value: a mapping is kept; a string (e.g. ``pdf: default``) or
      ``None`` -> ``{}``; any other type -> ``ValueError``.
    - Wherever a format's value already carries a ``header-includes`` key, its
      shape is normalized in place via :func:`normalize_header_includes`. A
      format that has no ``header-includes`` key is left untouched — this
      function never invents one, so formats nobody is injecting into come out
      byte-for-byte as they went in.
    """
    if not isinstance(config, dict):
        raise ValueError(
            f"_quarto.yml root must be a mapping, got {type(config).__name__}"
        )

    fmt = config.get("format")
    if fmt is None:
        fmt = {}
    elif isinstance(fmt, str):
        fmt = {fmt: {}}
    elif isinstance(fmt, list):
        merged: dict = {}
        for entry in fmt:
            if isinstance(entry, str):
                merged.setdefault(entry, {})
            elif isinstance(entry, dict):
                merged.update(entry)
            else:
                raise ValueError(
                    f"format list entry must be a string or mapping, got "
                    f"{type(entry).__name__}: {entry!r}"
                )
        fmt = merged
    elif not isinstance(fmt, dict):
        raise ValueError(
            f"format must be a string, list, mapping, or absent, got "
            f"{type(fmt).__name__}"
        )

    normalized: dict = {}
    for name, value in fmt.items():
        if isinstance(value, dict):
            normalized[name] = value
        elif value is None or isinstance(value, str):
            normalized[name] = {}
        else:
            raise ValueError(
                f"format.{name} must be a mapping, string, or null, got "
                f"{type(value).__name__}"
            )
        if "header-includes" in normalized[name]:
            normalized[name]["header-includes"] = normalize_header_includes(
                normalized[name]["header-includes"]
            )

    config["format"] = normalized
    return normalized


def ensure_pdf_format_block(config: dict) -> dict:
    """Normalize ``format`` and return ``format.pdf``, creating it when absent.

    Mutates ``config`` in place: a missing, null, or shorthand ``pdf`` value (e.g.
    ``pdf: default``) is replaced with an empty mapping so callers can write into
    it unconditionally. Genuinely malformed values raise from
    :func:`normalize_format_map` rather than being coerced.
    """
    fmt = normalize_format_map(config)
    pdf = fmt.get("pdf")
    if not isinstance(pdf, dict):
        pdf = {}
        fmt["pdf"] = pdf
    return pdf


@dataclass(frozen=True)
class RenderedDocument:
    """One document Quarto renders, with the structural context it was found in.

    ``section`` is the ``book`` key the document was reached through —
    ``"chapters"`` or ``"appendices"``. ``depth`` is 0 for an entry listed
    directly in that list and 1 for one nested inside a ``part``. ``is_part`` is
    true when the document *is* the ``part:`` value rather than an entry of the
    part's own ``chapters`` list.

    Callers that need book policy (e.g. "which colophon does render branding
    overwrite?") derive it from these fields rather than re-walking the config —
    that policy stays out of this generic layer.
    """

    path: str
    section: str
    depth: int
    is_part: bool


def iter_rendered_documents(config: object) -> list[RenderedDocument]:
    """Flatten every document a parsed ``_quarto.yml`` renders, in render order.

    Quarto lets a book group chapters into parts, so ``book.chapters`` is not a
    flat list of filenames — a ``{part: ..., chapters: [...]}`` mapping may
    appear anywhere in it::

        book:
          chapters:
            - index.qmd
            - part: The Place          # a bare LABEL — not a file
              chapters: [places/a.qmd]
            - part: parts/two.qmd      # a part can also BE a file
              chapters: [b.qmd]
          appendices: [appendix-a.qmd]

    ``book.chapters`` is walked first, then ``book.appendices``; both may carry
    parts. A ``part`` value is yielded only when it names a ``.qmd``/``.md``
    file — a bare part label is a heading, not a document, and must never be
    treated as one. Non-string, non-part entries are skipped, as is a ``book``
    that is not a mapping.
    """
    out: list[RenderedDocument] = []
    if not isinstance(config, dict):
        return out
    book = config.get("book")
    if not isinstance(book, dict):
        return out

    def _is_file(entry: object) -> bool:
        return isinstance(entry, str) and entry.lower().endswith(CHAPTER_FILE_SUFFIXES)

    def _walk(entries: object, *, section: str, depth: int) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            if _is_file(entry):
                out.append(RenderedDocument(entry, section, depth, is_part=False))
            elif isinstance(entry, dict) and "part" in entry:
                part_value = entry.get("part")
                if _is_file(part_value):
                    out.append(
                        RenderedDocument(part_value, section, depth, is_part=True)
                    )
                _walk(entry.get("chapters"), section=section, depth=depth + 1)

    _walk(book.get("chapters"), section="chapters", depth=0)
    _walk(book.get("appendices"), section="appendices", depth=0)
    return out


def iter_chapter_files(config: object) -> list[str]:
    """Flatten ``book.chapters`` from a parsed ``_quarto.yml`` into file paths.

    Chapter files in render order, parts flattened (see
    :func:`iter_rendered_documents`, of which this is the ``book.chapters``
    view). ``book.appendices`` is deliberately not included.
    """
    return [
        doc.path for doc in iter_rendered_documents(config) if doc.section == "chapters"
    ]


def iter_manuscript_files(project_dir: Path) -> list[Path]:
    """Every manuscript source file under *project_dir*.

    Quarto renders a chapter listed in ``_quarto.yml`` whether it is ``.qmd`` or
    ``.md`` (see :data:`CHAPTER_FILE_SUFFIXES`), so any step that processes
    manuscript text — NFC normalization, bare-URL autolinking, word counts —
    must reach both or it silently skips ``.md`` chapters. The two suffixes are
    found by **different rules**, because they carry different risk:

    - Every ``.qmd`` under the tree, wherever it sits. The suffix is
      Quarto's own, so a ``.qmd`` in a project is manuscript by construction —
      including partials under ``includes/`` that a chapter pulls in, which no
      ``_quarto.yml`` entry names.
    - A ``.md`` only when ``_quarto.yml`` renders it. Markdown is the project's
      general-purpose suffix, and a project carries plenty that is not
      manuscript: ``blurb.md`` (back-cover copy), ``author.md`` (bio), the
      ``covers/README.md`` packaging instructions written by
      ``cover_steps.write_book_pipeline_local_files``, and whatever notes a
      bring-your-own project ships. Treating those as manuscript inflates word
      counts and — because the rewriters are persisted on the render path —
      would autolink a bare URL in back-cover copy into literal angle brackets
      on a printed cover. A denylist of known filenames cannot hold that line;
      what the book renders can.

    A project with no readable ``_quarto.yml`` yields its ``.qmd`` files alone:
    without the manifest there is nothing that distinguishes a ``.md`` chapter
    from a stray note, and over-reaching is the harmful direction.
    """
    found = [p for p in project_dir.rglob("*.qmd") if p.is_file()]

    try:
        _, config = load_quarto_config(project_dir)
    except (OSError, ValueError):
        return sorted(found)

    root = project_dir.resolve()
    rendered: set[Path] = set()
    for doc in iter_rendered_documents(config):
        if not doc.path.lower().endswith(".md"):
            continue
        candidate = project_dir / doc.path
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        # A manifest entry is untrusted input on the upload paths: keep it inside
        # the project, and require it to exist before handing it to a rewriter.
        if resolved.is_relative_to(root) and resolved.is_file():
            rendered.add(candidate)

    return sorted(found + list(rendered))


def iter_chapter_files_on_disk(project_dir: Path) -> list[Path]:
    """``chapter-*`` documents at *project_dir*'s root, in either suffix.

    Root-only (``glob``, not ``rglob``) — this mirrors the ``chapter-*``
    filename convention our book projects write chapters under directly in
    the project root, not the ``book.chapters`` structure in ``_quarto.yml``
    (see :func:`iter_chapter_files` for that view). Matches both
    :data:`CHAPTER_FILE_SUFFIXES` so a ``.md`` chapter is found the same as a
    ``.qmd`` one.
    """
    return sorted(
        (
            p
            for p in project_dir.glob("chapter-*")
            if p.is_file() and p.suffix.lower() in CHAPTER_FILE_SUFFIXES
        ),
        key=lambda p: p.name,
    )


def find_first_chapter(config: object) -> str | None:
    """The first real chapter file in ``book.chapters``, skipping front/back matter.

    Parts-aware (see :func:`iter_chapter_files`). Matching is on the *basename*,
    so a chapter nested under a part directory (``partone/colophon.qmd``) is
    skipped the same way a top-level one is. Returns ``None`` when the book has
    no chapter beyond front/back matter.
    """
    for path in iter_chapter_files(config):
        if PurePosixPath(path).name.lower() not in NON_CHAPTER_FILENAMES:
            return path
    return None


def resolve_output_dir(project_path: Path) -> Path:
    """Resolve the directory Quarto writes rendered output to.

    Reads ``project.output-dir`` from ``_quarto.yml`` when present (Quarto lets
    a project override the default ``_book/`` — e.g. ``output-dir: docs``).
    Falls back to the default when the key/file is absent, unreadable, or
    malformed. Raises :class:`QuartoOutputDirError` when the configured value
    is absolute or traverses outside *project_path* — never a silent fallback
    for those, since Quarto may genuinely write elsewhere and PDF discovery
    must not follow it out of the staged project.
    """
    quarto_yml = project_path / "_quarto.yml"
    config = None
    if quarto_yml.is_file():
        try:
            config = yaml.safe_load(quarto_yml.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeError):
            config = None
    if isinstance(config, dict):
        project_cfg = config.get("project")
        if isinstance(project_cfg, dict):
            output_dir = project_cfg.get("output-dir")
            if isinstance(output_dir, str) and output_dir.strip():
                root = project_path.resolve()
                candidate = (root / output_dir).resolve()
                if candidate != root and root not in candidate.parents:
                    raise QuartoOutputDirError(
                        f"output-dir {output_dir!r} escapes the project directory"
                    )
                return candidate
    return (project_path / DEFAULT_OUTPUT_DIR).resolve()


def read_pdf_output_basename(project_path: Path) -> str | None:
    """Return ``format.pdf.output-basename`` from ``_quarto.yml``, if set.

    Our book template always sets this (to a slug of the title), so the rendered
    PDF's filename is knowable rather than something to go hunting for. Returns
    None when the file/key is absent or malformed — callers fall back to a glob.
    """
    quarto_yml = project_path / "_quarto.yml"
    if not quarto_yml.is_file():
        return None
    try:
        config = yaml.safe_load(quarto_yml.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeError):
        return None
    if not isinstance(config, dict):
        return None
    fmt = config.get("format")
    if not isinstance(fmt, dict):
        return None
    pdf_cfg = fmt.get("pdf")
    if not isinstance(pdf_cfg, dict):
        return None
    basename = pdf_cfg.get("output-basename")
    if isinstance(basename, str) and basename.strip():
        # Guard against a config that could point discovery at another directory.
        return PurePosixPath(basename.strip()).name or None
    return None


def resolve_rendered_pdf(project_path: Path) -> Path:
    """Return the PDF Quarto rendered for *project_path*.

    Prefers the exact file named by ``format.pdf.output-basename`` — the config
    knows the filename, so there is no reason to guess at it. Falls back to a
    glob of the output dir only when that file is absent (a project whose config
    predates the key, or a hand-built one).

    Deliberately raises rather than picking arbitrarily: the result is uploaded
    over the canonical ``book.pdf``, and ``glob()`` returns directory order, so
    "just take the first" could silently publish a stray PDF as the book.

    Raises:
        FileNotFoundError: no PDF in the output dir.
        ValueError: the basename is unknown and the fallback is ambiguous.
    """
    output_dir = resolve_output_dir(project_path)

    basename = read_pdf_output_basename(project_path)
    if basename:
        expected = output_dir / f"{basename}.pdf"
        if expected.is_file():
            return expected

    candidates = sorted(p for p in output_dir.glob("*.pdf") if p.is_file())
    if not candidates:
        raise FileNotFoundError(f"No PDF was produced in {output_dir}")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise ValueError(
            f"Ambiguous PDF output in {output_dir}: expected "
            f"{basename + '.pdf' if basename else 'a single PDF'}, found {names}"
        )
    return candidates[0]
