"""Filesystem enumeration must not silently omit Windows long-path documents."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
from portable_paths import logical_path, safe_path, walk_files


class PortablePathsTests(unittest.TestCase):
    def test_extended_unc_and_drive_are_reversible(self):
        self.assertEqual(str(logical_path(r'\\?\UNC\server\share\file')), r'\\server\share\file')
        self.assertEqual(str(logical_path(r'\\?\C:\folder\file')), r'C:\folder\file')
        if os.name == 'nt':
            self.assertEqual(str(safe_path(r'\\server\share\file')), r'\\?\UNC\server\share\file')
            self.assertEqual(str(safe_path(r'\\?\C:\folder\file')), r'\\?\C:\folder\file')

    def test_enumeration_errors_are_not_silenced(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('portable_paths.os.scandir', side_effect=PermissionError('denied')):
                with self.assertRaises(PermissionError):
                    walk_files(folder)

    @unittest.skipUnless(os.name == 'nt', 'Windows extended paths')
    def test_nested_long_paths_are_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            deep = root / ('a' * 120) / ('b' * 120) / ('c' * 70)
            safe_path(deep).mkdir(parents=True)
            safe_path(deep / 'note.md').write_text('content', encoding='utf-8')
            self.assertEqual(walk_files(root, '*.md'), [deep / 'note.md'])
            import shutil
            shutil.rmtree(safe_path(root / ('a' * 120)))


if __name__ == '__main__':
    unittest.main()
