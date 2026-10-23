<div align="center">

# sanmu-paper-reader

**Understand papers, line up the evidence, and build reusable research knowledge from materials and chemistry literature.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English** · [Back to collection](https://github.com/luffysolution-svg/sanmu-skills/blob/main/README.en.md)

[Installation](#installation) · [Scenarios](#scenarios) · [Showcase](#full-showcase) · [Domain checks](#five-checks-for-materials-and-chemistry) · [Validation](#validation-and-limits)

</div>

Works on existing papers, SI, Zotero attachments and parsed Markdown, and supports single-paper close reading, cross-paper comparison, interdisciplinary transfer, cross-topic synthesis and knowledge-base questions. The core is linking claims to evidence that has actually been read, and keeping conditions, limitations and open research questions. Depending on the task, it can add figure-text alignment (collapsible callouts and wikilink hover previews), draft SOPs for reproduction preparation, Mermaid mechanism diagrams, targeted SI search, benchmark comparison and authorized Zotero tag updates, while keeping evidence sources, units, operating conditions, and the distinction between what the authors report and research hypotheses.

## Full showcase

A photocatalytic hydrogen evolution paper published in the leading materials journal *Advanced Materials* (2024, 36, 2313513) was used as the close-reading test sample. The actual delivered result in an Obsidian knowledge base:

<details open>
<summary><b>1. Note structure and multi-level outline navigation (click to expand/collapse)</b></summary>

![Obsidian note structure and navigation](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_overview.png)
</details>

<details>
<summary><b>2. Draft reproduction-preparation SOP and recipe card (parameters and gaps recorded as in the source)</b></summary>

![Obsidian SOP recipe card](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_sop_card.png)
</details>

<details>
<summary><b>3. Native Mermaid flowchart of the reaction mechanism and charge dynamics</b></summary>

![Obsidian Mermaid flowchart](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_mermaid_mechanism.png)
</details>

<details>
<summary><b>4. Wikilink hover previews and collapsible subfigure alignment (Sub-figure Crop)</b></summary>

![Obsidian figure-text evidence alignment and cropped subfigures](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_visual_evidence.png)

![Obsidian collapsible figure and Figure 3 comparison](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_figure_comparison.png)
</details>

<details>
<summary><b>5. Targeted probing of a 50+ page SI and side-by-side catalytic performance benchmark</b></summary>

![Obsidian targeted SI probe and benchmark comparison](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_si_probe.png)
</details>

## Installation

### Ask your AI assistant to install it (recommended)

Copy this prompt:

```text
Please install and configure the sanmu-paper-reader literature reading skill:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git and Node.js, then install it into the current assistant's user-level skill directory with:
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
   Select the target for the current assistant; if it is already installed, check for local edits first.
2. Read SKILL.md and README.en.md in the installed directory.
3. The reading itself is done by the current assistant; no separate model API or deep-research is needed. The evidence, topic and benchmark tools use the Python 3.10+ standard library. When SI probing, PDF figure extraction or subfigure cropping is needed, run python -m pip install -r requirements.txt in a dedicated virtual environment to install PyMuPDF and Pillow.
4. From the skill directory containing SKILL.md, run python scripts/evidence_tools.py --help to confirm the script entry point. Installation does not require running the source repository's development tests.
5. Read Markdown directly when I provide it; when I provide a PDF, check the current host's PDF reading capability; when I provide a Zotero item, check the Zotero connection. Configure an available OCR tool only when scanned documents need parsing.
6. Do not display credential values. Tell me the skill location, the entry-point check result, and which optional reading capabilities are available.
```

### Command-line installation

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
```

The core reading and the evidence, topic and benchmark tools use the Python 3.10+ standard library. `si_probe.py` and `extract_si_figures.py` need PyMuPDF, and `crop_subfigures.py` needs Pillow. To use these features, install from the skill directory in a dedicated virtual environment:

```bash
python -m pip install -r requirements.txt
```

The skill installer does not install these dependencies automatically. PDF reading, OCR and Zotero can also be provided by the host or by optional tools; none of them is tied to a particular platform. To install from unpublished local source, copy or link the complete `skills/sanmu-paper-reader` directory into your assistant's skill directory; the remote command can only install versions already published to the repository.

## Scenarios

| Scenario | Example request | Main output |
| --- | --- | --- |
| Single paper | Read this photocatalysis paper closely, explain Fig. 4 and Fig. 5, and separate experimental observations from mechanistic inferences. | Core claims, evidence, methodological gaps and research ideas |
| Cross-paper | Compare MatterGen and GNoME, focusing on stability definitions, reference convex hulls and how experimental validation is defined. | Comparison table with conditions; agreements and disagreements |
| Cross-discipline | Transfer the methods of materials generative models to catalyst design, and point out concept correspondences and validation gaps. | Mapping of applicability conditions, mismatch risks and a validation plan |
| Cross-topic | Organize crystal generation, defect engineering and photothermal catalysis into one research question, separating literature support from new hypotheses. | Links between topics, evidence gaps and a reading path |
| Knowledge-base questions | In my existing papers, which ones support "defects always improve hydrogen evolution"? Find counterexamples and limiting conditions. | Sourced answer and search scope |

Asking one specific question does not produce a set of long documents. A full close reading produces a standalone reading note by default; cross-paper work and long-term accumulation can come with an evidence table, and source text is stored separately from the user's own existing views.

## Five checks for materials and chemistry

- **Synthesis and characterization**: precursors, composition, phases, heat treatment, sample identity and structural evidence; avoid presenting indirect characterization as the only mechanism.
- **Catalysis and electrochemistry**: light source, wavelength, reaction medium, temperature, loading, reference electrode, normalization and blank controls; photocatalysis and electrocatalysis are checked separately according to the actual study.
- **DFT and computation**: functional, U/spin, k-points, convergence, surfaces/defects, energy references, free-energy corrections; computed stability does not mean kinetic feasibility or synthesizability.
- **Spectroscopy and mechanisms**: peak assignment, fitting, controls, timescales, in-situ states and alternative explanations; PL intensity and lifetime must be interpreted in relation to the specific process.
- **Performance comparison**: metric definitions, units, conditions, samples and uncertainty; materials ML additionally separates training/test sets, data leakage, prediction/DFT/experimental validation and statistical denominators.

Reference files are organized in two layers, by scenario and by discipline, and the main file loads only the parts the current task needs. Cross-discipline and cross-topic work have their own methods instead of stitching together single-paper summary templates.

## Offline evidence and research tools

Run from the skill directory:

```bash
# 1. Evidence checking and inventory generation
python scripts/evidence_tools.py inventory ./sources --out ./work/sources.json
python scripts/evidence_tools.py check ./work/evidence.json --root ./sources --out ./work/check.json

# 2. Targeted search in text-layer SI and figure / full-page previews
python scripts/si_probe.py ./sources/paper-SI.pdf --search "TEOA,xenon,Hubbard"
python scripts/extract_si_figures.py ./sources/paper-SI.pdf --out-dir ./images/SI

# 3. Precise cropping of subpanels from large figures (Sub-figure Crop)
python scripts/crop_subfigures.py ./images/master_fig.png --out-dir ./images/subfigures --spec-json ./crops.json

# 4. Structured, side-by-side logging of performance benchmarks
python scripts/benchmark_collector.py add ./benchmarks.csv --json-entry '{"doi":"10.1002/adma.202313513","condition_id":"420nm","activity_rate":"10.71","activity_unit":"mmol g-1 h-1","source":"SI p.4 Table S1"}'

```

| Tool | What it does | Notes |
| --- | --- | --- |
| `evidence_tools.py` | `inventory` records relative paths, hashes and headings of Markdown sources; `check` checks fields, paths, hashes and quotations in the evidence table | No network access and no scientific conclusions; the assistant fills in the evidence table after reading, following the [evidence contract](references/evidence-contract.md); refuses to overwrite an existing file with different content |
| `si_probe.py` | Targeted search for parameters and captions in SI with a text layer | Scans need OCR first |
| `extract_si_figures.py` | Extracts SI figures by Figure S / Fig. S captions | The manifest's `extraction_mode` distinguishes `embedded_image` from `page_render`; multiple figures on one page share one full-page image; captions can be missed, so confirm on the original page |
| `crop_subfigures.py` | Crops a large multi-panel figure into single subfigures by coordinates | Show the image for whichever panel is being discussed |
| `benchmark_collector.py` | Records performance data with units, operating conditions and sources; can export Markdown | Use `record_id` or "DOI + `condition_id`" to distinguish conditions; only explicitly given fields are updated, and `""` / `null` clears a field; ambiguous matches raise an error without writing to disk; historical records are not merged automatically |

## Optional topic routing and layered reading

When existing textbooks, slides, papers or parsed SI need repeated questions, standard-library tools can build topic entry points that keep source file hashes and original line numbers:

```bash
python scripts/topic_tools.py build --root ./sources --files textbook.md paper.md --out ./work/topics
python scripts/topic_tools.py query ./work/topics --root ./sources --query "Does first-order half-life depend on initial concentration?" --limit 5
python scripts/topic_tools.py read ./work/topics --root ./sources --id "<returned chunk ID>"
```

If a doc2md `agent/` index exists, add `--agent-index ./sources/agent` to reuse its chunks and page numbers; without a page mapping, only line numbers are given. For Chinese synonyms, `--terms` can add keywords from the source text. The tool generates section routing and candidate passages; it does not generate summaries or scientific conclusions automatically. If the sources change, queries are refused and the index must be rebuilt and the evidence rechecked. The assistant escalates its reading through "topic entry, optional summary, source evidence", and checks numbers, formulas, conditions and disagreements against the source. See the [reading and real-question validation guidelines](references/topic-reading.md) (in Chinese).

## Working with parsing tools

The skill can read [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-doc2md/README.en.md) output directly but does not depend on that skill. Existing reliable parsing results are reused first; native text exports serve only as reading text, and complex formulas, figures, tables and two-column order need to be checked against the PDF. When SI has not been obtained, this is stated explicitly, and experimental or computational parameters from it are not guessed.

Zotero is only an optional source. The skill does not operate on the database directly or walk the storage directory. Reading and local testing do not modify the library; writing notes or annotations happens only with user authorization and is read back.

Developers can run `python -m unittest discover -s tests/sanmu-paper-reader -v` from the source repository root (image regression tests need PyMuPDF and Pillow). Tests live at the repository root and are not required to run as part of skill installation. An SOP is a reproduction-preparation draft based on sources that have been read: unknown quantities, heating programs, calibration and the like must be marked as missing with their source locations, and an executable protocol is prepared only after review by an experimenter.

## Validation and limits

Materials and chemistry are currently the main areas of iteration; the skill does not claim to cover all subfields.

Passing the mechanical checks only means the file and citation structure can be checked; it does not prove that page numbers, figure numbers, semantic support or scientific conclusions are correct. Interpretations must separate what the authors report, what the assistant interprets, and hypotheses still to be tested. Answers from a personal library are not the same as a complete systematic review.

## License

[MIT](LICENSE). Paper PDFs, user libraries and private annotations are not distributed with the skill.
