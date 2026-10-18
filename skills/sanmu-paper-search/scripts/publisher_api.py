"""Fetch closed-access PDFs through official publisher TDM APIs.

Supported routes:
  Elsevier Article Retrieval API (ELSEVIER_API_KEY)
  Wiley TDM API (WILEY_TDM_KEY or WILEY_TDM_CLIENT_TOKEN)

Keys are read from environment variables only. They are never written to disk,
logs, manifests, or the repository. Requests and cooldowns are accounted for in
a persistent rate ledger; existing acquired files are never overwritten when
their manifest hash or DOI disagrees. Other publishers use webvpn_acquire.py.

The script is optional and uses requests. PDFs are verified with the same
header/trailer and identity checks as fetch_pdf.py; manifests are consumable by
ingest.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

import acquisition_store
import fetch_pdf
import rate_ledger
from ingest import ingest as ingest_manifest
from zotero_local import ZoteroError, ZoteroLocal

SCHEMA = "sanmu-acquisition/v1"
MAX_BYTES = 80 * 1024 * 1024
DEFAULT_DELAY = 2.0
DEFAULT_JITTER = 1.0
ENV_KEYS = {
    "elsevier": ("ELSEVIER_API_KEY",),
    "wiley": ("WILEY_TDM_KEY", "WILEY_TDM_CLIENT_TOKEN"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def provider_for_doi(doi):
    value = (doi or "").strip().lower()
    if value.startswith("10.1016/"):
        return "elsevier"
    if value.startswith(("10.1002/", "10.1111/")):
        return "wiley"
    return "unsupported"


def api_key(provider):
    for name in ENV_KEYS.get(provider, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def configured(provider):
    return bool(api_key(provider))


def _read_pdf(response, max_bytes=MAX_BYTES):
    chunks = []
    total = 0
    for chunk in response.iter_content(1024 * 256):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            return None, {"status": "too_large", "bytes": total}
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        checks = fetch_pdf.inspect_pdf(data)
    except ValueError as exc:
        return None, {"status": "not_pdf", "error": str(exc),
                      "content_type": response.headers.get("content-type")}
    return data, {"status": "file_obtained", "checks": checks,
                  "content_type": "application/pdf"}


def fetch_api_pdf(doi, session=None, timeout=90, max_bytes=MAX_BYTES):
    """Return {status, provider, data?, checks?, http_status?} without leaking keys."""
    doi = (doi or "").strip()
    provider = provider_for_doi(doi)
    if provider == "unsupported":
        return {"status": "unsupported", "provider": provider}
    key = api_key(provider)
    if not key:
        return {"status": "not_configured", "provider": provider}
    if session is None:
        session = requests.Session()
        session.trust_env = False
    headers = {"User-Agent": "sanmu-paper-search/1.0", "Accept": "application/pdf"}
    try:
        if provider == "elsevier":
            url = "https://api.elsevier.com/content/article/doi/" + quote(doi, safe="")
            headers["X-ELS-APIKey"] = key
            response = session.get(url, headers=headers,
                                   params={"httpAccept": "application/pdf"},
                                   timeout=timeout, stream=True)
        else:
            url = "https://api.wiley.com/onlinelibrary/tdm/v1/articles/" + quote(doi, safe="")
            headers["Wiley-TDM-Client-Token"] = key
            response = session.get(url, headers=headers, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        return {"status": "network_error", "provider": provider,
                "error": type(exc).__name__}
    try:
        if response.status_code == 429:
            return {"status": "rate_limited", "provider": provider,
                    "retry_after": response.headers.get("retry-after")}
        if response.status_code in (401, 403):
            return {"status": "access_denied", "provider": provider,
                    "http_status": response.status_code}
        if response.status_code != 200:
            return {"status": "http_error", "provider": provider,
                    "http_status": response.status_code}
        content_type = (response.headers.get("content-type") or "").lower()
        data, result = _read_pdf(response, max_bytes)
        result.update({"provider": provider, "source_url": "https://doi.org/" + doi})
        if content_type and "content_type" not in result:
            result["content_type"] = content_type
        if data is not None:
            result["data"] = data
            result["bytes"] = len(data)
        return result
    finally:
        response.close()


def _item_data(item):
    return item.get("data", item)


def has_main_pdf(client, key, library="user"):
    children = client.children(key, library=library).get("items") or []
    for child in children:
        data = _item_data(child)
        if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf":
            continue
        title = (data.get("title") or "").lower()
        if any(marker in title for marker in
               ("supplementary", "supplemental", "supporting information",
                "supporting material", "appendix", " si ", "si —")):
            continue
        try:
            if client.file(data["key"], library=library).get("status") == "available":
                return True
        except ZoteroError:
            continue
    return False


def load_keys(keys, keys_file):
    values = []
    if keys:
        values.extend(part.strip() for part in keys.split(",") if part.strip())
    if keys_file:
        values.extend(line.strip() for line in Path(keys_file).read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.startswith("#"))
    unique = list(dict.fromkeys(values))
    for key in unique:
        if not re.fullmatch(r"[A-Za-z0-9]{1,20}", key):
            raise ValueError("Invalid Zotero item key: " + key)
    return unique


def _manifest(item, pdf_name, sha256, checks, identity, doi, provider):
    data = _item_data(item)
    authors = []
    for creator in data.get("creators") or []:
        name = creator.get("name") or " ".join(
            part for part in (creator.get("firstName"), creator.get("lastName")) if part)
        if name:
            authors.append({"name": name})
    year = (data.get("date") or "")[:4]
    source_provider = "elsevier_api" if provider == "elsevier" else "wiley_tdm"
    return {
        "schema": SCHEMA,
        "created_at": now(),
        "updated_at": now(),
        "paper": {
            "title": data.get("title") or "",
            "doi": doi,
            "identifiers": {"doi": [doi]} if doi else {},
            "authors": authors,
            "year": year or None,
        },
        "main": {
            "id": "main", "kind": "main", "status": "file_obtained", "file": pdf_name,
            "sha256": sha256, "content_type": "application/pdf",
            "format_checks": checks, "identity": identity,
            "source": {"provider": source_provider,
                       "url": "https://doi.org/" + doi,
                       "version": "publishedVersion",
                       "evidence": "official_tdm_api"},
        },
        "supplementary": [],
        "read": False,
    }


def _apply_ingest(record, manifest_path, key, client, args):
    try:
        result = ingest_manifest(
            str(manifest_path), client=client, parent_key=key,
            library=args.library, apply=True,
            identity_confirmed=args.identity_confirmed)
        record["ingest"] = result
        record["status"] = acquisition_store.ingest_status(result)
    except (ZoteroError, ValueError, OSError) as exc:
        record["ingest_error"] = str(exc)[:300]


def _save_record(record, data, checks, item, key, out, client, args, provider):
    dest_dir = out / key
    doi = (_item_data(item).get("DOI") or "").strip()
    existing = acquisition_store.inspect_existing(dest_dir, doi)
    if existing is not None:
        identity = existing.get("identity") or {"status": "not_checked"}
        record.update(status="skipped", reason="already_obtained",
                      file=existing["file"], manifest=existing["manifest"],
                      identity=identity, bytes=existing["bytes"],
                      sha256=existing["sha256"], api_provider=provider)
        if args.apply:
            if identity.get("status") == "matched" or args.identity_confirmed:
                _apply_ingest(record, existing["manifest"], key, client, args)
            else:
                record["ingest"] = {"status": "pending_identity_review"}
        return record
    # Stage the identity check outside the acquisition directory so a failed
    # check never leaves a half-written paper.pdf/manifest pair behind.
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=out))
    try:
        staged_pdf = stage / "paper.pdf"
        staged_pdf.write_bytes(data)
        identity = fetch_pdf.identity_check(staged_pdf, doi, record.get("title"))
        manifest = _manifest(item, "paper.pdf", hashlib.sha256(data).hexdigest(),
                             checks, identity, doi, provider)
        persisted = acquisition_store.persist(dest_dir, data, manifest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    identity = persisted.get("identity") or identity
    record.update(status="file_obtained", file=persisted["file"],
                  manifest=persisted["manifest"], identity=identity,
                  bytes=persisted["bytes"], sha256=persisted["sha256"],
                  api_provider=provider)
    if args.apply:
        if identity.get("status") == "matched" or args.identity_confirmed:
            _apply_ingest(record, persisted["manifest"], key, client, args)
        else:
            record["ingest"] = {"status": "pending_identity_review"}
    return record


def policy_for(provider, args):
    policy = dict(rate_ledger.DEFAULT_POLICIES.get(provider, {}))
    # webvpn_acquire passes --delay for browser pacing; --api-delay keeps the
    # API lane on its own (usually faster) schedule.
    delay = args.api_delay if hasattr(args, "api_delay") else getattr(args, "delay", None)
    if delay is not None:
        policy["min_interval"] = max(float(delay), float(policy.get("min_interval", 0.0)))
    daily = getattr(args, "daily_limit", 0)
    if daily:
        policy["daily_limit"] = int(daily)
    return policy


def _fetch_one(client, item, key, out, args, ledger=None):
    data = _item_data(item)
    doi = (data.get("DOI") or "").strip()
    provider = provider_for_doi(doi)
    record = {"key": key, "title": data.get("title"), "doi": doi,
              "api_provider": provider, "status": "planned", "backend": "api"}
    if not doi:
        record.update(status="skipped", reason="no_doi")
        return record
    if provider == "unsupported":
        record.update(status="skipped", reason="api_not_supported")
        return record
    if not configured(provider):
        record.update(status="skipped", reason="api_key_not_configured")
        return record
    if ledger is not None:
        scope = "api:" + provider
        check = ledger.reserve(scope, policy_for(provider, args))
        if not check.get("allowed"):
            record.update(status="stopped", reason=check.get("reason"),
                          wait_seconds=round(float(check.get("wait_seconds") or 0), 1),
                          cooldown_reason=check.get("cooldown_reason"))
            return record
    result = fetch_api_pdf(doi)
    if ledger is not None:
        ledger.note("api:" + provider, result.get("status"),
                    retry_after=result.get("retry_after"))
    record["api_status"] = result.get("status")
    if result.get("status") != "file_obtained" or not result.get("data"):
        record.update(status="failed", reason=result.get("status"),
                      http_status=result.get("http_status"),
                      detail=result.get("error"))
        return record
    try:
        checks = fetch_pdf.inspect_pdf(result["data"])
    except ValueError as exc:
        record.update(status="failed", reason="not_pdf", detail=str(exc)[:200])
        return record
    try:
        return _save_record(record, result["data"], checks, item, key, out,
                            client, args, provider)
    except acquisition_store.AcquisitionConflict as exc:
        record.update(status="failed", reason="existing_acquisition_conflict",
                      detail=str(exc)[:200])
        return record
    except OSError as exc:
        record.update(status="failed", reason="filesystem_error",
                      detail=str(exc)[:200])
        return record


def _doi_manifest(doi, sha256, checks, identity, provider):
    source_provider = "elsevier_api" if provider == "elsevier" else "wiley_tdm"
    return {
        "schema": SCHEMA,
        "created_at": now(),
        "updated_at": now(),
        "paper": {
            "title": "",
            "doi": doi,
            "identifiers": {"doi": [doi]} if doi else {},
            "authors": [],
            "year": None,
        },
        "main": {
            "id": "main", "kind": "main", "status": "file_obtained", "file": "paper.pdf",
            "sha256": sha256, "content_type": "application/pdf",
            "format_checks": checks, "identity": identity,
            "source": {"provider": source_provider,
                       "url": "https://doi.org/" + doi,
                       "version": "publishedVersion",
                       "evidence": "official_tdm_api"},
        },
        "supplementary": [],
        "read": False,
    }


def _fetch_doi(doi, out, args, ledger=None):
    doi = (doi or "").strip()
    provider = provider_for_doi(doi)
    record = {"doi": doi, "api_provider": provider, "backend": "api",
              "status": "planned"}
    if provider == "unsupported":
        record.update(status="skipped", reason="api_not_supported")
        return record
    if not configured(provider):
        record.update(status="skipped", reason="api_key_not_configured")
        return record
    if args.dry_run:
        record["api_configured"] = True
        return record
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", doi).strip("_")[:80] or "paper"
    dest = out / slug
    try:
        existing = acquisition_store.inspect_existing(dest, doi)
    except acquisition_store.AcquisitionConflict as exc:
        record.update(status="failed", reason="existing_acquisition_conflict",
                      detail=str(exc)[:200])
        return record
    if existing is not None:
        record.update(status="skipped", reason="already_obtained",
                      file=existing["file"], manifest=existing["manifest"],
                      sha256=existing["sha256"], bytes=existing["bytes"],
                      identity=existing.get("identity") or {"status": "not_checked"})
        acquisition_store.write_json(dest / "result.json", record)
        return record
    if ledger is not None:
        check = ledger.reserve("api:" + provider, policy_for(provider, args))
        if not check.get("allowed"):
            record.update(status="stopped", reason=check.get("reason"),
                          wait_seconds=round(float(check.get("wait_seconds") or 0), 1),
                          cooldown_reason=check.get("cooldown_reason"))
            return record
    result = fetch_api_pdf(doi)
    if ledger is not None:
        ledger.note("api:" + provider, result.get("status"),
                    retry_after=result.get("retry_after"))
    if result.get("status") != "file_obtained" or not result.get("data"):
        record.update(status="failed", reason=result.get("status"),
                      http_status=result.get("http_status"), detail=result.get("error"))
        return record
    try:
        checks = fetch_pdf.inspect_pdf(result["data"])
    except ValueError as exc:
        record.update(status="failed", reason="not_pdf", detail=str(exc)[:200])
        return record
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=out))
    try:
        staged_pdf = stage / "paper.pdf"
        staged_pdf.write_bytes(result["data"])
        identity = fetch_pdf.identity_check(staged_pdf, doi, None)
        manifest = _doi_manifest(doi, hashlib.sha256(result["data"]).hexdigest(),
                                 checks, identity, provider)
        try:
            persisted = acquisition_store.persist(dest, result["data"], manifest)
        except acquisition_store.AcquisitionConflict as exc:
            record.update(status="failed", reason="existing_acquisition_conflict",
                          detail=str(exc)[:200])
            return record
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    identity = persisted.get("identity") or identity
    record.update(status="file_obtained", file=persisted["file"],
                  manifest=persisted["manifest"], sha256=persisted["sha256"],
                  bytes=persisted["bytes"], identity=identity, checks=checks)
    acquisition_store.write_json(dest / "result.json", record)
    return record


def _record_is_success(record):
    return acquisition_store.is_success(record)


def _ledger_for(args):
    if getattr(args, "no_state", False):
        return rate_ledger.RateLedger(None)
    return rate_ledger.RateLedger(getattr(args, "state", None)
                                  or rate_ledger.default_state_path())


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger = _ledger_for(args)
    if args.doi:
        record = _fetch_doi(args.doi, out, args, ledger=ledger)
        acquisition_store.write_json(out / "results.json", record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if record.get("status") in ("file_obtained", "ingested", "ingested_partial"):
            return 0
        if record.get("status") == "skipped" and record.get("reason") == "already_obtained":
            return 0
        if record.get("status") == "planned" and args.dry_run:
            return 0
        return 1
    keys = load_keys(args.keys, args.keys_file)
    if not keys:
        raise ValueError("Provide --keys/--keys-file or --doi")
    client = ZoteroLocal(args.base_url, max_items=args.max_items, max_pages=args.max_pages)
    records = []
    for key in keys:
        try:
            item = client.get(key, library=args.library)
        except ZoteroError as exc:
            records.append({"key": key, "status": "failed", "error": exc.to_dict()})
            continue
        if not args.force and has_main_pdf(client, key, args.library):
            records.append({"key": key, "status": "skipped", "reason": "main_pdf_exists"})
            continue
        if len([r for r in records if r.get("status") in
                ("file_obtained", "ingested", "ingested_partial")]) >= args.max_total:
            records.append({"key": key, "status": "stopped", "reason": "max_total"})
            continue
        if args.dry_run:
            doi = (_item_data(item).get("DOI") or "").strip()
            provider = provider_for_doi(doi)
            check = ledger.check("api:" + provider, policy_for(provider, args))
            records.append({"key": key, "doi": doi, "api_provider": provider,
                            "api_configured": configured(provider),
                            "ledger": {k: check.get(k) for k in
                                       ("allowed", "reason", "wait_seconds")},
                            "status": "planned"})
            continue
        time.sleep(args.delay + random.uniform(0, max(0.0, args.jitter)))
        record = _fetch_one(client, item, key, out, args, ledger=ledger)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    result = {"schema": "sanmu-publisher-api/v1", "status": "partial",
              "state": str(ledger.path) if ledger.path else None, "items": records}
    result["ledger"] = ledger.snapshot(
        sorted({r.get("api_provider") for r in records if r.get("api_provider")}),
        {name: policy_for(name, args) for name in
         {r.get("api_provider") for r in records if r.get("api_provider")}})
    result["status"] = ("success" if records and all(_record_is_success(r) for r in records)
                        else "partial")
    acquisition_store.write_json(out / "results.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fetch"], help="fetch PDFs by Zotero keys or one DOI")
    parser.add_argument("--keys", help="Comma-separated Zotero item keys")
    parser.add_argument("--keys-file", help="One Zotero item key per line")
    parser.add_argument("--doi", help="Fetch one DOI without Zotero (writes a DOI-only manifest)")
    parser.add_argument("--out", required=True, help="Batch output directory")
    parser.add_argument("--library", default="user")
    parser.add_argument("--base-url", default="http://127.0.0.1:23119")
    parser.add_argument("--max-items", type=int, default=1000)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-total", type=int, default=50)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--jitter", type=float, default=DEFAULT_JITTER)
    parser.add_argument("--state", help="Persistent request-budget JSON path")
    parser.add_argument("--no-state", action="store_true",
                        help="Disable cross-run request accounting (not recommended)")
    parser.add_argument("--daily-limit", type=int, default=0,
                        help="Override the local per-provider daily request budget")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--identity-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result, code = None, run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        result, code = {"status": "failed", "error": str(exc)}, 2
    except Exception as exc:
        result, code = {"status": "failed", "error": type(exc).__name__ + ": " + str(exc)[:300]}, 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
