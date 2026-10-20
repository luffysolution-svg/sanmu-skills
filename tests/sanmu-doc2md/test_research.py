"""Research entry regressions: metadata evidence, exact matching and safe rebuilds."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import research_metadata as metadata
import build_research_document as research
import fitz
import yaml


class ResearchMetadataTests(unittest.TestCase):
    def test_yaml_round_trip_preserves_full_authors_and_original_abstract(self):
        supplied = {'title': '材料: "A" & B', 'authors': ['A: B', '组织 #1', 'M. O\'Neill'],
                    'date': '2025-03', 'abstract': 'Original: “abstract”\n\n---\nUnicode 中文\n',
                    'zotero_item_key': 'INTERNAL', 'zotero_pdf_uri': 'never', 'empty': ''}
        front = metadata.render_frontmatter(supplied)
        parsed = yaml.safe_load(front.removeprefix('---\n').removesuffix('---\n\n'))
        self.assertEqual(parsed['abstract'], supplied['abstract'])
        self.assertEqual(parsed['authors'], supplied['authors'])
        self.assertIsInstance(parsed['date'], str)
        self.assertNotIn('zotero_item_key', front)
        self.assertNotIn('zotero_pdf_uri', front)
        self.assertNotIn('empty', parsed)

    def test_zotero_export_full_creators_in_order_and_group_uri(self):
        payload = {'item_key': 'ABCD2345', 'metadata': {'library': {'type': 'group', 'id': 99}, 'data': {
            'itemType': 'journalArticle', 'title': 'Example paper title', 'abstractNote': 'Exact abstract',
            'publicationTitle': 'Journal', 'DOI': 'https://doi.org/10.1234/EXAMPLE',
            'date': '2025', 'tags': [{'tag': 'My reading queue', 'type': 0}],
            'creators': [{'firstName': 'Ada', 'lastName': 'Lovelace', 'creatorType': 'author'},
                                       {'name': 'Research Consortium', 'creatorType': 'author'},
                                       {'lastName': 'Editor', 'creatorType': 'editor'}]}}}
        result = metadata.metadata_from_export(payload)
        self.assertEqual(result['authors'], ['Ada Lovelace', 'Research Consortium'])
        self.assertEqual(result['zotero_item_uri'], 'zotero://select/groups/99/items/ABCD2345')
        self.assertEqual(result['publication_title'], 'Journal')
        self.assertEqual(result['doi'], '10.1234/example')
        self.assertEqual(result['date'], '2025')
        self.assertNotIn('keywords', result)  # Library labels are not article keywords.

    def test_unscoped_key_does_not_guess_library_uri(self):
        self.assertNotIn('zotero_item_uri', metadata.metadata_from_export(
            {'key': 'ABCD2345', 'itemType': 'journalArticle', 'title': 'Paper title'}))

    def test_truncated_authors_rejected(self):
        for author in ['Ada et al.', '张三 等']:
            with self.subTest(author=author), self.assertRaises(ValueError):
                metadata.normalize_metadata({'authors': [author]})
        with self.assertRaises(ValueError):
            metadata.normalize_metadata({'authors': 'Ada, Bob'})

    def test_crossref_rejects_doi_or_near_title_mismatch(self):
        message = {'DOI': '10.1234/paper', 'title': ['Materials discovery with models'],
                   'author': [{'given': 'Ada', 'family': 'Lovelace'}]}
        self.assertEqual(metadata.match_crossref(message, '10.1234/paper',
                                                'Materials discovery with models')['authors'], ['Ada Lovelace'])
        for doi, title in [('10.1234/other', 'Materials discovery with models'),
                           ('10.1234/paper', 'Materials discovery without models')]:
            with self.subTest(doi=doi, title=title), self.assertRaises(ValueError):
                metadata.match_crossref(message, doi, title)

    def test_crossref_cache_gate_rechecks_title_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doi = '10.1234/paper'
            cache = root / (hashlib.sha256(doi.encode()).hexdigest() + '.json')
            cache.write_text(json.dumps({'DOI': doi, 'title': ['A completely different paper']}), encoding='utf-8')
            with patch.object(metadata.urllib.request, 'urlopen') as network:
                with self.assertRaises(ValueError):
                    metadata.lookup_crossref(doi, 'Research about materials discovery', root)
                network.assert_not_called()

    def test_library_selection_needs_id_for_duplicate_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'export.json'
            path.write_text(json.dumps([{'item_key': 'ABCD2345', 'sha256': 'same'},
                                        {'item_key': 'BCDE3456', 'sha256': 'same'}]), encoding='utf-8')
            with self.assertRaises(ValueError):
                metadata.load_export(path, None, 'same')
            self.assertEqual(metadata.load_export(path, 'BCDE3456', 'same')['item_key'], 'BCDE3456')
            with self.assertRaises(ValueError):
                metadata.load_export(path, 'BCDE3456', 'other')

    def test_multi_attachment_export_ids_select_exact_record_and_preserve_item_uri(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'library.json'
            records = []
            for attachment, digest in [('BCDE3456', 'first-hash'), ('CDEF4567', 'second-hash')]:
                records.append({'id': f'ABCD2345-{attachment}', 'item_key': 'ABCD2345',
                    'attachment_key': attachment, 'sha256': digest, 'metadata': {
                        'key': 'ABCD2345', 'library': {'type': 'user'}, 'data': {
                            'itemType': 'journalArticle', 'title': 'Research about material discovery'}}})
            path.write_text(json.dumps(records), encoding='utf-8')
            for record in records:
                with self.subTest(identity=record['id']):
                    selected = metadata.load_export(path, record['id'], record['sha256'])
                    self.assertEqual(metadata.export_identity(selected), record['id'])
                    self.assertEqual(metadata.metadata_from_export(selected)['zotero_item_uri'],
                                     'zotero://select/library/items/ABCD2345')
            with self.assertRaises(ValueError):
                metadata.load_export(path, 'ABCD2345', 'first-hash')
            with self.assertRaises(ValueError):
                metadata.load_export(path, records[0]['id'], 'second-hash')
            self.assertIsNone(metadata.export_identity({'title': 'Canonical metadata', 'authors': ['Author']}))

    def test_pdf_properties_are_evidence_not_complete_authors_or_publication_date(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.pdf'
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((50, 50), 'Research about material discovery\ndoi:10.1234/paper\nReferences\n10.1234/cited')
                pdf.set_metadata({'title': 'Research about material discovery', 'author': 'First Author',
                                  'subject': 'doi:10.1234/paper', 'creationDate': 'D:20250101000000'})
                pdf.save(path)
            preflight = metadata.inspect_pdf(path)
            fields, provenance = metadata.collect_metadata(preflight)
            self.assertEqual(fields, {'title': 'Research about material discovery', 'doi': '10.1234/paper'})
            self.assertEqual(preflight['page_count'], 1)
            self.assertEqual(preflight['sha256'], metadata.sha256_file(path))
            self.assertEqual(preflight['candidates']['first_page_dois'], ['10.1234/paper'])
            self.assertEqual(provenance['missing_fields'], ['authors', 'abstract'])
            self.assertNotIn('date', fields)
            self.assertNotIn('zotero_item_uri', fields)

    def test_mismatched_export_fails_instead_of_mixing_versions(self):
        preflight = {'sha256': 'hash', 'fields': {'title': 'Research material discovery', 'doi': '10.1234/current'},
                     'first_page_text': 'Research material discovery', 'review': []}
        with self.assertRaises(ValueError):
            metadata.collect_metadata(preflight, {'title': 'Research material discovery', 'doi': '10.1234/other'})
        with self.assertRaises(ValueError):
            metadata.collect_metadata(preflight, {'title': 'Research about another subject'})

    def test_export_hash_cannot_override_wrong_title_or_corroborated_doi(self):
        preflight = {'sha256': 'hash', 'fields': {}, 'review': [],
                     'first_page_text': 'Research about material discovery\nOriginal article text content',
                     'candidates': {'first_page_dois': ['10.1234/current']}}
        with self.assertRaises(ValueError):
            metadata.collect_metadata(preflight, {'sha256': 'hash', 'metadata': {
                'title': 'A completely unrelated publication', 'doi': '10.1234/current'}})
        preflight['fields']['doi'] = '10.1234/current'  # Property + first-page corroboration.
        with self.assertRaises(ValueError):
            metadata.collect_metadata(preflight, {'sha256': 'hash', 'metadata': {
                'title': 'Research about material discovery', 'doi': '10.1234/other'}})

    def test_short_chinese_and_english_titles_match_with_weak_evidence_review(self):
        for title in ['材料研究', 'AI science']:
            with self.subTest(title=title):
                preflight = {'sha256': 'hash', 'fields': {}, 'review': [],
                    'first_page_text': title + '\nOriginal article text with enough content for the mismatch gate',
                    'candidates': {}}
                fields, provenance = metadata.collect_metadata(preflight, {'sha256': 'hash', 'metadata': {
                    'key': 'ABCD2345', 'library': {'type': 'user'}, 'data': {
                        'itemType': 'journalArticle', 'title': title}}})
                self.assertEqual(fields['title'], title)
                self.assertEqual(fields['zotero_item_uri'], 'zotero://select/library/items/ABCD2345')
                self.assertEqual(provenance['export_pdf_association'], 'corroborated')
                self.assertTrue(any('weak' in row for row in provenance['review']))
                with self.assertRaises(ValueError):
                    metadata.collect_metadata(preflight, {'title': 'Different unrelated publication'})

    def test_short_pdf_property_title_is_corroborated_by_first_page(self):
        with tempfile.TemporaryDirectory() as temp:
            for index, title in enumerate(['材料研究', 'AI science']):
                with self.subTest(title=title):
                    path = Path(temp) / f'short-{index}.pdf'
                    with fitz.open() as pdf:
                        page = pdf.new_page()
                        page.insert_text((50, 50), title, fontname='china-s' if index == 0 else 'helv')
                        pdf.set_metadata({'title': title})
                        pdf.save(path)
                    preflight = metadata.inspect_pdf(path)
                    self.assertEqual(preflight['fields']['title'], title)
                    self.assertTrue(any('weak' in row for row in preflight['review']))

    def test_unique_first_page_cited_doi_does_not_override_supplied_article_doi(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.pdf'
            title = 'Research about material discovery'
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((50, 50), title + '\nEarlier work doi:10.1234/cited motivated this study.')
                pdf.set_metadata({'title': title})
                pdf.save(path)
            preflight = metadata.inspect_pdf(path)
            self.assertEqual(preflight['candidates']['first_page_dois'], ['10.1234/cited'])
            self.assertNotIn('doi', preflight['fields'])
            fields, provenance = metadata.collect_metadata(preflight, {
                'sha256': preflight['sha256'], 'metadata': {'title': title, 'doi': '10.1234/current'}})
            self.assertEqual(fields['doi'], '10.1234/current')
            self.assertEqual(provenance['conflicts'][0]['status'], 'unconfirmed_candidate_conflict')
            self.assertTrue(any('body citations' in row for row in provenance['review']))

    def test_unverified_scan_export_keeps_review_and_omits_zotero_link(self):
        preflight = {'sha256': 'hash', 'fields': {}, 'review': [], 'first_page_text': '', 'candidates': {}}
        fields, provenance = metadata.collect_metadata(preflight, {'sha256': 'hash', 'metadata': {
            'key': 'ABCD2345', 'library': {'type': 'user'}, 'data': {
                'itemType': 'journalArticle', 'title': 'Supplied publication title'}}})
        self.assertNotIn('zotero_item_uri', fields)
        self.assertEqual(provenance['export_pdf_association'], 'needs_review')
        self.assertTrue(any('hash confirms attachment bytes only' in row for row in provenance['review']))

    def test_scan_does_not_invent_publication_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.pdf'
            with fitz.open() as pdf:
                pdf.new_page()
                pdf.set_metadata({'title': 'Unverified title in properties', 'author': 'Somebody'})
                pdf.save(path)
            preflight = metadata.inspect_pdf(path)
            fields, provenance = metadata.collect_metadata(preflight)
            self.assertEqual(fields, {})
            self.assertEqual(preflight['text_layer'], 'absent')
            self.assertEqual(provenance['missing_fields'], ['title', 'authors', 'abstract'])


class ResearchEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='research-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.raw = self.root / 'ocr' / 'raw'
        self.item = self.raw / 'ABCD2345'
        self.item.mkdir(parents=True)
        self.pdf = self.root / '论文 source.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 50), 'Research about material discovery\ndoi:10.1234/paper')
            pdf.set_metadata({'title': 'Research about material discovery', 'subject': 'doi:10.1234/paper'})
            pdf.save(self.pdf)
        self.content = self.item / 'result_content_list.json'
        self.content.write_text(json.dumps([
            {'type': 'text', 'text': 'First source column', 'page_idx': 0, 'bbox': [10, 700, 400, 750]},
            {'type': 'text', 'text': 'Second source column', 'page_idx': 0, 'bbox': [500, 100, 950, 150]},
        ]), encoding='utf-8')
        self.out = self.root / '交付 output'
        self.state = self.root / 'state'
        self.export = self.root / 'metadata.json'
        self.export.write_text(json.dumps({'title': 'Research about material discovery',
            'authors': ['First Author', 'Research Group'], 'abstract': 'Original abstract: exact text\n第二行',
            'doi': '10.1234/paper', 'zotero_item_key': 'never', 'zotero_pdf_uri': 'never'}), encoding='utf-8')

    def command(self, *extra):
        return [sys.executable, str(SCRIPTS / 'build_research_document.py'), '--pdf', str(self.pdf),
                '--raw', str(self.item), '--out', str(self.out), '--state-dir', str(self.state),
                '--metadata', str(self.export), '--id', 'ABCD2345', *extra]

    def run_cli(self, *extra, success=True):
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(self.command(*extra), capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60, env=env)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_raw_root_item_and_workspace_select_same_result(self):
        for raw in (self.item, self.raw, self.raw.parent):
            with self.subTest(raw=raw):
                root, part, folder = research.resolve_raw(raw, 'ABCD2345')
                self.assertEqual(root, self.raw)
                self.assertEqual(part, 'ABCD2345')
                self.assertEqual(folder, self.item)
        other = self.raw / 'BCDE3456'
        other.mkdir()
        (other / 'other_content_list.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            research.resolve_raw(self.raw, None)

    def test_real_cli_frontmatter_order_cache_and_no_formula_gate(self):
        self.run_cli()
        expected_file = self.out / 'Author 等 - Research about material discovery.md'
        self.assertTrue(expected_file.is_file(), f'Expected {expected_file} to exist')
        text = expected_file.read_text(encoding='utf-8')
        front = yaml.safe_load(text.split('---\n', 2)[1])
        self.assertEqual(front['authors'], ['First Author', 'Research Group'])
        self.assertEqual(front['abstract'], 'Original abstract: exact text\n第二行')
        self.assertNotIn('zotero_item_key', front)
        self.assertNotIn('zotero_pdf_uri', front)
        self.assertLess(text.index('First source column'), text.index('Second source column'))
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['automatic_checks'], 'pass')
        self.assertEqual(report['formula_count'], 0)
        self.assertIn('--allow-empty', report['commands'][-1]['command'])
        self.assertEqual(report['acceptance'], 'draft_pending_source_and_browser_review')
        self.assertFalse((self.out / 'build-report.json').exists())
        self.run_cli()
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['cache_hit'])
        self.assertEqual(len(report['commands']), 3)

    def test_cli_multi_attachment_id_and_canonical_raw_identity(self):
        identity = 'ABCD2345-BCDE3456'
        multi_item = self.raw / identity
        self.item.rename(multi_item)
        self.item = multi_item
        self.export.write_text(json.dumps({'id': identity, 'item_key': 'ABCD2345',
            'attachment_key': 'BCDE3456', 'sha256': metadata.sha256_file(self.pdf), 'metadata': {
                'key': 'ABCD2345', 'library': {'type': 'user'}, 'data': {
                    'itemType': 'journalArticle', 'title': 'Research about material discovery',
                    'authors': ['Author'], 'abstractNote': 'Original abstract'}}}), encoding='utf-8')
        command = self.command()
        command[command.index('--id') + 1] = identity
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['id'], identity)
        target = self.out / 'Author - Research about material discovery.md'
        self.assertTrue(target.is_file(), f'Expected {target} to exist')
        front = yaml.safe_load(target.read_text(encoding='utf-8').split('---\n', 2)[1])
        self.assertEqual(front['zotero_item_uri'], 'zotero://select/library/items/ABCD2345')
        # Canonical metadata without any ID leaves build identity to the raw dir.
        self.export.write_text(json.dumps({'title': 'Research about material discovery'}), encoding='utf-8')
        command = self.command()
        del command[-2:]  # No --id; the single raw folder supplies identity.
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['id'], identity)
        self.assertTrue((self.out / 'Research about material discovery.md').is_file())

    def test_owned_user_edit_refused_including_frontmatter_before_hash_finalization(self):
        self.run_cli()
        target = self.out / 'Author 等 - Research about material discovery.md'
        ledger = json.loads((self.state / '.build-ownership.json').read_text(encoding='utf-8'))
        self.assertEqual(ledger['files'][target.name], metadata.sha256_file(target))
        changed = target.read_text(encoding='utf-8').replace('First Author', 'My correction')
        target.write_text(changed, encoding='utf-8')
        result = self.run_cli(success=False)
        output_text = result.stdout + result.stderr
        self.assertTrue('人工修改' in output_text or r'\u4eba\u5de5\u4fee\u6539' in output_text)
        self.assertEqual(target.read_text(encoding='utf-8'), changed)

    def test_unowned_file_refused(self):
        self.out.mkdir()
        target = self.out / 'Author 等 - Research about material discovery.md'
        target.write_text('User original note', encoding='utf-8')
        self.run_cli(success=False)
        self.assertEqual(target.read_text(encoding='utf-8'), 'User original note')

    def test_repairs_forwarded_and_raw_left_untouched(self):
        original = self.content.read_bytes()
        repairs = self.root / 'repairs.json'
        repairs.write_text(json.dumps([{'page': 1, 'bbox': [10, 700, 400, 750], 'field': 'text',
            'old': 'First source column', 'new': 'Verified source correction', 'reason': 'Checked source page'}]), encoding='utf-8')
        self.run_cli('--repairs', str(repairs))
        target = self.out / 'Author 等 - Research about material discovery.md'
        self.assertIn('Verified source correction', target.read_text(encoding='utf-8'))
        self.assertEqual(self.content.read_bytes(), original)

    def test_validation_failure_recorded_and_not_claimed_pass(self):
        self.content.write_text(json.dumps([{'type': 'equation', 'text': r'\unknowncommand{x}',
            'page_idx': 0, 'bbox': [100, 200, 800, 250]}]), encoding='utf-8')
        self.run_cli(success=False)
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['automatic_checks'], 'fail')
        self.assertTrue(any(row['returncode'] for row in report['commands']))

    def test_zotero_naming_basename_formatting(self):
        # 1. Multi-author Zotero export record
        zotero_item = {
            'metadata': {
                'data': {
                    'title': 'Inverse Design of Solid-State Materials: via a Continuous Representation and Benchmarks',
                    'date': '2019-11-06',
                    'creators': [
                        {'firstName': 'Juhwan', 'lastName': 'Noh', 'creatorType': 'author'},
                        {'firstName': 'Jaehoon', 'lastName': 'Kim', 'creatorType': 'author'}
                    ]
                }
            }
        }
        name = metadata.format_zotero_basename(zotero_item)
        self.assertEqual(name, 'Noh 等 - 2019 - Inverse Design of Solid-State Materials - via a Continuous Representation and Benchmarks')

        # 2. Canonical single author & Chinese author
        single = {'authors': ['Albert Einstein'], 'date': '1905', 'title': 'Zur Elektrodynamik'}
        self.assertEqual(metadata.format_zotero_basename(single), 'Einstein - 1905 - Zur Elektrodynamik')

        chinese = {'authors': ['张三', '李四'], 'date': '2024-05', 'title': '材料基因组模型'}
        self.assertEqual(metadata.format_zotero_basename(chinese), '张三 等 - 2024 - 材料基因组模型')

        # 3. Truncation to 100 characters on title
        long_title = {'authors': ['Author'], 'date': '2020', 'title': 'A' * 120}
        formatted = metadata.format_zotero_basename(long_title)
        self.assertEqual(formatted, f'Author - 2020 - {"A" * 100}')

        # 4. Independent PDF / Word document: extract from structured filename
        stem_en = 'Vaswani et al - 2017 - Attention Is All You Need'
        self.assertEqual(metadata.format_zotero_basename(None, stem_en), stem_en)

        stem_zh = '李四 等 - 2023 - 凝聚态计算物理导论'
        self.assertEqual(metadata.format_zotero_basename({}, stem_zh), '李四 等 - 2023 - 凝聚态计算物理导论')

        stem_single = 'Goodfellow - 2016 - Deep Learning'
        self.assertEqual(metadata.format_zotero_basename(None, stem_single), 'Goodfellow - 2016 - Deep Learning')

        # 5. Independent document: extract from extra_candidates (properties from PDF/Word)
        candidates = {
            'author': 'Alice Smith; Bob Jones',
            'creationDate': 'D:20240315102030',
            'title': 'AI in Materials Science'
        }
        self.assertEqual(metadata.format_zotero_basename({}, 'paper_export', extra_candidates=candidates),
                         'paper_export')

    def test_supplemental_title_variant_matching(self):
        main_title = 'Inverse Design of Solid-State Materials via a Continuous Representation'
        self.assertTrue(metadata.is_supplemental_title_variant(
            main_title,
            'Inverse Design of Solid-State Materials via a Continuous Representation - Supplemental Information'))
        self.assertTrue(metadata.is_supplemental_title_variant(
            main_title,
            'Inverse Design of Solid-State Materials - Supplemental Information'))
        self.assertTrue(metadata.is_supplemental_title_variant(
            main_title,
            'Inverse Design of Solid-State Materials: Supporting Information'))
        self.assertFalse(metadata.is_supplemental_title_variant(
            main_title,
            'Crystal Graph Convolutional Neural Networks for Materials'))

    def test_collect_metadata_accepts_supplemental_variant_without_differ_error(self):
        preflight = {
            'fields': {'title': 'Inverse Design of Solid-State Materials via a Continuous Representation'},
            'pdf_properties': {'title': 'Inverse Design of Solid-State Materials via a Continuous Representation'},
            'first_page_text': 'Supplemental Information\nInverse Design of Solid-State Materials via a Continuous Representation',
            'sha256': 'dummy_hash',
            'review': []
        }
        payload = {
            'sha256': 'dummy_hash',
            'metadata': {
                'title': 'Inverse Design of Solid-State Materials via a Continuous Representation - Supplemental Information',
                'authors': ['Juhwan Noh', 'Yousung Jung']
            }
        }
        fields, provenance = metadata.collect_metadata(preflight, payload)
        self.assertIn('Supplemental Information', fields['title'])
        self.assertTrue(any('supplemental/supporting variant' in r for r in provenance['review']))


        # 6. Independent plain title fallback without author or year
        self.assertEqual(metadata.format_zotero_basename({}, 'Annual Research Review Report'),
                         'Annual Research Review Report')

    def test_build_cli_supports_zotero_naming_and_explicit_file(self):
        # Build with default academic naming (without needing --naming)
        meta_file = self.root / 'meta.json'
        meta_file.write_text(json.dumps({
            'title': 'Research about material discovery',
            'authors': ['Alice Smith', 'Bob Jones'],
            'date': '2025-02-15'
        }), encoding='utf-8')
        self.run_cli('--metadata', str(meta_file))
        expected_name = 'Smith 等 - 2025 - Research about material discovery.md'
        target = self.out / expected_name
        self.assertTrue(target.is_file(), f'Expected {target} to exist')
        self.assertFalse((self.out / '原文.md').exists())
        nav = (self.out / '00-目录.md').read_text(encoding='utf-8')
        import urllib.parse
        self.assertIn(expected_name, urllib.parse.unquote(nav))

    def test_build_cli_independent_pdf_without_metadata(self):
        # Independent PDF with no metadata provided: runs with default academic naming
        cmd = [sys.executable, str(SCRIPTS / 'build_research_document.py'),
               '--pdf', str(self.pdf), '--raw', str(self.item),
               '--out', str(self.out), '--state-dir', str(self.state),
               '--id', 'ABCD2345']
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        # self.pdf has metadata title 'Research about material discovery'
        expected = self.out / 'Research about material discovery.md'
        self.assertTrue(expected.is_file(), f'Expected {expected} to exist')
        self.assertFalse((self.out / '原文.md').exists())

    def test_supplemental_references_linking_and_banners(self):
        sample_text = (
            '# Results\n\n'
            'As shown in Figure S1 and Fig. S2, band gap decreases.\n'
            'Table S1 gives lattice parameters. 见图 S1.\n'
            '```text\n'
            'Figure S1 is untouched in code block\n'
            '```\n'
            'Already linked: [[Existing#Figure S1|Figure S1]].\n'
        )
        linked = metadata.link_supplemental_references(sample_text, 'Noh - 2019 - SI')
        self.assertIn('[[Noh - 2019 - SI#Figure S1|Figure S1]]', linked)
        self.assertIn('[[Noh - 2019 - SI#Figure S2|Fig. S2]]', linked)
        self.assertIn('[[Noh - 2019 - SI#Table S1|Table S1]]', linked)
        self.assertIn('[[Noh - 2019 - SI#Figure S1|图 S1]]', linked)
        self.assertIn('Figure S1 is untouched in code block', linked)
        self.assertIn('[[Existing#Figure S1|Figure S1]]', linked)

        # Banner injection
        si_text = '---\ntitle: "SI"\n---\n# Supplemental Information\nDetails here.\n'
        bannered = metadata.inject_supplement_banner(si_text, 'Noh - 2019 - Main')
        self.assertIn('> [!info] 关联主文', bannered)
        self.assertIn('本文档为 [[Noh - 2019 - Main]] 的补充材料', bannered)


    def test_main_si_pair_draft_then_validated_rebuild_and_hashes(self):
        main_text = 'Compare Fig. S1 and Table S1. `Fig. S1` stays code.'
        self.content.write_text(json.dumps([{'type': 'text', 'text': main_text,
            'page_idx': 0, 'bbox': [100, 300, 900, 400]}]), encoding='utf-8')
        self.run_cli('--file', 'Main', '--si-doc', 'SI', success=False)
        target = self.out / 'Main.md'
        self.assertIn('[[SI#Figure S1|Fig. S1]]', target.read_text(encoding='utf-8'))
        si_out, si_state = self.root / 'si-output', self.root / 'si-state'
        self.content.write_text(json.dumps([
            {'type': 'image', 'image_caption': 'Fig. S1. Results', 'page_idx': 0, 'bbox': [100, 300, 450, 600]},
            {'type': 'table', 'table_caption': 'Table S1. Values', 'table_body': '<table><tr><td>Value</td></tr></table>',
             'page_idx': 0, 'bbox': [100, 650, 900, 800]},
        ]), encoding='utf-8')
        command = self.command('--file', 'SI', '--supplement-to', 'Main')
        command[command.index('--out') + 1] = str(si_out)
        command[command.index('--state-dir') + 1] = str(si_state)
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        si_text = (si_out / 'SI.md').read_text(encoding='utf-8')
        self.assertIn('## Figure S1', si_text)
        self.assertIn('## Table S1', si_text)
        self.assertIn('[[Main]]', si_text)
        self.content.write_text(json.dumps([{'type': 'text', 'text': main_text,
            'page_idx': 0, 'bbox': [100, 300, 900, 400]}]), encoding='utf-8')
        self.run_cli('--file', 'Main', '--si-doc', 'SI')
        self.run_cli('--file', 'Main', '--si-doc', 'SI')
        report = json.loads((self.state / 'research/research-report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['cache_hit'])
        for folder, state in ((self.out, self.state), (si_out, si_state)):
            ledger = json.loads((state / '.build-ownership.json').read_text(encoding='utf-8'))
            for name, digest in ledger['files'].items():
                self.assertEqual(metadata.sha256_file(folder / name), digest)
        target.write_text(target.read_text(encoding='utf-8') + '\nManual note\n', encoding='utf-8')
        self.run_cli('--file', 'Main', '--si-doc', 'SI', success=False)
        self.assertIn('Manual note', target.read_text(encoding='utf-8'))

    def test_si_rewriting_protects_code_existing_links_and_aliases(self):
        protected = ('`Fig. S1`\n\n    Fig. S1\n\n'
                     '[Fig. S1](existing.md)\n![[SI#Figure S1|Fig. S1]]\n'
                     '<a href="elsewhere">Fig. S1</a>\n\n'
                     '~~~text\nFig. S1\n~~~\n')
        linked = metadata.link_supplemental_references(protected + '\nFig. S1 and 附图1.\n', 'SI')
        self.assertTrue(linked.startswith(protected))
        self.assertIn('[[SI#Figure S1|Fig. S1]] and [[SI#Figure S1|附图1]]', linked)
        self.assertEqual(metadata.link_supplemental_references(linked, 'SI'), linked)

    def test_classic_naming_effective_and_properties_are_not_facts(self):
        self.run_cli('--naming', 'classic')
        self.assertTrue((self.out / 'Research about material discovery.md').is_file())
        self.assertEqual(metadata.format_zotero_basename({}, 'opaque export', extra_candidates={
            'author': 'Unverified Author', 'creationDate': 'D:20250101', 'title': 'Unverified title'}), 'opaque export')


class SupplementalLinkTests(unittest.TestCase):
    def test_notes_link_to_document_while_figures_get_real_anchors(self):
        from research_metadata import link_supplemental_references
        text = ('See Supplementary Note 1, Supplemental Figure 2 and Figure S3. '
                'Existing [[Figure S4]] and `Figure S5` stay untouched.')
        linked = link_supplemental_references(text, 'SI')
        self.assertIn('[[SI|Supplementary Note 1]]', linked)
        self.assertIn('[[SI#Figure S2|Supplemental Figure 2]]', linked)
        self.assertIn('[[SI#Figure S3|Figure S3]]', linked)
        self.assertIn('[[Figure S4]]', linked)
        self.assertIn('`Figure S5`', linked)
        self.assertNotIn('[[SI#Note S1', linked)


if __name__ == '__main__':
    unittest.main()
