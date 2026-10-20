"""Tests for SI Figure Extractor and Subfigure Cropper."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2] / "skills" / "sanmu-paper-reader"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import io
import fitz
from PIL import Image
from scripts.crop_subfigures import crop_image_box, crop_subfigures
from scripts.extract_si_figures import extract_si_figures


class SIFigureExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "si.pdf"
        self.out = self.root / "images"

    def jpeg(self, size=(240, 160)):
        stream = io.BytesIO()
        Image.effect_noise(size, 70).convert("RGB").save(stream, format="JPEG", quality=95)
        return stream.getvalue()

    def test_single_non_png_stream_is_converted_to_png(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_image(fitz.Rect(20, 20, 380, 260), stream=self.jpeg())
            page.insert_text((20, 300), "Fig. S 12. A JPEG figure")
            doc.save(self.pdf)
        row = extract_si_figures(self.pdf, self.out, dpi=72)[0]
        self.assertEqual(row["figure_id"], "Figure S12")
        self.assertEqual(row["caption"], "A JPEG figure")
        self.assertEqual(row["extraction_mode"], "embedded_image")
        with Image.open(row["path"]) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (240, 160))

    def test_embedded_candidate_with_external_labels_requires_visual_check(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_image(fitz.Rect(40, 20, 280, 180), stream=self.jpeg())
            page.insert_text((20, 200), "Time (s)")
            page.insert_text((20, 300), "Fig. S1. Plot with external axis label")
            doc.save(self.pdf)
        row = extract_si_figures(self.pdf, self.out, dpi=72)[0]
        self.assertEqual(row["extraction_mode"], "embedded_image")
        self.assertTrue(row["requires_visual_check"])
        manifest = json.loads((self.out / "si_figures_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest[0]["requires_visual_check"])

    def test_multiple_images_do_not_select_last_xref_or_largest_as_whole(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            # Old code recorded the large stream's dimensions but converted the last.
            page.insert_image(fitz.Rect(10, 10, 250, 170), stream=self.jpeg())
            page.insert_image(fitz.Rect(270, 20, 350, 100), stream=self.jpeg((20, 20)))
            page.insert_text((20, 300), "Figure S1. Composite raster panels")
            doc.save(self.pdf)
        row = extract_si_figures(self.pdf, self.out, dpi=72)[0]
        self.assertEqual(row["extraction_mode"], "page_render")
        self.assertFalse(row["is_isolated_figure"])
        with Image.open(row["path"]) as image:
            self.assertEqual(image.size, (400, 500))
            self.assertEqual(image.size, (row["width"], row["height"]))
            self.assertNotEqual(image.getpixel((300, 60)), (255, 255, 255))

    def test_two_captions_share_explicit_page_preview_even_when_filtered(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.draw_rect(fitz.Rect(20, 20, 150, 100), fill=(1, 0, 0))
            page.insert_text((20, 130), "Figure S1. First plot")
            page.draw_rect(fitz.Rect(20, 180, 150, 260), fill=(0, 0, 1))
            page.insert_text((20, 300), "Fig. S2: Second plot")
            doc.save(self.pdf)
        rows = extract_si_figures(self.pdf, self.out, dpi=72)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["path"], rows[1]["path"])
        self.assertEqual(rows[0]["page_figure_ids"], ["Figure S1", "Figure S2"])
        self.assertTrue(all(r["extraction_mode"] == "page_render" for r in rows))
        self.assertEqual(len(list(self.out.glob("*.png"))), 1)
        filtered = extract_si_figures(self.pdf, self.root / "filtered", [2], dpi=72)
        self.assertEqual(filtered[0]["filename"], "Page_001.png")
        self.assertFalse(filtered[0]["is_isolated_figure"])

    def test_vector_overlay_is_preserved_with_single_embedded_image(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_image(fitz.Rect(10, 10, 250, 170), stream=self.jpeg())
            page.draw_rect(fitz.Rect(280, 30, 340, 90), fill=(1, 0, 0))
            page.insert_text((20, 300), "Figure S3. Raster plus vector panel")
            doc.save(self.pdf)
        row = extract_si_figures(self.pdf, self.out, dpi=72)[0]
        self.assertEqual(row["extraction_mode"], "page_render")
        with Image.open(row["path"]) as image:
            self.assertEqual(image.getpixel((310, 60)), (255, 0, 0))


class CropSubfiguresTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)

        # Create a synthetic 200x100 test image
        self.test_img = Image.new("RGB", (200, 100), color="white")
        self.src_file = self.temp_path / "master_test.png"
        self.test_img.save(self.src_file)

    def test_crop_image_box_normalized(self):
        # crop top-left quadrant [ymin, xmin, ymax, xmax] = [0.0, 0.0, 0.5, 0.5]
        cropped = crop_image_box(self.test_img, (0.0, 0.0, 0.5, 0.5), normalized=True)
        self.assertEqual(cropped.size, (100, 50))

    def test_crop_subfigures_batch(self):
        specs = [
            {"name": "panel_a", "box": [0.0, 0.0, 0.5, 0.5], "description": "top left"},
            {"name": "panel_b", "box": [0.0, 0.5, 0.5, 1.0], "description": "top right"}
        ]
        out_dir = self.temp_path / "subfigs"
        results = crop_subfigures(self.src_file, out_dir, specs)

        self.assertEqual(len(results), 2)
        self.assertTrue((out_dir / "panel_a.png").exists())
        self.assertTrue((out_dir / "panel_b.png").exists())
        manifest = out_dir / "master_test_crops_manifest.json"
        self.assertTrue(manifest.exists())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)


if __name__ == "__main__":
    unittest.main()
