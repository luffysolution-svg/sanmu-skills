"""Supporting Information (SI) Fast-Probe tool for rapid parameter extraction.

Enables instant targeting and extraction of synthesis protocols, catalytic testing
parameters, DFT configurations, and Figure S/Table S captions from multi-page SI PDFs
without full heavy OCR pipelines.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


KEYWORD_CATEGORIES = {
    "synthesis": [
        r"synthesis", r"preparation", r"chemicals", r"materials", r"fabrication",
        r"calcination", r"anneal", r"precursor", r"reagent"
    ],
    "catalysis": [
        r"photocatalytic", r"hydrogen evolution", r"her", r"water splitting",
        r"aqy", r"apparent quantum yield", r"scavenger", r"teoa", r"light source",
        r"xenon lamp", r"am\s*1\.5"
    ],
    "electrochemical": [
        r"electrochemical", r"eis", r"tafel", r"lsv", r"cv", r"overpotential",
        r"reference electrode", r"rhe", r"electrolyte"
    ],
    "computation": [
        r"dft", r"vasp", r"computational details", r"free energy", r"adsorption",
        r"energy barrier", r"pbe", r"cut-off", r"k-points", r"hubbard", r"u\s*="
    ],
    "characterization": [
        r"xrd", r"xps", r"xas", r"xanes", r"exafs", r"tem", r"stem", r"sem",
        r"epr", r"esr", r"ramen", r"uv-vis", r"pl", r"trpl", r"bet"
    ]
}


class SIProbeError(Exception):
    """Base exception for SI probe failures."""
    pass


def extract_text_pymupdf(pdf_path: Path) -> List[Dict[str, Any]]:
    """Extract page text using PyMuPDF (fitz)."""
    doc = fitz.open(pdf_path)
    pages = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text("text")
        pages.append({
            "page_num": page_idx + 1,
            "text": text
        })
    doc.close()
    return pages


def probe_si_document(pdf_path: Path, max_snippet_len: int = 1200) -> Dict[str, Any]:
    """Scan and index an SI PDF file, extracting key topics and figure/table references."""
    resolved_path = Path(pdf_path).resolve()
    if not resolved_path.exists():
        raise SIProbeError(f"File not found: {resolved_path}")
    if resolved_path.suffix.lower() != ".pdf":
        raise SIProbeError(f"Input is not a PDF file: {resolved_path.name}")
    if not HAS_PYMUPDF:
        raise SIProbeError("PyMuPDF (fitz) is required for SI fast-probe but is not installed.")

    pages = extract_text_pymupdf(resolved_path)
    total_pages = len(pages)

    # 1. Identify figure and table references
    figures_s: List[Dict[str, Any]] = []
    tables_s: List[Dict[str, Any]] = []

    fig_pattern = re.compile(r"^(?:Figure|Fig\.)\s+(S\d+)[.:\s\-]*(.+)$", re.IGNORECASE | re.MULTILINE)
    tab_pattern = re.compile(r"^(?:Table)\s+(S\d+)[.:\s\-]*(.+)$", re.IGNORECASE | re.MULTILINE)

    for page in pages:
        p_num = page["page_num"]
        p_text = page["text"]

        for match in fig_pattern.finditer(p_text):
            fig_id = f"Figure {match.group(1).upper()}"
            caption = match.group(2).strip()
            # truncate caption if too long
            if len(caption) > 200:
                caption = caption[:200] + "..."
            figures_s.append({
                "id": fig_id,
                "page": p_num,
                "caption": caption
            })

        for match in tab_pattern.finditer(p_text):
            tab_id = f"Table {match.group(1).upper()}"
            caption = match.group(2).strip()
            if len(caption) > 200:
                caption = caption[:200] + "..."
            tables_s.append({
                "id": tab_id,
                "page": p_num,
                "caption": caption
            })

    # Deduplicate by ID
    unique_figures: Dict[str, Dict[str, Any]] = {}
    for f in figures_s:
        if f["id"] not in unique_figures:
            unique_figures[f["id"]] = f

    unique_tables: Dict[str, Dict[str, Any]] = {}
    for t in tables_s:
        if t["id"] not in unique_tables:
            unique_tables[t["id"]] = t

    # 2. Categorize page matches based on domain keywords
    category_matches: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in KEYWORD_CATEGORIES}

    for page in pages:
        p_num = page["page_num"]
        p_text = page["text"]
        p_text_lower = p_text.lower()

        for category, patterns in KEYWORD_CATEGORIES.items():
            matched_keywords = []
            for pat in patterns:
                if re.search(r"\b" + pat + r"\b", p_text_lower):
                    matched_keywords.append(pat)

            if matched_keywords:
                # take first relevant paragraph as snippet
                snippet = ""
                paragraphs = [p.strip() for p in p_text.split("\n\n") if p.strip()]
                for para in paragraphs:
                    if any(re.search(r"\b" + kw + r"\b", para.lower()) for kw in matched_keywords):
                        snippet = para[:max_snippet_len]
                        break
                if not snippet and paragraphs:
                    snippet = paragraphs[0][:max_snippet_len]

                category_matches[category].append({
                    "page": p_num,
                    "matched_keywords": matched_keywords,
                    "snippet": snippet
                })

    return {
        "file_name": resolved_path.name,
        "total_pages": total_pages,
        "figures": list(unique_figures.values()),
        "tables": list(unique_tables.values()),
        "category_matches": category_matches
    }


def search_specific_terms(pdf_path: Path, terms: List[str], max_results: int = 10) -> List[Dict[str, Any]]:
    """Search for exact terms across the SI PDF and return matched contexts."""
    if not HAS_PYMUPDF:
        raise SIProbeError("PyMuPDF (fitz) is required for SI search.")
    doc = fitz.open(pdf_path)
    results = []

    for term in terms:
        clean_term = term.strip()
        if not clean_term:
            continue
        term_pattern = re.compile(re.escape(clean_term), re.IGNORECASE)

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            text = page.get_text("text")
            matches = list(term_pattern.finditer(text))
            for m in matches:
                start = max(0, m.start() - 150)
                end = min(len(text), m.end() + 250)
                context = text[start:end].replace("\n", " ").strip()
                results.append({
                    "term": clean_term,
                    "page": page_idx + 1,
                    "context": context
                })
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

    doc.close()
    return results


def format_markdown_report(probe_result: Dict[str, Any]) -> str:
    """Format probe findings into an easily readable Markdown summary."""
    lines = [
        f"# SI 定向透视摘要: {probe_result['file_name']}",
        f"**总页数**: {probe_result['total_pages']} 页\n",
        "## 1. 核心研究板块定位",
    ]

    for cat, matches in probe_result["category_matches"].items():
        cat_title = {
            "synthesis": "合成与制备 (Synthesis)",
            "catalysis": "催化性能与测试条件 (Catalysis)",
            "electrochemical": "电化学表征 (Electrochemical)",
            "computation": "DFT 理论计算细节 (DFT Details)",
            "characterization": "结构表征与谱学 (Characterization)"
        }.get(cat, cat.capitalize())

        pages_str = ", ".join(f"p.{m['page']}" for m in matches[:6])
        if not pages_str:
            pages_str = "未检测到特征关键词"
        lines.append(f"- **{cat_title}**: {pages_str}")

    lines.append("\n## 2. 补充图表索引")
    if probe_result["tables"]:
        lines.append("**表格 (Tables):**")
        for t in probe_result["tables"][:8]:
            lines.append(f"- **{t['id']}** (p.{t['page']}): {t['caption']}")
    if probe_result["figures"]:
        lines.append("\n**关键图题 (Figures - 前 10 项):**")
        for f in probe_result["figures"][:10]:
            lines.append(f"- **{f['id']}** (p.{f['page']}): {f['caption']}")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="SI Fast-Probe Tool")
    parser.add_argument("pdf_path", help="Path to Supporting Information PDF")
    parser.add_argument("--search", help="Comma-separated search keywords")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--out", help="Write output to specified file")

    args = parser.parse_args(argv)
    pdf_path = Path(args.pdf_path)

    try:
        if args.search:
            terms = [t.strip() for t in args.search.split(",") if t.strip()]
            results = search_specific_terms(pdf_path, terms)
            output_content = json.dumps(results, ensure_ascii=False, indent=2) if args.json else "\n".join(
                f"- [p.{r['page']}] **{r['term']}**: ...{r['context']}..." for r in results
            )
        else:
            data = probe_si_document(pdf_path)
            output_content = json.dumps(data, ensure_ascii=False, indent=2) if args.json else format_markdown_report(data)

        if args.out:
            Path(args.out).write_text(output_content, encoding="utf-8")
            print(f"Report written to {args.out}")
        else:
            print(output_content)
        return 0

    except SIProbeError as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Unexpected error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
