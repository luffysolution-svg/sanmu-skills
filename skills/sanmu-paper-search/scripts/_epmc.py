"""Europe PMC scholarly search, preprint discovery, and JATS XML / SI bundle fetching."""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
import re
import shutil
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from _core import InputError, deduplicate, ids, location, now, prepare_out, record, write_bundle
from _providers import SourceError, parse_id, plain_text

ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESPONSE = 64 * 1024 * 1024  # 64 MiB limit for binary bundles
# Zip-bomb guards for SI bundles. The compressed bundle is already capped at
# MAX_RESPONSE; real SI (PDF, images, CSV/CIF) rarely expands beyond ~8x, while
# bombs expand 1000x+. 512 MiB total keeps a single run's disk use bounded.
MAX_SUPPL_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SUPPL_FILE_BYTES = 256 * 1024 * 1024
MAX_SUPPL_MEMBERS = 2000


def retry_delay(headers, attempt):
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if value is not None:
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = None
        if seconds is not None and math.isfinite(seconds):
            return max(0.0, seconds)
    return 2 ** attempt + random.uniform(0, 0.25)


def plan(args):
    has_id = bool(getattr(args, "id", None) and str(args.id).strip())
    has_query = bool(getattr(args, "query", None) and str(args.query).strip())
    if not has_id and not has_query:
        raise InputError("Provide a search query or --id")
    years = (getattr(args, "year_from", None), getattr(args, "year_to", None))
    if any(year is not None and not 1000 <= year <= 9999 for year in years):
        raise InputError("Years must be between 1000 and 9999")
    if all(year is not None for year in years) and args.year_from > args.year_to:
        raise InputError("--year-from must not exceed --year-to")
    if getattr(args, "limit", None) is not None and not 1 <= args.limit <= 20:
        raise InputError("limit must be between 1 and 20")


def build_query(args):
    if getattr(args, "id", None) and str(args.id).strip():
        kind, val = parse_id(args.id)
        if kind == "doi":
            return f'DOI:"{val}"', True
        if kind == "pmcid":
            return f'PMCID:{val}', True
        if kind == "pmid":
            return f'EXT_ID:"{val}"', True
        return f'"{val}"', True
    query_text = (args.query or "").strip()
    try:
        kind, val = parse_id(query_text)
        if kind == "doi":
            return f'DOI:"{val}"', True
        if kind == "pmcid":
            return f'PMCID:{val}', True
        if kind == "pmid":
            return f'EXT_ID:"{val}"', True
    except InputError:
        pass
    # Normal topic search with optional filters
    filters = []
    if getattr(args, "has_suppl", False):
        filters.append("HAS_SUPPL:y")
    if getattr(args, "open_access", False):
        filters.append("OPEN_ACCESS:y")
    if getattr(args, "has_data", False):
        filters.append("HAS_DATA:y")
    if getattr(args, "preprints", False):
        filters.append("SRC:PPR")
    if getattr(args, "has_fulltext", False):
        filters.append("HAS_FT:y")
    y_from, y_to = getattr(args, "year_from", None), getattr(args, "year_to", None)
    if y_from is not None or y_to is not None:
        filters.append(f"(PUB_YEAR:[{y_from or 1000} TO {y_to or 9999}])")
    combined = query_text
    if filters:
        combined = f"{query_text} {' '.join(filters)}"
    return combined, False


def request_epmc(client, url, accept="application/json", binary=False):
    waited = 0.0
    headers = {"User-Agent": "sanmu-paper-search/1.6 (EuropePMC; Python stdlib)", "Accept": accept}
    request = Request(url, headers=headers, method="GET")
    for attempt in range(3):
        waited += client.wait("europepmc", interval=0.15, max_wait=max(0.0, 10.0 - waited))
        audit = {"provider": "europepmc", "url": client.redactor.text(url), "method": "GET",
                 "attempt": attempt + 1, "status": None, "retrieved_at": now()}
        resp_headers = {}
        try:
            try:
                with client.opener(request, timeout=30) as response:
                    audit["status"] = response.status
                    resp_headers = dict(response.headers)
                    content = response.read(MAX_RESPONSE + 1)
            except HTTPError as exc:
                audit["status"] = exc.code
                resp_headers = dict(exc.headers or {})
                content = exc.read(MAX_RESPONSE + 1)
            audit["response_headers"] = resp_headers
            if len(content) > MAX_RESPONSE:
                raise SourceError("Response exceeds 64 MiB limit", audit["status"])
            if audit["status"] in (429, 502, 503, 504):
                delay = retry_delay(resp_headers, attempt)
                client.cooldown("europepmc", None, delay)
                if attempt < 2 and delay <= 10.0 - waited:
                    client.raw.append(client.redactor.clean(audit))
                    continue
            if audit["status"] is None or not (200 <= audit["status"] < 300):
                audit["error"] = f"HTTP {audit['status']}"
                client.raw.append(client.redactor.clean(audit))
                raise SourceError(f"HTTP {audit['status']}", audit["status"])
            if binary:
                audit["body"] = {"content_length": len(content), "type": resp_headers.get("Content-Type", "binary")}
                client.raw.append(client.redactor.clean(audit))
                return content
            text = content.decode("utf-8", errors="replace")
            if "json" in accept:
                try:
                    data = json.loads(text)
                except (ValueError, TypeError) as exc:
                    audit["error"] = "Invalid JSON"
                    client.raw.append(client.redactor.clean(audit))
                    raise SourceError("Invalid JSON response from Europe PMC", audit["status"]) from exc
                audit["body"] = data
                client.raw.append(client.redactor.clean(audit))
                return data
            audit["body"] = {"text_preview": text[:500]}
            client.raw.append(client.redactor.clean(audit))
            return text
        except (URLError, OSError, TimeoutError) as exc:
            audit["error"] = client.redactor.text(str(exc))
            client.raw.append(client.redactor.clean(audit))
            if getattr(client.rate_limiter, "shared", False) and attempt < 2:
                delay = min(1.5 * (attempt + 1), max(0.0, 10.0 - waited))
                if delay > 0:
                    client.sleep(delay)
                    waited += delay
                    continue
            raise SourceError("Network error: " + audit["error"]) from exc
        except SourceError as exc:
            audit["error"] = str(exc)
            client.raw.append(client.redactor.clean(audit))
            raise
    raise SourceError("Europe PMC retry budget exhausted", 429)


def parse_relations(row):
    relations = []
    cc_list = (row.get("commentCorrectionList") or {}).get("commentCorrection") or []
    if isinstance(cc_list, dict):
        cc_list = [cc_list]
    for cc in cc_list:
        if not isinstance(cc, dict):
            continue
        c_type = cc.get("type") or "Correction"
        is_retraction = "retraction" in c_type.lower()
        relations.append({
            "type": "retraction" if is_retraction else "correction",
            "relation": c_type,
            "id": cc.get("id"),
            "source": cc.get("source"),
            "note": cc.get("note")
        })
    return relations


def parse_authors(row):
    authors = []
    author_list = (row.get("authorList") or {}).get("author") or []
    if isinstance(author_list, dict):
        author_list = [author_list]
    for a in author_list:
        if not isinstance(a, dict):
            continue
        name = a.get("fullName") or " ".join(filter(None, [a.get("firstName"), a.get("lastName")]))
        if not name:
            continue
        affils = [{"name": a["affiliation"]}] if a.get("affiliation") else []
        aid = a.get("authorId") or {}
        identifiers = {}
        if isinstance(aid, dict) and aid.get("type") == "ORCID" and aid.get("value"):
            identifiers["orcid"] = [aid["value"]]
        authors.append({"name": name.strip(), "identifiers": identifiers, "affiliations": affils})
    if not authors and row.get("authorString"):
        for name in str(row["authorString"]).split(","):
            if name.strip():
                authors.append({"name": name.strip(), "identifiers": {}, "affiliations": []})
    return authors


def normalize_epmc(row):
    raw_year = str(row.get("pubYear", "")).strip()
    year = int(raw_year) if raw_year.isdigit() and 1000 <= int(raw_year) <= 9999 else None
    doi_val = row.get("doi")
    pmid_val = row.get("pmid")
    pmcid_val = row.get("pmcid")
    identifiers = ids(doi=doi_val, pmid=pmid_val, pmcid=pmcid_val)
    title = plain_text(row.get("title") or "")
    abstract = plain_text(row.get("abstractText") or "")
    venue = row.get("journalTitle") or ((row.get("journalInfo") or {}).get("journal", {})).get("title")
    kind = row.get("pubType") or "journal-article"
    authors = parse_authors(row)
    relations = parse_relations(row)
    oa_locations = []
    if row.get("isOpenAccess") == "Y":
        target_url = f"https://europepmc.org/article/pmc/{pmcid_val}" if pmcid_val else (f"https://doi.org/{doi_val}" if doi_val else None)
        if target_url:
            oa_locations.append(location(target_url, "europepmc", is_oa=True, evidence="europepmc_open_access"))
    record_id = pmcid_val or (f"pmid:{pmid_val}" if pmid_val else doi_val) or row.get("id", "unknown")
    p = record("europepmc", record_id, title, year, identifiers, authors, abstract, kind=kind,
               oa_locations=oa_locations, relations=relations)
    p["citation_count"] = row.get("citedByCount") if isinstance(row.get("citedByCount"), int) else None
    p["extra_epmc"] = {
        "has_suppl": row.get("hasSuppl") == "Y",
        "has_fulltext_xml": row.get("hasFT") == "Y",
        "has_pdf": row.get("hasPDF") == "Y",
        "is_open_access": row.get("isOpenAccess") == "Y",
        "in_epmc": row.get("inEPMC") == "Y",
        "source": row.get("source")
    }
    return p


def parse_xml_summary(xml_text):
    sec_count = len(re.findall(r"<\s*sec[\s>]", xml_text, re.I))
    fig_count = len(re.findall(r"<\s*fig[\s>]", xml_text, re.I))
    table_count = len(re.findall(r"<\s*table-wrap[\s>]", xml_text, re.I))
    formula_count = len(re.findall(r"<\s*(?:disp-formula|inline-formula)[\s>]", xml_text, re.I))
    ref_count = len(re.findall(r"<\s*ref[\s>]", xml_text, re.I))
    title_match = re.search(r"<\s*article-title\b[^>]*>(.*?)</\s*article-title\s*>", xml_text, re.I | re.S)
    title = plain_text(title_match.group(1)) if title_match else None
    return {
        "article_title": title,
        "section_count": sec_count,
        "figure_count": fig_count,
        "table_count": table_count,
        "formula_count": formula_count,
        "reference_count": ref_count,
        "bytes_length": len(xml_text.encode("utf-8"))
    }


def categorize_file(path_str):
    ext = Path(path_str).suffix.lower().lstrip(".")
    if ext == "pdf":
        return "si_pdf"
    if ext in ("jpg", "jpeg", "png", "webp", "tif", "tiff", "gif", "svg"):
        return "figure_image"
    if ext in ("docx", "doc", "xlsx", "xls", "csv", "tsv", "cif", "xyz", "pdb"):
        return "data_document"
    if ext in ("mp4", "mov", "avi", "webm", "mp3", "wav"):
        return "multimedia"
    return "other"


def member_path(name):
    """Normalize a ZIP member name; reject paths unsafe on Windows or POSIX (ValueError)."""
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith("/") or any(p in (".", "..") or ":" in p
            or p.endswith((" ", ".")) or re.search(r'[<>"|?*\x00-\x1f]', p)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", p)
            for p in parts)):
        raise ValueError("Unsafe supplementary archive member path")
    return "/".join(parts)


def safe_extract_zip(zip_bytes, target_dir):
    """Extract an SI bundle. Unsafe member names are skipped; duplicates or caps raise ValueError."""
    target_dir = Path(target_dir).resolve()
    extracted_entries = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        members, folded, declared = [], set(), 0
        infos = zf.infolist()
        if len(infos) > MAX_SUPPL_MEMBERS:
            raise ValueError(f"Supplementary archive has more than {MAX_SUPPL_MEMBERS} members")
        for member in infos:
            try:
                name = member_path(member.filename)
            except ValueError:
                continue
            if member.is_dir():
                continue
            if name.casefold() in folded:
                raise ValueError("Supplementary archive has case-insensitive duplicate member names")
            folded.add(name.casefold())
            declared += member.file_size
            if member.file_size > MAX_SUPPL_FILE_BYTES or declared > MAX_SUPPL_TOTAL_BYTES:
                raise ValueError("Supplementary archive exceeds uncompressed size limit")
            members.append((name, member))
        target_dir.mkdir(parents=True, exist_ok=True)
        total = 0
        for name, member in members:
            dest = (target_dir / name).resolve()
            if not dest.is_relative_to(target_dir):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            # Count actual bytes too: declared sizes in the archive are untrusted.
            with zf.open(member) as src, open(dest, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_SUPPL_FILE_BYTES or total > MAX_SUPPL_TOTAL_BYTES:
                        raise ValueError("Supplementary archive exceeds uncompressed size limit")
                    digest.update(chunk)
                    dst.write(chunk)
            extracted_entries.append({
                "name": dest.name,
                "path": f"supplementary/{name}",
                "size": size,
                "sha256": digest.hexdigest(),
                "category": categorize_file(dest.name)
            })
    cat_counts = {}
    for entry in extracted_entries:
        cat_counts[entry["category"]] = cat_counts.get(entry["category"], 0) + 1
    return {
        "total_files": len(extracted_entries),
        "categories": cat_counts,
        "files": extracted_entries
    }


def execute(args, client):
    plan(args)
    out = prepare_out(args.out)
    query_str, is_id_lookup = build_query(args)
    params = {
        "query": query_str,
        "format": "json",
        "pageSize": min(20, getattr(args, "limit", 10)),
        "resultType": "core",
        "cursorMark": "*"
    }
    sort_choice = getattr(args, "sort", "relevance")
    if sort_choice == "cited":
        params["sort"] = "CITED desc"
    elif sort_choice == "date":
        params["sort"] = "P_PDATE_D desc"
    search_url = ENDPOINT + "/search?" + urlencode(params)
    start_audit_len = len(client.raw)
    papers = []
    hit_count = 0
    raw_results = []
    sources_status = {}
    epmc_data = {}
    fetch_mode = getattr(args, "fetch", "none")
    try:
        data = request_epmc(client, search_url, accept="application/json")
        hit_count = data.get("hitCount", 0)
        result_list = (data.get("resultList") or {}).get("result") or []
        if isinstance(result_list, dict):
            result_list = [result_list]
        raw_results = result_list
        for row in result_list:
            papers.append(normalize_epmc(row))
        papers = deduplicate(papers)
        sources_status["europepmc"] = {
            "status": "ok",
            "fetched": len(papers),
            "hit_count": hit_count,
            "truncated": hit_count > len(papers),
            "query_strategy": "id_lookup" if is_id_lookup else "search"
        }
    except SourceError as exc:
        sources_status["europepmc"] = {
            "status": "error",
            "error": str(exc),
            "http_status": exc.status,
            "fetched": 0,
            "truncated": False
        }
    fulltext_xml_fetched = False
    supplementary_fetched = False
    xml_content = None
    xml_summary = None
    suppl_manifest = None
    if fetch_mode != "none" and papers:
        target_paper = papers[0]
        pmcid = (target_paper["identifiers"].get("pmcid") or [None])[0]
        if pmcid:
            if fetch_mode in ("xml", "all"):
                try:
                    xml_url = f"{ENDPOINT}/{quote(pmcid)}/fullTextXML"
                    xml_content = request_epmc(client, xml_url, accept="application/xml")
                    xml_summary = parse_xml_summary(xml_content)
                    fulltext_xml_fetched = True
                except SourceError as exc:
                    sources_status["epmc_xml"] = {"status": "error", "error": str(exc)}
            if fetch_mode in ("suppl", "all"):
                try:
                    suppl_url = f"{ENDPOINT}/{quote(pmcid)}/supplementaryFiles"
                    zip_bytes = request_epmc(client, suppl_url, accept="application/zip", binary=True)
                    suppl_manifest = safe_extract_zip(zip_bytes, out / "supplementary")
                    supplementary_fetched = True
                except SourceError as exc:
                    sources_status["epmc_supplementary"] = {"status": "error", "error": str(exc)}
                except (zipfile.BadZipFile, OSError, ValueError) as exc:
                    # Drop partial extraction so the bundle manifest only lists a consistent run.
                    shutil.rmtree(out / "supplementary", ignore_errors=True)
                    sources_status["epmc_supplementary"] = {
                        "status": "error", "error": f"Supplementary archive rejected: {exc}"}
        else:
            sources_status["epmc_fetch"] = {
                "status": "skipped",
                "reason": "Target paper does not have a PMCID in Europe PMC (XML/SI download requires PMCID)"
            }
    audits = client.raw[start_audit_len:]
    epmc_data["results"] = raw_results
    epmc_data["responses"] = audits
    if xml_content:
        epmc_data["fulltext_xml"] = xml_content
        epmc_data["fulltext_summary"] = xml_summary
    if suppl_manifest:
        epmc_data["supplementary_manifest"] = suppl_manifest
    extra = {
        "hit_count": hit_count,
        "is_id_lookup": is_id_lookup,
        "fulltext_xml_fetched": fulltext_xml_fetched,
        "supplementary_fetched": supplementary_fetched,
        "fetch_mode": fetch_mode
    }
    run = write_bundle(out, "epmc", query_str, papers, sources_status, audits, client.redactor,
                       extra=extra, epmc_data=epmc_data)
    exit_code = {"success": 0, "partial": 1, "failed": 3}[run["status"]]
    return run, exit_code
