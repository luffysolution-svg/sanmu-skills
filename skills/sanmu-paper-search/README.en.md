# sanmu-paper-search

**Find traceable, readable papers from a topic, seed paper, or author.**

[简体中文](README.md) · **English** · [Collection](https://github.com/luffysolution-svg/sanmu-skills/blob/main/README.en.md)

Topic discovery, author publications, references/citations/recommendations, cross-source deduplication, full-text locations, and Zotero matching. Small requests return a concise shortlist; broader searches retain a protocol and coverage limitations. The core CLI uses Python 3.10+ and the standard library, with no mandatory research platform or model dependency.

## Ask your AI assistant to install it (recommended)

```text
Please install and configure sanmu-paper-search:
https://github.com/luffysolution-svg/sanmu-skills

1. Check Git, Node.js and Python 3.10+, then run:
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
   Select the current assistant's user-level target; check local changes before updating.
2. Read SKILL.md and README. From the skill directory run:
   python scripts/paper_search.py doctor
   python scripts/paper_search.py --help
3. Check only whether OPENALEX_API_KEY, SEMANTIC_SCHOLAR_API_KEY (or S2_API_KEY), UNPAYWALL_EMAIL, CROSSREF_EMAIL and optional SERPER_API_KEY and EXA_API_KEY are configured. Never display values. Serper adds Google Scholar, web, patent and other public searches on demand; Exa adds natural-language publication discovery. Explain available capabilities without credentials; point to official registration only when needed.
4. For Zotero, check the host integration and local API. Do not read its database or storage directly. Core search also works without Zotero.
5. Run a small public-paper query into a new directory outside the repository. Report per-source status, installation path and limitations. Do not modify Zotero items.
```

Alternatively copy the complete directory into `.agents/skills/sanmu-paper-search`. No pip installation is required. PyMuPDF is optional for PDF text-based identity checks.

## Examples

> Find papers on frustrated Lewis pairs for photothermal-assisted hydrogen evolution, explain relevance, and check available main text and SI.

> Starting from MatterGen, separately retrieve references, citing papers and recommendations; check which are already in Zotero.

> Find Claudio Zeni's other materials-generation papers. Resolve author identity before collecting publications.

> Update my earlier materials inverse-design search and compare added/missing records while preserving previous screening decisions.

## CLI quick start

Run from the skill directory. Use new output directories in a personal workspace, outside the public repository:

```bash
python scripts/paper_search.py doctor
python scripts/paper_search.py search "generative inorganic materials design" --sources openalex,semantic-scholar,crossref --limit 5 --out /path/to/work/topic
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --out /path/to/work/seed
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out /path/to/work/authors
python scripts/paper_search.py author "Tian Xie" --provider openalex --from-paper 10.1038/s41586-025-08628-5 --out /path/to/work/author-from-paper
python scripts/paper_search.py related 10.1038/s41586-025-08628-5 --relation citations --limit 5 --out /path/to/work/citations
python scripts/paper_search.py oa /path/to/work/seed --out /path/to/work/oa
python scripts/paper_search.py check /path/to/work/seed
```

Optional Google discovery uses a separate command (configure `SERPER_API_KEY` first):

```bash
python scripts/paper_search.py serper "single atom carbon nitride photocatalytic hydrogen" --type scholar --year-from 2020 --year-to 2022 --limit 5 --out /path/to/work/scholar
```

See the [Serper guide](references/serper.md) for all 13 public API types, safe PowerShell/Bash configuration, batch requests and credit limits. For Serper, `--limit` caps retained results per query across pages locally. Typed results and complete responses go into `results.json` / `response.json`; `papers.json` stays empty. Verify stable IDs before entering the paper workflow. Scholar snippets are not abstracts, publication details may truncate authors, PDF links do not establish OA, and webpage extraction may omit article content.

For optional natural-language publication discovery, configure `EXA_API_KEY` and run:

```bash
python scripts/paper_search.py exa "papers on frustrated Lewis pairs for photothermal hydrogen evolution" --type auto --limit 5 --year-from 2023 --out /path/to/work/exa
```

Exa searches the publication category. Eligible structured entities become candidates in `papers.json`; `results.json` / `response.json` retain the full results and responses. Verify entity identity through scholarly sources; extracted text stays separate from abstracts. Reported `costDollars` provides a cost reference. See the [Exa guide](references/exa.md) for configuration, domain filters and output details.

On Windows, quote actual absolute paths. After checking candidates, use `author --author-id ID --provider ...` for publications. Topic search and author publications support `--year-from` / `--year-to`. For the core scholarly commands, `--limit 1..20` is a per-source candidate budget; all deduplicated candidates are retained in source order, without cross-provider relevance reranking. The assistant reads and screens the shortlist. See the [CLI contract](references/cli.md).

## Sources

| Source | Purpose | Configuration |
| --- | --- | --- |
| OpenAlex | Topics, authors, citations and OA locations | `OPENALEX_API_KEY`; anonymous capabilities depend on current limits |
| Semantic Scholar | Topics, authors, graph relations and recommendations | `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY`; the anonymous pool may throttle |
| Crossref | DOI/metadata verification and supplementary discovery | Optional `CROSSREF_EMAIL` |
| Unpaywall | OA locations by DOI, not keyword discovery | `UNPAYWALL_EMAIL` |
| Serper (optional) | Google Scholar, web, patents and other public information across 13 API types | `SERPER_API_KEY`; requests consume credits; [configuration and routes](references/serper.md) |
| Exa (optional) | Natural-language publication candidate discovery | `EXA_API_KEY`; [configuration, parameters and cost records](references/exa.md) |
| Zotero | Existing items and attachments | Host tools or a local standard JSON export |

Configure the API credentials above as needed. Other scholarly tools can supplement coverage after checking their response formats and access terms.

## Outputs and limits

Each run retains the query, per-source status, normalized records, redacted raw responses, readable report and integrity hashes. Empty results, failure, truncation and unexecuted work remain distinct. Stable identifiers drive matching; metadata conflicts and provenance are retained. Resolve authors before retrieving publications; a name filter on seed-paper authors must match uniquely before collecting works. Preserve preprint/publication relationships. S2 throttling is shared across processes using the same key and accepts seconds or HTTP dates in `Retry-After`. Publisher-elided references retain a restricted status. See the [CLI contract](references/cli.md) for year filtering and common metadata fields.

Records distinguish returned metadata from available abstract text; a screening basis is recorded only after reading. A resolved URL is not a downloaded file, and a downloaded file has not necessarily been read. `scripts/fetch_pdf.py` can retrieve a direct public PDF and record URLs, SHA-256, format checks and identity-check status. For SI, inspect the publisher's attachment list and record which files were obtained and which remain missing.

Bounded retrieval does not establish exhaustive coverage. Broader searches need a [protocol](references/protocol.md) and screening evidence. No login/paywall bypass or direct Zotero database access. Keep private papers, notes, annotations, credentials and test runs outside public commits.

Optionally hand acquired materials to [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-doc2md/README.en.md) for conversion and [sanmu-paper-reader](https://github.com/luffysolution-svg/sanmu-skills/blob/main/skills/sanmu-paper-reader/README.en.md) for interpretation. Neither is required for delivering a shortlist and acquisition record.

## Validation and references

After installation, run `python scripts/paper_search.py doctor`, try a small query from the examples above, and use `check` to verify the saved output.

[Development tests](https://github.com/luffysolution-svg/sanmu-skills/tree/main/tests/sanmu-paper-search) cover normalization, identity merging, failure states, protected outputs and integrity checks. From the repository root, run:

```bash
python -m unittest discover -s tests/sanmu-paper-search -v
```

Design references: [K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup), [research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search), [OmniScholar](https://github.com/luffysolution-svg/omnischolar), [paper-search-pro](https://github.com/O0000-code/paper-search-pro), and [easyseek](https://github.com/Rimagination/easyseek). Core code is independently implemented without copying their source. More: [retrieval strategy](references/retrieval.md), [acquisition and handoff](references/handoff.md).

Covered by the [MIT license](LICENSE). Database content, papers and third-party services retain their own terms.
