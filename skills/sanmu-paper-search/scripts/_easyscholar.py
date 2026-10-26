"""easyScholar publication rank adapter (SCI IF, JCR quartile, CAS quartile, CCF, warning list)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from _core import InputError, dumps, load_papers, now, prepare_out, write_bundle
from _providers import SourceError

ENDPOINT = "https://www.easyscholar.cc/open/getPublicationRank"
CACHE_TTL_SECONDS = 30 * 24 * 3600

COMMON_VENUE_ALIASES = {
    "angewandte chemie": "Angewandte Chemie International Edition",
    "angew chem": "Angewandte Chemie International Edition",
    "angew chem int ed": "Angewandte Chemie International Edition",
    "angew chem int ed engl": "Angewandte Chemie International Edition",
    "jacs": "Journal of the American Chemical Society",
    "j am chem soc": "Journal of the American Chemical Society",
    "adv mater": "Advanced Materials",
    "advances in materials": "Advanced Materials",
    "adv funct mater": "Advanced Functional Materials",
    "adv energy mater": "Advanced Energy Materials",
    "adv sci": "Advanced Science",
    "nat commun": "Nature Communications",
    "nat catal": "Nature Catalysis",
    "nat energy": "Nature Energy",
    "nat mater": "Nature Materials",
    "nat chem": "Nature Chemistry",
    "energy environ sci": "Energy & Environmental Science",
    "acs catal": "ACS Catalysis",
    "acs nano": "ACS Nano",
    "acs energy lett": "ACS Energy Letters",
    "nano lett": "Nano Letters",
    "appl catal b": "Applied Catalysis B: Environmental",
    "appl catal b environ": "Applied Catalysis B: Environmental",
    "applied catalysis b environmental and energy": "Applied Catalysis B: Environmental",
    "j mater chem a": "Journal of Materials Chemistry A",
    "chem soc rev": "Chemical Society Reviews",
    "chem rev": "Chemical Reviews",
    "pnas": "Proceedings of the National Academy of Sciences of the United States of America",
    "proc natl acad sci u s a": "Proceedings of the National Academy of Sciences of the United States of America",
    "cvpr": "IEEE Conference on Computer Vision and Pattern Recognition",
    "iccv": "IEEE International Conference on Computer Vision",
    "neurips": "Neural Information Processing Systems",
    "nips": "Neural Information Processing Systems",
    "icml": "International Conference on Machine Learning",
}


def clean_secret_key(raw):
    if not raw:
        return ""
    return re.sub(r"\s+", "", str(raw)).strip()


def normalize_venue_key(name):
    text = str(name or "").strip()
    text = re.sub(r"\s*\((?:online|print|electronic|issn[^)]*)\)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*\[[A-Z]\]\s*$", "", text)
    norm = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", text.casefold()).strip()
    return norm


def candidate_venue_names(venue_name):
    raw = str(venue_name or "").strip()
    if not raw:
        return []
    cleaned = re.sub(r"\s*\((?:online|print|electronic|issn[^)]*)\)\s*$", "", raw, flags=re.I)
    cleaned = re.sub(r"\s*\[[A-Z]\]\s*$", "", cleaned).strip()
    norm = normalize_venue_key(cleaned)
    cands = []
    if norm in COMMON_VENUE_ALIASES:
        cands.append(COMMON_VENUE_ALIASES[norm])
    if cleaned and cleaned not in cands:
        cands.append(cleaned)
    if ":" in cleaned:
        hyphenated = re.sub(r"\s*:\s*", "-", cleaned)
        if hyphenated not in cands:
            cands.append(hyphenated)
        head = cleaned.split(":", 1)[0].strip()
        head_norm = normalize_venue_key(head)
        if head_norm in COMMON_VENUE_ALIASES and COMMON_VENUE_ALIASES[head_norm] not in cands:
            cands.append(COMMON_VENUE_ALIASES[head_norm])
    return cands[:3]


def _cache_file_path(env):
    explicit = (env or {}).get("EASYSCHOLAR_CACHE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    plugin_data = (env or {}).get("ZCODE_PLUGIN_DATA", "").strip()
    if plugin_data:
        return Path(plugin_data) / "easyscholar-cache.json"
    return Path(tempfile.gettempdir()) / "sanmu-paper-search-easyscholar-cache.json"


def fresh_cache_entry(entry):
    if not isinstance(entry, dict) or "rank" not in entry:
        return False
    try:
        updated = datetime.fromisoformat(entry.get("updated_at", "").replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        return 0 <= age < CACHE_TTL_SECONDS
    except (ValueError, TypeError, AttributeError):
        return False


def load_rank_cache(env):
    path = _cache_file_path(env)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def save_rank_cache(env, cache_data):
    if not isinstance(cache_data, dict):
        return
    path = _cache_file_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def parse_rank_payload(venue_name, matched_name, data):
    if not isinstance(data, dict):
        return None
    official = data.get("officialRank") or {}
    all_rank = official.get("all") if isinstance(official, dict) else None
    if not isinstance(all_rank, dict):
        all_rank = {}
    select_rank = official.get("select") if isinstance(official, dict) else None
    if not isinstance(select_rank, dict):
        select_rank = {}

    custom = data.get("customRank") or {}
    rank_info_list = custom.get("rankInfo") if isinstance(custom, dict) else []
    rank_entries = custom.get("rank") if isinstance(custom, dict) else []
    custom_resolved = {}
    if isinstance(rank_info_list, list) and isinstance(rank_entries, list):
        info_by_uuid = {}
        for item in rank_info_list:
            if isinstance(item, dict) and item.get("uuid"):
                info_by_uuid[str(item["uuid"])] = item
        slot_fields = {
            "1": "oneRankText",
            "2": "twoRankText",
            "3": "threeRankText",
            "4": "fourRankText",
            "5": "fiveRankText",
        }
        for entry in rank_entries:
            if not isinstance(entry, str) or "&&&" not in entry:
                continue
            uuid_part, idx_part = entry.split("&&&", 1)
            info = info_by_uuid.get(uuid_part.strip())
            if not info:
                continue
            abb = str(info.get("abbName") or "").strip()
            field = slot_fields.get(idx_part.strip())
            val = str(info.get(field) or "").strip() if field else ""
            if abb and val:
                custom_resolved[abb] = val

    if not all_rank and not custom_resolved:
        return None

    if_str = str(all_rank.get("sciif") or "").strip() or None
    if_val = None
    if if_str:
        try:
            if_val = float(if_str)
        except ValueError:
            if_val = None
    if5_str = str(all_rank.get("sciif5") or "").strip() or None
    jci_str = str(all_rank.get("jci") or "").strip() or None
    jcr = str(all_rank.get("sci") or all_rank.get("ssci") or "").strip() or None
    cas_up = str(all_rank.get("sciUp") or all_rank.get("xr") or "").strip() or None
    if not cas_up:
        for k, v in custom_resolved.items():
            if "SCI" in k.upper() and v:
                cas_up = v
                break
    cas_base = str(all_rank.get("sciBase") or "").strip() or None
    cas_top = str(all_rank.get("sciUpTop") or all_rank.get("xrTop") or "").strip() or None
    cas_small = str(all_rank.get("sciUpSmall") or all_rank.get("xrSmall") or "").strip() or None
    ccf = str(all_rank.get("ccf") or "").strip() or None
    ei = str(all_rank.get("eii") or "").strip() or None
    esi = str(all_rank.get("esi") or "").strip() or None
    cssci = str(all_rank.get("cssci") or all_rank.get("cssciEx") or "").strip() or None
    pku = str(all_rank.get("pku") or "").strip() or None

    warning = str(all_rank.get("sciWarn") or "").strip() or None
    if not warning:
        for k, v in custom_resolved.items():
            if "预警" in k and v:
                warning = v
                break

    badges = []
    if warning:
        badges.append(f"⚠ 预警:{warning}")
    if cas_up:
        is_top = bool(cas_top or "TOP" in cas_up.upper())
        is_q1 = "1区" in cas_up or "一区" in cas_up
        prefix = "🏆 " if (is_top or is_q1) else "📊 "
        suffix = " TOP" if (is_top and "TOP" not in cas_up.upper()) else ""
        badges.append(f"{prefix}{cas_up}{suffix}")
    elif cas_base:
        badges.append(f"📊 {cas_base}")
    if jcr:
        badges.append(f"JCR {jcr}")
    if if_str:
        badges.append(f"IF {if_str}")
    if ccf:
        badges.append(f"CCF {ccf}" if not ccf.upper().startswith("CCF") else ccf)
    if cssci and not jcr and not cas_up:
        badges.append(f"CSSCI {cssci}")
    if pku and not jcr and not cas_up:
        badges.append(f"北大核心")
    if ei and not badges:
        badges.append("EI")

    is_top_tier = bool(
        (cas_up and ("1区" in cas_up or "一区" in cas_up))
        or cas_top
        or (jcr and "Q1" in jcr.upper())
        or (ccf and ccf.strip().upper() in ("A", "CCF-A", "CCF A"))
        or (if_val is not None and if_val >= 10.0)
    )

    summary_parts = []
    if cas_up:
        summary_parts.append(f"中科院: {cas_up}" + (f" ({cas_top})" if cas_top and cas_top != cas_up else ""))
    elif cas_base:
        summary_parts.append(f"中科院基础版: {cas_base}")
    if jcr:
        summary_parts.append(f"JCR: {jcr}")
    if if_str:
        summary_parts.append(f"IF: {if_str}")
    if if5_str:
        summary_parts.append(f"5年IF: {if5_str}")
    if ccf:
        summary_parts.append(f"CCF: {ccf}")
    if jci_str:
        summary_parts.append(f"JCI: {jci_str}")
    if warning:
        summary_parts.append(f"预警等级: {warning}")

    if not badges and not summary_parts:
        return None

    return {
        "venue_name": venue_name,
        "matched_name": matched_name,
        "if": if_val,
        "if_str": if_str,
        "if5_str": if5_str,
        "jcr": jcr,
        "cas_up": cas_up,
        "cas_base": cas_base,
        "cas_top": cas_top,
        "cas_small": cas_small,
        "ccf": ccf,
        "ei": ei,
        "jci": jci_str,
        "esi": esi,
        "cssci": cssci,
        "pku": pku,
        "warning": warning,
        "custom_ranks": custom_resolved,
        "is_top_tier": is_top_tier,
        "badges": badges,
        "summary": " | ".join(summary_parts),
        "official_all": all_rank,
        "official_select": select_rank,
    }


def request_publication_rank(client, secret_key, pub_name):
    clean_key = clean_secret_key(secret_key)
    if not clean_key:
        raise InputError("EASYSCHOLAR_SECRET_KEY is not configured")
    query = urlencode({"secretKey": clean_key, "publicationName": pub_name})
    url = f"{ENDPOINT}?{query}"
    headers = {
        "User-Agent": "sanmu-paper-search/1.7 (easyScholar; Python stdlib)",
        "Accept": "application/json",
    }
    client.wait("easyscholar", clean_key, interval=0.52, max_wait=10.0)
    status = None
    body_text = ""
    try:
        with client.opener(Request(url, headers=headers, method="GET"), timeout=20) as resp:
            status = resp.status
            body_text = resp.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    except HTTPError as exc:
        status = exc.code
        body_text = exc.read(512 * 1024).decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError) as exc:
        detail = client.redactor.text(str(exc))
        client.raw.append({
            "provider": "easyscholar",
            "url": client.redactor.text(url),
            "status": None,
            "error": detail,
            "retrieved_at": now(),
        })
        raise SourceError("easyScholar network error: " + detail) from exc

    try:
        parsed = json.loads(body_text)
        json.dumps(parsed, allow_nan=False)
    except (ValueError, TypeError) as exc:
        client.raw.append({
            "provider": "easyscholar",
            "url": client.redactor.text(url),
            "status": status,
            "body": {"unparsed_body": body_text[:500]},
            "retrieved_at": now(),
        })
        raise SourceError("Invalid JSON from easyScholar", status) from exc

    client.raw.append(client.redactor.clean({
        "provider": "easyscholar",
        "url": url,
        "status": status,
        "body": parsed,
        "retrieved_at": now(),
    }))
    if status is None or not (200 <= status < 300):
        raise SourceError(f"easyScholar HTTP {status}", status)
    code = parsed.get("code")
    if code is not None and code != 200:
        msg = str(parsed.get("msg") or f"code {code}")
        raise SourceError(f"easyScholar error: {msg}", status)
    return parsed.get("data") or {}


def lookup_venue_rank(client, venue_name, cache=None, use_cache=True):
    raw_name = str(venue_name or "").strip()
    if not raw_name or len(raw_name) < 2 or "…" in raw_name or "..." in raw_name:
        return None
    if re.search(r"\.(?:com|org|net|edu|gov|cn|io)\b", raw_name, flags=re.I):
        return None
    norm_key = normalize_venue_key(raw_name)
    skip_Set = {
        "unknown", "preprint", "arxiv", "biorxiv", "medrxiv", "ssrn", "chemrxiv",
        "google scholar", "google patents", "elsevier", "springer", "wiley",
        "acs publications", "rsc", "mdpi", "taylor francis", "ieee", "nature portfolio",
    }
    if not norm_key or norm_key in skip_Set or norm_key.startswith(("patent ", "s2 ", "serper ")):
        return None

    if cache is not None and use_cache and norm_key in cache:
        entry = cache[norm_key]
        if fresh_cache_entry(entry):
            return entry["rank"]

    secret_key = clean_secret_key(client.env.get("EASYSCHOLAR_SECRET_KEY", ""))
    if not secret_key:
        return None

    for cand in candidate_venue_names(raw_name):
        cand_key = normalize_venue_key(cand)
        if cache is not None and use_cache and cand_key in cache:
            cached_entry = cache[cand_key]
            if fresh_cache_entry(cached_entry) and cached_entry.get("rank"):
                return cached_entry["rank"]
        data = request_publication_rank(client, secret_key, cand)
        rank_obj = parse_rank_payload(raw_name, cand, data)
        if rank_obj is not None:
            record_entry = {"rank": rank_obj, "updated_at": now()}
            if cache is not None:
                cache[norm_key] = record_entry
                cache[cand_key] = record_entry
            return rank_obj

    if cache is not None:
        cache[norm_key] = {"rank": None, "updated_at": now()}
    return None


def enrich_papers_with_ranks(client, papers, max_lookups=15, use_cache=True, cache=None, diagnostics=None):
    secret_key = clean_secret_key(client.env.get("EASYSCHOLAR_SECRET_KEY", ""))
    owns_cache = cache is None
    if owns_cache:
        cache = load_rank_cache(client.env) if use_cache else {}
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.setdefault("errors", [])
    diagnostics.setdefault("completed", 0)
    diagnostics.setdefault("truncated", False)
    lookups_done = 0
    cache_dirty = False
    enriched_count = 0

    for p in papers:
        if not isinstance(p, dict):
            continue
        if isinstance(p.get("venue_rank"), dict) and p["venue_rank"].get("badges"):
            enriched_count += 1
            continue
        journal = p.get("journal")
        if isinstance(journal, dict):
            journal = journal.get("name")
        venue_str = str(journal or p.get("venue") or "").strip()
        if not venue_str:
            continue
        norm_key = normalize_venue_key(venue_str)
        if not norm_key:
            continue

        if use_cache and fresh_cache_entry(cache.get(norm_key)):
            cached_rank = cache[norm_key].get("rank")
            if cached_rank:
                p["venue_rank"] = cached_rank
                enriched_count += 1
            diagnostics["completed"] += 1
            continue

        if not secret_key or lookups_done >= max_lookups:
            diagnostics["truncated"] = True
            continue
        try:
            lookups_done += 1
            rank_obj = lookup_venue_rank(client, venue_str, cache=cache, use_cache=use_cache)
            cache_dirty = True
            diagnostics["completed"] += 1
            if rank_obj:
                p["venue_rank"] = rank_obj
                enriched_count += 1
        except SourceError as exc:
            diagnostics["errors"].append(f"{venue_str}: {exc}")
            continue

    if owns_cache and cache_dirty and use_cache:
        save_rank_cache(client.env, cache)
    return enriched_count


def execute(args, client):
    secret_key = clean_secret_key(client.env.get("EASYSCHOLAR_SECRET_KEY", ""))
    if not secret_key:
        raise InputError("EASYSCHOLAR_SECRET_KEY is required for the rank command")
    venues = [str(v).strip() for v in (getattr(args, "venues", None) or []) if str(v).strip()]
    run_input = getattr(args, "run", None)
    if not venues and not run_input:
        raise InputError("Provide one or more venue names or --run <run_dir>")
    if len(venues) > 20:
        raise InputError("At most 20 venue names can be queried per command")

    papers = load_papers(run_input) if run_input else []
    out = prepare_out(args.out)
    cache = load_rank_cache(client.env)
    results = []
    errors = []

    for v in venues:
        try:
            rank = lookup_venue_rank(client, v, cache=cache, use_cache=not getattr(args, "no_cache", False))
            results.append({"venue": v, "found": rank is not None, "venue_rank": rank})
        except SourceError as exc:
            errors.append(f"{v}: {exc}")
            results.append({"venue": v, "found": False, "error": str(exc)})

    diagnostics = {}
    if papers:
        enrich_papers_with_ranks(client, papers, max_lookups=20,
                                use_cache=not getattr(args, "no_cache", False),
                                cache=cache, diagnostics=diagnostics)
    errors.extend(diagnostics.get("errors", []))
    save_rank_cache(client.env, cache)

    status = "error" if errors else "ok"
    sources = {
        "easyscholar": {
            "status": status,
            "fetched": sum(1 for r in results if r.get("found")) + sum(1 for p in papers if p.get("venue_rank")),
            "truncated": bool(errors) or diagnostics.get("truncated", False),
            **({"error": "; ".join(errors)} if errors else {}),
        }
    }
    completed = sum(1 for r in results if "error" not in r) + diagnostics.get("completed", 0)
    if errors and completed:
        sources["easyscholar:completed"] = {"status": "ok", "fetched": sources["easyscholar"]["fetched"], "truncated": False}
    (out / "results.json").write_text(dumps(client.redactor.clean(results)) + "\n", encoding="utf-8")
    (out / "response.json").write_text(dumps(client.redactor.clean(client.raw)) + "\n", encoding="utf-8")
    run = write_bundle(
        out,
        "rank",
        {"venues": venues, "run": str(run_input) if run_input else None},
        papers,
        sources,
        client.raw,
        client.redactor,
        extra={"venue_ranks": results},
    )
    code = 3 if run["status"] == "failed" else (1 if run["status"] == "partial" else 0)
    return run, code
