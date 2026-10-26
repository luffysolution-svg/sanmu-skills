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


SENSITIVE = {"apikey", "key", "token", "accesstoken", "email", "mailto", "signature", "credential",
             "password", "secret", "authorization", "xapikey", "xamzsignature", "xamzcredential", "xamzsecuritytoken"}


def public_url(value):
    parts = urlsplit(value)
    query = [(k, "[REDACTED]" if re.sub(r"[^a-z]", "", k.casefold()) in SENSITIVE else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    # Preserve explicit ports and IPv6 brackets while removing user information.
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), ""))


def inspect_pdf(data):
    if not data.startswith(b"%PDF-"):
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
    title_found = bool(title and clean(title) and clean(title) in clean(heading))
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


def _normalize_candidate_pdf_url(url):
    u = str(url or "").strip()
    if not u:
        return u
    # Convert arXiv abstract URL to PDF URL automatically
    m_arx = re.match(r"^https?://arxiv\.org/abs/([^\s?#]+)", u, flags=re.I)
    if m_arx:
        arx_id = m_arx.group(1).removesuffix(".pdf")
        return f"https://arxiv.org/pdf/{arx_id}.pdf"
    if re.match(r"^https?://arxiv\.org/pdf/[^\s?#]+$", u, flags=re.I) and not u.lower().endswith(".pdf"):
        return u.replace("http://", "https://", 1) + ".pdf"
    return u


def _download_pdf_bytes(url, max_bytes=80 * 1024 * 1024):
    url = _normalize_candidate_pdf_url(url)
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
            or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError("A public HTTPS URL without embedded credentials is required")
    parts.port  # Reject malformed port syntax before issuing a request.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 sanmu-paper-search/1.6",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    last_exc = None
    for attempt in range(3):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=40) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("insecure_redirect: HTTPS is required")
                data = response.read(max_bytes + 1)
                final_url = response.url
                content_type = response.headers.get_content_type()
            if len(data) > max_bytes:
                raise ValueError("response_too_large: PDF exceeds the download budget")
            checks = inspect_pdf(data)
            return data, url, final_url, content_type, checks
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            if isinstance(exc, HTTPError):
                last_exc = exc
                break
            last_exc = exc
            if attempt < 2:
                import time
                time.sleep(1.2 * (attempt + 1))
                continue
            break
        except ValueError as exc:
            last_exc = exc
            break
    raise last_exc


def fetch(url, out, doi=None, title=None, max_bytes=80 * 1024 * 1024, fallback_urls=None):
    candidates = []
    for u in [url] + list(fallback_urls or []):
        if u and u not in candidates:
            candidates.append(u)
    if not candidates:
        raise ValueError("A public HTTPS URL without embedded credentials is required")
    out = Path(out)
    if out.exists():
        raise ValueError("Output directory already exists; use a new directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pdf-stage-", dir=out.parent))
    try:
        data = source_used = final_url = content_type = checks = None
        last_error = None
        tried_urls = []
        pdf = stage / "paper.pdf"
        first_unverified = None
        for cand in candidates:
            tried_urls.append(public_url(cand))
            try:
                candidate = _download_pdf_bytes(cand, max_bytes=max_bytes)
                pdf.write_bytes(candidate[0])
                candidate_identity = identity_check(pdf, doi, title)
                if candidate_identity["status"] == "needs_review":
                    if first_unverified is None:
                        first_unverified = candidate, candidate_identity
                    continue
                data, source_used, final_url, content_type, checks = candidate
                identity = candidate_identity
                break
            except (HTTPError, URLError, socket.timeout, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                continue
        if data is None and first_unverified is not None:
            candidate, identity = first_unverified
            data, source_used, final_url, content_type, checks = candidate
            pdf.write_bytes(data)
        if data is None:
            if last_error is not None:
                raise last_error
            raise ValueError("not_pdf: no candidate URL returned a valid PDF stream")
        record = {"schema_version": "1.0", "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "file_obtained", "source_url": public_url(source_used), "final_url": public_url(final_url), "tried_urls": tried_urls, "content_type": content_type, "file": "paper.pdf", "sha256": hashlib.sha256(data).hexdigest(), "format_checks": checks, "identity": identity, "requested_identity": {"doi": doi, "title": title}, "license": "not_determined", "read": False}
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
    parser.add_argument("--fallback-url", action="append", default=[], help="Optional fallback OA PDF URLs to try if primary fails")
    args = parser.parse_args()
    try:
        result = fetch(args.url, args.out, args.doi, args.title, fallback_urls=args.fallback_url)
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
