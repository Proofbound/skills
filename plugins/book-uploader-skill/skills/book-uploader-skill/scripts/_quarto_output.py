"""Where Quarto writes rendered output for a project.

Minimal, self-contained mirror of
``proofbound_quarto.config.resolve_output_dir`` and of the search
order ``zip_intake.validate_project_manifest`` applies. Vendored rather than
imported because this skill installs without a monorepo checkout. If the
server's semantics change, change these too.

Quarto lets a project override the default ``_book/`` via ``project.output-dir``
in ``_quarto.yml``. Without this, the skill's local PDF gate would reject a
perfectly valid project that the server would have accepted.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

#: Searched when ``_quarto.yml`` names no ``project.output-dir``, and *also*
#: searched after a configured one — the server keeps this legacy fallback, and
#: the client must not reject a project the server would accept.
DEFAULT_OUTPUT_DIRS = ("_book", "docs")


class QuartoOutputDirError(ValueError):
    """``project.output-dir`` in ``_quarto.yml`` escapes the project directory."""


class AmbiguousPdfError(ValueError):
    """More than one PDF in the output directory that would supply the book."""


def configured_output_dir(project_path: Path) -> PurePosixPath | None:
    """``project.output-dir`` from ``_quarto.yml``, normalized and project-relative.

    ``None`` when the key, the file, or its ``project`` block is absent,
    unreadable, or malformed — those fall back to the defaults. The value is
    normalized (``./out`` and ``stage/../out`` both come back as ``out``) so
    callers can compare it against walked paths and use it verbatim as a ZIP
    entry name. Raises :class:`QuartoOutputDirError` for a value that is
    absolute or traverses outside *project_path*: Quarto may genuinely write
    there, but PDF discovery must not follow it out of the project we are about
    to upload.
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
    project_cfg = config.get("project")
    if not isinstance(project_cfg, dict):
        return None
    raw = project_cfg.get("output-dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    # Quarto configs are POSIX-spelled; tolerate a Windows-authored one rather
    # than treating "build\out" as a single directory named "build\out".
    raw = raw.strip().replace("\\", "/")

    root = project_path.resolve()
    candidate = (root / raw).resolve()
    if candidate == root or root not in candidate.parents:
        raise QuartoOutputDirError(
            f"_quarto.yml project.output-dir {raw!r} does not name a directory " "inside the project"
        )
    return PurePosixPath(candidate.relative_to(root).as_posix())


def output_dir_names(project_path: Path) -> tuple[str, ...]:
    """Output directory names to search, project-relative, in server order.

    The configured ``output-dir`` first (when set), then the legacy defaults —
    the same search order ``zip_intake.validate_project_manifest`` uses, so a
    project this client accepts is one the server accepts. Never raises: an
    escaping value degrades to the defaults, so callers that only want "which
    dirs are build output here?" don't have to handle the error. Callers that
    must *report* a hostile value call :func:`configured_output_dir` directly.
    """
    try:
        configured = configured_output_dir(project_path)
    except QuartoOutputDirError:
        return DEFAULT_OUTPUT_DIRS
    if configured is None:
        return DEFAULT_OUTPUT_DIRS
    name = configured.as_posix()
    return (name, *(d for d in DEFAULT_OUTPUT_DIRS if d != name))


def output_dirs(project_path: Path) -> list[Path]:
    """Candidate output directories, as paths under *project_path*."""
    return [project_path / name for name in output_dir_names(project_path)]


def is_output_path(rel_parts: tuple[str, ...], output_names: tuple[str, ...]) -> bool:
    """Whether a project-relative path lies inside one of *output_names*.

    Prefix-matched on whole path components, so a nested ``output-dir:
    build/out`` excludes only that subtree — never its ``build/`` siblings.
    """
    rel = PurePosixPath(*rel_parts)
    return any(rel.is_relative_to(PurePosixPath(name)) for name in output_names)


def describe_output_dirs(project_path: Path) -> str:
    """Human-readable list of the searched dirs, e.g. ``_book/ or docs/``."""
    return " or ".join(f"{name}/" for name in output_dir_names(project_path))


def find_pdf(project_path: Path) -> Path | None:
    """The PDF in the first output directory holding any, or ``None``.

    Raises :class:`AmbiguousPdfError` when that directory holds several, as the
    server's intake does: picking by name would let a stale render, a sample
    chapter, or a cover proof ship as the book.
    """
    for out in output_dirs(project_path):
        if out.is_dir():
            pdfs = sorted(out.glob("*.pdf"))
            if len(pdfs) > 1:
                rel = out.relative_to(project_path).as_posix()
                names = ", ".join(p.name for p in pdfs)
                raise AmbiguousPdfError(f"multiple PDFs in {rel}/ ({names}); keep only the rendered book PDF")
            if pdfs:
                return pdfs[0]
    return None
