"""Locate SI figure captions and save images or explicitly labelled page previews.

Caption detection is heuristic. Composite and multi-figure pages are rendered in
full rather than claiming that their largest embedded image is a complete figure.
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


class FigureExtractError(Exception):
    pass


def extract_si_figures(
    pdf_path: Path,
    out_dir: Path,
    figure_numbers: Optional[List[int]] = None,
    dpi: int = 300
) -> List[Dict[str, Any]]:
    """Locate Figure/Fig. S captions; return image/page metadata for each figure.

    ``extraction_mode=page_render`` means a shared page preview, not an isolated
    figure. Only a single-image, single-caption page without vector drawings is
    eligible for embedded-image extraction. ``is_isolated_figure`` denotes only
    that structural candidate, never verified completeness; text labels, clipping
    or rotation may be absent from the stream. ``requires_visual_check`` is always
    true so consumers must inspect the source page before treating it as complete.
    """
    if not HAS_PYMUPDF:
        raise FigureExtractError("PyMuPDF (fitz) is required for SI figure extraction.")

    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        raise FigureExtractError(f"SI PDF not found: {pdf_file}")

    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    if dpi <= 0:
        raise FigureExtractError("DPI must be greater than zero.")

    extracted: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\s*(?:Figure|Fig\.?)\s*S\s*(\d+)\b[. :	\-–—]*(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    with fitz.open(pdf_file) as doc:
        fig_map: Dict[int, Dict[str, Any]] = {}
        page_figures: Dict[int, List[int]] = {}
        for p_no, page in enumerate(doc):
            for match in pattern.finditer(page.get_text("text")):
                f_num = int(match.group(1))
                page_figures.setdefault(p_no, [])
                if f_num not in page_figures[p_no]:
                    page_figures[p_no].append(f_num)
                if f_num not in fig_map:
                    fig_map[f_num] = {
                        "page_idx": p_no,
                        "page_num": p_no + 1,
                        "caption": match.group(2).strip(),
                    }

        target_nums = figure_numbers if figure_numbers else sorted(fig_map)
        page_outputs: Dict[int, Dict[str, Any]] = {}
        for f_num in target_nums:
            if f_num not in fig_map:
                continue
            meta = fig_map[f_num]
            p_idx = meta["page_idx"]
            page = doc[p_idx]
            if p_idx not in page_outputs:
                images = page.get_images(full=True)
                best_img = None
                best_xref = None
                max_size = 0
                for img_info in images:
                    base_img = doc.extract_image(img_info[0])
                    if base_img and len(base_img["image"]) > max_size:
                        max_size = len(base_img["image"])
                        best_img = base_img
                        best_xref = img_info[0]

                # Multiple streams, repeated placements, vector content or multiple
                # captions can form composites. Preserve the whole page in those cases.
                isolated = (
                    len(images) == 1
                    and len(page.get_image_rects(images[0][0])) == 1
                    and not images[0][1]  # soft masks need compositing
                    and len(page_figures[p_idx]) == 1
                    and not page.get_drawings()
                )
                if isolated and best_img and max_size > 5000:
                    filename = f"Figure_S{f_num:02d}.png"
                    out_path = target_dir / filename
                    if best_img["ext"].lower() == "png":
                        out_path.write_bytes(best_img["image"])
                    else:
                        pix = fitz.Pixmap(doc, best_xref)
                        if pix.colorspace and pix.colorspace.n != 3:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        pix.save(str(out_path))
                    width, height = best_img["width"], best_img["height"]
                    mode = "embedded_image"
                else:
                    filename = f"Page_{p_idx + 1:03d}.png"
                    out_path = target_dir / filename
                    pix = page.get_pixmap(dpi=dpi, alpha=False)
                    pix.save(str(out_path))
                    width, height = pix.width, pix.height
                    mode = "page_render"
                page_outputs[p_idx] = {
                    "filename": filename,
                    "path": str(out_path),
                    "relative_path": filename,
                    "width": width,
                    "height": height,
                    "extraction_mode": mode,
                    "is_isolated_figure": mode == "embedded_image",  # structural candidate only
                    "requires_visual_check": True,
                    "page_figure_ids": [f"Figure S{n}" for n in page_figures[p_idx]],
                }
            extracted.append({
                "figure_id": f"Figure S{f_num}",
                "figure_number": f_num,
                "page": meta["page_num"],
                "caption": meta["caption"],
                **page_outputs[p_idx],
            })

    # Save manifest
    manifest_path = target_dir / "si_figures_manifest.json"
    manifest_path.write_text(json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")

    return extracted


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Locate SI figures and save embedded images or labelled page previews")
    parser.add_argument("pdf_path", help="Path to Supporting Information PDF")
    parser.add_argument("--out-dir", required=True, help="Directory to save extracted figures")
    parser.add_argument("--figures", help="Comma-separated figure numbers (e.g. 1,2,4,10)")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for fallback rendering (default: 300)")

    args = parser.parse_args(argv)

    try:
        f_nums = [int(n.strip()) for n in args.figures.split(",") if n.strip()] if args.figures else None
        res = extract_si_figures(
            pdf_path=Path(args.pdf_path),
            out_dir=Path(args.out_dir),
            figure_numbers=f_nums,
            dpi=args.dpi
        )
        print(f"Located {len(res)} figure captions; images/page previews saved to {args.out_dir}")
        for r in res[:5]:
            print(f"- {r['figure_id']} (p.{r['page']}): {r['filename']} ({r['width']}x{r['height']})")
        if len(res) > 5:
            print(f"... and {len(res) - 5} more figures.")
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
