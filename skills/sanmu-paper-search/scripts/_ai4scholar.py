"""AI4Scholar unified adapter: Semantic Scholar, Google Scholar, Google Patents, PubMed, Nano Draw, Auto-Cite & Credits."""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import random
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request

from _core import InputError, deduplicate, doi, dumps, ids, location, now, prepare_out, record, unique, write_bundle
from _providers import S2_AUTHOR_FIELDS, S2_BASE_FIELDS, S2_FIELDS, SourceError, normalize as normalize_provider_paper, plain_text, s2_id

BASE_URL = "https://ai4scholar.net"

AI4SCHOLAR_MODES = (
    "s2-search",
    "s2-bulk",
    "s2-match",
    "s2-detail",
    "s2-batch",
    "s2-citations",
    "s2-references",
    "s2-recommendations",
    "s2-snippets",
    "s2-authors",
    "s2-author-papers",
    "gs-search",
    "gs-cite",
    "gs-profiles",
    "gs-author",
    "patents",
    "pubmed-search",
    "pubmed-detail",
    "pubmed-batch",
    "pubmed-citations",
    "pubmed-related",
    "draw",
    "auto-cite",
    "credits",
)

DRAW_ACTIONS = ("smart", "generate", "edit", "style", "compose", "iterate", "critic", "svg", "vectorize")
DRAW_MODELS = ("flash", "flash31", "pro", "gptimage")
DRAW_SIZES = ("512px", "1K", "2K", "4K")
VECTORIZE_MODES = ("fast", "standard", "premium")


def require_api_key(client):
    key = (client.env.get("AI4SCHOLAR_API_KEY") or "").strip()
    if not key:
        s2_cand = (client.env.get("S2_API_KEY") or client.env.get("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
        if s2_cand.startswith("sk-user-"):
            key = s2_cand
    if not key:
        raise InputError("AI4SCHOLAR_API_KEY is not configured")
    return key


def _extract_credits(headers):
    if not isinstance(headers, dict):
        return {}
    lower = {str(k).lower(): v for k, v in headers.items()}
    info = {}
    for hdr, key in (("x-credits-charged", "charged"), ("x-credits-remaining", "remaining")):
        raw = lower.get(hdr)
        if raw is not None:
            try:
                val = float(raw)
                if math.isfinite(val):
                    info[key] = int(val) if val.is_integer() else val
            except (ValueError, TypeError):
                pass
    return info


def request_ai4scholar(client, method, path, *, params=None, payload=None, timeout=40, sse=False):
    api_key = require_api_key(client)
    url = BASE_URL + path + (("?" + urlencode(params)) if params else "")
    headers = {
        "User-Agent": "sanmu-paper-search/1.7 (AI4Scholar; Python stdlib)",
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream" if sse else "application/json",
    }
    body_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")

    waited = 0.0
    max_attempts = 1 if sse or path.startswith("/api/proxy/nano/") else 3
    for attempt in range(max_attempts):
        waited += client.wait("ai4scholar", api_key, interval=0.22, max_wait=max(0.0, 10.0 - waited))
        status, resp_headers, raw_text = None, {}, ""
        try:
            req = Request(url, data=body_bytes, headers=headers, method=method)
            with client.opener(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                raw_bytes = resp.read(32 * 1024 * 1024 + 1)
                if len(raw_bytes) > 32 * 1024 * 1024:
                    raise SourceError("AI4Scholar response exceeds 32 MiB limit", status)
                raw_text = raw_bytes.decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = exc.code
            resp_headers = dict(exc.headers or {})
            raw_text = exc.read(1024 * 1024).decode("utf-8", errors="replace")
        except (URLError, OSError, TimeoutError) as exc:
            detail = client.redactor.text(str(exc))
            client.raw.append({
                "provider": "ai4scholar",
                "url": client.redactor.text(url),
                "method": method,
                "attempt": attempt + 1,
                "status": None,
                "error": detail,
                "retrieved_at": now(),
            })
            raise SourceError("AI4Scholar network error: " + detail) from exc

        credits = _extract_credits(resp_headers)
        if sse and status is not None and 200 <= status < 300:
            event_type = ""
            result_data = None
            error_msg = None
            for line in raw_text.splitlines():
                line = line.strip()
                if not line:
                    event_type = ""
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        frame = json.loads(data_str)
                    except ValueError:
                        continue
                    if event_type == "result":
                        result_data = frame
                    elif event_type == "error":
                        error_msg = frame.get("message") if isinstance(frame, dict) else str(frame)
            audit_body = result_data if result_data is not None else {"sse_error": error_msg, "preview": raw_text[:600]}
            client.raw.append(client.redactor.clean({
                "provider": "ai4scholar",
                "url": url,
                "method": method,
                "attempt": attempt + 1,
                "status": status,
                "credits": credits,
                "body": audit_body,
                "retrieved_at": now(),
            }))
            if error_msg:
                raise SourceError(f"AI4Scholar Auto-Cite error: {error_msg}", status)
            if result_data is None:
                raise SourceError("AI4Scholar SSE stream ended without a result event", status)
            return result_data, credits

        try:
            parsed = json.loads(raw_text)
            json.dumps(parsed, allow_nan=False)
        except (ValueError, TypeError):
            parsed = {"unparsed_body": raw_text[:1000]}

        client.raw.append(client.redactor.clean({
            "provider": "ai4scholar",
            "url": url,
            "method": method,
            "attempt": attempt + 1,
            "status": status,
            "credits": credits,
            "body": parsed,
            "retrieved_at": now(),
        }))

        if status in (429, 502, 503, 504) and attempt < max_attempts - 1:
            delay = max(client.retry_after(resp_headers), 2 ** attempt + random.uniform(0, 0.25)) if status == 429 else (2 ** attempt + 0.2)
            if status == 429:
                client.cooldown("ai4scholar", api_key, delay)
            if delay <= 10.0 - waited:
                if status != 429:
                    client.sleep(delay)
                    waited += delay
                continue

        if status is None or not (200 <= status < 300):
            err_detail = ""
            if isinstance(parsed, dict):
                err_detail = str(parsed.get("message") or parsed.get("error") or parsed.get("detail") or "").strip()
            msg = f"AI4Scholar HTTP {status}" + (f": {err_detail}" if err_detail else "")
            raise SourceError(msg, status)
        if isinstance(parsed, dict) and "unparsed_body" in parsed:
            raise SourceError("Invalid JSON response from AI4Scholar", status)
        return parsed, credits

    raise SourceError("AI4Scholar retry budget exhausted", 429)


def _download_binary_asset(client, url, target_path, max_bytes=64 * 1024 * 1024):
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sanmu-paper-search/1.7",
        "Accept": "*/*",
    }
    try:
        with client.opener(Request(url, headers=headers, method="GET"), timeout=60) as resp:
            if resp.status != 200:
                return False
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes or not data:
                return False
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)
        return True
    except (HTTPError, URLError, OSError, TimeoutError, ValueError):
        return False


def _encode_image_input(raw_img):
    s = str(raw_img or "").strip()
    if not s:
        return None
    if s.startswith(("https://", "http://", "data:image/")):
        return s
    p = Path(s)
    if p.is_file():
        mime, _ = mimetypes.guess_type(str(p))
        if not mime or not mime.startswith("image/"):
            mime = "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    raise InputError(f"Image must be a public HTTP(S) URL, data:image URI, or existing local file: {s}")


def normalize_pubmed_item(item, relation=None):
    if not isinstance(item, dict):
        return None
    pmid = str(item.get("pmid") or "").strip()
    title = plain_text(item.get("title")) or ""
    if not pmid and not title:
        return None
    raw_authors = item.get("authors") or []
    authors = []
    for a in raw_authors:
        if isinstance(a, str) and a.strip():
            authors.append({"name": a.strip(), "identifiers": {}, "affiliations": []})
        elif isinstance(a, dict):
            name = (a.get("name") or f"{a.get('foreName', '')} {a.get('lastName', '')}").strip()
            if not name:
                continue
            affs = []
            if a.get("affiliation"):
                affs = [str(a["affiliation"]).strip()]
            elif isinstance(a.get("affiliations"), list):
                affs = [str(x).strip() for x in a["affiliations"] if str(x).strip()]
            authors.append({"name": name, "identifiers": {}, "affiliations": affs})

    journal_field = item.get("journal")
    if isinstance(journal_field, dict):
        venue = plain_text(journal_field.get("title") or journal_field.get("name"))
        pub_date = str(item.get("pubDate") or journal_field.get("pubDate") or "").strip() or None
    else:
        venue = plain_text(journal_field)
        pub_date = str(item.get("pubDate") or "").strip() or None

    year = item.get("year")
    if not isinstance(year, int):
        m_yr = re.search(r"\b(19|20)\d{2}\b", str(pub_date or ""))
        year = int(m_yr.group(0)) if m_yr else None

    raw_doi = item.get("doi")
    pmcid = item.get("pmcid") or item.get("pmc")
    identifiers = ids(doi=raw_doi, pmid=pmid, pmcid=pmcid)
    url = str(item.get("url") or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "")).strip() or None
    pdf_url = str(item.get("pdfUrl") or "").strip() or None
    oa_locs = []
    if pdf_url:
        oa_locs.append(location(pdf_url, "pubmed", True, None, "pubmed_pdf", None))
    elif pmcid:
        clean_pmc = str(pmcid).upper()
        if not clean_pmc.startswith("PMC"):
            clean_pmc = "PMC" + clean_pmc
        epmc_pdf = f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={clean_pmc}&blobtype=pdf"
        oa_locs.append(location(epmc_pdf, "europepmc", True, None, "pmcid_pdf", "publishedVersion"))
        pdf_url = epmc_pdf

    p = record(
        "pubmed",
        pmid or raw_doi or title,
        title,
        year,
        identifiers,
        authors,
        plain_text(item.get("abstract")),
        "journal-article",
        oa_locs,
        [relation] if relation else [],
    )
    p["url"] = url
    p["venue"] = venue
    p["journal"] = venue
    p["publication_date"] = pub_date
    p["citation_count"] = item.get("citationCount") if isinstance(item.get("citationCount"), int) else None
    p["reference_count"] = None
    p["tldr"] = None
    if pdf_url:
        p["pdfUrl"] = pdf_url
    if isinstance(item.get("meshTerms"), list) and item["meshTerms"]:
        p["mesh_terms"] = [str(m) for m in item["meshTerms"] if m]
    if isinstance(item.get("keywords"), list) and item["keywords"]:
        p["keywords"] = [str(k) for k in item["keywords"] if k]
    return p


def normalize_gs_item(item):
    if not isinstance(item, dict):
        return None
    title = plain_text(item.get("title")) or ""
    if not title:
        return None
    pub_info = " ".join(str(item.get("publicationInfo") or "").replace("\u00a0", " ").split())
    parts = [seg.strip() for seg in re.split(r"\s+-\s+", pub_info) if seg.strip()]
    author_seg = parts[0] if parts else ""
    venue_seg = parts[1] if len(parts) > 1 else ""
    publisher_seg = parts[2] if len(parts) > 2 else ""
    authors = []
    if author_seg and not author_seg.startswith("http"):
        for raw_a in author_seg.replace("…", "").replace("...", "").split(","):
            clean_a = raw_a.strip()
            if clean_a:
                authors.append({"name": clean_a, "identifiers": {}, "affiliations": []})
    venue = re.sub(r",?\s*\b(19|20)\d{2}\b\s*$", "", venue_seg).strip() or None
    if venue and re.fullmatch(r"(?:19|20)\d{2}", venue):
        venue = publisher_seg or None
    year = item.get("year")
    if not isinstance(year, int):
        m_yr = re.search(r"\b(19|20)\d{2}\b", pub_info)
        year = int(m_yr.group(0)) if m_yr else None

    link = str(item.get("link") or item.get("url") or "").strip()
    pdf_url = str(item.get("pdfUrl") or "").strip() or None
    raw_doi = doi(item.get("doi"))
    if not raw_doi and link:
        m_d = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", link)
        if m_d:
            raw_doi = doi(m_d.group(0))
    snippet_dois = []
    for m_sd in re.finditer(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", str(item.get("snippet") or "")):
        cand_doi = doi(m_sd.group(0))
        if cand_doi and cand_doi != raw_doi and cand_doi not in snippet_dois:
            snippet_dois.append(cand_doi)
    identifiers = ids(doi=raw_doi)
    oa_locs = [location(pdf_url, "google-scholar", True, None, "gs_pdf_link", None)] if pdf_url else []
    gs_id = str(item.get("id") or link or title)
    p = record(
        "google-scholar",
        gs_id,
        title,
        year,
        identifiers,
        authors,
        None,
        "journal-article",
        oa_locs,
    )
    p["url"] = link or None
    p["venue"] = venue or "Google Scholar"
    p["journal"] = venue or "Google Scholar"
    p["publication_date"] = str(year) if year else None
    p["citation_count"] = item.get("citedBy") if isinstance(item.get("citedBy"), int) else None
    p["reference_count"] = None
    p["tldr"] = None
    p["search_snippet"] = plain_text(item.get("snippet")) or ""
    if snippet_dois:
        p["candidate_dois"] = snippet_dois
    if pdf_url:
        p["pdfUrl"] = pdf_url
    if item.get("id"):
        p["gs_id"] = str(item["id"])
    inline = item.get("inlineLinks") if isinstance(item.get("inlineLinks"), dict) else {}
    if inline.get("citedByCitesId"):
        p["cites_id"] = str(inline["citedByCitesId"])
    return p


def normalize_patent_item(item):
    if not isinstance(item, dict):
        return None
    title = plain_text(item.get("title")) or ""
    pub_num = str(item.get("publicationNumber") or item.get("patentId") or "").strip()
    if not title and not pub_num:
        return None
    inventor_str = str(item.get("inventor") or "").strip()
    assignee_str = str(item.get("assignee") or "").strip()
    authors = []
    if inventor_str:
        for inv in re.split(r"[,;]", inventor_str):
            if inv.strip():
                authors.append({
                    "name": inv.strip(),
                    "identifiers": {},
                    "affiliations": [assignee_str] if assignee_str else [],
                })
    pub_date = str(item.get("publicationDate") or item.get("grantDate") or item.get("filingDate") or "").strip()
    m_yr = re.search(r"\b(18|19|20)\d{2}\b", pub_date)
    year = int(m_yr.group(0)) if m_yr else None
    pdf_url = str(item.get("pdfUrl") or "").strip() or None
    detail_link = str(item.get("detailLink") or item.get("link") or "").strip()
    if not detail_link and pub_num:
        clean_num = pub_num.replace("patent/", "").split("/")[0]
        detail_link = f"https://patents.google.com/patent/{clean_num}/en"
    oa_locs = [location(pdf_url, "google-patents", True, "publicdomain", "patent_pdf", "publishedVersion")] if pdf_url else []
    identifiers = {"patent": [pub_num]} if pub_num else {}
    venue = f"Patent {pub_num}" + (f" ({assignee_str})" if assignee_str else "")
    p = record(
        "google-patents",
        pub_num or title,
        title or pub_num,
        year,
        identifiers,
        authors,
        plain_text(item.get("snippet")),
        "patent",
        oa_locs,
    )
    p["url"] = detail_link or pdf_url
    p["venue"] = venue
    p["journal"] = venue
    p["publication_date"] = pub_date or None
    p["citation_count"] = None
    p["reference_count"] = None
    p["tldr"] = None
    if pdf_url:
        p["pdfUrl"] = pdf_url
    p["patent_info"] = {
        "patentId": item.get("patentId"),
        "publicationNumber": pub_num,
        "inventor": inventor_str,
        "assignee": assignee_str,
        "filingDate": item.get("filingDate"),
        "publicationDate": item.get("publicationDate"),
        "grantDate": item.get("grantDate"),
        "priorityDate": item.get("priorityDate"),
        "countryStatus": item.get("countryStatus"),
        "thumbnailUrl": item.get("thumbnailUrl"),
    }
    return p


def _year_range_str(args):
    explicit = getattr(args, "year", None)
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    y_from = getattr(args, "year_from", None)
    y_to = getattr(args, "year_to", None)
    if y_from is not None and y_to is not None:
        return f"{y_from}-{y_to}"
    if y_from is not None:
        return f"{y_from}-"
    if y_to is not None:
        return f"-{y_to}"
    return None


def execute(args, client):
    mode = getattr(args, "mode", "s2-search")
    if mode not in AI4SCHOLAR_MODES:
        raise InputError(f"Unsupported AI4Scholar mode: {mode}")
    require_api_key(client)

    y_from = getattr(args, "year_from", None)
    y_to = getattr(args, "year_to", None)
    if any(y is not None and not 1000 <= y <= 9999 for y in (y_from, y_to)):
        raise InputError("Years must be between 1000 and 9999")
    if y_from is not None and y_to is not None and y_from > y_to:
        raise InputError("--year-from must not exceed --year-to")

    limit = getattr(args, "limit", 10) or 10
    if not 1 <= limit <= 50:
        raise InputError("limit must be between 1 and 50")

    query_text = (getattr(args, "query", None) or "").strip()
    target_id = (getattr(args, "id", None) or "").strip()
    id_list = [x.strip() for x in (getattr(args, "ids", None) or []) if x and x.strip()]

    # Preflight validation before creating output directory
    if mode in ("s2-search", "s2-bulk", "s2-match", "s2-snippets", "s2-authors", "gs-search", "gs-profiles", "patents", "pubmed-search"):
        if not query_text and not (mode == "gs-search" and getattr(args, "cites", None)):
            raise InputError(f"Mode '{mode}' requires a search query")
    elif mode in ("s2-detail", "s2-citations", "s2-references", "s2-author-papers", "gs-cite", "gs-author", "pubmed-detail", "pubmed-citations", "pubmed-related"):
        if not target_id and not query_text:
            raise InputError(f"Mode '{mode}' requires --id (or positional query/id)")
        if not target_id:
            target_id = query_text
    elif mode in ("s2-batch", "pubmed-batch"):
        if not id_list and query_text:
            id_list = [x.strip() for x in re.split(r"[,;\s]+", query_text) if x.strip()]
        if not id_list:
            raise InputError(f"Mode '{mode}' requires --ids or comma-separated IDs")
    elif mode == "s2-recommendations":
        if not target_id and not id_list and query_text:
            id_list = [x.strip() for x in re.split(r"[,;\s]+", query_text) if x.strip()]
        if not target_id and not id_list:
            raise InputError("Mode 's2-recommendations' requires --id or --ids")
    elif mode == "draw":
        draw_action = getattr(args, "draw_action", "smart") or "smart"
        if draw_action not in DRAW_ACTIONS:
            raise InputError(f"Unsupported draw action: {draw_action}")
        images = [ _encode_image_input(img) for img in (getattr(args, "image", None) or []) if str(img).strip() ]
        if draw_action in ("edit", "style", "compose", "iterate", "critic", "vectorize") and not images:
            raise InputError(f"Draw action '{draw_action}' requires at least one --image (URL or local file path)")
        if draw_action == "compose" and len(images) < 2:
            raise InputError("Draw action 'compose' requires at least 2 images")
        if draw_action not in ("critic", "vectorize") and not query_text:
            raise InputError(f"Draw action '{draw_action}' requires a prompt")
    elif mode == "auto-cite":
        if not query_text:
            raise InputError("Mode 'auto-cite' requires academic text (100..10000 characters)")
        if not 100 <= len(query_text) <= 10000:
            raise InputError(f"Auto-cite text must be between 100 and 10000 characters (got {len(query_text)})")
        if getattr(args, "cite_mode", "auto") == "manual" and "[CITE" not in query_text:
            raise InputError("Auto-cite manual mode requires at least one [CITE] marker in the text")

    out = prepare_out(args.out)
    papers = []
    results_payload = []
    extra = {"ai4scholar_mode": mode}
    charged_total = 0
    remaining_latest = None

    def track_credits(c_info):
        nonlocal charged_total, remaining_latest
        if isinstance(c_info, dict):
            if isinstance(c_info.get("charged"), (int, float)):
                charged_total += c_info["charged"]
            if isinstance(c_info.get("remaining"), (int, float)):
                remaining_latest = c_info["remaining"]

    source_key = f"ai4scholar:{mode}"
    sources = {}

    try:
        if mode == "s2-search":
            params = {"query": query_text, "limit": min(limit, 100), "offset": getattr(args, "offset", 0) or 0, "fields": S2_FIELDS}
            yr = _year_range_str(args)
            if yr:
                params["year"] = yr
            data, cred = request_ai4scholar(client, "GET", "/graph/v1/paper/search", params=params)
            track_credits(cred)
            raw_items = data.get("data") or []
            papers = deduplicate([normalize_provider_paper("semantic-scholar", item) for item in raw_items if isinstance(item, dict) and item.get("title")])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "total": data.get("total"), "truncated": bool(data.get("next"))}

        elif mode == "s2-bulk":
            params = {"query": query_text, "fields": S2_FIELDS}
            yr = _year_range_str(args)
            if yr:
                params["year"] = yr
            if getattr(args, "sort", None):
                params["sort"] = args.sort
            if getattr(args, "token", None):
                params["token"] = args.token
            data, cred = request_ai4scholar(client, "GET", "/graph/v1/paper/search/bulk", params=params)
            track_credits(cred)
            raw_items = (data.get("data") or [])[:limit]
            papers = deduplicate([normalize_provider_paper("semantic-scholar", item) for item in raw_items if isinstance(item, dict) and item.get("title")])
            results_payload = raw_items
            if data.get("token"):
                extra["next_token"] = data["token"]
            sources[source_key] = {"status": "ok", "fetched": len(papers), "total": data.get("total"), "truncated": bool(data.get("token"))}

        elif mode == "s2-match":
            params = {"query": query_text, "fields": S2_FIELDS}
            data, cred = request_ai4scholar(client, "GET", "/graph/v1/paper/search/match", params=params)
            track_credits(cred)
            raw_items = data.get("data") or []
            papers = deduplicate([normalize_provider_paper("semantic-scholar", item) for item in raw_items if isinstance(item, dict) and item.get("title")])
            results_payload = raw_items
            sources[source_key] = {"status": "ok" if papers else "not_found", "fetched": len(papers), "truncated": False}

        elif mode == "s2-detail":
            norm_id = s2_id(target_id) if not target_id.startswith(("DOI:", "ARXIV:", "PMID:", "PMCID:", "CorpusId:", "URL:")) else target_id
            data, cred = request_ai4scholar(client, "GET", f"/graph/v1/paper/{quote(norm_id, safe=':')}", params={"fields": S2_FIELDS})
            track_credits(cred)
            if isinstance(data, dict) and data.get("title"):
                papers = [normalize_provider_paper("semantic-scholar", data)]
                results_payload = [data]
            sources[source_key] = {"status": "ok" if papers else "not_found", "fetched": len(papers), "truncated": False}

        elif mode == "s2-batch":
            norm_ids = []
            for raw_i in id_list[:100]:
                try:
                    norm_ids.append(s2_id(raw_i))
                except (InputError, SourceError):
                    norm_ids.append(raw_i)
            data, cred = request_ai4scholar(client, "POST", "/graph/v1/paper/batch", params={"fields": S2_FIELDS}, payload={"ids": norm_ids})
            track_credits(cred)
            raw_items = [x for x in (data if isinstance(data, list) else []) if isinstance(x, dict) and x.get("title")]
            papers = deduplicate([normalize_provider_paper("semantic-scholar", item) for item in raw_items])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "requested": len(norm_ids), "truncated": False}

        elif mode in ("s2-citations", "s2-references"):
            rel_type = "citations" if mode == "s2-citations" else "references"
            side_key = "citingPaper" if mode == "s2-citations" else "citedPaper"
            norm_id = s2_id(target_id) if not target_id.startswith(("DOI:", "ARXIV:", "PMID:", "PMCID:", "CorpusId:")) else target_id
            edge_fields = f"contexts,intents,isInfluential,{S2_BASE_FIELDS}"
            data, cred = request_ai4scholar(
                client,
                "GET",
                f"/graph/v1/paper/{quote(norm_id, safe=':')}/{rel_type}",
                params={"limit": min(limit, 100), "offset": getattr(args, "offset", 0) or 0, "fields": edge_fields},
            )
            track_credits(cred)
            rows = data.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                target_paper = row.get(side_key)
                if not isinstance(target_paper, dict) or not target_paper.get("title"):
                    continue
                p = normalize_provider_paper(
                    "semantic-scholar",
                    target_paper,
                    relation={"type": rel_type, "seed": target_id, "provider": "semantic-scholar"},
                )
                if row.get("contexts"):
                    p["citation_contexts"] = [str(c) for c in row["contexts"] if c]
                if row.get("intents"):
                    p["citation_intents"] = [str(i) for i in row["intents"] if i]
                if isinstance(row.get("isInfluential"), bool):
                    p["is_influential"] = row["isInfluential"]
                papers.append(p)
            papers = deduplicate(papers)
            results_payload = rows
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": bool(data.get("next"))}

        elif mode == "s2-recommendations":
            pool = getattr(args, "pool", "recent") or "recent"
            neg_ids = [x.strip() for x in (getattr(args, "negative_ids", None) or []) if x and x.strip()]
            pos_ids = id_list or ([target_id] if target_id else [])
            if len(pos_ids) == 1 and not neg_ids:
                norm_id = s2_id(pos_ids[0]) if not pos_ids[0].startswith(("DOI:", "ARXIV:", "PMID:", "CorpusId:")) else pos_ids[0]
                data, cred = request_ai4scholar(
                    client,
                    "GET",
                    f"/recommendations/v1/papers/forpaper/{quote(norm_id, safe=':')}",
                    params={"limit": min(limit, 100), "fields": S2_BASE_FIELDS, "from": pool},
                )
            else:
                norm_pos = [s2_id(x) if not x.startswith(("DOI:", "ARXIV:", "PMID:", "CorpusId:")) else x for x in pos_ids]
                norm_neg = [s2_id(x) if not x.startswith(("DOI:", "ARXIV:", "PMID:", "CorpusId:")) else x for x in neg_ids]
                data, cred = request_ai4scholar(
                    client,
                    "POST",
                    "/recommendations/v1/papers/",
                    params={"limit": min(limit, 100), "fields": S2_BASE_FIELDS},
                    payload={"positivePaperIds": norm_pos, "negativePaperIds": norm_neg},
                )
            track_credits(cred)
            raw_items = data.get("recommendedPapers") or []
            seed_label = pos_ids[0] if pos_ids else "seeds"
            papers = deduplicate([
                normalize_provider_paper(
                    "semantic-scholar",
                    item,
                    relation={"type": "recommendations", "seed": seed_label, "provider": "semantic-scholar"},
                )
                for item in raw_items
                if isinstance(item, dict) and item.get("title")
            ])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": False}

        elif mode == "s2-snippets":
            params = {"query": query_text, "limit": min(limit, 50)}
            yr = _year_range_str(args)
            if yr:
                params["year"] = yr
            if getattr(args, "venue", None):
                params["venue"] = args.venue
            data, cred = request_ai4scholar(client, "GET", "/graph/v1/snippet/search", params=params)
            track_credits(cred)
            rows = data.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                snip = row.get("snippet") or {}
                p_info = row.get("paper") or {}
                title = plain_text(p_info.get("title")) or ""
                if not title:
                    continue
                corpus_id = str(p_info.get("corpusId") or "").strip()
                raw_authors = p_info.get("authors") or []
                authors = [
                    {"name": (a.get("name") if isinstance(a, dict) else str(a)).strip(), "identifiers": {}, "affiliations": []}
                    for a in raw_authors
                    if (a.get("name") if isinstance(a, dict) else str(a)).strip()
                ]
                snip_text = plain_text(snip.get("text"))
                section = str(snip.get("section") or snip.get("snippetKind") or "").strip()
                p = record(
                    "semantic-scholar",
                    corpus_id or title,
                    title,
                    p_info.get("year") if isinstance(p_info.get("year"), int) else None,
                    ids(corpus_id=corpus_id),
                    authors,
                    snip_text,
                    "journal-article",
                )
                p["url"] = f"https://www.semanticscholar.org/p/{corpus_id}" if corpus_id else None
                p["venue"] = f"S2 全文片段 ({section})" if section else "Semantic Scholar 全文片段"
                p["journal"] = p["venue"]
                p["tldr"] = f"[匹配得分 {row.get('score', 0):.3f} · {section or '正文片段'}] {snip_text[:240]}" if snip_text else None
                papers.append(p)
                results_payload.append({
                    "title": title,
                    "corpusId": corpus_id,
                    "authors": [a["name"] for a in authors],
                    "section": section,
                    "score": row.get("score"),
                    "snippet": snip_text,
                    "url": p["url"],
                    "type": "s2-snippet",
                })
            papers = deduplicate(papers)
            sources[source_key] = {"status": "ok", "fetched": len(results_payload), "truncated": False}

        elif mode == "s2-authors":
            data, cred = request_ai4scholar(
                client,
                "GET",
                "/graph/v1/author/search",
                params={"query": query_text, "limit": min(limit, 50), "fields": S2_AUTHOR_FIELDS + ",hIndex"},
            )
            track_credits(cred)
            cands = []
            for a in data.get("data") or []:
                if not isinstance(a, dict) or not a.get("authorId"):
                    continue
                cands.append({
                    "provider": "semantic-scholar",
                    "author_id": str(a["authorId"]),
                    "name": a.get("name") or "",
                    "affiliations": a.get("affiliations") or [],
                    "paper_count": a.get("paperCount"),
                    "works_count": a.get("paperCount"),
                    "citation_count": a.get("citationCount"),
                    "cited_by_count": a.get("citationCount"),
                    "h_index": a.get("hIndex"),
                    "url": a.get("url") or f"https://www.semanticscholar.org/author/{a['authorId']}",
                })
            extra["author_candidates"] = cands
            results_payload = cands
            sources[source_key] = {"status": "ok", "fetched": len(cands), "truncated": bool(data.get("next"))}

        elif mode == "s2-author-papers":
            data, cred = request_ai4scholar(
                client,
                "GET",
                f"/graph/v1/author/{quote(target_id, safe='')}/papers",
                params={"limit": min(limit, 100), "fields": S2_BASE_FIELDS},
            )
            track_credits(cred)
            raw_items = data.get("data") or []
            papers = deduplicate([
                normalize_provider_paper("semantic-scholar", item)
                for item in raw_items
                if isinstance(item, dict) and item.get("title")
            ])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": bool(data.get("next"))}

        elif mode == "gs-search":
            page = getattr(args, "page", 1) or 1
            collected = []
            while len(collected) < limit:
                payload = {"query": query_text or getattr(args, "cites", ""), "page": page}
                if y_from is not None:
                    payload["yearFrom"] = y_from
                if y_to is not None:
                    payload["yearTo"] = y_to
                if getattr(args, "review_only", False):
                    payload["reviewOnly"] = True
                if getattr(args, "sort_by_date", False):
                    payload["sortByDate"] = True
                if getattr(args, "cites", None):
                    payload["cites"] = str(args.cites).strip()
                if getattr(args, "language", None):
                    payload["language"] = str(args.language).strip()
                data, cred = request_ai4scholar(client, "POST", "/google-scholar/v1/search", payload=payload)
                track_credits(cred)
                batch = data.get("results") or []
                if not batch:
                    break
                for item in batch:
                    if len(collected) >= limit:
                        break
                    if isinstance(item, dict):
                        item_copy = dict(item)
                        item_copy["type"] = "scholar"
                        collected.append(item_copy)
                if (data.get("resultsCount") or len(batch)) < 10:
                    break
                page += 1
            papers = deduplicate([p for p in (normalize_gs_item(item) for item in collected) if p is not None])
            results_payload = collected
            sources[source_key] = {"status": "ok", "fetched": len(collected), "truncated": len(collected) >= limit}

        elif mode == "gs-cite":
            data, cred = request_ai4scholar(
                client,
                "POST",
                "/google-scholar/v1/cite",
                payload={"paperId": target_id, "language": getattr(args, "language", "en") or "en"},
            )
            track_credits(cred)
            results_payload = [data]
            extra["gs_citations"] = data
            sources[source_key] = {"status": "ok", "fetched": len(data.get("citations") or []), "truncated": False}

        elif mode == "gs-profiles":
            data, cred = request_ai4scholar(
                client,
                "POST",
                "/google-scholar/v1/profiles",
                payload={"authorName": query_text},
            )
            track_credits(cred)
            profiles = data.get("profiles") or []
            cands = []
            for pr in profiles:
                if not isinstance(pr, dict):
                    continue
                aid = str(pr.get("authorId") or "").strip()
                name = str(pr.get("title") or pr.get("name") or "").strip()
                if not aid and not name:
                    continue
                cands.append({
                    "provider": "google-scholar",
                    "author_id": aid,
                    "name": name,
                    "affiliations": [pr["affiliations"]] if pr.get("affiliations") else [],
                    "cited_by_count": pr.get("citedBy"),
                    "citation_count": pr.get("citedBy"),
                    "email": pr.get("email"),
                    "interests": [i.get("title") if isinstance(i, dict) else str(i) for i in (pr.get("interests") or [])],
                })
            if not cands:
                try:
                    s2_data, s2_cred = request_ai4scholar(
                        client,
                        "GET",
                        "/graph/v1/author/search",
                        params={"query": query_text, "limit": min(limit, 50), "fields": S2_AUTHOR_FIELDS + ",hIndex"},
                    )
                    track_credits(s2_cred)
                    for a in s2_data.get("data") or []:
                        if not isinstance(a, dict) or not a.get("authorId"):
                            continue
                        cands.append({
                            "provider": "semantic-scholar",
                            "author_id": str(a["authorId"]),
                            "name": a.get("name") or "",
                            "affiliations": a.get("affiliations") or [],
                            "paper_count": a.get("paperCount"),
                            "works_count": a.get("paperCount"),
                            "citation_count": a.get("citationCount"),
                            "cited_by_count": a.get("citationCount"),
                            "h_index": a.get("hIndex"),
                            "url": a.get("url") or f"https://www.semanticscholar.org/author/{a['authorId']}",
                        })
                except SourceError:
                    pass
            extra["author_candidates"] = cands
            results_payload = cands
            sources[source_key] = {"status": "ok", "fetched": len(cands), "truncated": bool(data.get("nextPageToken"))}

        elif mode == "gs-author":
            data, cred = request_ai4scholar(
                client,
                "POST",
                "/google-scholar/v1/author",
                payload={"authorId": target_id, "sort": getattr(args, "sort", "cited") if getattr(args, "sort", None) in ("title", "pubdate") else "pubdate"},
            )
            track_credits(cred)
            articles = (data.get("articles") or [])[:limit]
            for art in articles:
                if not isinstance(art, dict) or not art.get("title"):
                    continue
                yr_val = None
                if art.get("year") and str(art["year"]).isdigit():
                    yr_val = int(art["year"])
                a_list = [
                    {"name": a.strip(), "identifiers": {}, "affiliations": []}
                    for a in str(art.get("authors") or data.get("name") or "").split(",")
                    if a.strip()
                ]
                p = record("google-scholar", art.get("link") or art["title"], art["title"], yr_val, {}, a_list, None, "journal-article")
                p["url"] = art.get("link")
                p["citation_count"] = art.get("citedBy") if isinstance(art.get("citedBy"), int) else None
                p["venue"] = art.get("publication") or "Google Scholar Author Profile"
                p["journal"] = p["venue"]
                papers.append(p)
            papers = deduplicate(papers)
            results_payload = [data]
            extra["author_profile"] = {k: data.get(k) for k in ("name", "affiliations", "email", "citedBy", "interests") if k in data}
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": False}

        elif mode == "patents":
            payload = {
                "query": query_text,
                "page": getattr(args, "page", 1) or 1,
                "num": min(max(limit, 10), 100),
            }
            for attr, field in (
                ("sort", "sort"),
                ("patent_status", "status"),
                ("patent_type", "type"),
                ("country", "country"),
                ("language", "language"),
                ("inventor", "inventor"),
                ("assignee", "assignee"),
                ("before", "before"),
                ("after", "after"),
            ):
                val = getattr(args, attr, None)
                if val and str(val).strip():
                    payload[field] = str(val).strip()
            if y_from is not None and "after" not in payload:
                payload["after"] = f"publication:{y_from}0101"
            if y_to is not None and "before" not in payload:
                payload["before"] = f"publication:{y_to}1231"
            data, cred = request_ai4scholar(client, "POST", "/google-scholar/v1/patents", payload=payload)
            track_credits(cred)
            raw_items = (data.get("results") or [])[:limit]
            for item in raw_items:
                if isinstance(item, dict):
                    item["type"] = "patents"
            papers = deduplicate([p for p in (normalize_patent_item(item) for item in raw_items) if p is not None])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": (data.get("resultsCount") or 0) > len(papers)}

        elif mode == "pubmed-search":
            sort_mode = getattr(args, "sort", "relevance")
            if sort_mode not in ("relevance", "date"):
                sort_mode = "relevance"
            payload = {
                "query": query_text,
                "limit": limit,
                "offset": getattr(args, "offset", 0) or 0,
                "sort": sort_mode,
            }
            min_date = getattr(args, "min_date", None) or (f"{y_from}/01/01" if y_from else None)
            max_date = getattr(args, "max_date", None) or (f"{y_to}/12/31" if y_to else None)
            if min_date:
                payload["minDate"] = str(min_date).strip()
            if max_date:
                payload["maxDate"] = str(max_date).strip()
            data, cred = request_ai4scholar(client, "POST", "/pubmed/v1/paper/search", payload=payload)
            track_credits(cred)
            raw_items = data.get("papers") if isinstance(data.get("papers"), list) else (data.get("data") or [])
            papers = deduplicate([p for p in (normalize_pubmed_item(item) for item in raw_items) if p is not None])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "total": data.get("total"), "truncated": (data.get("total") or 0) > len(papers)}

        elif mode == "pubmed-detail":
            clean_pmid = re.sub(r"^pmid:\s*", "", target_id, flags=re.I).strip()
            data, cred = request_ai4scholar(client, "GET", f"/pubmed/v1/paper/{quote(clean_pmid, safe='')}")
            track_credits(cred)
            item = data.get("paper") if isinstance(data, dict) and isinstance(data.get("paper"), dict) else data
            p = normalize_pubmed_item(item)
            if p:
                papers = [p]
                results_payload = [item]
            sources[source_key] = {"status": "ok" if papers else "not_found", "fetched": len(papers), "truncated": False}

        elif mode == "pubmed-batch":
            clean_pmids = [re.sub(r"^pmid:\s*", "", x, flags=re.I).strip() for x in id_list[:100]]
            data, cred = request_ai4scholar(client, "POST", "/pubmed/v1/paper/batch", payload={"pmids": clean_pmids, "ids": clean_pmids})
            track_credits(cred)
            raw_items = data if isinstance(data, list) else (data.get("papers") or data.get("data") or [])
            papers = deduplicate([p for p in (normalize_pubmed_item(item) for item in raw_items) if p is not None])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "requested": len(clean_pmids), "truncated": False}

        elif mode in ("pubmed-citations", "pubmed-related"):
            clean_pmid = re.sub(r"^pmid:\s*", "", target_id, flags=re.I).strip()
            endpoint = "citations" if mode == "pubmed-citations" else "related"
            rel_type = "citations" if mode == "pubmed-citations" else "recommendations"
            data, cred = request_ai4scholar(
                client,
                "GET",
                f"/pubmed/v1/paper/{quote(clean_pmid, safe='')}/{endpoint}",
                params={"limit": limit},
            )
            track_credits(cred)
            raw_items = data if isinstance(data, list) else (data.get("papers") or data.get("data") or [])
            rel_obj = {"type": rel_type, "seed": f"pmid:{clean_pmid}", "provider": "pubmed"}
            papers = deduplicate([p for p in (normalize_pubmed_item(item, relation=rel_obj) for item in raw_items) if p is not None])
            results_payload = raw_items
            sources[source_key] = {"status": "ok", "fetched": len(papers), "truncated": False}

        elif mode == "draw":
            draw_action = getattr(args, "draw_action", "smart") or "smart"
            model_name = getattr(args, "model", "flash") or "flash"
            aspect_ratio = getattr(args, "aspect_ratio", "16:9") or "16:9"
            image_size = getattr(args, "image_size", "2K") or "2K"
            images = [_encode_image_input(img) for img in (getattr(args, "image", None) or []) if str(img).strip()]
            payload = {"action": draw_action}
            if query_text:
                payload["prompt"] = query_text
            if draw_action not in ("svg", "critic"):
                payload["model"] = model_name
                payload["aspectRatio"] = aspect_ratio
                payload["imageSize"] = image_size
            if getattr(args, "style_preset", None):
                payload["stylePreset"] = args.style_preset
            if getattr(args, "lang", None):
                payload["lang"] = args.lang
            if images:
                payload["images"] = images
            if draw_action == "vectorize" and getattr(args, "vectorize_mode", None):
                payload["vectorizeMode"] = args.vectorize_mode

            data, cred = request_ai4scholar(client, "POST", "/api/proxy/nano/generate", payload=payload, timeout=240)
            track_credits(cred)
            if isinstance(data, dict) and data.get("success") is False:
                raise SourceError(str(data.get("message") or data.get("error") or "Nano Draw generation failed"))
            if isinstance(data.get("creditCost"), (int, float)) and not charged_total:
                charged_total = data["creditCost"]

            saved_files = {}
            if data.get("imageUrl"):
                img_ext = ".png"
                if ".jpg" in str(data["imageUrl"]).lower() or ".jpeg" in str(data["imageUrl"]).lower():
                    img_ext = ".jpg"
                local_img = out / f"figure{img_ext}"
                if _download_binary_asset(client, data["imageUrl"], local_img):
                    saved_files["image_file"] = local_img.name
            if data.get("svgCode"):
                local_svg = out / "figure.svg"
                local_svg.write_text(str(data["svgCode"]), encoding="utf-8")
                saved_files["svg_file"] = local_svg.name
            elif data.get("svgUrl"):
                local_svg = out / "figure.svg"
                if _download_binary_asset(client, data["svgUrl"], local_svg):
                    saved_files["svg_file"] = local_svg.name
            if data.get("pdfUrl"):
                local_pdf = out / "figure_vector.pdf"
                if _download_binary_asset(client, data["pdfUrl"], local_pdf):
                    saved_files["pdf_file"] = local_pdf.name
            if data.get("pptxUrl"):
                local_pptx = out / "figure_editable.pptx"
                if _download_binary_asset(client, data["pptxUrl"], local_pptx):
                    saved_files["pptx_file"] = local_pptx.name

            draw_summary = {
                "action": draw_action,
                "prompt": query_text,
                "model": model_name,
                "aspectRatio": aspect_ratio,
                "imageSize": image_size,
                "imageUrl": data.get("imageUrl"),
                "svgUrl": data.get("svgUrl"),
                "pdfUrl": data.get("pdfUrl"),
                "pptxUrl": data.get("pptxUrl"),
                "optimizedPrompt": data.get("optimizedPrompt"),
                "critique": data.get("critique"),
                "creditCost": data.get("creditCost") or charged_total,
                "savedFiles": saved_files,
            }
            extra["draw_result"] = draw_summary
            results_payload = [draw_summary]
            sources[source_key] = {"status": "ok", "fetched": 1, "truncated": False}

        elif mode == "auto-cite":
            payload = {
                "text": query_text,
                "mode": getattr(args, "cite_mode", "auto") or "auto",
                "minCitations": min(max(int(getattr(args, "min_citations", 10) or 10), 1), 50),
                "citationStyle": getattr(args, "citation_style", "gbt7714") or "gbt7714",
            }
            if getattr(args, "field", None):
                payload["field"] = args.field
            if getattr(args, "year_preference", None):
                payload["yearPreference"] = int(args.year_preference)
            if getattr(args, "preferred_venues", None):
                payload["preferredVenues"] = [v.strip() for v in str(args.preferred_venues).split(",") if v.strip()]
            if getattr(args, "exclude_preprints", False):
                payload["excludePreprints"] = True
            if getattr(args, "exclude_conferences", False):
                payload["excludeConferences"] = True

            data, cred = request_ai4scholar(client, "POST", "/api/proxy/auto-cite", payload=payload, timeout=240, sse=True)
            track_credits(cred)
            annotated = str(data.get("annotatedText") or query_text)
            refs = data.get("references") or []
            bibtex_str = str(data.get("bibtex") or "")
            (out / "annotated_text.md").write_text(annotated + "\n", encoding="utf-8")
            if bibtex_str.strip():
                (out / "references.bib").write_text(bibtex_str.strip() + "\n", encoding="utf-8")
            for r_item in refs:
                if not isinstance(r_item, dict):
                    continue
                r_title = plain_text(r_item.get("title") or r_item.get("formatted")) or ""
                if not r_title:
                    continue
                r_doi = r_item.get("doi")
                p = record(
                    "ai4scholar-autocite",
                    r_doi or f"ref-{r_item.get('number', len(papers)+1)}",
                    r_title,
                    r_item.get("year") if isinstance(r_item.get("year"), int) else None,
                    ids(doi=r_doi),
                    [],
                    r_item.get("matchReason") or r_item.get("formatted"),
                    "journal-article",
                )
                p["url"] = r_item.get("url") or (f"https://doi.org/{r_doi}" if r_doi else None)
                p["venue"] = r_item.get("venue")
                p["journal"] = r_item.get("venue")
                p["citation_count"] = r_item.get("citedBy") if isinstance(r_item.get("citedBy"), int) else None
                p["tldr"] = r_item.get("formatted")
                papers.append(p)
            papers = deduplicate(papers)
            extra["auto_cite_result"] = {
                "annotatedText": annotated,
                "references": refs,
                "bibtex": bibtex_str,
                "stats": data.get("stats"),
            }
            results_payload = refs
            sources[source_key] = {"status": "ok", "fetched": len(refs), "truncated": False}

        elif mode == "credits":
            data, cred = request_ai4scholar(client, "GET", "/api/credits")
            track_credits(cred)
            credits_obj = data.get("credits") if isinstance(data.get("credits"), dict) else {}
            api_key_obj = data.get("api_key") if isinstance(data.get("api_key"), dict) else {}
            balance = {
                "total_available": credits_obj.get("total_available"),
                "permanent": credits_obj.get("permanent"),
                "member_monthly_remaining": credits_obj.get("member_monthly_remaining"),
                "held": credits_obj.get("held"),
                "key_credit_limit": api_key_obj.get("credit_limit"),
                "key_credits_used": api_key_obj.get("credits_used"),
                "key_credits_remaining": api_key_obj.get("credits_remaining"),
                "membership": data.get("membership"),
            }
            extra["credits_balance"] = balance
            results_payload = [balance]
            sources[source_key] = {"status": "ok", "fetched": 1, "truncated": False}

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

    extra["credits"] = {
        "charged_total": charged_total,
        "remaining_latest": remaining_latest,
    }
    (out / "results.json").write_text(dumps(client.redactor.clean(results_payload)) + "\n", encoding="utf-8")
    (out / "response.json").write_text(dumps(client.redactor.clean(client.raw)) + "\n", encoding="utf-8")
    run = write_bundle(
        out,
        "ai4scholar",
        {
            "mode": mode,
            "query": query_text or None,
            "id": target_id or None,
            "ids": id_list or None,
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
