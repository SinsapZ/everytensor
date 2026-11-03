"""Interrupted indexing is retryable work, not a corrupt source file."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from everytensor import indexer, media, store
from everytensor.inventory import Restart


class IndexInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='everytensor-interruption-')
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.root = directory / 'documents'
        self.root.mkdir()
        self.path = self.root / 'long.txt'
        self.path.write_text('Searchable text with a stable continuation.\n' * 400,
                             encoding='utf-8')
        self.db = store.connect(directory / 'catalog.sqlite3')
        self.addCleanup(self.db.close)
        indexer.scan(self.db, self.root)
        self.fid = self.db.execute('SELECT id FROM files WHERE path=?',
                                   (str(self.path),)).fetchone()[0]

    def interrupt_at(self, count):
        calls = 0
        error = Restart()

        def checkpoint():
            nonlocal calls
            calls += 1
            if calls == count:
                raise error

        return checkpoint, error

    def state(self):
        return tuple(self.db.execute('SELECT status,error,fingerprint FROM files WHERE id=?',
                                     (self.fid,)).fetchone())

    def test_progressive_interruption_preserves_cursor_and_can_finish(self):
        indexer.index_pending(self.db, file_id=self.fid, limit=1, max_units=2)
        before = self.state()
        job = tuple(self.db.execute('SELECT * FROM extraction_jobs').fetchone())
        checkpoint, error = self.interrupt_at(2)
        with self.assertRaises(Restart) as raised:
            indexer.index_pending(self.db, checkpoint, file_id=self.fid,
                                  limit=1, max_units=2)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.state(), before)
        self.assertEqual(tuple(self.db.execute('SELECT * FROM extraction_jobs').fetchone()), job)
        self.assertEqual(self.db.execute('SELECT count(*) FROM units').fetchone()[0], 2)

        # Normal reconciliation must leave the saved continuation usable.
        indexer.scan(self.db, self.root)
        for _ in range(100):
            if not self.db.execute('SELECT 1 FROM extraction_jobs').fetchone():
                break
            indexer.index_pending(self.db, file_id=self.fid, limit=1, max_units=2)
        self.assertIsNone(self.db.execute('SELECT 1 FROM extraction_jobs').fetchone())
        self.assertEqual(self.state()[:2], ('ready', ''))
        self.assertIsNotNone(self.state()[2])
        expected = [text for text, _ in media.extract(self.path)]
        actual = [row[0] for row in self.db.execute('SELECT text FROM units ORDER BY ordinal')]
        self.assertEqual(actual, expected)

    def test_hashing_interruption_keeps_file_pending(self):
        checkpoint, error = self.interrupt_at(2)
        with self.assertRaises(Restart) as raised:
            indexer.index_pending(self.db, checkpoint, file_id=self.fid, limit=1)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.state(), ('pending', '', None))
        self.assertEqual(indexer.index_pending(self.db, file_id=self.fid, limit=1), 1)
        self.assertEqual(self.state()[:2], ('ready', ''))

    def test_full_extraction_interruption_keeps_file_pending(self):
        # First checkpoint selects the file, second hashes it, third extracts it.
        checkpoint, error = self.interrupt_at(3)
        with self.assertRaises(Restart) as raised:
            indexer.index_pending(self.db, checkpoint, file_id=self.fid, limit=1)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.state(), ('pending', '', None))
        self.assertEqual(self.db.execute('SELECT count(*) FROM units').fetchone()[0], 0)
        self.assertEqual(indexer.index_pending(self.db, file_id=self.fid, limit=1), 1)

    def test_real_extractor_failure_is_still_reported(self):
        with patch.object(media, 'extract', side_effect=ValueError('invalid source data')):
            self.assertEqual(indexer.index_pending(self.db, file_id=self.fid, limit=1), 0)
        self.assertEqual(self.state(), ('error', 'invalid source data', None))


if __name__ == '__main__':
    unittest.main()
