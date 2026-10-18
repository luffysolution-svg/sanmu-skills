"""Acquire subscription PDFs through an official API or a campus WebVPN session.

`fetch --backend auto` tries the Elsevier/Wiley TDM API first and enters the
browser lane only for DOIs the API does not cover. `login` opens the campus
library WebVPN entry and waits for the user to finish SSO. The browser lane
reuses that persistent Chrome profile, performs the library's publisher
handshake for Elsevier/Wiley, then downloads with serial, per-host pacing.
Other publishers use the same browser profile and generic PDF-link extraction,
without a dedicated adapter. CAPTCHA and institutional-login walls are reported
for manual completion; with --keep-open the script waits, then retries the
current item after the user clears it. The script never solves or bypasses a
challenge and never enters credentials on the user's behalf.

Optional dependencies: playwright and Google Chrome. The core search commands do
not need them; the API lane needs requests.
"""
from __future__ import annotations

import argparse
import base64
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
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import acquisition_store
import fetch_pdf
import rate_ledger
from ingest import ingest as ingest_manifest
from zotero_local import ZoteroError, ZoteroLocal

SCHEMA = "sanmu-acquisition/v1"
DEFAULT_PORTAL = "https://lib-scau-edu-cn-s.vpn.scau.edu.cn/"
PUBLISHERS = (
    ("elsevier", "ELSEVIER", ("sciencedirect.com", "cell.com", "elsevier.com"), "SD_REMOTEACCESS"),
    ("wiley", "WILEY", ("wiley.com",), None),
)
DOI_PUBLISHERS = {
    "10.1016": "elsevier",
    "10.1002": "wiley",
    "10.1111": "wiley",
}
CHALLENGE_MARKERS = (
    "are you a robot", "cf-chl", "正在进行安全验证",
    "verify you are human", "reference number:",
)
INSTITUTIONAL_MARKERS = (
    "access through your institution", "institutional login",
    "sign in via your institution", "sign in through your institution",
    "shibboleth", "carsi", "openathens", "机构登录", "通过机构访问",
)
SI_TITLE_MARKERS = ("supplementary", "supplemental", "supporting information",
                    "supporting material", "appendix", " si ", "si —")
PDF_MARKERS = ("pdf", "pdfft", "pdfdirect", "download=", "articlepdf",
               "stamp", "getpdf", "pdfdown")
SI_URL_MARKERS = ("supplementary", "supporting", "suppl", "/mmc", "moesm",
                  "supinfo", "si-", "figshare", "/suppl/", "supplement")
MAX_BYTES = 80 * 1024 * 1024

_JS_FETCH_PDF = """async (u) => {
  const r = await fetch(u, {credentials: "include", redirect: "follow"});
  const ctype = r.headers.get("content-type") || "";
  if (!r.ok) return {status: r.status, ctype, error: "http"};
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf.length > 80 * 1024 * 1024) return {status: r.status, ctype, error: "too_large"};
  let s = "";
  for (let i = 0; i < buf.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  }
  return {status: r.status, ctype, b64: btoa(s)};
}"""


def now():
    return datetime.now(timezone.utc).isoformat()


def default_profile():
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(root) / "sanmu-paper-search" / "webvpn-profile"
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "sanmu-paper-search" / "webvpn-profile"


def publisher_of(url):
    """Return the library-portal publisher key for a public publisher URL."""
    host = (urlsplit(url).hostname or "").lower()
    for key, _label, hosts, _cookie in PUBLISHERS:
        if any(host == item or host.endswith("." + item) for item in hosts):
            return key
    return "direct"


def publisher_for_doi(doi):
    """Fallback publisher mapping when the Zotero URL is a bare DOI resolver link."""
    prefix = (doi or "").strip().lower().split("/", 1)[0]
    return DOI_PUBLISHERS.get(prefix, "direct")


class _PdfLinks(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        href = None
        if tag == "meta" and (values.get("name") or values.get("property") or "").lower() == "citation_pdf_url":
            href = values.get("content")
        elif tag in {"a", "iframe", "embed"}:
            href = values.get("href") or values.get("src")
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        low = absolute.lower()
        if any(marker in low for marker in SI_URL_MARKERS):
            return
        if low.startswith(("http://", "https://")) and any(marker in low for marker in PDF_MARKERS):
            self.links.append(absolute)


def pdf_candidates(html, base_url):
    parser = _PdfLinks(base_url)
    parser.feed(html)
    unique = []
    for url in parser.links:
        low = url.lower()
        if any(marker in low for marker in SI_URL_MARKERS):
            continue
        if url not in unique:
            unique.append(url)

    def rank(url):
        low = url.lower()
        if "pdfdirect" in low or "getpdf.jsp" in low:
            return 0
        if "articlepdf" in low or "pdfdown" in low:
            return 1
        if "pdfft" in low:
            return 2
        if ".pdf" in low:
            return 2
        if "stamp/stamp.jsp" in low:
            return 4
        if "epdf" in low:
            return 5
        return 3

    return sorted(unique, key=rank)[:10]


def _institutional_login_page(html):
    low = (html or "").lower()
    return any(marker in low for marker in INSTITUTIONAL_MARKERS)


class HostLimiter:
    """Serial, per-host pacing with a hard stop after repeated 403/429 responses."""

    def __init__(self, interval=15.0, jitter=5.0, max_per_host=20, stop_after=2,
                 clock=time.monotonic, sleep=time.sleep):
        self.interval = max(0.0, interval)
        self.jitter = max(0.0, jitter)
        self.max_per_host = max(1, max_per_host)
        self.stop_after = max(1, stop_after)
        self.clock = clock
        self.sleep = sleep
        self.last = {}
        self.counts = {}
        self.blocks = {}

    def allowed(self, host):
        return (self.counts.get(host, 0) < self.max_per_host
                and self.blocks.get(host, 0) < self.stop_after)

    def wait(self, host):
        previous = self.last.get(host)
        if previous is not None:
            delay = self.interval + random.uniform(0, self.jitter) - (self.clock() - previous)
            if delay > 0:
                self.sleep(delay)

    def note(self, host, status=None):
        self.counts[host] = self.counts.get(host, 0) + 1
        self.last[host] = self.clock()
        if status in (403, 429):
            self.blocks[host] = self.blocks.get(host, 0) + 1
        else:
            self.blocks[host] = 0

    def blocked(self, host):
        return self.blocks.get(host, 0) >= self.stop_after


def _item_data(item):
    return item.get("data", item)


def has_main_pdf(client, key, library="user"):
    children = client.children(key, library=library).get("items") or []
    for child in children:
        data = _item_data(child)
        if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf":
            continue
        title = (data.get("title") or "").lower()
        if any(marker in title for marker in SI_TITLE_MARKERS):
            continue
        try:
            if client.file(data["key"], library=library).get("status") == "available":
                return True
        except ZoteroError:
            continue
    return False


def resolve_target(item):
    data = _item_data(item)
    url = (data.get("url") or "").strip()
    doi = (data.get("DOI") or "").strip()
    if url.startswith(("http://", "https://")):
        if "doi.org/" in url:
            return "https://doi.org/" + url.split("doi.org/", 1)[1]
        return url
    if doi:
        return "https://doi.org/" + doi
    return None


def _launch(profile, headless):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required: python -m pip install playwright") from exc
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=headless,
            ignore_default_args=["--enable-automation"],
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"],
            viewport={"width": 1360, "height": 900},
        )
    except Exception:
        pw.stop()
        raise
    state = Path(profile) / "storage-state.json"
    if state.is_file():
        try:
            cookies = json.loads(state.read_text(encoding="utf-8")).get("cookies") or []
            if cookies:
                context.add_cookies(cookies)
        except (OSError, ValueError):
            pass
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(90000)
    return pw, context, page


def _logged_in(url):
    low = (url or "").lower()
    return bool(low.startswith("https://")) and "cas-scau" not in low and "portal/shortcut" not in low


def _handshake(context, portal, publisher, timeout=30000):
    """Open the library database link once so the publisher sets its remote-access cookie."""
    if publisher == "direct":
        return {"status": "not_required", "publisher": publisher}
    spec = next((item for item in PUBLISHERS if item[0] == publisher), None)
    if spec is None:
        return {"status": "unsupported", "publisher": publisher}
    label, cookie = spec[1], spec[3]
    page = context.new_page()
    try:
        page.goto(portal, wait_until="domcontentloaded", timeout=60000)
        if not _logged_in(page.url):
            return {"status": "login_required", "publisher": publisher, "url": page.url}
        link = page.locator(f'a:has-text("{label}")').first
        if link.count() == 0:
            return {"status": "link_not_found", "publisher": publisher}
        with context.expect_page(timeout=timeout) as info:
            link.click()
        pub = info.value
        try:
            pub.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            pass
        if cookie:
            host = spec[2][0]
            for _ in range(15):
                pub.wait_for_timeout(1000)
                if any(item["name"] == cookie for item in context.cookies("https://" + host)):
                    return {"status": "ok", "publisher": publisher, "cookie": cookie,
                            "url": pub.url}
            return {"status": "cookie_missing", "publisher": publisher, "url": pub.url}
        return {"status": "attempted", "publisher": publisher, "url": pub.url}
    except Exception as exc:
        return {"status": "failed", "publisher": publisher, "detail": str(exc)[:200]}
    finally:
        page.close()


def login(profile, entry, timeout):
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    pw, context, page = _launch(profile, headless=False)
    try:
        page.goto(entry, wait_until="domcontentloaded", timeout=60000)
        page.bring_to_front()
        print(json.dumps({"status": "waiting_for_login", "url": page.url,
                          "action": "Complete campus SSO in the opened Chrome window."},
                         ensure_ascii=False), flush=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            try:
                url = page.url
            except Exception:
                return {"status": "browser_closed"}
            if _logged_in(url):
                state = profile / "storage-state.json"
                context.storage_state(path=str(state))
                return {"status": "logged_in", "url": url, "profile": str(profile),
                        "storage_state": str(state), "cookies": len(context.cookies())}
        return {"status": "login_timeout", "url": page.url}
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _fetch_via_page(page, url):
    if urlsplit(url).netloc != urlsplit(page.url).netloc:
        return None
    try:
        result = page.evaluate(_JS_FETCH_PDF, url)
    except Exception as exc:
        return {"error": "page_fetch_failed", "detail": str(exc)[:200]}
    if not result.get("b64"):
        return {"error": result.get("error") or "empty_response", "status": result.get("status"),
                "ctype": result.get("ctype")}
    try:
        return {"status": result.get("status"), "ctype": result.get("ctype"),
                "data": base64.b64decode(result["b64"])}
    except Exception as exc:
        return {"error": "base64_decode_failed", "detail": str(exc)[:200]}


def _fetch_via_request(context, page, url):
    try:
        response = context.request.get(url, timeout=90000,
                                       headers={"Referer": page.url, "Accept": "application/pdf"})
        data = response.body()
        return {"status": response.status, "ctype": response.headers.get("content-type"),
                "data": data, "retry_after": response.headers.get("retry-after")}
    except Exception as exc:
        return {"error": "request_failed", "detail": str(exc)[:200]}


def _fetch_via_download(context, url, referer=None):
    tab = context.new_page()
    tab.set_default_timeout(90000)
    try:
        with tab.expect_download(timeout=20000) as pending:
            tab.goto(url, referer=referer, wait_until="commit", timeout=45000)
        download = pending.value
        temp = Path(os.environ.get("TEMP", ".")) / ("sanmu-download-" + uuid_hex() + ".pdf")
        download.save_as(str(temp))
        data = temp.read_bytes()
        temp.unlink(missing_ok=True)
        return {"status": 200, "data": data}
    except Exception as exc:
        try:
            text = tab.inner_text("body")[:400].lower()
        except Exception:
            text = ""
        if any(marker in text for marker in CHALLENGE_MARKERS):
            return {"error": "publisher_challenge", "status": 403, "url": tab.url}
        return {"error": "download_event_failed", "detail": str(exc)[:200], "url": tab.url}
    finally:
        tab.close()


def uuid_hex():
    return hashlib.sha256(os.urandom(16)).hexdigest()[:12]


def _accept_pdf(result):
    if not result or result.get("error"):
        return None, result or {"error": "no_result"}
    data = result.get("data") or b""
    if len(data) > MAX_BYTES:
        return None, {"error": "response_too_large", "bytes": len(data)}
    try:
        checks = fetch_pdf.inspect_pdf(data)
    except ValueError as exc:
        return None, {"error": str(exc), "status": result.get("status"), "ctype": result.get("ctype")}
    return (data, checks), result


def _challenge_in_result(result):
    data = (result or {}).get("data") or b""
    if not data:
        return False
    text = data[:200000].decode("utf-8", "ignore").lower()
    return any(marker in text for marker in CHALLENGE_MARKERS)


def _manifest(item, pdf_name, sha256, checks, identity, target):
    data = _item_data(item)
    doi = (data.get("DOI") or "").strip()
    authors = []
    for creator in data.get("creators") or []:
        name = creator.get("name") or " ".join(
            part for part in (creator.get("firstName"), creator.get("lastName")) if part)
        if name:
            authors.append({"name": name})
    year = (data.get("date") or "")[:4]
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
            "source": {"provider": "campus_webvpn_browser",
                       "url": fetch_pdf.public_url(target),
                       "version": "publishedVersion",
                       "evidence": "user_authorized_webvpn_session"},
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


def fetch_item(context, page, client, item, key, out, portal, handshakes, limiter, args):
    data = _item_data(item)
    target = resolve_target(item)
    record = {"key": key, "title": data.get("title"), "doi": data.get("DOI"),
              "status": "planned", "backend": "browser"}
    if not target:
        record.update(status="skipped", reason="no_publisher_url_or_doi")
        return record
    publisher = publisher_of(target)
    if publisher == "direct":
        publisher = publisher_for_doi(data.get("DOI"))
    record.update(target_url=fetch_pdf.public_url(target), publisher=publisher)
    if publisher not in handshakes:
        handshakes[publisher] = _handshake(context, portal, publisher)
    handshake = handshakes[publisher]
    record["handshake"] = handshake.get("status")
    if handshake.get("status") == "login_required":
        record.update(status="stopped", reason="webvpn_login_required")
        return record
    host = urlsplit(target).hostname or "unknown"
    if not limiter.allowed(host):
        record.update(status="stopped", reason="host_budget_or_block_limit")
        return record
    limiter.wait(host)
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=90000)
        page_html = page.content()
    except Exception as exc:
        record.update(status="failed", reason="navigation_failed", detail=str(exc)[:200])
        limiter.note(host)
        return record
    low = page_html.lower()
    if any(marker in low for marker in CHALLENGE_MARKERS):
        record.update(status="needs_manual", reason="publisher_challenge", final_url=page.url,
                      manual_url=page.url,
                      manual_action="Complete the challenge once in the kept browser, then rerun.")
        limiter.note(host, 403)
        return record
    candidates = []
    for _ in range(10):
        try:
            page_html = page.content()
            candidates = pdf_candidates(page_html, page.url)
        except Exception:
            break
        if candidates:
            break
        try:
            page.wait_for_timeout(1500)
        except Exception:
            break
    if not candidates and page.url.lower().split("?", 1)[0].endswith(".pdf"):
        candidates = [page.url]
    if not candidates:
        if _institutional_login_page(page_html):
            record.update(status="needs_manual", reason="institutional_login_required",
                          page_title=page.title(), final_url=page.url, manual_url=page.url,
                          manual_action=("Click 'Access through your institution', choose the "
                                         "institution, and complete CARSI/Shibboleth or WebVPN "
                                         "login in the kept browser."))
        else:
            record.update(status="needs_manual", reason="no_main_pdf_link",
                          page_title=page.title(), final_url=page.url)
        limiter.note(host)
        return record
    record["candidates"] = len(candidates)
    last_error = None
    noted = False
    for candidate in candidates[:3]:
        result = _fetch_via_page(page, candidate)
        if result and result.get("error") in ("page_fetch_failed", "empty_response"):
            result = _fetch_via_request(context, page, candidate)
        if not result or result.get("error"):
            if result and result.get("error") == "publisher_challenge":
                record.update(status="needs_manual", reason="publisher_challenge",
                              pdf_url=candidate, final_url=result.get("url") or page.url,
                              manual_url=result.get("url") or candidate,
                              manual_action="Complete the challenge once in the kept browser, then rerun.")
                limiter.note(host, 403)
                noted = True
                break
            if result and result.get("status") in (403, 429):
                limiter.note(host, result["status"])
                noted = True
                last_error = result
                if result["status"] == 429:
                    record["rate_limited"] = True
                    record["retry_after"] = result.get("retry_after")
                if limiter.blocked(host):
                    break
                continue
            result = _fetch_via_download(context, candidate, referer=page.url)
        accepted, detail = _accept_pdf(result)
        if not accepted and _challenge_in_result(result):
            record.update(status="needs_manual", reason="publisher_challenge",
                          pdf_url=candidate, final_url=result.get("url") or page.url,
                          manual_url=result.get("url") or candidate,
                          manual_action="Complete the challenge once in the kept browser, then rerun.")
            limiter.note(host, 403)
            noted = True
            break
        if accepted:
            data_bytes, checks = accepted
            dest_dir = out / key
            doi = record.get("doi") or ""
            try:
                existing = acquisition_store.inspect_existing(dest_dir, doi)
            except acquisition_store.AcquisitionConflict as exc:
                record.update(status="failed", reason="existing_acquisition_conflict",
                              detail=str(exc)[:200])
                limiter.note(host)
                return record
            if existing is not None:
                identity = existing.get("identity") or {"status": "not_checked"}
                record.update(status="skipped", reason="already_obtained",
                              file=existing["file"], manifest=existing["manifest"],
                              identity=identity, candidate=fetch_pdf.public_url(candidate),
                              bytes=existing["bytes"], sha256=existing["sha256"])
                if args.apply:
                    if identity.get("status") == "matched" or args.identity_confirmed:
                        _apply_ingest(record, existing["manifest"], key, client, args)
                    else:
                        record["ingest"] = {"status": "pending_identity_review"}
                limiter.note(host)
                return record
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=out))
            try:
                staged_pdf = stage / "paper.pdf"
                staged_pdf.write_bytes(data_bytes)
                identity = fetch_pdf.identity_check(staged_pdf, record.get("doi"),
                                                   record.get("title"))
                manifest = _manifest(item, "paper.pdf",
                                     hashlib.sha256(data_bytes).hexdigest(),
                                     checks, identity, target)
                try:
                    persisted = acquisition_store.persist(dest_dir, data_bytes, manifest)
                except acquisition_store.AcquisitionConflict as exc:
                    record.update(status="failed", reason="existing_acquisition_conflict",
                                  detail=str(exc)[:200])
                    limiter.note(host)
                    return record
            finally:
                shutil.rmtree(stage, ignore_errors=True)
            identity = persisted.get("identity") or identity
            record.update(status="file_obtained", file=persisted["file"],
                          manifest=persisted["manifest"], identity=identity,
                          candidate=fetch_pdf.public_url(candidate),
                          bytes=persisted["bytes"], sha256=persisted["sha256"])
            if args.apply:
                if identity.get("status") == "matched" or args.identity_confirmed:
                    _apply_ingest(record, persisted["manifest"], key, client, args)
                else:
                    record["ingest"] = {"status": "pending_identity_review"}
            limiter.note(host)
            return record
        last_error = detail
        if detail and detail.get("status") in (403, 429):
            limiter.note(host, detail["status"])
            noted = True
            if detail["status"] == 429:
                record["rate_limited"] = True
                record["retry_after"] = detail.get("retry_after")
            if limiter.blocked(host):
                break
    if not noted:
        limiter.note(host)
    if record.get("status") != "needs_manual":
        record.update(status="failed", reason="pdf_not_obtained", detail=last_error)
    return record


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


def wait_for_manual_clearance(page, timeout, poll=3.0):
    """Wait in the already-open browser until a challenge or login wall clears."""
    deadline = time.time() + max(1, int(timeout))
    while time.time() < deadline:
        try:
            if page.is_closed():
                return {"status": "browser_closed"}
            page.bring_to_front()
            page.wait_for_timeout(int(poll * 1000))
            html = page.content()
            low = html.lower()
            if any(marker in low for marker in CHALLENGE_MARKERS):
                continue
            if _institutional_login_page(html):
                # A publisher page may keep an institutional-login link in its
                # footer even after access is restored; a visible PDF link is
                # stronger evidence than the absence of that label.
                if not pdf_candidates(html, page.url) and not page.url.lower().endswith(".pdf"):
                    continue
            return {"status": "cleared", "url": page.url}
        except Exception:
            try:
                if page.is_closed():
                    return {"status": "browser_closed"}
            except Exception:
                return {"status": "browser_closed"}
            continue
    return {"status": "timeout"}


def _load_publisher_api():
    try:
        import publisher_api
    except ImportError as exc:
        raise RuntimeError("requests is required for the API lane") from exc
    return publisher_api


def _browser_policy(args):
    policy = dict(rate_ledger.DEFAULT_BROWSER_POLICY)
    policy["min_interval"] = max(float(args.delay), 0.0)
    policy["window_limit"] = max(1, int(args.max_per_host))
    if args.daily_per_host:
        policy["daily_limit"] = int(args.daily_per_host)
    return policy


def _clear_batch_cooldowns(items, ledger):
    """Clear challenge/login cooldowns after the user confirms verification."""
    for entry in items:
        if not entry.get("data"):
            continue
        target = resolve_target(entry["data"])
        host = urlsplit(target).hostname if target else None
        if host:
            ledger.clear_challenge("webvpn:" + host)


def _dry_run(args, items, ledger):
    planned = []
    try:
        api = _load_publisher_api()
    except RuntimeError:
        api = None
    for entry in items:
        if entry.get("error"):
            planned.append({"key": entry["key"], "status": "failed", "error": entry["error"]})
            continue
        if entry.get("skip"):
            planned.append({"key": entry["key"], "status": "skipped", "reason": entry["skip"]})
            continue
        target = resolve_target(entry["data"])
        doi = (_item_data(entry["data"]).get("DOI") or "").strip()
        provider = api.provider_for_doi(doi) if api else "unsupported"
        host = urlsplit(target).hostname if target else None
        record = {
            "key": entry["key"], "doi": doi,
            "target_url": fetch_pdf.public_url(target) if target else None,
            "publisher": publisher_of(target) if target else None,
            "api_provider": provider,
            "api_configured": bool(api and provider != "unsupported"
                                   and api.configured(provider)),
            "status": "planned" if target else "failed",
        }
        if api and provider != "unsupported":
            record["api_ledger"] = {
                key: value for key, value in
                api._ledger_for(args).check("api:" + provider,
                                            api.policy_for(provider, args)).items()
                if key in ("allowed", "reason", "wait_seconds")
            }
        if host:
            record["browser_ledger"] = {
                key: value for key, value in
                ledger.check("webvpn:" + host, _browser_policy(args)).items()
                if key in ("allowed", "reason", "wait_seconds")
            }
        planned.append(record)
    result = {"schema": "sanmu-webvpn-batch/v1", "status": "dry_run",
              "portal": args.portal, "backend": args.backend,
              "state": str(ledger.path) if ledger.path else None,
              "delay_seconds": args.delay, "jitter_seconds": args.jitter,
              "max_per_host": args.max_per_host, "items": planned}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


API_OK = ("file_obtained", "ingested", "ingested_partial")
API_FALLBACK_SKIPS = ("api_not_supported", "api_key_not_configured", "no_doi")


def run_fetch(args):
    keys = load_keys(args.keys, args.keys_file)
    if not keys:
        raise ValueError("Provide --keys or --keys-file")
    client = ZoteroLocal(args.base_url, max_items=args.max_items, max_pages=args.max_pages)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger = rate_ledger.RateLedger(
        None if args.no_state else (args.state or rate_ledger.default_state_path()))
    limiter = HostLimiter(args.delay, args.jitter, args.max_per_host, args.stop_after)
    items = []
    for key in keys:
        try:
            item = client.get(key, library=args.library)
        except ZoteroError as exc:
            items.append({"key": key, "error": exc.to_dict()})
            continue
        if not args.force and has_main_pdf(client, key, args.library):
            items.append({"key": key, "data": _item_data(item), "skip": "main_pdf_exists"})
        else:
            items.append({"key": key, "data": _item_data(item)})
    if args.manual_cleared:
        _clear_batch_cooldowns(items, ledger)
    if args.dry_run:
        return _dry_run(args, items, ledger)

    try:
        api = _load_publisher_api() if args.backend in ("auto", "api") else None
    except RuntimeError:
        if args.backend == "api":
            raise
        api = None

    records = []
    log_path = out / "fetch-log.jsonl"
    scopes = {}
    handshakes = {}
    obtained = 0

    def append(record):
        nonlocal obtained
        records.append(record)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": now(), **record}, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if record.get("status") in API_OK:
            obtained += 1

    browser_items = []
    for entry in items:
        if entry.get("error"):
            append({"key": entry["key"], "status": "failed", "error": entry["error"]})
            continue
        if entry.get("skip"):
            append({"key": entry["key"], "status": "skipped", "reason": entry["skip"]})
            continue
        if obtained >= args.max_total:
            append({"key": entry["key"], "status": "stopped", "reason": "max_total"})
            continue
        if api is not None:
            doi = (_item_data(entry["data"]).get("DOI") or "").strip()
            provider = api.provider_for_doi(doi)
            if provider == "unsupported":
                if args.backend == "api":
                    append({"key": entry["key"], "status": "skipped",
                            "reason": "api_not_supported"})
                else:
                    browser_items.append(entry)
                continue
            scope = "api:" + provider
            scopes[scope] = api.policy_for(provider, args)
            record = api._fetch_one(client, entry["data"], entry["key"], out, args,
                                    ledger=ledger)
            append(record)
            if record.get("status") in API_OK:
                continue
            if record.get("status") == "skipped" and record.get("reason") in API_FALLBACK_SKIPS:
                if args.backend == "auto":
                    browser_items.append(entry)
                continue
            # access_denied, rate_limited, network_error and http_error must not
            # silently become browser requests against the same content.
            continue
        browser_items.append(entry)

    processed = 0
    if args.backend in ("auto", "browser") and browser_items:
        try:
            pw, context, page = _launch(Path(args.profile), args.headless)
        except RuntimeError as exc:
            for entry in browser_items:
                append({"key": entry["key"], "status": "failed",
                        "reason": "browser_unavailable", "detail": str(exc)[:200]})
        else:
            try:
                while processed < len(browser_items):
                    entry = browser_items[processed]
                    processed += 1
                    if obtained >= args.max_total:
                        append({"key": entry["key"], "status": "stopped",
                                "reason": "max_total"})
                        continue
                    target = resolve_target(entry["data"])
                    host = urlsplit(target).hostname if target else "unknown"
                    scope = "webvpn:" + host
                    scopes[scope] = _browser_policy(args)
                    check = ledger.reserve(scope, scopes[scope])
                    if not check.get("allowed"):
                        append({"key": entry["key"], "status": "stopped",
                                "reason": check.get("reason"),
                                "wait_seconds": round(float(check.get("wait_seconds") or 0), 1),
                                "cooldown_reason": check.get("cooldown_reason")})
                        continue
                    try:
                        record = fetch_item(context, page, client, entry["data"],
                                            entry["key"], out, args.portal,
                                            handshakes, limiter, args)
                    except Exception as exc:
                        record = {"key": entry["key"], "status": "failed",
                                  "reason": "unexpected_error",
                                  "detail": type(exc).__name__ + ": " + str(exc)[:200]}
                    if acquisition_store.is_success(record):
                        ledger.note(scope, 200)
                        ledger.clear(scope)
                        append(record)
                        continue
                    if record.get("status") == "needs_manual":
                        manual = None
                        if args.keep_open and not args.headless:
                            print(json.dumps({
                                "status": "waiting_for_manual", "key": entry["key"],
                                "seconds": args.challenge_timeout,
                                "reason": record.get("reason"),
                                "url": record.get("manual_url"),
                                "action": record.get("manual_action"),
                            }, ensure_ascii=False), flush=True)
                            manual_url = record.get("manual_url")
                            if manual_url and manual_url != page.url:
                                try:
                                    page.goto(manual_url, wait_until="domcontentloaded",
                                              timeout=90000)
                                except Exception:
                                    pass
                            manual = wait_for_manual_clearance(page, args.challenge_timeout)
                            record["manual_wait"] = manual
                        if manual and manual.get("status") == "cleared":
                            ledger.clear(scope)
                            try:
                                retry = fetch_item(context, page, client, entry["data"],
                                                   entry["key"], out, args.portal,
                                                   handshakes, limiter, args)
                            except Exception as exc:
                                retry = {"key": entry["key"], "status": "failed",
                                         "reason": "unexpected_error",
                                         "detail": type(exc).__name__ + ": " + str(exc)[:200]}
                            retry["manual_retry"] = True
                            if acquisition_store.is_success(retry):
                                ledger.note(scope, 200)
                                ledger.clear(scope)
                            elif retry.get("rate_limited"):
                                ledger.note(scope, "rate_limited",
                                            retry_after=retry.get("retry_after"))
                            else:
                                ledger.note(scope, "publisher_challenge",
                                            cooldown_seconds=args.challenge_cooldown)
                            append(retry)
                            if retry.get("status") == "needs_manual":
                                break
                            continue
                        ledger.note(scope, "publisher_challenge",
                                    cooldown_seconds=args.challenge_cooldown)
                        append(record)
                        break
                    elif record.get("status") == "failed":
                        if record.get("rate_limited"):
                            ledger.note(scope, "rate_limited",
                                        retry_after=record.get("retry_after"))
                        else:
                            ledger.note(scope, record.get("reason") or "failed")
                    append(record)
                    if record.get("status") == "stopped":
                        break
            finally:
                try:
                    context.close()
                finally:
                    pw.stop()

    result = {"schema": "sanmu-webvpn-batch/v1", "status": "partial",
              "portal": args.portal, "backend": args.backend,
              "state": str(ledger.path) if ledger.path else None,
              "handshakes": handshakes,
              "not_processed": max(0, len(browser_items) - processed),
              "items": records}
    result["ledger"] = ledger.snapshot(sorted(scopes), scopes)
    result["status"] = ("success" if records and all(
        acquisition_store.is_success(r) for r in records) else "partial")
    acquisition_store.write_json(out / "results.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    login_parser = sub.add_parser("login", help="Open the library WebVPN entry and wait for SSO")
    login_parser.add_argument("--profile", default=str(default_profile()))
    login_parser.add_argument("--entry", default=DEFAULT_PORTAL)
    login_parser.add_argument("--timeout", type=int, default=900)
    fetch_parser = sub.add_parser("fetch", help="Download PDFs for existing Zotero items")
    fetch_parser.add_argument("--profile", default=str(default_profile()))
    fetch_parser.add_argument("--portal", default=DEFAULT_PORTAL)
    fetch_parser.add_argument("--keys", help="Comma-separated Zotero item keys")
    fetch_parser.add_argument("--keys-file", help="One Zotero item key per line")
    fetch_parser.add_argument("--out", required=True, help="New or existing batch directory")
    fetch_parser.add_argument("--library", default="user")
    fetch_parser.add_argument("--base-url", default="http://127.0.0.1:23119")
    fetch_parser.add_argument("--backend", choices=("auto", "api", "browser"), default="auto",
                              help="auto: official API first, browser only when the API is unavailable")
    fetch_parser.add_argument("--delay", type=float, default=15.0,
                              help="Browser lane minimum seconds between requests per host")
    fetch_parser.add_argument("--jitter", type=float, default=5.0)
    fetch_parser.add_argument("--api-delay", type=float, default=None,
                              help="Override the API lane minimum interval (defaults: Elsevier 2s, Wiley 10s)")
    fetch_parser.add_argument("--max-per-host", type=int, default=20)
    fetch_parser.add_argument("--stop-after", type=int, default=2)
    fetch_parser.add_argument("--max-total", type=int, default=50)
    fetch_parser.add_argument("--daily-per-host", type=int, default=60,
                              help="Local daily browser-request budget per host")
    fetch_parser.add_argument("--daily-limit", type=int, default=0,
                              help="Override the local daily API budget per provider")
    fetch_parser.add_argument("--state", help="Persistent request-budget JSON path")
    fetch_parser.add_argument("--no-state", action="store_true",
                              help="Disable cross-run request accounting (not recommended)")
    fetch_parser.add_argument("--max-items", type=int, default=1000)
    fetch_parser.add_argument("--max-pages", type=int, default=10)
    fetch_parser.add_argument("--headless", action="store_true")
    fetch_parser.add_argument("--dry-run", action="store_true")
    fetch_parser.add_argument("--apply", action="store_true")
    fetch_parser.add_argument("--force", action="store_true")
    fetch_parser.add_argument("--identity-confirmed", action="store_true")
    fetch_parser.add_argument("--keep-open", action="store_true",
                              help="Keep the browser open, wait for manual clearance, then retry the item")
    fetch_parser.add_argument("--manual-cleared", action="store_true",
                              help="Assert that a pending CAPTCHA/institutional login was completed; clear this batch's host cooldowns")
    fetch_parser.add_argument("--challenge-timeout", type=int, default=300)
    fetch_parser.add_argument("--challenge-cooldown", type=int, default=86400,
                              help="Cooldown after an unresolved challenge or login wall")
    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            result = login(args.profile, args.entry, args.timeout)
            code = 0 if result.get("status") == "logged_in" else 1
        else:
            result, code = None, run_fetch(args)
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
