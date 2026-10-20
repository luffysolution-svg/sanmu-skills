import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / 'skills' / 'sanmu-doc2md') / 'scripts'))
import doctor


class DoctorTests(unittest.TestCase):
    def report(self, require_ocr=False, token=''):
        with patch.dict(os.environ, {'MINERU_API_TOKEN': token}), \
                patch.object(doctor.importlib.metadata, 'version', return_value='99.0.0'), \
                patch.object(doctor.importlib.util, 'find_spec', return_value=object()), \
                patch.object(doctor.shutil, 'which', return_value='node'), \
                patch.object(doctor, 'run_probe', side_effect=['v22.20.0', '0.19.0']):
            return doctor.check_environment(require_ocr)

    def test_missing_token_only_blocks_required_cloud_ocr(self):
        self.assertEqual(self.report()['status'], 'pass')
        self.assertEqual(self.report()['checks'][-1]['status'], 'warn')
        self.assertEqual(self.report(require_ocr=True)['status'], 'fail')

    def test_configured_token_is_never_in_report(self):
        secret = 'test-secret-do-not-print'
        report = self.report(require_ocr=True, token=secret)
        self.assertEqual(report['status'], 'pass')
        self.assertNotIn(secret, json.dumps(report))

    def test_missing_node_reports_failure_without_running_probe(self):
        with patch.object(doctor.shutil, 'which', return_value=None), \
                patch.object(doctor, 'run_probe') as probe:
            report = doctor.check_environment()
        self.assertEqual(report['status'], 'fail')
        self.assertFalse(probe.called)
        self.assertEqual(next(c['status'] for c in report['checks'] if c['name'] == 'katex'), 'fail')

    def test_json_cli_exit_status_matches_report(self):
        with patch.object(sys, 'argv', ['doctor.py', '--json']), \
                patch.object(doctor, 'check_environment', return_value={'status': 'fail', 'checks': []}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(doctor.main(), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
