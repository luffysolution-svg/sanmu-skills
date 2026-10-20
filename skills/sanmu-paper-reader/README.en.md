<div align="center">

# sanmu-paper-reader

**Read papers, trace evidence, and build reusable research knowledge in materials science and chemistry.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English**

[Installation](#installation) · [Scenarios](#scenarios) · [Showcase](#showcase) · [Domain checks](#materials-and-chemistry) · [Validation](#validation-and-limits)

</div>

Interpret papers, supporting information, Zotero attachments, and existing Markdown through single-paper reading, cross-paper comparison, interdisciplinary transfer, cross-topic synthesis, and evidence-grounded library questions. Enable visual evidence previews, replication-preparation SOP drafts, Mermaid mechanism charts, SI searches, benchmark comparisons, and authorized Zotero tagging according to the task. Preserve experimental conditions, uncertainties, and the distinction between reported findings and new hypotheses.

## Showcase

Benchmark reading results demonstrated on an *Advanced Materials* (2024, 36, 2313513) photocatalysis research paper in Obsidian:

<details open>
<summary><b>1. Note Structure & Outline Navigation View (Click to collapse/expand)</b></summary>

![Obsidian Note Structure](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_overview.png)
</details>

<details>
<summary><b>2. Replication-Preparation SOP Draft & Recipe Card (Reported parameters and gaps)</b></summary>

![Obsidian SOP Card](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_sop_card.png)
</details>

<details>
<summary><b>3. Native Mermaid Reaction Mechanism Flowchart</b></summary>

![Obsidian Mermaid Flow](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_mermaid_mechanism.png)
</details>

<details>
<summary><b>4. Bidirectional Evidence Hover & Sub-figure Crop Alignment</b></summary>

![Obsidian Subfigure Alignment](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_visual_evidence.png)

![Obsidian Collapsible Figure 3 Comparison](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_figure_comparison.png)
</details>

<details>
<summary><b>5. 50+ Page SI Fast-Probe & Catalytic Benchmark Comparison</b></summary>

![Obsidian SI Probe and Benchmark](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_si_probe.png)
</details>

## Installation

### Ask your AI assistant to install it (recommended)

Copy this prompt:

```text
Please install and configure the sanmu-paper-reader skill:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git and Node.js, then install at user scope for the current assistant:
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
   Select the correct assistant target and check existing local edits before updating.
2. Read SKILL.md and README.en.md in the installed skill directory.
3. The current assistant performs the reading. No separate model API or deep-research skill is required. Evidence, topic and benchmark tools use the Python 3.10+ standard library. For SI probing, PDF figure extraction or subfigure cropping, run python -m pip install -r requirements.txt in a dedicated virtual environment to install PyMuPDF and Pillow.
4. From the directory containing SKILL.md, run python scripts/evidence_tools.py --help to check the entry point. Installing the skill does not require the source repository development tests.
5. Read provided Markdown directly. Check the host's PDF reader for PDFs and its Zotero connection for library items. Configure an OCR tool only when scanned documents need it.
6. Do not display credentials. Report the installation path, entry-point check, and available optional reading integrations.
```

### Command-line installation

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
```

Reading instructions and the evidence, topic and benchmark tools use the Python 3.10+ standard library. `si_probe.py` and `extract_si_figures.py` require PyMuPDF; `crop_subfigures.py` requires Pillow. To use these features, run from the skill directory in a dedicated virtual environment:

```bash
python -m pip install -r requirements.txt
```

Skill installers do not install these dependencies automatically. PDF readers, OCR, and Zotero can also be provided by optional host integrations. For an unpublished local checkout, copy or link the complete `skills/sanmu-paper-reader` directory into your assistant's skill directory; remote installation only retrieves published versions.

## Scenarios

| Scenario | Example request | Result |
| --- | --- | --- |
| Single paper | Explain the evidence in Figures 4 and 5 of this photocatalysis paper; distinguish observations from mechanisms. | Claims, evidence, methodological gaps, research implications |
| Cross-paper | Compare MatterGen and GNoME by stability definitions, reference hulls, and experimental verification. | Condition-aware comparison and disagreements |
| Cross-discipline | Transfer crystal generation methods to catalyst design and identify assumptions that do not transfer. | Concept mapping, limitations, validation plan |
| Cross-topic | Connect crystal generation, defect engineering, and photothermal catalysis around a research question. | Supported connections, gaps, reading priorities |
| Library questions | Which of my papers support or qualify the claim that defects improve hydrogen production? | Traceable answer with search coverage |

A focused question can receive a focused answer. Full reading notes and evidence ledgers are appropriate for deeper work and ongoing knowledge accumulation.

## Materials and chemistry

Five domain references are loaded only when relevant:

- **Synthesis and characterization:** composition, phases, processing, sample identity, and the limits of structural evidence.
- **Catalysis and electrochemistry:** illumination, wavelength, medium, temperature, loading, electrode reference, normalization, controls, and thermal effects.
- **DFT and computation:** functional, spin/U, k-points, convergence, defects/surfaces, energy references, and free-energy corrections. Computed stability does not establish synthesizability.
- **Spectroscopy and mechanisms:** assignments, fitting assumptions, controls, timescales, operando conditions, and alternative explanations.
- **Performance comparison:** metric definitions, units, conditions, uncertainty, and validation levels. Materials ML also requires checking dataset splits, leakage, reference datasets, and denominators.

Instructions and references are currently written in Chinese; the assistant can apply them in the requested output language. Scenarios and domain checks are separate layers rather than one fixed summary template.

## Offline Evidence & Research Tools

From the skill directory:

```bash
# 1. Evidence ledger inventory & checks
python scripts/evidence_tools.py inventory ./sources --out ./work/sources.json
python scripts/evidence_tools.py check ./work/evidence.json --root ./sources --out ./work/check.json

# 2. Search text-layer SI and save embedded figures or page previews
python scripts/si_probe.py ./sources/paper-SI.pdf --search "TEOA,xenon,Hubbard"
python scripts/extract_si_figures.py ./sources/paper-SI.pdf --out-dir ./images/SI

# 3. Precision subfigure cropping (Sub-figure Crop)
python scripts/crop_subfigures.py ./images/master_fig.png --out-dir ./images/subfigures --spec-json ./crops.json

# 4. Cross-literature catalytic benchmark logging
python scripts/benchmark_collector.py add ./benchmarks.csv --json-entry '{"doi":"10.1002/adma.202313513","condition_id":"420nm","activity_rate":"10.71","activity_unit":"mmol g-1 h-1","source":"SI p.4 Table S1"}'

```

`inventory` records Markdown paths, hashes, titles, and headings. After reading, populate the evidence ledger using the [evidence contract](references/evidence-contract.md). `check` validates structure, path boundaries, source hashes, and quoted text. It makes no network or model calls. Conflicting existing outputs are rejected; use new filenames for revisions. `si_probe.py` searches text-layer SI; scans need OCR first. `extract_si_figures.py` recognizes line-start Figure S / Fig. S captions and labels each manifest entry with `extraction_mode`: `embedded_image` or `page_render`. Multi-figure, multi-image and vector-containing pages use full-page previews. Figure IDs on one page share `Page_XXX.png`; these are not isolated figure crops. Caption detection can miss figures and requires source-page review. `crop_subfigures.py` crops multi-panel scientific figures into focused subfigures. `benchmark_collector.py` records performance with units, conditions and sources. Use a unique `record_id`, or DOI/citation key + `condition_id`, to distinguish conditions within one paper. Updates patch only fields explicitly provided in JSON; use `""` or `null` to clear a field. A unique legacy row without identifiers still supports paper-only updates. Ambiguous or identified rows require a record/condition identifier; rejected updates do not write the file. Historical and custom CSV columns survive updates; historical records are not automatically merged.

## Optional topic routing and layered reading

For repeated questions over existing textbook, slide, paper, or SI Markdown, build a topic entry point with source hashes and exact line ranges using the Python standard library:

```bash
python scripts/topic_tools.py build --root ./sources --files textbook.md paper.md --out ./work/topics
python scripts/topic_tools.py query ./work/topics --root ./sources --query "Does first-order half-life depend on initial concentration?" --limit 5
python scripts/topic_tools.py read ./work/topics --root ./sources --id "<returned chunk ID>"
```

Add `--agent-index ./sources/agent` to reuse verified doc2md chunks and page mappings. Without a page map, only source lines are reported. Use `--terms` for source-language keywords or synonyms. The tool generates navigation and candidate passages, not summaries or scientific conclusions; changed sources invalidate queries and require rebuilding and evidence review. The assistant reads topic entries, optional summaries, then source evidence, checking exact numbers, formulas, conditions, and disagreements against the source. See the [reading and real-question validation protocol](references/topic-reading.md) (Chinese).

## Document and library integration

Existing [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-doc2md/README.en.md) output can be reused, but that skill is not required. Native PDF text is only a reading aid: formulas, tables, column order, and figure content may require source-page inspection. Record unavailable supporting information instead of inventing missing parameters.

Use supported Zotero integrations or verified read-only export interfaces. Do not access the database or scan storage directories. Reading tests do not modify the library; authorized writes require verification and read-back.

Developers can run `python -m unittest discover -s tests/sanmu-paper-reader -v` from the source repository root (image regressions need PyMuPDF and Pillow). Tests live at the repository root and are not required for skill installation. SOPs are replication-preparation drafts based on inspected sources: mark missing quantities, thermal programs, calibration details and their source locations. An experimenter must review them before preparing an executable protocol.

## Validation and limits

Initial domain development focuses on materials science and chemistry; it does not imply comprehensive validation across subfields.

Mechanical checks do not validate page numbers, figure identifiers, semantic support, or scientific correctness. Separate author-reported findings, assistant interpretations, and hypotheses. Answers from a personal library are not automatically systematic reviews.

## License

[MIT](LICENSE). Source PDFs, private libraries, and personal annotations are not distributed with the skill.
