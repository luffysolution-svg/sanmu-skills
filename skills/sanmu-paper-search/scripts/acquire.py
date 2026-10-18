"""Acquire one paper: explicit OA PDFs -> optional ScanSci OA -> browser handoff.

Output manifest.json (sanmu-acquisition/v1) keeps full paper metadata, main and
supplementary entries with relative file, sha256, size, identity, source, attempts.
Existing output can resume only the same paper; obtained bytes are never replaced.
SI discovery uses real publisher anchors and requires explicit --select-si IDs.
No Zotero access, archive extraction, cookie handling or automated login occurs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
import uuid

from _core import InputError, Redactor, doi as normalize_doi, dumps, license_status, load_papers, now, read_json
import fetch_pdf
import scansci_backend

SCHEMA = "sanmu-acquisition/v1"
MAX_BYTES = 80 * 1024 * 1024
TYPES = {"pdf", "zip", "docx", "xlsx", "pptx", "csv", "tsv", "txt", "json", "xml", "png", "jpeg", "tiff", "gzip"}
SUPPLEMENT = re.compile(r"supplement(?:ary|al)?|supporting[ _-]*(?:information|material|data)|附加材料|补充材料|电子附件|(?:^|[/_.-])(?:esm|supp|si)[/_.-]", re.I)


def safe_url(url):
    try:
        p = urlsplit(url)
        return bool(p.scheme == "https" and p.hostname and not p.username and not p.password)
    except (ValueError, TypeError):
        return False


def public_url(url):
    return Redactor().text(fetch_pdf.public_url(url)) if safe_url(url) else None


def attachment_type(url):
    path = urlsplit(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    ext = {"jpg": "jpeg", "tif": "tiff", "gz": "gzip"}.get(ext, ext)
    return ext if ext in TYPES else "unknown"


def entry(identifier, kind, source=None, name=None, type="pdf"):
    value = {"id": identifier, "kind": kind, "name": name or identifier,
             "attachment_type": type, "status": "not_obtained", "file": None,
             "sha256": None, "size": None, "identity": {"status": "needs_review", "reason": "file_not_obtained"},
             "source": source or {}, "attempts": [], "read": False}
    if kind == "supplementary":
        value["relation"] = {"type": "supplement_to", "target": "main"}
    return value


def source_record(value=None, **extra):
    value = value or {}
    result = {k: value.get(k) for k in ("provider", "license", "version", "evidence")}
    result.update({"url": public_url(value.get("url")), "is_oa": value.get("is_oa") if isinstance(value.get("is_oa"), bool) else None,
                   "license_status": license_status(value.get("license"))})
    result.update(extra)
    return result


def select_paper(papers=None, index=None, doi=None, metadata=None):
    wanted = normalize_doi(doi)
    if doi and not wanted:
        raise InputError("Invalid DOI")
    if papers:
        records = load_papers(papers)
        if index is not None:
            if index < 1 or index > len(records):
                raise InputError("--index is 1-based and must select an existing paper")
            paper = records[index - 1]
            if wanted and wanted not in [normalize_doi(d) for d in paper["identifiers"].get("doi", [])]:
                raise InputError("--index and --doi select different papers")
        else:
            matches = [p for p in records if wanted in [normalize_doi(d) for d in p["identifiers"].get("doi", [])]] if wanted else records
            if len(matches) != 1:
                raise InputError("Select exactly one paper with --index or --doi")
            paper = matches[0]
    else:
        if not wanted:
            raise InputError("Use --papers or --doi")
        paper = read_json(metadata) if metadata else {}
        if not isinstance(paper, dict):
            raise InputError("--metadata must contain one paper object")
        existing = paper.get("identifiers", {}).get("doi", [])
        if paper.get("doi"):
            existing = [*existing, paper["doi"]]
        if any(normalize_doi(d) != wanted for d in existing):
            raise InputError("Metadata DOI conflicts with requested DOI")
        paper = copy.deepcopy(paper)
        paper.setdefault("identifiers", {})["doi"] = [wanted]
        paper.setdefault("authors", [])
        paper.setdefault("title", "")
    paper = copy.deepcopy(paper)
    dois = paper.get("identifiers", {}).get("doi", [])
    if len(dois) > 1:
        raise InputError("Conflicting paper DOIs; resolve identity before acquisition")
    paper["doi"] = normalize_doi(dois[0]) if dois else wanted
    return paper


def oa_candidates(paper, limit=3):
    result, seen = [], set()
    for location in paper.get("oa_locations", []):
        if not isinstance(location, dict) or location.get("is_oa") is not True:
            continue
        url = location.get("url")
        if not safe_url(url) or url in seen:
            continue
        # Normalized providers put PDF URLs in oa_locations, but landing pages
        # must not be blindly downloaded. Explicit PDF evidence also supports query endpoints.
        if not (attachment_type(url) == "pdf" or "/pdf" in urlsplit(url).path.lower()
                or "pdf" in str(location.get("evidence", "")).lower()):
            continue
        seen.add(url)
        result.append(location)
        if len(result) >= limit:
            break
    return result


class PublisherLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.current = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.current = {"url": attrs.get("href", ""), "text": attrs.get("title", ""), "type": attrs.get("type", "")}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def discover_si(html, base_url, limit=20):
    if not safe_url(base_url):
        raise InputError("Publisher base URL must be HTTPS without credentials")
    parser = PublisherLinks()
    parser.feed(html)
    found, seen = [], set()
    for link in parser.links:
        href = link["url"].strip()
        if not href or href.startswith("#"):
            continue
        url = urljoin(base_url, href)
        label = " ".join(link["text"].split())
        if not safe_url(url) or url in seen or not SUPPLEMENT.search(label + " " + urlsplit(url).path):
            continue
        typ = attachment_type(url)
        if typ == "unknown":
            typ = {"application/pdf": "pdf", "application/zip": "zip", "text/csv": "csv"}.get(link["type"], "unknown")
        if typ == "unknown":
            continue
        seen.add(url)
        sid = "si-" + hashlib.sha256(url.encode()).hexdigest()[:12]
        item = entry(sid, "supplementary", source_record({"url": url, "provider": "publisher_html",
                     "evidence": "actual_anchor; explicit_supplement_label"}), label, typ)
        item.update({"status": "discovered", "discovered_on": public_url(base_url), "selected": False})
        # Raw URL kept only in memory, never persisted with tokens.
        found.append((item, url))
        if len(found) >= limit:
            break
    return found


def inspect_attachment(data, typ):
    if typ == "pdf":
        return fetch_pdf.inspect_pdf(data)
    valid = False
    if typ in {"zip", "docx", "xlsx", "pptx"}:
        valid = data.startswith((b"PK\x03\x04", b"PK\x05\x06"))
    elif typ == "gzip":
        valid = data.startswith(b"\x1f\x8b")
    elif typ == "png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif typ == "jpeg":
        valid = data.startswith(b"\xff\xd8\xff")
    elif typ == "tiff":
        valid = data.startswith((b"II*\x00", b"MM\x00*"))
    elif typ in {"txt", "csv", "tsv", "json", "xml"}:
        text = data.decode("utf-8-sig")
        if re.search(r"<!doctype\s+html|<html\b|<script\b", text[:4096], re.I):
            raise ValueError("html_or_login_response")
        valid = bool(text.strip()) and "\x00" not in text
        if typ == "json":
            json.loads(text)
        if typ == "xml":
            valid = valid and text.lstrip().startswith("<")
    if not valid:
        raise ValueError("attachment_signature_mismatch")
    return {"signature": "container_only" if typ in {"docx", "xlsx", "pptx"} else "checked",
            "declared_type": typ, "bytes": len(data), "archive_extracted": False}


def write_manifest(root, manifest):
    fd, name = tempfile.mkstemp(prefix=".manifest-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(Redactor().clean(manifest)) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, root / "manifest.json")
    finally:
        Path(name).unlink(missing_ok=True)


def _error(exc):
    from urllib.error import HTTPError, URLError
    if isinstance(exc, HTTPError):
        return {"status": "failed", "reason": "http_error", "http_status": exc.code}
    if isinstance(exc, (URLError, TimeoutError)):
        return {"status": "failed", "reason": "network_error"}
    if isinstance(exc, ValueError):
        msg = str(exc)
        safe = {
            "html_or_login_response", "not_pdf: response does not start with a PDF header",
            "incomplete_pdf: PDF trailer is missing", "attachment_signature_mismatch",
            "response_too_large", "insecure_redirect", "invalid_import_file_or_size",
            "invalid_file"
        }
        return {"status": "failed", "reason": msg if msg in safe else "invalid_file"}
    return {"status": "failed", "reason": "filesystem_error"}


def verify_existing(root, item):
    path = Path(item.get("file") or "")
    target = (root / path).resolve()
    if path.is_absolute() or ".." in path.parts or not target.is_relative_to(root.resolve()):
        raise InputError("Unsafe existing manifest path")
    if not target.is_file() or target.stat().st_size != item.get("size") or hashlib.sha256(target.read_bytes()).hexdigest() != item.get("sha256"):
        raise InputError("Existing acquired file changed or missing; refusing overwrite")


def obtain(root, item, paper, url=None, imported=None, source=None):
    if item["status"] == "file_obtained":
        verify_existing(root, item)
        return
    attempt = {"at": now(), "provider": "authorized_browser_import" if imported else (source or {}).get("provider"),
               "url": public_url(url), "status": "started"}
    item["attempts"].append(attempt)
    typ = item["attachment_type"]
    target_dir = root / "files" / (item["id"] + "-" + uuid.uuid4().hex)
    try:
        if imported:
            path = Path(imported)
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                raise ValueError("invalid_import_file_or_size")
            data = path.read_bytes()
            checks = inspect_attachment(data, typ)
        elif typ == "pdf":
            record = fetch_pdf.fetch(url, target_dir, paper.get("doi") if item["kind"] == "main" else None,
                                     paper.get("title") if item["kind"] == "main" else None, max_bytes=MAX_BYTES)
            path = target_dir / "paper.pdf"
            data = path.read_bytes()
            checks = record["format_checks"]
        else:
            if not safe_url(url):
                raise ValueError("invalid_url")
            with urlopen(Request(url, headers={"User-Agent": "sanmu-paper-search/1.0"}), timeout=40) as response:
                if not safe_url(response.url):
                    raise ValueError("insecure_redirect")
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("response_too_large")
            checks = inspect_attachment(data, typ)
        if imported or typ != "pdf":
            target_dir.mkdir(parents=True, exist_ok=False)
            path = target_dir / ("attachment." + typ)
            with path.open("xb") as f:
                f.write(data)
        hints = fetch_pdf.identity_check(path, paper.get("doi"), paper.get("title")) if typ == "pdf" and item["kind"] == "main" else {"status": "not_checked"}
        item.update({"status": "file_obtained", "file": path.relative_to(root).as_posix(),
                     "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "format_checks": checks,
                     "identity": {"status": "needs_review", "reason": "manual_identity_and_version_review_required", "hints": hints},
                     "source": source_record(source), "obtained_at": now()})
        if not imported and typ == "pdf":
            item["source"]["final_url"] = record.get("final_url")
        attempt["status"] = "file_obtained"
    except (OSError, ValueError, TimeoutError) as exc:
        attempt.update(_error(exc))
        item["status"] = "failed"


def acquire(paper, out, backend="auto", max_candidates=3, import_file=None, import_kind="main",
            import_id=None, import_type="pdf", import_source=None, publisher_html=None,
            publisher_url=None, select_si=(), max_si=20, cache=None, install=True,
            authorized_profile=None, use_tool_profile=False):
    """Return the persisted manifest. Network work is bounded; reruns append attempts."""
    if backend not in {"auto", "none", "scansci"} or not 1 <= max_candidates <= 10 or not 1 <= max_si <= 100:
        raise InputError("Invalid backend or candidate budget")
    if select_si and not (publisher_html or publisher_url):
        raise InputError("SI selection requires --publisher-html/--publisher-url discovery")
    if publisher_html and not publisher_url:
        raise InputError("Publisher HTML requires a base URL")
    if import_kind not in {"main", "supplementary"} or import_type not in TYPES:
        raise InputError("Invalid import kind or attachment type")
    if import_kind == "main" and import_type != "pdf":
        raise InputError("Main paper imports must be PDF")
    if import_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", import_id):
        raise InputError("Unsafe import ID")
    if import_file and import_kind == "supplementary" and not import_id:
        path = Path(import_file)
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise InputError("Invalid SI import file or size")
        import_id = "si-import-" + hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".acquire.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise InputError("Acquisition already running; stale lock requires manual inspection") from exc
    manifest = None
    try:
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            if manifest.get("schema") != SCHEMA or manifest.get("paper", {}).get("identifiers") != paper.get("identifiers"):
                raise InputError("Existing output belongs to a different paper or schema")
            if manifest["paper"].get("title") != paper.get("title"):
                raise InputError("Existing output title differs; resolve metadata before resuming")
            for item in [manifest["main"], *manifest["supplementary"]]:
                if item["status"] == "file_obtained":
                    verify_existing(root, item)
        else:
            if any(p != lock for p in root.iterdir()):
                raise InputError("Nonempty output has no acquisition manifest; refusing adoption")
            manifest = {"schema": SCHEMA, "created_at": now(), "paper": copy.deepcopy(paper),
                        "main": entry("main", "main"), "supplementary": [], "discovery": [],
                        "backend": {"status": "not_attempted"}, "read": False}
        def save():
            manifest["updated_at"] = now()
            main_ok = manifest["main"]["status"] == "file_obtained"
            si_issues = any(s.get("selected") and s["status"] != "file_obtained" for s in manifest["supplementary"])
            latest_discovery = {d.get("url"): d for d in manifest["discovery"]}
            discovery_issues = any(d["status"] != "ok" or d.get("unmatched_selected_ids") for d in latest_discovery.values())
            manifest["status"] = ("partial" if si_issues or discovery_issues else "file_obtained") if main_ok else "needs_browser"
            write_manifest(root, manifest)
        manifest["handoff"] = scansci_backend.browser_handoff(paper.get("doi"), authorized_profile, use_tool_profile)
        save()
        if import_file:
            item = manifest["main"]
            if import_kind == "supplementary":
                sid = import_id or "si-import-" + uuid.uuid4().hex[:12]
                matches = [s for s in manifest["supplementary"] if s["id"] == sid]
                item = matches[0] if matches else entry(sid, "supplementary", type=import_type)
                if not matches:
                    manifest["supplementary"].append(item)
                item["selected"] = True
            if item["status"] == "file_obtained":
                incoming = Path(import_file)
                if incoming.stat().st_size > MAX_BYTES or hashlib.sha256(incoming.read_bytes()).hexdigest() != item["sha256"]:
                    raise InputError("Import conflicts with existing entry; choose a new SI ID or output")
            obtain(root, item, paper, imported=import_file,
                   source={"provider": "authorized_browser_import", **(import_source or {}),
                           "evidence": "user_supplied_authorized_download"})
            save()
        main = manifest["main"]
        if main["status"] != "file_obtained":
            for candidate in oa_candidates(paper, max_candidates):
                obtain(root, main, paper, url=candidate["url"], source=candidate)
                save()
                if main["status"] == "file_obtained":
                    break
        if main["status"] != "file_obtained" and backend != "none" and paper.get("doi"):
            resolved = scansci_backend.resolve(paper["doi"], cache=cache, install=install)
            manifest["backend"] = {k: v for k, v in resolved.items() if k not in {"python", "source", "candidates"}}
            save()
            for candidate in resolved.get("candidates", [])[:max_candidates]:
                if candidate.get("is_oa") is not True or not safe_url(candidate.get("url")):
                    manifest["backend"]["status"] = "unsupported_policy"
                    save()
                    continue
                obtain(root, main, paper, url=candidate["url"], source=candidate)
                save()
                if main["status"] == "file_obtained":
                    break
        elif backend == "none":
            manifest["backend"] = {"status": "disabled"}
        if publisher_html or publisher_url:
            discovery = {"at": now(), "url": public_url(publisher_url), "status": "started"}
            manifest["discovery"].append(discovery)
            save()
            try:
                if publisher_html:
                    if Path(publisher_html).stat().st_size > scansci_backend.MAX_JSON:
                        raise ValueError("html_too_large")
                    html = Path(publisher_html).read_text(encoding="utf-8-sig")
                else:
                    if not safe_url(publisher_url):
                        raise ValueError("invalid_publisher_url")
                    with urlopen(Request(publisher_url, headers={"User-Agent": "sanmu-paper-search/1.0"}), timeout=30) as response:
                        if not safe_url(response.url):
                            raise ValueError("insecure_redirect")
                        data = response.read(scansci_backend.MAX_JSON + 1)
                        publisher_url = response.url
                    if len(data) > scansci_backend.MAX_JSON:
                        raise ValueError("html_too_large")
                    html = data.decode("utf-8")
                discovered = discover_si(html, publisher_url, max_si)
                discovery.update({"status": "ok", "candidates": len(discovered), "limit": max_si,
                                  "completeness": "not_asserted; bounded_anchor_discovery"})
                known = {s["id"]: s for s in manifest["supplementary"]}
                for new, url in discovered:
                    item = known.get(new["id"])
                    if item is None:
                        item = new
                        manifest["supplementary"].append(item)
                    if item["id"] in select_si:
                        item["selected"] = True
                        # Explicit selection authorizes this public attachment attempt;
                        # a discovered URL alone never asserts an OA license.
                        obtain(root, item, paper, url=url, source={**item["source"], "url": url})
                    save()
                unmatched = set(select_si) - {v[0]["id"] for v in discovered}
                if unmatched:
                    discovery["unmatched_selected_ids"] = sorted(unmatched)
            except (OSError, ValueError, TimeoutError) as exc:
                discovery.update(_error(exc))
        if main["status"] == "file_obtained":
            manifest["handoff"]["status"] = "not_needed"
        save()
        return manifest
    finally:
        lock.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--papers", type=Path, help="Existing normalized papers.json (or run directory)")
    parser.add_argument("--index", type=int, help="1-based paper selection")
    parser.add_argument("--doi", help="Paper DOI; may select a record from --papers")
    parser.add_argument("--metadata", type=Path, help="One paper JSON object, used with --doi")
    parser.add_argument("--out", required=True, type=Path, help="New output directory or same-paper acquisition to resume")
    parser.add_argument("--backend", choices=("auto", "none", "scansci"), default="auto")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--no-install", action="store_true", help="Disable isolated ScanSci preparation")
    parser.add_argument("--import-file", type=Path, help="PDF or explicitly typed SI obtained by an authorized browser")
    parser.add_argument("--import-kind", choices=("main", "supplementary"), default="main")
    parser.add_argument("--import-id", help="Existing discovered SI ID (or a new import ID)")
    parser.add_argument("--import-type", choices=sorted(TYPES), default="pdf")
    parser.add_argument("--source-url", help="Provenance URL for imported file")
    parser.add_argument("--version", help="User-declared version; retained without verification")
    parser.add_argument("--license", help="User-declared license; unknown remains unknown")
    parser.add_argument("--publisher-html", type=Path, help="Saved publisher HTML; requires --publisher-url as base")
    parser.add_argument("--publisher-url", help="Publisher page/base URL; fetched only when no saved HTML supplied")
    parser.add_argument("--select-si", action="append", default=[], help="Explicit discovered si-ID to obtain; repeatable")
    parser.add_argument("--max-si", type=int, default=20)
    parser.add_argument("--authorized-profile", type=Path, help="User-asserted authorized browser profile (handoff only)")
    parser.add_argument("--use-tool-profile", action="store_true", help="Authorize reusing ScanSci's own discovered profile for manual browser handoff")
    args = parser.parse_args(argv)
    if args.import_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.import_id):
        parser.error("--import-id must contain only letters, digits, underscore or hyphen")
    if args.publisher_html and not args.publisher_url:
        parser.error("--publisher-html requires --publisher-url")
    if args.papers and args.metadata:
        parser.error("--metadata cannot be combined with --papers")
    try:
        paper = select_paper(args.papers, args.index, args.doi, args.metadata)
        result = acquire(paper, args.out, args.backend, args.max_candidates, args.import_file,
                         args.import_kind, args.import_id, args.import_type,
                         {"url": args.source_url, "version": args.version, "license": args.license},
                         args.publisher_html, args.publisher_url, args.select_si, args.max_si,
                         args.cache_dir, not args.no_install, args.authorized_profile, args.use_tool_profile)
        code = 0 if result["status"] == "file_obtained" else 2
    except (InputError, OSError, ValueError) as exc:
        result, code = {"status": "error", "reason": str(exc) if isinstance(exc, InputError) else "invalid_input_or_filesystem_error"}, 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(dumps(Redactor().clean(result)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
