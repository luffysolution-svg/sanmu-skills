"""Regressions for journal reading order and metadata-safe math/rendering."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from build_markdown import build_chapter, FigureStore, figure_numbers, image_groups
from normalize_math import scan_math, protected_markdown
from preview_server import render_markdown


class ResearchCoreTests(unittest.TestCase):
    def test_source_order_preserves_columns_while_geometry_remains_default(self):
        items = [
            {'type': 'text', 'text': 'Left upper paragraph.', 'bbox': [50, 100, 450, 200], 'page_idx': 0},
            {'type': 'text', 'text': 'Left lower paragraph.', 'bbox': [50, 300, 450, 400], 'page_idx': 0},
            {'type': 'text', 'text': 'Right upper paragraph.', 'bbox': [550, 100, 950, 200], 'page_idx': 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            args = (items, None, None, Path(tmp) / 'images', 'Article', [])
            source, _ = build_chapter(*args, mode='book', reading_order='source')
            geometry, _ = build_chapter(*args, mode='book')
        self.assertLess(source.index('Left lower'), source.index('Right upper'))
        self.assertLess(geometry.index('Right upper'), geometry.index('Left lower'))

    def test_extended_figure_namespace_prevents_wrong_merging(self):
        items = [
            {'type': 'image', 'bbox': [50, 100, 450, 400], 'image_caption': ['Fig. 1 | Main figure.']},
            {'type': 'image', 'bbox': [60, 110, 460, 410], 'image_caption': ['Extended Data Fig. 1 | Other figure.']},
        ]
        self.assertEqual(figure_numbers(items), {'1', 'extended-data:1'})
        self.assertEqual(len(image_groups(items)), 2)

    def test_pdf_crops_keep_three_times_source_resolution(self):
        import fitz
        from PIL import Image
        import io
        with tempfile.TemporaryDirectory() as tmp, fitz.open() as pdf:
            page = pdf.new_page(width=1000, height=1000)
            page.draw_line((10, 10), (100, 100))
            blob = FigureStore(Path(tmp)).crop(page, (0, 0, .5, .5))
            with Image.open(io.BytesIO(blob)) as image:
                self.assertEqual(image.size, (1500, 1500))

    def test_yaml_is_not_math_or_rendered_body(self):
        text = '---\ntitle: "A $ literal"\nabstract: |\n  A $broken example and `code`.\n---\n\n# Article\n\n$z$\n'
        spans, issues = scan_math(text)
        self.assertEqual([s.body for s in spans], ['z'])
        self.assertEqual(issues, [])
        masked = protected_markdown(text)
        self.assertEqual(masked.count('\n'), text.count('\n'))
        self.assertNotIn('broken', masked)
        html = render_markdown(text)
        self.assertNotIn('literal', html)
        self.assertIn('data-display="false"', html)

    def test_frontmatter_is_published_with_correct_page_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / 'raw' / 'article'
            raw.mkdir(parents=True)
            (raw / 'article_content_list.json').write_text(json.dumps([
                {'type': 'text', 'text': 'A source paragraph.', 'bbox': [50, 100, 950, 200], 'page_idx': 0}
            ]), encoding='utf-8')
            meta = root / 'metadata.json'
            meta.write_text(json.dumps({'article': {'title': 'A title', 'authors': ['Full Author'], 'abstract': 'A $ literal.'}}), encoding='utf-8')
            out = root / 'output'
            command = [sys.executable, str(SCRIPTS / 'build_markdown.py'), '--raw', str(raw.parent), '--out', str(out), '--mode', 'book', '--reading-order', 'source', '--metadata-json', str(meta)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            text = (out / 'article.md').read_text(encoding='utf-8')
            report = json.loads((root / '.output.build' / 'build-report.json').read_text(encoding='utf-8'))
            entry = report['page_maps']['article'][0]
            self.assertEqual(text.splitlines()[entry['start_line']-1], 'A source paragraph.')
            self.assertTrue(text.startswith('---\n'))
            (out / 'article.md').write_text(text + '\nHuman notes.\n', encoding='utf-8')
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('Human notes.', (out / 'article.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
