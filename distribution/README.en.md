# Distribution

[简体中文](README.md) · **English** · [Collection](../README.en.md)

Maintain skill sources only in `skills/`. Claude Code and Codex install the collection from repository marketplaces. Tencent SkillHub uses three standalone packages; SkillsMP and skills.sh reference public GitHub content.

## Version and build

Set the version in `manifest.json`, root `plugin.json`, and `.claude-plugin/plugin.json`. Keep skill names stable. Tencent slugs default to the same skill names; check availability before the first publication.

Run from the repository root with Python 3.10+ and Git:

```bash
python distribution/build.py check
python -m unittest discover -s distribution/tests -v
python distribution/build.py build --out dist/1.2.0
```

Stage new files before building. Only current Git-tracked files are included. Untracked credentials, private materials, caches and installed dependencies are excluded. Existing output directories are never overwritten; use a new output path for another build.

Outputs:

- `sanmu-skills-1.2.0.zip`: portable plugin with all three skills, root `plugin.json`, Claude metadata and licenses. It can be submitted to the OpenAI public plugin directory, subject to platform checks and review.
- `skillhub/<skill-name>/`: directories for Tencent CLI publication.
- `skillhub/<skill-name>-1.2.0.zip`: archives matching those directories, with root `SKILL.md`. Check the live web uploader for its accepted formats.
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

Complete identity verification and configure an API Token using the [official guide](https://skillhub.cn/tutorials). The Windows publication guide uses WSL. Keep tokens out of source files, examples and commits.

```bash
skillhub auth whoami
skillhub publish ./dist/1.2.0/skillhub/sanmu-doc2md --dry-run
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-reader --dry-run
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-search --dry-run
```

After validation, submit each directory:

```bash
skillhub publish ./dist/1.2.0/skillhub/sanmu-doc2md --changelog "Initial release"
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-reader --changelog "Initial release"
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-search --changelog "Initial release"
```

`pending_review` means submitted, not publicly listed. If a slug is taken, verify the account and existing entry before changing generated publication metadata; keep portable skill names unchanged.

## SkillsMP and skills.sh

Keep the `agent-skills` GitHub topic and add `claude-skills` or `claude-code-skill` as specified by the SkillsMP FAQ. Wait for automatic indexing and verify a real detail page; adding a topic is not proof of listing.

The [skills.sh collection page](https://skills.sh/luffysolution-svg/sanmu-skills) uses `npx skills add` and anonymous installation telemetry. No separate npm package is required.
