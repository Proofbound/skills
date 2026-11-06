# Prompt Templates Catalog

This document catalogs all Jinja2 prompt templates used in the ProofBound book generation workflow.

**Source of truth**: the platform's own templates, served via `GET /books/prompts`.

**Fetched at runtime into this skill**: the skill no longer vendors these templates. `publish_book.py fetch-prompts` downloads them from `GET /books/prompts` into `~/.proofbound/cache/prompts/`; `render_prompt.py` and `generate_chapters.py` read from that cache by default. This eliminates template drift — there is no sync step.

## Template Categories

1. [Outline Generation](#outline-generation)
2. [Chapter Generation](#chapter-generation)
3. [Introduction & Summary](#introduction--summary)
4. [Cover Generation](#cover-generation)
5. [Bibliography](#bibliography)

---

## Outline Generation

### `outline-prompt.jinja2`

**Purpose**: Generate a structured book outline from a topic description

**Variables**:

- `topic` (string) - Book topic/description
- `writing_style` (string) - Target writing style (e.g., "informative and engaging")
- `writing_style_description` (string, optional) - What the style means (the server
  resolves it for the curated keys); rendered after the style when set
- `chapter_count` (integer) - Number of chapters to generate
- `words_per_chapter` (integer) - Target words per chapter
- `total_target_words` (integer) - Total book word count
- `source_materials` (string, optional) - Uploaded source content
- `audience` (string, optional) - The reader persona settled at intake; defaults to
  "General readers interested in {topic}"
- `estimated_pages` (integer) - Total page estimate

**Output Format**: the chapter list (number, title, description). The template names
no wire format: the server enforces the shape with forced tool use, and the skill
writes `outline.yml`.

**Key Features** (this is **pass 1 of 2** — the chapter list only):

- Generates the chapter list: number + title + 3-5 sentence description
- Does NOT include subtopics or sections — that's pass 2 (`outline-sections.jinja2`)
- Ensures no overlapping content between chapters
- Supports cross-chapter references by number

**Usage**: Phase 3 of book creation workflow (pass 1)

### `outline-sections.jinja2`

**Purpose**: Expand one chapter into a structured spine of 3-8 **section beats**
(pass 2 of 2 of outline generation)

**Variables**:

- `title` (string) - Book title
- `topic` (string) - Book description
- `writing_style` (string) - Target writing style
- `writing_style_description` (string, optional) - What the style means (the server
  resolves it for the curated keys); rendered after the style when set
- `chapter_title` (string) - The chapter being expanded
- `chapter_description` (string) - That chapter's description
- `sibling_titles` (array of strings, optional) - Other chapter titles, so beats
  don't overlap across chapters
- `words_per_chapter` (integer) - Chapter word budget (beats' `word_target`s sum to ~this)

**Output Format**: section beats `{heading, summary, key_points, word_target}`; as
with pass 1, the shape is enforced by the caller, not stated in the template.

**Usage**: Phase 3 of book creation workflow (pass 2), once the chapter list is approved

---

## Chapter Generation

### `chapter-generation/system.jinja2`

**Purpose**: System-level prompt enforcing formatting rules and prose style

**Variables**:

- `writing_style` (string) - Target writing style
- `writing_style_description` (string, optional) - What the style means (the server
  resolves it for the curated keys); rendered after the style when set
- `title` (string) - Book title
- `author` (string) - Author name
- `topic` (string) - Book description
- `chapter_context` (string, optional) - Extracted source material context
- `chapter_title` (string) - Current chapter title
- `chapter_description` (string) - Chapter description
- `sections` (array of beats) - The chapter's section spine; each beat has a
  `heading` and optional `summary` (rendered as the chapter's beat list)
- `target_words` (integer) - Target word count for chapter
- `craft_rules` (string, optional) - Line-level craft bullets (the same four as the
  served `guidance-craft.md`); rendered as a **Craft.** block when set
- `table_rules` (string, optional) - Table authoring rules (the served
  `guidance-craft.md` Tables section); rendered as a **Tables.** block when set

**Key Rules Enforced**:

- **NEVER use H1 (#) headings** - chapter title handled separately
- Open with prose before any heading
- One length signal: "about N words; shorter is fine if every point is developed"
- Use H2 (##) sparingly - max 3-5 major sections
- Write complete content, never placeholders
- Prefer flowing paragraphs over bullet lists
- Optional **Craft.** block (`craft_rules`) after the paragraph rules

**Role**: "system" role in Anthropic API calls

### `chapter-generation/section.jinja2`

**Purpose**: User-level prompt for generating section content within a chapter

**Variables**:

- `section_number` (integer) - Current section (1, 2, 3...)
- `total_sections` (integer) - Total sections in chapter
- `section_heading` (string) - This section beat's heading (the section to write)
- `section_summary` (string, optional) - 1-2 sentences on what the section covers
- `section_key_points` (array of strings, optional) - Must-hit bullets for this section
- `section_word_target` (integer) - Target words for this section
- `previous_sections` (array, optional) - Context from prior sections
  (each item: `{topic, summary}`)

**Conditional Instructions**:

- **If section 1**: open in prose with one or two paragraphs that show where the
  chapter is going, built on a concrete case or point
- **If final section**: connect the last point to the chapter as a whole and point
  ahead; no recap

Book and chapter context come from `system.jinja2`, which accompanies every section call.

**Key Features**:

- Provides context from previous sections to avoid repetition
- Use only facts from the supplied material or common knowledge; never invent a
  statistic, quote, name, or study
- States each section's scope as "about N words; shorter is fine if every point is developed"
- Reminds to avoid H1 headings

**Role**: "user" role in Anthropic API calls

### `extract-chapter-context.jinja2`

**Purpose**: Extract relevant source material context for a specific chapter

**Variables**:

- `title` (string) - Book title
- `author` (string) - Author name
- `topic` (string) - Book description
- `chapter_title` (string) - Current chapter title
- `chapter_description` (string) - Chapter description
- `sections` (array of beats) - The chapter's section spine (headings drive the extraction)
- `source_materials` (string) - Full uploaded source content

**Output**: Filtered, chapter-relevant excerpts from source materials

**Usage**: Stage 1 of 3-stage chapter generation (when source materials present)

---

## Introduction & Summary

### `introduction-prompt.jinja2`

**Purpose**: Generate book preface/introduction

**Variables**:

- `title` (string) - Book title
- `author` (string) - Author name
- `description` (string) - Book description
- `writing_style` (string) - Target writing style
- `writing_style_description` (string, optional) - What the style means (the server
  resolves it for the curated keys); rendered after the style when set
- `toc_chapters` (array) - Chapter list for reference

**Output**: Quarto Markdown for preface section

**Key Features**:

- Uses `{.unnumbered}` for preface heading
- Includes Proofbound callout box
- References chapter structure

**Target Location**: Populates `enhanced_introduction` in cc-config.yml

### `summary-prompt.jinja2`

**Purpose**: Generate book conclusion/summary

**Variables**:

- `title` (string) - Book title
- `author` (string) - Author name
- `topic` (string) - Book description
- `writing_style` (string) - Target writing style
- `writing_style_description` (string, optional) - What the style means (the server
  resolves it for the curated keys); rendered after the style when set
- `outline` (string or object) - Full book outline

**Output**: Quarto Markdown for summary section

**Structure**:

- Summary heading (unnumbered)
- Key Takeaways section
- Next Steps section
- Closing message

**Target Location**: Populates `enhanced_summary` in cc-config.yml

---

## Cover Generation

### `cover-front.jinja2`

**Purpose**: Generate image prompt for front cover artwork

**Variables**:

- `title` (string) - Book title
- `author` (string) - Author name
- `book_idea` (string) - Book description/concept

**Output**: Text prompt for AI image generation (Ideogram, DALL-E, etc.)

**Usage**: Cover generation workflow in cc-template-api

### `cover-back-illustration.jinja2`

**Purpose**: Generate image prompt for back cover illustration

**Variables**: (TBD - check implementation)

**Output**: Text prompt for AI image generation

**Usage**: Optional back cover artwork

### `cover-back-text.jinja2`

**Purpose**: Generate back cover marketing text/blurb

**Variables**: (TBD - check implementation)

**Output**: Marketing copy for back cover

**Usage**: Print cover generation

---

## Bibliography

### `bibliography-convert.jinja2`

**Purpose**: Convert bibliography to different format

**Variables**: (TBD - check implementation)

**Output**: Formatted bibliography entries

**Usage**: BibTeX conversion and formatting

---

## Jinja2 Syntax Reference

### Conditionals

```jinja2
{% if subtitle %}
subtitle: "{{ subtitle }}"
{% endif %}
```

### Loops

```jinja2
{% for chapter in toc_chapters %}
- Chapter {{ chapter.number }}: {{ chapter.chapter_title }}
{% endfor %}
```

### Filters

| Filter                   | Purpose                   | Example                                                    |
| ------------------------ | ------------------------- | ---------------------------------------------------------- |
| `\| join(", ")`          | Join array with separator | `{{ sections \| map(attribute='heading') \| join(", ") }}` |
| `\| replace('"', '\\"')` | Replace chars             | `{{ title \| replace('"', '\\"') }}`                       |
| `\| title`               | Title Case                | `{{ project_name \| title }}`                              |

### Comments

```jinja2
{# This is a comment - not included in output #}
```

### Whitespace Control

```jinja2
{% if source_materials -%}
  {{ source_materials }}
{%- endif %}
```

The `-` strips whitespace before/after the tag.

---

## Template Workflow

### Typical Book Generation Flow:

1. **Outline**: `outline-prompt.jinja2` (chapter list) → `outline-sections.jinja2`
   (section beats per chapter) → `outline.yml`
2. **Introduction**: `introduction-prompt.jinja2` → `enhanced_introduction` in cc-config.yml
3. **Chapters**:
   - (Optional) `extract-chapter-context.jinja2` → filtered source context
   - `chapter-generation/system.jinja2` → system prompt
   - `chapter-generation/section.jinja2` → user prompt
   - Repeat for each section → `chapters/chapter_N.qmd`
4. **Summary**: `summary-prompt.jinja2` → `enhanced_summary` in cc-config.yml
5. **Cover**: `cover-front.jinja2` → image generation prompt

### Render Example (Python)

```python
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

# Setup Jinja2 environment (prompts fetched via publish_book.py fetch-prompts)
prompts_dir = Path.home() / ".proofbound" / "cache" / "prompts"
env = Environment(loader=FileSystemLoader(prompts_dir / "chapter-generation"))

# Load template
system_template = env.get_template("system.jinja2")

# Render with variables
system_prompt = system_template.render(
    writing_style="informative and engaging",
    title="The Ultimate Guide to Chengdu Cuisine",
    author="Richard Sprague",
    topic="Comprehensive guide to Chengdu cuisine",
    chapter_context="",
    chapter_title="The Culinary Heritage of Chengdu",
    chapter_description="Historical evolution of Chengdu cuisine...",
    sections=[
        {"heading": "Geography and the roots of a cuisine", "summary": "..."},
        {"heading": "Religion, court, and folk traditions", "summary": "..."},
    ],
    target_words=5000
)
```

---

See also: [cc_config_schema.md](./cc_config_schema.md), [outline_schema.md](./outline_schema.md).
