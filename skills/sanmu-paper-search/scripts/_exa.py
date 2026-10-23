"""Optional Exa publication discovery with explicit request and cost records."""
from __future__ import annotations

import json
import math
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request

from _core import InputError, deduplicate, ids, now, prepare_out, record, write_bundle
from _providers import SourceError

ENDPOINT = "https://api.exa.ai/search"
MAX_RESPONSE = 16 * 1024 * 1024


def plan(args, client):
    if not client.env.get("EXA_API_KEY", "").strip():
        raise InputError("EXA_API_KEY is not configured")
    if not args.query.strip():
        raise InputError("Search query must not be empty")
    if not 0 <= args.text_chars <= 10000:
        raise InputError("--text-chars must be between 0 and 10000")
    years = (args.year_from, args.year_to)
    if any(year is not None and not 1000 <= year <= 9999 for year in years):
        raise InputError("Years must be between 1000 and 9999")
    if all(year is not None for year in years) and args.year_from > args.year_to:
        raise InputError("--year-from must not exceed --year-to")
    payload = {"query": args.query.strip(), "type": args.type,
               "category": "publication", "numResults": args.limit}
    for flag, values in (("includeDomains", args.include_domain), ("excludeDomains", args.exclude_domain)):
        if len(values) > 1200:
            raise InputError(flag + " supports at most 1200 entries")
        if any(not re.fullmatch(r"(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?:/[^\s?#]*)?", value)
               or "." not in value.split("/", 1)[0] for value in values):
            raise InputError(flag + " entries must be domain names or domain/path prefixes")
        if values:
            payload[flag] = list(dict.fromkeys(values))
    if args.year_from:
        payload["startPublishedDate"] = f"{args.year_from:04d}-01-01T00:00:00.000Z"
    if args.year_to:
        payload["endPublishedDate"] = f"{args.year_to:04d}-12-31T23:59:59.999Z"
    if args.text_chars:
        payload["contents"] = {"text": {"maxCharacters": args.text_chars}}
    return payload


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


def cost(data):
    value = (data.get("costDollars") or {}).get("total") if isinstance(data, dict) and isinstance(data.get("costDollars", {}), dict) else None
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def post(client, payload):
    key = client.env["EXA_API_KEY"].strip()
    request = Request(ENDPOINT, data=json.dumps(payload, allow_nan=False).encode("utf-8"),
                      headers={"x-api-key": key, "Content-Type": "application/json", "Accept": "application/json",
                               "User-Agent": "sanmu-paper-search/1.5 (Exa; Python stdlib)"}, method="POST")
    waited = 0.0
    for attempt in range(3):
        waited += client.wait("exa", key, interval=1.05, max_wait=max(0, 10 - waited))
        audit = {"provider": "exa", "url": ENDPOINT, "method": "POST", "attempt": attempt + 1,
                 "request": payload, "status": None, "retrieved_at": now()}
        headers = {}
        try:
            try:
                with client.opener(request, timeout=25) as response:
                    audit["status"] = response.status
                    headers = dict(response.headers)
                    content = response.read(MAX_RESPONSE + 1)
            except HTTPError as exc:
                audit["status"] = exc.code
                headers = dict(exc.headers or {})
                content = exc.read(MAX_RESPONSE + 1)
            audit["response_headers"] = headers
            if len(content) > MAX_RESPONSE:
                raise SourceError("Response exceeds 16 MiB limit", audit["status"])
            audit["response_text"] = content.decode("utf-8", errors="replace")
            try:
                data = json.loads(audit["response_text"], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                # Reject exponent overflow as well as literal NaN/Infinity.
                json.dumps(data, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise SourceError("Invalid JSON response", audit["status"]) from exc
            audit["body"] = data
            if audit["status"] in (429, 502, 503, 504):
                delay = retry_delay(headers, attempt)
                client.cooldown("exa", key, delay)
                if attempt < 2 and delay <= 10 - waited:
                    client.raw.append(client.redactor.clean(audit))
                    continue
            if audit["status"] == 402:
                raise SourceError("HTTP 402: Exa credit balance or key budget exhausted", 402)
            if audit["status"] is None or not 200 <= audit["status"] < 300:
                raise SourceError(f"HTTP {audit['status']}", audit["status"])
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise SourceError("Exa response has no results array", audit["status"])
            if any(not isinstance(row, dict) or not isinstance(row.get("title"), str) or not isinstance(row.get("url"), str)
                   for row in data["results"]):
                raise SourceError("Exa result requires title and url strings", audit["status"])
            if "costDollars" in data and cost(data) is None:
                raise SourceError("Exa costDollars.total must be finite and nonnegative", audit["status"])
            client.raw.append(client.redactor.clean(audit))
            return data
        except (URLError, OSError, TimeoutError) as exc:
            audit["error"] = client.redactor.text(str(exc))
            client.raw.append(client.redactor.clean(audit))
            raise SourceError("Network error; request billing is unknown: " + audit["error"]) from exc
        except SourceError as exc:
            audit["error"] = str(exc)
            client.raw.append(client.redactor.clean(audit))
            raise
    raise SourceError("Exa retry budget exhausted", 429)


def publications(results):
    papers = []
    normalized_title = lambda value: re.sub(r"[^\w]", "", str(value).casefold())
    for row in results:
        entities = row.get("entities")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict) or entity.get("type") != "publication":
                continue
            props = entity.get("properties")
            if not isinstance(props, dict) or not props.get("title") or normalized_title(props["title"]) != normalized_title(row["title"]):
                continue
            year = props.get("year")
            if type(year) is not int or not 1000 <= year <= 9999:
                year = None
            authors = [{"name": author["name"], "identifiers": {}, "affiliations": []}
                       for author in props.get("authors", []) if isinstance(author, dict) and isinstance(author.get("name"), str)] if isinstance(props.get("authors"), list) else []
            abstract = props.get("abstract") if isinstance(props.get("abstract"), str) else None
            paper = record("exa", entity.get("id") or row.get("id") or row["url"], props["title"], year,
                           ids(doi=props.get("doi")), authors, abstract,
                           props.get("type") if isinstance(props.get("type"), str) else None)
            paper.update(url=row["url"], identity_status="discovered", publication_date=props.get("date"))
            for source, target in (("citationCount", "citation_count"), ("referenceCount", "reference_count")):
                value = props.get(source)
                if type(value) is int and value >= 0:
                    paper[target] = value
            papers.append(paper)
            break
    return deduplicate(papers)


def execute(args, client):
    payload = plan(args, client)
    out = prepare_out(args.out)
    start = len(client.raw)
    results, papers = [], []
    try:
        data = post(client, payload)
        results = data["results"]
        papers = publications(results)
        status = {"status": "ok", "fetched": len(papers), "returned": len(results),
                  "truncated": len(results) >= args.limit, "identity_verified": False}
    except SourceError as exc:
        status = {"status": "error", "error": str(exc), "http_status": exc.status,
                  "fetched": 0, "truncated": True}
    audits = client.raw[start:]
    costs = [cost(row.get("body")) for row in audits]
    extra = {"result_count": len(results), "publication_count": len(papers),
             "unconverted_result_count": len(results) - len(papers),
             "cost_usd_reported": round(sum(value for value in costs if value is not None), 10),
             "cost_unknown_responses": sum(value is None for value in costs),
             "cost_is_final_bill": False, "result_truncated": status["truncated"],
             "limit_scope": "per_request_result_budget", "ordering": "provider_order",
             "identity_verified": False, "request_attempts": len(audits)}
    run = write_bundle(out, "exa", payload, papers, {"exa": status}, audits, client.redactor, extra,
                       exa_data={"results": results, "responses": audits})
    return run, {"success": 0, "partial": 1, "failed": 3}[run["status"]]
