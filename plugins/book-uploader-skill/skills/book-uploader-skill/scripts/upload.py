#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fonttools",
#     "httpx>=0.27",
#     "pypdf>=6.21",
#     "pyyaml>=6.0",
# ]
# ///
"""Upload (or render) a book project to ProofBound over HTTP (thin client).

A thin client of the public ``/books/*`` endpoints on ``api.proofbound.com``.
No monorepo, no service-role ``.env`` — just a logged-in ProofBound account. The
book is owned by the authenticated user.

Two paths, selected automatically by whether a rendered PDF is present:

  * **PDF present** (under the project's Quarto output dir) → ``POST /books/upload``: the
    server persists the assembled project as-is (no rendering).
  * **No PDF** → ``POST /books/render``: the server renders the source with
    Quarto/TinyTeX, then persists. The script polls ``GET /books/{book_id}``
    until the render completes. No local Quarto/TinyTeX required.

Usage:
    uv run python scripts/upload.py login          # browser sign-in (no password)
    uv run python scripts/upload.py run --project-path /path/to/folder \\
        [--title ...] [--author ...] [--subtitle ...] [--description ...]
        [--book-blurb ...]
    uv run python scripts/upload.py logout
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from pathlib import Path

import auth
import httpx
from _print_fonts import font_warning
from _quarto_output import (
    AmbiguousPdfError,
    QuartoOutputDirError,
    configured_output_dir,
    find_pdf as find_output_pdf,
)
from _upload_plan import ALLOWED_EXTENSIONS, SymlinkEscapeError, iter_upload_files  # noqa: F401 — re-exported

DASHBOARD_BASE = "https://app.proofbound.com/books"
UPLOAD_TIMEOUT = 180
# Render submission is quick (returns 202); the render runs server-side.
RENDER_SUBMIT_TIMEOUT = 60
# Poll budget for a server-side render to finish. Must exceed the server's
# whole-job backstop (RENDER_JOB_TIMEOUT_SECONDS, 2040s: two render passes, since
# the corrective gutter re-render counts) or a slow-but-successful render reads as
# unfinished. Queue wait behind other renders is not bounded, so a timeout still
# only means "may still finish". A parity test pins it.
POLL_INTERVAL = 5
POLL_TIMEOUT = 2400


def _find_pdf(project_path: Path) -> Path | None:
    """Locate the rendered book PDF under the project's Quarto output dir."""
    return find_output_pdf(project_path)


CC_CONFIG_NAME = "cc-config.yml"


def _cc_config_with_provenance(file_path: Path, authoring_model: str) -> bytes | None:
    """Return the project's cc-config.yml with ``authoring_model`` merged in.

    Returns ``None`` if the file can't be parsed, so an unreadable config falls
    back to being shipped verbatim rather than failing the upload — provenance is
    metadata, never worth losing a book over.
    """
    try:
        import yaml

        config = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(config, dict):
        return None
    config["authoring_model"] = authoring_model
    return yaml.safe_dump(config, default_flow_style=False, sort_keys=False).encode()


def build_upload_zip(
    project_path: Path,
    pdf_path: Path | None,
    authoring_model: str | None = None,
) -> bytes:
    """Zip the allowlisted source tree, optionally plus a single rendered PDF.

    Build-output dirs are excluded from the source walk so we don't ship stray
    .html/.tex — the standard ``_book/``/``docs/`` plus whatever a custom
    ``project.output-dir`` names. When *pdf_path* is given (upload path) it is added
    back explicitly at its relative path so the server can find it; when it is
    ``None`` (render path) only source is shipped and the server renders it.

    *authoring_model* is merged into the zipped ``cc-config.yml`` — ProofBound
    never writes the prose, so the client is the only party that knows it. It is
    patched into the ZIP ENTRY rather than the file on disk: this is the user's own
    project directory, and an upload should not quietly rewrite it. Doing it here
    covers both branches at once, which matters because the two take different
    server routes (``/books/upload`` bypasses the render pipeline entirely, so a
    server-side stamp would never reach it).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, rel in iter_upload_files(project_path, pdf_path):
            if authoring_model and rel.as_posix() == CC_CONFIG_NAME:
                patched = _cc_config_with_provenance(file_path, authoring_model)
                if patched is not None:
                    zf.writestr(str(rel), patched)
                    continue
            zf.write(file_path, rel)
    return buf.getvalue()


def cmd_login(args: argparse.Namespace) -> int:
    """Start browser sign-in, then wait for approval (no password needed)."""
    try:
        data = auth.start_device_login(open_browser=not args.no_browser)
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    print("🔐 Sign in to ProofBound in your browser:")
    print(f"\n    {data['verification_uri_complete']}\n")
    print(f"   If asked, confirm this code:  {data['user_code']}")
    print("   (A browser window should have opened automatically.)")
    print("   Waiting for you to approve…\n")

    try:
        auth.wait_for_device_login()
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print("✓ Logged in")
    print(f"  API: {auth.get_api_url()}")
    return 0


def cmd_login_wait(args: argparse.Namespace) -> int:
    """Resume polling a sign-in already started by `login`."""
    try:
        auth.wait_for_device_login()
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print("✓ Logged in")
    print(f"  API: {auth.get_api_url()}")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    auth.report_logout(auth.logout(), "✓")
    return 0


def _error_detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", resp.text)
    except Exception:
        return resp.text


def _do_upload(base_url, token, zip_bytes, overrides) -> int:
    """POST /books/upload — server persists the assembled project (with PDF)."""
    print(f"☁️  Uploading to {base_url}/books/upload ...")
    try:
        resp = httpx.post(
            f"{base_url}/books/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"project": ("project.zip", zip_bytes, "application/zip")},
            data=overrides,
            timeout=UPLOAD_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        print(f"❌ Upload request failed: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(
            f"❌ Upload failed (HTTP {resp.status_code}): {_error_detail(resp)}",
            file=sys.stderr,
        )
        return 1

    body = resp.json()
    book_id = body.get("book_id")
    print()
    print("✅ Upload complete!")
    _print_warnings(body)
    print(f"   Book ID: {book_id}")
    if body.get("pdf_url"):
        print(f"   PDF: {body['pdf_url']}")
    print(f"🎉 Dashboard: {DASHBOARD_BASE}/{book_id}")
    return 0


def _print_warnings(body: dict) -> None:
    """Surface the server's advisory ``warnings`` — they are the whole point.

    Both submit responses can carry non-fatal notes (a short manuscript, a book
    that cannot record what wrote it). Reading only ``book_id`` out of the body,
    as this script used to, made the advisory invisible on exactly the paths it
    was added for.
    """
    for warning in body.get("warnings") or []:
        print(f"   ⚠️  {warning}")


def _do_render(base_url, token, zip_bytes, overrides) -> int:
    """POST /books/render then poll GET /books/{id} — server renders the source."""
    headers = {"Authorization": f"Bearer {token}"}
    print(f"☁️  Submitting render to {base_url}/books/render ...")
    try:
        resp = httpx.post(
            f"{base_url}/books/render",
            headers=headers,
            files={"project": ("project.zip", zip_bytes, "application/zip")},
            data=overrides,
            timeout=RENDER_SUBMIT_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        print(f"❌ Render request failed: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 202:
        print(
            f"❌ Render submission failed (HTTP {resp.status_code}): " f"{_error_detail(resp)}",
            file=sys.stderr,
        )
        return 1

    accepted = resp.json()
    book_id = accepted.get("book_id")
    print(f"   Accepted — book {book_id}. Rendering server-side...")
    _print_warnings(accepted)

    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            poll = httpx.get(f"{base_url}/books/{book_id}", headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            print(f"   ⏳ poll error (retrying): {exc}", file=sys.stderr)
            continue
        if poll.status_code != 200:
            print(f"   ⏳ poll HTTP {poll.status_code} (retrying)...", file=sys.stderr)
            continue
        body = poll.json()
        status = body.get("status")
        if status == "completed":
            print()
            print("✅ Render complete!")
            print(f"   Book ID: {book_id}")
            if body.get("pdf_url"):
                print(f"   PDF: {body['pdf_url']}")
            print(f"🎉 Dashboard: {DASHBOARD_BASE}/{book_id}")
            return 0
        if status == "failed":
            print(
                f"❌ Render failed: {body.get('error') or 'unknown error'}",
                file=sys.stderr,
            )
            return 1
        print(f"   ⏳ {status}...")

    print(
        f"⏳ Stopped waiting after {POLL_TIMEOUT}s. The render may still finish on "
        f"the server; check the dashboard: {DASHBOARD_BASE}/{book_id}",
        file=sys.stderr,
    )
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    project_path = Path(args.project_path).resolve()
    if not project_path.is_dir():
        print(f"❌ Project path is not a directory: {project_path}", file=sys.stderr)
        return 2

    try:
        token = auth.get_token()
    except RuntimeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    overrides = {
        k: v
        for k, v in (
            ("title", args.title),
            ("author", args.author),
            ("subtitle", args.subtitle),
            ("description", args.description),
            # Also sent as a form field, not only patched into the zipped
            # cc-config.yml: the server decides whether to raise the
            # "cannot say what wrote it" advisory, and it should see the same
            # answer the book will carry.
            ("authoring_model", args.authoring_model),
        )
        if v
    }
    base_url = auth.get_api_url()

    # Fail here rather than shipping a project the server will reject at intake.
    try:
        configured_output_dir(project_path)
    except QuartoOutputDirError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    # getattr: callers hand-build an args namespace (the tests, and any wrapper),
    # so a missing attribute must read as "not supplied".
    book_blurb = getattr(args, "book_blurb", None)

    try:
        pdf_path = _find_pdf(project_path)
    except AmbiguousPdfError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    if pdf_path is not None:
        rel = pdf_path.relative_to(project_path)
        fonts = font_warning(pdf_path)
        if fonts:
            print(f"⚠️  {fonts}")
        print(f"📦 Zipping {project_path.name} (+ {rel})...")
        try:
            zip_bytes = build_upload_zip(project_path, pdf_path, args.authoring_model)
        except SymlinkEscapeError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print(f"   {len(zip_bytes) / 1_048_576:.2f} MB")
        # book_blurb rides the upload path only — /books/render has no such form
        # field, and FastAPI would drop it silently rather than reject it.
        if book_blurb:
            overrides["book_blurb"] = book_blurb
        return _do_upload(base_url, token, zip_bytes, overrides)

    # No PDF — ship source only and let the server render it.
    if book_blurb:
        print("   ⚠️  --book-blurb is ignored on the render path (no local PDF); " "set it later from the dashboard.")
    print("📦 No local PDF found — zipping source for server-side render...")
    try:
        zip_bytes = build_upload_zip(project_path, None, args.authoring_model)
    except SymlinkEscapeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    print(f"   {len(zip_bytes) / 1_048_576:.2f} MB")
    return _do_render(base_url, token, zip_bytes, overrides)


def main() -> int:
    parser = argparse.ArgumentParser(description="ProofBound book uploader (thin client)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Sign in to ProofBound in your browser")
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser; just print the sign-in link",
    )
    login.set_defaults(func=cmd_login)

    login_wait = sub.add_parser("login-wait", help="Resume waiting for a started sign-in")
    login_wait.set_defaults(func=cmd_login_wait)

    logout = sub.add_parser("logout", help="Log out and clear credentials")
    logout.set_defaults(func=cmd_logout)

    run = sub.add_parser("run", help="Upload an assembled project folder")
    run.add_argument("--project-path", required=True)
    run.add_argument("--title", default=None, help="Override title")
    run.add_argument("--author", default=None, help="Override author")
    run.add_argument("--subtitle", default=None, help="Override subtitle")
    run.add_argument("--description", default=None, help="Override description")
    run.add_argument(
        "--book-blurb",
        default=None,
        help=(
            "Back-cover marketing copy (max 1200 chars). Distinct from "
            "--description, which is the book's concept. Upload path only."
        ),
    )
    run.add_argument(
        "--authoring-model",
        default=None,
        help=(
            "Model that wrote the prose (e.g. claude-opus-5). Recorded in "
            "cc-config.yml so the finished book says what authored it."
        ),
    )
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
