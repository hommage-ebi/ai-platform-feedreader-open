import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from pipeline import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(preflight.__file__).parent
        self.python = (self.project / '.python-version').read_text().strip()
        self.dependencies = dict(dependency.split('==') for dependency in
            tomllib.loads((self.project / 'pyproject.toml').read_text())['project']['dependencies'])

    def check(self, versions=None, imports=None):
        with patch.object(preflight.platform, 'python_version', return_value=self.python), \
                patch.object(preflight.importlib.metadata, 'version', side_effect=(versions or self.dependencies).__getitem__), \
                patch.object(preflight.importlib, 'import_module', side_effect=imports) as import_module:
            return preflight.check(), import_module

    def test_checks_only_pinned_runtime_libraries(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network is not part of preflight')):
            report, imports = self.check()
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['completed'])
        self.assertEqual(report['checks']['libraries'], self.dependencies)
        self.assertEqual(report['checks']['imports'], list(preflight.MODULES))
        self.assertEqual([call.args[0] for call in imports.call_args_list], list(preflight.MODULES))
        self.assertEqual(set(report['checks']), {'python', 'libraries', 'imports'})

    def test_wrong_python_fails(self):
        with patch.object(preflight.platform, 'python_version', return_value='0.0.0'):
            report = preflight.check()
        self.assertFalse(report['ok'])
        self.assertTrue(report['completed'])
        self.assertIn('Expected Python', report['error'])

    def test_wrong_package_version_fails(self):
        versions = {**self.dependencies, 'pillow': '0.0.0'}
        report, imports = self.check(versions=versions)
        self.assertFalse(report['ok'])
        self.assertIn('pillow: expected', report['error'])
        imports.assert_not_called()

    def test_missing_native_library_fails(self):
        report, _ = self.check(imports=OSError('Cairo library unavailable'))
        self.assertFalse(report['ok'])
        self.assertEqual(report['exit_code'], 1)
        self.assertIn('Cairo library unavailable', report['error'])

    def test_fresh_report_on_bad_bootstrap_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            report.write_text('{"ok":true}')
            result = subprocess.run(['bash', str(self.project / 'bootstrap.sh'),
                '--report', str(report), '--unknown-option'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            recorded = json.loads(report.read_text())
            self.assertFalse(recorded['ok'])
            self.assertEqual(recorded['stage'], 'arguments')
            self.assertIn('--unknown-option', recorded['error'])

    def test_failed_check_replaces_previous_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            report.write_text('{"ok":true}')
            with patch.object(preflight, 'check', return_value={'ok': False, 'completed': True,
                    'exit_code': 1, 'error': 'Missing dependency'}):
                self.assertEqual(preflight.main(['--report', str(report)]), 1)
            self.assertEqual(json.loads(report.read_text())['error'], 'Missing dependency')


if __name__ == '__main__':
    unittest.main()
