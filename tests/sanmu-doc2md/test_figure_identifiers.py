"""One label grammar must classify supplemental variants used by publishers."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
from figure_identifiers import caption_identifier


class FigureIdentifiersTests(unittest.TestCase):
    def test_publisher_supplemental_variants_share_one_namespace(self):
        for caption, kind in (
            ('Supplemental Figure 1: results', 'Figure'),
            ('Supplementary Figure S2', 'Figure'),
            ('Supplemental Table 3. values', 'Table'),
            ('Supplementary Table S4', 'Table'),
            ('Supplementary Note 5', 'Note'),
        ):
            with self.subTest(caption=caption):
                identifier = caption_identifier(caption)
                self.assertEqual(identifier.namespace, 'supplemental')
                self.assertEqual(identifier.kind, kind)
                self.assertEqual(identifier.number[0], 'S')

    def test_main_and_extended_labels_are_unchanged(self):
        self.assertEqual(caption_identifier('Figure 2. main').namespace, 'main')
        extended = caption_identifier('Extended Data Fig. 2. x')
        self.assertEqual((extended.namespace, extended.number), ('extended', '2'))
        self.assertIsNone(caption_identifier('The figure 2 shows results'))


if __name__ == '__main__':
    unittest.main()
