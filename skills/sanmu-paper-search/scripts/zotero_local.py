#!/usr/bin/env python3
"""Independent, standard-library Zotero local API v3 client (Zotero 8+ writes).

Import ZoteroLocal and ZoteroError; no request is made by construction. Read methods
return raw Zotero objects or bounded {items, truncated, next_start, total} pages.
Mutations default to previews and require apply=True. File paths come exclusively
from /file/view/url. Zotero manages storage and WebDAV synchronization.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import (HTTPRedirectHandler, ProxyHandler, Request,
                            build_opener, url2pathname)

APP_NAME = "Sanmu Paper Search (Independent Local API)"
_KEY = re.compile(r"^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$")
MAX_RESPONSE = 16 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
_COMMON_MIME = {".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
                ".json": "application/json", ".zip": "application/zip"}


class ZoteroError(Exception):
    """Safe to serialize; response bodies and authorization keys are never exposed."""

    def __init__(self, code, message, *, status=None, details=None, partial=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.details, self.partial = details, partial

    def to_dict(self):
        result = {"code": self.code, "message": str(self)}
        for name in ("status", "details", "partial"):
            if getattr(self, name) is not None:
                result[name] = getattr(self, name)
        return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _key(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ZoteroError("invalid_input", "Expected an eight-character Zotero key.")
    return value


def _prefix(library):
    if library == "user":
        return "/api/users/0"
    if re.fullmatch(r"g[1-9][0-9]*", str(library)):
        return "/api/groups/" + library[1:]
    raise ZoteroError("invalid_input", "Library must be 'user' or g<groupID>.")


def normalize_identifier(value):
    """Return (namespace, normalized value); only DOI and arXiv are accepted."""
    text = str(value).strip()
    doi = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.I):
        return "doi", doi.lower()
    arxiv = re.sub(r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)", "", text, flags=re.I)
    arxiv = re.sub(r"\.pdf$", "", arxiv, flags=re.I)
    if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?", arxiv, re.I):
        return "arxiv", re.sub(r"v\d+$", "", arxiv, flags=re.I).lower()
    raise ZoteroError("invalid_identifier", "A valid DOI or arXiv identifier is required.")


def _identifiers(data):
    found = set()
    for candidate in (data.get("DOI"), data.get("url"), data.get("archiveID")):
        if candidate:
            try:
                found.add(normalize_identifier(candidate))
            except ZoteroError:
                pass
    for line in str(data.get("extra", "")).splitlines():
        if re.match(r"^\s*(?:DOI|arXiv)\s*:", line, re.I):
            try:
                found.add(normalize_identifier(line.strip()))
            except ZoteroError:
                pass
    return found


def _private(path, directory=False):
    """Restrict secrets to the current OS user; fail closed if ACL setup fails."""
    try:
        os.chmod(path, 0o700 if directory else 0o600)
        if os.name == "nt":
            identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                      capture_output=True, text=True, check=True)
            sid = next(csv.reader(identity.stdout.strip().splitlines()))[-1]
            if not re.fullmatch(r"S-1-[0-9-]+", sid):
                raise ValueError("Invalid SID")
            grant = f"*{sid}:" + ("(OI)(CI)F" if directory else "F")
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", grant],
                           capture_output=True, check=True)
    except (OSError, ValueError, subprocess.SubprocessError, StopIteration):
        raise ZoteroError("cache_permissions", "Could not restrict session-cache permissions.") from None


class ZoteroLocal:
    """Small synchronous client. Never shares the host plugin's identity or cache.

    get(key, library='user') -> raw object
    file(key, library='user') -> {status: available|missing|not_file, path?}
    upsert(metadata, identifier=None, ..., apply=False) -> status/key/item
    attach(parent_key, path, ..., apply=False, attachment_key=None) -> status/key/md5
    ZoteroError.partial retains completed writes on a subsequent-stage failure.
    """

    def __init__(self, base_url="http://127.0.0.1:23119", cache_path=None,
                 timeout=30, max_items=1000, max_pages=10, opener=None):
        parsed = urlsplit(base_url)
        try:
            host_ok = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            host_ok = False
        if (parsed.scheme != "http" or not host_ok or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ZoteroError("unsafe_url", "Only a loopback HTTP origin is allowed.")
        try:
            self.origin = (parsed.hostname, parsed.port or 80)
        except ValueError:
            raise ZoteroError("unsafe_url", "Invalid loopback port.") from None
        self.base_url = base_url.rstrip("/")
        if timeout <= 0 or not 1 <= max_items <= 10000 or not 1 <= max_pages <= 100:
            raise ZoteroError("invalid_input", "Invalid timeout or pagination budget.")
        self.timeout, self.max_items, self.max_pages = timeout, max_items, max_pages
        self.cache_path = Path(cache_path) if cache_path else Path.home() / ".cache" / "sanmu-paper-search" / "zotero-local-session.json"
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())
        self._session_key = None
        self._remember = False
        self.server_id = None
        self.version = None
        self._cache_loaded = False

    def _forget(self):
        self._session_key = None
        self._remember = False
        try:
            self.cache_path.unlink(missing_ok=True)
        except OSError:
            raise ZoteroError("cache_io", "Could not invalidate the independent session cache.") from None

    def _load_cache(self):
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if self.cache_path.is_symlink():
            raise ZoteroError("cache_permissions", "Session-cache symlinks are not allowed.")
        try:
            if not self.cache_path.exists():
                return
            _private(self.cache_path)
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._forget()
            return
        if (isinstance(value, dict) and value.get("base_url") == self.base_url and value.get("serverID") == self.server_id
                and value.get("appName") == APP_NAME and value.get("remember") is True
                and isinstance(value.get("key"), str) and re.fullmatch(r"[A-Za-z0-9]{16,64}", value["key"])):
            self._session_key, self._remember = value["key"], True
        else:
            self._forget()

    def _save_cache(self):
        parent = self.cache_path.parent
        if parent.is_symlink() or self.cache_path.is_symlink():
            raise ZoteroError("cache_permissions", "Session-cache symlinks are not allowed.")
        tmp = None
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _private(parent, directory=True)
            fd, tmp = tempfile.mkstemp(prefix=".session-", dir=parent)
            os.close(fd)
            _private(tmp)
            Path(tmp).write_text(json.dumps({"appName": APP_NAME, "base_url": self.base_url,
                                           "serverID": self.server_id, "key": self._session_key,
                                           "remember": True}), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            raise ZoteroError("cache_io", "Could not save the independent session cache.") from None
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def _request(self, method, path, body=None, headers=None, timeout=None):
        # Absolute URLs are accepted only for validated same-origin upload grants.
        if path.startswith("/") and not path.startswith("//"):
            url = self.base_url + path
        else:
            parsed = urlsplit(path)
            try:
                safe = (parsed.scheme == "http" and (parsed.hostname, parsed.port or 80) == self.origin
                        and not parsed.username and not parsed.password and not parsed.fragment)
            except ValueError:
                safe = False
            if not safe:
                raise ZoteroError("unsafe_url", "A non-local or different-origin URL was rejected.")
            url = path
        request_headers = {"Zotero-API-Version": "3", **(headers or {})}
        req = Request(url, data=body, headers=request_headers, method=method)
        try:
            try:
                response = self._opener.open(req, timeout=timeout or self.timeout)
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                raw = response.read(MAX_RESPONSE + 1)
        except (OSError, URLError, TimeoutError):
            raise ZoteroError("connection_failed", "Could not reach the loopback Zotero API (or it timed out).") from None
        if len(raw) > MAX_RESPONSE:
            raise ZoteroError("response_too_large", "Local API response exceeded the configured safety bound.")
        if 300 <= status < 400:
            raise ZoteroError("redirect_blocked", "HTTP redirects are disabled.", status=status)
        server = response_headers.get("zotero-server-id")
        if server and self.server_id and server != self.server_id:
            self._forget()
            self.server_id = server
            self._cache_loaded = True
            if method != "GET":
                raise ZoteroError("server_changed", "Zotero server changed; inspect the result before retrying.", status=status)
        if server:
            self.server_id = server
        self.version = response_headers.get("x-zotero-version", self.version)
        return status, response_headers, raw

    @staticmethod
    def _check(response, allowed=(200,)):
        status, headers, raw = response
        if status not in allowed:
            code = {401: "authorization_expired", 403: "write_denied", 404: "not_found",
                    405: "writes_unsupported", 412: "version_conflict", 428: "server_required",
                    429: "rate_limited"}.get(status, "http_error")
            if status == 403 and b"local api is not enabled" in raw.lower():
                code = "local_api_disabled"
            details = {"retry_after": headers["retry-after"]} if "retry-after" in headers else None
            raise ZoteroError(code, f"Local Zotero API returned HTTP {status}.", status=status, details=details)
        return response

    @staticmethod
    def _json(response):
        try:
            return json.loads(response[2])
        except (ValueError, UnicodeError):
            raise ZoteroError("invalid_response", "Zotero returned invalid JSON.") from None

    def _get(self, path):
        return self._json(self._check(self._request("GET", path)))

    def doctor(self):
        self._check(self._request("GET", "/api/"))
        if self.server_id:
            self._load_cache()
        return {"status": "ready", "base_url": self.base_url, "version": self.version,
                "server_id_available": bool(self.server_id), "authorized": bool(self._session_key),
                "app_name": APP_NAME, "write_support": "not_probed", "writes_require_apply": True}

    def authorize(self, *, apply=False):
        if not apply:
            return {"status": "planned", "app_name": APP_NAME}
        self.doctor()
        if not self.server_id:
            raise ZoteroError("writes_unsupported", "Zotero did not provide a server ID; local writing requires Zotero 8+.")
        response = self._request("POST", "/api/local/authorize",
                                 json.dumps({"appName": APP_NAME}).encode(),
                                 {"Content-Type": "application/json", "Zotero-Server-ID": self.server_id},
                                 timeout=max(self.timeout, 120))
        self._check(response)
        grant = self._json(response)
        token = grant.get("key") if isinstance(grant, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9]{16,64}", token):
            raise ZoteroError("invalid_response", "Zotero returned an invalid authorization grant.")
        self._session_key, self._remember = token, bool(grant.get("remember"))
        if self._remember:
            self._save_cache()
        return {"status": "authorized", "remembered": self._remember, "app_name": APP_NAME}

    def _write(self, method, path, body, *, headers=None, content_type="application/json"):
        self.doctor()
        if not self._session_key:
            self.authorize(apply=True)
        token = self._session_key
        if not self._remember:
            self._session_key = None
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        response = self._request(method, path, body,
                                 {"Content-Type": content_type, **(headers or {}),
                                  "Zotero-API-Key": token, "Zotero-Server-ID": self.server_id},
                                 timeout=max(self.timeout, 120))
        if response[0] in (401, 403):
            self._forget()
        if response[0] in (412, 428) and b"zotero-server-id" in response[2].lower():
            self._forget()
            raise ZoteroError("server_changed", "Server identity was rejected; refresh and retry explicitly.", status=response[0])
        return response

    def _list(self, path, *, params=None, limit=None, start=0):
        budget = self.max_items if limit is None else limit
        if not isinstance(budget, int) or not 1 <= budget <= self.max_items or start < 0:
            raise ZoteroError("invalid_input", "List limit/start exceeds the configured budget.")
        items, offset, total, complete = [], start, None, False
        for _ in range(self.max_pages):
            size = min(100, budget - len(items))
            query = {**(params or {}), "limit": size, "start": offset}
            response = self._check(self._request("GET", path + "?" + urlencode(query)))
            page = self._json(response)
            if not isinstance(page, list) or len(page) > size:
                raise ZoteroError("invalid_response", "Expected a bounded Zotero result array.")
            value = response[1].get("total-results")
            if value and value.isdigit():
                total = int(value)
            items.extend(page)
            offset += len(page)
            if (total is not None and offset >= total) or (len(page) < size and total is None):
                complete = True
                break
            if not page or len(items) >= budget:
                break
        return {"items": items, "truncated": not complete, "total": total,
                "next_start": None if complete else offset, "start": start, "count": len(items)}

    def libraries(self, *, limit=None):
        result = self._list("/api/users/0/groups", limit=limit)
        result["items"] = [{"id": "user", "name": "My Library", "type": "user"}] + [
            {"id": "g" + str(g["id"]), "name": g.get("data", {}).get("name", ""), "type": "group"}
            for g in result["items"]]
        result["count"] = len(result["items"])
        return result

    def collections(self, library="user", *, limit=None, start=0):
        return self._list(_prefix(library) + "/collections", limit=limit, start=start)

    def search(self, query="", library="user", *, collection=None, tag=None,
               limit=None, start=0, mode="titleCreatorYear"):
        if mode not in ("titleCreatorYear", "everything"):
            raise ZoteroError("invalid_input", "Unknown search mode.")
        path = _prefix(library)
        path += "/collections/" + _key(collection) if collection else ""
        path += "/items" if collection else "/items/top"
        params = {"q": query, "qmode": mode, "format": "json"}
        if tag is not None:
            params["tag"] = tag
        return self._list(path, params=params, limit=limit, start=start)

    def get(self, key, library="user"):
        return self._get(_prefix(library) + "/items/" + _key(key))

    def children(self, key, library="user", *, limit=None, start=0):
        return self._list(_prefix(library) + "/items/" + _key(key) + "/children", limit=limit, start=start)

    def file(self, key, library="user"):
        key = _key(key)
        response = self._request("GET", _prefix(library) + "/items/" + key + "/file/view/url")
        if response[0] in (400, 404):
            return {"status": "not_file" if response[0] == 400 else "missing", "key": key}
        self._check(response)
        try:
            url = response[2].decode("utf-8").strip()
        except UnicodeError:
            raise ZoteroError("invalid_response", "Invalid attachment file URL encoding.") from None
        if not url:
            return {"status": "missing", "key": key}
        parsed = urlsplit(url)
        if (parsed.scheme != "file" or parsed.netloc not in ("", "localhost")
                or parsed.query or parsed.fragment or not parsed.path.startswith("/")
                or unquote(parsed.path).startswith("//") or "\\" in unquote(parsed.path)
                or "\x00" in unquote(parsed.path)):
            raise ZoteroError("unsafe_file_url", "Zotero did not return an exact local file URL.")
        path = Path(url2pathname(parsed.path))
        if not path.is_file():
            return {"status": "missing", "key": key, "path": str(path)}
        return {"status": "available", "key": key, "path": str(path), "size": path.stat().st_size}

    def export(self, key=None, library="user", *, format="bibtex", limit=None):
        if format not in ("bibtex", "biblatex", "ris", "csljson"):
            raise ZoteroError("invalid_input", "Unsupported export format.")
        page = {"items": [self.get(key, library)], "truncated": False, "next_start": None} if key else self.search(library=library, limit=limit)
        # Export individually to preserve boundedness regardless of format pagination support.
        output = []
        for item in page["items"]:
            response = self._check(self._request("GET", _prefix(library) + "/items/" + _key(item["key"]) + "?" + urlencode({"format": format})))
            output.append(response[2].decode("utf-8"))
        return {"format": format, "entries": output, "count": len(output),
                "truncated": page["truncated"], "next_start": page["next_start"]}

    def _create(self, data, library="user", kind="items"):
        response = self._check(self._write("POST", _prefix(library) + "/" + kind, [data],
                                          headers={"Zotero-Write-Token": secrets.token_hex(16)}))
        result = self._json(response)
        if result.get("failed"):
            raise ZoteroError("write_failed", "Zotero rejected the new object.",
                              details={"positions": list(result["failed"])})
        obj = result.get("successful", {}).get("0", {})
        key = obj.get("key") if isinstance(obj, dict) else None
        key = key or result.get("success", {}).get("0") or result.get("unchanged", {}).get("0")
        if not key:
            raise ZoteroError("invalid_response", "Zotero did not report the created object's key.",
                              partial={"stage": "create_response", "created_may_exist": True})
        return _key(key)

    def update(self, key, fields=None, library="user", *, add_tags=(), remove_tags=(),
               add_collections=(), remove_collections=(), apply=False):
        fields = dict(fields or {})
        reserved = {"key", "version", "dateAdded", "dateModified", "collections", "tags", "md5", "mtime", "filename", "linkMode"}
        if reserved.intersection(fields):
            raise ZoteroError("invalid_input", "Use set operations for tags/collections; identity and file fields are protected.")
        for collection in (*add_collections, *remove_collections):
            _key(collection)
        if not all(isinstance(t, str) and t for t in (*add_tags, *remove_tags)):
            raise ZoteroError("invalid_input", "Tags must be non-empty strings.")
        current = self.get(key, library)
        baseline = current["data"]
        for attempt in range(3):
            data = current["data"]
            conflicts = [f for f, value in fields.items()
                         if data.get(f) != baseline.get(f) and data.get(f) != value]
            if conflicts:
                raise ZoteroError("field_conflict", "Concurrent changes affect requested fields.",
                                  status=412, details={"fields": conflicts, "key": key})
            patch = {f: v for f, v in fields.items() if data.get(f) != v}
            if add_tags or remove_tags:
                tags = [t for t in data.get("tags", []) if t.get("tag") not in remove_tags]
                existing = {t.get("tag") for t in tags}
                tags += [{"tag": t} for t in dict.fromkeys(add_tags) if t not in existing and t not in remove_tags]
                if tags != data.get("tags", []):
                    patch["tags"] = tags
            if add_collections or remove_collections:
                collections = [c for c in data.get("collections", []) if c not in remove_collections]
                collections += [c for c in dict.fromkeys(add_collections) if c not in collections and c not in remove_collections]
                if collections != data.get("collections", []):
                    patch["collections"] = collections
            if not patch:
                return {"status": "unchanged", "key": key, "item": current}
            if not apply:
                return {"status": "planned", "key": key, "version": current["version"], "patch": patch}
            response = self._write("PATCH", _prefix(library) + "/items/" + _key(key), patch,
                                   headers={"If-Unmodified-Since-Version": str(current["version"])})
            if response[0] == 412:
                if attempt == 2:
                    raise ZoteroError("version_conflict", "Item kept changing; retry explicitly.", status=412)
                current = self.get(key, library)
                continue
            self._check(response, (200, 204))
            try:
                return {"status": "updated", "key": key, "item": self.get(key, library)}
            except ZoteroError as error:
                error.partial = {"key": key, "stage": "readback", "updated": True}
                raise
        raise AssertionError("Unreachable")

    def upsert(self, metadata, identifier=None, library="user", *, apply=False, update_existing=False):
        if not isinstance(metadata, dict):
            raise ZoteroError("invalid_input", "Metadata must be a JSON object.")
        metadata = dict(metadata)
        stable = _identifiers(metadata)
        if identifier:
            stable.add(normalize_identifier(identifier))
        if not stable:
            raise ZoteroError("invalid_identifier", "Upsert requires a DOI or arXiv stable identifier.")
        for namespace in ("doi", "arxiv"):
            if len({value for kind, value in stable if kind == namespace}) > 1:
                raise ZoteroError("identifier_conflict", "Metadata has conflicting stable identifiers.")
        # Full bounded scan, not a fuzzy title match or the first q result. Refuse to
        # create/update if a later page might contain another exact identifier.
        page = self.search(library=library)
        matches = [item for item in page["items"] if stable & _identifiers(item.get("data", {}))]
        if page["truncated"]:
            raise ZoteroError("incomplete_dedup", "Stable-ID scan was truncated; increase the bounded budget before upsert.",
                              details={"scanned": page["count"], "next_start": page["next_start"]})
        if len(matches) > 1:
            raise ZoteroError("ambiguous_identifier", "Multiple items share the supplied stable identifier.",
                              details={"keys": [i["key"] for i in matches]})
        # Identifier argument must also be written so rerunning an upsert is idempotent.
        if identifier:
            kind, value = normalize_identifier(identifier)
            if kind == "doi":
                metadata.setdefault("DOI", value)
            elif (kind, value) not in _identifiers(metadata):
                metadata["extra"] = (str(metadata.get("extra", "")).rstrip() + "\narXiv: " + value).strip()
        if matches:
            existing = _identifiers(matches[0]["data"])
            for kind in ("doi", "arxiv"):
                old = {v for k, v in existing if k == kind}
                new = {v for k, v in stable if k == kind}
                if old and new and old != new:
                    raise ZoteroError("identifier_conflict", "Matched item has another stable identifier.")
            if not update_existing:
                return {"status": "unchanged", "key": matches[0]["key"], "item": matches[0], "matched": True}
            tags = metadata.pop("tags", [])
            collections = metadata.pop("collections", [])
            return self.update(matches[0]["key"], metadata, library,
                               add_tags=[t["tag"] if isinstance(t, dict) else t for t in tags],
                               add_collections=collections, apply=apply)
        if not metadata.get("itemType") or not metadata.get("title"):
            raise ZoteroError("invalid_input", "Creating an item requires itemType and title.")
        if not apply:
            return {"status": "planned", "action": "create", "data": metadata}
        key = self._create(metadata, library)
        try:
            return {"status": "created", "key": key, "item": self.get(key, library)}
        except ZoteroError as error:
            error.partial = {"key": key, "stage": "readback", "created": True}
            raise

    @staticmethod
    def _file_digest(path):
        """Hash only a caller file or the exact local file identified by the API."""
        try:
            target = Path(path)
            before = target.stat()
            if not target.is_file() or before.st_size > MAX_FILE:
                raise ZoteroError("invalid_file", "Attachment exceeds the 256 MiB verification bound.")
            digest, size = hashlib.md5(), 0
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_FILE:
                        raise ZoteroError("file_changed", "Attachment grew beyond the verification bound.")
                    digest.update(chunk)
            after = target.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != before.st_size:
                raise ZoteroError("file_changed", "Attachment changed during verification.")
            return digest.hexdigest(), size
        except OSError:
            raise ZoteroError("file_missing", "The exact attachment file reported by Zotero could not be verified.") from None

    def attach(self, parent_key, path, title=None, content_type=None, library="user", *,
               apply=False, attachment_key=None):
        """Import a local stored file. Resume failed uploads with attachment_key.

        Identical MD5 attachments are skipped if the local file is present; a
        missing stored attachment is reused and repaired. No physical renaming.
        """
        _key(parent_key)
        parent = self.get(parent_key, library)
        if parent.get("data", {}).get("itemType") in ("attachment", "note", "annotation"):
            raise ZoteroError("invalid_parent", "An attachment requires a regular parent item.")
        path = Path(path)
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size > MAX_FILE:
                raise ZoteroError("invalid_file", "Expected a regular file no larger than 256 MiB.")
            payload = path.read_bytes()
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(payload) != stat.st_size:
                raise ZoteroError("file_changed", "Source file changed while being read.")
        except OSError:
            raise ZoteroError("file_missing", "Source attachment could not be read.") from None
        digest = hashlib.md5(payload).hexdigest()
        page = self.children(parent_key, library)
        if page["truncated"]:
            raise ZoteroError("incomplete_dedup", "Attachment scan was truncated; no import performed.")
        candidates = [i for i in page["items"] if i.get("data", {}).get("itemType") == "attachment"]
        repair = None
        for child in candidates:
            if child["data"].get("md5", "").lower() == digest:
                info = self.file(child["key"], library)
                if info["status"] == "available":
                    actual, _ = self._file_digest(info["path"])
                    if actual != digest:
                        raise ZoteroError("attachment_conflict", "Existing attachment bytes disagree with Zotero's stored MD5.",
                                          details={"key": child["key"]})
                    return {"status": "skipped", "key": child["key"], "md5": digest, "reason": "identical_attachment"}
                if child["data"].get("linkMode") in ("imported_file", "imported_url"):
                    repair = child["key"]
        if not repair and not attachment_key:
            empty = [i for i in candidates if i["data"].get("linkMode") == "imported_file"
                     and not i["data"].get("md5") and i["data"].get("filename") == path.name
                     and i["data"].get("title") == (title or path.name)
                     and self.file(i["key"], library)["status"] == "missing"]
            if len(empty) > 1:
                raise ZoteroError("ambiguous_attachment", "Multiple incomplete attachments match; supply attachment_key.",
                                  details={"keys": [i["key"] for i in empty]})
            if empty:
                repair = empty[0]["key"]
        if attachment_key:
            candidate = next((i for i in candidates if i["key"] == _key(attachment_key)), None)
            if not candidate or candidate["data"].get("linkMode") not in ("imported_file", "imported_url"):
                raise ZoteroError("invalid_input", "Resume key must be a stored attachment of this parent.")
            recorded = candidate["data"].get("md5")
            if recorded and recorded.lower() != digest:
                raise ZoteroError("attachment_conflict", "Resume attachment has a different file hash.")
            repair = attachment_key
        mime = content_type or _COMMON_MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if "\r" in mime or "\n" in mime:
            raise ZoteroError("invalid_input", "Invalid content type.")
        data = {"itemType": "attachment", "parentItem": parent_key, "linkMode": "imported_file",
                "title": title or path.name, "filename": path.name, "contentType": mime, "tags": [], "relations": {}}
        if not apply:
            return {"status": "planned", "action": "repair" if repair else "attach", "key": repair,
                    "data": data, "md5": digest, "size": len(payload)}
        key, stage = repair, "create"
        try:
            key = key or self._create(data, library)
            endpoint = _prefix(library) + "/items/" + key + "/file"
            stage = "authorize_upload"
            form = urlencode({"md5": digest, "filename": path.name, "filesize": len(payload),
                              "mtime": stat.st_mtime_ns // 1000000, "contentType": mime}).encode()
            grant = self._json(self._check(self._write("POST", endpoint, form,
                                      content_type="application/x-www-form-urlencoded", headers={"If-None-Match": "*"})))
            if not grant.get("exists"):
                upload_url, upload_key = grant.get("url"), grant.get("uploadKey")
                if not isinstance(upload_url, str) or not isinstance(upload_key, str) or not upload_key:
                    raise ZoteroError("invalid_response", "Zotero did not provide a complete upload grant.")
                parsed = urlsplit(upload_url)
                if not re.fullmatch(r"/api/local/uploads/[A-Za-z0-9]+", parsed.path) or parsed.query:
                    raise ZoteroError("unsafe_url", "Unexpected local upload URL.")
                stage = "upload"
                self._check(self._request("POST", upload_url, payload, {"Content-Type": mime},
                                          timeout=max(self.timeout, 120)), (200, 201, 204))
                stage = "register"
                self._check(self._write("POST", endpoint, urlencode({"upload": upload_key}).encode(),
                                       content_type="application/x-www-form-urlencoded", headers={"If-None-Match": "*"}), (200, 204))
            stage = "verify"
            stored = self.get(key, library)
            info = self.file(key, library)
            if info["status"] != "available":
                raise ZoteroError("file_missing", "Upload finished but Zotero has no available local attachment file.")
            actual, actual_size = self._file_digest(info["path"])
            if (actual != digest or actual_size != len(payload)
                    or stored.get("data", {}).get("md5", "").lower() != digest):
                raise ZoteroError("attachment_verification_failed", "Stored attachment metadata or bytes differ from the uploaded file.")
            return {"status": "created" if not repair else "repaired", "key": key, "md5": digest,
                    "size": len(payload), "verified": True}
        except ZoteroError as error:
            error.partial = {"attachment_key": key, "parent_key": parent_key, "stage": stage,
                             "md5": digest, "resume_with_attachment_key": key}
            raise

    def note(self, parent_key, text, library="user", *, apply=False):
        # Accept note HTML explicitly, rather than pretending to parse Markdown.
        self.get(parent_key, library)
        data = {"itemType": "note", "parentItem": _key(parent_key), "note": text, "tags": []}
        if not apply:
            return {"status": "planned", "data": data}
        return {"status": "created", "key": self._create(data, library)}

    def create_collection(self, name, parent=None, library="user", *, apply=False):
        if not isinstance(name, str) or not name.strip():
            raise ZoteroError("invalid_input", "Collection name is required.")
        parent = _key(parent) if parent else False
        page = self.collections(library)
        matches = [i for i in page["items"] if i["data"].get("name") == name
                   and (i["data"].get("parentCollection") or False) == parent]
        if page["truncated"]:
            raise ZoteroError("incomplete_dedup", "Collection scan was truncated.")
        if len(matches) > 1:
            raise ZoteroError("ambiguous_collection", "Multiple collections match name and parent.")
        if matches:
            return {"status": "unchanged", "key": matches[0]["key"]}
        data = {"name": name, "parentCollection": parent}
        if not apply:
            return {"status": "planned", "data": data}
        return {"status": "created", "key": self._create(data, library, "collections")}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ZoteroError("invalid_input", message)


def main(argv=None):
    parser = _Parser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default="http://127.0.0.1:23119")
    common.add_argument("--cache-path")
    common.add_argument("--library", default="user")
    common.add_argument("--limit", type=int, default=100)
    common.add_argument("--max-items", type=int, default=1000)
    common.add_argument("--max-pages", type=int, default=10)
    common.add_argument("--start", type=int, default=0)
    common.add_argument("--apply", action="store_true", help="Perform mutations (otherwise preview).")
    subs = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("doctor", "libraries", "collections", "search", "get", "children", "file", "export", "update", "upsert", "attach", "note", "create-collection"):
        sub = subs.add_parser(name, parents=[common])
        if name in ("get", "children", "file", "update"):
            sub.add_argument("key")
        if name == "search":
            sub.add_argument("query", nargs="?", default="")
            sub.add_argument("--collection")
            sub.add_argument("--tag")
            sub.add_argument("--mode", choices=("titleCreatorYear", "everything"), default="titleCreatorYear")
        if name == "export":
            sub.add_argument("key", nargs="?")
            sub.add_argument("--format", default="bibtex", choices=("bibtex", "biblatex", "ris", "csljson"))
        if name in ("update", "upsert"):
            sub.add_argument("--data", required=True, help="JSON object, or @path to UTF-8 JSON.")
        if name == "update":
            for operation in ("add-tag", "remove-tag", "add-collection", "remove-collection"):
                sub.add_argument("--" + operation, action="append", default=[])
        if name == "upsert":
            sub.add_argument("--identifier")
            sub.add_argument("--update-existing", action="store_true", help="Explicitly update matching metadata.")
        if name == "attach":
            sub.add_argument("parent_key")
            sub.add_argument("path")
            sub.add_argument("--title")
            sub.add_argument("--content-type")
            sub.add_argument("--attachment-key", help="Resume an incomplete stored attachment.")
        if name == "note":
            sub.add_argument("parent_key")
            sub.add_argument("text", help="Zotero note HTML (plain text also accepted by Zotero).")
        if name == "create-collection":
            sub.add_argument("name")
            sub.add_argument("--parent")
    try:
        args = parser.parse_args(argv)
        client = ZoteroLocal(args.base_url, args.cache_path, max_items=args.max_items, max_pages=args.max_pages)
        command = args.command
        if command == "doctor":
            result = client.authorize(apply=True) if args.apply else client.doctor()
        elif command == "libraries":
            result = client.libraries(limit=args.limit)
        elif command == "collections":
            result = client.collections(args.library, limit=args.limit, start=args.start)
        elif command == "search":
            result = client.search(args.query, args.library, collection=args.collection, tag=args.tag,
                                   limit=args.limit, start=args.start, mode=args.mode)
        elif command == "children":
            result = client.children(args.key, args.library, limit=args.limit, start=args.start)
        elif command in ("get", "file"):
            result = getattr(client, command)(args.key, args.library)
        elif command == "export":
            result = client.export(args.key, args.library, format=args.format, limit=args.limit)
        elif command in ("update", "upsert"):
            source = Path(args.data[1:]).read_text(encoding="utf-8") if args.data.startswith("@") else args.data
            data = json.loads(source)
            if not isinstance(data, dict):
                raise ZoteroError("invalid_input", "--data must contain a JSON object.")
            if command == "upsert":
                result = client.upsert(data, args.identifier, args.library, apply=args.apply, update_existing=args.update_existing)
            else:
                result = client.update(args.key, data, args.library, add_tags=args.add_tag,
                                       remove_tags=args.remove_tag, add_collections=args.add_collection,
                                       remove_collections=args.remove_collection, apply=args.apply)
        elif command == "attach":
            result = client.attach(args.parent_key, args.path, args.title, args.content_type,
                                   args.library, apply=args.apply, attachment_key=args.attachment_key)
        elif command == "note":
            result = client.note(args.parent_key, args.text, args.library, apply=args.apply)
        else:
            result = client.create_collection(args.name, args.parent, args.library, apply=args.apply)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except ZoteroError as error:
        print(json.dumps({"ok": False, "error": error.to_dict()}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"ok": False, "error": {"code": "invalid_input_or_response", "message": "Invalid local input or malformed API response."}}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
