#!/usr/bin/env python3
"""Opt-in live API smoke checks through the packaged stdio MCP server.

Runs are private and isolated; no keys are printed and no Zotero library is changed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time

from gui_host import RpcClient


def run(plugin_root, output, extended=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    client = RpcClient(plugin_root, output / "plugin-data", output / "workspace")
    summary = []
    responses = {}

    def check(label, tool, args, *, require_papers=False):
        started = time.monotonic()
        try:
            result = client.call(tool, args)
            data = result.get("structuredContent", {})
            active = data.get("subRun") or data.get("activeRun") or {}
            run_data = active.get("run") or {}
            papers = active.get("papers") or []
            failed = bool(result.get("isError") or data.get("error") or run_data.get("status") == "failed")
            if require_papers and not papers:
                failed = True
            row = {"case": label, "tool": tool, "ok": not failed,
                   "seconds": round(time.monotonic() - started, 2),
                   "papers": len(papers), "run_id": active.get("runId"),
                   "status": run_data.get("status"),
                   "result_count": run_data.get("result_count"),
                   "author_candidates": len(run_data.get("author_candidates") or []),
                   "source_statuses": run_data.get("source_status", run_data.get("source_statuses"))}
            if data.get("lastDownload"):
                download = data["lastDownload"]
                row["download_status"] = download.get("status")
                if download.get("status") in {"failed", "error"}:
                    failed = True
                    row["ok"] = False
            if failed:
                row["error"] = data.get("error") or result.get("content")
            responses[label] = result
        except Exception as error:
            row = {"case": label, "tool": tool, "ok": False,
                   "seconds": round(time.monotonic() - started, 2), "error": str(error)}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return responses.get(label, {}).get("structuredContent", {})

    try:
        tools = client.request("tools/list").get("tools", [])
        (output / "tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")
        check("doctor", "doctor_status", {})
        check("isolate-zotero", "save_config", {"zoteroUrl": "http://127.0.0.1:1",
                                                "exportSubdir": str(output / "exports")})
        for source in ("openalex", "semantic-scholar", "crossref"):
            check("search-" + source, "search_papers", {
                "query": "carbon nitride photocatalytic hydrogen", "limit": 2,
                "sources": [source], "yearFrom": 2021, "yearTo": 2026,
            }, require_papers=True)
        lookup = check("lookup-doi", "lookup_paper", {
            "id": "10.1038/s41586-021-03819-2", "sources": ["openalex", "crossref"],
            "title": "Highly accurate protein structure prediction with AlphaFold",
        }, require_papers=True)
        check("lookup-arxiv", "lookup_paper", {"id": "arxiv:1706.03762", "sources": ["semantic-scholar"]}, require_papers=True)
        for relation in ("references", "citations", "recommendations"):
            check("relations-" + relation, "explore_related", {
                "id": "10.1038/s41586-021-03819-2", "relation": relation, "limit": 2,
                "sources": ["openalex", "semantic-scholar"], "preserveActiveRun": True,
            })
        for provider in ("openalex", "semantic-scholar"):
            check("author-" + provider, "search_author", {
                "query": "John Jumper", "fromPaper": "10.1038/s41586-021-03819-2",
                "provider": provider, "limit": 2, "preserveActiveRun": True,
            })
        check("epmc-search", "search_epmc", {
            "query": "carbon nitride photocatalysis", "limit": 2, "yearFrom": 2020,
            "openAccess": True, "hasFulltext": True, "hasSuppl": True,
        }, require_papers=True)
        check("epmc-xml", "search_epmc", {
            "id": "PMC8371605", "limit": 1, "fetchMode": "xml", "saveDir": str(output / "epmc"),
        }, require_papers=True)
        check("exa-search", "search_exa", {
            "query": "graphitic carbon nitride single atom nickel photocatalytic hydrogen evolution",
            "limit": 2, "textChars": 400, "type": "auto", "yearFrom": 2021, "yearTo": 2026,
        })
        serper_cases = [
            ("scholar", {"query": "carbon nitride photocatalytic hydrogen", "yearFrom": 2021}),
            ("patents", {"query": "photocatalytic hydrogen carbon nitride"}),
            ("webpage", {"url": "https://www.example.com/"}),
        ]
        if extended:
            serper_cases += [
                ("search", {"query": "Nature photocatalysis"}),
                ("news", {"query": "photocatalysis hydrogen"}),
                ("images", {"query": "carbon nitride photocatalysis"}),
                ("videos", {"query": "photocatalysis hydrogen"}),
                ("places", {"query": "South China Agricultural University"}),
                ("maps", {"query": "South China Agricultural University"}),
                ("reviews", {"params": {"placeId": "ChIJIXnnR2n-AjQRKYcgOFlec6A"}}),
                ("shopping", {"query": "photocatalysis reactor"}),
                ("lens", {"url": "https://upload.wikimedia.org/wikipedia/commons/9/91/GraphiteUSGOV.jpg"}),
                ("autocomplete", {"query": "photocatalysis hydrogen"}),
            ]
        for kind, args in serper_cases:
            check("serper-" + kind, "search_serper", {"type": kind, "limit": 2, **args})
        check("oa-resolve", "resolve_oa_and_pdf", {
            "action": "resolve_oa", "inputIdOrRun": "10.1038/s41586-021-03819-2",
            "unpaywall": "auto",
        })
        check("pdf-download", "resolve_oa_and_pdf", {
            "action": "download_pdf", "pdfUrl": "https://arxiv.org/pdf/1706.03762",
            "title": "Attention Is All You Need", "year": 2017,
            "saveDir": str(output / "pdf"),
        })
        active = lookup.get("activeRun") or {}
        papers = active.get("papers") or []
        if papers:
            check("basket-add", "manage_basket", {"action": "add", "paper": papers[0]})
        runs = [r["run_id"] for r in summary if r.get("run_id")]
        if runs:
            check("run-load", "manage_runs", {"operation": "load", "runId": runs[0]})
        if len(set(runs)) >= 2:
            unique = list(dict.fromkeys(runs))
            check("run-diff", "manage_runs", {"operation": "diff", "beforeRunId": unique[0], "afterRunId": unique[1]})
            check("run-merge", "manage_runs", {"operation": "merge", "runIds": unique[:2]})
        (output / "responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = sorted(p for p in output.rglob("*") if p.is_file() and p.name not in {"SHA256SUMS", "config.json", "state.json"})
        (output / "SHA256SUMS").write_text("".join(
            hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(output).as_posix() + "\n" for p in files
        ), encoding="utf-8", newline="\n")
    finally:
        client.close()
    print(f"Live checks: {sum(bool(r['ok']) for r in summary)}/{len(summary)} without transport/application errors", flush=True)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extended", action="store_true")
    options = parser.parse_args()
    run(options.plugin_root, options.output, options.extended)
