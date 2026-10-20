"""Offline local-API fixture tests; never contact a real Zotero instance."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md')
sys.path.insert(0, str(ROOT / 'scripts'))
import zotero_export as export

PDF = b'%PDF-1.4\nfixture bytes\n%%EOF\n'


def paper(key, kind='journalArticle', **extra):
    return {'key': key, 'library': {'type': 'user', 'id': 0}, 'data': {
        'key': key, 'itemType': kind, 'title': 'Paper with journal metadata',
        'publicationTitle': 'Journal of Tests', 'volume': '12', 'issue': '3',
        'pages': '4-9', 'DOI': '10.1000/test', 'date': '2026',
        'abstractNote': 'Abstract', 'creators': [{'creatorType': 'author',
        'firstName': 'A', 'lastName': 'Researcher'}], **extra}}


def attachment(key, **extra):
    return {'key': key, 'data': {'key': key, 'itemType': 'attachment',
                               'contentType': 'application/pdf', **extra}}


@contextmanager
def api_fixture(top, children=None, files=None, page_size=None):
    children, files = children or {}, files or {}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append((self.command, self.path, dict(self.headers)))
            parsed = urlsplit(self.path)
            path = parsed.path.removeprefix('/api/users/0')
            if path == '/items/top':
                data = top
            elif path.endswith('/children'):
                data = children.get(path.split('/')[2], [])
            elif path.endswith('/file'):
                spec = files.get(path.split('/')[2], (404, b'missing'))
                status, body = spec
                self.send_response(status)
                if status in (301, 302, 303, 307, 308):
                    self.send_header('Location', body)
                    body = b''
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self.send_error(404)
                return
            if isinstance(data, int):
                self.send_error(data)
                return
            params = parse_qs(parsed.query)
            start = int(params.get('start', ['0'])[0])
            limit = min(int(params.get('limit', ['100'])[0]), page_size or 100)
            raw = json.dumps(data[start:start + limit]).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Total-Results', str(len(data)))
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/api/users/0', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ZoteroExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='zotero-export-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / 'out'

    def test_api_file_handoff_percent_decoded_once_and_wrapper_metadata(self):
        # %2520 in the URI must remain a literal %20, not become a space.
        source = self.root / 'literal %20 and %2F 中文 space.pdf'
        source.write_bytes(PDF)
        decoy = self.root / 'literal   and %2F 中文 space.pdf'
        decoy.write_bytes(b'not the selected file')
        item, child = paper('PAPER001'), attachment('ATTACH01')
        with api_fixture([item], {'PAPER001': [child]},
                         {'ATTACH01': (302, source.as_uri())}) as (base, calls):
            # Any attempt at discovery violates the adapter's file-only handoff.
            with patch.object(Path, 'iterdir', side_effect=AssertionError('no browsing')):
                records = export.export_library(self.out, base)
        rec = records[0]
        self.assertEqual(rec['status'], 'ready')
        self.assertEqual(rec['metadata'], item)
        self.assertEqual(rec['attachment'], child)
        self.assertEqual(rec['sha256'], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(Path(rec['path']).read_bytes(), PDF)
        self.assertEqual(source.read_bytes(), PDF)
        self.assertEqual(json.loads((self.out / 'library.json').read_text(encoding='utf-8')), records)
        self.assertEqual((self.out / 'ids.tsv').read_text(encoding='utf-8'),
                         f'{rec["path"]}\tPAPER001\n')
        self.assertEqual([urlsplit(c[1]).path for c in calls], [
            '/api/users/0/items/top', '/api/users/0/items/PAPER001/children',
            '/api/users/0/items/ATTACH01/file'])
        from research_metadata import load_export, metadata_from_export
        loaded = load_export(self.out / 'library.json', 'PAPER001', rec['sha256'])
        fields = metadata_from_export(loaded)
        self.assertEqual(fields['publication_title'], 'Journal of Tests')
        self.assertEqual(fields['doi'], '10.1000/test')
        self.assertEqual(fields['authors'], ['A Researcher'])

    def test_exact_byte_duplicates_keep_alias_records_and_stable_ids(self):
        top = [paper('PAPER002'), paper('PAPER001')]
        children = {'PAPER001': [attachment('ATTACH01')], 'PAPER002': [attachment('ATTACH02')]}
        files = {'ATTACH01': (200, PDF), 'ATTACH02': (200, PDF)}
        with api_fixture(top, children, files) as (base, _):
            first = export.export_library(self.out, base)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.rglob('*') if p.is_file()}
            top.reverse()
            second = export.export_library(self.out, base)
        self.assertEqual(first, second)
        self.assertEqual([r['status'] for r in first], ['ready', 'duplicate_bytes'])
        self.assertEqual(first[1]['duplicate_of'], 'PAPER001')
        self.assertEqual(first[0]['path'], first[1]['path'])
        self.assertEqual(first[0]['sha256'], first[1]['sha256'])
        self.assertEqual(first[1]['metadata']['key'], 'PAPER002')
        self.assertEqual(len(list((self.out / 'sources').glob('*.pdf'))), 1)
        self.assertEqual(len((self.out / 'ids.tsv').read_text(encoding='utf-8').splitlines()), 1)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_no_pdf_and_excluded_deleted_nonpaper_types(self):
        top = [paper('PAPER001'), paper('DELETED1', deleted=1), paper('BOOK0001', 'book'),
               paper('NOTE0001', 'note'), paper('ATTACH00', 'attachment')]
        children = {'PAPER001': [attachment('ATTACH01', contentType='text/html'),
                                 attachment('ATTACH02', deleted=1)]}
        with api_fixture(top, children) as (base, calls):
            records = export.export_library(self.out, base)
        self.assertEqual([r['status'] for r in records], ['no_pdf'])
        self.assertEqual((self.out / 'ids.tsv').read_bytes(), b'')
        self.assertFalse((self.out / 'sources').exists())
        self.assertEqual(len(calls), 2)

    def test_paper_and_attachment_failures_are_isolated(self):
        top = [paper(f'PAPER00{i}') for i in range(1, 6)]
        children = {f'PAPER00{i}': [attachment(f'ATTACH0{i}')] for i in range(1, 6)}
        children['PAPER001'] = 500
        files = {'ATTACH02': (200, b'HTML, not PDF'), 'ATTACH03': (404, b'no file'),
                 'ATTACH04': (302, (self.root / 'missing.pdf').as_uri()), 'ATTACH05': (200, PDF)}
        with api_fixture(top, children, files) as (base, _):
            records = export.export_library(self.out, base)
        self.assertEqual([r['status'] for r in records], ['error'] * 4 + ['ready'])
        self.assertTrue(all(r.get('error') for r in records[:4]))
        self.assertEqual(len((self.out / 'ids.tsv').read_text(encoding='utf-8').splitlines()), 1)

    def test_network_file_and_http_redirects_never_followed(self):
        locations = ['file://server/share/a.pdf', 'file://localhost/share/a.pdf',
                     'file:////server/share/a.pdf', 'file:///%5C%5Cserver/share/a.pdf',
                     'https://example.com/private.pdf', 'http://127.0.0.1:1/private.pdf',
                     (self.root / 'valid.pdf').as_uri() + '?query=x']
        for index, location in enumerate(locations):
            with self.subTest(location=location):
                with api_fixture([paper('PAPER001')], {'PAPER001': [attachment('ATTACH01')]},
                                 {'ATTACH01': (302, location)}) as (base, calls):
                    records = export.export_library(self.root / f'out-{index}', base)
                self.assertEqual(records[0]['status'], 'error')
                self.assertNotIn('path', records[0])
                self.assertEqual(len(calls), 3)

    def test_invalid_base_urls_fail_before_network_or_writes(self):
        bad = ['https://example.com/api/users/0', 'http://127.0.0.1.evil.test',
               'http://user:secret@127.0.0.1:23119', 'file:///tmp',
               'http://192.168.0.1:23119', 'http://127.0.0.1:23119/other',
               'http://127.0.0.1:23119?token=x', 'http://127.0.0.1#fragment',
               'http://127.0.0.1:65536', 'http://127.0.0.1\\@example.com',
               'http://127.0.0.1\n']
        with patch.object(export.LocalAPI, 'get', side_effect=AssertionError('network')):
            for url in bad:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    export.export_library(self.out, url)
        self.assertFalse(self.out.exists())
        self.assertEqual(export.validate_base_url('http://localhost:23119'), export.DEFAULT_BASE_URL)
        self.assertEqual(export.validate_base_url('http://[::1]:23119/'),
                         'http://[::1]:23119/api/users/0')

    def test_pagination_selection_and_missing_keys(self):
        top = [paper('PAPER001'), paper('PAPER002'), paper('PAPER003')]
        with api_fixture(top, page_size=1) as (base, calls):
            records = export.export_library(self.out, base, ['PAPER003', 'MISSING1'])
        self.assertEqual([r['item_key'] for r in records], ['PAPER003', 'MISSING1'])
        self.assertEqual([r['status'] for r in records], ['no_pdf', 'error'])
        top_calls = [c for c in calls if '/items/top?' in c[1]]
        self.assertEqual([parse_qs(urlsplit(c[1]).query)['start'][0] for c in top_calls], ['0', '1', '2'])
        self.assertFalse(any('/PAPER001/children' in c[1] for c in calls))
        with self.assertRaises(ValueError):
            export.export_library(self.out, items=['../../escape'])

    def test_multi_attachment_failure_does_not_hide_ready_attachment(self):
        with api_fixture([paper('PAPER001')], {'PAPER001': [attachment('ATTACH02'), attachment('ATTACH01')]},
                         {'ATTACH01': (404, b'missing'), 'ATTACH02': (200, PDF)}) as (base, _):
            records = export.export_library(self.out, base)
        self.assertEqual([r['status'] for r in records], ['error', 'ready'])
        self.assertEqual(records[1]['id'], 'PAPER001-ATTACH02')
        self.assertIn('\tPAPER001-ATTACH02\n', (self.out / 'ids.tsv').read_text(encoding='utf-8'))

    def test_interrupted_http_body_isolated_and_next_paper_exported(self):
        top = [paper('PAPER001'), paper('PAPER002')]
        children = {'PAPER001': [attachment('ATTACH01')], 'PAPER002': [attachment('ATTACH02')]}
        real_pdf = export.LocalAPI.pdf

        def interrupted(api, key):
            if key == 'ATTACH01':
                raise IncompleteRead(b'%PDF-', 100)
            return real_pdf(api, key)

        with api_fixture(top, children, {'ATTACH02': (200, PDF)}) as (base, _):
            with patch.object(export.LocalAPI, 'pdf', interrupted):
                records = export.export_library(self.out, base)
        self.assertEqual([r['status'] for r in records], ['error', 'ready'])

    def test_different_pdf_bytes_are_not_deduplicated(self):
        top = [paper('PAPER001'), paper('PAPER002')]
        children = {'PAPER001': [attachment('ATTACH01')], 'PAPER002': [attachment('ATTACH02')]}
        with api_fixture(top, children, {'ATTACH01': (200, PDF),
                                        'ATTACH02': (200, PDF + b'changed')}) as (base, _):
            records = export.export_library(self.out, base)
        self.assertEqual([r['status'] for r in records], ['ready', 'ready'])
        self.assertNotEqual(records[0]['sha256'], records[1]['sha256'])
        self.assertEqual(len((self.out / 'ids.tsv').read_text(encoding='utf-8').splitlines()), 2)

    def test_changed_existing_pdf_is_preserved_and_reported(self):
        dest = self.out / 'sources/PAPER001-ATTACH01.pdf'
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b'%PDF-user-edited')
        with api_fixture([paper('PAPER001')], {'PAPER001': [attachment('ATTACH01')]},
                         {'ATTACH01': (200, PDF)}) as (base, _):
            records = export.export_library(self.out, base)
        self.assertEqual(records[0]['status'], 'error')
        self.assertEqual(dest.read_bytes(), b'%PDF-user-edited')
        self.assertEqual((self.out / 'ids.tsv').read_bytes(), b'')

    def test_edited_manifests_are_never_overwritten(self):
        for name in ('library.json', 'ids.tsv'):
            with self.subTest(name=name):
                out = self.root / name.replace('.', '-')
                with api_fixture([paper('PAPER001')], {'PAPER001': [attachment('ATTACH01')]},
                                 {'ATTACH01': (200, PDF)}) as (base, _):
                    export.export_library(out, base)
                    (out / name).write_bytes(b'user edits\n')
                    before = {p: p.read_bytes() for p in out.rglob('*') if p.is_file()}
                    with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
                        export.export_library(out, base)
                self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_atomic_write_failure_leaves_no_partial_file(self):
        target = self.root / 'export.pdf'
        with patch.object(export.os, 'link', side_effect=OSError('injected publish failure')):
            with self.assertRaises(OSError):
                export._atomic_write(target, PDF)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('.zotero-*.tmp')), [])

    def test_atomic_write_race_preserves_concurrently_created_file(self):
        target = self.root / 'export.pdf'

        def race(source, dest):
            Path(dest).write_bytes(b'user bytes')
            raise FileExistsError()

        with patch.object(export.os, 'link', side_effect=race):
            with self.assertRaises(ValueError):
                export._atomic_write(target, PDF)
        self.assertEqual(target.read_bytes(), b'user bytes')
        self.assertEqual(list(self.root.glob('.zotero-*.tmp')), [])

    def test_cli_standalone_success_and_no_pdf_exit_status(self):
        for available in (True, False):
            with self.subTest(available=available):
                children = {'PAPER001': [attachment('ATTACH01')]} if available else {}
                with api_fixture([paper('PAPER001')], children, {'ATTACH01': (200, PDF)}) as (base, calls):
                    result = subprocess.run([sys.executable, str(ROOT / 'scripts/zotero_export.py'),
                        '--out', str(self.root / str(available)), '--base-url', base,
                        '--items', 'PAPER001'], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0 if available else 1, result.stderr)
                counts = json.loads(result.stdout)
                self.assertEqual(counts['ready'], int(available))
                self.assertEqual(counts['no_pdf'], int(not available))
                self.assertTrue(all(call[0] == 'GET' for call in calls))
                for _, _, headers in calls:
                    self.assertFalse(any(k.lower() in ('authorization', 'cookie', 'proxy-authorization') for k in headers))

    def test_failed_top_listing_writes_nothing(self):
        with api_fixture(500) as (base, _), self.assertRaises(ValueError):
            export.export_library(self.out, base)
        self.assertFalse(self.out.exists())

    def test_symlink_export_destination_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            self.out.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation requires permissions on this platform')
        with patch.object(export.LocalAPI, 'get', side_effect=AssertionError('network')):
            with self.assertRaises(ValueError):
                export.export_library(self.out)
        self.assertEqual(list(outside.iterdir()), [])


class ExportRefreshTests(unittest.TestCase):
    def test_explicit_library_routes_retained_and_identity_mismatch_rejected(self):
        for route in ('/api/users/123', '/api/groups/42'):
            url = 'http://127.0.0.1:23119' + route
            self.assertEqual(export.validate_base_url(url), url)
        api = export.LocalAPI('http://127.0.0.1:23119/api/groups/42')
        with self.assertRaisesRegex(ValueError, 'type mismatch'):
            export.library_identity(api, paper('PAPER001'))
        item = paper('PAPER001')
        item['library'] = {'type': 'group', 'id': 43}
        with self.assertRaisesRegex(ValueError, 'ID mismatch'):
            export.library_identity(api, item)
        item['library']['id'] = 42
        self.assertEqual(export.library_identity(api, item), {'type': 'group', 'id': 42})

    def test_refresh_managed_manifest_and_preserve_manual_edits(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'export'
            item = paper('PAPER001')
            with api_fixture([item]) as (base, _):
                export.export_library(out, base)
                item['data']['title'] = 'Updated remote title'
                with self.assertRaises(ValueError):
                    export.export_library(out, base)
                refreshed = export.export_library(out, base, refresh=True)
                self.assertEqual(refreshed[0]['title'], 'Updated remote title')
                self.assertEqual(json.loads((out / 'library.json').read_text(encoding='utf-8'))[0]['title'], 'Updated remote title')
                (out / 'ids.tsv').write_text('Human edited\n', encoding='utf-8')
                before = (out / 'library.json').read_bytes()
                with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
                    export.export_library(out, base, refresh=True)
                self.assertEqual((out / 'library.json').read_bytes(), before)
                self.assertEqual((out / 'ids.tsv').read_text(encoding='utf-8'), 'Human edited\n')

    def test_children_and_annotations_paginate_without_following_next_link(self):
        import zotero_sync
        children = [attachment('ATTACH01'), attachment('ATTACH02')]
        annotations = [{'key': f'ANNOT00{i}', 'data': {'key': f'ANNOT00{i}', 'itemType': 'annotation'}} for i in range(1, 4)]
        with api_fixture([], {'PAPER001': children, 'ATTACH01': annotations}, page_size=1) as (base, calls):
            client = zotero_sync.ZoteroSyncClient(base)
            self.assertEqual(len(client.get_children('PAPER001')), 2)
            self.assertEqual(len(client.get_children('ATTACH01')), 3)
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(c[0] == 'GET' for c in calls))

    def test_failure_json_and_repeating_pagination_rejected(self):
        from io import BytesIO
        class Response(BytesIO):
            headers = {'Total-Results': '200'}
        api = export.LocalAPI()
        with patch.object(api, 'get', return_value=Response(json.dumps({'failed': {'0': 'bad'}}).encode())):
            with self.assertRaisesRegex(ValueError, 'item list'):
                list(api.items('/items/top'))
        with patch.object(api, 'get', side_effect=lambda *args: Response(json.dumps([paper('PAPER001')]).encode())):
            with self.assertRaisesRegex(ValueError, 'Repeated item'):
                list(api.items('/items/top'))

    def test_owned_report_tampering_and_atomic_replace_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'report.json'
            export.write_owned(path, {'value': 1}, 'owner')
            original = path.read_bytes()
            with patch.object(export.os, 'replace', side_effect=OSError('publish failed')):
                with self.assertRaises(OSError):
                    export.write_owned(path, {'value': 2}, 'owner')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(export.read_owned(path, 'owner'), {'value': 1})
            with self.assertRaisesRegex(ValueError, 'Unowned'):
                export.read_owned(path, 'someone else')
            data = json.loads(path.read_text(encoding='utf-8'))
            data['payload']['value'] = 'human edit'
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'manually edited'):
                export.write_owned(path, {'value': 2}, 'owner')


if __name__ == '__main__':
    unittest.main()
