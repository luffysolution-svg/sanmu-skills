"""Offline MCP child-process fixture; never issues requests or reads user data."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
mode = args[0]

def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default

def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")

if mode == "doctor":
    print(json.dumps({"status": "success", "python_supported": True, "emails": {
        key: os.environ.get(key) for key in ("POLITE_EMAIL", "OPENALEX_EMAIL", "CROSSREF_EMAIL", "UNPAYWALL_EMAIL")}}))
    raise SystemExit(0)
if mode == "check":
    root = Path(args[1])
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    issues = [entry["path"] for entry in manifest["files"] if hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]]
    print(json.dumps({"status": "failed" if issues else "success", "issues": issues}))
    raise SystemExit(4 if issues else 0)
out = Path(option("--out"))
if "slow" in args:
    time.sleep(0.1)
if mode.startswith("https:"):
    out.mkdir(parents=True)
    (out / "paper.pdf").write_bytes(b"%PDF-1.7\nfixture pdf\n%%EOF")
    write(out / "acquisition.json", {"status": "file_obtained", "identity": {"status": "not_checked"}})
    print(json.dumps({"status": "file_obtained"}))
    raise SystemExit(0)
status = "failed" if "fail" in args or (mode == "author" and option("--provider") == "semantic-scholar") else "partial" if "partial" in args else "success"
out.mkdir(parents=True)
paper = {"title": "Fixture paper", "year": 2025, "identifiers": {"doi": ["10.1234/fixture"]}, "authors": [{"name": "Alice Lee"}], "abstract": "Fixture abstract"}
if "Wrong target" in args:
    paper = {**paper, "title": "Unrelated title", "identifiers": {"doi": ["10.1234/unrelated"]}}
run = {"command": mode, "status": status, "query": {"text": args[1] if len(args) > 1 else "", "args": args}, "source_status": {"fixture": {"status": "error" if status != "success" else "ok", "error": "fixture source failed" if status != "success" else None}}}
if status == "failed":
    run["source_status"]["fixture"]["error"] += " " + os.environ.get("EXA_API_KEY", "")
write(out / "run.json", run)
papers = [] if status == "failed" else [paper]
if mode == "rank" and option("--run"):
    papers = json.loads((Path(option("--run")) / "papers.json").read_text(encoding="utf-8"))
    for item in papers:
        item["venue_rank"] = {"if": 20, "badges": ["IF 20"]}
    write(out / "results.json", [])
write(out / "papers.json", papers)
(out / "report.md").write_text("fixture report", encoding="utf-8")
if mode == "epmc" and option("--fetch") != "none":
    (out / "fulltext.xml").write_text("<article>fixture</article>", encoding="utf-8")
    write(out / "fulltext_summary.json", {"status": "success"})
    if option("--fetch") in {"all", "suppl"}:
        supplementary = out / "supplementary"
        supplementary.mkdir()
        (supplementary / "figure.webp").write_bytes(b"RIFF-fixture")
        (supplementary / "support.pdf").write_bytes(b"%PDF-1.7\nfixture\n%%EOF")
if mode == "sciverse":
    sv_mode = option("--mode", "meta-search")
    run["sciverse_mode"] = sv_mode
    if sv_mode == "content":
        (out / "fulltext_excerpt.md").write_text("# Fixture Excerpt\n![Figure 1](dt=2026/fig1.jpg)\n", encoding="utf-8")
        figs_dir = out / "figures"
        figs_dir.mkdir(exist_ok=True)
        (figs_dir / "01_fig1.jpg").write_bytes(b"\xff\xd8\xff\xe0fixture-jpeg")
        run["sciverse_content"] = {
            "doc_id": option("--doc-id", "a" * 64),
            "title": "Fixture paper",
            "offset": int(option("--offset", "0")),
            "limit": int(option("--chars", "6000")),
            "chars_returned": 42,
            "next_offset": 1000,
            "more": True,
            "text": "# Fixture Excerpt\n![Figure 1](dt=2026/fig1.jpg)\n",
            "downloaded_figures": [{"index": 1, "alt": "Figure 1", "file_name": "dt=2026/fig1.jpg", "local_path": "figures/01_fig1.jpg", "bytes": 16}]
        }
    elif sv_mode == "schema-materials":
        (out / "schema_materials.md").write_text("# Fixture Materials Pack\n", encoding="utf-8")
        run["sciverse_materials"] = {
            "goal": option("--goal", "reproduction"),
            "schema_ids": ["2008.12187v1-f78968c8e105"],
            "markdown_file": "schema_materials.md",
            "items": [{
                "schema_id": "2008.12187v1-f78968c8e105",
                "found": True,
                "schema_paper": {"title": "Fixture Schema Paper", "year": 2025, "research_problem": "Fixture problem", "central_contribution": "Fixture contribution", "headline_result": "Fixture result"},
                "entities": [{"entity_type": "Component", "name": "Gather Node", "text": "Graph pooling"}],
                "relations": [{"relation_type": "part_of", "source_local_id": "a", "target_local_id": "b"}],
                "evidence": [{"hot_groups": ["core_claim_result"], "key": "MAE", "value_text": "0.0079"}]
            }]
        }
    write(out / "run.json", run)
write(out / "manifest.json", {"algorithm": "sha256", "files": [{"path": path.relative_to(out).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size} for path in out.rglob("*") if path.is_file() and path.name != "manifest.json"]})
print(json.dumps(run))
raise SystemExit({"success": 0, "partial": 1, "failed": 3}[status])
