# Distribution

[简体中文](README.md) · **English** · [Collection](../README.en.md)

Maintain skill sources only in `skills/`. Claude Code and Codex install the collection from repository marketplaces. Tencent SkillHub uses three standalone packages; SkillsMP and skills.sh reference public GitHub content.

## Version and build

Set the version in `manifest.json`, root `plugin.json`, and `.claude-plugin/plugin.json`. Keep skill names stable. Tencent slugs default to the same skill names; check availability before the first publication.

Run from the repository root with Python 3.10+ and Git:

```bash
python distribution/build.py check
python -m unittest discover -s distribution/tests -v
python distribution/build.py build --out dist/1.4.0
```

Stage new runtime files before building. Inputs use tracked paths from the Git index and current working-tree bytes. Stage moves before release validation; a missing tracked runtime file fails validation to prevent silent omissions. Skill packages accept root `SKILL.md`, READMEs, LICENSE, requirements, and the `scripts/`, `references/` and `assets/` trees. Tests, examples, development docs and ROADMAP are excluded. Nested tests, caches, `_selftest.py`, credentials and installed dependencies cause runtime validation to fail. Existing output directories are never overwritten; use a new output path for another build.

Static validation checks standard Agent Skills frontmatter, matching public versions, local Markdown links and inline `references/*.md` paths in `SKILL.md` and runtime references. Targets must exist inside the same standalone package. Online URLs, fenced examples and Obsidian wikilinks are not treated as local file links.

The manifest's `packageBudget` sets a project budget of 200 runtime files and 5 MiB uncompressed for the collection. The Tencent web uploader enforces 200 files and 10 MB per package; the project keeps the same file ceiling with a smaller size margin. `check` prints collection and per-skill counts and raw bytes; `build` prints file counts, uncompressed bytes and ZIP bytes for each archive. No quality report is generated.

## Repository development layout

Runtime sources live under `skills/<skill-name>/`. Development assets live at the repository root:

- `tests/sanmu-doc2md/`, `tests/sanmu-paper-reader/`, `tests/sanmu-paper-search/`: offline tests; the doc2md entry point is `tests/sanmu-doc2md/_selftest.py`.
- `examples/sanmu-doc2md/`: preserved examples, source PDF, images, `RESULTS.md` and historical `RESEARCH-VALIDATION.md`.
- `docs/development/sanmu-doc2md/ROADMAP.md`: future development plans.
- `docs/showcases/sanmu-paper-reader/images/`: reader screenshots.
- `distribution/tests/`: package builder regression tests.

```bash
python tests/sanmu-doc2md/_selftest.py
python -m unittest discover -s tests/sanmu-paper-reader -v
python -m unittest discover -s tests/sanmu-paper-search -v
```

All paths are relative to the repository root. Examples and showcase images support repository browsing and development; installed runtime references must point to packaged resources.

Outputs:

- `sanmu-skills-1.4.0.zip`: portable plugin with all three skills, root `plugin.json`, Claude metadata and licenses. It can be submitted to the OpenAI public plugin directory, subject to platform checks and review.
- `skillhub/<skill-name>/`: directories for Tencent CLI publication.
- `skillhub/<skill-name>-1.4.0.zip`: archives matching those directories, with root `SKILL.md`. Check the live web uploader for its accepted formats.
- `SHA256SUMS`: SHA-256 checksums of the four ZIP files.

The builder adds Tencent's `slug`, `displayName`, `version`, `summary` and `license` to generated skill frontmatter. It preserves skill bodies and resources, without adding Tencent-specific top-level fields to the portable sources.

## Claude Code and Codex

```bash
claude plugin validate .claude-plugin/marketplace.json --strict
claude plugin validate .claude-plugin/plugin.json --strict
```

Codex discovers `.agents/plugins/marketplace.json`; its local source `./` points to the root plugin. Test with a separate configuration, then run `codex plugin add sanmu-skills@sanmu-skills`. User installation commands are in the collection README.

A self-hosted GitHub marketplace is separate from Anthropic or OpenAI's official directories. The OpenAI public directory requires a [developer-platform submission](https://developers.openai.com/plugins/deploy/submission): upload the ZIP, resolve automated findings and submit for review.

## Tencent SkillHub

All three skills are published:

| Skill | slug |
| --- | --- |
| sanmu-doc2md | `sanmu-doc2md` |
| sanmu-paper-reader | `sanmu-paper-reader` |
| sanmu-paper-search | `sanmu-paper-search` |

Upload the built artifact `skillhub/<skill-name>-<version>.zip`, not the `skills/<skill-name>/` source directory: its `node_modules/` and `__pycache__/` push doc2md past the 200-file limit and make the other skills skip `.pyc` files. The platform allows 200 files and 10 MB per package; the current packages contain 31, 23 and 14 files. Update later versions from the console's version history without changing the slug.

To publish with an API token, complete identity verification and configure it using the [official guide](https://skillhub.cn/tutorials). The Windows publication guide uses WSL. Keep tokens out of source files, examples and commits.

```bash
skillhub auth whoami
skillhub publish ./dist/1.4.0/skillhub/sanmu-doc2md --dry-run
skillhub publish ./dist/1.4.0/skillhub/sanmu-paper-reader --dry-run
skillhub publish ./dist/1.4.0/skillhub/sanmu-paper-search --dry-run
```

After validation, submit each directory:

```bash
skillhub publish ./dist/1.4.0/skillhub/sanmu-doc2md --changelog "Initial release"
skillhub publish ./dist/1.4.0/skillhub/sanmu-paper-reader --changelog "Initial release"
skillhub publish ./dist/1.4.0/skillhub/sanmu-paper-search --changelog "Initial release"
```

`pending_review` means submitted, not publicly listed.

## SkillsMP and skills.sh

Keep the `agent-skills` GitHub topic and add `claude-skills` or `claude-code-skill` as specified by the SkillsMP FAQ. Wait for automatic indexing and verify a real detail page; adding a topic is not proof of listing.

The [skills.sh collection page](https://skills.sh/luffysolution-svg/sanmu-skills) uses `npx skills add` and anonymous installation telemetry. No separate npm package is required.
