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

    def test_captions_starting_with_numeric_terms_like_3d_or_2d_match(self):
        s22 = caption_identifier("Figure S22. 3D image of NOCC5's AFM.")
        self.assertIsNotNone(s22)
        self.assertEqual((s22.namespace, s22.kind, s22.number, s22.heading), ('supplemental', 'Figure', 'S22', 'Figure S22'))
        f1 = caption_identifier('Figure 1. 2D/1D NiO/CdS heterojunction')
        self.assertIsNotNone(f1)
        self.assertEqual((f1.namespace, f1.kind, f1.number), ('main', 'Figure', '1'))
        ch_fig = caption_identifier('图 1.1 晶胞结构示意图')
        self.assertIsNotNone(ch_fig)
        self.assertEqual((ch_fig.namespace, ch_fig.number), ('main', '1.1'))


if __name__ == '__main__':
    unittest.main()
