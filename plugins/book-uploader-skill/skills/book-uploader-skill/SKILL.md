---
name: book-uploader-skill
description: Upload a local Quarto project or loose .qmd chapters to a ProofBound account (validates, fixes config, scans secrets, renders if no PDF). Triggers - upload to ProofBound, import a Quarto project.
license: ProofBound Source-Available License (see LICENSE)
version: 0.1.13
---

# ProofBound Book Uploader Skill

Take a local folder, get it onto a ProofBound account. A conversational pre-flight (validate → auto-fix → render-vs-upload → security scan) followed by an authenticated HTTP send to `api.proofbound.com`: `POST /books/upload` when the folder already has a rendered PDF, or `POST /books/render` (server renders with Quarto/TinyTeX, then poll to completion) when it doesn't. The book lands in the logged-in user's library — no monorepo checkout, service-role credentials, or local Quarto required.

## When to Use This Skill

Trigger when users:

- Say "upload to ProofBound", "push this book to ProofBound", "import a Quarto project"
- Point at a folder of `.qmd` files and want it on their dashboard
- Want to migrate an externally-built book into the platform
- Have hand-edited or AI-generated content and need it visible alongside platform-generated books

**Do not** trigger this for:

- Generating a brand-new book — that's `book-builder-skill`.
- Generating cover artwork — that's `cover-generator-skill`.

## Prerequisites

A **ProofBound account**. The skill uploads over HTTPS to `api.proofbound.com`; it does not need the monorepo, a `.env`, or service-role keys. The book is owned by whoever is logged in.

1. **UV**: `brew install uv` or `pip install uv`. It auto-installs the scripts'
   Python deps on first run (inline PEP 723 metadata) — **no `uv sync` step**.
2. **A ProofBound account** (free) — sign-in is in the browser, no password.

Commands run from the skill directory (where this `SKILL.md` lives).

## Setup: sign in (browser, no password)

Sign in once. Credentials (access + refresh tokens) are stored at `~/.proofbound/credentials.json` — the same shared location the cover-generator-skill uses — and auto-refresh on expiry.

```bash
uv run scripts/upload.py login
```

This opens a ProofBound sign-in page and prints a link + short code. When Claude runs it, run it in the **background** and relay the link/code to the user ("Open this, sign in, click Approve"); a browser also opens automatically. The command finishes on approval. If interrupted, resume with `uv run scripts/upload.py login-wait`.

To target a non-production API, set `PROOFBOUND_API_URL` (e.g. `http://localhost:8001` against a local cc-template-api). Log out with `uv run scripts/upload.py logout`.

---

## The Five Phases

### Phase 1: Discover

**Goal**: Understand what's in the folder.

```bash
uv run python scripts/validate_folder.py --project-path /path/to/folder --json
```

The script returns a structured report:

```json
{
  "ready_to_upload": false,
  "blockers": [
    "missing _quarto.yml",
    "missing cc-config.yml",
    "no PDF in _book/ or docs/"
  ],
  "warnings": ["no index.qmd", "no covers/ directory"],
  "chapters": ["chapters/chapter_01.qmd", "chapters/chapter_02.qmd"],
  "config_files": { "_quarto.yml": false, "cc-config.yml": false },
  "pdf_path": null,
  "covers": { "artwork": false, "kdp_pdf": false, "lulu_pdf": false }
}
```

`warnings` also lists pipe tables the renderer cannot fit, as
`chapters/x.qmd:LINE: N-column table ... consider transposing`. They never block
an upload; offer to transpose the table so the long dimension (years, categories)
runs down the rows.

Show the user a human-readable summary. Decide which phases to run:

- If `_quarto.yml` or `cc-config.yml` missing → **Phase 2** (auto-fix)
- **Phase 3** (render-vs-upload) — `pdf_path` decides the endpoint; no PDF is fine
- Always → **Phase 4** (security scan)
- Final → **Phase 5** (upload or render)

### Phase 2: Auto-fix Missing Config (only when needed)

**Goal**: Generate a minimal `_quarto.yml` and `cc-config.yml` that will render and validate.

Collect from the user:

| Parameter | Required | Notes                        |
| --------- | -------- | ---------------------------- |
| Title     | Yes      | Book title                   |
| Author    | Yes      | Author name                  |
| Subtitle  | No       | Empty string if not provided |

Then run:

```bash
uv run python scripts/autofix_folder.py \
  --project-path /path/to/folder \
  --title "Book Title" \
  --author "Author Name" \
  --subtitle "Optional Subtitle" \
  --trim-size 6x9
```

This writes `_quarto.yml` and `cc-config.yml`. Behavior:

- **Discovers chapters** in `chapters/*.qmd` first, falling back to `*.qmd` in the project root. Sort by leading number when filenames look numbered (`chapter_01.qmd`, `01-intro.qmd`); otherwise alphabetical.
- **Skips special files** (`index.qmd`, `summary.qmd`, `references.qmd`, `colophon.qmd`) from the chapters list — they're added if and only if they exist on disk.
- **Never overwrites** existing files. If `_quarto.yml` is present, the script leaves it alone and warns. Same for `cc-config.yml`.
- **Emits a simple template** that renders with Quarto's default `pdflatex` engine — no XeLaTeX, no Crimson Pro/Inter requirement. The platform applies the full ProofBound styling when the book is rebuilt server-side.
- **Sets interior page geometry** from `--trim-size` (`6x9` default, or `a5` / `digest`), including the KDP inside margin (`inner=0.75in`, `outer=0.75in`; the server widens the gutter at render time if the book's length needs it). Omit the flag for standard US Trade 6×9.
- **Declares `book_type` in `cc-config.yml`** (default `nonfiction_practical`, `--book-type` to change): the server requires one and never picks it for the author. An existing `cc-config.yml` without it gets the line appended, and the script says so. To leave the description, back-cover blurb or author bio out on purpose, record the user's explicit decline as `declined: [author_bio]` in `cc-config.yml`; `validate_folder.py` checks the names and warns about any of the three that is neither supplied (the blurb counts from `blurb.md` or `blurb:` in `covers/cover-config.yml`) nor declined.
- **Declares `trim_size` in `cc-config.yml`** from the same flag. The server reads the trim from `cc-config.yml` (not from the `_quarto.yml` geometry) to size the cover and the library row, so the two must agree.
- **Titles headerless chapters.** A chapter `.qmd` with no front-matter `title:` and no top-level `#` heading gets a `title:` block — from `outline.yml` when the file's leading number matches a chapter there, otherwise from the filename (`chapter_03-getting-started.qmd` → "Getting Started", `chapter_3.qmd` → "Chapter 3"). Files that already carry a title are not touched. ProofBound only writes chapter titles on the compose paths; on upload/render an untitled chapter renders with a blank heading and TOC entry. The fix summary lists every title added — tell the user, and adjust any they don't like before uploading. When titles were added and the output dir already held a rendered PDF, that PDF is set aside as `<name>.pdf.stale` so the upload step cannot ship the old titleless render; re-render, or let the server render.

After auto-fix, **fall through to Phase 3**.

### Phase 3: Render-vs-Upload

**Goal**: Decide which endpoint the upload script will use — no local render required.

Check the project's Quarto output dir for a `*.pdf` — `project.output-dir` from
`_quarto.yml` first when it sets one, then the legacy `<project>/_book/` and
`<project>/docs/` (the same search order the server's intake uses). The first of
those holding any PDF must hold exactly one — several (a stale render beside a
cover proof, say) block the upload rather than letting name order pick the book;
ask the user which to keep.

- **PDF present** → the script `POST`s to `/books/upload`; the server persists the
  assembled project as-is.
- **No PDF** → the script `POST`s the source to `/books/render`; the server runs
  Quarto/TinyTeX, then persists. The script polls until the render finishes.

The selection is automatic in `upload.py` (see Phase 5) — you do **not** need to
gate on the PDF or ask the user to run `quarto render` locally. Local Quarto is
optional: render it yourself for a faster path and a preview, or let the server
do it. If the user explicitly wants to render locally first, that still works and
takes the `/books/upload` path.

When a local PDF is present, `validate_folder.py` and `upload.py` also check its
fonts and **warn** (never block) if any would be garbled by KDP's print converter
(a CFF font on a non-1000-unit glyph grid, or a font that is not embedded). When
the warning appears, tell the user what it names and offer to remove the local PDF
so the server renders the book with print-safe fonts; uploading the local PDF
anyway is the user's call. If the check itself could not run, the warning says so.

### Phase 4: Security Scan

**Goal**: Catch `.env`, credentials, and large files before they end up in R2.

```bash
uv run python scripts/security_scan.py --project-path /path/to/folder --json
```

Returns a list of suspicious files with reason tags:

```json
{
  "findings": [
    { "path": ".env", "reason": "env-file" },
    { "path": "scripts/credentials.json", "reason": "credential-name" },
    { "path": "raw-video.mp4", "reason": "large-file", "size_mb": 47.3 }
  ]
}
```

The upload zip is built from an **allowlist** (the server's own `ALLOWED_EXTENSIONS`, imported from the vendored `proofbound_portable.upload`): only source/config text (`.qmd .md .yml .yaml .bib .csl .json .txt .csv .tsv`) and book assets (`.pdf .png .jpg .jpeg .svg .eps .css .ttf .otf`) are included, plus the rendered PDF. Build-output dirs (`_book/`, `docs/`, or a custom `project.output-dir`) and excluded dirs (`.git`, `.venv`, `node_modules`, …) are skipped. The server independently re-validates and **rejects** any disallowed file type, oversized entry, symlink, or zip-slip path.

Because of the allowlist, a stray `.env` (no recognized extension) is **not** uploaded. The security scan is still worth running so the user knows what's in the folder — but a `credentials.json` _would_ match `.json` and ship. Confirm before proceeding if any findings are present:

> Found `scripts/credentials.json` (a `.json` file that would be uploaded). Move or delete it, or confirm "upload anyway" to proceed.

Only continue on explicit user confirmation.

### Phase 5: Upload or Render

**Goal**: Send the folder over HTTP and report the dashboard URL.

```bash
uv run scripts/upload.py run \
  --project-path /path/to/folder \
  [--title "..."] [--author "..."] [--subtitle "..."] [--description "..."]
  [--book-blurb "..."]
```

This:

1. Gets a valid access token via `auth.py` (auto-refresh; errors if not logged in).
2. Looks for a rendered PDF under the project's Quarto output dir (`project.output-dir`
   first, then `_book/`/`docs/`) and picks the path:
   - **PDF found** → zips the **allowlisted** source tree plus that single PDF
     (build artifacts like `.html`/`.tex` are dropped) and `POST`s to
     `{api}/books/upload`.
   - **No PDF** → zips the source only and `POST`s to `{api}/books/render`
     (HTTP 202), then polls `GET {api}/books/{book_id}` every few seconds until
     `completed` or `failed`.
3. Prints the returned `book_id`, PDF URL, and dashboard URL.

The optional `--title/--author/--subtitle/--description` flags override what the server reads from `_quarto.yml`/`outline.yml`/`cc-config.yml`; they populate the library row only and never change uploaded files.

`--book-blurb` is the **back-cover marketing copy** (max 1200 chars), which is a
different thing from `--description` (the book's concept, what it is about). The server
reads the blurb from `blurb.md`, else from the `blurb:` key of `covers/cover-config.yml`
(where the cover-generator skill keeps it); `--book-blurb` overrides both. The flag rides
the **upload path only** — `/books/render` has no such field, so on a
source-only folder the script says so rather than dropping it silently.

Step 3 also prints any `warnings` the server returned. Beyond the provenance note (no
`authoring_model`), these now include author-file advisories: an `author.md` truncated to
500 chars, Windows line endings converted, or a photo stored under a corrected extension.
Surface them — the server cannot reach the user, and this script is the only party that can
tell them their author files were altered.

Show the user the dashboard URL on success: `https://app.proofbound.com/books/<book_id>`.

---

## Failure Modes

**Required file missing** (after auto-fix attempted):

```
The folder is not upload-ready:
  - missing cc-config.yml (auto-fix offered to create one — run with --title and --author)
```

**Sensitive file detected** (allowlisted type that would ship, e.g. `.json`):

```
⚠️  Found scripts/credentials.json — it matches an allowlisted extension and would
    be uploaded. Delete or move it, or type "upload anyway" to proceed.
```

**Print-unsafe fonts in the local PDF** (warning, not an error):

```
⚠️  book.pdf has fonts KDP's print converter garbles: Inter-ExtraBold (CFF outlines
    on a 2048-unit grid). Remove the local PDF so the server renders the book with
    print-safe fonts, or re-render locally with TrueType fonts.
```

**No PDF** (informational, not an error): the script ships the source and the
server renders it. A large book can take over 30 minutes, longer than a
foreground Bash call allows (10 minutes at most), so when Claude runs this, run
it in the **background** and read its output when it finishes. Expect a wait while it polls; a `failed` status prints
the server's render diagnostic (usually a LaTeX/Quarto error in the source).
The script waits up to 40 minutes (above the server's 2040s render cap, which includes the corrective gutter re-render); if it stops waiting, the render may still
finish on the server, so check the dashboard link it prints.

**Render already in progress** (`429`): the account already has a book rendering.
Wait for it to finish (poll its dashboard entry), then re-run.

**Upload/render returns an error**: the script prints the server's `detail`. Common causes:

- `401` — not logged in or token expired: run `upload.py login`
- `422` — invalid project (missing `_quarto.yml`/`cc-config.yml`, no `.qmd`, a disallowed file type, or — for the upload path only — no PDF) — re-run validate/auto-fix
- `413` — the zip or a file exceeds the size caps (upload 80 MB body / render 25 MB; 80 MB/file, 150 MB total decompressed)
- `429` — a render is already in progress for this account (render path only)

---

## Notes for Future Maintainers

- This skill mirrors the patterns from `cover-generator-skill` (shared `auth.py` + `~/.proofbound/credentials.json`) and `book-builder-skill` (folder validation idioms).
- `scripts/auth.py` (login/refresh/logout against `/auth/*`) is added when the skill is packaged and is shared with the other ProofBound skills; it is not part of this skill's own source.
- The auto-fix template is intentionally **simpler** than the platform's canonical `_quarto.yml`. The canonical template needs XeLaTeX + KOMA-Script + Crimson Pro/Inter fonts, which most local machines don't have. The platform applies the full styling when it rebuilds the book server-side.
- The client `ALLOWED_EXTENSIONS` is the server allowlist itself (`proofbound_portable.upload`, vendored into `scripts/_vendor/`), so there is nothing to keep in sync.
- Server-side PDF rendering is supported: a folder with no PDF takes the
  `/books/render` path (submit → poll). A folder that already has a PDF takes the
  `/books/upload` path (persist as-is). `upload.py` chooses automatically.
