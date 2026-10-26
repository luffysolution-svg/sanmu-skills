"""Portable paper records, conservative identity merging and verifiable run bundles."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit, parse_qsl, urlencode, quote

SCHEMA = "sanmu-paper-search/v1"
PROVIDERS = ("openalex", "semantic-scholar", "crossref")
SECRET_NAMES = ("S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY",
                "UNPAYWALL_EMAIL", "CROSSREF_EMAIL", "SERPER_API_KEY", "EXA_API_KEY", "OPENALEX_EMAIL", "POLITE_EMAIL")
# Canonicalized names are shared by structured fields and parsed URL queries.
SENSITIVE = {"apikey", "authorization", "token", "accesstoken", "email", "mailto",
             "password", "secret", "xapikey", "key", "signature", "credential",
             "xamzsignature", "xamzcredential", "xamzsecuritytoken"}


def _sensitive_key(value, query=False):
    normalized = re.sub(r"[^a-z]", "", str(value).casefold())
    secret_names = {re.sub(r"[^a-z]", "", name.casefold()) for name in SECRET_NAMES}
    return normalized in secret_names or (normalized in SENSITIVE and (normalized != "key" or query))


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


class InputError(ValueError):
    pass


class Redactor:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        values = {variant for name in SECRET_NAMES for variant in
                  (env.get(name, ""), env.get(name, "").strip())} - {""}
        self.values = sorted({variant for value in values for variant in
                              (value, quote(value, safe=""), urlencode({"v": value})[2:])},
                             key=len, reverse=True)

    def text(self, text):
        def redact_query(match):
            value = match.group(0)
            # Also accept query fragments embedded in error messages.
            leading_amp = value.startswith("&")
            try:
                parts = urlsplit("?" + value[1:] if leading_amp else value)
                pairs = parse_qsl(parts.query, keep_blank_values=True)
                if not any(_sensitive_key(key, query=True) for key, _ in pairs):
                    return value
                query = urlencode([(key, "[REDACTED]" if _sensitive_key(key, query=True) else val)
                                   for key, val in pairs])
                cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
                return "&" + cleaned[1:] if leading_amp else cleaned
            except ValueError:
                return value

        def redact_field(match):
            try:
                key = json.loads(match[1].rsplit(":", 1)[0].strip())
            except (ValueError, TypeError):
                return match[0]
            return match[1] + '"[REDACTED]"' if _sensitive_key(key) else match[0]

        # Raw JSON and malformed error bodies may echo credentials absent from env.
        text = re.sub(r'("(?:[^"\\]|\\.)*"\s*:\s*)("(?:[^"\\]|\\.)*")', redact_field, text)
        # Regex finds URL boundaries only; names/values are decoded by the query parser.
        text = re.sub(r"(?:https?://|[?&])[^\s\"'<>\\]*", redact_query, text, flags=re.I)
        for value in self.values:
            text = text.replace(value, "[REDACTED]")
        return text

    def clean(self, obj):
        if isinstance(obj, dict):
            return {self.text(str(k)): ("[REDACTED]" if
                    _sensitive_key(k) and not isinstance(v, bool) else self.clean(v))
                    for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.clean(v) for v in obj]
        return self.text(obj) if isinstance(obj, str) else obj


def doi(value):
    if not value:
        return None
    text = unquote(str(value)).strip()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I).lower()
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else None


def ids(**values):
    result = {}
    for key, value in values.items():
        if value is None or value == "":
            continue
        entries = value if isinstance(value, list) else [value]
        cleaned = []
        for entry in entries:
            if not entry:
                continue
            text = str(entry).strip()
            if key == "doi":
                text = doi(text)
            elif key == "openalex":
                text = text.rstrip("/").rsplit("/", 1)[-1].upper()
            elif key in ("pmid", "pmcid", "orcid"):
                text = text.rstrip("/").rsplit("/", 1)[-1]
            elif key == "arxiv":
                text = re.sub(r"^arxiv:\s*", "", text.rsplit("/abs/", 1)[-1], flags=re.I)
            if text and text not in cleaned:
                cleaned.append(text)
        if cleaned:
            result[key] = cleaned
    return result


def license_status(value):
    text = (value or "").lower()
    if not text:
        return "unknown"
    if "-nc" in text or "-nd" in text or "/by-nc" in text or "/by-nd" in text or "all-rights-reserved" in text:
        return "restricted"
    if "cc0" in text or "publicdomain" in text or "cc-by" in text or "creativecommons.org/licenses/by/" in text:
        return "open"
    return "unknown"


def location(url, provider, is_oa=None, license=None, evidence=None, version=None):
    return {"url": url, "provider": provider, "is_oa": is_oa if isinstance(is_oa, bool) else None,
            "license": license or None, "license_status": license_status(license),
            "evidence": evidence or "url_only", "version": version}


def record(provider, record_id, title, year, identifiers, authors, abstract=None,
           kind=None, oa_locations=None, relations=None):
    return {"title": title or "", "year": year, "type": kind,
            "identifiers": identifiers, "authors": authors or [], "abstract": abstract or None,
            "evidence_level": "abstract" if abstract else "metadata_only",
            "abstract_available": bool(abstract), "read": False,
            "provenance": [{"provider": provider, "record_id": str(record_id or ""), "retrieved_at": now()}],
            "versions": [], "relations": relations or [], "oa_locations": oa_locations or [], "conflicts": []}


def unique(values):
    seen, result = set(), []
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _stable(paper):
    return {(kind, str(value).casefold()) for kind, values in paper.get("identifiers", {}).items()
            for value in values if kind in {"doi", "openalex", "semantic_scholar", "pmid", "pmcid", "arxiv", "corpus_id"}}


def _fallback(paper):
    norm = lambda s: re.sub(r"[^\w]", "", s.casefold())
    authors = paper.get("authors") or []
    title = norm(paper.get("title") or "")
    if not title or not paper.get("year") or not authors:
        return None
    name = norm(authors[0].get("name") or "")
    return (title, str(paper["year"]), name) if name else None


def _arxiv_families(paper):
    return {re.sub(r"v\d+$", "", value.casefold())
            for value in paper.get("identifiers", {}).get("arxiv", [])}


def _version_conflicts(a, b):
    """Only bibliographic/arXiv evidence, never a provider API version field."""
    if not (_arxiv_families(a) & _arxiv_families(b)):
        return []
    reasons = []
    a_doi, b_doi = (set(p.get("identifiers", {}).get("doi", [])) for p in (a, b))
    if a_doi and b_doi and not a_doi & b_doi:
        reasons.append("different_doi")
    kinds = [set(re.split(r"[,;]", (p.get("type") or "").casefold())) for p in (a, b)]
    preprint = {"preprint", "posted-content"}
    published = {"article", "journal-article", "journalarticle", "conference-paper",
                 "proceedings-article", "conference", "conferencepaper", "review"}
    if ((kinds[0] & preprint and kinds[1] & published) or
            (kinds[1] & preprint and kinds[0] & published)):
        reasons.append("preprint_publication_type")
    for family in _arxiv_families(a) & _arxiv_families(b):
        versions = [{value.casefold() for value in p.get("identifiers", {}).get("arxiv", [])
                     if re.sub(r"v\d+$", "", value.casefold()) == family
                     and re.search(r"v\d+$", value, re.I)} for p in (a, b)]
        if versions[0] and versions[1] and not versions[0] & versions[1]:
            reasons.append("different_arxiv_version")
    return sorted(set(reasons))


def _groups_version_conflict(a, b):
    return any(_version_conflicts(left, right) for left in a for right in b)


def _merge_authors(group, base):
    """Fuse aligned, unique full names only; retain ambiguous lists in versions/conflicts."""
    result = json.loads(json.dumps(base))
    name = lambda author: " ".join((author.get("name") or "").casefold().split())
    names = [name(author) for author in result]
    if (not all(names) or len(set(names)) != len(names) or
            any(re.search(r"\b[^\W\d_]\b", value) for value in names)):
        return result
    for paper in group:
        authors = paper.get("authors") or []
        if [name(author) for author in authors] != names:
            continue
        for target, incoming in zip(result, authors):
            left, right = target.get("identifiers") or {}, incoming.get("identifiers") or {}
            if any({str(v).casefold() for v in left[key]}.isdisjoint(
                    str(v).casefold() for v in right[key]) for key in left.keys() & right.keys()
                    if left[key] and right[key]):
                continue
            target["identifiers"] = {key: unique(left.get(key, []) + right.get(key, []))
                                     for key in sorted(left.keys() | right.keys())}
            for field in ("affiliations", "raw_affiliation_strings"):
                values = unique(target.get(field, []) + incoming.get(field, []))
                if values or field in target or field in incoming:
                    target[field] = values
    return result


def _combine(group):
    result = json.loads(json.dumps(group[0]))
    for field in ("provenance", "relations", "oa_locations", "local_matches", "conflicts"):
        combined = unique([v for p in group for v in p.get(field, [])])
        if combined or field != "local_matches":
            result[field] = combined
    result["identifiers"] = {}
    for p in group:
        for key, values in p.get("identifiers", {}).items():
            result["identifiers"][key] = unique(result["identifiers"].get(key, []) + values)
    result["versions"] = unique([v for p in group for v in p.get("versions", [])] + [
        {k: p.get(k) for k in ("title", "year", "type", "identifiers", "authors", "abstract", "provenance")}
        for p in group]) if len(group) > 1 else result.get("versions", [])
    for field in ("title", "year", "type", "authors", "abstract", "url", "venue", "journal",
                  "publication_date", "citation_count", "reference_count", "tldr"):
        alternatives = unique([p[field] for p in group if p.get(field) or
                               (field in ("citation_count", "reference_count") and p.get(field) == 0)])
        if len(alternatives) > 1:
            result["conflicts"].append({"field": field, "values": alternatives})
        if alternatives:
            if field == "abstract":
                result[field] = max(alternatives, key=len)
            elif field == "authors":
                result[field] = max(alternatives, key=lambda lst: (len(lst), sum(bool(a.get("identifiers")) + bool(a.get("affiliations")) for a in lst)))
            elif field in ("citation_count", "reference_count"):
                nums = [x for x in alternatives if isinstance(x, int)]
                result[field] = max(nums) if nums else alternatives[0]
            else:
                result[field] = alternatives[0]
    result["authors"] = _merge_authors(group, result.get("authors") or [])
    for key, values in result["identifiers"].items():
        if len(values) > 1:
            result["conflicts"].append({"field": "identifiers." + key, "values": values})
    result["conflicts"] = unique(result["conflicts"])
    result["evidence_level"] = "abstract" if result.get("abstract") else "metadata_only"
    result["abstract_available"] = bool(result.get("abstract"))
    result["read"] = False
    return result


def deduplicate(papers):
    """Stable-ID connected components first; only then cautious bibliographic matching."""
    groups = [[p] for p in papers]
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            a_ids = set().union(*(_stable(p) for p in groups[i]))
            for j in range(i + 1, len(groups)):
                b_ids = set().union(*(_stable(p) for p in groups[j]))
                if a_ids & b_ids and not _groups_version_conflict(groups[i], groups[j]):
                    groups[i].extend(groups.pop(j))
                    changed = True
                    break
            if changed:
                break
    # A title match cannot merge two known DOIs, preprints with journal articles,
    # or two fully identified but different records. At least one lacks stable IDs.
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            a = _combine(groups[i])
            for j in range(i + 1, len(groups)):
                b = _combine(groups[j])
                known_a, known_b = set(a["identifiers"].get("doi", [])), set(b["identifiers"].get("doi", []))
                types = {a.get("type"), b.get("type")}
                preprint_mismatch = bool(types & {"preprint", "posted-content"}) and len(types - {None}) > 1
                if (_fallback(a) and _fallback(a) == _fallback(b) and not preprint_mismatch
                        and not (known_a and known_b and not known_a & known_b)
                        and not (_stable(a) and _stable(b))
                        and not _groups_version_conflict(groups[i], groups[j])):
                    groups[i].extend(groups.pop(j))
                    changed = True
                    break
            if changed:
                break
    result = [_combine(group) for group in groups]
    for i, paper in enumerate(result):
        for j in range(i + 1, len(result)):
            other = result[j]
            reasons = sorted({reason for a in groups[i] for b in groups[j]
                              for reason in _version_conflicts(a, b)})
            families = sorted(_arxiv_families(paper) & _arxiv_families(other))
            if reasons:
                for source, target in ((paper, other), (other, paper)):
                    source["relations"] = unique(source.get("relations", []) + [{
                        "type": "arxiv_version_relation", "target_identifiers": target["identifiers"],
                        "evidence": {"shared_arxiv_families": families, "reasons": reasons}}])
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InputError(f"Cannot read JSON: {path}: {exc}") from exc


def load_papers(path):
    path = Path(path)
    data = read_json(path / "papers.json" if path.is_dir() else path)
    if isinstance(data, dict):
        data = data.get("papers")
    if not isinstance(data, list) or any(not isinstance(p, dict) or not isinstance(p.get("identifiers"), dict)
                                         or not isinstance(p.get("authors"), list) for p in data):
        raise InputError("Expected normalized papers.json records")
    for p in data:
        if any(not isinstance(v, list) or any(not isinstance(x, str) for x in v)
               for v in p["identifiers"].values()):
            raise InputError("Identifier values must be arrays of strings")
    return data


def zotero_records(path):
    """Read a host export whitelist. Never open Zotero databases, storage or notes."""
    data = read_json(path)
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError("Zotero export must contain an items array")
    library = data.get("library", {}) if isinstance(data, dict) else {}
    if not isinstance(library, (dict, str, int)):
        library = {}
    if isinstance(library, dict):
        library = {k: library[k] for k in ("id", "type", "libraryID", "libraryType") if k in library}
    papers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        d = item.get("data", item)
        if not isinstance(d, dict) or d.get("itemType") in ("note", "attachment", "annotation"):
            continue
        if not d.get("title"):
            continue
        creators = d.get("creators") or []
        authors = [{"name": c.get("name") or " ".join(filter(None, [c.get("firstName"), c.get("lastName")])),
                    "identifiers": {}, "affiliations": []} for c in creators
                   if isinstance(c, dict) and c.get("creatorType", "author") == "author"]
        match_year = re.search(r"\b(?:18|19|20|21)\d{2}\b", str(d.get("date", "")))
        p = record("zotero", item.get("key", d.get("key", "")), d["title"],
                   int(match_year[0]) if match_year else None, ids(doi=d.get("DOI")), authors,
                   d.get("abstractNote"), d.get("itemType"))
        attachments = item.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        # Only explicit host status; file paths, notes and annotations are never copied.
        p["local_matches"] = [{"key": item.get("key", d.get("key")),
                                "library": item.get("libraryID", d.get("libraryID", library)),
                                "attachment_listed_count": len(attachments),
                                "attachment_status": "listed_not_checked"}]
        papers.append(p)
    return papers


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare_out(path):
    path = Path(path)
    if path.exists():
        raise InputError(f"Output already exists; choose a new run directory: {path}")
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise InputError(f"Cannot create output directory: {exc}") from exc
    return path


def write_bundle(out, command, query, papers, sources, raw, redactor, extra=None, serper_data=None, exa_data=None, epmc_data=None):
    errors = [s for s in sources.values() if s.get("status") == "error"]
    usable = [s for s in sources.values() if s.get("status") in ("ok", "not_found")]
    status = ("partial" if usable or papers else "failed") if errors else "success"
    run = {"schema": SCHEMA, "command": command, "query": query, "status": status,
           "read": False, "screening_generated": False,
           "created_at": now(), "counts": {"papers": len(papers), "raw_responses": len(raw),
           "source_errors": len(errors)}, "source_status": sources}
    if extra:
        run.update(extra)
    run, papers = redactor.clean(run), redactor.clean(papers)
    (out / "raw").mkdir(exist_ok=True)
    artifacts = {"run.json": dumps(run), "papers.json": dumps(papers)}
    if serper_data is not None:
        artifacts["results.json"] = dumps(redactor.clean(serper_data["results"]))
        artifacts["response.json"] = dumps(redactor.clean(serper_data["responses"]))
    if exa_data is not None:
        artifacts["results.json"] = dumps(redactor.clean(exa_data["results"]))
        artifacts["response.json"] = dumps(redactor.clean(exa_data["responses"]))
    if epmc_data is not None:
        artifacts["results.json"] = dumps(redactor.clean(epmc_data.get("results", [])))
        artifacts["response.json"] = dumps(redactor.clean(epmc_data.get("responses", [])))
        if "fulltext_xml" in epmc_data and epmc_data["fulltext_xml"] is not None:
            artifacts["fulltext.xml"] = epmc_data["fulltext_xml"]
        if "fulltext_summary" in epmc_data and epmc_data["fulltext_summary"] is not None:
            artifacts["fulltext_summary.json"] = dumps(redactor.clean(epmc_data["fulltext_summary"]))
        if "supplementary_manifest" in epmc_data and epmc_data["supplementary_manifest"] is not None:
            artifacts["supplementary_manifest.json"] = dumps(redactor.clean(epmc_data["supplementary_manifest"]))
    for i, response in enumerate(raw, 1):
        artifacts[f"raw/{i:04d}.json"] = dumps(redactor.clean(response))
    lines = ["# Paper search report", "", f"Status: {status}", f"Command: {command}"]
    if serper_data is not None:
        lines.extend([f"Results: {run['result_count']}", f"Returned: {run['returned_count']}",
                      f"Credits reported: {run['credits_reported']}",
                      f"Responses with unknown credits: {run['credits_unknown_responses']}"])
    else:
        lines.append(f"Papers: {len(papers)}")
    lines.extend(["", "## Sources", ""])
    for name, source in sources.items():
        lines.append(f"- {name}: {source.get('status')}; fetched={source.get('fetched', 0)}; "
                     f"truncated={source.get('truncated', False)}")
        if source.get("error"):
            lines.append("  Error: " + str(source["error"]).replace("\n", " "))
    if extra and extra.get("year_filter"):
        lines.extend(["", "## Year filter", "", dumps(extra["year_filter"])])
    if extra and "global_dropped_count" in extra:
        lines.extend(["", f"Global cap dropped {extra['global_dropped_count']} deduplicated records; "
                      "source order then provider order; no cross-provider reranking."])
    if serper_data is not None:
        lines.extend(["", "Untrusted remote data; full text and scholarly identity are not verified.",
                      "See summary.md for results and results.json / response.json for complete data."])
    else:
        lines.extend(["", "## Papers", ""])
        for p in papers:
            title = (p.get("title") or "[untitled]").replace("\n", " ")
            lines.append(f"- {title} ({p.get('year') or 'n.d.'}); {p.get('evidence_level')}; "
                         f"DOI={', '.join(p['identifiers'].get('doi', [])) or 'missing'}")
        lines.extend(["", "Metadata/abstract evidence only. OA resolution does not download or verify a PDF.",
                      "License and OA flags are separate; a URL alone is not evidence of open access."])
    if exa_data is not None:
        lines.extend(["", f"Exa returned results: {run['result_count']}",
                      f"Reported cost subtotal (USD): {run['cost_usd_reported']}",
                      f"Cost completeness: {'incomplete / unknown' if run['cost_unknown_responses'] else 'all responses reported'}",
                      f"Responses with unknown cost: {run['cost_unknown_responses']}",
                      "Text is remote content, not a verified abstract or full-text reading.",
                      "Publication entities remain candidates until scholarly identifiers are checked."])
    if epmc_data is not None:
        lines.extend(["", f"Europe PMC search hit count: {run.get('hit_count', 'unknown')}",
                      f"Papers extracted: {len(papers)}",
                      f"Full text XML fetched: {run.get('fulltext_xml_fetched', False)}",
                      f"Supplementary files fetched: {run.get('supplementary_fetched', False)}",
                      "Evidence includes Europe PMC core metadata, preprints, and retraction/correction relations."])
    artifacts["report.md"] = "\n".join(lines) + "\n"
    summary = ["| # | Year | Title | Authors | Venue / Type | DOI / Stable ID | Evidence |",
               "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, p in enumerate(papers, 1):
        title = (p.get("title") or "[untitled]").replace("\n", " ").replace("|", "\\|")
        names = [a.get("name") or "" for a in p.get("authors", [])]
        authors = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
        stable = [f"{k}:{v}" for k, values in p["identifiers"].items() for v in values]
        summary.append(f"| {i} | {p.get('year') or 'n.d.'} | {title} | {authors or 'missing'} | "
                       f"{p.get('venue') or p.get('type') or 'n.a.'} | {', '.join(stable[:3]) or 'missing'} | {p.get('evidence_level')} |")
    if epmc_data is not None:
        summary = ["| # | Year | Title | Authors | Venue / Source | Identifiers | Relations | FT/SI |",
                   "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for i, p in enumerate(papers, 1):
            title = (p.get("title") or "[untitled]").replace("\n", " ").replace("|", "\\|")
            names = [a.get("name") or "" for a in p.get("authors", [])]
            authors = ", ".join(names[:2]) + (" et al." if len(names) > 2 else "")
            stable = [f"{k}:{v}" for k in ("doi", "pmcid", "pmid") for v in p.get("identifiers", {}).get(k, [])]
            rels = "; ".join(f"{r.get('type')}: {r.get('relation')} ({r.get('source')}:{r.get('id')})"
                             for r in p.get("relations", [])) or "None"
            extra_info = p.get("extra_epmc") or {}
            ft_si = f"FT:{'Y' if extra_info.get('has_fulltext_xml') else 'N'}, SI:{'Y' if extra_info.get('has_suppl') else 'N'}"
            summary.append(f"| {i} | {p.get('year') or 'n.d.'} | {title} | {authors or 'missing'} | "
                           f"{p.get('venue') or p.get('type') or 'n.a.'} | {', '.join(stable) or 'missing'} | {rels} | {ft_si} |")
    if serper_data is not None:
        def cell(value):
            return " ".join(str(value if value is not None else "").split()).replace("|", "\\|")

        summary = ["| # | Type | Title / Value | URL | Year | Snippet |",
                   "| --- | --- | --- | --- | --- | --- |"]
        queries = serper_data["results"]["queries"]
        for i, item in enumerate(serper_data["results"]["results"], 1):
            data = item["data"]
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            title = data.get("title") or data.get("value") or metadata.get("title") or "[untitled]"
            url = data.get("url") or data.get("link") or data.get("imageUrl") or ""
            if item["type"] == "webpage":
                url = url or queries[item["query_index"]]["request"].get("url", "")
            snippet = data.get("snippet") or data.get("text") or data.get("markdown") or ""
            snippet = " ".join(str(snippet).split())[:200]
            summary.append("| " + " | ".join(cell(value) for value in
                           (i, item["type"], title, url, data.get("year"), snippet)) + " |")
    artifacts["summary.md"] = "\n".join(summary) + "\n"
    for relative, text in list(artifacts.items()):
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redactor.text(text), encoding="utf-8", newline="\n")
    for p in out.rglob("*"):
        if p.is_file():
            rel = p.relative_to(out).as_posix()
            if rel not in artifacts and rel != "manifest.json":
                artifacts[rel] = ""
    manifest = {"schema": SCHEMA, "algorithm": "sha256", "files": [
        {"path": relative, "sha256": sha256(out / relative), "size": (out / relative).stat().st_size}
        for relative in sorted(artifacts)]}
    (out / "manifest.json").write_text(dumps(manifest) + "\n", encoding="utf-8")
    return redactor.clean(run)


def check_bundle(path, include_metadata=False):
    root = Path(path).resolve()
    manifest = read_json(root / "manifest.json")
    issues, checked, seen = [], [], set()
    if not isinstance(manifest, dict) or manifest.get("algorithm") != "sha256" or not isinstance(manifest.get("files"), list):
        raise InputError("Invalid manifest")
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            issues.append("Invalid manifest entry")
            continue
        relative = entry.get("path", "")
        if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
            issues.append("Unsafe manifest path")
            continue
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            issues.append("Unsafe manifest path: " + relative)
            continue
        if Path(relative).is_absolute() or ".." in Path(relative).parts or relative in seen:
            issues.append("Unsafe or duplicate manifest path: " + relative)
            continue
        seen.add(relative)
        if not target.is_file():
            issues.append("Missing: " + relative)
        elif sha256(target) != entry.get("sha256") or target.stat().st_size != entry.get("size"):
            issues.append("Hash/size mismatch: " + relative)
        checked.append({"path": relative, "sha256": entry.get("sha256"), "size": entry.get("size")})
    for required in ("run.json", "papers.json", "report.md"):
        if required not in seen:
            issues.append("Not in manifest: " + required)
    if (root / "run.json").is_file():
        try:
            run = read_json(root / "run.json")
            if isinstance(run, dict) and run.get("command") in ("serper", "exa", "epmc"):
                for required in ("results.json", "response.json"):
                    if required not in seen:
                        issues.append("Not in manifest: " + required)
        except InputError:
            issues.append("Invalid run.json")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    for extra in sorted(actual - seen - {"manifest.json"}):
        issues.append("Unmanifested: " + extra)
    result = {"schema": SCHEMA, "status": "failed" if issues else "success", "checked": len(checked), "issues": issues}
    if include_metadata:
        result["files"] = checked
    return result
