"""Bounded official Serper POST routes; remote content is always untrusted data."""
from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from _core import InputError, now, prepare_out, write_bundle
from _providers import SourceError

MODES = ("search", "images", "news", "maps", "places", "videos", "shopping", "scholar",
         "patents", "autocomplete", "reviews", "lens", "webpage")
ENDPOINTS = {mode: ("https://scrape.serper.dev" if mode == "webpage" else
                    "https://google.serper.dev/" + mode) for mode in MODES}
COMMON = {"q", "location", "gl", "hl", "num", "autocorrect", "tbs", "page"}
ALLOW = {mode: set(COMMON) for mode in MODES}
ALLOW.update(
    scholar=(COMMON - {"num"}) | {"as_ylo", "as_yhi", "cites", "cluster", "scisbd"},
    maps={"q", "hl", "ll", "placeId", "cid", "page"},
    autocomplete={"q", "location", "gl", "hl"},
    reviews={"cid", "fid", "placeId", "gl", "hl", "sortBy", "topicId", "nextPageToken"},
    lens={"url", "location", "gl", "hl", "tbs"},
    webpage={"url", "includeMarkdown", "includeImages", "includeLinks", "includeVideos"},
)
ROWS = {"search": "organic", "images": "images", "news": "news", "maps": "places",
        "places": "places", "videos": "videos", "shopping": "shopping", "scholar": "organic",
        "patents": "organic", "autocomplete": "suggestions", "reviews": "reviews", "lens": "organic"}
BOOLS = {"autocorrect", "includeMarkdown", "includeImages", "includeLinks", "includeVideos"}
INTS = {"num": (1, 100), "page": (1, 100), "as_ylo": (1000, 9999),
        "as_yhi": (1000, 9999), "scisbd": (0, 1)}
MAX_RESPONSE = 16 * 1024 * 1024


def strict_json(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise InputError("Duplicate JSON parameter: " + key)
            result[key] = value
        return result

    def constant(value):
        raise InputError("Non-finite JSON number is not allowed")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as exc:
        raise InputError("Invalid JSON parameters: " + str(exc)) from exc


def validate_url(value):
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ("http", "https") and parts.hostname and
                 parts.username is None and parts.password is None and
                 not any(char.isspace() or ord(char) < 32 for char in value))
        parts.port  # Validate port syntax too; the destination is sent as data to Serper.
    except ValueError:
        valid = False
    if not valid:
        raise InputError("url must be an absolute HTTP(S) URL without embedded credentials")


def validate(mode, params):
    if mode not in MODES:
        raise InputError("Unsupported Serper type")
    if not isinstance(params, dict):
        raise InputError("Each Serper request must be a JSON object")
    unknown = set(params) - ALLOW[mode]
    if unknown:
        raise InputError("Unsupported parameters for " + mode + ": " + ", ".join(sorted(unknown)))
    for key, value in params.items():
        if key in BOOLS:
            if type(value) is not bool:
                raise InputError(key + " must be a boolean")
        elif key in INTS:
            low, high = INTS[key]
            if type(value) is not int or not low <= value <= high:
                raise InputError(f"{key} must be an integer between {low} and {high}")
        elif not isinstance(value, str) or not value.strip():
            raise InputError(key + " must be a nonempty string")
    if "url" in params:
        validate_url(params["url"])
    if "sortBy" in params and params["sortBy"] not in ("mostRelevant", "newest", "highestRating", "lowestRating"):
        raise InputError("sortBy must be mostRelevant, newest, highestRating or lowestRating")
    if mode in ("webpage", "lens"):
        if "url" not in params:
            raise InputError(mode + " requires --url or a url parameter")
    elif mode == "reviews":
        if not any(params.get(key) for key in ("cid", "fid", "placeId")):
            raise InputError("reviews requires cid, fid or placeId")
    elif mode == "maps":
        if not any(params.get(key) for key in ("q", "cid", "placeId")):
            raise InputError("maps requires a query, cid or placeId")
    elif mode == "scholar":
        selectors = [key for key in ("cites", "cluster") if key in params]
        if len(selectors) > 1:
            raise InputError("Specify only one experimental scholar selector: cites or cluster")
        if not params.get("q") and not selectors:
            raise InputError("scholar requires a query or an experimental cites/cluster selector")
        if "scisbd" in params and any(key in params for key in ("as_ylo", "as_yhi")):
            raise InputError("scisbd with scholar year filters is unreliable and is not supported")
        if params.get("as_ylo", 1000) > params.get("as_yhi", 9999):
            raise InputError("Scholar start year must not exceed end year")
    elif not params.get("q"):
        raise InputError(mode + " requires a query")
    return dict(params)


def positive_page(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise InputError("page must be an integer") from exc
    if not 1 <= number <= 100:
        raise InputError("page must be between 1 and 100")
    return number


def page_budget(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise InputError("pages must be an integer") from exc
    if not 1 <= number <= 3:
        raise InputError("pages must be between 1 and 3")
    return number


def plan(args, client):
    if not client.env.get("SERPER_API_KEY", "").strip():
        raise InputError("SERPER_API_KEY is not configured")
    if args.batch:
        if (args.query is not None or args.url is not None or args.params is not None or
                args.page is not None or args.pages != 1 or args.year_from is not None or args.year_to is not None):
            raise InputError("--batch cannot be combined with query, --url, --params, page options or year filters")
        # The file is an explicit budget decision. Never expand a query into a batch.
        try:
            from pathlib import Path
            values = strict_json(Path(args.batch).read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise InputError("Cannot read batch file: " + str(exc)) from exc
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise InputError("Batch file must contain an array of 1..100 request objects")
        return [validate(args.type, value) for value in values]
    params = {} if args.params is None else strict_json(args.params)
    if not isinstance(params, dict):
        raise InputError("--params must be a JSON object")
    params = dict(params)
    for key, value in (("q", args.query), ("url", args.url), ("page", args.page),
                       ("as_ylo", args.year_from), ("as_yhi", args.year_to)):
        if value is not None:
            if key in params:
                raise InputError("Parameter specified twice: " + key)
            params[key] = value
    params = validate(args.type, params)
    if args.pages > 1:
        if "page" not in ALLOW[args.type] and args.type != "reviews":
            raise InputError("--pages is not supported for " + args.type)
        if params.get("page", 1) + args.pages - 1 > 100:
            raise InputError("Requested pagination exceeds page 100")
    return [params]


def post(client, mode, payload):
    """Send exactly one billed request. Never retry POSTs, including HTTP 429/5xx."""
    if mode not in ENDPOINTS:
        raise InputError("Unsupported Serper type")
    if isinstance(payload, list):
        if not 1 <= len(payload) <= 100:
            raise InputError("Batch requires 1..100 request objects")
        payload = [validate(mode, value) for value in payload]
    else:
        payload = validate(mode, payload)
    key = client.env.get("SERPER_API_KEY", "").strip()
    if not key:
        raise InputError("SERPER_API_KEY is not configured")
    url = ENDPOINTS[mode]
    request = Request(url, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                      headers={"X-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json",
                               "User-Agent": "sanmu-paper-search/1.0 (Serper; Python stdlib)"}, method="POST")
    audit = {"provider": "serper", "type": mode, "url": url, "method": "POST", "attempt": 1,
             "request": payload, "status": None, "retrieved_at": now()}
    try:
        try:
            with client.opener(request, timeout=25) as response:
                audit["status"] = response.status
                audit["response_headers"] = dict(response.headers)
                content = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            audit["status"] = exc.code
            audit["response_headers"] = dict(exc.headers or {})
            content = exc.read(MAX_RESPONSE + 1)
        if len(content) > MAX_RESPONSE:
            audit["body_prefix"] = content[:MAX_RESPONSE].decode("utf-8", errors="replace")
            raise SourceError("Response exceeds 16 MiB limit", audit["status"])
        text = content.decode("utf-8", errors="replace")
        # Preserve the response text as well as JSON, including malformed/error responses.
        audit["response_text"] = text
        def finite_float(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Non-finite response number")
            return number

        try:
            data = json.loads(text, parse_float=finite_float,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (ValueError, TypeError) as exc:
            raise SourceError("Invalid JSON response", audit["status"]) from exc
        audit["body"] = data
        if audit["status"] is None or not 200 <= audit["status"] < 300:
            raise SourceError(f"HTTP {audit['status']}", audit["status"])
        if not isinstance(data, (dict, list)):
            raise SourceError("Response must be an object or batch array", audit["status"])
        return data
    except (URLError, OSError, TimeoutError) as exc:
        audit["error"] = str(exc)
        raise SourceError("Network error: " + client.redactor.text(str(exc))) from exc
    except SourceError as exc:
        audit["error"] = str(exc)
        raise
    finally:
        client.raw.append(client.redactor.clean(audit))


def reported_credits(data):
    """Use reported costs only, including errors; unknown costs remain explicit."""
    if not isinstance(data, dict):
        return None
    value = data.get("credits")
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value


def rows(mode, data):
    if not isinstance(data, dict):
        raise SourceError("Each query response must be an object")
    if data.get("error") or data.get("message") or (
            isinstance(data.get("statusCode"), int) and data["statusCode"] >= 400):
        raise SourceError("Serper error response: " + str(data.get("error") or data.get("message") or data["statusCode"]))
    if "credits" in data and reported_credits(data) is None:
        raise SourceError("credits must be a finite nonnegative number")
    if "searchParameters" in data and not isinstance(data["searchParameters"], dict):
        raise SourceError("searchParameters must be an object")
    if mode == "webpage":
        if not any(isinstance(data.get(key), str) for key in ("text", "markdown")):
            raise SourceError("webpage response requires text or markdown")
        for key in ("text", "markdown"):
            if key in data and not isinstance(data[key], str):
                raise SourceError(key + " must be a string")
        for key in ("links", "images", "videos"):
            if key in data and not isinstance(data[key], list):
                raise SourceError(key + " must be an array")
        return [{key: value for key, value in data.items() if key != "credits"}]
    key = ROWS[mode]
    values = data.get(key)
    if not isinstance(values, list):
        raise SourceError("Response has no results array: " + key)
    if any(not isinstance(value, dict) for value in values):
        raise SourceError(key + " rows must be objects")
    if mode == "autocomplete" and any(not isinstance(value.get("value"), str) for value in values):
        raise SourceError("suggestions must contain string values")
    if "nextPageToken" in data and not isinstance(data["nextPageToken"], str):
        raise SourceError("nextPageToken must be a string")
    return values


def row_key(mode, item):
    """Conservative within-query identity; never merge distinct queries or paper records."""
    preferred = {"images": ("imageUrl", "link"), "shopping": ("productId", "link"),
                 "scholar": ("clusterId", "id", "link"), "patents": ("publicationNumber", "link"),
                 "maps": ("placeId", "cid", "fid"), "places": ("placeId", "cid", "fid"),
                 "reviews": ("id", "link"), "autocomplete": ("value",)}.get(mode, ("link",))
    for field in preferred:
        value = item.get(field)
        if type(value) in (str, int) and str(value):
            return field, str(value)
    # Position can change between pages without making a new result.
    return "row", json.dumps({key: value for key, value in item.items() if key != "position"},
                              sort_keys=True, ensure_ascii=False, allow_nan=False)


def execute(args, client):
    requests = plan(args, client)  # All local validation precedes output or network activity.
    out = prepare_out(args.out)
    raw_start = len(client.raw)
    results, responses, statuses = [], [], {}
    credits, unknown, attempted, completed, returned, duplicates = 0, 0, 0, 0, 0, 0
    completed_queries, seen_rows = set(), {index: set() for index in range(len(requests))}
    queries = [{"index": index, "request": value, "experimental": bool(
        args.type == "scholar" and any(key in value for key in ("cites", "cluster", "scisbd")))}
        for index, value in enumerate(requests)]

    def consume(index, page, request, data):
        nonlocal completed, returned, duplicates
        name = f"serper:{index}:page:{page}"
        try:
            entries = rows(args.type, data)
            retained = sum(item["query_index"] == index for item in results)
            fresh, repeated = [], 0
            for item in entries:
                key = row_key(args.type, item)
                if key in seen_rows[index]:
                    repeated += 1
                else:
                    seen_rows[index].add(key)
                    fresh.append(item)
            selected = fresh[:max(0, args.limit - retained)]
            results.extend({"type": args.type, "query_index": index, "page": page, "data": item} for item in selected)
            returned += len(entries)
            duplicates += repeated
            completed += 1
            completed_queries.add(index)
            # Search APIs expose no reliable total; a nonempty page may have more results.
            possibly_more = (bool(data.get("nextPageToken")) if args.type == "reviews" else
                             bool(entries) and args.type != "webpage")
            statuses[name] = {"status": "ok", "fetched": len(selected), "returned": len(entries), "pages": 1,
                              "duplicate_count": repeated, "truncated": len(fresh) > len(selected) or possibly_more, "request": request,
                              "credits": reported_credits(data)}
            return entries
        except SourceError as exc:
            statuses[name] = {"status": "error", "error": str(exc), "fetched": 0, "pages": 0,
                              "truncated": True, "request": request, "credits": reported_credits(data)}
            return None

    def send(payload):
        nonlocal credits, unknown, attempted
        attempted += 1
        before = len(client.raw)
        data, error = None, None
        try:
            data = client.post_serper(args.type, payload)
        except SourceError as exc:
            error = exc
        audit = client.raw[-1] if len(client.raw) > before else {}
        # Even a malformed/failed response can report billed credits.
        body = audit.get("body", data)
        credit_items = body if isinstance(body, list) else [body]
        for item in credit_items:
            cost = reported_credits(item)
            if cost is None:
                unknown += 1
            else:
                credits += cost
        if isinstance(payload, list):
            # Unreturned slots may still have been billed; never claim complete accounting.
            unknown += max(0, len(payload) - len(credit_items))
        responses.append({"request": payload, "response": body,
                          "http_status": audit.get("status"), "error": str(error) if error else None})
        return data, error

    if args.batch:
        data, error = send(requests)
        if error or not isinstance(data, list):
            statuses["serper:batch"] = {"status": "error", "fetched": 0, "truncated": True,
                "error": str(error) if error else "Batch response must be an array"}
        else:
            for index, request in enumerate(requests):
                if index < len(data):
                    consume(index, request.get("page", 1), request, data[index])
                else:
                    statuses[f"serper:{index}"] = {"status": "error", "fetched": 0, "truncated": True,
                                                    "error": "Missing batch response"}
            if len(data) != len(requests):
                statuses["serper:batch"] = {"status": "error", "fetched": 0, "truncated": True,
                                            "error": "Batch response count does not match request count"}
    else:
        request = dict(requests[0])
        for offset in range(args.pages):
            page = request.get("page", 1) if args.type != "reviews" else offset + 1
            data, error = send(request)
            if error:
                statuses[f"serper:0:page:{page}"] = {"status": "error", "error": str(error),
                    "http_status": error.status, "fetched": 0, "pages": 0, "truncated": True, "request": request}
                break
            entries = consume(0, page, dict(request), data)
            if entries is None or not entries:
                break
            if len(results) >= args.limit or offset + 1 >= args.pages:
                break
            if args.type == "reviews":
                token = data.get("nextPageToken")
                if not token or token == request.get("nextPageToken"):
                    break
                request = {**request, "nextPageToken": token}
            else:
                request = {**request, "page": page + 1}

    typed = {"schema": "sanmu-paper-search/serper-results/v1", "provider": "serper", "type": args.type,
             "trust": "untrusted_remote_data", "queries": queries, "results": results}
    query = {"type": args.type, "batch": bool(args.batch), "requests": requests, "limit": args.limit,
             "pages": args.pages, "year_from": args.year_from, "year_to": args.year_to}
    extra = {"result_count": len(results), "returned_count": returned, "duplicate_count": duplicates,
             "queries_completed": len(completed_queries),
             "requests_attempted": attempted, "pages_completed": completed,
             "credits_reported": credits, "credits_unknown_responses": unknown,
             "credits_complete": unknown == 0, "experimental": any(q["experimental"] for q in queries),
             "trust": "untrusted_remote_data", "fulltext_verified": False,
             "limit_scope": "local_per_query_total", "result_truncated": any(s.get("truncated") for s in statuses.values()),
             "result_artifact": "results.json", "response_artifact": "response.json"}
    run = write_bundle(out, "serper", query, [], statuses, client.raw[raw_start:], client.redactor, extra,
                       serper_data={"results": typed, "responses": responses})
    return run, {"success": 0, "partial": 1, "failed": 3}[run["status"]]
