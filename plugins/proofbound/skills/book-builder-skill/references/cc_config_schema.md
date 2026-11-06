# cc-config.yml Schema Reference

The `cc-config.yml` file defines the book's metadata and configuration variables used throughout the Quarto project. This file is processed with Jinja2 templating to generate personalized book content.

## Required Fields

These fields MUST be present for the project to build correctly:

| Field    | Type   | Description                   | Example                                   |
| -------- | ------ | ----------------------------- | ----------------------------------------- |
| `title`  | string | Book title                    | `"The Ultimate Guide to Chengdu Cuisine"` |
| `author` | string | Author name                   | `"Richard Sprague"`                       |
| `date`   | string | Publication date (YYYY-MM-DD) | `"2026-02-10"`                            |
| `year`   | string | Publication year              | `"2026"`                                  |

## Optional Metadata Fields

| Field         | Type    | Default  | Description                                     |
| ------------- | ------- | -------- | ----------------------------------------------- |
| `subtitle`    | string  | _(none)_ | Book subtitle                                   |
| `description` | string  | _(none)_ | Book concept/description used for AI generation |
| `page_count`  | integer | `0`      | Updated after PDF build                         |

## Writing & Structure Fields

Two **orthogonal** axes steer how the book reads. Both are settled during intake
(Phases 1–2) and consulted while writing.

| Field           | Type   | Default         | Forwarded to compose? | Description                                                                                               |
| --------------- | ------ | --------------- | --------------------- | --------------------------------------------------------------------------------------------------------- |
| `writing_style` | string | `practical`     | Yes                   | The _tone_ axis (e.g. `"informative and engaging"`).                                                      |
| `book_type`     | string | none (required) | Yes                   | The _structural_ axis — how the book is shaped. Selects the structural style guide; forwarded to compose. |

`book_type` is one of `nonfiction_practical`, `nonfiction_narrative`,
`nonfiction_reference`, `memoir`, `fiction`, or `childrens`. It is recorded in
`cc-config.yml` so a resumed session keeps the same structural guide
(`book-type-style-guides.md`, fetched into the prompt cache), and `publish_book.py`
forwards it to `/books/compose` (the server validates the key and records it on
the book; a book without one is rejected with 422 `book_info_incomplete`). It shapes the prose you write,
not the typeset output.

## Publishing Information Fields

These fields appear on the copyright page and in book metadata:

| Field                | Type   | Default                       | Description         |
| -------------------- | ------ | ----------------------------- | ------------------- |
| `publisher`          | string | `"Proofbound"`                | Publisher name      |
| `publisher_location` | string | `"Mercer Island, Washington"` | Publisher location  |
| `edition`            | string | `"First Edition"`             | Edition identifier  |
| `printing`           | string | `"First printing"`            | Printing identifier |
| `isbn`               | string | `"[To be assigned]"`          | ISBN number         |
| `cover_designer`     | string | `"Proofbound Press"`          | Cover design credit |

## Website Settings

| Field         | Type   | Description                                 |
| ------------- | ------ | ------------------------------------------- |
| `page_footer` | string | Copyright notice for web/PDF footer         |
| `site_url`    | string | URL for web publishing (GitHub Pages, etc.) |

## AI-Populated Fields

These fields are populated during the book generation workflow:

| Field                   | Type               | Description                                              |
| ----------------------- | ------------------ | -------------------------------------------------------- |
| `source_materials`      | string (multiline) | Summarized content from uploaded source files            |
| `enhanced_introduction` | string (multiline) | AI-generated book preface/introduction (Quarto Markdown) |
| `enhanced_summary`      | string (multiline) | AI-generated book summary/conclusion (Quarto Markdown)   |

## Jinja2 Template Variables

When creating a new project, the following Jinja2 variables are available:

- `{{ project_name }}` - Project directory name (converted to Title Case)
- `{{ current_date }}` - Current date in YYYY-MM-DD format
- `{{ current_date[:4] }}` - Current year (first 4 chars of date)

### Jinja2 Filters Used

- `| title` - Convert to Title Case
- `| replace('-', ' ')` - Replace hyphens with spaces
- `| replace('_', ' ')` - Replace underscores with spaces

## Complete Example

```yaml
# CC Template Configuration
# This file defines variables used in template processing

# Basic book metadata
title: "The Ultimate Guide to Chengdu Cuisine"
subtitle: "A Journey Through Sichuan's Most Beloved Dishes"
author: "Richard Sprague"
writing_style: "informative and engaging" # tone axis (forwarded to compose)
book_type: "nonfiction_practical" # structural axis (required; selects the style guide)
declined: [] # fields the user explicitly declined: description, book_blurb, author_bio
date: "2026-02-10"
year: "2026"
page_count: 0 # Updated after PDF build

# Website and publishing settings
page_footer: "Copyright 2026 Richard Sprague"
site_url: "https://richardsprague.github.io/chengdu-cuisine/"

# Publishing information (for copyright page)
publisher: "Proofbound"
publisher_location: "Mercer Island, Washington"
edition: "First Edition"
printing: "First printing"
isbn: "[To be assigned]"
cover_designer: "Proofbound Press"

# Book description (used by AI for content generation)
description: |
  A comprehensive guide to Chengdu cuisine, exploring the history, ingredients, and techniques
  behind Sichuan's most iconic dishes. From hotpot to dan dan noodles, this book celebrates
  the bold flavors and rich culinary traditions of China's "City of Gastronomy."

# Source Materials (populated when source files are uploaded)
source_materials: |
  # Notes from Chengdu Trip 2024
  - Visited 15 restaurants across Chengdu
  - Interviewed 3 local chefs about traditional techniques
  - Collected recipes for 20+ classic dishes
  - Photographed ingredients at local markets

# AI Enhancement Variables (populated by generation workflow)
enhanced_introduction: |
  # Preface {.unnumbered}

  This is "The Ultimate Guide to Chengdu Cuisine" by Richard Sprague.

  Chengdu, the capital of Sichuan province, has earned UNESCO's designation as a "City of Gastronomy"
  through centuries of culinary innovation. This book explores the dishes that define Chengdu's
  food culture and the techniques that make Sichuan cuisine one of China's most celebrated regional styles.

  ::: {.callout-note}
  ## About This Book

  This book was created with Proofbound, which makes it easy for anyone to turn their ideas and notes
  into a professional, high-quality printed book. To learn more about Proofbound visit
  <https://proofbound.com>.
  :::

enhanced_summary: |
  # Summary {.unnumbered}

  This concludes "The Ultimate Guide to Chengdu Cuisine" by Richard Sprague.

  ## Key Takeaways

  Through this exploration of Chengdu's culinary landscape, we've discovered:

  - The historical evolution of Sichuan cuisine and its defining characteristics
  - Essential ingredients like Sichuan peppercorns, doubanjiang, and chili oil
  - Traditional cooking techniques that create complex layers of flavor
  - Classic dishes from street food to restaurant specialties

  ## Next Steps

  As you continue your culinary journey, consider visiting Chengdu to experience these dishes firsthand,
  or experiment with the recipes and techniques described in this book. The bold flavors and rich
  traditions of Chengdu cuisine offer endless opportunities for exploration.

  Thank you for reading The Ultimate Guide to Chengdu Cuisine. May it inspire you to appreciate and
  celebrate one of the world's greatest food cultures.
```

## Usage in Project

1. **During scaffolding**: The `cc-config.yml` is processed with basic variables (`project_name`, `current_date`)
2. **During template rendering**: All fields become available as Jinja2 variables in `.qmd` files
3. **In prompts**: Fields like `title`, `author`, `description` are passed to AI generation prompts
4. **In output**: Values populate the title page, copyright page, and footer

## Validation Rules

For the thin-client publish flow, `title`, `author`, and `book_type` are
required — `publish_book.py` reads them from `cc-config.yml` (falling back to
`outline.yml`'s `book.title`) and forwards `subtitle`, `description`,
`writing_style`, `book_type`, `trim_size`, and `declined` (as `declined_fields`)
when present. `description`, the blurb (`blurb.md`), and the author bio
(`author.md`) must each be supplied or listed under `declined:` — and only after
the user explicitly declined it; otherwise compose returns 422
`book_info_incomplete`. `source_materials`
is recorded locally to steer your writing but is **not** forwarded —
`/books/compose` rejects unknown fields. The platform performs the authoritative
validation when scaffolding (`POST /books/compose`); fields like
`date`/`year`/publisher are injected canonically server-side and need not be set
locally.
