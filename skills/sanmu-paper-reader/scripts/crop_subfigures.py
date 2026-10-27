"""Sub-figure cropping tool for scientific figures (Python 3.10+, PIL + optional OpenCV).

Supports precise box cropping (normalized 0.0-1.0 coordinates or pixel bounds)
and auto-grid division to isolate individual panels (e.g. Figure 1b, Figure 4f)
for targeted visual comparison in literature notes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class CropError(Exception):
    pass


def crop_image_box(
    im: Image.Image,
    box: Tuple[float, float, float, float],
    normalized: bool = True
) -> Image.Image:
    """Crop an image using [ymin, xmin, ymax, xmax] coordinates."""
    w, h = im.size
    ymin, xmin, ymax, xmax = box

    if normalized:
        left = int(xmin * w)
        top = int(ymin * h)
        right = int(xmax * w)
        bottom = int(ymax * h)
    else:
        left, top, right, bottom = int(xmin), int(ymin), int(xmax), int(ymax)

    left = max(0, min(w - 1, left))
    top = max(0, min(h - 1, top))
    right = max(left + 1, min(w, right))
    bottom = max(top + 1, min(h, bottom))

    return im.crop((left, top, right, bottom))


def crop_subfigures(
    image_path: Path,
    out_dir: Path,
    crop_specs: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Crop multiple specified subfigures from a master scientific figure."""
    if not HAS_PIL:
        raise CropError("Pillow (PIL) is required for subfigure cropping.")

    src_path = Path(image_path).resolve()
    if not src_path.exists():
        raise CropError(f"Master figure file not found: {src_path}")

    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    output_names = set()
    manifest_file = target_dir / f"{src_path.stem}_crops_manifest.json"
    for spec in crop_specs:
        name = spec.get("name", "subfig")
        # The name becomes a file name: keep it to one portable path component.
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) or name in (".", ".."):
            raise CropError(f"Invalid crop name (use 1-64 of A-Z a-z 0-9 . _ -): {name!r}")
        filename = f"{name}.png"
        if filename.casefold() in output_names:
            raise CropError(f"Duplicate crop output name: {name!r}")
        output_names.add(filename.casefold())
        out_file = target_dir / filename
        if out_file.exists() or out_file.is_symlink():
            raise CropError(f"Output already exists; use a new name or directory: {out_file}")
    if manifest_file.exists() or manifest_file.is_symlink():
        raise CropError(f"Manifest already exists; use a new directory: {manifest_file}")

    results: List[Dict[str, Any]] = []
    # Context managers release file handles promptly (Windows locks open files).
    with Image.open(src_path) as master_im:
        for spec in crop_specs:
            name = spec.get("name", "subfig")
            box = spec.get("box")  # [ymin, xmin, ymax, xmax]
            if not box or len(box) != 4:
                continue

            normalized = spec.get("normalized", True)
            filename = f"{name}.png"
            out_file = target_dir / filename
            with crop_image_box(master_im, box, normalized=normalized) as cropped_im:
                with out_file.open("xb") as output:
                    cropped_im.save(output, "PNG")
                width, height = cropped_im.width, cropped_im.height

            results.append({
                "name": name,
                "filename": filename,
                "path": str(out_file),
                "relative_path": out_file.name,
                "width": width,
                "height": height,
                "box": box,
                "description": spec.get("description", "")
            })

    # Save crop manifest
    with manifest_file.open("x", encoding="utf-8") as output:
        output.write(json.dumps(results, ensure_ascii=False, indent=2))

    return results


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Subfigure Precision Crop Tool")
    parser.add_argument("image_path", help="Path to the master figure image")
    parser.add_argument("--out-dir", required=True, help="Directory to save cropped subfigures")
    parser.add_argument("--spec-json", required=True, help="JSON file or string containing crop specifications")

    args = parser.parse_args(argv)

    try:
        spec_text = args.spec_json.strip()
        if spec_text.startswith("{") or spec_text.startswith("["):
            specs_data = json.loads(spec_text)
        else:
            specs_data = json.loads(Path(spec_text).read_text(encoding="utf-8"))

        if isinstance(specs_data, dict) and "crops" in specs_data:
            specs_data = specs_data["crops"]

        res = crop_subfigures(
            image_path=Path(args.image_path),
            out_dir=Path(args.out_dir),
            crop_specs=specs_data
        )
        print(f"Successfully cropped {len(res)} subfigures into {args.out_dir}")
        for r in res:
            print(f"- {r['name']}: {r['filename']} ({r['width']}x{r['height']}) - {r['description']}")
        return 0
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
