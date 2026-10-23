#!/usr/bin/env python3
"""Bounded scholarly metadata CLI (Python 3.10+, standard library only).

Examples:
  paper_search.py search "photothermal hydrogen" --out /path/to/work/runs/search-01
  paper_search.py lookup 10.1002/adma.202313513 --out /path/to/work/runs/lookup-01
  paper_search.py author "Jane Doe" --provider openalex --out /path/to/work/runs/authors-01
  paper_search.py author --author-id A123 --provider openalex --out /path/to/work/runs/author-papers-01
  paper_search.py related doi:10.1002/adma.202313513 --relation citations --out /path/to/work/runs/citations-01
  paper_search.py oa /path/to/work/runs/lookup-01 --out /path/to/work/runs/oa-01
  paper_search.py merge /path/to/work/runs/search-01 /path/to/work/runs/lookup-01 --zotero host-export.json --out /path/to/work/runs/merged-01
  paper_search.py check /path/to/work/runs/merged-01 --include-metadata
  paper_search.py diff /path/to/work/runs/search-01 /path/to/work/runs/merged-01 --out /path/to/work/runs/diff-01

Exit codes: 0 success, 1 partial, 2 invalid input, 3 no usable source, 4 failed integrity check.
All stdout is JSON; credentials are read exclusively from environment variables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from _core import (InputError, PROVIDERS, SCHEMA, Redactor, check_bundle, deduplicate, dumps,
                   load_papers, prepare_out, unique, write_bundle, zotero_records)
from _providers import Client, Providers, SourceError, parse_id
from _serper import MODES, execute as execute_serper, page_budget, positive_page


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def bounded(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("limit must be between 1 and 20")
    return number


def sources(value):
    result = list(dict.fromkeys(value.split(",")))
    if not result or any(s not in PROVIDERS for s in result):
        raise argparse.ArgumentTypeError("sources must be comma-separated openalex,semantic-scholar,crossref")
    return result


def parser():
    p = Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Show configuration presence as booleans, never credential values")
    cmd = commands.add_parser("serper", help="Official Serper routes; untrusted typed results, no paper normalization")
    cmd.add_argument("query", nargs="?")
    cmd.add_argument("--type", choices=MODES, default="search")
    cmd.add_argument("--url", help="Target URL for lens/webpage (sent as data to the fixed Serper endpoint)")
    cmd.add_argument("--limit", type=bounded, default=10, help="Local per-query total cap (1..20); does not set num")
    cmd.add_argument("--page", type=positive_page)
    cmd.add_argument("--pages", type=page_budget, default=1, help="Maximum sequential pages (1..3), stopping at local cap")
    cmd.add_argument("--params", help="Strict JSON object of allowed route parameters; scholar suppresses num")
    cmd.add_argument("--year-from", type=int, help="Scholar only; sent as as_ylo")
    cmd.add_argument("--year-to", type=int, help="Scholar only; sent as as_yhi")
    cmd.add_argument("--batch", help="Explicit JSON file: 1..100 request objects, one POST to the selected endpoint")
    cmd.add_argument("--out", required=True)
    cmd = commands.add_parser("exa", help="Optional publication discovery with Exa; preserves results and reported costs")
    cmd.add_argument("query")
    cmd.add_argument("--type", choices=("auto", "lite"), default="auto")
    cmd.add_argument("--limit", type=bounded, default=5)
    cmd.add_argument("--year-from", type=int)
    cmd.add_argument("--year-to", type=int)
    cmd.add_argument("--include-domain", action="append", default=[])
    cmd.add_argument("--exclude-domain", action="append", default=[])
    cmd.add_argument("--text-chars", type=int, default=3000)
    cmd.add_argument("--out", required=True)
    cmd = commands.add_parser("epmc", help="Europe PMC search, preprint discovery, and JATS XML / SI bundle fetching")
    cmd.add_argument("query", nargs="?", help="Search query, or DOI/PMID/PMCID for lookup/fetch")
    cmd.add_argument("--id", help="Explicit DOI, PMID, or PMCID (e.g. PMC12710600 or 10.1002/adma.202313513)")
    cmd.add_argument("--limit", type=bounded, default=10, help="Local per-query result cap (1..20)")
    cmd.add_argument("--year-from", type=int)
    cmd.add_argument("--year-to", type=int)
    cmd.add_argument("--sort", choices=("relevance", "cited", "date"), default="relevance")
    cmd.add_argument("--has-suppl", action="store_true", help="Only papers with supplementary files (HAS_SUPPL:y)")
    cmd.add_argument("--open-access", action="store_true", help="Only open access papers (OPEN_ACCESS:y)")
    cmd.add_argument("--has-data", action="store_true", help="Only papers with associated data (HAS_DATA:y)")
    cmd.add_argument("--preprints", action="store_true", help="Only preprints (SRC:PPR)")
    cmd.add_argument("--has-fulltext", action="store_true", help="Only papers with full text in Europe PMC (HAS_FT:y)")
    cmd.add_argument("--fetch", choices=("none", "all", "xml", "suppl"), default="none",
                     help="Fetch JATS XML and/or supplementary ZIP files into output")
    cmd.add_argument("--out", required=True)
    for name in ("search", "lookup", "related"):
        cmd = commands.add_parser(name)
        cmd.add_argument("query" if name == "search" else "id")
        cmd.add_argument("--sources", type=sources, default=list(PROVIDERS) if name != "related" else list(PROVIDERS[:2]))
        cmd.add_argument("--limit", type=bounded, default=10, help="Per-source candidate budget (1..20); keep all deduplicated results")
        cmd.add_argument("--out", required=True)
        if name == "search":
            cmd.add_argument("--year-from", type=int)
            cmd.add_argument("--year-to", type=int)
        if name == "lookup":
            cmd.add_argument("--title", help="Optional exact normalized title validation; mismatches retained as errors")
        if name == "related":
            cmd.add_argument("--relation", choices=("references", "citations", "recommendations"), default="references")
    cmd = commands.add_parser("author")
    cmd.add_argument("query", nargs="?")
    cmd.add_argument("--provider", choices=PROVIDERS[:2], required=True)
    cmd.add_argument("--author-id")
    cmd.add_argument("--from-paper", help="Seed DOI/stable ID; resolve author IDs from its author list")
    cmd.add_argument("--year-from", type=int, help="Paper years; requires --author-id or --from-paper")
    cmd.add_argument("--year-to", type=int, help="Paper years; requires --author-id or --from-paper")
    cmd.add_argument("--limit", type=bounded, default=10, help="Per-source candidate budget (1..20); keep all deduplicated results")
    cmd.add_argument("--out", required=True)
    cmd = commands.add_parser("oa")
    cmd.add_argument("input", help="Normalized run/papers.json or DOI/stable paper ID")
    cmd.add_argument("--sources", type=sources, default=list(PROVIDERS[:2]))
    cmd.add_argument("--limit", type=bounded, default=10, help="Per-source candidate budget (1..20); keep all deduplicated results")
    cmd.add_argument("--unpaywall", choices=("auto", "never", "required"), default="auto")
    cmd.add_argument("--out", required=True)
    cmd = commands.add_parser("merge")
    cmd.add_argument("inputs", nargs="+")
    cmd.add_argument("--zotero", help="Read a JSON export produced by the Zotero host; whitelisted fields only")
    cmd.add_argument("--out", required=True)
    cmd = commands.add_parser("check")
    cmd.add_argument("run")
    cmd.add_argument("--include-metadata", action="store_true", help="Include relative paths, sizes and hashes")
    cmd = commands.add_parser("diff")
    cmd.add_argument("before")
    cmd.add_argument("after")
    cmd.add_argument("--out", required=True)
    return p


def _error(exc, fetched=0):
    result = {"status": "error", "error": str(exc), "http_status": getattr(exc, "status", None),
              "fetched": fetched, "truncated": True}
    if getattr(exc, "kind", None):
        result["reason"] = exc.kind
    return result


def collect(adapter, names, method, *args):
    papers, statuses = [], {}
    for source in names:
        try:
            found, status = getattr(adapter, method)(source, *args)
            papers.extend(found)
            statuses[source] = status
        except SourceError as exc:
            partial = getattr(exc, "papers", [])
            papers.extend(partial)
            statuses[source] = _error(exc, len(partial))
            if getattr(exc, "pages", None) is not None:
                statuses[source]["pages"] = exc.pages
            if exc.status == 404 and method == "lookup":
                statuses[source].update(status="not_found", truncated=False)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            statuses[source] = _error(SourceError("Malformed source response: " + str(exc)))
    return papers, statuses


def diff_papers(before, after):
    def stable(p):
        return {(key, v.casefold()) for key, values in p["identifiers"].items() for v in values}
    added, removed, changed, consumed = [], [], [], set()
    for old in before:
        hits = [i for i, new in enumerate(after) if stable(old) & stable(new)]
        if not stable(old):
            hits = [i for i, new in enumerate(after) if (old.get("title"), old.get("year"), old.get("authors")) ==
                    (new.get("title"), new.get("year"), new.get("authors"))]
        if not hits:
            removed.append(old)
            continue
        consumed.update(hits)
        new = after[hits[0]]
        fields = [key for key in sorted(set(old) | set(new)) if key not in ("provenance", "versions") and old.get(key) != new.get(key)]
        if fields:
            changed.append({"identifiers": new["identifiers"], "fields": fields,
                            "before": {k: old.get(k) for k in fields}, "after": {k: new.get(k) for k in fields}})
    added.extend(p for i, p in enumerate(after) if i not in consumed)
    return {"added": added, "removed": removed, "changed": changed,
            "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)}}


def execute(args, client):
    redactor, adapter = client.redactor, Providers(client)
    if args.command == "doctor":
        env = client.env
        return {"schema": SCHEMA, "status": "success", "python_supported": sys.version_info >= (3, 10),
                "configuration": {"semantic_scholar_api_key": bool(env.get("S2_API_KEY") or env.get("SEMANTIC_SCHOLAR_API_KEY")),
                                  "openalex_api_key": bool(env.get("OPENALEX_API_KEY")),
                                  "serper_api_key": bool(env.get("SERPER_API_KEY")),
                                  "exa_api_key": bool(env.get("EXA_API_KEY", "").strip()),
                                  "openalex_email": bool(env.get("OPENALEX_EMAIL")),
                                  "unpaywall_email": bool(env.get("UNPAYWALL_EMAIL")),
                                  "crossref_email": bool(env.get("CROSSREF_EMAIL")),
                                  "polite_email": bool(env.get("POLITE_EMAIL")),
                                  "europepmc_public_access": True}}, 0
    if args.command == "check":
        result = check_bundle(args.run, args.include_metadata)
        return result, 4 if result["status"] == "failed" else 0
    if args.command == "serper":
        return execute_serper(args, client)
    if args.command == "exa":
        from _exa import execute as execute_exa
        return execute_exa(args, client)
    if args.command == "epmc":
        from _epmc import execute as execute_epmc
        return execute_epmc(args, client)
    # Input errors are diagnosed before reserving a directory or issuing a request.
    command = args.command
    year_from, year_to = getattr(args, "year_from", None), getattr(args, "year_to", None)
    year_filter = year_from is not None or year_to is not None
    if any(year is not None and not 1000 <= year <= 9999 for year in (year_from, year_to)):
        raise InputError("Years must be between 1000 and 9999")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise InputError("--year-from must not exceed --year-to")
    if command == "author" and year_filter and not (args.author_id or args.from_paper):
        raise InputError("Year filters require --author-id or --from-paper")
    if command in ("lookup", "related"):
        parse_id(args.id)
    if command == "author" and args.from_paper:
        parse_id(args.from_paper)
    if command == "search" and not args.query.strip():
        raise InputError("Search query must not be empty")
    if command == "author" and args.from_paper and args.author_id:
        raise InputError("Specify only one of --author-id or --from-paper; the query acts as a name filter")
    if command == "author" and not args.from_paper and bool(args.query) == bool(args.author_id):
        raise InputError("Specify either an author search query or --author-id")
    if command == "author" and not (args.query or args.author_id or args.from_paper):
        raise InputError("Specify an author search query, --author-id, or --from-paper")
    if command == "author" and args.author_id:
        author_id = args.author_id.rstrip("/").rsplit("/", 1)[-1]
        pattern = r"A\d+" if args.provider == "openalex" else r"\d+"
        if not re.fullmatch(pattern, author_id, re.I):
            raise InputError("Author ID does not match the selected provider")
    loaded = None
    if command == "merge":
        loaded = [load_papers(path) for path in args.inputs]
        local = zotero_records(args.zotero) if args.zotero else []
    if command == "diff":
        loaded = (load_papers(args.before), load_papers(args.after))
    if command == "oa":
        if Path(args.input).exists():
            loaded = load_papers(args.input)
        else:
            parse_id(args.input)
    out = prepare_out(args.out)
    papers, statuses, extra = [], {}, {}
    if command in ("search", "lookup", "related"):
        params = ([args.query, args.limit, year_from, year_to] if command == "search" else [args.id] if command == "lookup"
                  else [args.id, args.relation, args.limit])
        papers, statuses = collect(adapter, args.sources, command, *params)
        query = {"text": args.query} if command == "search" else {"id": args.id}
        query.update(limit=args.limit, sources=args.sources)
        if command == "search" and "crossref" in args.sources:
            query["source_query_strategy"] = {"crossref": "title_only"}
            statuses["crossref"]["query_strategy"] = "title_only"
        if command == "related":
            query["relation"] = args.relation
            for paper in papers:
                direction = {"references": "seed_to_result", "citations": "result_to_seed",
                             "recommendations": "similarity_not_citation"}[args.relation]
                paper["relations"].append({"type": args.relation, "seed": args.id, "direction": direction})
        if command == "lookup" and args.title:
            title_key = lambda value: re.sub(r"[^\w]", "", value.casefold())
            query["expected_title"] = args.title
            for paper in papers:
                if title_key(paper["title"]) != title_key(args.title):
                    for provenance in paper["provenance"]:
                        statuses[provenance["provider"]].update(status="error", error="Exact DOI matched but expected title differs",
                                                              title_verified=False)
                else:
                    for provenance in paper["provenance"]:
                        statuses[provenance["provider"]]["title_verified"] = True
    elif command == "author":
        query = {"text": args.query, "author_id": args.author_id, "provider": args.provider, "limit": args.limit}
        extra["author_candidates"] = []
        extra["author_mode"] = "papers" if args.author_id else "candidates"
        if args.from_paper:
            # Resolve stable author IDs from the seed paper's author list instead of name search,
            # which avoids homonym collisions in common-name or transliterated-name cases.
            seeds = []
            for provider in dict.fromkeys([args.provider] + list(PROVIDERS[:2])):
                try:
                    found, seed_status = adapter.lookup(provider, args.from_paper)
                    seeds.extend(found)
                    statuses["paper:" + provider] = seed_status
                    if found:
                        statuses["paper:" + provider]["author_mode"] = "paper_authors"
                        selected_namespace = "semantic_scholar" if args.provider == "semantic-scholar" else args.provider
                        if any(a.get("identifiers", {}).get(selected_namespace)
                               for paper in found for a in paper.get("authors", [])):
                            break
                except SourceError as exc:
                    statuses["paper:" + provider] = _error(exc)
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    statuses["paper:" + provider] = _error(SourceError("Malformed seed response: " + str(exc)))
            seed_authors = [author for paper in deduplicate(seeds) for author in paper.get("authors", [])]
            wanted = (args.query or "").strip().casefold()
            matched = ([a for a in seed_authors if a.get("name", "").strip().casefold() == wanted]
                       or [a for a in seed_authors if wanted and wanted in a.get("name", "").casefold()])
            # Identifier namespaces use underscores (semantic_scholar) while providers use hyphens.
            namespace = {"openalex": "openalex", "semantic-scholar": "semantic_scholar"}

            def as_candidates(authors):
                return [{"provider": provider, "author_id": value, "name": author.get("name"),
                         "identifiers": author.get("identifiers", {}),
                         "affiliations": author.get("affiliations", []),
                         "from_paper": args.from_paper}
                        for author in authors
                        for provider, key in namespace.items()
                        for value in (author.get("identifiers") or {}).get(key, [])]
            extra["author_candidates"] = as_candidates(matched or seed_authors)
            extra["author_mode"] = "paper_authors"
            ids_for = unique([c["author_id"] for c in as_candidates(matched) if c["provider"] == args.provider])
            query["from_paper"] = args.from_paper
            if not seeds:
                statuses[args.provider] = _error(SourceError("Seed paper could not be resolved; author identity is unknown"))
                extra["author_mode"] = "unresolved"
            elif wanted and not matched:
                statuses[args.provider] = {"status": "not_found", "fetched": 0, "pages": 1, "truncated": False,
                                           "error": "Author name is not in the seed paper author list",
                                           "author_mode": "no_match", "from_paper": args.from_paper}
            elif not wanted or len(ids_for) > 1:
                mode = "ambiguous" if wanted else "paper_authors"
                extra["author_mode"] = mode
                statuses[args.provider] = {"status": "ok", "fetched": 0, "pages": 1, "truncated": False,
                                           "author_mode": mode, "from_paper": args.from_paper}
            elif len(ids_for) == 1:
                extra["author_mode"] = "papers"
                query["author_id"] = args.author_id = ids_for[0]
            else:
                statuses[args.provider] = _error(SourceError("Seed authors have no ID for the selected provider"))
                extra["author_mode"] = "unresolved"
        if args.from_paper and not args.author_id:
            pass  # Seed paper authored list or name mismatch; nothing further to fetch.
        else:
            try:
                papers, status, candidates = adapter.authors(args.provider, args.query, args.author_id, args.limit, year_from, year_to)
                statuses[args.provider] = status
                if not args.from_paper:
                    extra["author_candidates"] = candidates
                elif not candidates:
                    status["author_mode"] = "papers"
                    status["from_paper"] = args.from_paper
            except SourceError as exc:
                papers = getattr(exc, "papers", [])
                statuses[args.provider] = _error(exc, len(papers))
                if getattr(exc, "pages", None) is not None:
                    statuses[args.provider]["pages"] = exc.pages
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                statuses[args.provider] = _error(SourceError("Malformed source response: " + str(exc)))
    elif command == "oa":
        query = {"input": Path(args.input).name if loaded is not None else args.input,
                 "limit": args.limit, "unpaywall": args.unpaywall}
        extra.update(downloaded=False, action="resolve_only")
        if loaded is not None:
            papers = loaded[:args.limit]
            statuses["input"] = {"status": "ok", "fetched": len(papers), "total": len(loaded), "truncated": len(loaded) > len(papers)}
        else:
            papers, statuses = collect(adapter, args.sources, "lookup", args.input)
        if args.unpaywall != "never":
            if client.env.get("UNPAYWALL_EMAIL"):
                for paper in papers:
                    for identifier in paper["identifiers"].get("doi", []):
                        name = "unpaywall:" + identifier
                        try:
                            locations = adapter.unpaywall(identifier)
                            paper["oa_locations"] = unique(paper.get("oa_locations", []) + locations)
                            statuses[name] = {"status": "ok", "fetched": len(locations), "truncated": False}
                        except SourceError as exc:
                            statuses[name] = _error(exc)
                        except (ValueError, TypeError, KeyError, AttributeError) as exc:
                            statuses[name] = _error(SourceError("Malformed Unpaywall response: " + str(exc)))
            elif args.unpaywall == "required":
                statuses["unpaywall"] = _error(SourceError("UNPAYWALL_EMAIL is not configured"))
            else:
                statuses["unpaywall"] = {"status": "skipped", "reason": "UNPAYWALL_EMAIL is not configured"}
    elif command == "merge":
        papers = deduplicate([p for group in loaded for p in group])
        # Local exports annotate matches only; unrelated private records do not enter the result.
        for local_paper in local:
            for index, paper in enumerate(papers):
                combined = deduplicate([paper, local_paper])
                if len(combined) == 1:
                    papers[index]["local_matches"] = unique(paper.get("local_matches", []) + local_paper["local_matches"])
        query = {"inputs": [Path(path).name for path in args.inputs], "zotero_export": bool(args.zotero)}
        statuses["input"] = {"status": "ok", "fetched": sum(map(len, loaded)), "truncated": False}
        extra["local_match_count"] = sum(bool(p.get("local_matches")) for p in papers)
    elif command == "diff":
        before, papers = loaded
        query = {"before": Path(args.before).name, "after": Path(args.after).name}
        extra["diff"] = diff_papers(before, papers)
        statuses["input"] = {"status": "ok", "fetched": len(papers), "truncated": False}
    input_count = len(papers)
    if year_filter:
        query.update(year_from=year_from, year_to=year_to)
        unknown = sum(p.get("year") is None for p in papers)
        filtered = [p for p in papers if p.get("year") is None or (
            (year_from is None or p["year"] >= year_from) and (year_to is None or p["year"] <= year_to))]
        extra["year_filter"] = {"year_from": year_from, "year_to": year_to,
            "unknown_year_count": unknown, "locally_removed": len(papers) - len(filtered),
            "unknown_year_policy": "keep_returned_unknown; provider-side filters may omit undated records",
            "mode": "provider_and_local"}
        papers = filtered
    papers = deduplicate(papers)
    limit = getattr(args, "limit", None)
    extra["result_truncated"] = any(s.get("truncated") is True for s in statuses.values())
    extra["pre_dedup_count"] = input_count
    extra["post_dedup_count"] = len(papers)
    extra["global_dropped_count"] = 0
    extra["limit_scope"] = "per_source_candidate_budget" if limit else "all_input_records"
    extra["ordering"] = "source_order_then_provider_order; no_cross_provider_reranking"
    result = write_bundle(out, command, query, papers, redactor.clean(statuses), client.raw, redactor, extra)
    return result, {"success": 0, "partial": 1, "failed": 3}[result["status"]]


def main(argv=None, client=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    client = client or Client()
    try:
        args = parser().parse_args(argv)
        result, code = execute(args, client)
    except InputError as exc:
        result, code = {"schema": SCHEMA, "status": "failed", "error": {"kind": "input", "message": str(exc)}}, 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile) as exc:
        result, code = {"schema": SCHEMA, "status": "failed", "error": {"kind": "runtime", "message": str(exc)}}, 3
    print(dumps(client.redactor.clean(result)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
