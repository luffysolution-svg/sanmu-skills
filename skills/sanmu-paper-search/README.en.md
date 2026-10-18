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
3. Check only whether OPENALEX_API_KEY, SEMANTIC_SCHOLAR_API_KEY (or S2_API_KEY), UNPAYWALL_EMAIL, CROSSREF_EMAIL and POLITE_EMAIL are configured. Never display values. Explain available capabilities without credentials; point to official registration only when needed.
4. For Zotero, start the desktop app, enable local application communication, and run python scripts/zotero_local.py doctor. The independent client needs no host MCP or cloud API key; do not read the database or storage directly. Before the first authorized write, run doctor --apply and let me approve Zotero's local authorization dialog. Core search also works without Zotero.
5. Use acquire.py when full text is needed. It prioritizes open-access direct links; institutional access supports --institutional-proxy to transform publisher URLs into campus WebVPN/EZProxy endpoints, using the assistant's browser tool with an authorized session, then recording the downloaded file with acquire --import-file.
6. Run a small public-paper query into a new directory outside the repository. Report per-source status, installation path and limitations; installation checks must not modify Zotero items. The local Zotero app manages attachments and WebDAV synchronization; do not claim sync completion without checking its status.
```

Alternatively copy the complete directory into `.agents/skills/sanmu-paper-search`. The Python 3.10+ standard-library core needs no pip installation. PyMuPDF is optional for PDF text-based identity checks.

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
# Optional sources require explicit selection; the original three remain the default
python scripts/paper_search.py search 'all:"crystal diffusion"' --sources arxiv --limit 2 --out /path/to/work/arxiv
python scripts/paper_search.py lookup pmid:31452104 --sources europe-pmc --out /path/to/work/europe-pmc
```

Local Zotero, acquisition and ingestion (run from the skill directory):

```bash
python scripts/zotero_local.py doctor
# First local write authorization: approve the dialog in Zotero
python scripts/zotero_local.py doctor --apply
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition
# If institutional access is needed, first download through the assistant's authorized browser session
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition --backend none --import-file /path/to/work/browser-paper.pdf
python scripts/ingest.py /path/to/work/acquisition/manifest.json
# Verify identity and SI relationships, review the preview, then apply within the authorized scope
python scripts/ingest.py /path/to/work/acquisition/manifest.json --apply
```

`ingest` checks file SHA-256 and identity before deduplicating by stable ID and importing. It previews by default and does not automatically rewrite existing metadata. If text-based identity remains unverified or SI is present, complete the relevant review before using `--identity-confirmed` / `--si-confirmed`; see the [CLI guide](references/cli.md) and [local Zotero guide](references/local-zotero.md). First-time local authorization needs no cloud API key. Successful attachment import establishes local completion only; the local Zotero app handles WebDAV sync, whose completion must be checked separately.

On Windows, quote actual absolute paths. After checking candidates, use `author --author-id ID --provider ...` for publications. Topic search and author publications support `--year-from` / `--year-to`. `--limit` is a per-source budget; all deduplicated candidates are retained in source order, without cross-provider relevance reranking. The assistant reads and screens the shortlist. See the [CLI contract](references/cli.md).

## Sources

| Source | Purpose | Configuration |
| --- | --- | --- |
| OpenAlex | Topics, authors, citations and OA locations | `OPENALEX_API_KEY`; anonymous capabilities depend on current limits |
| Semantic Scholar | Topics, authors, graph relations and recommendations | `SEMANTIC_SCHOLAR_API_KEY` or `S2_API_KEY`; the anonymous pool may throttle |
| Crossref | DOI/metadata verification and supplementary discovery | Optional `CROSSREF_EMAIL` |
| arXiv | Direct preprint search and lookup by arXiv ID | Explicit `--sources arxiv`; no key |
| Europe PMC | Direct search and lookup by DOI/PMID/PMCID | Explicit `--sources europe-pmc`; no key |
| Unpaywall | OA locations by DOI, not keyword discovery | `UNPAYWALL_EMAIL` or an available contact-email fallback, including `POLITE_EMAIL` |
| Zotero | Items, attachments and deduplicated writes | Independent `zotero_local.py`, with no host MCP or cloud API key; host tools and standard JSON exports remain usable |

`search` / `lookup` retain the original three-source default; optional sources add no default requests. `related` supports only OpenAlex and Semantic Scholar; other sources explicitly report `unsupported`. arXiv preprints retain relationships to published DOIs. Free-to-read access does not establish an open license.

Core search works independently with no bundled credentials. Acquisition uses standard-library open access and direct links by default, without external third-party download packages. Institutional access supports `--institutional-proxy` to generate campus WebVPN / EZProxy access URLs, and uses the assistant's browser tool with an authorized session; import the downloaded file with `acquire.py --import-file`. Full-text availability is not guaranteed.

## Outputs and limits

Each run retains the query, per-source status, normalized records, redacted raw responses, readable report and integrity hashes. Empty results, failure, truncation and unexecuted work remain distinct. Stable identifiers drive matching; metadata conflicts and provenance are retained. Resolve authors before retrieving publications, and preserve preprint/publication relationships.

Records distinguish returned metadata from available abstract text; a screening basis is recorded only after reading. A resolved URL is not a downloaded file, and a downloaded file has not necessarily been read. `scripts/fetch_pdf.py` still retrieves direct public PDFs. `acquire.py` creates a `manifest.json` separate from search records, tracking main text and SI individually with sources, hashes, identity, attempts and acquisition status. SI links extracted from publisher HTML are candidates only: review them and select explicitly with `--select-si`. A candidate list neither establishes acquisition nor proves complete SI coverage.

Bounded retrieval does not establish exhaustive coverage. Broader searches need a [protocol](references/protocol.md) and screening evidence. No login/paywall bypass or direct Zotero database access. Keep private papers, notes, annotations, credentials and test runs outside public commits.

Optionally hand acquired materials to [sanmu-doc2md](../sanmu-doc2md/README.en.md) for conversion and [sanmu-paper-reader](../sanmu-paper-reader/README.en.md) for interpretation. Neither is required for delivering a shortlist and acquisition record.

## Validation and references

```bash
python -m unittest discover -s tests -v
```

Offline tests use mock responses to cover normalization, identity merging, failure states, protected outputs and integrity checks. Live availability requires a separate small test. Real Zotero test data stays in the local workspace.

Design references: [K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup), [research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search), [OmniScholar](https://github.com/luffysolution-svg/omnischolar), [paper-search-pro](https://github.com/O0000-code/paper-search-pro), and [easyseek](https://github.com/Rimagination/easyseek). Retrieval and acquisition cores are independently implemented. More: [retrieval strategy](references/retrieval.md), [acquisition and handoff](references/handoff.md), [local Zotero](references/local-zotero.md).

Covered by the collection's [MIT license](../../LICENSE). Database content, papers, third-party code and services retain their own terms.
