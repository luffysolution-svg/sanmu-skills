<div align="center">

# sanmu-paper-search

**Start from a topic, paper or author and find literature you can verify and read.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![No dependencies](https://img.shields.io/badge/core-stdlib%20only-brightgreen.svg)](#sources-and-configuration)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.md) · **English** · [Back to collection](https://github.com/luffysolution-svg/sanmu-skills/blob/main/README.en.md)

[Installation](#installation) · [Examples](#examples) · [Commands](#command-reference) · [Sources](#sources-and-configuration) · [Results and limits](#results-and-limits)

</div>

Topic search, author publications, references / citing papers / similar papers, cross-source deduplication, full-text and SI location, and Zotero holdings matching. Everyday requests for a few papers get a list of candidates directly; only proposal-stage or systematic searches record a full protocol. The core CLI uses only the Python 3.10+ standard library and does not depend on any research service or model.

## Installation

### Ask your AI assistant to install it (recommended)

```text
Please install and configure sanmu-paper-search:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git, Node.js and Python 3.10+, then run:
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
   If it is already installed, check for local edits first.
2. From the skill directory run:
   python scripts/paper_search.py doctor
   python scripts/paper_search.py --help
3. Check whether OPENALEX_API_KEY, SEMANTIC_SCHOLAR_API_KEY (S2_API_KEY also accepted), UNPAYWALL_EMAIL, CROSSREF_EMAIL,
   and the optional SERPER_API_KEY and EXA_API_KEY are configured, without displaying values. Europe PMC needs no key.
   Without credentials, explain what is available; only point to the official registration pages when actually needed.
4. If Zotero is needed, check the host plugin and the local API; do not read the database or storage directly. Searching also works without Zotero.
5. Create a new working directory outside the repository, run one small query, and report per-source status, the installation path and limitations; do not change Zotero.
```

### Manual installation

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
```

You can also copy the whole skill directory to `~/.agents/skills/sanmu-paper-search`. No `pip install` is needed; PyMuPDF is optional if you want to check paper identity against the PDF text layer.

## Examples

> Find papers on frustrated Lewis pairs for photothermal-assisted hydrogen evolution, explain why each is worth reading, and check which have full text or SI available.

> Starting from the MatterGen paper, find its references, later citing papers and similar studies separately, and check which are already in Zotero.

> Find Claudio Zeni's other materials-generation work. Confirm the author's identity first, and do not mix in authors with the same name.

> Has this paper been retracted or had an erratum? Also download the original figures from the supplementary material.

> Update the materials inverse-design literature I compiled last year, list the added and missing records, and keep the earlier screening decisions.

## Command reference

Run from the skill directory. Point `--out` to a new directory in your personal workspace, not inside a public repository. On Windows, use the actual absolute path in quotes.

```bash
python scripts/paper_search.py doctor

# Topic, seed paper, author
python scripts/paper_search.py search "generative inorganic materials design" --sources openalex,semantic-scholar,crossref --limit 5 --out /path/to/work/topic
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --out /path/to/work/seed
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out /path/to/work/authors
python scripts/paper_search.py author "Tian Xie" --provider openalex --from-paper 10.1038/s41586-025-08628-5 --out /path/to/work/author-from-paper

# Citations, open access, verification
python scripts/paper_search.py related 10.1038/s41586-025-08628-5 --relation citations --limit 5 --out /path/to/work/citations
python scripts/paper_search.py oa /path/to/work/seed --out /path/to/work/oa
python scripts/paper_search.py check /path/to/work/seed
```

- After checking the author candidates, use `author --author-id ID --provider ...` to fetch publications.
- `search` and author publications support `--year-from` / `--year-to`.
- `--limit 1..20` is the candidate budget per source. All candidates are kept after cross-source deduplication, without cross-database reranking; the assistant reads them and forms the shortlist.

Full parameters and record formats are in the [CLI guide](references/cli.md).

### Europe PMC: full text, SI and retraction checks (no key)

```bash
python scripts/paper_search.py epmc "photocatalytic hydrogen" --has-suppl --open-access --limit 5 --out /path/to/work/epmc-search
python scripts/paper_search.py epmc PMC12710600 --fetch all --out /path/to/work/epmc-bundle
```

Supports preprints (`--preprints`), associated data (`--has-data`), and retraction and erratum checks. `--fetch all/xml/suppl` downloads the JATS full-text XML and the supplementary-material archive, and safe extraction yields the SI PDFs and the high-resolution original figures from the main text. Note that its coverage of materials journals is incomplete, so it should not be the only search source; see the [Europe PMC guide](references/epmc.md).

### Serper: Google Scholar, web, patents (optional, needs `SERPER_API_KEY`)

```bash
python scripts/paper_search.py serper "single atom carbon nitride photocatalytic hydrogen" --type scholar --year-from 2020 --year-to 2022 --limit 5 --out /path/to/work/scholar
```

13 types of public endpoints in total, billed per request. Results are saved in `results.json` / `response.json` and `papers.json` stays empty; verify stable IDs before entering the paper workflow. Configuration, batch requests and costs are in the [Serper guide](references/serper.md).

### Exa: find papers from a description (optional, needs `EXA_API_KEY`)

```bash
python scripts/paper_search.py exa "papers on frustrated Lewis pairs for photothermal hydrogen evolution" --type auto --limit 5 --year-from 2023 --out /path/to/work/exa
```

Only structured publication entities are written to `papers.json`, as candidates to be verified; the full results are saved separately. The `costDollars` value returned by the API serves as a cost reference. See the [Exa guide](references/exa.md).

## Sources and configuration

| Source | Use | Configuration |
| --- | --- | --- |
| OpenAlex | Topics, authors, citations, OA locations | `OPENALEX_API_KEY`; without it, degrades according to the official limits |
| Semantic Scholar | Topics, authors, citations, similar-paper recommendations | `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY`; the anonymous shared pool may be rate-limited |
| Crossref | DOI and bibliographic verification, supplementary recall | Optional `CROSSREF_EMAIL` |
| Europe PMC | Preprints, associated data, retraction checks, JATS full text and SI original-figure bundles | No key |
| Unpaywall | OA locations for a DOI | `UNPAYWALL_EMAIL`; not used for keyword search |
| Serper (optional) | 13 types of endpoints including Google Scholar, web and patents | `SERPER_API_KEY`, billed per request |
| Exa (optional) | Finding publications from a natural-language description | `EXA_API_KEY` |
| Zotero | Checking holdings and existing attachments | Host Zotero tools, or a local standard JSON export |

`doctor` only reports whether each credential is configured and never displays the values.

## Results and limits

- **Every run leaves a record**: the query, per-source status, normalized bibliographic records, redacted raw responses, a readable list and hashes; failure, zero results, truncation and not-run are marked separately.
- **Identity is matched by stable IDs**: conflicting fields keep their sources; authors are disambiguated first, and works are fetched from a seed paper only when the name matches uniquely; preprints and published versions keep their relationship and are not merged into one record by title.
- **Rate limiting and retries**: Semantic Scholar is rate-limited across processes for the same key, and `Retry-After` is honored; when a publisher omits references, this is recorded as a restricted status, not as zero references.
- **Found ≠ downloaded ≠ read**: `metadata_only` / `abstract` only indicate the text level returned by the API; a screening basis is recorded only after actual reading.
- **Full text and SI**: public PDFs are downloaded with `scripts/fetch_pdf.py`, recording the source and hash; SI from other publishers is checked item by item by the assistant, recording what was obtained and what is missing.
- **Completeness has limits**: a search with a limited budget cannot prove completeness; systematic searches record per-source budgets and the screening process according to the [search protocol](references/protocol.md).
- **Stays within bounds**: does not bypass logins or paywalls and does not operate on the Zotero database directly; do not publicly commit private PDFs, notes, credentials or run records.

Once you have the full text, you can hand it to [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-doc2md/README.en.md) for conversion and [sanmu-paper-reader](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-paper-reader/README.en.md) for close reading; if neither is installed, the literature list, sources and files are still delivered.

## Validation and design references

After installation, run `doctor` first, then search for a few papers using the examples above and validate the output with `check`. Development tests cover field normalization, identity merging, error states, output protection and hashes; run them from the repository root:

```bash
python -m unittest discover -s tests/sanmu-paper-search -v
```

Design references: [K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup), [research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search), [OmniScholar](https://github.com/luffysolution-svg/omnischolar), [paper-search-pro](https://github.com/O0000-code/paper-search-pro), [easyseek](https://github.com/Rimagination/easyseek). The core code is implemented independently and does not copy source code from these projects. Further reading: [first-run setup and API keys](references/setup.md) (Chinese) · [retrieval strategy](references/retrieval.md) · [acquisition and handoff](references/handoff.md).

## License

[MIT](LICENSE). The respective licenses of database content, papers and third-party services continue to apply.
