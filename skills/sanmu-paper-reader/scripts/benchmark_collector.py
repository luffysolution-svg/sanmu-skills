"""Cross-literature benchmark structured collector and ledger.

Manages, deduplicates, and logs standardized catalytic and materials performance
benchmarks into a persistent CSV/JSON ledger across literature reviews.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

BENCHMARK_FIELDS = [
    "doi",
    "citation_key",
    "year",
    "journal",
    "catalyst_system",
    "reaction_type",
    "activity_rate",
    "activity_unit",
    "aqy_percent",
    "light_condition",
    "scavenger_or_electrolyte",
    "benchmark_comparison",
    "mechanism",
    "record_date",
    "record_id",
    "condition_id",
    "source"
]


class BenchmarkError(Exception):
    pass


def load_benchmarks(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        if reader.fieldnames and len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise BenchmarkError("Duplicate CSV headers; repair a copy before updating.")
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise BenchmarkError("Malformed CSV row; repair a copy before updating.")
        return rows


def save_benchmarks(csv_path: Path, rows: List[Dict[str, str]]) -> None:
    # Keep historical and custom columns, including headers in an empty ledger.
    fields = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            fields = next(csv.reader(f), [])
    for field in BENCHMARK_FIELDS + [key for row in rows for key in row]:
        if field not in fields:
            fields.append(field)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    # Replace only after successful serialization; a failed write leaves the original.
    import os
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=csv_path.parent, suffix=".csv.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, csv_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def upsert_benchmark(csv_path: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Patch one record; omitted fields survive, explicit null/empty strings clear.

    Prefer globally unique record_id, or DOI/citation_key + condition_id. A legacy
    paper-only entry can update only one unqualified historical row. Once records
    have condition/record identifiers, callers must provide one to select a row.
    """
    if not isinstance(entry, dict):
        raise BenchmarkError("Benchmark entry must be a JSON object.")
    if any(not isinstance(k, str) or not k for k in entry):
        raise BenchmarkError("Field names must be non-empty strings.")
    cleaned_entry = {k: "" if v is None else str(v).strip() for k, v in entry.items()}
    doi = cleaned_entry.get("doi", "").lower()
    citation_key = cleaned_entry.get("citation_key", "")
    record_id = cleaned_entry.get("record_id", "")
    condition_id = cleaned_entry.get("condition_id", "")
    if not doi and not citation_key and not record_id:
        raise BenchmarkError("Provide a 'doi', 'citation_key', or existing 'record_id'.")
    rows = load_benchmarks(csv_path)
    paper_matches = [i for i, r in enumerate(rows) if
                     (doi and r.get("doi", "").strip().lower() == doi) or
                     (citation_key and r.get("citation_key", "").strip() == citation_key)]
    # Check the paper identifiers before condition filtering can hide conflicts.
    # Missing legacy identifiers may be filled, but established identities cannot
    # be reassigned (or cleared) by a performance-data patch.
    def identity_conflicts(row, fields):
        return [field for field in fields if field in cleaned_entry
                and row.get(field, "").strip()
                and (row[field].strip().lower() if field == "doi" else row[field].strip())
                != (cleaned_entry[field].lower() if field == "doi" else cleaned_entry[field])]

    for i in paper_matches:
        conflicts = identity_conflicts(rows[i], ("doi", "citation_key"))
        if conflicts:
            raise BenchmarkError(f"Conflicting paper identity at CSV row {i + 2}: {', '.join(conflicts)}. "
                                 "Use the existing identifiers; review identity corrections separately.")
    if record_id:
        matches = [i for i, r in enumerate(rows) if r.get("record_id", "").strip() == record_id]
    elif condition_id:
        matches = [i for i in paper_matches if rows[i].get("condition_id", "").strip() == condition_id]
    else:
        matches = paper_matches
        if len(matches) == 1 and (rows[matches[0]].get("record_id") or rows[matches[0]].get("condition_id")):
            raise BenchmarkError("This paper has an identified record. Supply record_id or the paper's condition_id; use a new identifier to add another condition.")
    if len(matches) > 1:
        candidates = [{"csv_row": i + 2, "record_id": rows[i].get("record_id", ""),
                       "condition_id": rows[i].get("condition_id", "")} for i in matches]
        raise BenchmarkError("Ambiguous benchmark match. Supply a unique record_id or DOI/citation_key + condition_id. "
                             "If historical rows lack identifiers, assign identifiers in a reviewed copy first. "
                             f"Candidates: {json.dumps(candidates, ensure_ascii=False)}")
    target = matches[0] if matches else None
    if target is not None:
        conflicts = identity_conflicts(rows[target], ("record_id", "doi", "citation_key", "condition_id"))
        if conflicts:
            raise BenchmarkError(f"Identity change refused at CSV row {target + 2}: {', '.join(conflicts)}. "
                                 "Use the existing identifiers; add a new record for a different condition.")
        merged = dict(rows[target])
        merged.update(cleaned_entry)
        status = "updated"
    else:
        if not doi and not citation_key:
            raise BenchmarkError("New records need a doi or citation_key in addition to record_id.")
        merged = dict(cleaned_entry)
        if "record_date" not in merged:
            merged["record_date"] = str(date.today())
        status = "added"
    # Validate only the resulting record against its peers. Unrelated historical
    # duplicates do not prevent updates elsewhere in the ledger.
    for i, row in enumerate(rows):
        if i == target:
            continue
        same_record = bool(merged.get("record_id")) and merged["record_id"] == row.get("record_id", "").strip()
        same_paper = ((bool(merged.get("doi")) and merged["doi"].lower() == row.get("doi", "").strip().lower())
                      or (bool(merged.get("citation_key")) and merged["citation_key"] == row.get("citation_key", "").strip()))
        if same_paper:
            for field in ("doi", "citation_key"):
                existing, resulting = row.get(field, "").strip(), merged.get(field, "").strip()
                if field == "doi":
                    existing, resulting = existing.lower(), resulting.lower()
                if existing and resulting and existing != resulting:
                    raise BenchmarkError(f"Conflicting resulting paper identity with CSV row {i + 2}: {field}. "
                                         "Use consistent DOI/citation_key identifiers; review corrections separately.")
        same_condition = (bool(merged.get("condition_id"))
                          and merged["condition_id"] == row.get("condition_id", "").strip())
        if same_record or (same_paper and same_condition):
            raise BenchmarkError(f"Duplicate resulting identity with CSV row {i + 2}. "
                                 "Use that record's existing record_id or paper + condition_id; review duplicates separately.")
    if target is None:
        rows.append(merged)
    else:
        rows[target] = merged
    save_benchmarks(csv_path, rows)
    return {"status": status, "total_records": len(rows), "entry": merged}


def export_markdown_table(csv_path: Path) -> str:
    rows = load_benchmarks(csv_path)
    if not rows:
        return "*暂无 Benchmark 性能记录。*"

    headers = [
        "记录/工况", "催化体系", "年份/期刊", "反应类型", "活性速率", "AQY (%)", "光源/条件", "牺牲剂/电解液", "对比/机制", "来源"
    ]
    md_lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |"
    ]

    for r in rows:
        sys_name = r.get("catalyst_system", "-")
        year_j = f"{r.get('year', '')} {r.get('journal', '')}".strip()
        react = r.get("reaction_type", "-")
        rate = f"{r.get('activity_rate', '')} {r.get('activity_unit', '')}".strip()
        aqy = r.get("aqy_percent", "-")
        light = r.get("light_condition", "-")
        scav = r.get("scavenger_or_electrolyte", "-")
        mech = f"{r.get('benchmark_comparison', '')}; {r.get('mechanism', '')}".strip(" ;")

        identity = " / ".join(filter(None, [r.get("record_id"), r.get("condition_id")])) or "legacy"
        source = " / ".join(filter(None, [r.get("doi") or r.get("citation_key"), r.get("source")]))
        cells = [identity, f"**{sys_name}**", year_j, react, rate, aqy, light, scav, mech, source]
        md_lines.append("| " + " | ".join(c.replace("|", r"\|").replace("\n", "<br>") for c in cells) + " |")

    return "\n".join(md_lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Collector Tool")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    add_p = subparsers.add_parser("add", help="Add or update a benchmark entry")
    add_p.add_argument("csv_path", help="Path to benchmarks.csv")
    add_p.add_argument("--json-entry", required=True, help="JSON string representing the benchmark record")

    export_p = subparsers.add_parser("export-md", help="Export benchmarks as Markdown table")
    export_p.add_argument("csv_path", help="Path to benchmarks.csv")
    export_p.add_argument("--out", help="Output file path")

    list_p = subparsers.add_parser("list", help="List all benchmark entries")
    list_p.add_argument("csv_path", help="Path to benchmarks.csv")

    args = parser.parse_args(argv)

    try:
        csv_path = Path(args.csv_path)
        if args.subcommand == "add":
            data = json.loads(args.json_entry)
            res = upsert_benchmark(csv_path, data)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif args.subcommand == "export-md":
            table_md = export_markdown_table(csv_path)
            if args.out:
                Path(args.out).write_text(table_md, encoding="utf-8")
                print(f"Exported to {args.out}")
            else:
                print(table_md)
        elif args.subcommand == "list":
            rows = load_benchmarks(csv_path)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
