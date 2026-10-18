"""Public GET adapters. No SDK, database, full-text fetching or hidden fallbacks."""
from __future__ import annotations

import email.utils
import html
from html.parser import HTMLParser
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from _core import InputError, Redactor, doi, ids, location, now, record

BASE = {"openalex": "https://api.openalex.org", "semantic-scholar": "https://api.semanticscholar.org/graph/v1",
        "crossref": "https://api.crossref.org", "unpaywall": "https://api.unpaywall.org/v2",
        "arxiv": "https://export.arxiv.org", "europe-pmc": "https://www.ebi.ac.uk/europepmc/webservices/rest"}
MAX_BODY = 16 * 1024 * 1024


def polite_email(env):
    return (env.get("OPENALEX_EMAIL") or env.get("CROSSREF_EMAIL")
            or env.get("UNPAYWALL_EMAIL") or env.get("POLITE_EMAIL"))


def unpaywall_email(env):
    return env.get("UNPAYWALL_EMAIL") or polite_email(env)

S2_FIELDS = "paperId,corpusId,externalIds,title,year,authors,abstract,publicationTypes,isOpenAccess,openAccessPdf,url"
S2_AUTHOR_FIELDS = "name,authorId,affiliations,paperCount,citationCount,url"


class SourceError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class UnsupportedSource(SourceError):
    """The requested provider cannot perform this operation or resolve this ID."""


class Client:
    def __init__(self, env=None, opener=None, sleep=None, clock=None):
        self.env = os.environ if env is None else env
        self.redactor = Redactor(self.env)
        self.opener = opener or urlopen
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.last = {}
        self.raw = []

    def get(self, source, path, params=None):
        params = dict(params or {})
        headers = {"User-Agent": "sanmu-paper-search/1.0 (metadata; Python stdlib)", "Accept": "application/json"}
        if source == "semantic-scholar":
            key = self.env.get("S2_API_KEY") or self.env.get("SEMANTIC_SCHOLAR_API_KEY")
            if key:
                headers["x-api-key"] = key
        contact = polite_email(self.env)
        if source == "arxiv":
            headers["Accept"] = "application/atom+xml"
        if source == "openalex":
            if self.env.get("OPENALEX_API_KEY"):
                params["api_key"] = self.env["OPENALEX_API_KEY"]
            elif contact:
                params["mailto"] = contact
                headers["User-Agent"] = f"sanmu-paper-search/1.0 (mailto:{contact})"
        if source == "crossref" and (self.env.get("CROSSREF_EMAIL") or contact):
            params["mailto"] = self.env.get("CROSSREF_EMAIL") or contact
        if source == "unpaywall":
            email = unpaywall_email(self.env)
            if not email:
                raise SourceError("Unpaywall requires UNPAYWALL_EMAIL or a polite email fallback")
            params["email"] = email
        base = "https://api.semanticscholar.org" if source == "semantic-scholar" and path.startswith("/recommendations/") else BASE[source]
        url = base + path + (("?" + urlencode(params)) if params else "")
        for attempt in range(3):
            interval = 3.0 if source == "arxiv" else 1.05 if source == "semantic-scholar" else 0.15
            if source in self.last:
                self.sleep(max(0, interval - (self.clock() - self.last[source])))
            self.last[source] = self.clock()
            status, response_headers, body = None, {}, None
            try:
                with self.opener(Request(url, headers=headers, method="GET"), timeout=25) as response:
                    status = response.status
                    response_headers = dict(response.headers)
                    payload = response.read(MAX_BODY + 1)
                    if len(payload) > MAX_BODY:
                        raise SourceError("Response exceeds 16 MiB limit", status)
                    body = payload.decode("utf-8", errors="replace")
            except HTTPError as exc:
                status = exc.code
                response_headers = dict(exc.headers or {})
                body = exc.read(1024 * 1024).decode("utf-8", errors="replace")
            except (URLError, OSError, TimeoutError) as exc:
                detail = self.redactor.text(str(exc))
                self.raw.append({"provider": source, "url": self.redactor.text(url), "attempt": attempt + 1,
                                 "status": None, "error": detail, "retrieved_at": now()})
                raise SourceError("Network error: " + detail) from exc
            except SourceError as exc:
                self.raw.append({"provider": source, "url": self.redactor.text(url), "status": status,
                                 "error": str(exc), "retrieved_at": now()})
                raise
            try:
                data = {"xml": body} if source == "arxiv" else json.loads(body)
            except (ValueError, TypeError):
                data = {"unparsed_body": body}
            self.raw.append(self.redactor.clean({"provider": source, "url": url, "attempt": attempt + 1,
                                                 "status": status, "body": data, "retrieved_at": now()}))
            if status == 429 and attempt < 2:
                retry = next((v for k, v in response_headers.items() if k.lower() == "retry-after"), "1")
                try:
                    delay = max(0.0, float(retry))
                except (ValueError, TypeError):
                    try:
                        delay = max(0, (email.utils.parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        delay = 1.0
                if delay > 10:
                    raise SourceError("HTTP 429; Retry-After exceeds bounded 10 second retry budget", 429)
                self.sleep(delay)
                continue
            if status is None or status >= 400:
                raise SourceError(f"HTTP {status}", status)
            if not isinstance(data, dict) or "unparsed_body" in data:
                raise SourceError("Invalid JSON response", status)
            return parse_arxiv_feed(body) if source == "arxiv" else data
        raise SourceError("Retry budget exhausted", 429)


def arxiv_id(value):
    value = re.sub(r"^(?:arxiv:\s*|https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/)", "", value.strip(), flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v[1-9]\d*)?", value, re.I):
        raise InputError("Invalid arXiv identifier")
    return value


def parse_arxiv_feed(body):
    ns = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/",
          "x": "http://arxiv.org/schemas/atom"}
    try:
        # Atom metadata never needs a DTD; reject entity declarations before parsing.
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", body, re.I):
            raise ValueError("DTD/entity declarations are not supported")
        root = ET.fromstring(body)
        if root.tag != "{" + ns["a"] + "}feed":
            raise ValueError("Expected an Atom feed")
        rows = []
        for entry in root.findall("a:entry", ns):
            text = lambda key: " ".join((entry.findtext(key, default="", namespaces=ns)).split())
            identifier = text("a:id")
            if identifier.endswith("/api/errors") or text("a:title").casefold() == "error":
                raise ValueError("arXiv API error: " + text("a:summary"))
            identifier = arxiv_id(identifier)
            rows.append({"id": identifier, "title": text("a:title"), "summary": text("a:summary"),
                         "published": text("a:published"), "updated": text("a:updated"),
                         "doi": text("x:doi"), "journal_ref": text("x:journal_ref"),
                         "license": text("x:license"),
                         "authors": [{"name": " ".join((a.findtext("a:name", "", ns)).split()),
                                      "affiliations": [v.text or "" for v in a.findall("x:affiliation", ns)]}
                                     for a in entry.findall("a:author", ns)],
                         "links": [dict(link.attrib) for link in entry.findall("a:link", ns)]})
        total = root.findtext("o:totalResults", namespaces=ns)
        total = int(total) if total is not None else None
        if total is not None and total < 0:
            raise ValueError("Negative result count")
        return {"results": rows, "total": total}
    except (ET.ParseError, ValueError, TypeError) as exc:
        raise SourceError("Invalid arXiv feed: " + str(exc)) from exc


def parse_id(value):
    value = value.strip()
    normalized = doi(value)
    if normalized:
        return "doi", normalized
    lowered = value.lower()
    if lowered.startswith(("arxiv:", "http://arxiv.org/", "https://arxiv.org/",
                           "http://export.arxiv.org/", "https://export.arxiv.org/")) or re.fullmatch(
            r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?", value, re.I):
        return "arxiv", arxiv_id(value)
    if re.fullmatch(r"(?:pmcid:)?PMC\d+", value, re.I):
        return "pmcid", value.split(":")[-1].upper()
    if lowered.startswith("pmid:"):
        if not value[5:].isdigit():
            raise InputError("PMID must contain digits only")
        return "pmid", value[5:]
    for prefix, kind in (("https://openalex.org/", "openalex"), ("openalex:", "openalex"),
                         ("semantic-scholar:", "semantic_scholar"), ("s2:", "semantic_scholar"),
                         ("corpusid:", "corpus_id")):
        if lowered.startswith(prefix):
            identifier = value[len(prefix):]
            if not identifier or any(c.isspace() for c in identifier):
                break
            return kind, identifier
    if value.startswith("W") and value[1:].isdigit():
        return "openalex", value
    raise InputError("Use a DOI, openalex:W..., s2:paperId, pmid:..., pmcid:PMC..., arxiv:..., or corpusid:...")


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
    if source == "arxiv":
        identifier = arxiv_id(data["id"])
        year = int(data["published"][:4]) if re.match(r"\d{4}-", data.get("published", "")) else None
        authors = [{"name": a["name"], "identifiers": {}, "affiliations": a["affiliations"]}
                   for a in data.get("authors", [])]
        locations = [location(link["href"], source, True, data.get("license"),
                              "arxiv_repository; free_to_read", "submittedVersion")
                     for link in data.get("links", []) if link.get("href") and
                     (link.get("rel") == "alternate" or link.get("title") == "pdf" or link.get("type") == "application/pdf")]
        p = record(source, identifier, data.get("title"), year, ids(arxiv=identifier), authors,
                   data.get("summary"), "preprint", locations)
        # arXiv's DOI field points to a related publication, not the identity of this preprint.
        if doi(data.get("doi")):
            p["relations"].append({"type": "published_version", "provider": source,
                                   "target_identifiers": ids(doi=data["doi"]), "evidence": "arxiv:doi"})
        p["versions"] = [{"provider": source, "arxiv_id": identifier,
                          "version": (re.search(r"v\d+$", identifier) or [None])[0],
                          "published": data.get("published"), "updated": data.get("updated"),
                          "journal_ref": data.get("journal_ref") or None}]
        return p
    if source == "europe-pmc":
        identifiers = ids(doi=data.get("doi"), pmid=data.get("pmid") or
                          (data.get("id") if data.get("source") == "MED" else None), pmcid=data.get("pmcid"))
        authors = [{"name": a.get("fullName") or " ".join(filter(None, [a.get("firstName"), a.get("lastName")])),
                    "identifiers": ids(orcid=(a.get("authorId") or {}).get("value")
                                       if (a.get("authorId") or {}).get("type") == "ORCID" else None),
                    "affiliations": [v.get("affiliation", "") for v in
                                     (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", [])]}
                   for a in (data.get("authorList") or {}).get("author", [])]
        if not authors and data.get("authorString"):
            authors = [{"name": data["authorString"], "identifiers": {}, "affiliations": [],
                        "unparsed_author_string": True}]
        year = str(data.get("pubYear") or "")
        year = int(year) if re.fullmatch(r"\d{4}", year) else None
        types = (data.get("pubTypeList") or {}).get("pubType", [])
        if isinstance(types, str):
            types = [types]
        license = data.get("license")
        locations = []
        for link in (data.get("fullTextUrlList") or {}).get("fullTextUrl", []):
            if link.get("url"):
                free = str(link.get("availabilityCode") or "").upper() == "F"
                locations.append(location(link["url"], source, True if free or data.get("isOpenAccess") == "Y" else None,
                                          link.get("license") or license, "europe_pmc_fulltext; " +
                                          str(link.get("availability") or "availability_unknown"), link.get("version")))
        p = record(source, str(data.get("source", "")) + ":" + str(data.get("id", "")),
                   plain_text(data.get("title")), year, identifiers, authors,
                   plain_text(data.get("abstractText")), ",".join(types) or None, locations)
        p["licenses"] = [license] if license else []
        p["oa_status"] = {"is_oa": {"Y": True, "N": False}.get(data.get("isOpenAccess")),
                          "in_pmc": data.get("inPMC"), "has_pdf": data.get("hasPDF")}
        p["versions"] = [{"provider": source, "source": data.get("source"),
                          "version": data.get("versionNumber"), "first_publication_date": data.get("firstPublicationDate")}]
        return p
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
        p = record(source, data.get("paperId"), data.get("title"), data.get("year"), identifiers,
                   authors, data.get("abstract"), ",".join(data.get("publicationTypes") or []) or None, locations)
        p["oa_status"] = {"is_oa": data.get("isOpenAccess")}
        return p
    titles = data.get("title") or []
    authors = [{"name": a.get("name") or " ".join(filter(None, [a.get("given"), a.get("family")])),
                "identifiers": ids(orcid=a.get("ORCID")), "affiliations": a.get("affiliation") or []}
               for a in data.get("author") or []]
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
            if source == "europe-pmc":
                envelope = data.get("resultList")
                if not isinstance(envelope, dict):
                    raise SourceError("Europe PMC page has no resultList object")
            key = rows_key or {"openalex": "results", "crossref": "items", "semantic-scholar": "data",
                               "arxiv": "results", "europe-pmc": "result"}[source]
            entries = envelope.get(key)
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
        kind, value = parse_id(identifier)
        supported = {"openalex": {"doi", "openalex", "pmid"},
                     "semantic-scholar": {"doi", "semantic_scholar", "pmid", "arxiv", "corpus_id"},
                     "crossref": {"doi"}, "arxiv": {"arxiv"}, "europe-pmc": {"doi", "pmid", "pmcid"}}
        if source not in supported or kind not in supported[source]:
            raise UnsupportedSource(f"{source} lookup does not support {kind} identifiers")
        if source in ("arxiv", "europe-pmc"):
            if source == "arxiv":
                _, found = self._page(source, "/api/query", {"id_list": value, "max_results": 1}, [], 0)
            else:
                query = {"doi": f'DOI:"{value}"', "pmid": f'EXT_ID:{value} AND SRC:MED',
                         "pmcid": f'PMCID:{value}'}[kind]
                _, found = self._page(source, "/search", {"query": query, "format": "json",
                                                            "resultType": "core", "pageSize": 2}, [], 0)
            if not found:
                return [], {"status": "not_found", "fetched": 0, "pages": 1, "truncated": False}
            def matches(paper):
                values = paper["identifiers"].get(kind, [])
                if kind == "arxiv" and not re.search(r"v\d+$", value, re.I):
                    return any(re.sub(r"v\d+$", "", v, flags=re.I).casefold() == value.casefold() for v in values)
                return any(v.casefold() == value.casefold() for v in values)
            found = [p for p in found if matches(p)]
            if not found:
                raise SourceError("Exact identifier mismatch in provider response")
            return found, {"status": "ok", "fetched": len(found), "pages": 1, "truncated": False, "id_verified": True}
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
            return {"year": f"{year_from or ''}:{year_to or ''}"}
        if source == "openalex":
            filters = ([f"from_publication_date:{year_from}-01-01"] if year_from else [])
            filters += [f"to_publication_date:{year_to}-12-31"] if year_to else []
        else:
            filters = ([f"from-pub-date:{year_from}-01-01"] if year_from else [])
            filters += [f"until-pub-date:{year_to}-12-31"] if year_to else []
        return {"filter": ",".join(filters)}

    def _search_repository(self, source, query, limit, year_from, year_to):
        if source == "arxiv":
            if year_from is not None or year_to is not None:
                query = f"({query}) AND submittedDate:[{year_from or 1000}01010000 TO {year_to or 9999}12312359]"
        elif year_from is not None or year_to is not None:
            query = f"({query}) AND FIRST_PDATE:[{year_from or 1000}-01-01 TO {year_to or 9999}-12-31]"
        papers, pages, offset, cursor, total, more = [], 0, 0, "*", None, False
        while len(papers) < limit and pages < 4:
            size = min(10, limit - len(papers))
            if source == "arxiv":
                data, normalized = self._page(source, "/api/query", {"search_query": query, "start": offset,
                    "max_results": size, "sortBy": "relevance", "sortOrder": "descending"}, papers, pages)
                entries, total, following = data["results"], data.get("total"), None
            else:
                data, normalized = self._page(source, "/search", {"query": query, "format": "json",
                    "resultType": "core", "pageSize": size, "cursorMark": cursor}, papers, pages)
                entries, total, following = data["resultList"]["result"], data.get("hitCount"), data.get("nextCursorMark")
            papers.extend(normalized[:size])
            pages += 1
            offset += len(entries)
            more = total > offset if isinstance(total, int) else len(entries) == size
            if not entries:
                break
            if not more or (source == "europe-pmc" and (not following or following == cursor)):
                break
            cursor = following
        return papers, {"status": "ok", "fetched": len(papers), "pages": pages, "total": total,
                        "truncated": bool(more), "limit": limit, "query_strategy": "provider_search"}

    def search(self, source, query, limit, year_from=None, year_to=None):
        if source in ("arxiv", "europe-pmc"):
            return self._search_repository(source, query, limit, year_from, year_to)
        if source not in ("openalex", "semantic-scholar", "crossref"):
            raise UnsupportedSource(f"{source} search is unsupported")
        years = self._year_params(source, year_from, year_to)
        papers, cursor, offset, pages = [], "*", 0, 0
        total, truncated = None, False
        while len(papers) < limit and pages < 4:
            size = min(10, limit - len(papers))
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
                               "affiliations": a.get("affiliations") or [], "paper_count": a.get("paperCount")}
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
            size = min(10, limit - len(papers))
            if source == "openalex":
                year_filter = self._year_params(source, year_from, year_to).get("filter")
                filters = "author.id:" + author_id + (("," + year_filter) if year_filter else "")
                data, normalized = self._page(source, "/works", {"filter": filters,
                                                          "per-page": size, "cursor": cursor}, papers, pages)
                entries = data.get("results")
                total = (data.get("meta") or {}).get("count")
                following = (data.get("meta") or {}).get("next_cursor")
            else:
                data, normalized = self._page(source, "/author/" + quote(author_id, safe="") + "/papers",
                                       {"fields": S2_FIELDS, "limit": size, "offset": offset}, papers, pages)
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

    def related(self, source, identifier, relation, limit):
        if source not in ("openalex", "semantic-scholar"):
            raise UnsupportedSource(f"{source} related is unsupported")
        if source == "openalex":
            seed = self.client.get(source, "/works/" + quote(oa_id(identifier), safe=""))
            seed_id = (seed.get("id") or "").rsplit("/", 1)[-1]
            if not seed_id:
                raise SourceError("OpenAlex seed has no stable ID")
            if relation in ("references", "recommendations"):
                refs = seed.get("referenced_works" if relation == "references" else "related_works")
                if not isinstance(refs, list):
                    raise SourceError("OpenAlex response lacks requested relation")
                papers, failures = [], []
                for ref in refs[:limit]:
                    try:
                        data = self.client.get(source, "/works/" + quote(ref.rsplit("/", 1)[-1], safe=""))
                        papers.append(normalize(source, data))
                    except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                        failures.append({"id": ref, "error": str(exc), "http_status": getattr(exc, "status", None)})
                status = {"status": "error" if failures else "ok", "fetched": len(papers), "pages": 1 + min(len(refs), limit),
                          "total": len(refs), "truncated": len(refs) > limit, "relation": relation}
                if failures:
                    status.update(error="Some related records failed", failures=failures)
                return papers, status
            papers, cursor, pages, total, more = [], "*", 0, None, False
            while len(papers) < limit and pages < 4:
                size = min(10, limit - len(papers))
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
        paper_id = s2_id(identifier)
        if relation == "recommendations":
            # Recommendations has its own public GET API, not Graph /paper paths.
            canonical = self.client.get(source, "/paper/" + quote(paper_id, safe=""), {"fields": "paperId"}).get("paperId")
            if not canonical:
                raise SourceError("Semantic Scholar seed has no paperId")
            data, normalized = self._page(source, "/recommendations/v1/papers/forpaper/" + quote(canonical, safe=""),
                                   {"limit": limit, "fields": S2_FIELDS}, [], 0, rows_key="recommendedPapers")
            entries = data["recommendedPapers"]
            return normalized[:limit], {"status": "ok", "fetched": min(len(entries), limit),
                    "pages": 1, "truncated": len(entries) >= limit, "relation": relation, "total": None}
        papers, offset, pages, more = [], 0, 0, False
        while len(papers) < limit and pages < 4:
            size = min(10, limit - len(papers))
            key = "citedPaper" if relation == "references" else "citingPaper"
            data, normalized = self._page(source, "/paper/" + quote(paper_id, safe="") + "/" + relation,
                                   {"fields": S2_FIELDS, "limit": size, "offset": offset}, papers, pages, nested_key=key)
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
