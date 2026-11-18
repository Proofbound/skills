r"""Local CLI over the back-of-book index engine (bead ``proofbound_monorepo-gnka``).

Lets a skill index a Quarto project **on the user's own machine**, so the LLM
half — curation — happens in the host conversation on the user's tokens rather
than as a server-side OpenRouter call. That is the same division ADR-0005 draws
for chapter prose, applied to the index.

Two commands, matching the two halves:

``extract``
    Statistical only (spaCy NER + YAKE, no model). Emits the candidate payload
    the host Claude curates — the *same* ``cid -> term`` wire shape the server
    ships to a model, so the grounding contract (invariant #5) is unchanged: an
    invented ``cid`` does not resolve and its entry is dropped.

``apply``
    Grounds the curated entries, inserts markers, writes ``book-index.qmd``, and
    registers both in ``_quarto.yml``.

**This module adds no engine logic.** Every transformation is the production
function, imported. Marker insertion in particular must never be reimplemented:
``escape_index_key`` escapes two alphabets in one left-to-right pass and
``protected_ranges`` is a hand-written state machine, both pinned against Pandoc
rather than reasoned about. That is invariant #4 — *never reimplement or
regex-substitute the marker layer* — restated for skill users in the bundled
``reference/INVARIANTS.md``.

What this deliberately does *not* reuse is ``BookIndexMixin``. That applier is
bound to the generation-time scaffold path and to ``chapter-*.qmd`` filenames,
and its ``.pb-book-index-txn/`` rollback exists because a server temp dir has no
other undo. A local project has a better one: git. So chapter discovery here
goes through ``_quarto.yml`` (arbitrary filenames, parts flattened) and the undo
story is the working tree, enforced by :func:`_require_clean_worktree`.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — fixed argv, no shell, git only
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from ..config import (
    NON_CHAPTER_FILENAMES,
    atomic_yaml_dump,
    header_entry_has_marker,
    iter_chapter_files,
    load_quarto_config,
    normalize_format_map,
    normalize_header_includes,
)
from ..index_page import (
    BOOK_INDEX_PAGE_FILENAME,
    is_pb_generated_index_page,
)
from ..indexing import (
    IndexExtractionSkipped,
    IndexVocabulary,
    append_cross_references,
    build_index_page,
    candidate_payload,
    curate_vocabulary,
    extract_candidates,
    insert_markers,
)
from ..indexing.preamble import BOOK_INDEX_PREAMBLE, PREAMBLE_MARKER

# CliError lives in .errors; importing it here keeps it re-exported from its
# historical home.
from .errors import CliError

#: Substring identifying a marker this tool already inserted. Deliberately the
#: raw inline **code-span** opener from ``marker_for`` rather than a bare
#: ``\index{``: a user's own hand-written LaTeX uses the bare form, and reading
#: that as ours is precisely the confusion invariant #2 forbids.
APPLIED_MARKER_FRAGMENT = "`\\index{"


# ---------------------------------------------------------------------------
# Project reading
# ---------------------------------------------------------------------------


def chapter_documents(project_dir: Path) -> list[tuple[str, str]]:
    """Return ``[(relative_path, text), ...]`` for the project's real chapters.

    Discovery goes through ``_quarto.yml`` rather than a ``chapter-*.qmd`` glob:
    a user's own project names files whatever it likes, and the glob would
    silently index nothing. ``iter_chapter_files`` also flattens parts, so a
    book that groups chapters under ``{part: ...}`` is handled.

    Front and back matter (``NON_CHAPTER_FILENAMES``) and the index page itself
    are excluded — indexing the colophon or the index would place markers in
    boilerplate.
    """
    _, config = load_quarto_config(project_dir)
    documents: list[tuple[str, str]] = []
    for relative in iter_chapter_files(config):
        name = PurePosixPath(relative).name.lower()
        if name in NON_CHAPTER_FILENAMES or name == BOOK_INDEX_PAGE_FILENAME:
            continue
        path = project_dir / relative
        if not path.is_file():
            continue
        try:
            documents.append((relative, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as exc:
            raise CliError(f"could not read chapter {relative}: {exc}") from exc
    return documents


def _require_clean_worktree(project_dir: Path, *, force: bool) -> None:
    """Refuse to rewrite files that git could not restore.

    ``apply`` edits the user's manuscript in place. The server's crash-safe
    transaction is not reused here (see the module docstring), so git *is* the
    undo — which means a dirty or absent repository leaves the user with no way
    back, and that is worth stopping for rather than warning about.
    """
    if force:
        return
    try:
        result = subprocess.run(  # nosec B603 — fixed argv, no shell
            ["git", "-C", str(project_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CliError(f"could not run git in {project_dir}: {exc}") from exc

    if result.returncode != 0:
        raise CliError(
            f"{project_dir} is not a git repository, so there would be no way to "
            "undo the rewrite. Commit it to git first, or re-run with --force if "
            "you have your own backup."
        )
    if result.stdout.strip():
        raise CliError(
            f"{project_dir} has uncommitted changes, so an undo would also discard "
            "them. Commit or stash first, or re-run with --force."
        )

    # A clean ``status`` is not proof of recoverability: it lists neither
    # ignored files nor anything under an ignored directory, so a gitignored
    # project sitting inside a repository reports clean while git holds nothing
    # to restore — the one case where this rewrite is truly irreversible.
    tracked = subprocess.run(  # nosec B603 — fixed argv, no shell
        ["git", "-C", str(project_dir), "ls-files", "--", "."],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if tracked.returncode != 0 or not tracked.stdout.strip():
        raise CliError(
            f"git is not tracking anything under {project_dir} (it is probably "
            "gitignored), so git could not undo the rewrite. Commit the project, "
            "or re-run with --force if you have your own backup."
        )


# ---------------------------------------------------------------------------
# Input files
# ---------------------------------------------------------------------------


def _read_json(path: str, what: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliError(f"{what} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"{path} is not valid JSON ({what}): {exc}") from exc


def _load_candidate_ids(path: str) -> set[str]:
    """Return every ``cid`` in an ``extract`` payload, validating its shape.

    Shape-checked rather than trusted because the failure it prevents is silent:
    a missing ``candidates`` key used to make the drift guard vacuous, and cids
    are positional, so stale entries would then mark entirely different terms.
    """
    payload = _read_json(path, "candidates")
    if not isinstance(payload, dict):
        raise CliError(
            f"{path}: expected the JSON object written by `extract`, got "
            f"{type(payload).__name__} — did you pass the entries file here?"
        )
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise CliError(
            f"{path}: no `candidates` list — this is not an `extract` payload."
        )
    cids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("cid"), str):
            raise CliError(f"{path}: every candidate needs a string `cid`.")
        cids.add(candidate["cid"])
    return cids


def _load_curated_entries(path: str) -> list[dict]:
    """Return the curated entries, as a non-empty list of mappings."""
    payload = _read_json(path, "entries")
    if isinstance(payload, dict):
        payload = payload.get("entries")
    if not isinstance(payload, list):
        raise CliError(
            f"{path}: expected a JSON list of entries, or an object with an "
            "`entries` list — did you pass the candidates file here?"
        )
    if not payload:
        raise CliError(
            f"{path} has no entries. An empty curation would silently fall back to "
            "a purely statistical index over the whole manuscript."
        )
    if not all(isinstance(entry, dict) for entry in payload):
        raise CliError(f"{path}: every entry must be a JSON object.")
    return payload


# ---------------------------------------------------------------------------
# _quarto.yml registration
# ---------------------------------------------------------------------------


def register_index_page(project_dir: Path) -> None:
    """Add the preamble block and ``book-index.qmd`` to ``_quarto.yml``.

    Idempotent on both halves: the preamble is matched by
    :data:`PREAMBLE_MARKER` and the page by filename, so re-running ``apply``
    does not stack duplicates.

    Placement mirrors ``_build_final_chapter_files`` exactly — the index goes
    **after** ``references.qmd`` and **before** ``colophon.qmd``. The colophon
    must stay last (``enforce_render_branding`` re-asserts that on the server),
    so appending blindly would either displace it or be undone at render.

    An existing ``format.pdf`` is **required**, never created. The server can
    call ``ensure_pdf_format_block`` safely because its scaffold always writes a
    pdf block; here the input is a user's project, where inventing one changes
    what the book renders to — an HTML-only book would silently gain a PDF
    target it cannot build without a LaTeX toolchain, and a book with no
    ``format`` key at all would lose its default HTML output.
    """
    quarto_path, config = load_quarto_config(project_dir)
    if not isinstance(config, dict):
        raise CliError(f"{quarto_path}: expected a mapping at the top level")

    formats = normalize_format_map(config)
    pdf = formats.get("pdf")
    if not isinstance(pdf, dict):
        raise CliError(
            f"{quarto_path} has no `format: pdf:` block. The back-of-book index is "
            "PDF-only — its page renders \\printindex and the imakeidx preamble "
            "lives in format.pdf.header-includes — so add a pdf format to this "
            "book before indexing it."
        )
    config["format"] = formats

    entries = normalize_header_includes(pdf.get("header-includes"))
    if not any(header_entry_has_marker(entry, PREAMBLE_MARKER) for entry in entries):
        entries.append(BOOK_INDEX_PREAMBLE)
        pdf["header-includes"] = entries

    book = config.get("book")
    if not isinstance(book, dict) or not isinstance(book.get("chapters"), list):
        raise CliError(f"{quarto_path}: book.chapters must be a non-empty list")

    chapters = book["chapters"]
    if BOOK_INDEX_PAGE_FILENAME not in chapters:
        colophon = next(
            (
                position
                for position, entry in enumerate(chapters)
                if isinstance(entry, str)
                and PurePosixPath(entry).name.lower() == "colophon.qmd"
            ),
            None,
        )
        if colophon is None:
            chapters.append(BOOK_INDEX_PAGE_FILENAME)
        else:
            chapters.insert(colophon, BOOK_INDEX_PAGE_FILENAME)

    # atomic_yaml_dump rewrites the whole document from the parsed structure, so
    # comments, anchors and block-scalar styling are lost. Its docstring scopes
    # that tradeoff to server-generated configs; this one is hand-maintained, so
    # say so rather than let the user discover it in a diff.
    print(
        f"note: rewriting {quarto_path.name} drops any comments and formatting it "
        "had (the YAML content is preserved). Review the diff before committing.",
        file=sys.stderr,
    )
    atomic_yaml_dump(quarto_path, config)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    documents = chapter_documents(project_dir)
    if not documents:
        raise CliError(
            f"no chapter files found via {project_dir / '_quarto.yml'} — check that "
            "book.chapters lists your chapters and that they exist on disk"
        )

    try:
        candidates = extract_candidates([text for _name, text in documents])
    except IndexExtractionSkipped as skipped:
        raise CliError(f"extraction produced nothing usable: {skipped}") from skipped

    payload = {
        "project_dir": str(project_dir),
        "chapters": [name for name, _text in documents],
        "candidates": candidate_payload(candidates),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(
            f"{len(payload['candidates'])} candidates from {len(documents)} chapters "
            f"-> {args.output}",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


def _guard_index_page(page_path: Path) -> None:
    """Never overwrite a hand-written index page (invariant #3).

    The sentinel is the only way to tell ours from theirs, and it is checked
    before any write.
    """
    if page_path.exists() and not is_pb_generated_index_page(page_path):
        raise CliError(
            f"{page_path} already exists and was not generated by ProofBound — "
            "refusing to overwrite it (page_collision)"
        )


def _finish_interrupted_run(
    project_dir: Path, page_path: Path, marked: Sequence[str]
) -> None:
    """Complete an apply that wrote chapters and then died before the page.

    ``insert_markers`` is idempotent, so a second full run would place zero
    markers and die claiming nothing matched. Distinguish the two real cases: a
    finished index (nothing to do) from a run that wrote chapters and then
    failed before the page or ``_quarto.yml``, which is repairable and is the
    only state this tool can leave behind.
    """
    if page_path.exists() and is_pb_generated_index_page(page_path):
        raise CliError(
            "this project already has an index. Markers cannot be stripped "
            "safely (a user's own \\index{} is indistinguishable from ours), "
            "so re-index by restoring the chapters first: "
            "`git checkout -- .` then re-run extract, curate, and apply."
        )
    print(
        f"note: {len(marked)} chapter(s) already carry markers from an "
        "interrupted run; completing the index page and _quarto.yml only.",
        file=sys.stderr,
    )
    page_path.write_text(
        build_index_page(IndexVocabulary(entries=(), source="curated")),
        encoding="utf-8",
    )
    register_index_page(project_dir)


def _grounded_vocabulary(
    args: argparse.Namespace, documents: Sequence[tuple[str, str]]
) -> IndexVocabulary:
    """Re-derive candidates from the current text and ground the curation."""
    # Parse and shape-check both inputs BEFORE the expensive spaCy pass, so a
    # swapped --candidates/--entries pair costs a message rather than a minute
    # of extraction followed by a raw AttributeError.
    expected = _load_candidate_ids(args.candidates)
    raw_entries = _load_curated_entries(args.entries)

    try:
        candidates = extract_candidates([text for _name, text in documents])
    except IndexExtractionSkipped as skipped:
        raise CliError(f"extraction produced nothing usable: {skipped}") from skipped

    # cids are POSITIONAL, so a drifted manuscript does not merely degrade the
    # index — stale entries ground to different terms and place confidently
    # wrong markers, the exact corruption the cid contract exists to prevent.
    # Never skip this check.
    if {c.cid for c in candidates} != expected:
        raise CliError(
            "the manuscript changed since extract ran — the candidate ids no longer "
            f"match {args.candidates}. cids are positional, so applying these "
            "entries would mark the wrong terms. Re-run extract, re-curate, then "
            "apply."
        )

    vocabulary = curate_vocabulary(candidates, raw_entries)
    if vocabulary.source != "curated" and not args.allow_statistical_fallback:
        raise CliError(
            f"only {len(vocabulary.entries)} of the {len(raw_entries)} curated "
            "entries could be grounded against the candidates, which is below "
            "BOOK_INDEX_MIN_ENTRIES — the engine fell back to a purely statistical "
            "index, silently discarding the curation. Curate more entries, or pass "
            "--allow-statistical-fallback to accept the statistical index."
        )
    return vocabulary


def _write_index(
    project_dir: Path,
    page_path: Path,
    documents: Sequence[tuple[str, str]],
    vocabulary: IndexVocabulary,
) -> tuple[int, int]:
    """Mark every chapter, write the page, and register it. Returns counts."""
    entries = vocabulary.entries

    # 1. Compute every rewrite in memory before touching disk.
    transformed: list[tuple[str, str, int]] = []
    total_markers = 0
    for name, before in documents:
        after, count = insert_markers(before, entries, chapter_label=name)
        if count:
            transformed.append((name, after, count))
            total_markers += count

    if not total_markers:
        raise CliError(
            "no markers could be placed — none of the curated terms matched the "
            "manuscript text"
        )

    # Cross-references ride the LAST chapter that received markers, never the
    # index page: \index reaches the .idx only at its page's shipout, which is
    # after imakeidx has already run makeindex at \printindex. Past the final
    # marker is also the only placement that sorts `see also` after the
    # locators rather than among them (bead proofbound_monorepo-ud0u).
    name, after, count = transformed[-1]
    transformed[-1] = (name, append_cross_references(after, entries), count)

    # 2. Write.
    for name, text, _count in transformed:
        (project_dir / name).write_text(text, encoding="utf-8")
    page_path.write_text(build_index_page(vocabulary), encoding="utf-8")
    register_index_page(project_dir)

    return total_markers, len(transformed)


def _report_applied(
    vocabulary: IndexVocabulary, total_markers: int, chapters: int
) -> None:
    """Print the summary, and the cross-reference render warning if earned."""
    entries = vocabulary.entries
    print(
        f"indexed {len(entries)} entries, {total_markers} markers across "
        f"{chapters} chapters (source: {vocabulary.source})",
        file=sys.stderr,
    )
    if not any(entry.see or entry.see_also for entry in entries):
        return
    # proofbound_monorepo-rmj7. The server survives this via the
    # finalize_and_build fallback, which reverts and rebuilds; there is no
    # such safety net here, so the user has to be told the render may fail
    # and that `git checkout -- .` is the way back.
    print(
        "warning: this index has see/see-also cross-references, which can make "
        "Quarto fail with 'error generating index' when a cross-referenced term "
        "is mentioned on the same page as the block "
        "(proofbound_monorepo-rmj7). If the render fails, `git checkout -- .` "
        "and re-curate without cross-references.",
        file=sys.stderr,
    )


def cmd_apply(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    _require_clean_worktree(project_dir, force=args.force)

    page_path = project_dir / BOOK_INDEX_PAGE_FILENAME
    _guard_index_page(page_path)

    documents = chapter_documents(project_dir)
    if not documents:
        raise CliError("no chapter files found — nothing to index")

    # Resolved before the input files are even read: neither branch below needs
    # a curation, and being told "already indexed" should not require passing a
    # valid --candidates first.
    marked = [name for name, text in documents if APPLIED_MARKER_FRAGMENT in text]
    if marked:
        _finish_interrupted_run(project_dir, page_path, marked)
        return 0

    vocabulary = _grounded_vocabulary(args, documents)
    total_markers, chapters = _write_index(
        project_dir, page_path, documents, vocabulary
    )
    _report_applied(vocabulary, total_markers, chapters)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir).resolve()
    page_path = project_dir / BOOK_INDEX_PAGE_FILENAME
    applied = is_pb_generated_index_page(page_path)
    print(
        json.dumps(
            {
                "project_dir": str(project_dir),
                "has_book_index": applied,
                "index_page_exists": page_path.exists(),
                # Present but unowned means a hand-written page we must not touch.
                "user_authored_page": page_path.exists() and not applied,
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="book-index",
        description="Index a local Quarto book: extract candidates, then apply "
        "curated entries.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser(
        "extract", help="extract index candidates (statistical; no model)"
    )
    extract.add_argument("project_dir", help="Quarto project directory")
    extract.add_argument("-o", "--output", help="write JSON here instead of stdout")
    extract.set_defaults(func=cmd_extract)

    apply_cmd = sub.add_parser(
        "apply", help="apply curated entries: markers, index page, _quarto.yml"
    )
    apply_cmd.add_argument("project_dir", help="Quarto project directory")
    apply_cmd.add_argument(
        "--candidates", required=True, help="the JSON written by `extract`"
    )
    apply_cmd.add_argument(
        "--entries", required=True, help="curated entries JSON (list, or {entries: []})"
    )
    apply_cmd.add_argument(
        "--force",
        action="store_true",
        help="skip the clean-git-worktree check (you have your own backup)",
    )
    apply_cmd.add_argument(
        "--allow-statistical-fallback",
        action="store_true",
        help="accept a purely statistical index when too few curated entries ground",
    )
    apply_cmd.set_defaults(func=cmd_apply)

    status = sub.add_parser("status", help="report whether the project has an index")
    status.add_argument("project_dir", help="Quarto project directory")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
