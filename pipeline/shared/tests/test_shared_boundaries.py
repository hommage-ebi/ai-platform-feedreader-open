"""The shared foundation must remain usable with no source adapter installed."""
import ast
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline.shared.agents import load_agents
from pipeline.shared.interests import load_interests, render_relevance
from pipeline.shared.image_store import store_figure
from pipeline.shared.runtime import digest, lock, project_root, read, record, root_from_run, write

ROOT = Path(__file__).resolve().parents[3]


class SharedBoundaryTests(unittest.TestCase):
    def test_shared_imports_do_not_load_any_source_package(self):
        program = '''
import importlib.abc, sys
class RejectSource(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pipeline.arxiv' or fullname.startswith('pipeline.arxiv.'):
            raise AssertionError('Shared code imported source adapter: ' + fullname)
sys.meta_path.insert(0, RejectSource())
from pipeline.shared import agents, collection, interests, runtime, image_store
assert not any(name == 'pipeline.arxiv' or name.startswith('pipeline.arxiv.') for name in sys.modules)
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_modules_import_dependencies_explicitly(self):
        for path in (ROOT / 'pipeline/arxiv').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.ImportFrom):
                    continue
                with self.subTest(module=path.name, line=node.lineno):
                    self.assertNotIn('*', [name.name for name in node.names])
                    if node.level == 1 and node.module == 'common':
                        self.assertTrue(
                            {name.name for name in node.names}.isdisjoint(
                                {'digest', 'lock', 'now', 'project_root', 'read',
                                 'record', 'root_from_run', 'write'}),
                            'Import source-neutral helpers from shared.runtime')

    def test_shared_config_and_root_discovery_need_no_arxiv_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'pipeline/config', root / 'pipeline/config')
            (root / 'pipeline/pyproject.toml').write_text('[project]\nname="fixture"\n')
            (root / 'package.json').write_text('{}')
            run = root / 'data/runs/2026-10-19/source-neutral'
            run.mkdir(parents=True)
            self.assertFalse((root / 'pipeline/arxiv').exists())
            self.assertEqual(root_from_run(run), root)
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(project_root(run), root)
            self.assertEqual(load_agents(root), load_agents(ROOT))
            interests = load_interests(root)
            template = '{interest_config}\n{topic_example}\n{input_file}\n{output_file}'
            text = render_relevance(template, interests, 'input.json', 'output.json')
            self.assertIn(interests['interests'][0]['criteria'], text)
            self.assertIn('input.json\noutput.json', text)

    def test_shared_runtime_refuses_unidentified_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                root_from_run(Path(directory) / 'nested')

    def test_durable_runtime_operations_are_source_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with lock(root / '.test.lock'):
                write(root / 'data.json', {'label': '材料', 'keys': ['b', 'a']})
            self.assertEqual(read(root / 'data.json')['label'], '材料')
            self.assertEqual(digest({'a': 1, 'b': 2}), digest({'b': 2, 'a': 1}))
            record(root, 'custom-stage', 'failed', {'error': 'synthetic'}, 'source:item')
            self.assertEqual(read(root / 'ledger.json')['stages']['custom-stage:source:item']['status'], 'failed')
            self.assertEqual(json.loads((root / 'failures.jsonl').read_text())['stage'], 'custom-stage')

    def test_image_store_accepts_prevalidated_non_arxiv_identity(self):
        from PIL import Image
        image = Image.new('RGB', (3, 2), (13, 47, 89))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        class MemoryStore:
            def __init__(self):self.objects = {}
            def put(self, key, data, mime):self.objects[key] = (data, mime)
            def get(self, key):return self.objects[key]
        store = MemoryStore()
        article = {'id': 'example-source:item-42', 'version': 'revision-a'}
        entry = store_figure(article, {'src': 'selected:figure-1'}, {}, Path('/unused'),
                             store, loader=lambda directory, source: (stream.getvalue(), 'image/png'))
        self.assertEqual(entry['article_id'], article['id'])
        self.assertEqual(entry['version'], article['version'])
        self.assertTrue(entry['object_key'].startswith('papers/example-source%3Aitem-42/revision-a/'))
        self.assertEqual(Image.open(io.BytesIO(store.get(entry['object_key'])[0])).convert('RGB').tobytes(), image.tobytes())

    def test_image_identity_rejects_unsafe_segments_before_materialization(self):
        called=[]
        def loader(*args):called.append(args);raise AssertionError('unsafe identity reached loader')
        for version in ('', '.', '..', 'v1/extra', 'revision%2Fa', 'bad\\version', 'bad\x00version', '-revision', 'a'*65):
            with self.subTest(version=version),self.assertRaises(ValueError):
                store_figure({'id':'example:item','version':version},{'src':'image'}, {}, '/unused',None,loader=loader)
        for article_id in ('','..','x/../y','x//y','x\\y','x%2Fy','x\x00y','\ud800'):
            with self.subTest(article_id=article_id),self.assertRaises(ValueError):
                store_figure({'id':article_id,'version':'current'},{'src':'image'}, {}, '/unused',None,loader=loader)
        self.assertEqual(called,[])
