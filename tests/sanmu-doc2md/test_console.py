import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
from console_output import safe_print


class ConsoleTests(unittest.TestCase):
    def test_unicode_console_keeps_chinese(self):
        stream = io.StringIO()
        safe_print('教材', 2, file=stream)
        self.assertEqual(stream.getvalue(), '教材 2\n')

    def test_legacy_console_cannot_crash_progress(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='cp1252', newline='\n')
        safe_print('教材', 'ready', file=stream, flush=True)
        self.assertEqual(buffer.getvalue().decode('cp1252'), r'\u6559\u6750 ready' + '\n')

    def test_extract_cli_with_legacy_redirected_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / '教材.md').write_text('# 教材\n\n$x$\n', encoding='utf-8')
            script = (Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts/extract_formulas.py'
            env = dict(os.environ, PYTHONIOENCODING='cp1252:strict')
            run = subprocess.run([sys.executable, str(script), str(root)],
                                 env=env, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode('ascii', errors='replace'))
            self.assertIn(b'1', run.stdout)


if __name__ == '__main__':
    unittest.main()
