---
name: book-builder-skill
description: Conversational book creation workflow for ProofBound. Use when users want to "create a book", "help me write a book", "turn my notes into a book", or upload documents mentioning books or publishing. Guides through intake, design, TOC, and chapter/intro/summary writing locally, then publishes via the ProofBound API — the platform scaffolds the canonical Quarto project, renders the PDF, and saves the book to the user's library. Requires a ProofBound login; no local Quarto/TinyTeX needed. If the ProofBound MCP connector is available in this session, prefer its get_book_workflow (the canonical, always-current workflow); this skill is the standalone Claude Code / filesystem fallback.
license: ProofBound Source-Available License (see LICENSE)
version: 4.8.2
---

# ProofBound Book Builder Skill

> **Source of truth — read first.** If the ProofBound MCP connector is available
> in this session (the `get_book_workflow` / `mcp__…Proofbound…` tools are
> present), call **`get_book_workflow` FIRST** and follow it end to end —
> including its render tools (`render_book` / `compose_staged_book` /
> `render_staged_book`). Call it with **`can_run_scripts: true`**: you are in
> Claude Code, so you can run `publish_book.py fetch-prompts` and render a
> template with real values, which beats reading Jinja2 by eye — and it returns a
> fetch command instead of ~14 KB of template source. If that fetch fails (not
> signed in, no network), call `get_book_workflow` again with
> `can_run_scripts: false` to get the templates inline. It is the canonical,
> always-current workflow (phases, prompts, `book_type` and render-path
> decisions). **Use this skill only when the
> MCP connector is _not_ available** — it is the standalone Claude Code /
> filesystem path (local files + `publish_book.py` + durable `state.py` resume).
> Both surfaces share the same backend and the same `/books/prompts`; where they
> differ, `get_book_workflow` wins.

This skill guides users through a conversational book-creation workflow. Claude
helps the user design the book and writes the prose locally; the **ProofBound
platform** then does the heavy lifting — it scaffolds the canonical Quarto
project (ProofBound branding + colophon), renders the PDF, and persists the
finished book to the user's library. The result is indistinguishable from a
webapp-generated book and appears in the user's ProofBound dashboard.

This is a **thin client**: the only network calls are to `api.proofbound.com`.
There is **no local Quarto, TinyTeX, or rendering** — the platform owns all of
that. Prompt templates are fetched from the platform at runtime (so the skill
never drifts from the canonical prompts).

## When to Use This Skill

Trigger this skill when users:

- Say "create a book", "help me write a book", "I want to write a book"
- Ask about "turning notes into a book" or "book from documents"
- Upload files (PDF, txt, md, docx) and mention books or publishing
- Reference "book project", "manuscript", or "publishing"

## Prerequisites

1. **A ProofBound account** (free at [proofbound.com](https://proofbound.com)).
   Nothing to install and **no password to type** — sign-in happens in the
   browser. The only tool needed is [`uv`](https://docs.astral.sh/uv/), which
   auto-installs the script's dependencies on first run (the scripts carry inline
   [PEP 723](https://peps.python.org/pep-0723/) metadata, so there is **no
   `uv sync` step**).

   **Signing the user in (Claude drives this).** When the user isn't logged in
   yet, run the browser sign-in for them. Because the command prints the sign-in
   link and then blocks until the user approves, run it in the **background** and
   relay the link:

   ```bash
   # From the skill directory (where this SKILL.md lives).
   uv run scripts/publish_book.py login        # run in background
   ```

   It prints a line like:

   ```
   🔐 Sign in to ProofBound in your browser:
       https://app.proofbound.com/cli?code=WXYZ-1234
      If asked, confirm this code:  WXYZ-1234
   ```

   Show that link + code to the user ("Open this link, sign in, and click
   Approve"). A browser window also opens automatically on their machine. The
   command finishes once they approve; credentials are saved to
   `~/.proofbound/credentials.json` and auto-refresh thereafter. If the
   background process is interrupted, resume the wait with
   `uv run scripts/publish_book.py login-wait`.

   For local testing, override the API base with `PROOFBOUND_API_URL` (e.g.
   `http://localhost:8001`); the sign-in link then points at your local web app.

2. **Fetch the canonical prompts** (once per session, or whenever they may have
   changed):

   ```bash
   uv run scripts/publish_book.py fetch-prompts
   ```

   This writes the platform's prompt templates, guidance docs, and the phase
   narrative (`book-workflow-narrative.md`) to `~/.proofbound/cache/prompts/`.
   `render_prompt.py` and `generate_chapters.py` read from that cache by default.

3. **Generation mode** (how the prose gets written — both write locally):

   - **Session Mode** (default, recommended): Claude writes the chapters, intro,
     and summary directly in the conversation. No API key needed — uses the
     active Claude session.
   - **Script Mode** (advanced): batch chapter generation against the Anthropic
     API via `generate_chapters.py`. Requires `ANTHROPIC_API_KEY` and the
     `script-mode` extra (`uv sync --extra script-mode`).

4. **No Quarto/TinyTeX.** Rendering happens server-side. Nothing to install
   locally beyond `uv` (which fetches the scripts' Python deps on first run).

## Workflow Overview

```
Phase 0: Resume Check     → Check for in-progress books, offer to resume
Phase 1: Intake           → Gather topic, source materials, book_type
Phase 1b: Research Map    → research-notes.md source ledger      [GATE 1]
Phase 2: Book Design      → Write cc-config.yml (local)
Phase 3: TOC Architecture → Write outline.yml (local)            [GATE 2]
                          → Draft book-bible.md                  [GATE 3]
Phase 4: Content          → Chapters, intro, summary, blurb.md + author.md (local)
Phase 5: Publish          → publish_book.py: submit → poll → dashboard URL
Phase 6: Iteration        → Rewrite locally, re-publish
```

Phases 1–5 follow the served phase narrative (`book-workflow-narrative.md`,
below); this file carries only the skill's own mechanics. The narrative has
**three approval gates, and no more**: the research map, the outline, and the
book bible. Chapters are _not_ gated.

Everything in Phases 1–4 is written to a **local project directory**, e.g.
`~/Books/<slug>/`:

```
~/Books/<slug>/
├── book-state.json        # canonical state (schema v2) — managed by state.py
├── PROGRESS.md            # generated projection of book-state.json; never a source of truth
├── cc-config.yml          # title, author, subtitle?, description?, writing_style?, book_type?, trim_size?
├── outline.yml            # chapters: [{number, chapter_title, chapter_description?, sections}]
├── book-bible.md           # local style sheet for consistency; never published
├── research-notes.md       # (optional) source ledger: IDs, URLs, access dates, claims, open gaps
├── chapter-summaries.md    # 150–250 words per saved chapter; what resume reads instead of full text
├── chapters/
│   ├── chapter_1.qmd       # one file per outline chapter (.qmd or .md)
│   └── chapter_2.qmd
├── introduction.md         # book preface/introduction prose
├── summary.md              # book summary prose
├── blurb.md                # (optional) back-cover blurb / marketing copy, <= 1200 chars
├── author.md               # (optional) author bio, <= 500 chars
├── author.jpg              # (optional) author photo, JPEG/PNG, <= 5 MB
└── references.bib          # (optional) BibTeX → rendered References section
```

`book-state.json`, `PROGRESS.md`, `book-bible.md`, `research-notes.md`, and
`chapter-summaries.md` are **working files** — they steer your writing and are
never sent to compose.

`publish_book.py publish --project-dir ~/Books/<slug>` reads exactly this layout.
The Quarto boilerplate (`index.qmd`, `summary.qmd`, `colophon.qmd`, `_quarto.yml`,
ProofBound logo, copyright page) is **not** written locally — the platform
injects all of it canonically during `compose`.

State is persisted durably after every artifact via `python3 scripts/state.py`,
which also regenerates `PROGRESS.md` from it on every write.
See **State Management** below.

## Phase 0: Resume Check

**Goal**: Never lose work to a dropped session. Before starting a new book,
check whether the user has an in-progress one.

**Actions** (run before asking anything in Phase 1):

1. List in-progress books:
   ```bash
   python3 scripts/state.py list
   ```
   Rows flagged `<orphan: …>` (the project dir is gone) or `<corrupt: …>` need a
   decision from the user — they are never auto-repaired or deleted.
2. If any appear, ask: "I see an in-progress book `<slug>` (phase N, last
   updated …, title …). Resume it, start fresh, or delete the old state?"
3. On **resume**, read in this order — **`book-state.json` first**, never the
   chapter text:
   1. `python3 scripts/state.py show <slug>` — this loads canonical
      `book-state.json` (migrating a schema-v1 record if needed) and
      regenerates `PROGRESS.md` when it is missing, stale, or malformed.
   2. `book-bible.md` and `chapter-summaries.md` — this is your context for
      continuity, and it is why resume does **not** re-read finished chapters.
   3. Full chapter text **only** for chapters whose stage is not `saved` (an
      incomplete artifact), or for a targeted continuity check.
   4. Reconcile state against disk: every chapter marked `saved` must have a
      non-empty `chapters/chapter_<N>.qmd`. Re-queue anything missing.
      If `book-state.json` will not parse, rebuild it from the artifacts rather
      than hand-editing: `python3 scripts/state.py rebuild <slug>`.
   5. **Voice calibration — one bounded, declared read.** Before drafting, you
      may read the OPENING of ONE saved chapter (the most recent, ~first 60
      lines) to re-hear the voice — once per resumed session, and tell the user
      you are doing it. Reading more chapters, or a whole chapter, is a full
      re-read by another name; use `chapter-summaries.md` and `book-bible.md`.
   6. **Then finish ONE chapter and stop.** Do exactly the work that was in
      flight — the incomplete chapter, or if all are `saved`, the next single
      artifact in the workflow. Save it, report a short summary plus word count,
      and **stop for a go-ahead** before starting anything else. Do not queue,
      pitch, or draft the remaining chapters, and do not decide to finish the
      book in one run: a resumed session is picking up someone else's plan, so
      ask before widening it. The normal no-per-chapter-approval pace
      (Phase 4a) resumes once the user says to continue.
4. On **start fresh**, Phase 1 begins. Create the state record as soon as you
   have a slug — this also creates the project dir and its `PROGRESS.md`:
   ```bash
   python3 scripts/state.py init <slug> --project-dir ~/Books/<slug> \
     --title "..." --author "..." --target-words N --chapter-count N
   ```
   `--project-dir` defaults to `~/Books/<slug>`; pass it explicitly when the
   user wants the book somewhere else.
5. On **delete**, remove the index record: `python3 scripts/state.py delete <slug>`
   (add `--purge` to also delete the project's `book-state.json`/`PROGRESS.md`;
   the manuscript files are never touched).

---

## The phase narrative (served — read it before Phase 1)

What to do in each phase — intake, `book_type`, the research map, design, the
two-pass outline and its gates, the book bible, the per-chapter write loop, the
bibliography, the back-cover copy, and the pre-publish checklist — is **not** in
this file. It is `book-workflow-narrative.md` in the prompt cache, the same text
the MCP connector's `get_book_workflow` serves, rendered for this skill by
`fetch-prompts`. Read it before Phase 1 and follow it phase by phase; it changes
without a skill release, so never work from a remembered copy. If it is missing
from the cache, run `fetch-prompts` again — do not improvise the workflow.

This file adds only what the narrative leaves to the client: **where each
artifact lives on disk** and **which `state.py` command records it**. The phase
sections below carry exactly that.

## Phase 1: Intake

Follow the narrative's Phase 1. Then:

1. Summarize any uploaded files (PDF, txt, md, docx) into the `source_materials`
   field of `cc-config.yml` (Phase 2) — it steers your writing and is never sent
   to the server.
2. Create the state record as soon as you have a slug (Phase 0, step 4).
3. Record the chosen `book_type` in `cc-config.yml` (Phase 2).

## Phase 1b: Research Map — **Gate 1**

Write the source ledger to `~/Books/<slug>/research-notes.md`. Material packs in
Phase 4 carry its source IDs forward, so provenance survives to bibliography
time. Record the gate either way:

```bash
python3 scripts/state.py gate <slug> research_map not_applicable   # skipped
python3 scripts/state.py gate <slug> research_map approved         # after the user approves
```

## Phase 2: Book Design (→ cc-config.yml)

Write the Phase-2 decisions to `~/Books/<slug>/cc-config.yml`:

```yaml
title: "<final title>"
subtitle: "<subtitle if applicable>"
author: "<author name>"
description: |
  <2-3 paragraphs explaining concept, scope, and approach>
writing_style: "informative and engaging" # the *tone* axis
book_type: "nonfiction_practical" # the *structural* axis (Phase 1); required
trim_size: "6x9"
declined: [] # fields the user explicitly declined: description / book_blurb / author_bio
source_materials: |
  <Phase 1 summarized content>   # steers your local writing; not sent to the server
```

`title`, `author`, and `book_type` are required. `description`, `book_blurb`
(`blurb.md`), and `author_bio` (`author.md`) must each be supplied or listed
under `declined:` — only after the user has explicitly declined it (the
narrative's Phase 4c). Everything but `source_materials` is forwarded to the
platform. See `references/cc_config_schema.md` for the full field catalog.

**Author photo** (optional, JPEG or PNG, max 5 MB): if the user has one, copy it
to `~/Books/<slug>/author.{jpg|png}`. `publish_book.py` validates magic bytes and
size; the platform re-validates and renders it onto the back cover.

## Phase 3: TOC Architecture (→ outline.yml)

Write the outline the narrative's Phase 3 builds to `~/Books/<slug>/outline.yml`
(`references/outline_schema.md`): a `book:` block carrying `target_length` (the
Phase-2 book target), then one entry per chapter with `number`,
`chapter_title`, `chapter_description`, and its `sections` beats (`heading`,
`summary`, `key_points`, `word_target`). Render the two outline prompts with
`render_prompt.py` (Phase 4, "Loading prompts").

Only `number`, `chapter_title`, `chapter_description`, and each chapter's summed
`word_target` reach the platform — `publish_book.py` sums the beats for you; the
beats themselves stay local.

Record the two gates, and write the bible to `~/Books/<slug>/book-bible.md`:

```bash
python3 scripts/state.py gate <slug> outline approved      # gate 2
python3 scripts/state.py gate <slug> book_bible approved   # gate 3
```

---

## Phase 4: Content (chapters → introduction → summary → back cover)

### Loading prompts

Make sure the cache is populated (`publish_book.py fetch-prompts`), then render
a template instead of interpreting `{% for %}` blocks by hand:

```bash
python3 scripts/render_prompt.py \
  --template chapter-generation/system.jinja2 \
  --context /tmp/ctx.json
```

`render_prompt.py` reads `~/.proofbound/cache/prompts/` by default (override with
`--prompts-dir`).

### Guidance docs

The cache also holds the Markdown guidance docs the narrative cites by section
name. Read them **directly by name** — they are plain Markdown, never run them
through `render_prompt.py`:

| Narrative section            | Cached file                        |
| ---------------------------- | ---------------------------------- |
| "Book-type style guides"     | `book-type-style-guides.md`        |
| "Multi-agent playbook"       | `guidance-multi-agent.md`          |
| "Revision rubric"            | `guidance-revision-rubric.md`      |
| "Craft guide"                | `guidance-craft.md`                |
| "Word-count discipline"      | `guidance-word-count.md`           |
| "Book bible"                 | `guidance-book-bible.md`           |
| "Research & grounding"       | `guidance-research-grounding.md`   |
| "Intake & outline quality"   | `guidance-intake-outline.md`       |
| Bring-your-own project rules | `guidance-byo-project-contract.md` |

These steer **Session Mode** only. Script Mode (`generate_chapters.py`) renders
`system.jinja2` + `section.jinja2` with a fixed variable set and never loads them.

### Phase 4a — Chapters (Session Mode)

Run the narrative's per-chapter loop (from `guidance-multi-agent.md`). This skill
adds only the state marks between its passes:

1. **Material pass**, then `python3 scripts/state.py mark <slug> chapter <N> pitched`
2. **Draft**, re-reading `book-bible.md` first. Then `mark <slug> chapter <N> drafted`.
3. **Editor pass**. Then `mark <slug> chapter <N> edited`.
4. **Revise**, then update `book-bible.md`.

One exception to the narrative's autonomy: a **resumed** session finishes only
the chapter that was in flight, then stops for a go-ahead (Phase 0, step 6).

**Per-chapter durability invariant** — once the revised chapter is ready, and
_before_ starting the next, complete these **in this exact order**. State must
never claim completion before the artifact exists:

1. `Write` the chapter to `~/Books/<slug>/chapters/chapter_<N>.qmd`, then read it
   back to verify it landed.
2. Update `book-bible.md`.
3. Append a **150–250-word entry** for the chapter to `chapter-summaries.md`
   (`## Chapter <N> — <title>` heading, so `state.py rebuild` can find it) and
   verify it landed. This is what a future session reads instead of the chapter.
4. Mark the chapter `saved` in state — this writes `book-state.json` atomically
   and regenerates `PROGRESS.md` from it:
   ```bash
   python3 scripts/state.py mark <slug> chapter <N> saved --summary-written \
     --target-words <summed section-beat word_target>
   ```
   `--chars`/`--words` are derived from the chapter file automatically; pass them
   only to override. `--target-words` records the chapter's scope so
   `PROGRESS.md` shows delivered-vs-scope.
5. Report the saved chapter to the user: title, word count, one-line summary.

Only then proceed to Chapter N+1, so a dropped session loses at most one
in-flight chapter. Chapter files carry no leading title heading on this path
(it publishes through compose); a `.qmd` later uploaded as a bring-your-own
project must carry its own `title:` front matter instead.

#### Script Mode (advanced)

Only if the user requests batch/offline generation. Requires `ANTHROPIC_API_KEY`
and a populated prompt cache:

```bash
uv sync --extra script-mode
uv run python scripts/generate_chapters.py \
  --project-dir ~/Books/<slug>
```

`generate_chapters.py` reads prompts from `~/.proofbound/cache/prompts/`
(override with `--prompts-dir`) and writes `chapters/chapter_<N>.qmd`. Its
default model is the platform's, cached by `fetch-prompts`; pass `--model` only
to override it.

### Phase 4b — Introduction and summary

Render each prompt with the chapter summaries, so it works from what the chapters
actually say:

```bash
python3 scripts/render_prompt.py \
  --template introduction-prompt.jinja2 \
  --context-json '{"title": "...", "author": "...", "description": "...", "writing_style": "informative and engaging", "toc_chapters": [...]}' \
  --chapter-summaries ~/Books/<slug>/chapter-summaries.md
```

Derive `toc_chapters` from `outline.yml`; render `summary-prompt.jinja2` the
same way. Write `introduction.md` and `summary.md` as plain prose, then:

```bash
python3 scripts/state.py mark <slug> intro complete --chars <len>
python3 scripts/state.py mark <slug> summary complete --chars <len>
```

### Phase 4c — Bibliography and back cover

- Bibliography (only if the book cites real sources) → `references.bib`.
- Back-cover blurb → `blurb.md`; author bio → `author.md`. Plain UTF-8, no
  frontmatter; show the user the character count. A field the user explicitly
  declined goes under `declined:` in `cc-config.yml` instead.

---

## Phase 5: Publish

Run the narrative's pre-publish checklist first. Then:

1. **Confirm the project dir is complete**: `cc-config.yml`, `outline.yml`, one
   `chapters/chapter_<N>.qmd` per outline chapter, `introduction.md`,
   `summary.md`, and `blurb.md` / `author.md` unless declined (plus optional
   `author.{jpg,png}` / `references.bib`).

2. **Publish** — always pass `--authoring-model` (the model you are running as)
   so the finished book records what wrote it:

   ```bash
   uv run scripts/publish_book.py publish --project-dir ~/Books/<slug> \
     --authoring-model claude-opus-5 \
     --workflow-version book-builder-skill/4.8.2
   ```

   This reads the local content, `POST`s it to `/books/compose`, then polls
   `GET /books/{id}` until the server-side render completes. It prints the
   `book_id` and the `files.proofbound.com` PDF URL, plus any `warnings` — relay
   those as the narrative's Phase 5 says. The book now appears in the user's
   ProofBound dashboard.

   A slow render can take over 30 minutes, longer than a foreground Bash call
   allows (10 minutes at most). When Claude runs this, run it in the
   **background** and read its output when it finishes, or pass `--no-wait` and
   poll `publish_book.py status --book-id <id>`.

   Useful flags:

   - `--trim-size 6x9|a5|digest` — override `cc-config.yml`.
   - `--no-wait` — submit and exit; poll later with
     `publish_book.py status --book-id <id>`.
   - `--download-src ~/Books/<slug>-source.zip` — after completion, download the
     server-assembled Quarto project (the editable escape hatch).
   - `--introduction PATH` / `--summary PATH` — override the default file names.
   - `--poll-timeout N` — seconds to wait for the render before giving up (default 2400, above the server's 2040s render cap, which includes the corrective gutter re-render). Queue wait is unbounded, so a timeout does not mean the render failed; check with `status --book-id`.

3. **Record the book id** in state for later iteration:

   ```bash
   python3 scripts/state.py set <slug> book_id <book_id>
   ```

4. **Report** to the user: the dashboard entry, the PDF URL, and the `book_id`.
   Offer the source download and the cover/print hand-off (below).

> **One render at a time per account.** If a previous render is still in flight,
> `/books/compose` returns 429 and `publish_book.py` reports it — wait and retry.

> **Free-tier renders are metered and stamped.** Renders from this skill and the
> MCP connector share one free allowance (**50 renders per rolling 30 days**);
> exhausting it returns **402** (see Error Handling). A free-tier account's
> `book.pdf` also carries a ProofBound banner on **page 1 only** — the clean
> master is kept server-side and swapped in automatically when the account
> upgrades, with no re-render, and books you already rendered unlock too. Say
> so when you hand the user their PDF rather than letting the banner surprise
> them: upgrade at https://proofbound.com/upgrade?reason=clean_pdf.

### Downloading the editable source

```bash
uv run scripts/publish_book.py download-src \
  --book-id <book_id> --output ~/Books/<slug>-source.zip
```

Returns the canonical Quarto project the platform assembled (templates, branding,
colophon, the user's content). Useful for local inspection or hand-editing before
a future re-publish.

### Hand-off to cover generation / print

Every published book gets a **placeholder cover automatically** (title/subtitle/
author on a solid background), generated by the platform during the render — so
the dashboard entry looks finished immediately and no extra step is required.

To replace it with a designed cover, pass the `book_id` to the cover-generator
skill (`generate_cover.py upload --book-id <book_id>`) or use the ProofBound
dashboard's customization flow; either overwrites the placeholder. Then on to Lulu
for print-on-demand.

---

## Phase 6: Iteration

**Goal**: Revise a chapter, the intro, or the summary of an already-published
book.

There is **no per-chapter update endpoint** — iteration is deliberately simple:
rewrite locally, then re-publish the whole book.

### Process

1. **Identify the book**:
   ```bash
   python3 scripts/state.py list
   python3 scripts/state.py show <slug>
   ```
   The local project dir is the source of truth for re-publishing. If it was
   cleaned up, recover it with `publish_book.py download-src` and unzip.
2. **Edit locally**: rewrite the target `chapters/chapter_<N>.qmd`,
   `introduction.md`, or `summary.md`. Use Claude Max in conversation; preserve
   structure (consult `outline.yml`) and voice (consult `cc-config.yml`).
3. **Update state** (bumps `revision_count` for a durable audit trail):
   ```bash
   python3 scripts/state.py mark <slug> chapter <N> saved --bump-revision
   # or: ... intro ... / ... summary ...
   ```
4. **Re-publish**:
   ```bash
   uv run scripts/publish_book.py publish --project-dir ~/Books/<slug>
   ```
   This creates a fresh book entry with the revised content. (Re-publishing
   produces a new `book_id`; update state if you want to track the latest.)

---

## State Management

**The project directory owns the state.** `scripts/state.py` manages three
files, with exactly one source of truth:

- **Canonical**: `<project_dir>/book-state.json` (schema v2) — the only record
  that is ever authoritative.
- **Index**: `~/.proofbound/in-progress/<slug>.json` — a **pointer**
  (`{schema_version, slug, project_dir, updated_at}`), not a copy, so `list` can
  find books without scanning the filesystem.
- **Projection**: `<project_dir>/PROGRESS.md` — generated from canonical state
  and **never a source of truth**. Do not hand-edit it.

The same `book-state.json` schema is what the MCP connector's guidance tells
file-capable hosts to maintain by hand, so a book started either way resumes
either way.

**Write guarantees** (honest — nothing is atomic across two files): the
canonical JSON is persisted atomically first (temp file + rename), then
`PROGRESS.md` is regenerated from it. Every mutating command regenerates the
projection; if that fails, canonical state is still valid and the command says
so. `render-progress` is the repair command, not a required second step.
`show` regenerates `PROGRESS.md` whenever it is missing, stale (its header
echoes a `revision` that no longer matches), or malformed.

**Migration from schema v1** happens on load and needs no action from you: v1
put the canonical record in the home directory and a `.proofbound-state.json`
mirror in the project. On load, a v2 `book-state.json` wins unconditionally;
otherwise the freshest legacy record is migrated into the project dir, the home
record is rewritten as a pointer, and the legacy mirror is deleted. Records are
never merged. A v1 record whose project dir is gone is **flagged** by `list` as
an orphan and left alone — ask the user before touching it.

**Corrupt state** is rebuilt from the project's own artifacts
(`state.py rebuild`) — `outline.yml` for the chapter roster, `chapters/` for
stages and counts, `chapter-summaries.md` for which chapters are summarized.
There is deliberately no backup copy; a second stateful file would only drift.

### CLI

```bash
python3 scripts/state.py list
python3 scripts/state.py show <slug> [--no-repair]
python3 scripts/state.py init <slug> [--project-dir D --title T --author A --target-words N --chapter-count N]
python3 scripts/state.py set <slug> <dotted.path> <value>
python3 scripts/state.py gate <slug> (research_map | outline | book_bible) <status>
python3 scripts/state.py mark <slug> chapter <N> <stage> [--chars N] [--words N] [--target-words N] [--summary-written] [--bump-revision]
python3 scripts/state.py mark <slug> (intro | summary) <status> [--chars N]
python3 scripts/state.py render-progress <slug>
python3 scripts/state.py rebuild <slug> [--project-dir D --title T --author A]
python3 scripts/state.py delete <slug> [--purge]
```

Valid chapter **stages**: `pending`, `pitched`, `drafted`, `edited`, `saved`,
`truncated`, `failed` — only `saved` claims a durable file on disk.
Valid intro/summary **statuses**: `pending`, `in_progress`, `complete`,
`truncated`, `failed`. Valid **gate** statuses: `not_applicable`, `pending`,
`approved`.

---

## Error Handling

### Not logged in

```
Not logged in. Run: uv run scripts/publish_book.py login
```

### Prompt cache empty

`render_prompt.py` / `generate_chapters.py` will report the cache is missing.
Fix: `uv run scripts/publish_book.py fetch-prompts`.

### Book info incomplete (422)

`publish_book.py` prints the missing fields and the server's hint. Ask the user
for each one, or whether they decline it — then write the file
(`blurb.md`, `author.md`, `description:` in `cc-config.yml`) or add the field to
`declined:`, and re-publish. A missing `book_type` cannot be declined.

### Render in progress (429)

```
a render is already in progress for this account; try again later
```

One render per account at a time. Wait, then re-run `publish`.

### Render allowance exhausted (402)

```
You've used your 50 free renders in the last 30 days. Your local content is
unchanged. Renders free up one at a time as each ages out. Upgrade at
https://proofbound.com/upgrade?reason=render_quota
```

The account has used its free render allowance (50 renders per rolling 30 days,
shared with the MCP connector). Relay it as written — the local content is
untouched, so nothing needs regenerating. Do not retry in a loop.

### Render failed

`publish_book.py` reports the server's error and exits non-zero. Common causes:
a malformed chapter, an invalid `trim_size`, or oversized author photo. Fix the
local content and re-publish.

### Chapter generation error (Session Mode)

Any chapter already marked `saved` in state is durably saved on disk — only
the in-flight chapter is at risk. Retry it, or mark it `failed` and regenerate
later via Phase 6.

### Corrupt or missing state

`state.py` reports the parse error and points at the recovery path. Rebuild
canonical state from the artifacts rather than hand-editing JSON:

```bash
python3 scripts/state.py rebuild <slug>
```

If only `PROGRESS.md` is wrong or missing, `python3 scripts/state.py render-progress <slug>`
regenerates it — it is a projection, so it is always safe to discard.

---

## Reference Materials

Detailed schemas live in `references/`:

- **cc_config_schema.md** — `cc-config.yml` fields
- **outline_schema.md** — `outline.yml` structure
- **prompt_templates.md** — catalog of the Jinja2 prompt templates (fetched from
  the platform into `~/.proofbound/cache/prompts/`)
- **cover_and_print_specs.md** — KDP/Lulu print specs (for cover hand-off)

---

## Final Notes

- **Thin client**: the only network dependency is `api.proofbound.com`. No local
  Quarto/TinyTeX, no vendored templates.
- **Login required**: publishing and prompt-fetching both need a ProofBound
  account.
- **Default to Session Mode**: Claude writes the prose; Script Mode is for batch
  generation only.
- **Tone**: encouraging, collaborative, patient. Let the user control the pace;
  explain what you're doing and why; allow iteration at every phase.

**Remember**: the goal is to guide the user through a rewarding creative process
that ends with a polished, professional book in their ProofBound library.
