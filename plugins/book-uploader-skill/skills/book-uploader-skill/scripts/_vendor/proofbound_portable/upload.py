"""Upload policy: what may leave a customer's machine, and what the server accepts.

Shared by the server (``proofbound_shared.storage``, ``zip_intake``) and the
book-uploader skill so both sides apply one rule.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Default exclusion sets for project-tree walkers
# ---------------------------------------------------------------------------
#
# Single source of truth for what does NOT belong in an uploaded book project.
# Both the per-file ``upload_src_tree`` (here) and the ZIP-builder in
# ``cc-template-api/scripts/upload_local_book.py`` import these so the two
# code paths cannot drift apart again.
#
# History: these used to live in two places with different contents — the
# narrower one let ``.git/`` and ``.DS_Store`` leak from local-folder uploads
# straight into R2.

DEFAULT_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".claude",
        ".quarto",
        ".pytest_cache",
        "_book",
        "_freeze",
        "docs",
        "site_libs",
        "__pycache__",
        "node_modules",
        ".venv",
    }
)

DEFAULT_EXCLUDE_FILES = frozenset({".DS_Store", "project.zip"})

DEFAULT_EXCLUDE_EXTENSIONS = frozenset(
    {
        ".tex",
        ".html",
        ".log",
        ".aux",
        ".pyc",
    }
)


def is_excluded_project_path(
    rel_parts: tuple[str, ...],
    name: str,
    suffix: str,
    *,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    exclude_files: frozenset[str] = DEFAULT_EXCLUDE_FILES,
    exclude_extensions: frozenset[str] = DEFAULT_EXCLUDE_EXTENSIONS,
    exclude_prefixes: frozenset[tuple[str, ...]] = frozenset(),
) -> bool:
    """Return True if a project-relative path must not leave the user's machine.

    Canonical predicate for every project-tree walker (``upload_src_tree``,
    ``build_project_zip``, the book-uploader skill, the zip->src migration).
    The named sets are a blacklist, so the decisive rule is the first one:

    * **Any dot-prefixed path component is excluded.** A blacklist can never
      enumerate the credential files a real project carries — ``.env``,
      ``.envrc``, ``.env.local``, ``.npmrc``, ``.aws/``, ``id_rsa`` inside
      ``.ssh/`` — so dotfiles are excluded wholesale rather than named one by
      one. Nothing a book project needs is dot-prefixed: ``_quarto.yml`` and
      ``_book/`` lead with an underscore, and the only dot-prefixed files the
      templates scaffold are ``.gitignore`` and ``.gitkeep``,
      plus the derived files ProofBound regenerates on every render and must NOT
      persist or export (``.pb-build-stamp.def``, managed Lua filters such as
      ``.pb-bidi.lua``; see ``pdf_headers.ManagedFilter``). Relying on this rule
      is what keeps ``.lua`` off the upload allowlist. This matches
      ``zip_intake._is_ignored_entry``, which already drops dot-prefixed
      entries on the receiving side; this closes the *sending* side, where a
      secret would otherwise be read off disk, zipped, and shipped to R2.
    * :data:`DEFAULT_EXCLUDE_DIRS` / ``_FILES`` / ``_EXTENSIONS` — build
      output, VCS, caches, OS junk.
    * *exclude_prefixes* — project-specific relative paths (each a tuple of
      parts) excluded with everything beneath them. The caller derives these
      from the project itself, chiefly the rendered-output directory when
      ``_quarto.yml`` redirects ``project.output-dir`` away from ``_book/``.
    """
    if any(part.startswith(".") for part in rel_parts):
        return True
    if any(rel_parts[: len(prefix)] == prefix for prefix in exclude_prefixes):
        return True
    if any(part in exclude_dirs for part in rel_parts):
        return True
    if name in exclude_files:
        return True
    if suffix in exclude_extensions:
        return True
    return False


# Allowlisted file extensions. Source text types kept by upload_src_tree plus the
# binary/asset types a rendered Quarto book legitimately carries. Everything
# else (executables, scripts, archives) is rejected.
ALLOWED_EXTENSIONS = frozenset(
    {
        # source / config text
        ".qmd",
        ".md",
        ".yml",
        ".yaml",
        ".bib",
        ".csl",
        ".json",
        ".txt",
        ".csv",
        ".tsv",
        # rendered / asset binaries
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".svg",
        ".eps",
        ".css",
        ".ttf",
        ".otf",
    }
)
