<div align="center">

# sanmu-doc2md

**Turn papers, textbooks and slides into a Markdown knowledge base you can maintain over time.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![KaTeX validation](https://img.shields.io/badge/KaTeX-validation-brightgreen.svg)](https://katex.org/)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[简体中文](README.md) · **English** · [Back to collection](https://github.com/luffysolution-svg/sanmu-skills/blob/main/README.en.md)

[Installation](#installation) · [What it solves](#what-it-solves) · [Three routes](#three-routes-for-three-document-types) · [Case studies](#case-studies) · [Commands](#command-reference)

</div>

Converts scanned textbooks, PDF books, science and engineering slides, Chinese and English papers, and theses into Markdown with high-resolution images, tables and renderable LaTeX formulas, for reading in Obsidian and for retrieval by AI assistants.

The hard part is not getting output, but getting correct output: formula meaning must not change, figures must be complete, and page numbers must lead back to the source. So the skill takes the most reliable content it can from the source file, goes back to the original page when unsure, and records every correction. A formula that compiles only has valid syntax; figures and scientific symbols still need to be checked against the source, and you should take a final look in the client you actually use.

## Installation

### Ask your AI assistant to install it (recommended)

Send the following to Claude Code, Codex, WorkBuddy, Cursor or another AI coding assistant:

```text
Please install and configure the sanmu-doc2md skill:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git, Node.js and Python, then install the skill into the current assistant's user-level skill directory:
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
   If it is already installed, check for local edits first to avoid overwriting them.
2. Find the installed sanmu-doc2md directory (it contains SKILL.md, requirements.txt and scripts/).
   Run all following commands in that directory, not in the collection repository root.
3. Preferably in a dedicated Python virtual environment, install the dependencies:
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
4. Check the environment (this does not submit OCR):
   python scripts/doctor.py
   python scripts/build_markdown.py --help
5. Check whether MINERU_API_TOKEN is configured, without displaying its value. If not, tell me to apply for one at https://mineru.net;
   if it is configured, run python scripts/doctor.py --require-ocr.
6. Tell me the installation path, the check results and anything that still needs to be configured.
```

### Manual installation

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
```

Go to the installed `sanmu-doc2md` directory (or `skills/sanmu-doc2md` when running from source). The commands below are relative to that directory:

```bash
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
python scripts/doctor.py
```

`doctor.py` checks whether Python, Node, KaTeX and credentials are ready, and test-compiles one formula. It does not install anything or display credential values. Add `--json` for structured output.

Cloud OCR needs the environment variable `MINERU_API_TOKEN` (apply at [mineru.net](https://mineru.net)). If you already have OCR results, assembly and validation work without a token.

## What it solves

Common failures when ordinary OCR tools convert science and engineering documents directly:

| Problem | Symptom |
| --- | --- |
| Broken formulas | Subscripts, superscripts, fractions and Greek letters are scrambled into isolated characters |
| Wrong layout | Reading order in long textbooks is reversed; text continued at the top of a page becomes a level-2 heading |
| Brackets parsed as links | `[通常为 $\mathcal{N}(0, I)$ ]` ("typically ...") turns into a blue link in Obsidian, exposing the formula source |
| Broken chemical formulas in headings | `$\mathrm{CO}_2$` loses its delimiters, leaving `CO_2`, which also triggers italics |
| Lost resources | End-of-chapter QR codes and complex data tables are swallowed by plain-text conversion |
| Typos | "轫致辐射" → "韧致辐射" (look-alike characters in "bremsstrahlung"), `ν` → `v`, `N₀` → `No` |
| Model commentary in the text | Lines from the OCR model such as `The Ground Truth image displays...` appear in the body |
| Old images pile up | After crop boxes are adjusted, old crops remain and the directory keeps growing |

All of these rules come from processing real textbooks and papers; the details are in the skill's reference documents.

## Three routes for three document types

Textbooks, slides and papers have very different layouts, and one shared process would break on some of them, so there are three routes:

| | Textbooks and monographs | Slides and lecture notes | Papers and theses |
| --- | --- | --- | --- |
| Entry point | `build_markdown.py --mode book` | `build_markdown.py --mode slides` | `build_research_document.py` |
| Organization | Split by chapter: `00-目录.md`, `01-第一章.md`… | By lecture or class session | One directory per paper: main note + `00-目录.md` |
| Text and figure layout | Figures and tables are placed in the text according to their position on the page | Images grouped at the top of the page, with the key-point text | Follows the checked OCR reading order; two columns do not run into each other |
| Headings | Only chapter numbers count; section headings are distinguished from exercise prompts | Each page's topic is its heading | Files named `Author - Year - Title` |
| Figures | Recropped from the source PDF at 3× or more; multi-panel figures merged | Original PPTX images preferred; small in-figure labels filtered out | High-confidence multi-panel stitching; two-way links to SI figures |
| Text and metadata | Substantive footnotes kept; running headers and footers removed | OCR typos corrected against the original PPT text | Full YAML: DOI, authors, abstract, Zotero link |

## Workflow

```text
1. Preflight   Check for a text layer and embedded original images; decide which route to take
2. OCR         Split long documents into 96-page chunks and submit them to MinerU in batches
3. Proofread   Correct look-alike characters against the native text layer / original PPT text
4. Figures     Prefer original images, otherwise recrop from the PDF at 3×; merge fragments, keep QR codes in tables
5. Formulas    Chinese into \text, protect inequality signs, disambiguate brackets, keep heading formulas
6. Assembly    Generate 00-目录.md and the body text; optionally remove old crops that are no longer referenced
7. Validation  Structure and link checks + full KaTeX preflight + an actual look in Obsidian
```

## Case studies

The case documents in the repository record the following results. These numbers describe these specific documents and are not a quality guarantee for arbitrary documents.

| Case | Documents | Formulas | Images | Result |
| :--- | :--- | :---: | :---: | :--- |
| Materials analysis and testing methods | 10 chapters, 694 pages of PPT slides | 2,118 | 199 | All formulas passed preflight; 7 typos corrected from native text; duplicate images removed |
| Physical Chemistry (6th edition), Vol. 2 | 556-page fully scanned textbook | 7,523 | 332 | KaTeX 7,523 / 7,523 passed; 28 end-of-chapter QR codes extracted |
| Chinese papers and theses | 5 documents, 611 pages in total (including a 240-page doctoral dissertation) | 5,797 | 398 | Fixed Obsidian bracket misparsing and heading chemical formulas; safely removed 89 old images |
| English journal papers | 7 papers, 87 pages in total (Nature, Matter, etc.) | 876 | 114 | Correct two-column reading order; long Windows paths work; complete YAML metadata |
| **Total** | **Nearly 2,000 pages** | **16,314** | **1,043** | All formulas in these cases compiled |

See [RESULTS.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESULTS.md) and [RESEARCH-VALIDATION.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESEARCH-VALIDATION.md) (both in Chinese). The [reproducible example](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/book-demo/demo.md) is kept in the repository and is not installed with the skill.

## Command reference

```bash
# Splitting and OCR
python scripts/mineru_ocr.py split path/to/document.pdf --out-root ./ocr-work --chunk-size 96
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# Textbooks
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./knowledge-base --mode book \
    --manifest ./ocr-work/manifest.json --chapters ./chapters.json \
    --pdf path/to/document.pdf --keep-table-images --prune-orphans

# Slides
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./knowledge-base \
    --mode slides --source-dir ./slides --pdf-dir ./slide-pdfs

# Papers
python scripts/build_research_document.py --pdf ./source.pdf --raw ./ocr-work/raw/article \
    --metadata ./metadata.json --out "./research/First Author et al - Year - Title"

# Validation
python scripts/validate_output.py ./knowledge-base --images-dir images
python scripts/extract_formulas.py ./knowledge-base --out formulas.json
node scripts/validate_math.cjs formulas.json

# Local preview (listens on 127.0.0.1 only)
python scripts/preview_server.py ./knowledge-base --port 8766 --katex-dir scripts/node_modules/katex/dist
```

## What the output looks like

- **Clean directory**: only navigation, Markdown body files and `images/`; build state is kept next to it in `.<directory-name>.build/`.
- **All paths relative**: images use `![](images/...)`, so the whole knowledge base can be moved freely and synced across devices.
- **Two-track fidelity**: formulas are standard LaTeX; complex tables and ring-shaped chemical structures also keep a crop of the original image.
- **Working navigation**: `00-目录.md` in the root uses URI-encoded links, so you can jump directly in Obsidian.
- **Traceable without overwriting**: source files, page mappings and the basis for each repair are kept; content you edited by hand is protected by default, and substantive footnotes are not deleted as page footers.

## What's included

```text
SKILL.md                        Skill description: route selection, workflow, lessons and pitfalls
references/
  book-workflow.md              Splitting long textbooks, regrouping chapters, common problems
  research-documents.md         Paper naming, metadata, SI two-way links and two-column reading order
  math-normalization.md         15 formula rules with real before/after examples
  repairs.md                    Source-based repairs: text, figure blocks, captions and scan noise
  setup.md                      First-run setup: dependencies and the MinerU token
  mineru-api.md                 MinerU API, result structure, coordinate system and download fallback
  zotero-sync.md                Zotero read-only API, MCP writes and read-back
scripts/
  doctor.py                     Environment and credential checks
  mineru_ocr.py                 Split / submit / poll / resume / download
  build_markdown.py             Textbook and slide assembly
  build_research_document.py    Paper assembly
  audit_figures.py              Review of fragmented multi-panel figures; generates repair drafts
  normalize_math.py             Formula normalization
  extract_formulas.py           Extract all formulas
  validate_output.py            Structure, link and leftover checks
  validate_math.cjs             Full KaTeX preflight
  preview_server.py             Local offline preview
  zotero_export.py              Read-only export of Zotero items and attachments
  zotero_sync.py                Sync Zotero metadata, notes and annotations
```

Development tests run from the repository root; see [Contributing](https://github.com/luffysolution-svg/sanmu-skills/blob/main/README.en.md#contributing).

## License

[MIT](LICENSE)
