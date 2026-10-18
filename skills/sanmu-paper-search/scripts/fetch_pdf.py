"""Fetch a public PDF and preserve acquisition evidence without claiming it was read."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SENSITIVE = {"api_key", "apikey", "key", "token", "access_token", "email", "mailto", "signature", "credential", "x-amz-signature", "x-amz-credential", "x-amz-security-token"}


def public_url(value):
    parts = urlsplit(value)
    query = [(k, "[REDACTED]" if k.lower() in SENSITIVE else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, urlencode(query), ""))


def inspect_pdf(data):
    if not data.startswith(b"%PDF-"):
        if re.search(rb"(?i)<!doctype\s+html|<html\b|<script\b|recaptcha|challenge", data[:4096]):
            raise ValueError("html_or_login_response")
        raise ValueError("not_pdf: response does not start with a PDF header")
    if b"%%EOF" not in data[-4096:]:
        raise ValueError("incomplete_pdf: PDF trailer is missing")
    return {"header": True, "trailer": True, "bytes": len(data)}


def assess_identity_text(text, doi=None, title=None):
    # Only the opening title block is identity evidence; reference text is not.
    heading = re.split(r"(?im)^\s*(?:abstract|references|bibliography|参考文献)\s*[:：]?\s*$", text)[0]
    heading = heading[:3000]
    clean = lambda s: re.sub(r"[^\w]", "", s.casefold())
    normalize_doi = lambda s: re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", s.strip(), flags=re.I).casefold().rstrip(".,;:)")
    observed = {normalize_doi(s) for s in re.findall(r"10\.\d{4,9}/[^\s<>\"']+", heading, flags=re.I)}
    doi_found = bool(doi and normalize_doi(doi) in observed)
    title_found = bool(title and clean(title) in clean(heading))
    matched = (doi_found if doi else title_found) and (title_found if title else True)
    return {"status": "matched" if matched else "needs_review", "method": "first_page_opening_text", "doi_found": doi_found, "title_found": title_found, "scope": "identity_hint_only; not_full_content_or_version_verification"}


def identity_check(path, doi=None, title=None):
    result = {"status": "not_checked", "method": "none"}
    if not doi and not title:
        return result
    try:
        import fitz
    except ImportError:
        return {"status": "not_checked", "reason": "optional_pymupdf_unavailable"}
    try:
        with fitz.open(path) as doc:
            text = doc[0].get_text() if len(doc) else ""
        return assess_identity_text(text, doi, title)
    except Exception:
        return {"status": "needs_review", "reason": "pdf_text_unreadable"}


def fetch(url, out, doi=None, title=None, max_bytes=80 * 1024 * 1024):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("A public HTTPS URL without embedded credentials is required")
    out = Path(out)
    if out.exists():
        raise ValueError("Output directory already exists; use a new directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pdf-stage-", dir=out.parent))
    try:
        request = Request(url, headers={"User-Agent": "sanmu-paper-search/1.0", "Accept": "application/pdf"})
        with urlopen(request, timeout=40) as response:
            if urlsplit(response.url).scheme != "https":
                raise ValueError("insecure_redirect: HTTPS is required")
            data = response.read(max_bytes + 1)
            final_url = response.url
            content_type = response.headers.get_content_type()
        if len(data) > max_bytes:
            raise ValueError("response_too_large: PDF exceeds the download budget")
        checks = inspect_pdf(data)
        pdf = stage / "paper.pdf"
        pdf.write_bytes(data)
        identity = identity_check(pdf, doi, title)
        record = {"schema_version": "1.0", "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "file_obtained", "source_url": public_url(url), "final_url": public_url(final_url), "content_type": content_type, "file": "paper.pdf", "sha256": hashlib.sha256(data).hexdigest(), "format_checks": checks, "identity": identity, "requested_identity": {"doi": doi, "title": title}, "license": "not_determined", "read": False}
        (stage / "acquisition.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if out.exists():
            raise ValueError("Output directory appeared during download; refusing overwrite")
        stage.rename(out)
        return record
    finally:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--out", required=True, help="New acquisition directory")
    parser.add_argument("--doi")
    parser.add_argument("--title")
    args = parser.parse_args()
    try:
        result = fetch(args.url, args.out, args.doi, args.title)
        code = 0
    except HTTPError as exc:
        result, code = {"ok": False, "error": "http_error", "http_status": exc.code}, 2
    except (URLError, socket.timeout, TimeoutError):
        result, code = {"ok": False, "error": "network_error"}, 2
    except (ValueError, OSError) as exc:
        result, code = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "filesystem_error"}, 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
