# CLAUDE.md — Proofbound/skills

The one public repo for ProofBound skills (renamed from `Proofbound/claude-plugins`; the local checkout directory may still be `claude-plugins`). It holds the Claude Code marketplace (`.claude-plugin/marketplace.json`, added with `/plugin marketplace add https://proofbound.com/workshop/marketplace.json`) and the published skill bundles. No build, no tests here.

## Rules that the tree does not tell you

- **CI-managed paths.** For every skill in the private `proofbound-monorepo`'s `skills/channels.yml`, its `skill-publish.yml` writes `plugins/<claude_code_name>/` (`.claude-plugin/plugin.json` + `skills/<skill>/`), `dist/<skill>.zip`, and that skill's catalog entry. `plugins/proofbound/` is book-builder-skill. Never hand-edit these; edit the skill in the monorepo and bump its version. `channels.yml` there decides what ships and the entry name (`claude_code_name`).
- **One PR per skill version.** CI opens a single PR, `Publish <skill> <version>` from branch `publish/<skill>-v<version>`, carrying the bundle, the zip and (when it has to change) the catalog entry. Review and merge it; there is no second PR and no merge order.
- **Versions live in plugin.json only.** Installed users see an update only when the plugin's version changes. For the CI-managed entries that is `version` in `plugins/<name>/.claude-plugin/plugin.json`, set by CI on every publish; never add a `version` to those catalog entries.
- Only skills whose real work runs on the ProofBound server belong here. A skill that works locally (e.g. book-index) is given away once its source is public — keep it out.
- Every `source` is absolute (`git-subdir` with `url: https://github.com/Proofbound/skills.git` for our own plugins, even though they live in this repo; `github` for the others), never `./...`: proofbound.com/workshop/marketplace.json is this same file proxied by the worker-router, and a URL-added marketplace cannot resolve relative paths.
- Other entries point at their own public repos; this repo only lists them, and they are hand-maintained. `conference-booth-report` has no plugin.json (SKILL.md at its repo root), so its entry is `strict: false` with `skills: ["./"]` and the version lives here.
- `https://proofbound.com/skills/<skill>.zip` serves `dist/<skill>.zip` from `main` (worker-router); `dist/` is its allowlist.
- Validate before merging a hand edit: `claude plugin validate .`
