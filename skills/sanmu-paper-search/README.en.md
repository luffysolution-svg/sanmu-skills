# sanmu-paper-search

**Find traceable, readable papers from a topic, seed paper, or author.**

[简体中文](README.md) · **English** · [Collection](../../README.en.md)

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
   python -m unittest discover -s tests -v
3. Check only whether OPENALEX_API_KEY, SEMANTIC_SCHOLAR_API_KEY (or S2_API_KEY), UNPAYWALL_EMAIL and CROSSREF_EMAIL are configured. Never display values. Explain available capabilities without credentials; point to official registration only when needed.
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

On Windows, quote actual absolute paths. After checking candidates, use `author --author-id ID --provider ...` for publications. Topic search and author publications support `--year-from` / `--year-to`. `--limit` is a per-source budget; all deduplicated candidates are retained in source order, without cross-provider relevance reranking. The assistant reads and screens the shortlist. See the [CLI contract](references/cli.md).

## Sources

| Source | Purpose | Configuration |
| --- | --- | --- |
| OpenAlex | Topics, authors, citations and OA locations | `OPENALEX_API_KEY`; anonymous capabilities depend on current limits |
| Semantic Scholar | Topics, authors, graph relations and recommendations | `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY`; the anonymous pool may throttle |
| Crossref | DOI/metadata verification and supplementary discovery | Optional `CROSSREF_EMAIL` |
| Unpaywall | OA locations by DOI, not keyword discovery | `UNPAYWALL_EMAIL` |
| Zotero | Existing items and attachments | Host tools or a local standard JSON export |

No bundled credentials or mandatory dependency on OmniScholar, easyseek or ScanSci PDF. Optional backends require explicit capability, source-policy, licensing and output checks.

## Outputs and limits

Each run retains the query, per-source status, normalized records, redacted raw responses, readable report and integrity hashes. Empty results, failure, truncation and unexecuted work remain distinct. Stable identifiers drive matching; metadata conflicts and provenance are retained. Resolve authors before retrieving publications, and preserve preprint/publication relationships.

Records distinguish returned metadata from available abstract text; a screening basis is recorded only after reading. A resolved URL is not a downloaded file, and a downloaded file has not necessarily been read. `scripts/fetch_pdf.py` can retrieve a direct public PDF and record URLs, SHA-256, format checks and identity-check status. SI still requires inspecting the publisher's actual attachment list; automatic complete SI acquisition is outside this release.

Bounded retrieval does not establish exhaustive coverage. Broader searches need a [protocol](references/protocol.md) and screening evidence. No login/paywall bypass or direct Zotero database access. Keep private papers, notes, annotations, credentials and test runs outside public commits.

Optionally hand acquired materials to [sanmu-doc2md](../sanmu-doc2md/README.en.md) for conversion and [sanmu-paper-reader](../sanmu-paper-reader/README.en.md) for interpretation. Neither is required for delivering a shortlist and acquisition record.

## Validation and references

```bash
python -m unittest discover -s tests -v
```

Offline tests use mock responses to cover normalization, identity merging, failure states, protected outputs and integrity checks. Live availability requires a separate small test. Real Zotero test data stays in the local workspace.

Design references: [K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup), [research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search), [OmniScholar](https://github.com/luffysolution-svg/omnischolar), [paper-search-pro](https://github.com/O0000-code/paper-search-pro), and [easyseek](https://github.com/Rimagination/easyseek). Core code is independently implemented without copying their source. More: [retrieval strategy](references/retrieval.md), [acquisition and handoff](references/handoff.md).

Covered by the collection's [MIT license](../../LICENSE). Database content, papers and third-party services retain their own terms.
