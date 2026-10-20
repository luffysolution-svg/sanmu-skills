import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))
from audit_figures import (audit_page_images, audit_workspace, extract_figure_numbers,
                           generate_confident_repairs_for_blocks)


class AuditFiguresTests(unittest.TestCase):
    def test_single_figure_multi_panel_clustering(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 200, 400, 600],
             'image_caption': ['图 2.4 晶胞结构 (a) 体心立方'], 'image_footnote': []},
            {'type': 'image', 'bbox': [500, 200, 800, 600],
             'image_caption': ['(b) 面心立方'], 'image_footnote': []},
        ]
        inv, reps, anoms = audit_page_images(page_num=44, blocks=blocks)
        self.assertEqual(len(inv), 2)
        # Should generate 1 update_image and 1 skip
        self.assertEqual(len(reps), 2)
        self.assertEqual(reps[0]['action'], 'update_image')
        self.assertEqual(reps[1]['action'], 'skip')
        self.assertEqual(reps[0]['new_bbox'], [97, 197, 803, 603])
        self.assertIn('图 2.4 晶胞结构 (a) 体心立方', reps[0]['image_caption'])

    def test_multiple_figure_numbers_flagged(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 100, 800, 500],
             'image_caption': ['图 1.1 结构图', '图 1.2 曲线图'], 'image_footnote': []}
        ]
        inv, reps, anoms = audit_page_images(page_num=10, blocks=blocks)
        self.assertTrue(any(a['kind'] == 'multiple_figure_numbers' for a in anoms))

    def test_inverted_reading_order_detected(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 600, 800, 900], 'image_caption': ['图 3.1 底部插图']},
            {'type': 'text', 'bbox': [100, 100, 800, 300], 'text': '物理上位于上方的正文'},
        ]
        inv, reps, anoms = audit_page_images(page_num=25, blocks=blocks)
        self.assertTrue(any(a['kind'] == 'inverted_reading_order' for a in anoms))

    def test_english_explicit_prefixes_and_normalization(self):
        for caption, expected in [
            ('Figure 1. Main plot', '1'),
            ('Fig. 2 | Structure', '2'),
            ('  FIG. 3-2: Structure', '3.2'),
            ('Figure 4．1. Structure', '4.1'),
            ('Extended Data Fig. 1 | Structure', 'extended-data:1'),
            ('extended data Figure 2-1. Plot', 'extended-data:2.1'),
        ]:
            with self.subTest(caption=caption):
                self.assertEqual(extract_figure_numbers([caption]), [expected])
        self.assertEqual(extract_figure_numbers(['Figure 1. Plot', 'Fig. 1. Plot']), ['1'])

    def test_caption_body_references_are_not_english_labels(self):
        for caption, expected in [
            ('Figure 1. Compare Fig. 2 and Extended Data Fig. 1.', ['1']),
            ('Extended Data Fig. 1. Related to Figure 1.', ['extended-data:1']),
            ('Fig. 1. A wrapped reference to\nFig. 2 is in the body.', ['1']),
            ('See Figure 1 for details.', []),
            ('As in Extended Data Fig. 1, points overlap.', []),
            ('Supplementary Figure 1. Supplementary plot', ['supplemental:S1']),
            ('(b) compare Fig. 2.', []),
        ]:
            with self.subTest(caption=caption):
                self.assertEqual(extract_figure_numbers([caption]), expected)

    def test_chinese_numbering_and_multiple_caption_entries_preserved(self):
        # 只解析题注开头的编号；正文中的“见图 2.5”是交叉引用，与英文
        # "see Fig. 2" 同等处理，不能当作第二个图号参与聚类。
        self.assertEqual(extract_figure_numbers([
            '图 2-4 晶胞结构，见图 2．5', '图2.4 重复',
        ]), ['2.4'])
        self.assertEqual(extract_figure_numbers([
            'Figure 1. First plot; see Fig. 9.', 'Fig. 2. Second plot',
        ]), ['1', '2'])

    def test_main_and_extended_data_same_number_do_not_cluster(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 100, 400, 400],
             'image_caption': ['Fig. 1. Main plot; see Extended Data Fig. 1.']},
            {'type': 'image', 'bbox': [500, 100, 800, 400],
             'image_caption': ['Extended Data Fig. 1. Supporting plot; see Figure 1.']},
        ]
        inv, reps, anoms = audit_page_images(1, blocks)
        self.assertEqual([item['numbers'] for item in inv], [['1'], ['extended-data:1']])
        self.assertEqual(reps, [])
        self.assertEqual(anoms, [])

    def test_main_and_extended_fragments_produce_separate_repair_groups(self):
        blocks = [
            {'type': 'image', 'bbox': [100, 100, 300, 300],
             'image_caption': ['Figure 1. Main (a); see Extended Data Fig. 1.']},
            {'type': 'image', 'bbox': [400, 100, 600, 300],
             'image_caption': ['Fig. 1. Main (b)']},
            {'type': 'image', 'bbox': [100, 600, 300, 800],
             'image_caption': ['Extended Data Fig. 1. Supporting (a); see Fig. 1.']},
            {'type': 'image', 'bbox': [400, 600, 600, 800],
             'image_caption': ['Extended Data Figure 1. Supporting (b)']},
        ]
        _, repairs, anomalies = audit_page_images(1, blocks)
        updates = [r for r in repairs if r['action'] == 'update_image']
        self.assertEqual([r['new_bbox'] for r in updates],
                         [[97, 97, 603, 303], [97, 597, 603, 803]])
        self.assertEqual([r['bbox'] for r in repairs if r['action'] == 'skip'],
                         [[400, 100, 600, 300], [400, 600, 600, 800]])
        self.assertEqual(anomalies, [])

    def test_english_fragment_candidates_remain_manual_drafts(self):
        for prefix in ('Figure', 'Fig.', 'Extended Data Fig.'):
            with self.subTest(prefix=prefix):
                blocks = self.fragmented_blocks()
                blocks[0]['image_caption'] = [f'{prefix} 1. Fragment (a); see Fig. 9.']
                original = copy.deepcopy(blocks)
                inv, reps, anoms = audit_page_images(1, blocks)
                self.assertEqual(len(reps), 2)
                self.assertEqual(reps[0]['new_bbox'], [97, 197, 803, 603])
                self.assertEqual(anoms, [])
                self.assertTrue(all('待原页人工确认' in r['reason'] for r in reps))
                self.assertFalse(any(r.get('verified') or r.get('applied') for r in reps))
                self.assertEqual(blocks, original)

    @staticmethod
    def fragmented_blocks(page_idx=0):
        return [
            {'type': 'image', 'page_idx': page_idx, 'bbox': [100, 200, 400, 600],
             'img_path': 'images/a.jpg', 'image_caption': ['Fig. 1. Fragment (a)']},
            {'type': 'image', 'page_idx': page_idx, 'bbox': [500, 200, 800, 600],
             'img_path': 'images/b.jpg', 'image_caption': ['(b)']},
        ]

    def write_content(self, root, relative, blocks=None):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(blocks if blocks is not None else self.fragmented_blocks()),
                        encoding='utf-8')
        return path

    def cli(self, root, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / 'audit_figures.py'),
                               str(root), *map(str, args)],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_independent_sources_keep_local_pages_and_distinct_path_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = self.write_content(root, 'raw/a/shared/content_list.json',
                                   self.fragmented_blocks(page_idx=120))
            b = self.write_content(root, 'raw/b/shared/content_list.json',
                                   self.fragmented_blocks(page_idx=120))
            originals = [p.read_bytes() for p in (a, b)]
            report, repairs = audit_workspace(root)
            self.assertEqual(repairs, [])
            self.assertFalse(report['repair_output_allowed'])
            self.assertEqual(report['proposed_repairs'], 4)
            self.assertEqual(report['inventory_summary']['unique_pages_with_figures'], 2)
            sources = report['sources']
            self.assertEqual([s['source_id'] for s in sources],
                             ['a/shared/content_list.json', 'b/shared/content_list.json'])
            for source in sources:
                self.assertEqual(source['page_numbering'], 'document-local')
                self.assertEqual([i['page'] for i in source['inventory']], [121, 121])
                self.assertEqual([r['page'] for r in source['repairs']], [121, 121])
                self.assertTrue(all(i['source_id'] == source['source_id']
                                    for i in source['inventory']))
            self.assertEqual(audit_workspace(root / 'raw'), (report, repairs))
            self.write_content(root, 'raw/0/earlier/content_list.json')
            updated, _ = audit_workspace(root)
            self.assertEqual(updated['sources'][1:], sources)
            self.assertEqual([p.read_bytes() for p in (a, b)], originals)

    def test_old_hundred_page_offsets_cannot_collide_independent_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'a/content_list.json', self.fragmented_blocks(100))
            self.write_content(root, 'b/content_list.json', self.fragmented_blocks(0))
            report, repairs = audit_workspace(root)
            self.assertEqual(repairs, [])
            self.assertEqual([[r['page'] for r in s['repairs']] for s in report['sources']],
                             [[101, 101], [1, 1]])
            self.assertEqual(report['inventory_summary']['unique_pages_with_figures'], 2)

    def test_multi_document_cli_rejects_flat_output_without_touching_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'raw/a/content_list.json')
            self.write_content(root, 'raw/b/content_list.json')
            out = root / 'repairs.json'
            report_path = root / 'report.json'
            out.write_text('existing repair file', encoding='utf-8')
            result = self.cli(root, '--out-repairs', out, '--out-report', report_path)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('single document', result.stderr)
            self.assertEqual(out.read_text(encoding='utf-8'), 'existing repair file')
            self.assertFalse(report_path.exists())
            out.unlink()
            result = self.cli(root, '--out-repairs', out)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(out.exists())
            result = self.cli(root, '--out-report', report_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(len(report['sources']), 2)
            self.assertEqual([len(s['repairs']) for s in report['sources']], [2, 2])
            self.assertEqual(report['repair_status'], 'draft_requires_manual_source_confirmation')

    def test_single_document_cli_exports_local_page_draft(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'paper/content_list.json', self.fragmented_blocks(5))
            out = root / 'repairs.json'
            report_path = root / 'report.json'
            result = self.cli(root / 'paper', '--out-repairs', out, '--out-report', report_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            repairs = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual([r['page'] for r in repairs], [6, 6])
            self.assertNotIn('source_id', repairs[0])  # Strict builder repair schema.
            report = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertTrue(report['repair_output_allowed'])
            self.assertEqual(report['sources'][0]['repairs'], repairs)

    def test_manifest_global_pages_and_nested_content_list_match_builder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'raw/part-a/auto/content_list.json')
            self.write_content(root, 'raw/part-b/auto/content_list.json', self.fragmented_blocks(2))
            # The builder uses the unversioned list, not a v2 alternative.
            self.write_content(root, 'raw/part-a/auto/content_list_v2.json', [])
            manifest = {'parts': [
                {'id': 'part-a', 'start_page': 1, 'end_page': 100},
                {'id': 'part-b', 'start_page': 101, 'end_page': 150},
            ]}
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            report, repairs = audit_workspace(root)
            self.assertTrue(report['repair_output_allowed'])
            self.assertEqual([r['page'] for r in repairs], [1, 1, 103, 103])
            self.assertEqual([s['source_id'] for s in report['sources']], ['part-a', 'part-b'])
            out = root / 'repairs.json'
            result = self.cli(root / 'raw', '--manifest', manifest_path, '--out-repairs', out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(out.read_text(encoding='utf-8')), repairs)

    def test_anomalies_carry_source_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for source in ('a', 'b'):
                self.write_content(root, f'{source}/content_list.json', [
                    {'type': 'image', 'bbox': [100, 100, 800, 500],
                     'image_caption': ['Fig. 1. Main', 'Extended Data Fig. 1. Extra']},
                ])
            report, _ = audit_workspace(root)
            self.assertEqual([a['source_id'] for a in report['anomalies']],
                             ['a/content_list.json', 'b/content_list.json'])
            self.assertTrue(all(a['page'] == 1 for a in report['anomalies']))

    def test_audit_workspace_without_manifest_ignores_content_list_v2(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'paper/content_list.json', self.fragmented_blocks(0))
            # MinerU also generates a nested per-page list which must not cause parse errors
            self.write_content(root, 'paper/content_list_v2.json', [['nested', 'list', 'per', 'page']])
            report, repairs = audit_workspace(root)
            self.assertEqual(len(report['sources']), 1)
            self.assertEqual(report['sources'][0]['source_id'], 'paper/content_list.json')
            self.assertEqual(len(repairs), 2)  # Unambiguous single document produces 2 repairs (update + skip)

    def test_supplemental_figure_numbers_and_namespaces(self):
        for caption, expected in [
            ('Figure S1. Supplemental crystal structure', 'supplemental:S1'),
            ('Fig. S2: Adsorption energy curves', 'supplemental:S2'),
            ('Figure S3-1. Multi panel comparison', 'supplemental:S3.1'),
            ('图 S1 晶胞参数对比', 'supplemental:S1'),
            ('附图 S2 能量曲线', 'supplemental:S2'),
            ('附图 3 补充图', 'supplemental:S3'),
        ]:
            with self.subTest(caption=caption):
                self.assertEqual(extract_figure_numbers([caption]), [expected])

    def test_generate_confident_repairs_for_blocks(self):
        blocks = [
            {'type': 'image', '_page': 1, 'bbox': [100, 100, 400, 500],
             'image_caption': ['Fig. 1. Overall scheme (a)']},
            {'type': 'image', '_page': 1, 'bbox': [450, 100, 800, 500],
             'image_caption': ['(b) Zoom in']},
            {'type': 'image', '_page': 2, 'bbox': [100, 100, 400, 500],
             'image_caption': ['Fig. S1. Supplemental structure (a)']},
            {'type': 'image', '_page': 2, 'bbox': [450, 100, 800, 500],
             'image_caption': ['(b) Zoomed detail']},
        ]
        repairs = generate_confident_repairs_for_blocks(blocks)
        self.assertEqual(len(repairs), 4)
        page1_update = next(r for r in repairs if r['page'] == 1 and r['action'] == 'update_image')
        page2_update = next(r for r in repairs if r['page'] == 2 and r['action'] == 'update_image')
        self.assertEqual(page1_update['confidence'], 'high')
        self.assertEqual(page2_update['confidence'], 'high')
        self.assertIn('Fig. 1. Overall scheme (a)', page1_update['image_caption'])
        self.assertIn('Fig. S1. Supplemental structure (a)', page2_update['image_caption'])


    def test_manifest_ambiguous_content_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'raw/part/a_content_list.json')
            self.write_content(root, 'raw/part/b_content_list.json')
            (root / 'manifest.json').write_text(json.dumps({
                'parts': [{'id': 'part', 'start_page': 1}]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'expected one content_list'):
                audit_workspace(root)

    def test_explicit_missing_manifest_does_not_fall_back_to_local_pages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'content_list.json')
            with self.assertRaises(FileNotFoundError):
                audit_workspace(root, root / 'missing.json')

    def test_manifest_page_and_source_ambiguity_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_content(root, 'raw/a/content_list.json')
            self.write_content(root, 'raw/b/content_list.json')
            for parts, message in [
                ([{'id': 'a', 'start_page': 1}, {'id': 'b', 'start_page': 1}],
                 'ambiguous manifest page'),
                ([{'id': 'a', 'start_page': 1}, {'id': 'a', 'start_page': 2}],
                 'duplicate manifest part'),
                ([{'id': 'a'}], 'start_page'),
                ([{'id': '../a', 'start_page': 1}], '相对路径'),
            ]:
                with self.subTest(parts=parts):
                    (root / 'manifest.json').write_text(json.dumps({'parts': parts}), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, message):
                        audit_workspace(root)


if __name__ == '__main__':
    unittest.main()
