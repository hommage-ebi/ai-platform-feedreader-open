"""Collection mechanics work without an installed source adapter."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.shared import collection
from pipeline.shared.collection import (
    TRANSACTION, collection_lock, commit_collection, recover_collection,
)
from pipeline.shared.runtime import read, write


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'pipeline').mkdir()
        (self.root / 'pipeline/pyproject.toml').write_text('[project]\nname="fixture"\n')
        (self.root / 'package.json').write_text('{}')
        self.run = self.root / 'data/runs/example-source'
        self.run.mkdir(parents=True)
        self.dest = self.root / 'data/articles'
        self.dest.mkdir()
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_commit_and_read_without_source_adapter(self):
        files = {
            'stories/item/revision-a.html': {'kind': 'html', 'data': '<p>Article</p>'},
            'papers.json': {'kind': 'json', 'data': [{'id': 'example:item'}]},
        }
        with collection_lock(self.dest, self.run):
            recover_collection(self.dest)
            commit_collection(self.dest, files)
        self.assertFalse((self.root / 'pipeline/arxiv').exists())
        self.assertEqual((self.dest / 'stories/item/revision-a.html').read_text(), '<p>Article</p>')
        self.assertEqual(read(self.dest / 'papers.json'), [{'id': 'example:item'}])
        self.assertFalse((self.dest / TRANSACTION).exists())
        self.assertFalse((self.dest / '.collection.lock').exists())

    def test_interrupted_commit_retains_journal_for_replay(self):
        files = {
            'images-manifest.json': {'kind': 'json', 'data': {'images': []}},
            'papers.json': {'kind': 'json', 'data': [{'id': 'example:item'}]},
        }
        original_write = collection.write

        def interrupt(path, data):
            if Path(path) == self.dest / 'papers.json':
                raise OSError('Simulated interruption after manifest')
            return original_write(path, data)

        with collection_lock(self.dest, self.run):
            with patch.object(collection, 'write', side_effect=interrupt):
                with self.assertRaises(OSError):
                    commit_collection(self.dest, files)
        self.assertTrue((self.dest / TRANSACTION).is_file())
        self.assertEqual(read(self.dest / 'images-manifest.json'), {'images': []})
        self.assertFalse((self.dest / 'papers.json').exists())
        with collection_lock(self.dest, self.run):
            recover_collection(self.dest)
        self.assertEqual(read(self.dest / 'papers.json'), [{'id': 'example:item'}])
        self.assertFalse((self.dest / TRANSACTION).exists())

    def test_collection_lock_rejects_other_owner(self):
        with collection_lock(self.dest, self.run):
            owner = read(self.dest / '.collection.lock/owner.json')
            with self.assertRaisesRegex(RuntimeError, 'collection is locked'):
                with collection_lock(self.dest, self.run):
                    self.fail('Overlapping writer entered the collection')
            self.assertEqual(read(self.dest / '.collection.lock/owner.json'), owner)
        self.assertFalse((self.dest / '.collection.lock').exists())

    def test_pending_publication_rejects_collection_write(self):
        write(self.root / '.sites-runtime/publish.lock', {'pending': True})
        with self.assertRaisesRegex(RuntimeError, 'Publication is pending'):
            with collection_lock(self.dest, self.run):
                self.fail('Writer entered during publication')
        self.assertFalse((self.dest / '.collection.lock').exists())

    def test_lock_cleanup_preserves_replaced_owner(self):
        with collection_lock(self.dest, self.run):
            write(self.dest / '.collection.lock/owner.json', {'token': 'replacement'})
        self.assertTrue((self.dest / '.collection.lock').exists())

    def test_replay_rejects_unsafe_paths(self):
        for path in ('../outside.json', '/absolute.json', 'bad\\path', TRANSACTION):
            with self.subTest(path=path):
                write(self.dest / TRANSACTION, {
                    'schema_version': 1,
                    'files': {path: {'kind': 'json', 'data': {}}},
                })
                with self.assertRaisesRegex(ValueError, 'Unsafe collection transaction path'):
                    recover_collection(self.dest)
                self.assertTrue((self.dest / TRANSACTION).exists())


if __name__ == '__main__':
    unittest.main()
