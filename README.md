# ProofBound skills

```
/plugin marketplace add https://proofbound.com/workshop/marketplace.json
/plugin install proofbound@proofbound
```

The same catalog is this GitHub repo, so `/plugin marketplace add Proofbound/skills` also works.

| Plugin | What it does |
|---|---|
| `proofbound` | Write a book in conversation with Claude, then publish it to your ProofBound library. ProofBound renders the PDF and stores the book. Needs a [ProofBound](https://proofbound.com) account; sign-in is a browser device flow. |
| `book-research` | Build a book's source dossier: scholarly search, BibTeX, filed materials. ([source](https://github.com/Proofbound/book-research-skill)) |
| `conference-booth-report` | Turn trade-show booth photos into an emailable HTML report. ([source](https://github.com/Proofbound/conference-booth-report)) |
| `photo-essay` | Turn a folder of photos into an HTML photo essay. ([source](https://github.com/Proofbound/photo-essay-skill)) |

Install any of them with `/plugin install <name>@proofbound`. Update with `/plugin marketplace update proofbound`.

The published skill bundles are also downloadable as zips (for claude.ai and other agents) from [proofbound.com/workshop/skills](https://proofbound.com/workshop/skills). ProofBound's own skills here (`plugins/`, `dist/`) are published automatically from ProofBound's source; please don't send PRs against those paths.
