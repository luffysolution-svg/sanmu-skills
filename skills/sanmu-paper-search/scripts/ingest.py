"""Validate an acquisition manifest and import its files through local Zotero."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from _core import doi
from zotero_local import ZoteroLocal, ZoteroError, normalize_identifier


def metadata_for(paper):
    identifiers = paper.get("identifiers") or {}
    identifier = doi(paper.get("doi")) or next(iter(identifiers.get("doi", [])), None)
    data = {"itemType": "journalArticle", "title": paper.get("title") or ""}
    if identifier:
        data["DOI"] = identifier
        data["url"] = "https://doi.org/" + identifier
    arxiv = next(iter(identifiers.get("arxiv", [])), None)
    if arxiv and not identifier:
        data.update(itemType="preprint", archive="arXiv", archiveID=arxiv,
                    url="https://arxiv.org/abs/" + arxiv)
    if paper.get("year"):
        data["date"] = str(paper["year"])
    creators = []
    for author in paper.get("authors") or []:
        name = author.get("name", "").strip()
        if name:
            creators.append({"creatorType": "author", "name": name})
    if creators:
        data["creators"] = creators
    if paper.get("abstract"):
        data["abstractNote"] = paper["abstract"]
    return data


def load_manifest(path, identity_confirmed=False, si_confirmed=False):
    path = Path(path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "sanmu-acquisition/v1" and manifest.get("schema_version") != "sanmu-acquisition/v1":
        raise ValueError("Unsupported acquisition manifest schema")
    paper = manifest.get("paper") or {}
    if not paper.get("title"):
        raise ValueError("Paper title is required before importing")
    entries, pending = [], []
    for entry in [manifest.get("main") or {}] + list(manifest.get("supplementary") or []):
        if entry.get("status") != "file_obtained":
            if entry and (entry is manifest.get("main") or entry.get("selected")
                          or entry.get("status") == "failed"):
                pending.append({"id": entry.get("id"), "status": entry.get("status")})
            continue
        relative = Path(entry.get("file") or "")
        target = (path.parent / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(path.parent) or not target.is_file():
            raise ValueError("Attachment must be an existing file inside the acquisition directory")
        expected = entry.get("sha256")
        if not expected or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Attachment SHA-256 mismatch: " + str(relative))
        is_si = entry.get("kind") in ("supplementary", "si") or entry is not manifest.get("main")
        identity = entry.get("identity") or {}
        if not is_si and identity.get("status") != "matched" and not identity_confirmed:
            raise ValueError("Main-file identity needs review; use --identity-confirmed after checking the file")
        if is_si and not si_confirmed:
            raise ValueError("SI relationship needs review; use --si-confirmed after checking the publisher attachment list")
        entries.append((entry, target, is_si))
    if not entries:
        raise ValueError("No obtained files in acquisition manifest")
    return manifest, entries, pending


def verify_parent(item, paper):
    data = item.get("data", item)
    if data.get("itemType") in ("attachment", "note", "annotation"):
        raise ValueError("Parent must be a bibliographic item")
    expected_arxiv = set()
    for value in (paper.get("identifiers") or {}).get("arxiv", []):
        expected_arxiv.add(normalize_identifier("arxiv:" + value)[1])
    observed_arxiv = set()
    for value in [data.get("url"), data.get("archiveID"), *str(data.get("extra") or "").splitlines()]:
        if not value:
            continue
        try:
            kind, identifier = normalize_identifier(value)
            if kind == "arxiv":
                observed_arxiv.add(identifier)
        except ZoteroError:
            pass
    if expected_arxiv and observed_arxiv and expected_arxiv.isdisjoint(observed_arxiv):
        raise ValueError("Parent arXiv identifier conflicts with acquisition manifest")
    expected = doi(paper.get("doi")) or next(iter((paper.get("identifiers") or {}).get("doi", [])), None)
    observed = doi(data.get("DOI"))
    if expected and observed:
        if doi(expected) != observed:
            raise ValueError("Parent DOI conflicts with acquisition manifest")
    else:
        norm = lambda text: re.sub(r"[^\w]", "", (text or "").casefold())
        if not norm(paper.get("title")) or norm(data.get("title")) != norm(paper.get("title")):
            raise ValueError("Parent title does not match acquisition manifest")


def ingest(path, client=None, parent_key=None, library="user", apply=False,
           identity_confirmed=False, si_confirmed=False):
    manifest, files, pending = load_manifest(path, identity_confirmed, si_confirmed)
    client = client or ZoteroLocal()
    paper = manifest["paper"]
    if parent_key:
        item = client.get(parent_key, library=library)
        verify_parent(item, paper)
        parent = {"status": "matched", "key": parent_key}
    else:
        parent = client.upsert(metadata_for(paper), library=library, apply=apply)
        parent_key = parent.get("key")
    result = {"schema": "sanmu-ingest/v1", "status": "planned" if not apply else "success",
              "parent": parent, "attachments": [], "pending": pending,
              "sync": "managed_by_zotero; WebDAV_completion_not_checked"}
    for entry, file_path, is_si in files:
        source = entry.get("source") or {}
        title = ("Supporting information" if is_si else "Full text")
        if is_si:
            title += " — " + (entry.get("title") or entry.get("name") or file_path.name)
        elif source.get("version"):
            title += " — " + source["version"]
        if not parent_key:
            result["attachments"].append({"id": entry.get("id"), "status": "planned",
                                           "file": entry["file"], "title": title})
            continue
        try:
            attached = client.attach(parent_key, file_path, title=title,
                                     content_type=entry.get("content_type"), library=library, apply=apply)
            result["attachments"].append({"id": entry.get("id"), **attached})
        except ZoteroError as exc:
            result["status"] = "partial"
            result["attachments"].append({"id": entry.get("id"), "status": "failed", "error": exc.to_dict()})
            break
    if pending and apply:
        result["status"] = "partial"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--parent-key")
    parser.add_argument("--library", default="user")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-items", type=int, default=1000, help="Zotero deduplication scan budget")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--base-url", default="http://127.0.0.1:23119")
    parser.add_argument("--cache-path")
    parser.add_argument("--identity-confirmed", action="store_true")
    parser.add_argument("--si-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        client = ZoteroLocal(args.base_url, args.cache_path, max_items=args.max_items, max_pages=args.max_pages)
        result = ingest(args.manifest, client=client, parent_key=args.parent_key, library=args.library, apply=args.apply,
                        identity_confirmed=args.identity_confirmed, si_confirmed=args.si_confirmed)
        code = 1 if result["status"] == "partial" else 0
    except ZoteroError as exc:
        result, code = {"status": "failed", "error": exc.to_dict()}, 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result, code = {"status": "failed", "error": str(exc)}, 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
