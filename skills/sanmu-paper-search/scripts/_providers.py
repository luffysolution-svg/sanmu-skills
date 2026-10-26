"""Public GET adapters. No SDK, database, full-text fetching or hidden fallbacks."""
from __future__ import annotations

import email.utils
import html
from html.parser import HTMLParser
import json
import os
import math
import random
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from _core import InputError, Redactor, deduplicate, doi, ids, location, now, record
from _rate_limit import RateLimiter, RateLimitError

BASE = {"openalex": "https://api.openalex.org", "semantic-scholar": "https://api.semanticscholar.org/graph/v1",
        "crossref": "https://api.crossref.org", "unpaywall": "https://api.unpaywall.org/v2",
        "europepmc": "https://www.ebi.ac.uk/europepmc/webservices/rest"}
# Graph FullPaper (lookup/search) includes TLDR; BasePaper/PaperWithLinks do not.
S2_BASE_FIELDS = "paperId,corpusId,externalIds,title,year,authors,abstract,publicationTypes,isOpenAccess,openAccessPdf,url,citationCount,referenceCount,venue,journal,publicationDate"
S2_FIELDS = S2_BASE_FIELDS + ",tldr"
S2_RECOMMENDATION_FIELDS = S2_BASE_FIELDS
S2_AUTHOR_FIELDS = "name,authorId,affiliations,paperCount,citationCount,url"


class SourceError(Exception):
    def __init__(self, message, status=None, kind=None):
        super().__init__(message)
        self.status = status
        self.kind = kind


class Client:
    def __init__(self, env=None, opener=None, sleep=None, clock=None, *, rate_limiter=None, shared_rate_limit=None):
        self.env = os.environ if env is None else env
        self.redactor = Redactor(self.env)
        self.opener = opener or urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        shared = (opener is None and sleep is None and clock is None) if shared_rate_limit is None else shared_rate_limit
        self.rate_limiter = rate_limiter or RateLimiter(shared=shared, clock=clock, sleep=self.sleep)
        self.last = {}
        self.raw = []

    def wait(self, source, key=None, interval=None, max_wait=10.0):
        interval = (1.05 if source == "semantic-scholar" else 0.15) if interval is None else interval
        try:
            return self.rate_limiter.wait(source, key, interval, max_wait)
        except (RateLimitError, OSError) as exc:
            raise SourceError("Rate limit: " + str(exc), kind="rate_limited") from exc

    def cooldown(self, source, key, seconds):
        try:
            self.rate_limiter.cooldown(source, key, seconds)
        except (RateLimitError, OSError) as exc:
            raise SourceError("Cannot persist rate-limit cooldown: " + str(exc), kind="rate_limited") from exc

    @staticmethod
    def retry_after(headers):
        value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
        try:
            seconds = float(value)
            if math.isfinite(seconds):
                return max(0.0, seconds)
        except (ValueError, TypeError):
            pass
        try:
            date = email.utils.parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError, AttributeError):
            return 1.0

    def post_serper(self, mode, payload):
        from _serper import post
        return post(self, mode, payload)

    def contact_email(self, source):
        preferred = {"openalex": "OPENALEX_EMAIL", "crossref": "CROSSREF_EMAIL",
                     "unpaywall": "UNPAYWALL_EMAIL"}.get(source)
        names = [preferred, "OPENALEX_EMAIL", "CROSSREF_EMAIL", "UNPAYWALL_EMAIL", "POLITE_EMAIL"]
        return next((self.env.get(name, "").strip() for name in names if name and self.env.get(name, "").strip()), None)

    def get(self, source, path, params=None):
        params = dict(params or {})
        headers = {"User-Agent": "sanmu-paper-search/1.0 (metadata; Python stdlib)", "Accept": "application/json"}
        key = self.env.get("OPENALEX_API_KEY", "").strip() if source == "openalex" else None
        if source == "semantic-scholar":
            key = self.env.get("S2_API_KEY", "").strip() or self.env.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
            if key:
                headers["x-api-key"] = key
        polite_email = self.contact_email(source)
        if source == "openalex":
            if key:
                params["api_key"] = key
            elif polite_email:
                params["mailto"] = polite_email
                headers["User-Agent"] = f"sanmu-paper-search/1.0 (mailto:{polite_email})"
        if source == "crossref" and polite_email:
            params["mailto"] = polite_email
        if source == "unpaywall":
            if not polite_email:
                raise SourceError("UNPAYWALL_EMAIL or a fallback contact email is not configured")
            params["email"] = polite_email
        base = "https://api.semanticscholar.org" if source == "semantic-scholar" and path.startswith("/recommendations/") else BASE[source]
        url = base + path + (("?" + urlencode(params)) if params else "")
        waited = 0.0
        for attempt in range(3):
            try:
                waited += self.wait(source, key, max_wait=max(0.0, 10.0 - waited))
            except SourceError as exc:
                exc.status = 429
                raise
            status, response_headers, body = None, {}, None
            try:
                with self.opener(Request(url, headers=headers, method="GET"), timeout=25) as response:
                    status = response.status
                    response_headers = dict(response.headers)
                    payload = response.read(16 * 1024 * 1024 + 1)
                    if len(payload) > 16 * 1024 * 1024:
                        raise SourceError("Response exceeds 16 MiB limit", status)
                    body = payload.decode("utf-8")
            except HTTPError as exc:
                status = exc.code
                response_headers = dict(exc.headers or {})
                body = exc.read(1024 * 1024).decode("utf-8", errors="replace")
            except (URLError, OSError, TimeoutError, UnicodeDecodeError) as exc:
                detail = self.redactor.text(str(exc))
                self.raw.append({"provider": source, "url": self.redactor.text(url), "attempt": attempt + 1,
                                 "status": None, "error": detail, "retrieved_at": now()})
                raise SourceError("Network error: " + detail) from exc
            except SourceError as exc:
                self.raw.append({"provider": source, "url": self.redactor.text(url), "status": status,
                                 "error": str(exc), "retrieved_at": now()})
                raise
            try:
                data = json.loads(body)
                # NaN/Infinity and exponent overflow cannot enter a JSON run bundle.
                json.dumps(data, allow_nan=False)
            except (ValueError, TypeError):
                data = {"unparsed_body": body}
            self.raw.append(self.redactor.clean({"provider": source, "url": url, "attempt": attempt + 1,
                                                 "status": status, "body": data, "retrieved_at": now()}))
            if status in (429, 502, 503, 504):
                backoff = 2 ** attempt + random.uniform(0, 0.25)
                delay = max(self.retry_after(response_headers), backoff) if status == 429 else backoff
                if status == 429:
                    # Persist even on the final attempt or when this call cannot wait.
                    self.cooldown(source, key, delay)
                if attempt < 2:
                    if delay > 10.0 - waited:
                        raise SourceError(f"HTTP {status}; Retry-After/backoff exceeds bounded 10 second retry wait budget", status)
                    if status != 429:
                        self.sleep(delay)
                        waited += delay
                    continue
            if status is None or not 200 <= status < 300:
                raise SourceError(f"HTTP {status}", status)
            if not isinstance(data, dict) or "unparsed_body" in data:
                raise SourceError("Invalid JSON response", status)
            return data
        raise SourceError("Retry budget exhausted", 429)


def parse_id(value):
    value = value.strip()
    normalized = doi(value)
    if normalized:
        return "doi", normalized
    lowered = value.lower()
    patterns = {"openalex": r"W[0-9]+", "semantic_scholar": r"[A-Za-z0-9_-]+",
                "pmid": r"[0-9]+", "pmcid": r"(?:PMC)?[0-9]+", "corpus_id": r"[0-9]+",
                "arxiv": r"(?:[0-9]{4}\.[0-9]{4,5}|[A-Za-z][A-Za-z0-9.-]*/[0-9]{7})(?:v[0-9]+)?"}
    for prefix, kind in (("https://openalex.org/", "openalex"), ("openalex:", "openalex"),
                         ("semantic-scholar:", "semantic_scholar"), ("s2:", "semantic_scholar"),
                         ("pmid:", "pmid"), ("pmcid:", "pmcid"), ("arxiv:", "arxiv"), ("corpusid:", "corpus_id")):
        if lowered.startswith(prefix):
            identifier = value[len(prefix):]
            if not re.fullmatch(patterns[kind], identifier, re.I):
                break
            return kind, ids(**{kind: identifier})[kind][0]
    if re.fullmatch(patterns["openalex"], value, re.I):
        return "openalex", value.upper()
    if re.fullmatch(r"PMC[0-9]+", value, re.I):
        return "pmcid", value.upper()
    if re.fullmatch(r"[0-9]+", value):
        return "pmid", value
    raise InputError("Use a DOI, openalex:W..., s2:paperId, pmid:..., pmcid:..., arxiv:..., or corpusid:...")


def s2_id(identifier):
    kind, value = parse_id(identifier)
    prefix = {"doi": "DOI:", "semantic_scholar": "", "pmid": "PMID:", "arxiv": "ARXIV:", "corpus_id": "CorpusId:"}
    if kind not in prefix:
        raise SourceError("Semantic Scholar cannot resolve this ID type directly")
    return prefix[kind] + value


def oa_id(identifier):
    kind, value = parse_id(identifier)
    if kind == "openalex":
        return value.upper()
    if kind == "doi":
        return "https://doi.org/" + value
    if kind == "pmid":
        return "pmid:" + value
    if kind == "pmcid":
        return "pmcid:" + value
    raise SourceError("OpenAlex cannot resolve this ID type directly")


def plain_text(value):
    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, text):
            self.parts.append(text)

        def handle_starttag(self, tag, attrs):
            if tag in ("p", "br", "div", "jats:p", "jats:title"):
                self.parts.append(" ")

        def handle_endtag(self, tag):
            if tag in ("p", "div", "jats:p", "jats:title"):
                self.parts.append(" ")

    if not value:
        return None
    parser = TextParser()
    parser.feed(str(value))
    parser.close()
    return " ".join(html.unescape("".join(parser.parts)).split())


def normalize(source, data):
    if source == "openalex":
        all_ids = data.get("ids") or {}
        identifiers = ids(doi=data.get("doi") or all_ids.get("doi"), openalex=data.get("id"),
                          pmid=all_ids.get("pmid"), pmcid=all_ids.get("pmcid"))
        authors = []
        for entry in data.get("authorships") or []:
            author = entry.get("author") or {}
            authors.append({"name": author.get("display_name", ""),
                            "identifiers": ids(openalex=author.get("id"), orcid=author.get("orcid")),
                            "affiliations": entry.get("institutions") or [],
                            "raw_affiliation_strings": entry.get("raw_affiliation_strings") or []})
        abstract = None
        inverted = data.get("abstract_inverted_index")
        if isinstance(inverted, dict):
            words = [(pos, word) for word, positions in inverted.items() for pos in positions]
            abstract = " ".join(word for _, word in sorted(words))
        locations = []
        for loc in data.get("locations") or []:
            for url in dict.fromkeys([loc.get("pdf_url"), loc.get("landing_page_url")]):
                if url:
                    locations.append(location(url, source, loc.get("is_oa"), loc.get("license"),
                                              "provider_location", loc.get("version")))
        p = record(source, data.get("id"), data.get("display_name") or data.get("title"),
                   data.get("publication_year"), identifiers, authors, abstract, data.get("type"), locations)
        if data.get("is_authors_truncated"):
            p["authors_truncated"] = True
        p["oa_status"] = data.get("open_access") or {}
        primary_loc = data.get("primary_location") or {}
        src_obj = (primary_loc.get("source") if isinstance(primary_loc, dict) else None) or {}
        oa_venue = (src_obj.get("display_name") if isinstance(src_obj, dict) else None) or (
            (data.get("host_venue") or {}).get("display_name") if isinstance(data.get("host_venue"), dict) else None
        )
        if oa_venue:
            p["venue"] = oa_venue
            p["journal"] = oa_venue
        if isinstance(data.get("cited_by_count"), int):
            p["citation_count"] = data["cited_by_count"]
        if isinstance(data.get("referenced_works_count"), int):
            p["reference_count"] = data["referenced_works_count"]
        elif isinstance(data.get("referenced_works"), list):
            p["reference_count"] = len(data["referenced_works"])
        if data.get("publication_date"):
            p["publication_date"] = data["publication_date"]
        if isinstance(primary_loc, dict) and primary_loc.get("landing_page_url"):
            p["url"] = primary_loc["landing_page_url"]
        return p
    if source == "semantic-scholar":
        external = data.get("externalIds") or {}
        identifiers = ids(doi=external.get("DOI"), semantic_scholar=data.get("paperId"),
                          pmid=external.get("PubMed"), pmcid=external.get("PubMedCentral"),
                          arxiv=external.get("ArXiv"), corpus_id=data.get("corpusId") or external.get("CorpusId"))
        authors = [{"name": a.get("name", ""), "identifiers": ids(semantic_scholar=a.get("authorId"),
                    orcid=(a.get("externalIds") or {}).get("ORCID")),
                    "affiliations": a.get("affiliations") or []} for a in data.get("authors") or []]
        pdf = data.get("openAccessPdf") or {}
        locations = [location(pdf["url"], source, data.get("isOpenAccess"), pdf.get("license"),
                              "openAccessPdf", pdf.get("status"))] if pdf.get("url") else []
        for arx_id in identifiers.get("arxiv", []):
            arx_pdf = f"https://arxiv.org/pdf/{arx_id}.pdf"
            if not any(loc.get("url") == arx_pdf for loc in locations):
                locations.append(location(arx_pdf, source, True, "arxiv", "arxiv_pdf", "submittedVersion"))
        p = record(source, data.get("paperId"), data.get("title"), data.get("year"), identifiers,
                   authors, data.get("abstract"), ",".join(data.get("publicationTypes") or []) or None, locations)
        p["oa_status"] = {"is_oa": data.get("isOpenAccess")}
        for field, remote in (("url", "url"), ("venue", "venue"), ("journal", "journal"),
                              ("citation_count", "citationCount"), ("reference_count", "referenceCount"),
                              ("publication_date", "publicationDate"), ("tldr", "tldr")):
            if remote in data:
                p[field] = data[remote]
        return p
    titles = data.get("title") or []
    inst_re = re.compile(r"\b(?:university|institute|school of|department of|college of|laboratory|academy of|faculty of|ministry of|center for|centre for|hospital)\b", re.I)
    authors = []
    for a in data.get("author") or []:
        given_family = " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
        org_name = (a.get("name") or "").strip()
        if not given_family and org_name and (inst_re.search(org_name) or len(org_name) > 60):
            continue
        final_name = given_family or org_name
        if final_name:
            authors.append({"name": final_name, "identifiers": ids(orcid=a.get("ORCID")), "affiliations": a.get("affiliation") or []})
    date = data.get("published") or data.get("issued") or data.get("published-online") or {}
    parts = date.get("date-parts") or [[]]
    year = parts[0][0] if parts and parts[0] else None
    relations = [{"type": kind, "provider": source, **relation} for kind, entries in (data.get("relation") or {}).items()
                 for relation in entries if isinstance(relation, dict)]
    # Crossref links and license URLs are metadata, not a positive OA assertion.
    locations = [location(link["URL"], source, None, None, "crossref_link")
                 for link in data.get("link") or [] if link.get("URL")]
    p = record(source, data.get("DOI"), plain_text(titles[0] if isinstance(titles, list) and titles else str(titles)),
               year, ids(doi=data.get("DOI")), authors, plain_text(data.get("abstract")), data.get("type"), locations, relations)
    p["licenses"] = data.get("license") or []
    container = data.get("container-title") or []
    cr_venue = plain_text(container[0]) if isinstance(container, list) and container else None
    if cr_venue:
        p["venue"] = cr_venue
        p["journal"] = cr_venue
    if isinstance(data.get("is-referenced-by-count"), int):
        p["citation_count"] = data["is-referenced-by-count"]
    if isinstance(data.get("references-count"), int):
        p["reference_count"] = data["references-count"]
    if data.get("URL"):
        p["url"] = data["URL"]
    return p


class Providers:
    def __init__(self, client):
        self.client = client

    def _page(self, source, path, params, papers, pages, rows_key=None, nested_key=None):
        """Fetch, validate and normalize a whole page before committing any rows."""
        try:
            data = self.client.get(source, path, params)
            if not isinstance(data, dict):
                raise SourceError("Page response must be an object")
            envelope = data
            if source == "crossref":
                envelope = data.get("message")
                if not isinstance(envelope, dict):
                    raise SourceError("Crossref page has no message object")
            if source == "openalex" and not isinstance(data.get("meta", {}), dict):
                raise SourceError("OpenAlex page metadata must be an object")
            key = rows_key or {"openalex": "results", "crossref": "items", "semantic-scholar": "data"}[source]
            entries = envelope.get(key)
            if source == "semantic-scholar" and nested_key == "citedPaper" and entries is None:
                info = data.get("citingPaperInfo")
                if isinstance(info, dict):
                    pdf = info.get("openAccessPdf")
                    disclaimer = info.get("disclaimer") or (pdf.get("disclaimer") if isinstance(pdf, dict) else None)
                    if "elided" in str(disclaimer or "").lower():
                        raise SourceError("publisher_restricted: publisher has elided this paper's references",
                                          200, kind="publisher_restricted")
            if not isinstance(entries, list):
                raise SourceError("Page response has no results array: " + key)
            normalized = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SourceError("Page result must be an object")
                if nested_key:
                    if nested_key not in entry:
                        raise SourceError("Related row is missing " + nested_key)
                    entry = entry[nested_key]
                    if entry is None:  # Provider can expose an unavailable citation target.
                        continue
                    if not isinstance(entry, dict):
                        raise SourceError("Related paper must be an object or null")
                normalized.append(normalize(source, entry))
            return data, normalized
        except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            error = exc if isinstance(exc, SourceError) else SourceError("Malformed source page: " + str(exc))
            error.papers = list(papers)
            error.pages = pages
            if error is exc:
                raise
            raise error from exc

    def lookup(self, source, identifier):
        if source == "openalex":
            data = self.client.get(source, "/works/" + quote(oa_id(identifier), safe=""))
        elif source == "semantic-scholar":
            data = self.client.get(source, "/paper/" + quote(s2_id(identifier), safe=""), {"fields": S2_FIELDS})
        else:
            kind, value = parse_id(identifier)
            if kind != "doi":
                raise SourceError("Crossref lookup requires a DOI")
            data = self.client.get(source, "/works/" + quote(value, safe="")).get("message")
            if not isinstance(data, dict):
                raise SourceError("Crossref response has no work message")
        paper = normalize(source, data)
        kind, value = parse_id(identifier)
        values = paper["identifiers"].get(kind, [])
        if not any(v.casefold() == value.casefold() for v in values):
            raise SourceError("Exact identifier mismatch in provider response")
        return [paper], {"status": "ok", "fetched": 1, "pages": 1, "truncated": False, "id_verified": True}

    def _year_params(self, source, year_from, year_to):
        if year_from is None and year_to is None:
            return {}
        if source == "semantic-scholar":
            return {"year": f"{year_from or ''}-{year_to or ''}"}
        if source == "openalex":
            filters = ([f"from_publication_date:{year_from}-01-01"] if year_from else [])
            filters += [f"to_publication_date:{year_to}-12-31"] if year_to else []
        else:
            filters = ([f"from-pub-date:{year_from}-01-01"] if year_from else [])
            filters += [f"until-pub-date:{year_to}-12-31"] if year_to else []
        return {"filter": ",".join(filters)}

    def search(self, source, query, limit, year_from=None, year_to=None):
        years = self._year_params(source, year_from, year_to)
        papers, cursor, offset, pages = [], "*", 0, 0
        total, truncated = None, False
        while len(papers) < limit and pages < 4:
            size = min(20 if source == "semantic-scholar" else 10, limit - len(papers))
            if source == "openalex":
                data, normalized = self._page(source, "/works", {"search": query, "per-page": size, "cursor": cursor, **years}, papers, pages)
                entries = data.get("results")
                total = (data.get("meta") or {}).get("count")
                following = (data.get("meta") or {}).get("next_cursor")
            elif source == "semantic-scholar":
                data, normalized = self._page(source, "/paper/search", {"query": query, "limit": size, "offset": offset, "fields": S2_FIELDS, **years}, papers, pages)
                entries, total, following = data.get("data"), data.get("total"), data.get("next")
            else:
                data, normalized = self._page(source, "/works", {"query.title": query, "sort": "score", "order": "desc", "rows": size, "cursor": cursor, **years}, papers, pages)
                message = data.get("message") or {}
                entries, total, following = message.get("items"), message.get("total-results"), message.get("next-cursor")
            if not isinstance(entries, list):
                raise SourceError("Search response has no results array")
            papers.extend(normalized[:size])
            pages += 1
            more = total > len(papers) if isinstance(total, (int, float)) else (
                following is not None if source == "semantic-scholar" else bool(following) and len(entries) == size)
            truncated = bool(more)
            if not entries or not more or following is None or following == cursor or following == offset:
                break
            if source == "semantic-scholar":
                offset = following
            else:
                cursor = following
        return papers, {"status": "ok", "fetched": len(papers), "pages": pages, "total": total,
                        "truncated": truncated, "limit": limit,
                        "query_strategy": "title_only" if source == "crossref" else "provider_search"}

    def authors(self, source, query, author_id, limit, year_from=None, year_to=None):
        if not author_id:
            if source == "openalex":
                data = self.client.get(source, "/authors", {"search": query, "per-page": limit})
                entries = data.get("results")
                total = (data.get("meta") or {}).get("count")
                candidates = [{"provider": source, "author_id": a.get("id"), "name": a.get("display_name"),
                               "orcid": a.get("orcid"), "affiliations": a.get("last_known_institutions") or [],
                               "paper_count": a.get("works_count")} for a in entries or []]
            else:
                data = self.client.get(source, "/author/search", {"query": query, "limit": limit, "fields": S2_AUTHOR_FIELDS})
                entries, total = data.get("data"), data.get("total")
                candidates = [{"provider": source, "author_id": a.get("authorId"), "name": a.get("name"),
                               "affiliations": a.get("affiliations") or [], "paper_count": a.get("paperCount"),
                               "citation_count": a.get("citationCount"), "url": a.get("url")}
                              for a in entries or []]
            if not isinstance(entries, list):
                raise SourceError("Author response has no candidates array")
            return [], {"status": "ok", "fetched": len(candidates), "pages": 1, "total": total,
                        "truncated": total > len(candidates) if isinstance(total, int) else None}, candidates
        author_id = author_id.rstrip("/").rsplit("/", 1)[-1]
        import re
        if source == "openalex" and not re.fullmatch(r"A\d+", author_id, re.I):
            raise InputError("OpenAlex author ID must be A followed by digits")
        if source == "semantic-scholar" and not author_id.isdigit():
            raise InputError("Semantic Scholar author ID must be numeric")
        papers, pages, cursor, offset, total, more = [], 0, "*", 0, None, False
        while len(papers) < limit and pages < 4:
            size = min(20 if source == "semantic-scholar" else 10, limit - len(papers))
            if source == "openalex":
                year_filter = self._year_params(source, year_from, year_to).get("filter")
                filters = "author.id:" + author_id + (("," + year_filter) if year_filter else "")
                data, normalized = self._page(source, "/works", {"filter": filters,
                                                          "per-page": size, "cursor": cursor}, papers, pages)
                entries = data.get("results")
                total = (data.get("meta") or {}).get("count")
                following = (data.get("meta") or {}).get("next_cursor")
            else:
                date_filter = ({"publicationDateOrYear": f"{year_from or ''}:{year_to or ''}"}
                               if year_from is not None or year_to is not None else {})
                data, normalized = self._page(source, "/author/" + quote(author_id, safe="") + "/papers",
                                       {"fields": S2_BASE_FIELDS, "limit": size, "offset": offset, **date_filter}, papers, pages)
                entries, following = data.get("data"), data.get("next")
            if not isinstance(entries, list):
                raise SourceError("Author papers response has no results array")
            papers.extend(normalized[:size])
            pages += 1
            more = total > len(papers) if isinstance(total, int) else following is not None
            if not entries or not more or following is None or following in (cursor, offset):
                break
            cursor, offset = following, following
        return papers, {"status": "ok", "fetched": len(papers), "pages": pages, "total": total,
                        "truncated": bool(more), "author_id": author_id}, []

    def _extract_seed_doi(self, identifier):
        kind, value = parse_id(identifier)
        if kind == "doi":
            return value
        # Raw audit history can contain unrelated papers; resolve this seed explicitly.
        try:
            papers, _ = self.lookup("semantic-scholar", identifier)
            return next(iter(papers[0]["identifiers"].get("doi", [])), None)
        except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError):
            return None

    def _crossref_references_fallback(self, seed_doi, limit):
        msg = self.client.get("crossref", "/works/" + quote(seed_doi, safe="")).get("message")
        if not isinstance(msg, dict):
            return []
        raw_refs = msg.get("reference")
        if not isinstance(raw_refs, list) or not raw_refs:
            return []
        ref_dois = []
        for r in raw_refs:
            if isinstance(r, dict) and r.get("DOI"):
                d = doi(r.get("DOI"))
                if d and d not in ref_dois:
                    ref_dois.append(d)
                if len(ref_dois) >= limit:
                    break
        oa_by_doi = {}
        if ref_dois:
            try:
                oa_data = self.client.get("openalex", "/works", {
                    "filter": "doi:" + "|".join("https://doi.org/" + d for d in ref_dois[:limit]),
                    "per-page": min(20, len(ref_dois[:limit]))
                })
                for item in oa_data.get("results") or []:
                    if isinstance(item, dict):
                        p_norm = normalize("openalex", item)
                        for d_val in p_norm.get("identifiers", {}).get("doi", []):
                            oa_by_doi[d_val.lower()] = p_norm
            except Exception:
                pass
        papers = []
        for idx, r in enumerate(raw_refs):
            if len(papers) >= limit:
                break
            if not isinstance(r, dict):
                continue
            r_doi = doi(r.get("DOI"))
            if r_doi and r_doi.lower() in oa_by_doi:
                papers.append(oa_by_doi[r_doi.lower()])
                continue
            title = plain_text(r.get("article-title") or r.get("volume-title") or r.get("unstructured") or (f"Reference ({r_doi})" if r_doi else ""))
            if not title and not r_doi:
                continue
            yr_match = re.search(r"\b(19|20)\d{2}\b", str(r.get("year") or ""))
            yr = int(yr_match[0]) if yr_match else None
            author_name = plain_text(r.get("author"))
            authors = [{"name": author_name, "identifiers": {}, "affiliations": []}] if author_name else []
            p = record("crossref", r_doi or r.get("key") or f"ref-{idx+1}", title or r_doi, yr,
                       ids(doi=r_doi) if r_doi else {}, authors, None, "reference-entry", [])
            venue = plain_text(r.get("journal-title") or r.get("series-title"))
            if venue:
                p["venue"] = venue
                p["journal"] = venue
            if r_doi:
                p["url"] = "https://doi.org/" + r_doi
            papers.append(p)
        return papers

    def related(self, source, identifier, relation, limit):
        if source == "crossref":
            raise SourceError("Crossref is not a supported related provider")
        if source == "openalex":
            seed = self.client.get(source, "/works/" + quote(oa_id(identifier), safe=""))
            seed_id = (seed.get("id") or "").rsplit("/", 1)[-1]
            if not seed_id:
                raise SourceError("OpenAlex seed has no stable ID")
            if relation in ("references", "recommendations"):
                refs = seed.get("referenced_works" if relation == "references" else "related_works")
                if not isinstance(refs, list):
                    raise SourceError("OpenAlex response lacks requested relation")
                target_refs = refs[:limit]
                by_oa, requests = {}, 1
                short_ids = [parse_id(ref)[1] for ref in target_refs]
                if len(short_ids) > 1:
                    requests += 1
                    try:
                        batch_data = self.client.get(source, "/works", {
                            "filter": "openalex:" + "|".join(short_ids), "per-page": len(short_ids)})
                        for entry in batch_data.get("results") or []:
                            paper = normalize(source, entry)
                            for stable_id in paper["identifiers"].get("openalex", []):
                                if stable_id in short_ids:
                                    by_oa[stable_id] = paper
                    except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError):
                        pass  # Individual lookups below recover all missing batch targets.
                papers, failures = [], []
                for ref, stable_id in zip(target_refs, short_ids):
                    try:
                        if stable_id not in by_oa:
                            requests += 1
                            found, _ = self.lookup(source, stable_id)
                            by_oa[stable_id] = found[0]
                        papers.append(by_oa[stable_id])
                    except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                        failures.append({"id": ref, "error": str(exc), "http_status": getattr(exc, "status", None)})
                status = {"status": "error" if failures else "ok", "fetched": len(papers), "pages": requests,
                          "total": len(refs), "truncated": len(refs) > limit, "relation": relation}
                if failures:
                    status.update(error="Some related records failed", failures=failures)
                return papers, status
            papers, cursor, pages, total, more = [], "*", 0, None, False
            while len(papers) < limit and pages < 4:
                size = min(20 if source == "semantic-scholar" else 10, limit - len(papers))
                data, normalized = self._page(source, "/works", {"filter": "cites:" + seed_id, "per-page": size, "cursor": cursor}, papers, pages)
                entries, meta = data.get("results"), data.get("meta") or {}
                if not isinstance(entries, list):
                    raise SourceError("Citation response has no results array")
                papers.extend(normalized[:size])
                pages += 1
                total, following = meta.get("count"), meta.get("next_cursor")
                more = total > len(papers) if isinstance(total, int) else bool(following)
                if not entries or not more or not following or following == cursor:
                    break
                cursor = following
            return papers, {"status": "ok", "fetched": len(papers), "pages": pages + 1,
                            "total": total, "truncated": bool(more), "relation": relation}
        try:
            paper_id = s2_id(identifier)
            if relation == "recommendations":
                # Recommendations has its own public GET API, not Graph /paper paths.
                if re.fullmatch(r"[0-9a-f]{40}", paper_id, re.I):
                    canonical = paper_id
                else:
                    canonical = self.client.get(source, "/paper/" + quote(paper_id, safe=""), {"fields": "paperId"}).get("paperId")
                if not canonical:
                    raise SourceError("Semantic Scholar seed has no paperId")
                data, normalized = self._page(source, "/recommendations/v1/papers/forpaper/" + quote(canonical, safe=""),
                                       {"limit": limit, "fields": S2_RECOMMENDATION_FIELDS}, [], 0, rows_key="recommendedPapers")
                entries = data["recommendedPapers"]
                return normalized[:limit], {"status": "ok", "fetched": min(len(entries), limit),
                        "pages": 1, "truncated": len(entries) >= limit, "relation": relation, "total": None}
            papers, offset, pages, more = [], 0, 0, False
            while len(papers) < limit and pages < 4:
                size = min(20 if source == "semantic-scholar" else 10, limit - len(papers))
                key = "citedPaper" if relation == "references" else "citingPaper"
                data, normalized = self._page(source, "/paper/" + quote(paper_id, safe="") + "/" + relation,
                                       {"fields": S2_BASE_FIELDS, "limit": size, "offset": offset}, papers, pages, nested_key=key)
                entries = data["data"]
                papers.extend(normalized[:size])
                papers = papers[:limit]
                pages += 1
                following = data.get("next")
                more = following is not None
                if not entries or not more or following == offset:
                    break
                offset = following
            return papers, {"status": "ok", "fetched": len(papers), "pages": pages, "truncated": more,
                            "relation": relation, "total": None}
        except SourceError as exc:
            if self.client.opener is not urlopen:
                raise
            partial = list(getattr(exc, "papers", []) or [])
            if exc.kind == "publisher_restricted" or exc.status in (429, 502, 503, 504):
                seed_doi = self._extract_seed_doi(identifier)
                fallback_papers = []
                fallback_via = None
                fallback_status = None
                if relation == "references" and seed_doi:
                    try:
                        fallback_papers = self._crossref_references_fallback(seed_doi, limit - len(partial))
                        if fallback_papers:
                            fallback_via = "crossref+openalex"
                    except Exception:
                        pass
                if not fallback_papers:
                    try:
                        oa_target = ("https://doi.org/" + seed_doi) if seed_doi else identifier
                        oa_papers, fallback_status = self.related("openalex", oa_target, relation, limit - len(partial))
                        if oa_papers:
                            fallback_papers = oa_papers
                            fallback_via = "openalex"
                    except Exception:
                        pass
                combined = deduplicate(partial + fallback_papers)
                if fallback_papers:
                    return combined[:limit], {
                        "status": "error",
                        "error": str(exc), "http_status": exc.status,
                        "reason": exc.kind or "source_unavailable",
                        "fetched": len(combined[:limit]),
                        "pages": getattr(exc, "pages", 0),
                        "truncated": True,
                        "relation": relation,
                        "total": None,
                        "fallback_used": fallback_via,
                        "fallback_status": fallback_status,
                        "note": "Original Semantic Scholar retrieval failed; alternate-source results retained."
                    }
            raise

    def unpaywall(self, identifier):
        data = self.client.get("unpaywall", "/" + quote(identifier, safe=""))
        if doi(data.get("doi")) != doi(identifier):
            raise SourceError("Unpaywall DOI mismatch")
        result = []
        for loc in data.get("oa_locations") or []:
            for url in dict.fromkeys([loc.get("url_for_pdf"), loc.get("url_for_landing_page"), loc.get("url")]):
                if url:
                    result.append(location(url, "unpaywall", data.get("is_oa"), loc.get("license"),
                                           loc.get("evidence") or "unpaywall_location", loc.get("version")))
        return result
