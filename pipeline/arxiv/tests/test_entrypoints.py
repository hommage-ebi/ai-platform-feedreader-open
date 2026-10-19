"""Package CLIs and direct script CLIs use the same package implementations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class EntrypointTests(unittest.TestCase):
    def test_package_entrypoint_help_from_repository_root(self):
        for module in ('pipeline.arxiv.workflow', 'pipeline.arxiv.reader_adapter', 'pipeline.preflight'):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_direct_entrypoint_help_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            for relative in ('pipeline/arxiv/workflow.py', 'pipeline/arxiv/reader_adapter.py', 'pipeline/preflight.py'):
                with self.subTest(entrypoint=relative):
                    result = subprocess.run([sys.executable, str(ROOT / relative), '--help'], cwd=directory,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_direct_status_from_run_directory_needs_no_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / 'data/runs/2026-10-19/smoke'
            run.mkdir(parents=True)
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            environment.pop('PROJECT_ROOT', None)
            result = subprocess.run([sys.executable, str(ROOT / 'pipeline/arxiv/workflow.py'),
                                     'status', '--project-root', str(root)], cwd=run,
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'incomplete')
            self.assertEqual(json.loads((run / 'ledger.json').read_text())['pending'][0]['stage'], 'collect')
            self.assertFalse((root / 'ledger.json').exists())
