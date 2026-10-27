"""SciVerse open platform adapter: meta-search, semantic-search (agentic RAG), content, resource, relations, catalog & Paper Schema."""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import random
import re
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request

from _core import InputError, deduplicate, doi, dumps, ids, location, now, prepare_out, record, unique, write_bundle
from _providers import SourceError, plain_text

DEFAULT_BASE_URL = "https://api.sciverse.space"

SCIVERSE_MODES = (
    "meta-search",
    "semantic-search",
    "content",
    "resource",
    "relations",
    "catalog",
    "schema-search",
    "schema-materials",
    "schema-entities",
    "schema-evidence",
    "schema-citations",
    "schema-in-paper",
)

SCIVERSE_COLLECTIONS = ("papers", "authors", "sources")
SEMANTIC_MODES = ("fast", "balanced", "quality")
BOOST_LEVELS = ("NONE", "MILD", "STRONG")
RELATION_TYPES = ("REFERENCES", "CITATIONS", "RELATED_WORKS", "references", "citations", "recommendations")
MATERIAL_GOALS = ("overview", "survey", "benchmark", "method", "reproduction")
EVIDENCE_GROUPS = (
    "citation_signal",
    "comparison_detail",
    "core_claim_result",
    "evidence_score",
    "formula",
    "reference_core",
    "reference_semantics",
    "resource",
    "schema_unit",
    "table_evidence",
)

SEMANTIC_MODE_MAP = {
    "fast": {"retrieval": "es"},
    "balanced": {"retrieval": "hybrid"},
    "quality": {"retrieval": "hybrid", "sub_queries": 3},
}


def resolve_base_url(client):
    raw = (client.env.get("SCIVERSE_BASE_URL") or "").strip()
    if not raw:
        return DEFAULT_BASE_URL
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "sciverse.space" or host.endswith(".sciverse.space")):
        raise InputError("SCIVERSE_BASE_URL must be an https://*.sciverse.space URL")
    return raw.rstrip("/")


def resolve_api_token(client):
    for key_name in ("SCIVERSE_API_KEY", "SCIVERSE_API_TOKEN"):
        val = (client.env.get(key_name) or "").strip()
        if val:
            return val
    if (client.env.get("SANMU_DISABLE_SCIVERSE_CRED_FILE") or "").strip() != "1":
        cred_file = Path.home() / ".sciverse" / "credentials.json"
        if cred_file.is_file():
            try:
                data = json.loads(cred_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and str(data.get("token") or "").strip():
                    return str(data["token"]).strip()
            except (OSError, ValueError):
                pass
    raise InputError("SCIVERSE_API_KEY (or SCIVERSE_API_TOKEN) is not configured")


def has_sciverse_token(env):
    for key_name in ("SCIVERSE_API_KEY", "SCIVERSE_API_TOKEN"):
        if (env.get(key_name) or "").strip():
            return True
    if (env.get("SANMU_DISABLE_SCIVERSE_CRED_FILE") or "").strip() != "1":
        cred_file = Path.home() / ".sciverse" / "credentials.json"
        if cred_file.is_file():
            try:
                data = json.loads(cred_file.read_text(encoding="utf-8"))
                return bool(isinstance(data, dict) and str(data.get("token") or "").strip())
            except (OSError, ValueError):
                return False
    return False


def validate_resource_file_name(file_name):
    text = str(file_name or "").strip()
    if not text or "\\" in text or ".." in text.split("/") or text.startswith("/") or ":" in text:
        raise InputError(f"Invalid SciVerse resource file_name: {file_name}")
    return text


def extract_markdown_figures(markdown_text):
    if not markdown_text:
        return []
    found = []
    seen = set()
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", str(markdown_text)):
        alt = (match.group(1) or "").strip()
        raw_path = (match.group(2) or "").strip()
        if not raw_path or raw_path.startswith(("http://", "https://", "data:", "/")) or "\\" in raw_path or ".." in raw_path.split("/"):
            continue
        if raw_path not in seen:
            seen.add(raw_path)
            found.append({"alt": alt, "file_name": raw_path})
    return found


def request_sciverse(client, method, path, *, params=None, payload=None, timeout=40, binary=False):
    token = resolve_api_token(client)
    base_url = resolve_base_url(client)
    url = base_url + path + (("?" + urlencode(params, doseq=True)) if params else "")
    headers = {
        "User-Agent": "sanmu-paper-search/1.7 (SciVerse; Python stdlib)",
        "Authorization": f"Bearer {token}",
        "Accept": "image/*" if binary else "application/json",
        "X-Request-ID": f"sanmu-{uuid.uuid4().hex[:16]}",
        "X-Sciverse-Source": f"{os.name}-sanmu-paper-search",
    }
    body_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")

    waited = 0.0
    max_attempts = 3
    for attempt in range(max_attempts):
        waited += client.wait("sciverse", token, interval=0.25, max_wait=max(0.0, 10.0 - waited))
        status, resp_headers, raw_bytes = None, {}, b""
        try:
            req = Request(url, data=body_bytes, headers=headers, method=method)
            with client.opener(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                raw_bytes = resp.read(32 * 1024 * 1024 + 1)
                if len(raw_bytes) > 32 * 1024 * 1024:
                    raise SourceError("SciVerse response exceeds 32 MiB limit", status)
        except HTTPError as exc:
            status = exc.code
            resp_headers = dict(exc.headers or {})
            raw_bytes = exc.read(1024 * 1024)
        except (URLError, OSError, TimeoutError) as exc:
            detail = client.redactor.text(str(exc))
            client.raw.append({
                "provider": "sciverse",
                "url": client.redactor.text(url),
                "method": method,
                "attempt": attempt + 1,
                "status": None,
                "error": detail,
                "retrieved_at": now(),
            })
            raise SourceError("SciVerse network error: " + detail) from exc

        if binary and status is not None and 200 <= status < 300:
            ctype = str(resp_headers.get("Content-Type") or resp_headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
            digest = hashlib.sha256(raw_bytes).hexdigest()
            client.raw.append(client.redactor.clean({
                "provider": "sciverse",
                "url": url,
                "method": method,
                "attempt": attempt + 1,
                "status": status,
                "content_type": ctype,
                "bytes": len(raw_bytes),
                "sha256": digest,
                "retrieved_at": now(),
            }))
            return {"bytes": raw_bytes, "content_type": ctype, "sha256": digest}

        raw_text = raw_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw_text)
            json.dumps(parsed, allow_nan=False)
        except (ValueError, TypeError):
            parsed = {"unparsed_body": raw_text[:1000]}

        # Avoid persisting fulltext content verbatim inside raw/0001.json audit logs
        audit_body = parsed
        if path == "/content" and isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            text_val = parsed["text"]
            audit_body = {
                k: v for k, v in parsed.items() if k != "text"
            }
            audit_body["chars_returned"] = len(text_val)
            audit_body["text_sha256"] = hashlib.sha256(text_val.encode("utf-8")).hexdigest()
            audit_body["text_preview"] = text_val[:300]

        client.raw.append(client.redactor.clean({
            "provider": "sciverse",
            "url": url,
            "method": method,
            "attempt": attempt + 1,
            "status": status,
            "body": audit_body,
            "retrieved_at": now(),
        }))

        if status in (429, 502, 503, 504) and attempt < max_attempts - 1:
            delay = max(client.retry_after(resp_headers), 2 ** attempt + random.uniform(0, 0.25)) if status == 429 else (2 ** attempt + 0.2)
            if status == 429:
                client.cooldown("sciverse", token, delay)
            if delay <= 10.0 - waited:
                if status != 429:
                    client.sleep(delay)
                    waited += delay
                continue

        if status is None or not (200 <= status < 300):
            err_detail = ""
            if isinstance(parsed, dict):
                err_detail = str(
                    parsed.get("message")
                    or (parsed.get("error", {}).get("message") if isinstance(parsed.get("error"), dict) else parsed.get("error"))
                    or parsed.get("detail")
                    or ""
                ).strip()
            msg = f"SciVerse HTTP {status}" + (f": {err_detail}" if err_detail else "")
            raise SourceError(msg, status)
        if isinstance(parsed, dict) and "unparsed_body" in parsed:
            raise SourceError("Malformed JSON from SciVerse", status)
        return parsed

    raise SourceError("SciVerse retry budget exhausted", 429)


def _normalize_authors(raw_authors, raw_affiliations=None):
    authors = []
    seen_names = set()
    aff_map = {}
    if isinstance(raw_affiliations, list):
        for entry in raw_affiliations:
            if isinstance(entry, dict) and entry.get("name"):
                affs = [plain_text(x) for x in (entry.get("affiliations") or []) if plain_text(x)]
                if affs:
                    aff_map[str(entry["name"]).strip().casefold()] = affs
    for item in raw_authors or []:
        name = ""
        orcid_val = None
        affs = []
        if isinstance(item, str):
            name = plain_text(item) or ""
        elif isinstance(item, dict):
            name = plain_text(item.get("name") or item.get("display_name")) or ""
            orcid_val = item.get("orcid")
            if isinstance(item.get("affiliations"), list):
                affs = [plain_text(x) for x in item["affiliations"] if plain_text(x)]
        if not name:
            continue
        key = name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        if not affs and key in aff_map:
            affs = aff_map[key]
        authors.append({
            "name": name,
            "identifiers": ids(orcid=orcid_val),
            "affiliations": unique(affs),
        })
    return authors


def _extract_arxiv_id(doi_val, unique_id_val=None, schema_id_val=None):
    for candidate in (doi_val, unique_id_val, schema_id_val):
        text = str(candidate or "").strip()
        m_doi = re.search(r"10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)", text, re.I)
        if m_doi:
            return m_doi.group(1)
        m_sid = re.match(r"^(\d{4}\.\d{4,5}(?:v\d+)?)-[0-9a-f]{8,}$", text, re.I)
        if m_sid:
            return m_sid.group(1)
    return None


def normalize_sciverse_meta_paper(item):
    if not isinstance(item, dict):
        return None
    title = plain_text(item.get("title")) or ""
    unique_id = str(item.get("unique_id") or "").strip()
    doc_id = str(item.get("doc_id") or "").strip()
    raw_doi = item.get("doi")
    if not raw_doi and unique_id.lower().startswith("paper:10."):
        raw_doi = unique_id.split(":", 1)[1]
    clean_doi = doi(raw_doi)
    arxiv_id = _extract_arxiv_id(clean_doi or raw_doi, unique_id)

    year = item.get("publication_published_year")
    if not isinstance(year, int):
        m_yr = re.match(r"^(\d{4})", str(item.get("publication_published_date") or ""))
        year = int(m_yr.group(1)) if m_yr else None

    authors = _normalize_authors(item.get("author"))
    abstract = plain_text(item.get("abstract"))
    venue = plain_text(item.get("publication_venue_name_unified"))

    oa_locs = []
    is_oa_raw = item.get("access_is_oa")
    is_oa = True if str(is_oa_raw).lower() == "true" or is_oa_raw is True else (False if str(is_oa_raw).lower() == "false" or is_oa_raw is False else None)
    lic = item.get("access_license")
    oa_url = str(item.get("access_oa_url") or "").strip()
    if oa_url.startswith(("http://", "https://")):
        oa_locs.append(location(oa_url, "sciverse", is_oa=True if is_oa is None else is_oa, license=lic, evidence=item.get("access_oa_status") or "oa_url"))
    for loc in item.get("locations") or []:
        if isinstance(loc, dict):
            u = str(loc.get("pdf_url") or loc.get("landing_page_url") or loc.get("url") or "").strip()
            if u.startswith(("http://", "https://")):
                oa_locs.append(location(u, "sciverse", is_oa=loc.get("is_oa", is_oa), license=loc.get("license") or lic, version=loc.get("version")))
        elif isinstance(loc, str) and loc.strip().startswith(("http://", "https://")):
            oa_locs.append(location(loc.strip(), "sciverse", is_oa=is_oa, license=lic))
    if arxiv_id:
        arx_clean = re.sub(r"\.pdf$", "", arxiv_id, flags=re.I)
        oa_locs.append(location(f"https://arxiv.org/pdf/{arx_clean}.pdf", "arxiv", is_oa=True, evidence="arxiv_id", version="submittedVersion"))

    paper_ids = ids(
        doi=clean_doi,
        arxiv=arxiv_id,
        sciverse=unique_id or None,
        sciverse_doc=doc_id or None,
    )
    kind = plain_text(item.get("type") or item.get("publication_venue_type") or item.get("metadata_type")) or "journal-article"
    p = record("sciverse", unique_id or doc_id or clean_doi or title[:48], title, year, paper_ids, authors, abstract, kind, unique(oa_locs))
    p["venue"] = venue
    p["journal"] = venue
    vol = plain_text(item.get("publication_venue_biblio_volume"))
    issue = plain_text(item.get("publication_venue_biblio_issue"))
    pages = plain_text(item.get("publication_venue_biblio_pages"))
    if venue or vol or pages:
        p["journal_info"] = {k: v for k, v in (("name", venue), ("volume", vol), ("issue", issue), ("pages", pages)) if v}
    pub_date = plain_text(item.get("publication_published_date"))
    if pub_date:
        p["publication_date"] = pub_date
    if isinstance(item.get("citation_count"), (int, float)):
        p["citation_count"] = int(item["citation_count"])
    if isinstance(item.get("reference_count"), (int, float)):
        p["reference_count"] = int(item["reference_count"])
    if isinstance(item.get("influential_citation_count"), (int, float)):
        p["influential_citation_count"] = int(item["influential_citation_count"])
    if isinstance(item.get("fwci"), (int, float)) and math.isfinite(float(item["fwci"])):
        p["fwci"] = round(float(item["fwci"]), 3)
    if isinstance(item.get("cited_by_percentile_year"), dict):
        p["citation_percentile"] = item["cited_by_percentile_year"]
    if isinstance(item.get("keywords"), list):
        kw = [plain_text(k) for k in item["keywords"] if plain_text(k)]
        if kw:
            p["keywords"] = unique(kw)
    if unique_id:
        p["sciverse_unique_id"] = unique_id
    if doc_id:
        p["sciverse_doc_id"] = doc_id
    if isinstance(item.get("is_content_accessible"), bool):
        p["is_content_accessible"] = item["is_content_accessible"]
    p["url"] = oa_url or (f"https://doi.org/{clean_doi}" if clean_doi else None)
    if arxiv_id and not p.get("pdfUrl"):
        arx_clean = re.sub(r"\.pdf$", "", arxiv_id, flags=re.I)
        p["pdfUrl"] = f"https://arxiv.org/pdf/{arx_clean}.pdf"
    elif oa_url and (oa_url.lower().endswith(".pdf") or "/pdf/" in oa_url.lower()):
        p["pdfUrl"] = oa_url
    return p


def normalize_sciverse_semantic_hit(hit):
    if not isinstance(hit, dict):
        return None
    title = plain_text(hit.get("title")) or ""
    doc_id = str(hit.get("doc_id") or "").strip()
    unique_id = str(hit.get("unique_id") or "").strip()
    raw_doi = hit.get("doi")
    if not raw_doi and unique_id.lower().startswith("paper:10."):
        raw_doi = unique_id.split(":", 1)[1]
    clean_doi = doi(raw_doi)
    year = hit.get("publication_published_year")
    if not isinstance(year, int):
        m_yr = re.match(r"^(\d{4})", str(hit.get("publication_published_date") or ""))
        year = int(m_yr.group(1)) if m_yr else None

    authors = _normalize_authors(hit.get("author"))
    abstract = plain_text(hit.get("abstract"))
    venue = plain_text(hit.get("publication_venue_name_unified"))
    chunk_text = plain_text(hit.get("chunk")) or ""
    score = hit.get("score")
    offset = hit.get("offset") if isinstance(hit.get("offset"), int) else 0
    page_no = hit.get("page_no") if isinstance(hit.get("page_no"), int) else None

    paper_ids = ids(
        doi=clean_doi,
        sciverse=unique_id or None,
        sciverse_doc=doc_id or None,
    )
    p = record("sciverse-semantic", doc_id or unique_id or hit.get("chunk_id") or title[:48], title, year, paper_ids, authors, abstract, "journal-article")
    p["venue"] = venue
    p["journal"] = venue
    pub_date = plain_text(hit.get("publication_published_date"))
    if pub_date:
        p["publication_date"] = pub_date
    if isinstance(hit.get("citation_count"), (int, float)):
        p["citation_count"] = int(hit["citation_count"])
    if isinstance(hit.get("influential_citation_count"), (int, float)):
        p["influential_citation_count"] = int(hit["influential_citation_count"])
    if doc_id:
        p["sciverse_doc_id"] = doc_id
        p["is_content_accessible"] = True
    if unique_id:
        p["sciverse_unique_id"] = unique_id
    if clean_doi:
        p["url"] = f"https://doi.org/{clean_doi}"
    chunk_entry = {
        "chunk_id": hit.get("chunk_id"),
        "doc_id": doc_id or None,
        "offset": offset,
        "page_no": page_no,
        "score": round(float(score), 4) if isinstance(score, (int, float)) and math.isfinite(float(score)) else None,
        "chunk": chunk_text,
        "recall_source": hit.get("recall_source") or hit.get("source"),
    }
    p["sciverse_chunks"] = [chunk_entry]
    if chunk_text:
        loc_tag = f"offset={offset}" + (f", p.{page_no}" if page_no is not None else "")
        p["tldr"] = f"[原文命中片段 {loc_tag}] {chunk_text[:320]}" + ("..." if len(chunk_text) > 320 else "")
        p["search_snippet"] = chunk_text
    return p


def normalize_sciverse_schema_paper(item):
    if not isinstance(item, dict):
        return None
    schema_id = str(item.get("schema_id") or "").strip()
    title = plain_text(item.get("title") or item.get("bibliographic_title")) or ""
    clean_doi = doi(item.get("doi"))
    arxiv_id = _extract_arxiv_id(clean_doi or item.get("doi"), schema_id_val=schema_id)
    year = item.get("year") if isinstance(item.get("year"), int) else (item.get("schema_year") if isinstance(item.get("schema_year"), int) else None)
    venue = plain_text(item.get("venue") or item.get("schema_venue"))
    authors = _normalize_authors(item.get("authors"), item.get("author_affiliations"))
    abstract = plain_text(item.get("abstract"))

    oa_locs = []
    access_obj = item.get("access") if isinstance(item.get("access"), dict) else {}
    oa_url = str(access_obj.get("oa_url") or "").strip()
    if oa_url.startswith(("http://", "https://")):
        oa_locs.append(location(oa_url, "sciverse-schema", is_oa=access_obj.get("is_oa", True), license=access_obj.get("license")))
    if arxiv_id:
        arx_clean = re.sub(r"\.pdf$", "", arxiv_id, flags=re.I)
        oa_locs.append(location(f"https://arxiv.org/pdf/{arx_clean}.pdf", "arxiv", is_oa=True, evidence="arxiv_id", version="submittedVersion"))

    paper_ids = ids(
        doi=clean_doi,
        arxiv=arxiv_id,
        sciverse_schema=schema_id or None,
    )
    p = record("sciverse-schema", schema_id or clean_doi or title[:48], title, year, paper_ids, authors, abstract, "conference-paper", unique(oa_locs))
    p["venue"] = venue
    p["journal"] = venue
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    if isinstance(metrics.get("citation_count"), (int, float)):
        p["citation_count"] = int(metrics["citation_count"])
    if isinstance(metrics.get("reference_count"), (int, float)):
        p["reference_count"] = int(metrics["reference_count"])
    if isinstance(metrics.get("fwci"), (int, float)) and math.isfinite(float(metrics["fwci"])):
        p["fwci"] = round(float(metrics["fwci"]), 3)
    if schema_id:
        p["sciverse_schema_id"] = schema_id
    schema_meta = {
        k: item.get(k)
        for k in (
            "schema_id",
            "document_thesis",
            "research_problem",
            "central_contribution",
            "headline_result",
            "argument_flow",
            "topics",
            "tasks",
            "has_code",
            "has_data",
            "has_supplementary_material",
        )
        if item.get(k) is not None and item.get(k) != "" and item.get(k) != []
    }
    if schema_meta:
        p["sciverse_schema"] = schema_meta
    tldr_parts = []
    if plain_text(item.get("central_contribution")):
        tldr_parts.append(f"核心贡献: {plain_text(item['central_contribution'])}")
    if plain_text(item.get("headline_result")):
        tldr_parts.append(f"主要结果: {plain_text(item['headline_result'])}")
    elif plain_text(item.get("research_problem")):
        tldr_parts.append(f"研究问题: {plain_text(item['research_problem'])}")
    if tldr_parts:
        p["tldr"] = " | ".join(tldr_parts)[:420]
    if arxiv_id:
        arx_clean = re.sub(r"\.pdf$", "", arxiv_id, flags=re.I)
        p["pdfUrl"] = f"https://arxiv.org/pdf/{arx_clean}.pdf"
        p["url"] = f"https://arxiv.org/abs/{arx_clean}"
    elif clean_doi:
        p["url"] = f"https://doi.org/{clean_doi}"
    return p


def _format_materials_markdown(materials_items, goal):
    lines = [f"# SciVerse Paper Schema 研究材料包 (goal: {goal})", ""]
    for idx, item in enumerate(materials_items or [], 1):
        if not isinstance(item, dict):
            continue
        sp = item.get("schema_paper") if isinstance(item.get("schema_paper"), dict) else {}
        sid = item.get("schema_id") or sp.get("schema_id") or f"paper-{idx}"
        title = plain_text(sp.get("title") or sp.get("bibliographic_title")) or sid
        yr = sp.get("year") or sp.get("schema_year") or "n.d."
        venue = plain_text(sp.get("venue") or sp.get("schema_venue")) or "n/a"
        lines.extend([
            f"## {idx}. {title} ({yr})",
            f"- **schema_id**: `{sid}` | **载体**: {venue} | **DOI**: `{sp.get('doi') or '无'}`",
        ])
        for label, field in (
            ("核心论点 (Thesis)", "document_thesis"),
            ("研究问题 (Problem)", "research_problem"),
            ("核心贡献 (Contribution)", "central_contribution"),
            ("主要结果 (Headline Result)", "headline_result"),
        ):
            val = plain_text(sp.get(field))
            if val:
                lines.append(f"- **{label}**: {val}")
        counts = item.get("counts") if isinstance(item.get("counts"), dict) else {}
        if counts:
            cnt_parts = [
                f"{k}: {v.get('returned', 0)}/{v.get('total', 0)}{' (truncated)' if v.get('truncated') else ''}"
                for k, v in counts.items()
                if isinstance(v, dict)
            ]
            if cnt_parts:
                lines.append(f"- **结构资源统计**: {' | '.join(cnt_parts)}")
        entities = item.get("entities") or []
        if entities:
            lines.extend(["", f"### 结构对象 Entities ({len(entities)})", ""])
            for ent in entities[:40]:
                if not isinstance(ent, dict):
                    continue
                etype = ent.get("entity_type") or "Entity"
                esub = f"/{ent['entity_subtype']}" if ent.get("entity_subtype") else ""
                ename = plain_text(ent.get("name") or ent.get("title") or ent.get("local_id")) or "Unnamed"
                etext = plain_text(ent.get("text")) or ""
                lines.append(f"- **[{etype}{esub}] {ename}** (`{ent.get('section') or 'n/a'}`): {etext[:260]}")
        relations = item.get("relations") or []
        if relations:
            lines.extend(["", f"### 论文内部逻辑关系 Relations ({len(relations)})", ""])
            for rel in relations[:30]:
                if not isinstance(rel, dict):
                    continue
                rtype = rel.get("relation_type") or "relates_to"
                src_e = rel.get("source_entity") if isinstance(rel.get("source_entity"), dict) else {}
                tgt_e = rel.get("target_entity") if isinstance(rel.get("target_entity"), dict) else {}
                src_name = plain_text(src_e.get("name") or src_e.get("title") or rel.get("source_local_id") or rel.get("source_entity_id")) or "?"
                tgt_name = plain_text(tgt_e.get("name") or tgt_e.get("title") or rel.get("target_local_id") or rel.get("target_entity_id")) or "?"
                lines.append(f"- `{src_name}` ➔ **{rtype}** ➔ `{tgt_name}`")
        evidence_list = item.get("evidence") or []
        if evidence_list:
            lines.extend(["", f"### 结构化证据 Evidence ({len(evidence_list)})", ""])
            for ev in evidence_list[:40]:
                if not isinstance(ev, dict):
                    continue
                groups = ",".join(ev.get("hot_groups") or ev.get("groups") or []) or "evidence"
                ekey = ev.get("key") or ev.get("path_bucket") or "value"
                eval_txt = plain_text(ev.get("value_text") if ev.get("value_text") is not None else str(ev.get("value_number") or ev.get("value_bool") or ""))
                if eval_txt:
                    lines.append(f"- **[{groups}] `{ekey}`**: {eval_txt[:280]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resolve_doc_id_from_query(client, query_or_id):
    text = str(query_or_id or "").strip()
    if not text:
        raise InputError("--doc-id or a paper DOI/title query is required for content mode")
    if re.fullmatch(r"[0-9a-f]{64}", text, re.I):
        return text.lower(), None
    clean_d = doi(text)
    if clean_d:
        res = request_sciverse(client, "POST", "/meta-search", payload={
            "filters": [{"field": "doi", "operator": "FILTER_OP_EQ", "value": clean_d}],
            "page_size": 3,
        })
        for item in (res.get("results") or []):
            if isinstance(item, dict) and item.get("doc_id"):
                return str(item["doc_id"]).strip(), normalize_sciverse_meta_paper(item)
    if text.lower().startswith("paper:"):
        res = request_sciverse(client, "POST", "/meta-search", payload={
            "filters": [{"field": "unique_id", "operator": "FILTER_OP_EQ", "value": text}],
            "page_size": 3,
        })
        for item in (res.get("results") or []):
            if isinstance(item, dict) and item.get("doc_id"):
                return str(item["doc_id"]).strip(), normalize_sciverse_meta_paper(item)
    # Fallback: try agentic-search to locate a paper with fulltext doc_id
    ag = request_sciverse(client, "POST", "/agentic-search", payload={"query": text, "top_k": 3})
    for hit in (ag.get("hits") or []):
        if isinstance(hit, dict) and hit.get("doc_id"):
            return str(hit["doc_id"]).strip(), normalize_sciverse_semantic_hit(hit)
    raise SourceError(f"No SciVerse fulltext doc_id found for '{text}'", 404)


def _resolve_schema_id_from_query(client, query_or_id):
    text = str(query_or_id or "").strip()
    if not text:
        raise InputError("--schema-id or a paper query/DOI is required")
    # If it looks like an explicit schema_id (contains :: or ends with -<hex>)
    if re.search(r"-[0-9a-f]{8,}$", text, re.I):
        return text, None
    clean_d = doi(text)
    payload = {"query": clean_d or text, "size": 5}
    res = request_sciverse(client, "POST", "/paper-schema/search", payload=payload)
    items = res.get("items") or []
    if clean_d:
        for it in items:
            if isinstance(it, dict) and doi(it.get("doi")) == clean_d and it.get("schema_id"):
                return str(it["schema_id"]).strip(), normalize_sciverse_schema_paper(it)
    for it in items:
        if isinstance(it, dict) and it.get("schema_id"):
            return str(it["schema_id"]).strip(), normalize_sciverse_schema_paper(it)
    raise SourceError(f"No Paper Schema record found for '{text}' (Paper Schema primarily covers 1M+ AI/CS conference & arXiv papers)", 404)


def _download_figure_resources(client, out_dir, figure_refs, max_figures=8):
    downloaded = []
    fig_dir = out_dir / "figures"
    for idx, fig in enumerate((figure_refs or [])[:max_figures], 1):
        fname = fig["file_name"] if isinstance(fig, dict) else str(fig)
        alt = fig.get("alt", "") if isinstance(fig, dict) else ""
        try:
            safe_fname = validate_resource_file_name(fname)
            bin_res = request_sciverse(client, "GET", "/resource", params={"file_name": safe_fname}, binary=True)
            ext = Path(safe_fname).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
                guessed = mimetypes.guess_extension(bin_res["content_type"] or "") or ".jpg"
                ext = ".jpg" if guessed == ".jpe" else guessed
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(safe_fname).stem).strip("_")[:48] or f"fig_{idx}"
            local_rel = f"figures/{idx:02d}_{stem}{ext}"
            fig_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / local_rel).write_bytes(bin_res["bytes"])
            downloaded.append({
                "index": idx,
                "alt": alt,
                "file_name": safe_fname,
                "local_path": local_rel,
                "content_type": bin_res["content_type"],
                "bytes": len(bin_res["bytes"]),
                "sha256": bin_res["sha256"],
            })
        except (SourceError, InputError):
            continue
    return downloaded


def execute(args, client):
    resolve_api_token(client)
    resolve_base_url(client)
    mode = getattr(args, "mode", "meta-search") or "meta-search"
    if mode not in SCIVERSE_MODES:
        raise InputError(f"Unsupported SciVerse mode: {mode}")

    query_text = (getattr(args, "query", None) or "").strip()
    target_id = (getattr(args, "id", None) or "").strip()
    doc_id = (getattr(args, "doc_id", None) or "").strip()
    schema_id = (getattr(args, "schema_id", None) or "").strip()
    collection = getattr(args, "collection", "papers") or "papers"
    if collection not in SCIVERSE_COLLECTIONS:
        raise InputError(f"collection must be one of {SCIVERSE_COLLECTIONS}")

    limit = getattr(args, "limit", 10)
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise InputError("--limit must be between 1 and 100")

    y_from = getattr(args, "year_from", None)
    y_to = getattr(args, "year_to", None)
    if any(y is not None and not 1000 <= y <= 9999 for y in (y_from, y_to)):
        raise InputError("Years must be between 1000 and 9999")
    if y_from is not None and y_to is not None and y_from > y_to:
        raise InputError("--year-from must not exceed --year-to")

    filters_adv = None
    if getattr(args, "filters", None):
        try:
            filters_adv = json.loads(args.filters)
        except ValueError as exc:
            raise InputError(f"--filters must be valid JSON: {exc}") from exc
        if not isinstance(filters_adv, (list, dict)):
            raise InputError("--filters must be a JSON array (for meta-search) or object (for semantic-search)")

    if mode == "semantic-search" and not query_text:
        raise InputError("semantic-search requires a natural-language query")
    if mode == "content" and not (doc_id or target_id or query_text):
        raise InputError("content mode requires --doc-id, --id, or query")
    if mode == "resource" and not (getattr(args, "file_name", None) or query_text):
        raise InputError("resource mode requires --file-name (or query as file_name)")
    if mode == "relations" and not (target_id or query_text):
        raise InputError("relations mode requires --id (unique_id or DOI) or query")
    if mode in ("schema-materials", "schema-citations", "schema-in-paper") and not (schema_id or target_id or query_text):
        raise InputError(f"{mode} requires --schema-id, --id, or query")
    if mode == "schema-in-paper" and not query_text and not getattr(args, "paragraph_ids", None) and not getattr(args, "markers", None):
        raise InputError("schema-in-paper requires a search query (or --paragraph-id / --marker)")

    out = prepare_out(args.out)
    papers = []
    results_payload = []
    sources = {}
    source_key = f"sciverse:{mode}"
    extra = {"sciverse_mode": mode}

    try:
        if mode == "meta-search":
            filters = []
            if getattr(args, "title_contains", None):
                filters.append({"field": "title", "operator": "FILTER_OP_CONTAINS", "value": args.title_contains.strip()})
            if getattr(args, "abstract_contains", None):
                filters.append({"field": "abstract", "operator": "FILTER_OP_CONTAINS", "value": args.abstract_contains.strip()})
            authors_filter = [a.strip() for a in (getattr(args, "authors", None) or []) if a and a.strip()]
            if authors_filter:
                filters.append({"field": "author", "operator": "FILTER_OP_IN", "value": authors_filter})
            journals_filter = [j.strip() for j in (getattr(args, "journals", None) or []) if j and j.strip()]
            if journals_filter:
                filters.append({"field": "publication_venue_name_unified", "operator": "FILTER_OP_IN", "value": journals_filter})
            subjects_filter = [s.strip() for s in (getattr(args, "subjects", None) or []) if s and s.strip()]
            if subjects_filter:
                filters.append({"field": "subjects", "operator": "FILTER_OP_IN", "value": subjects_filter})
            if y_from is not None and collection == "papers":
                filters.append({"field": "publication_published_year", "operator": "FILTER_OP_GTE", "value": y_from})
            if y_to is not None and collection == "papers":
                filters.append({"field": "publication_published_year", "operator": "FILTER_OP_LTE", "value": y_to})
            if target_id:
                clean_d = doi(target_id)
                if clean_d:
                    filters.append({"field": "doi", "operator": "FILTER_OP_EQ", "value": clean_d})
                elif target_id.lower().startswith("paper:"):
                    filters.append({"field": "unique_id", "operator": "FILTER_OP_EQ", "value": target_id})
                elif re.fullmatch(r"[0-9a-f]{64}", target_id, re.I):
                    filters.append({"field": "doc_id", "operator": "FILTER_OP_EQ", "value": target_id.lower()})
            if getattr(args, "oa_only", False) and collection == "papers":
                filters.append({"field": "access_is_oa", "operator": "FILTER_OP_EQ", "value": "true"})
            if getattr(args, "language", None) and collection == "papers":
                filters.append({"field": "language", "operator": "FILTER_OP_EQ", "value": args.language.strip()})
            if isinstance(filters_adv, list):
                for f_item in filters_adv:
                    if isinstance(f_item, dict) and f_item.get("field") and "value" in f_item:
                        filters.append({"operator": "FILTER_OP_EQ", **f_item})

            page = max(1, int(getattr(args, "page", 1) or 1))
            page_size = min(50, max(1, limit))
            body = {"collection": collection, "page": page, "page_size": page_size}
            if query_text:
                body["query"] = query_text
            if filters:
                body["filters"] = filters

            sort_arg = (getattr(args, "sort", None) or "").strip().lower()
            sort_list = []
            if sort_arg in ("cited", "citation_count", "citations"):
                sort_list.append({"field": "cited_by_count" if collection != "papers" else "citation_count", "order": "SORT_ORDER_DESC"})
            elif sort_arg in ("date", "year", "newest"):
                sort_list.append({"field": "publication_published_year", "order": "SORT_ORDER_DESC"})
            elif sort_arg == "year_asc":
                sort_list.append({"field": "publication_published_year", "order": "SORT_ORDER_ASC"})
            elif sort_arg == "fwci":
                sort_list.append({"field": "fwci", "order": "SORT_ORDER_DESC"})
            elif sort_arg in ("influential", "influential_citation_count"):
                sort_list.append({"field": "influential_citation_count", "order": "SORT_ORDER_DESC"})
            elif sort_arg in ("references", "reference_count"):
                sort_list.append({"field": "reference_count", "order": "SORT_ORDER_DESC"})
            elif sort_arg in ("h_index", "h-index") and collection != "papers":
                sort_list.append({"field": "summary_stats.h_index", "order": "SORT_ORDER_DESC"})
            elif not query_text and collection == "papers":
                sort_list.append({"field": "publication_published_year", "order": "SORT_ORDER_DESC"})

            if sort_list:
                body["sort"] = sort_list
            elif query_text and collection == "papers":
                for boost_field in ("freshness_boost", "impact_boost", "language_affinity"):
                    b_val = str(getattr(args, boost_field, "NONE") or "NONE").upper()
                    if b_val in ("MILD", "STRONG"):
                        body[boost_field] = b_val

            data = request_sciverse(client, "POST", "/meta-search", payload=body)
            raw_items = data.get("results") or []
            results_payload = raw_items
            total_count = data.get("total_count")
            extra["total_count"] = total_count
            extra["collection"] = collection

            if collection == "papers":
                for item in raw_items[:limit]:
                    p = normalize_sciverse_meta_paper(item)
                    if p:
                        papers.append(p)
                papers = deduplicate(papers)
            elif collection == "authors":
                candidates = []
                for item in raw_items[:limit]:
                    if not isinstance(item, dict):
                        continue
                    aid = str(item.get("id") or "").strip().replace("https://openalex.org/", "")
                    stats = item.get("summary_stats") if isinstance(item.get("summary_stats"), dict) else {}
                    candidates.append({
                        "provider": "sciverse",
                        "author_id": aid,
                        "name": plain_text(item.get("display_name") or item.get("name")) or aid,
                        "orcid": item.get("orcid") or None,
                        "works_count": int(item["works_count"]) if isinstance(item.get("works_count"), (int, float)) else None,
                        "cited_by_count": int(item["cited_by_count"]) if isinstance(item.get("cited_by_count"), (int, float)) else None,
                        "h_index": int(stats["h_index"]) if isinstance(stats.get("h_index"), (int, float)) else None,
                        "affiliations": [plain_text(x) for x in (item.get("last_known_institutions") or []) if plain_text(x)],
                    })
                extra["author_candidates"] = candidates
            elif collection == "sources":
                venues = []
                for item in raw_items[:limit]:
                    if not isinstance(item, dict):
                        continue
                    stats = item.get("summary_stats") if isinstance(item.get("summary_stats"), dict) else {}
                    venues.append({
                        "id": item.get("id"),
                        "display_name": item.get("display_name"),
                        "type": item.get("type"),
                        "issn": item.get("issn") or [],
                        "issn_l": item.get("issn_l"),
                        "is_oa": item.get("is_oa"),
                        "is_in_doaj": item.get("is_in_doaj"),
                        "works_count": int(item["works_count"]) if isinstance(item.get("works_count"), (int, float)) else None,
                        "cited_by_count": int(item["cited_by_count"]) if isinstance(item.get("cited_by_count"), (int, float)) else None,
                        "h_index": int(stats["h_index"]) if isinstance(stats.get("h_index"), (int, float)) else None,
                        "mean_citedness_2yr": stats.get("2yr_mean_citedness"),
                    })
                extra["source_venues"] = venues

            sources[source_key] = {
                "status": "ok",
                "fetched": len(raw_items),
                "total_count": total_count,
                "truncated": bool(isinstance(total_count, int) and total_count > len(raw_items)),
            }

        elif mode == "semantic-search":
            sem_mode = getattr(args, "semantic_mode", "balanced") or "balanced"
            if sem_mode not in SEMANTIC_MODES:
                raise InputError(f"--semantic-mode must be one of {SEMANTIC_MODES}")
            body = {
                **SEMANTIC_MODE_MAP[sem_mode],
                "query": query_text,
                "top_k": min(100, max(1, limit)),
            }
            src_types = [s for s in (getattr(args, "source_types", None) or []) if s in ("pdf", "web")]
            if src_types:
                body["source_types"] = src_types
            sem_filters = dict(filters_adv) if isinstance(filters_adv, dict) else {}
            authors_filter = [a.strip() for a in (getattr(args, "authors", None) or []) if a and a.strip()]
            if authors_filter:
                sem_filters["author"] = authors_filter
            journals_filter = [j.strip() for j in (getattr(args, "journals", None) or []) if j and j.strip()]
            if journals_filter:
                sem_filters["publication_venue_name_unified"] = journals_filter[0] if len(journals_filter) == 1 else journals_filter
            if y_from is not None or y_to is not None:
                yr_obj = {}
                if y_from is not None:
                    yr_obj["gte"] = y_from
                if y_to is not None:
                    yr_obj["lte"] = y_to
                sem_filters["publication_published_year"] = yr_obj
            if getattr(args, "language", None):
                sem_filters["lang"] = args.language.strip()
            doc_ids_scope = [d.strip().lower() for d in (getattr(args, "doc_ids", None) or []) if d and d.strip()]
            if doc_id and doc_id.lower() not in doc_ids_scope:
                doc_ids_scope.append(doc_id.lower())
            if doc_ids_scope:
                sem_filters["doc_id"] = doc_ids_scope
            if sem_filters:
                body["filters"] = sem_filters

            data = request_sciverse(client, "POST", "/agentic-search", payload=body)
            hits = data.get("hits") or []
            results_payload = hits
            for h in hits:
                p = normalize_sciverse_semantic_hit(h)
                if p:
                    papers.append(p)
            papers = deduplicate(papers)
            extra["semantic_mode"] = sem_mode
            extra["chunk_count"] = len(hits)
            extra["sciverse_chunks"] = [
                {
                    "chunk_id": h.get("chunk_id"),
                    "doc_id": h.get("doc_id"),
                    "title": plain_text(h.get("title")),
                    "year": h.get("publication_published_year"),
                    "venue": plain_text(h.get("publication_venue_name_unified")),
                    "offset": h.get("offset"),
                    "page_no": h.get("page_no"),
                    "score": round(float(h["score"]), 4) if isinstance(h.get("score"), (int, float)) else None,
                    "chunk": plain_text(h.get("chunk")),
                }
                for h in hits[:30]
                if isinstance(h, dict)
            ]
            sources[source_key] = {
                "status": "ok",
                "fetched": len(hits),
                "papers": len(papers),
                "truncated": len(hits) >= body["top_k"],
            }

        elif mode == "content":
            resolved_doc_id, seed_paper = (doc_id, None) if doc_id else _resolve_doc_id_from_query(client, target_id or query_text)
            if seed_paper:
                papers.append(seed_paper)
            offset = max(0, int(getattr(args, "offset", 0) or 0))
            chars_limit = min(65536, max(200, int(getattr(args, "chars", 6000) or 6000)))
            data = request_sciverse(client, "GET", "/content", params={
                "doc_id": resolved_doc_id,
                "offset": offset,
                "limit": chars_limit,
            })
            text_content = str(data.get("text") or "")
            fig_refs = extract_markdown_figures(text_content)
            downloaded_figs = []
            if getattr(args, "download_figures", False) and fig_refs:
                downloaded_figs = _download_figure_resources(client, out, fig_refs, max_figures=min(12, limit))
            (out / "fulltext_excerpt.md").write_text(text_content + ("\n" if not text_content.endswith("\n") else ""), encoding="utf-8")
            content_summary = {
                "doc_id": resolved_doc_id,
                "title": seed_paper.get("title") if seed_paper else (query_text or target_id or resolved_doc_id),
                "doi": (seed_paper.get("identifiers", {}).get("doi") or [None])[0] if seed_paper else doi(target_id or query_text),
                "offset": offset,
                "limit": chars_limit,
                "chars_returned": len(text_content),
                "bytes_returned": data.get("bytes_returned"),
                "next_offset": data.get("next_offset"),
                "more": bool(data.get("more")),
                "text": text_content,
                "figure_refs": fig_refs,
                "downloaded_figures": downloaded_figs,
                "excerpt_file": "fulltext_excerpt.md",
            }
            extra["sciverse_content"] = content_summary
            results_payload = [content_summary]
            sources[source_key] = {
                "status": "ok",
                "fetched": len(text_content),
                "figures_found": len(fig_refs),
                "figures_downloaded": len(downloaded_figs),
                "truncated": bool(data.get("more")),
            }

        elif mode == "resource":
            raw_fname = getattr(args, "file_name", None) or query_text
            safe_fname = validate_resource_file_name(raw_fname)
            downloaded = _download_figure_resources(client, out, [{"alt": "resource", "file_name": safe_fname}], max_figures=1)
            if not downloaded:
                raise SourceError(f"Failed to download SciVerse resource: {safe_fname}", 404)
            extra["sciverse_resources"] = downloaded
            results_payload = downloaded
            sources[source_key] = {"status": "ok", "fetched": len(downloaded), "truncated": False}

        elif mode == "relations":
            raw_seed = target_id or query_text
            clean_d = doi(raw_seed)
            unique_id = f"paper:{clean_d}" if clean_d else (raw_seed if raw_seed.lower().startswith("paper:") else "")
            if not unique_id:
                ms = request_sciverse(client, "POST", "/meta-search", payload={"query": raw_seed, "page_size": 3})
                for it in (ms.get("results") or []):
                    if isinstance(it, dict) and it.get("unique_id"):
                        unique_id = str(it["unique_id"]).strip()
                        break
            if not unique_id:
                raise SourceError(f"Could not resolve SciVerse unique_id for '{raw_seed}'", 404)

            rel_raw = str(getattr(args, "relation", "REFERENCES") or "REFERENCES").strip()
            rel_upper_map = {
                "references": "REFERENCES",
                "citations": "CITATIONS",
                "recommendations": "RELATED_WORKS",
                "related_works": "RELATED_WORKS",
                "REFERENCES": "REFERENCES",
                "CITATIONS": "CITATIONS",
                "RELATED_WORKS": "RELATED_WORKS",
            }
            api_rel = rel_upper_map.get(rel_raw, "REFERENCES")
            rel_norm_map = {
                "REFERENCES": "references",
                "CITATIONS": "citations",
                "RELATED_WORKS": "recommendations",
            }
            rel_tag = rel_norm_map[api_rel]
            page = max(1, int(getattr(args, "page", 1) or 1))
            page_size = min(50, max(1, limit))
            data = request_sciverse(client, "POST", "/meta-paper-relations", payload={
                "unique_id": unique_id,
                "relation": api_rel,
                "page": page,
                "page_size": page_size,
            })
            rel_items = data.get("items") or []
            results_payload = rel_items
            seed_display_id = clean_d or unique_id

            # Batch-hydrate sciverse unique_ids via /meta-search so papers get full metadata!
            sciverse_uids = [
                str(it["id"]).strip()
                for it in rel_items[:limit]
                if isinstance(it, dict) and str(it.get("id") or "").startswith("paper:")
            ]
            hydrated_by_uid = {}
            if sciverse_uids:
                try:
                    hyd = request_sciverse(client, "POST", "/meta-search", payload={
                        "filters": [{"field": "unique_id", "operator": "FILTER_OP_IN", "value": sciverse_uids[:50]}],
                        "page_size": min(50, len(sciverse_uids)),
                    })
                    for h_item in (hyd.get("results") or []):
                        if isinstance(h_item, dict) and h_item.get("unique_id"):
                            hp = normalize_sciverse_meta_paper(h_item)
                            if hp:
                                hydrated_by_uid[str(h_item["unique_id"]).strip()] = hp
                except SourceError:
                    pass

            for it in rel_items[:limit]:
                if not isinstance(it, dict):
                    continue
                rid = str(it.get("id") or "").strip()
                rtype = str(it.get("id_type") or "").strip().lower()
                rtitle = plain_text(it.get("title")) or rid
                if rid in hydrated_by_uid:
                    p = hydrated_by_uid[rid]
                else:
                    r_doi = doi(rid.split(":", 1)[1] if rid.lower().startswith("paper:10.") else rid)
                    r_oa = rid.rsplit("/", 1)[-1].upper() if "openalex.org/w" in rid.lower() else None
                    p_ids = ids(doi=r_doi, openalex=r_oa, sciverse=rid if rid.startswith("paper:") else None)
                    p = record("sciverse", rid, rtitle, None, p_ids, [], None, "journal-article")
                    if r_doi:
                        p["url"] = f"https://doi.org/{r_doi}"
                p["relations"] = unique((p.get("relations") or []) + [{"type": rel_tag, "seed": seed_display_id}])
                papers.append(p)
            papers = deduplicate(papers)
            total_count = data.get("total_count")
            extra["relation"] = rel_tag
            extra["seed_unique_id"] = unique_id
            extra["total_count"] = total_count
            sources[source_key] = {
                "status": "ok",
                "fetched": len(rel_items),
                "total_count": total_count,
                "truncated": bool(isinstance(total_count, int) and total_count > page * page_size),
            }

        elif mode == "catalog":
            params = {
                "collection": collection,
                "include_sample_values": "true" if getattr(args, "sample_values", False) else "false",
            }
            if getattr(args, "field_stats", False):
                params["include_field_stats"] = "true"
            data = request_sciverse(client, "GET", "/meta-catalog", params=params)
            fields_list = data.get("fields") or []
            results_payload = fields_list
            extra["sciverse_catalog"] = {
                "collection": collection,
                "field_count": len(fields_list),
                "default_fields": data.get("default_fields") or [],
                "filter_operators": data.get("filter_operators") or [],
                "fields": fields_list,
            }
            sources[source_key] = {"status": "ok", "fetched": len(fields_list), "truncated": False}

        elif mode == "schema-search":
            body = {"size": min(50, max(1, limit))}
            if query_text:
                body["query"] = query_text
            ps_filters = dict(filters_adv) if isinstance(filters_adv, dict) else {}
            if y_from is not None:
                ps_filters["year_from"] = y_from
            if y_to is not None:
                ps_filters["year_to"] = y_to
            if getattr(args, "has_code", False):
                ps_filters["has_code"] = True
            if getattr(args, "has_data", False):
                ps_filters["has_data"] = True
            if target_id and doi(target_id):
                ps_filters["doi"] = doi(target_id)
            if ps_filters:
                body["filters"] = ps_filters
            if not body.get("query") and not body.get("filters"):
                raise InputError("schema-search requires a query or filter")

            data = request_sciverse(client, "POST", "/paper-schema/search", payload=body)
            items = data.get("items") or []
            results_payload = items
            for it in items[:limit]:
                p = normalize_sciverse_schema_paper(it)
                if p:
                    papers.append(p)
            papers = deduplicate(papers)
            extra["total"] = data.get("total")
            sources[source_key] = {
                "status": "ok",
                "fetched": len(items),
                "truncated": bool(data.get("next_cursor")),
            }

        elif mode == "schema-materials":
            goal = getattr(args, "goal", "reproduction") or "reproduction"
            if goal not in MATERIAL_GOALS:
                raise InputError(f"--goal must be one of {MATERIAL_GOALS}")
            schema_ids = [s.strip() for s in (getattr(args, "schema_ids", None) or []) if s and s.strip()]
            if schema_id and schema_id not in schema_ids:
                schema_ids.insert(0, schema_id)
            if not schema_ids:
                resolved_sid, _ = _resolve_schema_id_from_query(client, target_id or query_text)
                schema_ids = [resolved_sid]

            data = request_sciverse(client, "POST", "/paper-schema/materials", payload={
                "schema_ids": schema_ids[:20],
                "goal": goal,
                "include_relation_context": True,
                "per_schema_entity_limit": min(100, max(10, int(getattr(args, "entity_limit", 40) or 40))),
                "per_schema_relation_limit": min(100, max(10, int(getattr(args, "relation_limit", 40) or 40))),
                "per_schema_attribute_limit": min(150, max(10, int(getattr(args, "evidence_limit", 60) or 60))),
                "per_schema_term_limit": 25,
            })
            mat_items = data.get("items") or []
            results_payload = mat_items
            for m_item in mat_items:
                if isinstance(m_item, dict) and isinstance(m_item.get("schema_paper"), dict):
                    p = normalize_sciverse_schema_paper(m_item["schema_paper"])
                    if p:
                        papers.append(p)
            papers = deduplicate(papers)
            md_text = _format_materials_markdown(mat_items, goal)
            (out / "schema_materials.md").write_text(md_text, encoding="utf-8")
            extra["sciverse_materials"] = {
                "goal": goal,
                "schema_ids": schema_ids[:20],
                "markdown_file": "schema_materials.md",
                "items": [
                    {
                        "schema_id": m.get("schema_id"),
                        "found": m.get("found", True),
                        "schema_paper": m.get("schema_paper"),
                        "counts": m.get("counts"),
                        "entities": (m.get("entities") or [])[:40],
                        "relations": (m.get("relations") or [])[:35],
                        "evidence": (m.get("evidence") or [])[:45],
                        "terms": [t.get("term") if isinstance(t, dict) else str(t) for t in (m.get("terms") or [])[:25]],
                    }
                    for m in mat_items
                    if isinstance(m, dict)
                ],
            }
            sources[source_key] = {
                "status": "ok",
                "fetched": len(mat_items),
                "truncated": any(
                    bool(c.get("truncated"))
                    for m in mat_items if isinstance(m, dict)
                    for c in (m.get("counts") or {}).values() if isinstance(c, dict)
                ),
            }

        elif mode == "schema-entities":
            ent_types = [t.strip() for t in (getattr(args, "entity_types", None) or []) if t and t.strip()]
            if schema_id and not query_text:
                params = {"size": min(100, max(1, limit))}
                if ent_types:
                    params["entity_types"] = ent_types
                data = request_sciverse(client, "GET", f"/paper-schema/schemas/{quote(schema_id, safe='')}/entities", params=params)
            else:
                body = {"size": min(100, max(1, limit)), "hydrate_schema_papers": True}
                if query_text:
                    body["query"] = query_text
                f_obj = {}
                if schema_id:
                    f_obj["schema_ids"] = [schema_id]
                if ent_types:
                    f_obj["entity_types"] = ent_types
                if f_obj:
                    body["filters"] = f_obj
                data = request_sciverse(client, "POST", "/paper-schema/entities/search", payload=body)
            items = data.get("items") or []
            results_payload = items
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("schema_paper"), dict):
                    p = normalize_sciverse_schema_paper(it["schema_paper"])
                    if p:
                        papers.append(p)
            papers = deduplicate(papers)
            extra["sciverse_entities"] = items[:50]
            sources[source_key] = {"status": "ok", "fetched": len(items), "truncated": bool(data.get("next_cursor"))}

        elif mode == "schema-evidence":
            raw_groups = [g.strip() for g in (getattr(args, "evidence_groups", None) or []) if g and g.strip()]
            groups = [g for g in raw_groups if g in EVIDENCE_GROUPS] or ["core_claim_result", "table_evidence", "comparison_detail", "formula", "resource"]
            body = {
                "groups": groups,
                "hydrate_schema_papers": True,
                "size": min(50, max(1, limit)),
            }
            if query_text:
                body["query"] = query_text
            if schema_id:
                body["schema_ids"] = [schema_id]
            data = request_sciverse(client, "POST", "/paper-schema/evidence/search", payload=body)
            items = data.get("items") or []
            results_payload = items
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("schema_paper"), dict):
                    p = normalize_sciverse_schema_paper(it["schema_paper"])
                    if p:
                        ev_val = plain_text(it.get("value_text"))
                        if ev_val and not p.get("search_snippet"):
                            p["search_snippet"] = f"[{','.join(it.get('hot_groups') or [])}] {ev_val}"
                        papers.append(p)
            papers = deduplicate(papers)
            extra["sciverse_evidence"] = [
                {
                    "schema_id": it.get("schema_id"),
                    "attribute_id": it.get("attribute_id"),
                    "key": it.get("key"),
                    "path": it.get("path"),
                    "groups": it.get("hot_groups") or it.get("groups") or [],
                    "value_text": plain_text(it.get("value_text")),
                    "paper_title": plain_text((it.get("schema_paper") or {}).get("title")) if isinstance(it.get("schema_paper"), dict) else None,
                }
                for it in items[:40]
                if isinstance(it, dict)
            ]
            sources[source_key] = {"status": "ok", "fetched": len(items), "truncated": bool(data.get("next_cursor"))}

        elif mode == "schema-citations":
            resolved_sid, seed_paper = (schema_id, None) if schema_id else _resolve_schema_id_from_query(client, target_id or query_text)
            if seed_paper:
                papers.append(seed_paper)
            direction = getattr(args, "direction", "outbound") or "outbound"
            if direction not in ("outbound", "inbound"):
                direction = "outbound"
            depth = min(3, max(1, int(getattr(args, "depth", 1) or 1)))
            summary_data = request_sciverse(client, "GET", f"/paper-schema/schemas/{quote(resolved_sid, safe='')}/citation-summary")
            graph_data = request_sciverse(client, "GET", f"/paper-schema/schemas/{quote(resolved_sid, safe='')}/citation-graph", params={
                "direction": direction,
                "depth": depth,
                "max_nodes": min(100, max(10, limit * 3)),
                "max_edges": min(200, max(20, limit * 6)),
            })
            nodes = graph_data.get("nodes") or []
            edges = graph_data.get("edges") or []
            results_payload = nodes
            rel_tag = "references" if direction == "outbound" else "citations"
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                sp = n.get("schema_paper") if isinstance(n.get("schema_paper"), dict) else n
                p = normalize_sciverse_schema_paper(sp)
                if p:
                    if n.get("schema_id") != resolved_sid:
                        p["relations"] = unique((p.get("relations") or []) + [{"type": rel_tag, "seed": resolved_sid}])
                    papers.append(p)
            papers = deduplicate(papers)
            extra["sciverse_citation_graph"] = {
                "root_schema_id": resolved_sid,
                "direction": direction,
                "depth": depth,
                "summary": summary_data,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "edges": edges[:100],
                "truncated": bool(graph_data.get("truncated")),
            }
            sources[source_key] = {
                "status": "ok",
                "fetched": len(nodes),
                "edges": len(edges),
                "truncated": bool(graph_data.get("truncated")),
            }

        elif mode == "schema-in-paper":
            resolved_sid, seed_paper = (schema_id, None) if schema_id else _resolve_schema_id_from_query(client, target_id)
            if seed_paper:
                papers.append(seed_paper)
            para_ids = [p.strip() for p in (getattr(args, "paragraph_ids", None) or []) if p and p.strip()]
            markers = [int(m) for m in (getattr(args, "markers", None) or []) if str(m).strip().isdigit()]
            window = min(5, max(0, int(getattr(args, "window", 1) or 1)))
            if para_ids or markers:
                body = {"window": window, "max_segments": min(50, max(5, limit * 2))}
                if para_ids:
                    body["paragraph_ids"] = para_ids
                else:
                    body["schema_id"] = resolved_sid
                    body["marker_nums"] = markers
                data = request_sciverse(client, "POST", "/paper-schema/resolve-provenance", payload=body)
            else:
                body = {
                    "schema_id": resolved_sid,
                    "query": query_text,
                    "top_k": min(25, max(1, limit)),
                    "window": window,
                }
                if getattr(args, "section_hint", None):
                    body["section_hint"] = args.section_hint.strip()
                data = request_sciverse(client, "POST", "/paper-schema/search-in-schema", payload=body)
            segments = data.get("segments") or []
            results_payload = segments
            extra["sciverse_in_paper"] = {
                "schema_id": resolved_sid,
                "query": query_text or None,
                "segment_count": len(segments),
                "segments": segments[:40],
            }
            sources[source_key] = {"status": "ok", "fetched": len(segments), "truncated": False}

    except SourceError as exc:
        sources[source_key] = {
            "status": "error",
            "error": str(exc),
            "http_status": getattr(exc, "status", None),
            "fetched": len(papers),
            "truncated": True,
        }

    if getattr(args, "with_ranks", False) and papers:
        from _easyscholar import enrich_papers_with_ranks
        enrich_papers_with_ranks(client, papers, max_lookups=15)

    (out / "results.json").write_text(dumps(client.redactor.clean(results_payload)) + "\n", encoding="utf-8")
    (out / "response.json").write_text(dumps(client.redactor.clean(client.raw)) + "\n", encoding="utf-8")
    run = write_bundle(
        out,
        "sciverse",
        {
            "mode": mode,
            "collection": collection if mode in ("meta-search", "catalog") else None,
            "query": query_text or None,
            "id": target_id or None,
            "doc_id": doc_id or None,
            "schema_id": schema_id or None,
            "limit": limit,
            "year_from": y_from,
            "year_to": y_to,
        },
        papers,
        sources,
        client.raw,
        client.redactor,
        extra=extra,
    )
    code = 3 if run["status"] == "failed" else (1 if run["status"] == "partial" else 0)
    return run, code
