#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
# ]
# ///
"""
Book-builder skill — thin HTTP client for the ProofBound API.

Prose is written locally (by Claude, in Session Mode); the platform does the
heavy lifting: it scaffolds the canonical Quarto project (ProofBound branding +
colophon), renders the PDF, and persists the book to the authenticated user's
library. No local Quarto/TinyTeX required.

Requires a ProofBound account. Run `login` first.

Commands:
    login / logout
    fetch-prompts   Download canonical prompt templates → local cache
    publish         Gather local content → POST /books/compose → poll → URL
    status          Poll a book's render status by id
    download-src    Download a book's Quarto source tree as a zip

Usage:
    cd <the unzipped book-builder-skill directory>
    uv sync

    uv run python scripts/publish_book.py login
    uv run python scripts/publish_book.py fetch-prompts

    # After writing content locally (cc-config.yml, outline.yml, chapters/,
    # introduction.md, summary.md):
    uv run python scripts/publish_book.py publish --project-dir ~/Books/my-book

    uv run python scripts/publish_book.py download-src \
        --book-id <uuid> --output ~/Books/my-book-source.zip

Local content layout read by `publish` (--project-dir):
    cc-config.yml          title, author, book_type, subtitle?, description?, writing_style?, trim_size?,
                           declined? (fields the user explicitly declined: description/book_blurb/author_bio)
    outline.yml            chapters: [{number, chapter_title, chapter_description?}]
    chapters/chapter_<N>.qmd   (or .md)  — one per outline chapter
    introduction.md        book preface/introduction prose
    summary.md             book summary prose
    blurb.md               (optional) back-cover blurb / marketing copy, <= 1200 chars
    author.md              (optional) author bio, <= 500 chars
    author.{jpg,jpeg,png}  (optional) author photo, <= 5 MB
    references.bib         (optional) BibTeX entries → rendered References section
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx
import yaml
from auth import get_api_url, get_token, logout, start_device_login, wait_for_device_login

# Author, blurb and word-target policy is the server's own, through
# proofbound-portable vendored beside this script -- a real tree in the shipped
# zip, a symlink in a development checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))

from proofbound_portable.author import (  # noqa: E402
    MAX_AUTHOR_BIO_CHARS,
    MAX_PHOTO_BYTES,
    detect_image_format,
    fold_soft_breaks,
)
from proofbound_portable.limits import (  # noqa: E402
    MAX_BOOK_BLURB_CHARS,
    MAX_TOTAL_TARGET_WORDS,
    MIN_TOTAL_TARGET_WORDS,
)

# The skill's own version is its SKILL.md frontmatter `version:`, the one copy a
# claude.ai upload and a Claude Code install both carry beside scripts/.
SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"

PROMPTS_CACHE = Path.home() / ".proofbound" / "cache" / "prompts"
# Script Mode's server-chosen default model, cached beside the prompts and read
# by generate_chapters.py (same constant there; a test pins the two together).
SCRIPT_MODE_MODEL_FILE = "script-mode-model.json"

PROMPTS_TIMEOUT = 30
COMPOSE_TIMEOUT = 60
POLL_TIMEOUT = 30
SOURCE_TIMEOUT = 120

# How long to wait for a server-side render before giving up the poll loop. Must
# exceed the server's whole-job backstop (RENDER_JOB_TIMEOUT_SECONDS, 2040s: two
# render passes, since the corrective gutter re-render counts) or a
# slow-but-successful render reads as timed out. Queue wait behind other renders
# is not bounded, so a timeout still only means "may still finish". A parity test
# pins it.
DEFAULT_POLL_DEADLINE = 2400  # seconds
POLL_INTERVAL = 5  # seconds

# Matches the server BookComposeRequest.bibliography_content max_length (200_000).
MAX_BIBLIOGRAPHY_CHARS = 200_000
# Match cc_template_api.models.customization.WORD_TARGET_MIN/MAX, the bounds the
# compose surface enforces on each outline entry's word_target.
WORD_TARGET_MIN = 100
WORD_TARGET_MAX = 20_000


def _fail(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _api_headers() -> dict[str, str]:
    token = get_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ============================================
# fetch-prompts
# ============================================


def _skill_version() -> str | None:
    """This copy's version from the SKILL.md frontmatter, or None if unreadable."""
    try:
        text = SKILL_MD.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    for line in text.split("\n")[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        if key.strip() == "version":
            return value.strip() or None
    return None


def _version_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _stale_skill_warning(policy: dict | None) -> str | None:
    """Return a re-upload warning when this skill is older than the server's minimum.

    A skill uploaded to claude.ai cannot update itself, so the server names the
    oldest version it supports (``book_builder_skill.minimum``). Advisory: an old
    skill still works, it just follows a playbook the server has moved past.
    """
    minimum = (policy or {}).get("minimum")
    if not minimum:
        return None
    ours = _skill_version()
    ours_t, theirs_t = (_version_tuple(ours) if ours else None), _version_tuple(minimum)
    if theirs_t is None:
        # A minimum we cannot parse is our own formatting slip, not the user's.
        return None
    if ours_t is not None and ours_t >= theirs_t:
        return None
    return (
        f"⚠️  This book-builder skill is version {ours or 'unknown'}; ProofBound now expects at least "
        f"{minimum}.\n   Download the current skill and install it again (on claude.ai, delete the old "
        "skill and add the new zip rather than replacing it)."
    )


def cmd_fetch_prompts(args):
    out_dir = Path(args.output_dir).expanduser()
    base_url = get_api_url()

    print("Fetching canonical prompt templates...")
    try:
        resp = httpx.get(
            f"{base_url}/books/prompts",
            # client=skill adds book-workflow-narrative.md rendered for this skill.
            params={"client": "skill"},
            headers=_api_headers(),
            timeout=PROMPTS_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _fail(f"request failed: {exc}")

    if resp.status_code != 200:
        _fail(f"{resp.status_code} — {resp.text}")

    body = resp.json()
    prompts = body.get("prompts", {})
    if not prompts:
        _fail("server returned no prompts")

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for rel_path, text in prompts.items():
        # rel_path uses POSIX separators (e.g. chapter-generation/system.jinja2).
        # Guard against path traversal before writing under the cache root.
        dest = (out_dir / rel_path).resolve()
        if not str(dest).startswith(str(out_dir.resolve())):
            _fail(f"refusing to write outside cache: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written += 1

    # Replace, never keep: a stale default from an earlier fetch would outlive a
    # registry change, which is exactly the drift this file exists to remove.
    model_file = out_dir / SCRIPT_MODE_MODEL_FILE
    script_mode_model = body.get("script_mode_model")
    if script_mode_model:
        model_file.write_text(json.dumps(script_mode_model, indent=2) + "\n", encoding="utf-8")
    else:
        model_file.unlink(missing_ok=True)
        print("⚠️  Server sent no Script Mode model; generate_chapters.py cannot run until it does.")

    print(f"\n✅ Cached {written} prompt template(s) → {out_dir}")

    warning = _stale_skill_warning(body.get("book_builder_skill"))
    if warning:
        print(f"\n{warning}")


# ============================================
# publish
# ============================================


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        _fail(f"missing required file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"failed to parse {path.name}: {exc}")
    if not isinstance(data, dict):
        _fail(f"{path.name} is not a YAML mapping")
    return data


def _sum_beat_word_targets(chapter: dict) -> int | None:
    """Sum a chapter's section-beat ``word_target``s into the one scope number.

    Returns ``None`` when the chapter carries no beats with targets — which is
    itself meaningful: the server reads an outline with no targets anywhere as a
    likely-skipped beat pass and says so. Out-of-range sums are dropped rather
    than clamped; a bogus target is worse than none, and the server would 422 it.
    """
    sections = chapter.get("sections")
    if not isinstance(sections, list):
        return None
    total = 0
    for beat in sections:
        if not isinstance(beat, dict):
            continue
        target = beat.get("word_target")
        if isinstance(target, int) and not isinstance(target, bool) and target > 0:
            total += target
    if not WORD_TARGET_MIN <= total <= WORD_TARGET_MAX:
        return None
    return total


def _read_chapter_content(project_dir: Path, number: int) -> str:
    chapters_dir = project_dir / "chapters"
    for ext in ("qmd", "md"):
        candidate = chapters_dir / f"chapter_{number}.{ext}"
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if not text:
                _fail(f"chapter {number} is empty: {candidate}")
            return text
    _fail(f"chapter {number} not found " f"(expected {chapters_dir}/chapter_{number}.qmd or .md)")


def _resolve_text_file(explicit: str | None, project_dir: Path, *names: str) -> str:
    """Return the stripped text of an explicit path or the first matching name."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            _fail(f"file not found: {path}")
    else:
        path = next((project_dir / n for n in names if (project_dir / n).exists()), None)
        if path is None:
            joined = " or ".join(names)
            _fail(f"missing {joined} in {project_dir}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        _fail(f"file is empty: {path}")
    return text


def _gather_author_photo(project_dir: Path) -> str | None:
    """Find an optional author photo, validate it, return base64 (or None)."""
    for name in ("author.jpg", "author.jpeg", "author.png"):
        path = project_dir / name
        if not path.exists():
            continue
        raw = path.read_bytes()
        if len(raw) > MAX_PHOTO_BYTES:
            _fail(f"author photo exceeds 5 MB: {path} ({len(raw) / 1_048_576:.1f} MB)")
        # Validate by magic bytes (not extension) — server re-validates too.
        if detect_image_format(raw) is None:
            _fail(f"author photo is not a valid JPEG or PNG: {path}")
        return base64.b64encode(raw).decode()
    return None


def _build_compose_payload(args) -> dict:
    project_dir = Path(args.project_dir).expanduser()
    if not project_dir.is_dir():
        _fail(f"project dir not found: {project_dir}")

    cc_config = _load_yaml(project_dir / "cc-config.yml")
    outline = _load_yaml(project_dir / "outline.yml")

    book = outline.get("book") or {}
    title = cc_config.get("title") or book.get("title")
    author = cc_config.get("author")
    if not title:
        _fail("title missing from cc-config.yml")
    if not author:
        _fail("author missing from cc-config.yml")

    raw_chapters = outline.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        _fail("outline.yml has no chapters")

    outline_entries: list[dict] = []
    chapter_entries: list[dict] = []
    for ch in raw_chapters:
        number = ch.get("number")
        chapter_title = ch.get("chapter_title")
        if not isinstance(number, int) or number < 1:
            _fail(f"outline chapter missing valid 'number': {ch!r}")
        if not chapter_title:
            _fail(f"outline chapter {number} missing 'chapter_title'")
        entry = {"number": number, "chapter_title": chapter_title}
        if ch.get("chapter_description"):
            entry["chapter_description"] = ch["chapter_description"]
        # Sum the local section beats into the one scope number that travels. The
        # beats themselves stay local; this lets the server report a chapter that
        # came in short of what it was planned for. Warn-only, never a floor.
        word_target = _sum_beat_word_targets(ch)
        if word_target:
            entry["word_target"] = word_target
        outline_entries.append(entry)
        chapter_entries.append({"number": number, "content": _read_chapter_content(project_dir, number)})

    introduction = _resolve_text_file(args.introduction, project_dir, "introduction.md", "index.qmd")
    summary = _resolve_text_file(args.summary, project_dir, "summary.md", "summary.qmd")

    payload: dict = {
        "title": title,
        "author": author,
        "outline": outline_entries,
        "chapters": chapter_entries,
        "introduction": introduction,
        "summary": summary,
    }

    subtitle = cc_config.get("subtitle") or book.get("subtitle")
    if subtitle:
        payload["subtitle"] = subtitle
    description = cc_config.get("description") or book.get("description")
    if description:
        payload["description"] = description
    if cc_config.get("writing_style"):
        payload["writing_style"] = cc_config["writing_style"]
    # Structural axis; the server validates the key (BookComposeRequest.book_type).
    if cc_config.get("book_type"):
        payload["book_type"] = cc_config["book_type"]
    # Fields the user explicitly declined. Forwarded as-is: the server owns the
    # completeness rule and rejects unknown or conflicting names.
    declined = cc_config.get("declined")
    if declined is not None:
        if not isinstance(declined, list):
            _fail("`declined:` in cc-config.yml must be a list of field names")
        if declined:
            payload["declined_fields"] = declined

    trim_size = args.trim_size or cc_config.get("trim_size")
    if trim_size:
        payload["trim_size"] = trim_size

    target_length = book.get("target_length") or cc_config.get("total_target_words")
    if isinstance(target_length, int) and MIN_TOTAL_TARGET_WORDS <= target_length <= MAX_TOTAL_TARGET_WORDS:
        payload["total_target_words"] = target_length

    # Provenance. ProofBound never writes prose, so unless the client says what
    # authored the book, nothing does. Both are recorded in the rendered
    # cc-config.yml so a finished book is self-describing.
    # getattr: callers hand-build an args namespace (tests, and the skill's own
    # resume path), so a missing attribute must read as "not supplied".
    authoring_model = getattr(args, "authoring_model", None)
    if authoring_model:
        payload["authoring_model"] = str(authoring_model)[:100]
    workflow_version = getattr(args, "workflow_version", None)
    if workflow_version:
        payload["workflow_version"] = str(workflow_version)[:100]

    blurb_path = project_dir / "blurb.md"
    if blurb_path.exists():
        blurb = blurb_path.read_text(encoding="utf-8").strip()
        if len(blurb) > MAX_BOOK_BLURB_CHARS:
            _fail(f"book blurb exceeds {MAX_BOOK_BLURB_CHARS} chars: {blurb_path}")
        if blurb:
            payload["book_blurb"] = blurb

    bio_path = project_dir / "author.md"
    if bio_path.exists():
        # Fold Markdown soft breaks before measuring: the server does the same
        # (proofbound_shared.author_files.normalize_bio) before enforcing the
        # limit, so measuring the raw text would fail a bio that in fact fits.
        text = bio_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        bio = fold_soft_breaks(text).strip()
        if len(bio) > MAX_AUTHOR_BIO_CHARS:
            _fail(f"author bio exceeds {MAX_AUTHOR_BIO_CHARS} chars: {bio_path}")
        if bio:
            payload["author_bio"] = bio

    photo_b64 = _gather_author_photo(project_dir)
    if photo_b64:
        payload["author_photo_base64"] = photo_b64

    bib_path = project_dir / "references.bib"
    if bib_path.exists():
        bib = bib_path.read_text(encoding="utf-8")
        if len(bib) > MAX_BIBLIOGRAPHY_CHARS:
            _fail(f"bibliography exceeds {MAX_BIBLIOGRAPHY_CHARS} chars: {bib_path}")
        # Verbatim BibTeX (not stripped) so it lands identically server-side; a
        # blank/whitespace-only file is treated as "no bibliography", matching the
        # server's `(bibliography_content or "").strip()` gate in _write_bibliography().
        if bib.strip():
            payload["bibliography_content"] = bib

    return payload


def _poll_until_done(base_url: str, book_id: str, deadline: int) -> dict:
    """Poll GET /books/{id} until completed/failed or the deadline elapses."""
    start = time.monotonic()
    last_status = None
    while True:
        try:
            resp = httpx.get(
                f"{base_url}/books/{book_id}",
                headers=_api_headers(),
                timeout=POLL_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            _fail(f"poll request failed: {exc}")
        if resp.status_code != 200:
            _fail(f"poll failed: {resp.status_code} — {resp.text}")

        data = resp.json()
        status = data.get("status")
        if status != last_status:
            print(f"   status: {status}")
            last_status = status
        if status == "completed":
            return data
        if status == "failed":
            _fail(f"render failed: {data.get('error') or 'unknown error'}")

        if time.monotonic() - start > deadline:
            _fail(
                f"stopped waiting after {deadline}s (book_id={book_id}, last "
                f"status={status}). The render may still finish on the server; "
                f"check with: publish_book.py status --book-id {book_id}"
            )
        time.sleep(POLL_INTERVAL)


def _download_source(base_url: str, book_id: str, output: Path) -> None:
    print(f"Downloading source for {book_id}...")
    try:
        resp = httpx.get(
            f"{base_url}/books/{book_id}/source",
            headers={"Authorization": f"Bearer {get_token()}"},
            timeout=SOURCE_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _fail(f"source request failed: {exc}")
    if resp.status_code != 200:
        _fail(f"source download failed: {resp.status_code} — {resp.text}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(resp.content)
    print(f"   ✅ Source → {output} ({len(resp.content) / 1024:.0f} KB)")


def _fail_if_book_info_incomplete(resp) -> None:
    """Turn a 422 ``book_info_incomplete`` into the missing list plus the server's hint.

    Any other 422 returns, so the caller reports it raw.
    """
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return
    if not isinstance(detail, dict) or detail.get("error") != "book_info_incomplete":
        return
    missing = ", ".join(detail.get("missing") or []) or "(none)"
    _fail(
        f"book information incomplete — missing: {missing}.\n"
        f"{detail.get('hint', '')}\n"
        "Nothing was rendered; your local content is unchanged."
    )


def cmd_publish(args):
    payload = _build_compose_payload(args)
    base_url = get_api_url()

    n_chapters = len(payload["chapters"])
    print(f"Publishing '{payload['title']}' ({n_chapters} chapters)...")
    try:
        resp = httpx.post(
            f"{base_url}/books/compose",
            json=payload,
            headers=_api_headers(),
            timeout=COMPOSE_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _fail(f"compose request failed: {exc}")

    if resp.status_code == 429:
        _fail("a render is already in progress for this account; try again later")
    if resp.status_code == 402:
        # Free-render allowance exhausted (shared with the MCP connector). The
        # raw body is a structured `detail` dict, which is noise to the user —
        # say what happened and what fixes it. Local content is untouched.
        _fail(
            "your free render allowance is used up for now. "
            "Your local content is unchanged — wait for the rolling window to "
            "roll over, or upgrade at https://proofbound.com to keep rendering."
        )
    if resp.status_code == 422:
        _fail_if_book_info_incomplete(resp)
    if resp.status_code not in (200, 202):
        _fail(f"compose failed: {resp.status_code} — {resp.text}")

    data = resp.json()
    book_id = data.get("book_id")
    if not book_id:
        _fail(f"server did not return a book_id: {data!r}")
    print(f"   Submitted. book_id = {book_id}")

    if args.no_wait:
        print("\n✅ Submitted (not waiting). Poll with:")
        print(f"   publish_book.py status --book-id {book_id}")
        return

    result = _poll_until_done(base_url, book_id, args.poll_timeout)
    pdf_url = result.get("pdf_url") or ""

    if args.download_src:
        _download_source(base_url, book_id, Path(args.download_src).expanduser())

    print("\n✅ Book published!")
    print(f"   book_id : {book_id}")
    if pdf_url:
        print(f"   PDF     : {pdf_url}")
    print("\n   The book now appears in your ProofBound dashboard.")


# ============================================
# status / download-src
# ============================================


def cmd_status(args):
    base_url = get_api_url()
    try:
        resp = httpx.get(
            f"{base_url}/books/{args.book_id}",
            headers=_api_headers(),
            timeout=POLL_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        _fail(f"request failed: {exc}")
    if resp.status_code != 200:
        _fail(f"{resp.status_code} — {resp.text}")
    data = resp.json()
    print(f"book_id : {data.get('book_id')}")
    print(f"status  : {data.get('status')}")
    if data.get("pdf_url"):
        print(f"pdf_url : {data['pdf_url']}")
    if data.get("error"):
        print(f"error   : {data['error']}")


def cmd_download_src(args):
    _download_source(get_api_url(), args.book_id, Path(args.output).expanduser())


# ============================================
# auth
# ============================================


def cmd_login(args):
    """Start browser sign-in, then wait for approval.

    Prints the sign-in link + code immediately (and opens the browser), then
    blocks until the user approves. When an agent runs this, run it in the
    background and relay the printed link/code while it waits.
    """
    try:
        data = start_device_login(open_browser=not args.no_browser)
    except RuntimeError as e:
        _fail(str(e))

    print("🔐 Sign in to ProofBound in your browser:")
    print(f"\n    {data['verification_uri_complete']}\n")
    print(f"   If asked, confirm this code:  {data['user_code']}")
    print("   (A browser window should have opened automatically.)")
    print("   Waiting for you to approve…\n")

    try:
        wait_for_device_login()
    except RuntimeError as e:
        _fail(str(e))
    print("✅ Logged in successfully")
    print(f"   API: {get_api_url()}")


def cmd_login_wait(args):
    """Resume polling a sign-in already started by `login`."""
    try:
        wait_for_device_login()
    except RuntimeError as e:
        _fail(str(e))
    print("✅ Logged in successfully")
    print(f"   API: {get_api_url()}")


def cmd_logout(args):
    logout()
    print("✅ Logged out")


# ============================================
# CLI
# ============================================


def main():
    parser = argparse.ArgumentParser(description="ProofBound book builder: fetch-prompts → write locally → publish")
    sub = parser.add_subparsers(dest="command", required=True)

    login_p = sub.add_parser("login", help="Sign in to ProofBound in your browser")
    login_p.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser; just print the sign-in link",
    )

    sub.add_parser("login-wait", help="Resume waiting for a started sign-in to be approved")

    sub.add_parser("logout", help="Log out and delete stored credentials")

    fp = sub.add_parser("fetch-prompts", help="Download canonical prompt templates")
    fp.add_argument("--output-dir", default=str(PROMPTS_CACHE), help=f"Cache dir (default: {PROMPTS_CACHE})")

    pub = sub.add_parser("publish", help="Submit local content → render → persist")
    pub.add_argument("--project-dir", required=True, help="Local content directory")
    pub.add_argument("--introduction", help="Override path to introduction markdown")
    pub.add_argument("--summary", help="Override path to summary markdown")
    pub.add_argument("--trim-size", help="Trim-size key (overrides cc-config.yml)")
    pub.add_argument(
        "--authoring-model",
        help=(
            "Identifier of the model that wrote the prose (e.g. claude-opus-5). "
            "ProofBound never generates chapters, so this is the only record of "
            "what authored the book; it is stored in the rendered cc-config.yml."
        ),
    )
    pub.add_argument(
        "--workflow-version",
        help=(
            "Version of the book-writing workflow followed (e.g. the skill "
            "version, or the get_book_workflow identity line). Stored in the "
            "rendered cc-config.yml so the book records what built it."
        ),
    )
    pub.add_argument("--no-wait", action="store_true", help="Submit and exit without polling")
    pub.add_argument("--download-src", help="After completion, save the source zip to this path")
    pub.add_argument(
        "--poll-timeout",
        type=int,
        default=DEFAULT_POLL_DEADLINE,
        help=f"Max seconds to wait for render (default: {DEFAULT_POLL_DEADLINE})",
    )

    st = sub.add_parser("status", help="Poll a book's render status")
    st.add_argument("--book-id", required=True)

    ds = sub.add_parser("download-src", help="Download a book's Quarto source zip")
    ds.add_argument("--book-id", required=True)
    ds.add_argument("--output", required=True, help="Destination .zip path")

    args = parser.parse_args()

    dispatch = {
        "login": cmd_login,
        "login-wait": cmd_login_wait,
        "logout": cmd_logout,
        "fetch-prompts": cmd_fetch_prompts,
        "publish": cmd_publish,
        "status": cmd_status,
        "download-src": cmd_download_src,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
