"""Which files an upload ships: the one rule both the zip and the dry run use.

``upload.build_upload_zip`` writes these files and ``validate_folder.simulate_upload``
sizes them. Keeping the rule here is what makes the dry run's file count and MB
match the real zip (bead 3y3a): the simulation used to skip the extension
allowlist and leave out the PDF the upload adds back.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

from _quarto_output import is_output_path, output_dir_names

# The exclusion/author/trim policy is shared with the server through
# proofbound-portable, vendored beside these scripts -- a real tree in the shipped
# zip, a symlink in a development checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
from proofbound_portable.upload import (  # noqa: E402
    ALLOWED_EXTENSIONS,
    DEFAULT_EXCLUDE_DIRS,
    is_excluded_project_path,
)


class SymlinkEscapeError(ValueError):
    """A file in the upload resolves (via a symlink) to a path outside the project."""


def _require_inside(project_root: Path, file_path: Path, rel: Path) -> None:
    """Raise unless *file_path*'s real location is under *project_root* (resolved).

    ``ZipFile.write`` stores a symlink target's bytes as an ordinary entry, so the
    server's symlink-entry check never sees the link. A link that stays inside the
    project is harmless (those bytes would ship anyway); one that leaves it would
    upload an unrelated file, so it is refused here, naming the link.
    """
    real = file_path.resolve()
    if real != project_root and project_root not in real.parents:
        raise SymlinkEscapeError(
            f"{rel} is a symlink to {real}, outside the project; " "copy the file into the project or remove the link"
        )


def iter_upload_files(project_path: Path, pdf_path: Path | None) -> Iterator[tuple[Path, Path]]:
    """Yield ``(file_path, project-relative path)`` for every file the zip ships.

    The allowlisted source tree, with build-output dirs (the standard
    ``_book/``/``docs/`` plus any custom ``project.output-dir``) skipped, then
    *pdf_path* once at its relative path when given (upload path; ``None`` is the
    render path, where the server renders the source). Raises
    :class:`SymlinkEscapeError` for a file whose real path leaves the project.
    Directory symlinks are not descended into.
    """
    output_names = output_dir_names(project_path)
    project_root = project_path.resolve()
    for root, dirs, files in os.walk(project_path):
        root_rel = Path(root).relative_to(project_path)
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d not in DEFAULT_EXCLUDE_DIRS
            and not is_output_path((*root_rel.parts, d), output_names)
        ]
        for filename in files:
            file_path = Path(root) / filename
            rel = file_path.relative_to(project_path)
            if is_excluded_project_path(rel.parts, filename, file_path.suffix):
                continue
            if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            _require_inside(project_root, file_path, rel)
            yield file_path, rel
    if pdf_path is not None:
        pdf_rel = pdf_path.relative_to(project_path)
        _require_inside(project_root, pdf_path, pdf_rel)
        yield pdf_path, pdf_rel
