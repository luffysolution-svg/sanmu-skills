import json
import tempfile
import unittest
from pathlib import Path

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'
import sys
sys.path.insert(0, str(SCRIPTS))

from zotero_sync import (
    compute_metadata_diff,
    extract_frontmatter_and_content,
    extract_notes_section,
    markdown_to_zotero_html,
)


class ZoteroSyncTests(unittest.TestCase):
    def test_extract_notes_section(self):
        body = (
            '# 导读\n\n'
            '正文内容在这里。\n\n'
            '## 阅读笔记\n'
            '1. 这是一篇重要的逆向设计文献。\n'
            '2. 晶格参数预测精度达到 98%。\n\n'
            '## 参考文献\n'
            '[1] Nature, 2026.\n'
        )
        notes = extract_notes_section(body)
        self.assertIsNotNone(notes)
        self.assertIn('这是一篇重要的逆向设计文献', notes)
        self.assertNotIn('## 参考文献', notes)
        self.assertNotIn('正文内容在这里', notes)

    def test_markdown_to_zotero_html(self):
        md = '## 结论\n\n- 准确率提升\n- 速度加快\n\n> 核心亮点'
        res_html = markdown_to_zotero_html(md)
        self.assertIn('data-schema-version="9"', res_html)
        self.assertIn('<h2>结论</h2>', res_html)
        self.assertIn('<li>准确率提升</li>', res_html)
        self.assertIn('<blockquote>', res_html)
        self.assertIn('<p>核心亮点</p>', res_html)

    def test_compute_metadata_diff(self):
        fm = {
            'title': 'Corrected Title from PDF',
            'doi': '10.1038/s41586-025-0001',
            'date': '2025-03',
            'authors': ['Alice Smith', 'Bob Jones'],
            'abstract': 'Original abstract.',
        }
        zitem = {
            'data': {
                'title': 'Messy Scanned Title',
                'DOI': '10.1038/s41586-0000',
                'date': '2025-03',
                'creators': [
                    {'name': 'Alice Smith', 'creatorType': 'author'},
                    {'name': 'Bob Jones', 'creatorType': 'author'},
                ],
                'abstractNote': 'Original abstract.',
            }
        }
        diff = compute_metadata_diff(fm, zitem)
        self.assertIn('date', diff['identical'])
        self.assertIn('abstract', diff['identical'])
        self.assertIn('authors', diff['identical'])
        self.assertIn('title', diff['different'])
        self.assertEqual(diff['different']['title']['local'], 'Corrected Title from PDF')
        self.assertEqual(diff['different']['title']['zotero'], 'Messy Scanned Title')
        self.assertIn('doi', diff['different'])

    def test_extract_frontmatter_and_content(self):
        with tempfile.TemporaryDirectory() as td:
            md_path = Path(td) / 'test.md'
            md_path.write_text(
                '---\ntitle: "Test Paper"\nzotero_item_uri: "zotero://select/library/items/6DXKSD7N"\n---\n\n# Body\nText',
                encoding='utf-8'
            )
            meta, content = extract_frontmatter_and_content(md_path)
            self.assertEqual(meta['title'], 'Test Paper')
            self.assertEqual(meta['zotero_item_uri'], 'zotero://select/library/items/6DXKSD7N')
            self.assertIn('# Body', content)


class SyncRoundTripTests(unittest.TestCase):
    def setUp(self):
        import copy
        import zotero_sync as sync
        self.sync = sync
        self.copy = copy.deepcopy
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.md = self.root / 'paper.md'
        self.item = {'key': 'PAPER001', 'version': 1, 'library': {'type': 'user', 'id': 123},
                     'data': {'key': 'PAPER001', 'itemType': 'journalArticle', 'title': 'Remote',
                              'tags': [], 'creators': []}}
        parent = self
        class Client(sync.ZoteroSyncClient):
            def get_item(self, key):
                if key != parent.item['key']:
                    for rows in parent.children.values():
                        for child in rows:
                            if child.get('key') == key:
                                return parent.copy(child)
                    raise sync.ExportError('Local API returned HTTP 404 for /items/' + key)
                return parent.copy(parent.item)
            def get_children(self, key):
                return parent.copy(parent.children.get(key, []))
            def pdf(self, key):
                return b'%PDF-fixture'
            def get_annotations(self):
                return [{**parent.copy(ann), 'library': parent.item['library'],
                         'data': {**ann['data'], 'parentItem': att}}
                        for att, rows in parent.children.items() for ann in rows
                        if ann['data'].get('itemType') == 'annotation']
        self.client = Client()
        self.children = {}
        self.write(title='Remote')

    def write(self, body='', **values):
        import yaml
        metadata = {'zotero_item_uri': 'zotero://select/library/items/PAPER001', **values}
        self.md.write_text('---\n' + yaml.safe_dump(metadata, allow_unicode=True) + '---\n' + body, encoding='utf-8')

    def run_sync(self, **kwargs):
        return self.sync.sync_document(self.md, self.client, **kwargs)

    def set_title(self, value):
        from markdown_metadata import replace_frontmatter, split_frontmatter
        text = self.md.read_text(encoding='utf-8')
        metadata, _ = split_frontmatter(text)
        metadata['title'] = value
        self.md.write_text(replace_frontmatter(text, metadata), encoding='utf-8')

    def test_three_way_push_pending_readback_then_pull_and_conflict(self):
        self.assertEqual(self.run_sync(apply=True)['status'], 'ok')
        self.set_title('Local edit')
        first = self.run_sync(apply=True)
        self.assertEqual(first['status'], 'pending_mcp')
        self.assertFalse(first['metadata_updated'])
        second = self.run_sync(apply=True)
        self.assertEqual(first['operations'][0]['id'], second['operations'][0]['id'])
        self.item['data']['title'] = 'Local edit'
        self.item['version'] += 1
        self.assertEqual(self.run_sync(apply=True)['status'], 'ok')
        self.item['data']['title'] = 'Remote next'
        self.assertEqual(self.run_sync(apply=True)['status'], 'ok')
        self.assertEqual(extract_frontmatter_and_content(self.md)[0]['title'], 'Remote next')
        self.set_title('Local conflict')
        self.item['data']['title'] = 'Remote conflict'
        before = self.md.read_bytes()
        result = self.run_sync(apply=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.md.read_bytes(), before)

    def test_initial_direction_required_and_empty_clear_blocked(self):
        self.write(title='Local')
        self.assertEqual(self.run_sync(apply=True)['status'], 'blocked')
        self.assertFalse(self.md.with_name('.paper.md.zotero-sync.json').exists())
        self.assertEqual(self.run_sync(apply=True, initial_direction='remote')['status'], 'ok')
        self.set_title('')
        self.assertEqual(self.run_sync(apply=True)['fields']['title']['status'], 'blocked_empty_clear')
        self.assertEqual(self.item['data']['title'], 'Remote')

    def test_missing_remote_values_stay_omitted_and_frontmatter_boundaries(self):
        self.run_sync(apply=True)
        metadata, _ = extract_frontmatter_and_content(self.md)
        self.assertNotIn('doi', metadata)
        self.md.write_bytes('\ufeff---\r\ntitle: "before---after"\r\nabstract: |\r\n  text --- text\r\n...\r\nBody\r\n'.encode())
        metadata, body = extract_frontmatter_and_content(self.md)
        self.assertEqual(metadata['title'], 'before---after')
        self.assertEqual(body, 'Body\r\n')
        self.md.write_text('---\ntitle: incomplete', encoding='utf-8')
        with self.assertRaises(ValueError):
            extract_frontmatter_and_content(self.md)

    def test_note_create_readback_update_idempotence_preserves_user_notes(self):
        self.write('## 阅读笔记\nFirst note\n', title='Remote')
        self.children['PAPER001'] = [{'key': 'USERNOTE', 'version': 1,
            'data': {'itemType': 'note', 'note': '<p>User note</p>'}}]
        first = self.run_sync(apply=True)
        op = first['operations'][0]
        self.assertEqual(op['kind'], 'note_create')
        # A different renderer wrapper must still confirm the complete content.
        remote_html = '<div><p>' + op['marker'] + '</p><p>First note</p></div>'
        self.children['PAPER001'].append({'key': 'MANAGED1', 'version': 2,
            'data': {'itemType': 'note', 'note': remote_html}})
        verified = self.run_sync(apply=True)
        self.assertEqual(verified['status'], 'ok')
        self.assertTrue(verified['notes_synced_to_zotero'])
        self.assertEqual(self.run_sync(apply=True)['operations'], [])
        self.md.write_text(self.md.read_text(encoding='utf-8').replace('First note', 'Updated note'), encoding='utf-8')
        update = self.run_sync(apply=True)['operations'][0]
        self.assertEqual(update['kind'], 'note_update')
        self.assertEqual(update['key'], 'MANAGED1')
        self.children['PAPER001'][1]['data']['note'] = update['parameters']['fields']['note']
        self.assertEqual(self.run_sync(apply=True)['status'], 'ok')
        self.assertEqual(self.run_sync(apply=True)['operations'], [])
        self.children['PAPER001'][1]['data']['note'] += '<p>Human remote edit</p>'
        self.assertEqual(self.run_sync(apply=True)['status'], 'blocked')
        self.assertEqual(self.children['PAPER001'][0]['data']['note'], '<p>User note</p>')

    def test_annotation_update_delete_and_manual_body_preserved(self):
        self.write('Human introduction\n\n## Other\nHuman ending\n', title='Remote')
        self.children['PAPER001'] = [{'key': 'ATTACH01', 'data': {'itemType': 'attachment',
            'contentType': 'application/pdf', 'title': 'Supporting information'}}]
        self.children['ATTACH01'] = [{'key': 'ANNOT001', 'data': {'itemType': 'annotation',
            'annotationText': 'first', 'annotationComment': 'comment', 'annotationPageLabel': 'i'}}]
        self.run_sync(apply=True, pull_annotations=True)
        self.children['ATTACH01'][0]['data']['annotationText'] = 'changed'
        self.run_sync(apply=True, pull_annotations=True)
        text = self.md.read_text(encoding='utf-8')
        self.assertIn('changed', text)
        self.assertNotIn('\nfirst\n', text)
        self.assertIn('Human ending', text)
        self.assertEqual(text.count('<!-- annotation:ATTACH01:ANNOT001 -->'), 1)
        self.children['ATTACH01'] = []
        self.run_sync(apply=True, pull_annotations=True)
        text = self.md.read_text(encoding='utf-8')
        self.assertNotIn('ANNOT001', text)
        self.md.write_text(text.replace('受管区域', 'manual edits'), encoding='utf-8')
        self.assertEqual(self.run_sync(apply=True, pull_annotations=True)['status'], 'blocked')

    def test_annotation_region_survives_line_ending_conversion(self):
        # Obsidian / editors may rewrite CRLF to LF without touching the content.
        self.write('Human introduction\n', title='Remote')
        self.children['PAPER001'] = [{'key': 'ATTACH01', 'data': {'itemType': 'attachment',
            'contentType': 'application/pdf', 'title': 'Full Text PDF'}}]
        self.children['ATTACH01'] = [{'key': 'ANNOT001', 'data': {'itemType': 'annotation',
            'annotationText': 'quote', 'annotationComment': 'note', 'annotationPageLabel': '3'}}]
        self.run_sync(apply=True, pull_annotations=True)
        crlf = self.md.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        self.md.write_bytes(crlf)
        report = self.run_sync(apply=True, pull_annotations=True)
        self.assertEqual(report['status'], 'ok', report['conflicts'])

    def test_group_misrouting_and_response_identity_fail_closed(self):
        self.write(zotero_item_uri='zotero://select/groups/42/items/PAPER001')
        with self.assertRaisesRegex(ValueError, 'Group document'):
            self.run_sync(apply=True)
        self.client = type(self.client)('http://127.0.0.1:23119/api/groups/42')
        with self.assertRaisesRegex(ValueError, 'library type mismatch'):
            self.run_sync(apply=True)
        self.item['library'] = {'type': 'group', 'id': 42}
        result = self.run_sync(apply=True)
        self.assertEqual(result['library'], {'type': 'group', 'id': 42})
        self.write(title='Remote')
        with self.assertRaisesRegex(ValueError, 'Personal document'):
            self.run_sync(apply=True)

    def test_unsupported_authors_relations_and_tag_mapping(self):
        self.write(title='Remote', authors=['Lossy Name'], relations={'x': 'y'})
        result = self.run_sync(apply=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('authors', result['unsupported'])
        self.write(title='Remote', tags=[])
        self.run_sync(apply=True)
        metadata, _ = extract_frontmatter_and_content(self.md)
        metadata['tags'] = ['new']
        self.write(**metadata)
        result = self.run_sync(apply=True)
        self.assertEqual(result['operations'][0]['parameters']['addTags'], ['new'])
        self.item['data']['tags'] = [{'tag': 'new'}]
        self.assertEqual(self.run_sync(apply=True)['status'], 'ok')

    def test_http_failure_and_report_collision_return_nonzero_without_writes(self):
        from unittest.mock import patch
        report = self.root / 'report.json'
        self.assertEqual(self.sync.main([str(self.md), '--apply', '--report', str(self.md)]), 1)
        self.assertIn('zotero_item_uri', self.md.read_text(encoding='utf-8'))
        with patch.object(self.sync.ZoteroSyncClient, 'get_item', side_effect=RuntimeError('HTTP 500 failed')):
            code = self.sync.main([str(self.md), '--apply', '--report', str(report)])
        self.assertEqual(code, 1)
        result = json.loads(report.read_text(encoding='utf-8'))['payload']['reports'][0]
        self.assertEqual(result['status'], 'error')
        self.assertIn('HTTP 500', result['error'])
        report.write_text('human report', encoding='utf-8')
        before = self.md.read_bytes()
        self.assertEqual(self.sync.main([str(self.md), '--apply', '--report', str(report)]), 1)
        self.assertEqual(before, self.md.read_bytes())
        self.assertEqual(report.read_text(encoding='utf-8'), 'human report')

    def test_failed_child_page_no_state_or_local_writes(self):
        from unittest.mock import patch
        self.set_title('different')
        before = self.md.read_bytes()
        with patch.object(self.client, 'get_children', side_effect=RuntimeError('HTTP 500')):
            with self.assertRaises(RuntimeError):
                self.run_sync(apply=True, initial_direction='local')
        self.assertEqual(before, self.md.read_bytes())
        self.assertFalse(self.md.with_name('.paper.md.zotero-sync.json').exists())


if __name__ == '__main__':
    unittest.main()
