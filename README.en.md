<div align="center">

# sanmu-skills

**Sanmu's Agent Skills collection**

Practical workflows, packaged as reusable skills for AI assistants.

[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English**

[Quick start](#quick-start) · [Skills](#skills) · [Usage](#usage) · [Contributing](#contributing)

</div>

## Quick start

### Ask your AI assistant to install it (recommended)

Copy this prompt into your coding assistant:

```text
Please install and configure Sanmu's sanmu-skills collection:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git and Node.js, then install the collection for the current AI assistant at user scope:
   npx skills add luffysolution-svg/sanmu-skills --skill '*' -g
   Select the target matching the current assistant and check for local edits before updating an existing installation.
2. Read each installed skill's README and configure its dependencies.
3. For sanmu-doc2md, enter the skill directory containing SKILL.md, prefer a dedicated Python virtual environment, and run:
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
   python scripts/doctor.py
   python -m unittest discover -s tests -v
4. Check MINERU_API_TOKEN configuration without displaying its value. If missing, direct me to https://mineru.net and explain how to set it locally.
5. For sanmu-paper-reader, use Python 3.10+ to run python -m unittest discover -s tests -v from its skill directory. Its evidence tools have no third-party dependencies; check PDF/OCR/Zotero integrations only as needed.
6. For sanmu-paper-search, use Python 3.10+ to run python scripts/paper_search.py doctor and python -m unittest discover -s tests -v from its directory. The core uses the standard library; check only whether API credentials are configured. For Zotero, run python scripts/zotero_local.py doctor as needed: no host MCP or cloud API key is required. Use doctor --apply within the authorized scope for the first local authorization dialog. Full-text acquisition prepares optional ScanSci in the user cache on demand, preferring an existing audited match, otherwise pinning 1.18.2 (new setup requires Python 3.11+). No manual ScanSci installation or Tor setup is required; institutional access uses the assistant's browser tool with an authorized session. The local Zotero app manages attachment sync; do not claim WebDAV completion without checking.
7. Report installation paths, validation results, and any remaining configuration steps.
```

Full AI installation instructions: [Document conversion](skills/sanmu-doc2md/README.en.md#option-1-ask-your-ai-assistant-to-install-and-configure-it-recommended) · [Paper reading](skills/sanmu-paper-reader/README.en.md#ask-your-ai-assistant-to-install-it-recommended) · [Paper search](skills/sanmu-paper-search/README.en.md#ask-your-ai-assistant-to-install-it-recommended)

### Command-line installation

Install the entire collection to your user directory:

```bash
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g
```

The installer guides you through selecting an AI assistant and an installation method. Git and a Node.js version supported by the [skills CLI](https://github.com/vercel-labs/skills) are required.

Install a specific skill:

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
```

## Skills

| Skill | Purpose | Documentation |
| --- | --- | --- |
| **sanmu-doc2md** | Turn papers, textbooks, and slides into a Markdown knowledge base with LaTeX formulas, high-resolution figures, and Zotero metadata. | [Usage guide](skills/sanmu-doc2md/README.en.md) |
| **sanmu-paper-reader** | Evidence-grounded paper reading, cross-paper comparison, interdisciplinary transfer, cross-topic synthesis, and library questions, with materials and chemistry checks. | [Usage guide](skills/sanmu-paper-reader/README.en.md) |
| **sanmu-paper-search** | Discover papers from topics, seeds or authors, verify metadata, expand citations, acquire available main text/SI, and deduplicate imports through an independent local Zotero client. | [Usage guide](skills/sanmu-paper-search/README.en.md) |

### sanmu-doc2md

A workflow for academic documents with dense formulas, complex figures, and challenging layouts, covering recognition, correction, knowledge-base assembly, and validation.

- **Document-specific organization**: chapters for textbooks, lectures for slides, and standalone notes for papers.
- **Scientific content preservation**: compile every formula and extract or recrop figures from the source.
- **Reference management**: Zotero metadata, annotation synchronization, and links between papers and supporting information.
- **Maintainable output**: relative paths, dedicated image directories, external build state, and guarded incremental updates.

[Explore the full guide and examples →](skills/sanmu-doc2md/README.en.md)

### sanmu-paper-reader

Interprets papers, supporting information, Zotero attachments, and Markdown notes with condition-aware claim extraction and traceable evidence.

- **Scenario-based workflows**: single-paper reading, cross-paper comparison, interdisciplinary transfer, cross-topic synthesis, and library questions.
- **Evidence-grounded claims**: separate observed facts from author claims and hypotheses; retain test conditions, uncertainty definitions, and unretrieved items.
- **Materials and chemistry checks**: specialized domain rules for synthesis and characterization, catalysis and electrochemistry, DFT computation, spectroscopy mechanisms, and performance benchmarks.
- **Offline evidence verification**: standard-library Python tools to verify source hashes and quoted text without exposing private documents to external networks.

[Explore the full guide and examples →](skills/sanmu-paper-reader/README.en.md)

### sanmu-paper-search

Search OpenAlex, Semantic Scholar and Crossref by default from a topic, seed paper or author. Select arXiv and Europe PMC explicitly with `--sources arxiv,europe-pmc`; they add no default requests. Retain provenance, merge evidence, failures and truncation. Distinguish free-to-read access from open licensing and preserve preprint/published-DOI relationships. Keep everyday searches lightweight and document broader protocols.

The Python 3.10+ standard-library core runs independently. `zotero_local.py` needs no host MCP or cloud API key; Zotero's local dialog authorizes the first write. `acquire.py` records main text and SI separately in an acquisition manifest; publisher HTML candidates require explicit selection. `ingest.py` verifies hashes and identity before deduplicating and importing. The local Zotero app handles subsequent WebDAV synchronization; check its completion separately.

Optional ScanSci prepares itself in the user cache on demand, reusing an existing version, including older versions, whose audited functions match. Otherwise it pins `scansci-pdf==1.18.2`; new setup requires Python 3.11+. Only hash-verified OpenAlex OA and Europe PMC OA functions run, never the default mixed-source downloader or institutional modules. Institutional access uses the assistant's browser tool with an authorized session, then `acquire --import-file`. Users need not install ScanSci manually or configure Tor. Full-text and SI coverage depends on actual sources and access results.

Start from the skill directory:

```bash
python scripts/zotero_local.py doctor
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition
python scripts/ingest.py /path/to/work/acquisition/manifest.json
```

`seed` is an existing search run. Ingestion previews by default; complete identity and SI review before adding `--apply` within the authorized scope. See the [CLI guide](skills/sanmu-paper-search/references/cli.md), [local Zotero guide](skills/sanmu-paper-search/references/local-zotero.md), and [installation and scope](skills/sanmu-paper-search/README.en.md).

Design reference: [ScanSci PDF](https://github.com/Rimagination/scansci-pdf). Optional source adapters extract and execute two audited upstream pure-Python functions at runtime after SHA-256 verification under ScanSci's separate [Apache-2.0 license and exceptions](https://github.com/Rimagination/scansci-pdf/blob/main/LICENSE). They neither bundle nor use its `_core` compiled extension. The search core is independently implemented.

[Installation, commands and scope →](skills/sanmu-paper-search/README.en.md)

## Usage

After installation, describe your task in an AI assistant that supports Agent Skills. For example:

> Turn this scanned textbook into a Markdown knowledge base, organized by chapter. Preserve formulas and high-resolution figures, and validate the complete output.

Each skill's guide documents its runtime and input requirements. `sanmu-doc2md` requires Python and KaTeX; cloud OCR also requires MinerU credentials. Follow [Installation and dependencies](skills/sanmu-doc2md/README.en.md#installation-and-dependencies), then run `scripts/doctor.py` to check your environment.

### Discover and update

```bash
# List the skills in this collection
npx skills add luffysolution-svg/sanmu-skills --list

# Update installed skills
npx skills update
```

### Manual installation

```bash
git clone https://github.com/luffysolution-svg/sanmu-skills.git
```

Copy or link the complete skill directory from `skills/` into your assistant's skill directory, such as `~/.agents/skills/sanmu-doc2md`. Keep the scripts, references, and examples together.

## Contributing

Use [GitHub Issues](https://github.com/luffysolution-svg/sanmu-skills/issues) to report problems, share reproducible cases, or suggest improvements. Pull requests are welcome.

Place each skill under `skills/<skill-name>/`, with a matching name in `SKILL.md`. Preserve the practical guidance in existing skills and keep changes focused on the issue being addressed.

Validate `sanmu-doc2md`:

```bash
python -m pip install -r skills/sanmu-doc2md/requirements.txt
npm --prefix skills/sanmu-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s skills/sanmu-doc2md/tests -v
python skills/sanmu-doc2md/scripts/doctor.py
```

Validate `sanmu-paper-reader` (Python 3.10+, no third-party dependencies):

```bash
python -m unittest discover -s skills/sanmu-paper-reader/tests -v
```

Validate `sanmu-paper-search` (Python 3.10+, standard-library core):

```bash
python -m unittest discover -s skills/sanmu-paper-search/tests -v
python skills/sanmu-paper-search/scripts/paper_search.py doctor
```

## License

This project is distributed under the [MIT License](LICENSE).
