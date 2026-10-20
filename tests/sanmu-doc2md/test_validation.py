import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
from validate_output import validate


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'book'
        (self.root / 'images').mkdir(parents=True)
        Image.new('RGB', (12, 12), 'white').save(self.root / 'images/a.png')

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, text, name='note.md'):
        (self.root / name).write_text(text, encoding='utf-8')

    def report(self):
        return validate(self.root, self.root / 'images')

    def test_shared_asset_not_warning(self):
        self.note('# Note\n\n![a](images/a.png)\n\n![a](images/a.png)\n\n$x$\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass')
        self.assertEqual(r['total_images'], 2)
        self.assertEqual(r['duplicate_images'], 1)
        self.assertEqual(r['warnings'], [])

    def test_path_escape_rejected(self):
        Image.new('RGB', (10, 10)).save(self.root.parent / 'outside.png')
        self.note('# Note\n\n![outside](../outside.png)\n')
        self.assertEqual(self.report()['status'], 'fail')

    def test_unpaired_formula_fails(self):
        self.note('# Note\n\n![a](images/a.png)\n\n$x\n')
        self.assertEqual(self.report()['status'], 'fail')

    def test_code_dollars_not_formulas(self):
        self.note('# Note\n\n![a](images/a.png)\n\n`$HOME`\n\n```sh\n$BAD\n```\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['total_math'], 0)

    def test_nested_note_shared_image_not_orphan(self):
        (self.root / 'chapters').mkdir()
        self.note('# Root\n', '00-目录.md')
        self.note('# Chapter\n\n![a](../images/a.png)\n', 'chapters/ch.md')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])

    def test_unclosed_fence_reports_hidden_image(self):
        self.note('# Note\n\n```\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'fail', r)
        self.assertTrue(any('围栏未闭合' in error for error in r['errors']))
        self.assertEqual(r['detail'][0]['source_image_candidates'], 1)
        self.assertEqual(r['detail'][0]['parsed_images'], 0)

    def test_duplicate_fences_warn_about_swallowed_image(self):
        self.note('# Note\n\n```\n```python\nprint(1)\n```\n```\n\n![a](images/a.png)\n\n```\n')
        r = self.report()
        self.assertTrue(any('图片引用' in warning and '遮蔽' in warning for warning in r['warnings']))
        self.assertEqual(r['total_images'], 0)

    def test_labelled_code_image_example_is_allowed(self):
        self.note('# Note\n\n```markdown\n![example](missing.png)\n```\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])
        self.assertEqual(r['detail'][0]['source_image_candidates'], 2)
        self.assertEqual(r['detail'][0]['parsed_images'], 1)

    def test_math_bracket_not_mistaken_for_markdown_link(self):
        self.note('# Note\n\n![a](images/a.png)\n\n'
                  r'$\mathrm{Al}_{4}\left[\mathrm{Si}_{4}\mathrm{O}_{10}\right](\mathrm{OH})_{8}$' + '\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['errors'], [])

    def test_chemical_state_fake_links_must_be_disambiguated(self):
        self.note('# Note\n\n![a](images/a.png)\n\n'
                  'Pt(s)|K3[Fe(CN)6](aq), K4[Fe(CN)6](aq)||Mn2+(aq)\n')
        r = self.report()
        self.assertEqual(r['status'], 'fail', r)
        self.assertTrue(any('化学物态' in e for e in r['errors']))
        self.note('# Note\n\n' + r'K3\[Fe(CN)6\](aq)' + '\n')
        self.assertEqual(self.report()['status'], 'pass')

    def test_closed_fences_in_containers_are_allowed(self):
        self.note('# Note\n\n> ```markdown\n> ![example](missing.png)\n> ```\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['warnings'], [])

    def test_closed_fences_allow_non_markdown_line_separators(self):
        for separator in ('\u2028', '\v', '\f'):
            with self.subTest(separator=repr(separator)):
                self.note('# Note\n\n```text\nleft' + separator + 'right\n```\n\n![a](images/a.png)\n')
                r = self.report()
                self.assertEqual(r['status'], 'pass', r)
                self.assertEqual(r['warnings'], [])

    def test_source_candidates_count_adjacent_images_separately(self):
        self.note('# Note\n\n![a](images/a.png) ![b](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['detail'][0]['source_image_candidates'], 2)
        self.assertEqual(r['detail'][0]['parsed_images'], 2)

    def test_missing_image_still_counts_as_parsed(self):
        self.note('# Note\n\n![missing](images/missing.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['detail'][0]['parsed_images'], 1)
        self.assertEqual(r['total_images'], 0)

    def test_orphan_details_do_not_delete_files(self):
        orphan = self.root / 'images/unused.png'
        Image.new('RGB', (10, 10)).save(orphan)
        self.note('# Note\n\n![a](images/a.png)\n')
        r = self.report()
        self.assertEqual(r['status'], 'pass', r)
        self.assertEqual(r['orphan_images'], ['images/unused.png'])
        self.assertTrue(any('images/unused.png' in warning for warning in r['warnings']))
        self.assertTrue(orphan.exists())

    def test_resource_syntaxes_and_real_alt_tokens(self):
        self.note('# Note\n\n![plain][img]\n\n[img]: images/a.png "title"\n\n'
                  '![[images/a.png|preview]]\n\n<img src="images/a.png" alt="plain">\n')
        report = self.report()
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['total_images'], 3)
        for image in ('![bad $x$](images/a.png)', '![a [nested] $x$][img]\n\n[img]: images/a.png',
                      '<img src="images/a.png" alt="$x$">'):
            self.note('# Note\n\n' + image)
            self.assertTrue(any('图片说明' in e for e in self.report()['errors']))

    def test_wiki_and_reference_target_anchors(self):
        self.note('# Target\n\n## Figure S1\n\ntext ^block-one\n\n<a id="custom"></a>\n', 'target.md')
        self.note('# Note\n\n[[target#Figure S1|SI]] [[target#^block-one]]\n'
                  '[ref][r] <a href="target.md#custom">HTML</a>\n\n[r]: target.md#figure-s1\n')
        self.assertEqual(self.report()['status'], 'pass', self.report())
        self.note('# Note\n\n[[target#Absent]] [x](target.md#absent)\n')
        self.assertEqual(sum('锚点不存在' in e for e in self.report()['errors']), 2)

    def test_sibling_si_unique_and_ambiguous_lookup(self):
        sibling = self.root.parent / 'SI'
        sibling.mkdir()
        (sibling / 'supplement.md').write_text('# SI\n\n## Figure S1\n', encoding='utf-8')
        self.note('# Note\n\n[[supplement#Figure S1]]\n')
        report = validate(self.root, None, resource_root=self.root.parent)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['documents'], 1)
        other = self.root.parent / 'other'
        other.mkdir()
        (other / 'supplement.md').write_text('# Other\n', encoding='utf-8')
        self.assertTrue(any('Ambiguous' in e for e in validate(self.root, None, resource_root=self.root.parent)['errors']))

    def test_resource_lookup_never_scans_parent_of_resource_root(self):
        from unittest.mock import patch
        import os
        from portable_paths import logical_path
        sibling = self.root.parent / 'SI'
        sibling.mkdir()
        (sibling / 'supplement.md').write_text('# SI\n\n## Figure S1\n', encoding='utf-8')
        self.note('# Note\n\n[[supplement#Figure S1]]\n')
        real_scandir = os.scandir

        def guarded_scandir(path):
            resolved = logical_path(path).resolve()
            if not resolved.is_relative_to(self.root.parent.resolve()):
                raise PermissionError(f'Access denied to unboundary path: {path}')
            return real_scandir(path)

        with patch('portable_paths.os.scandir', side_effect=guarded_scandir):
            report = validate(self.root, None, resource_root=self.root.parent)
            self.assertEqual(report['status'], 'pass', report)
            self.assertEqual(report['errors'], [])

    def test_navigation_gate_is_explicit_for_library_callers(self):
        self.note('# Note\n')
        self.assertEqual(self.report()['status'], 'pass')
        self.assertEqual(validate(self.root, None, require_navigation=True)['status'], 'fail')
        self.note('# Index\n', '00-目录.md')
        self.assertEqual(validate(self.root, None, require_navigation=True)['status'], 'pass')

    def test_dependency_absence_cannot_pass(self):
        from unittest.mock import patch
        self.note('# Note\n\n![a](images/a.png)\n')
        with patch('validate_output.Image', None):
            self.assertEqual(self.report()['status'], 'fail')
        with patch('validate_output.MarkdownIt', None):
            self.assertEqual(self.report()['status'], 'fail')

    def test_code_and_math_resources_not_validated(self):
        self.note('# Note\n\n`![[missing.png]]`\n\n> ```md\n> <img src="missing.png">\n> ```\n\n'
                  '$[x](missing)$\n\n<pre><img src="missing.png"></pre>\n')
        report = self.report()
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['total_images'], 0)


if __name__ == '__main__':
    unittest.main()
