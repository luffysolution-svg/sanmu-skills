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
from urllib.request import ProxyHandler, Request, build_opener, urlopen

_DEFAULT_URLOPEN = urlopen
_DEFAULT_BUILD_OPENER = build_opener


SENSITIVE = {"apikey", "key", "token", "accesstoken", "email", "mailto", "signature", "credential",
             "password", "secret", "authorization", "xapikey", "xamzsignature", "xamzcredential", "xamzsecuritytoken"}


def public_url(value):
    parts = urlsplit(value)
    query = [(k, "[REDACTED]" if re.sub(r"[^a-z]", "", k.casefold()) in SENSITIVE else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    # Preserve explicit ports and IPv6 brackets while removing user information.
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), ""))


def inspect_pdf(data):
    if b"%PDF-" not in data[:1024]:
        raise ValueError("not_pdf: response does not start with a PDF header")
    if b"%%EOF" not in data[-65536:]:
        raise ValueError("incomplete_pdf: PDF trailer is missing")
    return {"header": True, "trailer": True, "bytes": len(data)}


def assess_identity_text(text, doi=None, title=None):
    # Strip from reference headings only; many journals print the paper's own DOI in the first-page footer below the abstract.
    heading = re.split(r"(?im)^\s*(?:references|bibliography|参考文献)\s*[:：]?\s*$", text)[0]
    heading = heading[:5000]
    clean = lambda s: re.sub(r"[^\w]", "", s.casefold())
    normalize_doi = lambda s: re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", s.strip(), flags=re.I).casefold().rstrip(".,;:)]>")
    observed = {normalize_doi(s) for s in re.findall(r"10\.\d{4,9}/[^\s<>\"']+", heading, flags=re.I)}
    doi_found = bool(doi and normalize_doi(doi) in observed)
    has_conflicting_doi = bool(doi and observed and not doi_found)
    title_found = bool(title and clean(title) and clean(title) in clean(heading))
    matched = (doi_found if doi else title_found) and (title_found if title else True)
    return {"status": "matched" if matched else "needs_review", "method": "first_page_opening_text", "doi_found": doi_found, "title_found": title_found, "has_conflicting_doi": has_conflicting_doi, "scope": "identity_hint_only; not_full_content_or_version_verification"}


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


def publisher_pdf_candidates(landing_url):
    candidates = []
    parts = urlsplit(str(landing_url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        return candidates
    host = parts.hostname.lower()
    path = parts.path or ""
    origin = f"https://{parts.netloc}"
    if "sciencedirect.com" in host and "/article/pii/" in path:
        candidates.append(f"{origin}{path}/pdfft?isDTMRedir=true&download=true")
    if "springer.com" in host or "nature.com" in host:
        m = re.search(r"/articles?/(10\.\d+/[^\s?#]+)", path)
        if m:
            candidates.append(f"https://{host}/content/pdf/{m.group(1)}.pdf")
        elif not path.endswith(".pdf"):
            candidates.append(f"{origin}{path}.pdf")
    if "wiley.com" in host and "/doi/" in path:
        candidates.append(re.sub(r"/doi/(?:abs|full|epdf)/", "/doi/pdfdirect/", f"{origin}{path}") + "?download=true")
    if "tandfonline.com" in host and "/doi/" in path:
        candidates.append(re.sub(r"/doi/(?:abs|full)/", "/doi/pdf/", f"{origin}{path}"))
    if "mdpi.com" in host:
        candidates.append(f"{origin}{path.removesuffix('/htm').rstrip('/')}/pdf")
    if "ieeexplore.ieee.org" in host:
        m = re.search(r"/document/(\d+)", path)
        if m:
            candidates.append(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}")
    if "dl.acm.org" in host or "pubs.acs.org" in host or "pnas.org" in host:
        candidates.append(re.sub(r"/doi/(?:abs|full)/", "/doi/pdf/", f"{origin}{path}").replace("/doi/10.", "/doi/pdf/10."))
    if "rsc.org" in host and "/articlelanding/" in path:
        candidates.append(f"{origin}{path.replace('/articlelanding/', '/articlepdf/')}")
    if "frontiersin.org" in host and "/articles/10." in path:
        candidates.append(f"{origin}{path.removesuffix('/full').rstrip('/')}/pdf")
    if ("biorxiv.org" in host or "medrxiv.org" in host) and path.startswith("/content/"):
        base_p = re.sub(r"(?:\.full)?(?:\.pdf)?$", "", path)
        candidates.append(f"{origin}{base_p}.full.pdf")
    if "arxiv.org" in host and path.startswith("/abs/"):
        candidates.append(f"https://arxiv.org/pdf/{path[len('/abs/'):].removesuffix('.pdf')}.pdf")
    return list(dict.fromkeys(candidates))


def resolve_doi_pdf_candidates(doi_str, env=None):
    import os
    from urllib.parse import quote, urljoin
    env = os.environ if env is None else env
    clean_doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(doi_str or "").strip(), flags=re.I).rstrip(".,;:)]>")
    if not clean_doi:
        return []
    found = []
    # 1. Known preprint DOIs (arXiv, bioRxiv, medRxiv)
    m_arx = re.match(r"^10\.48550/arXiv\.(.+)$", clean_doi, flags=re.I)
    if m_arx:
        found.append(f"https://arxiv.org/pdf/{m_arx.group(1).removesuffix('.pdf')}.pdf")
    if clean_doi.lower().startswith("10.1101/"):
        found.append(f"https://www.biorxiv.org/content/{clean_doi}v1.full.pdf")
        found.append(f"https://www.medrxiv.org/content/{clean_doi}v1.full.pdf")

    # 2. Unpaywall OA API
    email = (env.get("UNPAYWALL_EMAIL") or env.get("POLITE_EMAIL") or env.get("OPENALEX_EMAIL") or "").strip()
    if email:
        try:
            upw_url = f"https://api.unpaywall.org/v2/{quote(clean_doi, safe='/')}?email={quote(email)}"
            with urlopen(Request(upw_url, headers={"User-Agent": "sanmu-paper-search/1.7"}), timeout=10) as r:
                upw_data = json.loads(r.read(1024 * 1024).decode("utf-8", errors="replace"))
            best = upw_data.get("best_oa_location") or {}
            if best.get("url_for_pdf"):
                found.append(best["url_for_pdf"])
            for loc in upw_data.get("oa_locations") or []:
                if isinstance(loc, dict) and loc.get("url_for_pdf"):
                    found.append(loc["url_for_pdf"])
        except Exception:
            pass

    # 3. AI4Scholar / Semantic Scholar Graph openAccessPdf
    ai4s_key = (env.get("AI4SCHOLAR_API_KEY") or "").strip()
    s2_url = (
        f"https://ai4scholar.net/graph/v1/paper/DOI:{quote(clean_doi, safe='')}?fields=openAccessPdf,externalIds"
        if ai4s_key
        else f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(clean_doi, safe='')}?fields=openAccessPdf,externalIds"
    )
    s2_headers = {"User-Agent": "sanmu-paper-search/1.7", "Accept": "application/json"}
    if ai4s_key:
        s2_headers["Authorization"] = f"Bearer {ai4s_key}"
    elif env.get("S2_API_KEY"):
        s2_headers["x-api-key"] = env["S2_API_KEY"].strip()
    try:
        with urlopen(Request(s2_url, headers=s2_headers), timeout=12) as r:
            s2_data = json.loads(r.read(1024 * 1024).decode("utf-8", errors="replace"))
        ext_ids = s2_data.get("externalIds") or {}
        if ext_ids.get("ArXiv"):
            found.append(f"https://arxiv.org/pdf/{str(ext_ids['ArXiv']).removesuffix('.pdf')}.pdf")
        if ext_ids.get("PubMedCentral"):
            pmc = str(ext_ids["PubMedCentral"]).upper()
            if not pmc.startswith("PMC"):
                pmc = "PMC" + pmc
            found.append(f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmc}&blobtype=pdf")
        oa_pdf = s2_data.get("openAccessPdf") or {}
        if oa_pdf.get("url"):
            found.append(oa_pdf["url"])
        elif oa_pdf.get("disclaimer"):
            for m_u in re.findall(r"https?://[^\s,)]+", str(oa_pdf["disclaimer"])):
                u_clean = m_u.rstrip(".,;:)]>")
                if "unpaywall.org" not in u_clean and "doi.org" not in u_clean:
                    found.append(_normalize_candidate_pdf_url(u_clean))
    except Exception:
        pass

    # 4. DOI landing page resolution + publisher PDF patterns + meta citation_pdf_url
    try:
        doi_req = Request(
            f"https://doi.org/{quote(clean_doi, safe='/')}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            },
        )
        with urlopen(doi_req, timeout=15) as r:
            landing = r.url
            ct = r.headers.get_content_type()
            if "pdf" in ct.lower():
                found.insert(0, landing)
            else:
                found.extend(publisher_pdf_candidates(landing))
                html_snippet = r.read(256 * 1024).decode("utf-8", errors="replace")
                m_meta = re.search(
                    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
                    html_snippet,
                    flags=re.I,
                ) or re.search(
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
                    html_snippet,
                    flags=re.I,
                )
                if m_meta:
                    found.append(urljoin(landing, m_meta.group(1).strip()))
    except Exception:
        pass

    return [u for u in dict.fromkeys(found) if isinstance(u, str) and u.startswith("https://")]


def _download_pdf_bytes(url, max_bytes=80 * 1024 * 1024):
    url = _normalize_candidate_pdf_url(url)
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
            or any(c.isspace() or ord(c) < 32 for c in url)):
        raise ValueError("A public HTTPS URL without embedded credentials is required")
    parts.port  # Reject malformed port syntax before issuing a request.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 sanmu-paper-search/1.7",
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
            last_exc = exc
            can_try_direct = (urlopen is _DEFAULT_URLOPEN) or (build_opener is not _DEFAULT_BUILD_OPENER)
            should_try_direct = (
                can_try_direct
                and (
                    (isinstance(exc, HTTPError) and exc.code in (403, 429, 502, 503))
                    or not isinstance(exc, HTTPError)
                )
            )
            if should_try_direct:
                try:
                    direct_opener = build_opener(ProxyHandler({}))
                    direct_req = Request(url, headers=headers)
                    with direct_opener.open(direct_req, timeout=40) as response:
                        if urlsplit(response.url).scheme != "https":
                            raise ValueError("insecure_redirect: HTTPS is required")
                        data = response.read(max_bytes + 1)
                        final_url = response.url
                        content_type = response.headers.get_content_type()
                    if len(data) > max_bytes:
                        raise ValueError("response_too_large: PDF exceeds the download budget")
                    checks = inspect_pdf(data)
                    checks["direct_no_proxy_fallback"] = True
                    return data, url, final_url, content_type, checks
                except Exception:
                    pass
            if isinstance(exc, HTTPError):
                break
            if attempt < 2:
                import time
                time.sleep(1.2 * (attempt + 1))
                continue
            break
        except ValueError as exc:
            last_exc = exc
            break
    raise last_exc


def fetch(url, out, doi=None, title=None, max_bytes=80 * 1024 * 1024, fallback_urls=None, resolve_doi=False):
    candidates = []
    for u in ([url] if url else []) + list(fallback_urls or []):
        if u and u not in candidates:
            candidates.append(u)
    if not candidates and resolve_doi and doi:
        for resolved_u in resolve_doi_pdf_candidates(doi):
            if resolved_u not in candidates:
                candidates.append(resolved_u)
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

        def _try_candidate_list(url_list):
            nonlocal data, source_used, final_url, content_type, checks, identity, first_unverified, last_error
            for cand in url_list:
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
                    return True
                except (HTTPError, URLError, socket.timeout, TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
                    continue
            return False

        identity = {"status": "not_checked", "method": "none"}
        _try_candidate_list(candidates)
        if data is None and first_unverified is None and resolve_doi and doi:
            extra_cands = [u for u in resolve_doi_pdf_candidates(doi) if u not in candidates]
            if extra_cands:
                _try_candidate_list(extra_cands)
        if data is None and first_unverified is not None:
            candidate, identity = first_unverified
            data, source_used, final_url, content_type, checks = candidate
            pdf.write_bytes(data)
        if data is None:
            if last_error is not None:
                raise last_error
            raise ValueError("not_pdf: no candidate URL returned a valid PDF stream")
        direct_fallback_used = bool(isinstance(checks, dict) and checks.pop("direct_no_proxy_fallback", False))
        record = {"schema_version": "1.0", "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "file_obtained", "source_url": public_url(source_used), "final_url": public_url(final_url), "tried_urls": tried_urls, "content_type": content_type, "file": "paper.pdf", "sha256": hashlib.sha256(data).hexdigest(), "format_checks": checks, "identity": identity, "requested_identity": {"doi": doi, "title": title}, "license": "not_determined", "read": False}
        if direct_fallback_used:
            record["direct_no_proxy_fallback"] = True
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


def check_local_pdf(path, doi=None, title=None):
    p = Path(path)
    if not p.is_file():
        raise ValueError("local_pdf_not_found")
    data = p.read_bytes()
    checks = inspect_pdf(data)
    identity = identity_check(p, doi, title)
    return {
        "ok": True,
        "status": "file_obtained",
        "file": str(p),
        "sha256": hashlib.sha256(data).hexdigest(),
        "format_checks": checks,
        "identity": identity,
        "requested_identity": {"doi": doi, "title": title},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="")
    parser.add_argument("--out", help="New acquisition directory")
    parser.add_argument("--check-local", help="Inspect and verify identity of a local PDF file")
    parser.add_argument("--doi")
    parser.add_argument("--title")
    parser.add_argument("--fallback-url", action="append", default=[], help="Optional fallback OA PDF URLs to try if primary fails")
    parser.add_argument("--resolve-doi", action="store_true", help="Automatically resolve DOI to OA/publisher PDF URLs")
    args = parser.parse_args()
    target_doi = (args.doi or "").strip() or None
    if args.check_local:
        try:
            result = check_local_pdf(args.check_local, target_doi, args.title)
            code = 0
        except (ValueError, OSError) as exc:
            result, code = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "filesystem_error"}, 2
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    if not args.out:
        parser.error("the following arguments are required: --out")
    primary_url = (args.url or "").strip()
    auto_resolve = args.resolve_doi
    if primary_url and re.match(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+$", primary_url, flags=re.I):
        if not target_doi:
            target_doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", primary_url, flags=re.I)
        primary_url = ""
        auto_resolve = True
    elif not primary_url and target_doi:
        auto_resolve = True
    try:
        result = fetch(primary_url, args.out, target_doi, args.title, fallback_urls=args.fallback_url, resolve_doi=auto_resolve)
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
