"""Bound cache rewrite frequency while an exact delta remains searchable."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from everytensor import quick_cache, quick_index, store


class QuickCompactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='everytensor-compaction-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'index.sqlite3'
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)
        quick_index.install(self.db)
        self.model = 'compaction-test'
        with self.db:
            quick_index.save(self.db, 'model', self.model)
            for fid, vector in ((1, [1, 0]), (2, [0, 1])):
                source = Path(self.temp.name)/f'{fid}.txt'
                source.write_text('content', encoding='utf-8')
                stat = source.stat()
                self.db.execute('''INSERT INTO files(id,path,root,name,kind,status,size,mtime)
                    VALUES(?,?,?,?,?,'ready',?,?)''',
                    (fid, str(source), self.temp.name, source.name, 'document',
                     stat.st_size, stat.st_mtime_ns))
                self.db.execute('INSERT INTO quick_vectors VALUES(?,?,?,?,?)',
                    ('name', fid, str(source), self.model, np.array(vector, '<f2').tobytes()))
        self.assertTrue(quick_cache.build(self.path, self.model, 2))
        self.cache = self.path.parent/quick_cache.FILENAME

    def change(self):
        with self.db:
            self.db.execute("UPDATE quick_vectors SET data=? WHERE kind='name' AND source_id=1",
                            (np.array([-1, 0], '<f2').tobytes(),))

    def dirty_count(self):
        return self.db.execute('SELECT count(*) FROM quick_dirty').fetchone()[0]

    def test_recent_small_delta_defers_and_stays_searchable(self):
        before = self.cache.read_bytes()
        self.change()
        with patch.object(quick_cache, '_write_snapshot', side_effect=AssertionError('unexpected rewrite')) as write:
            self.assertFalse(quick_cache.build(self.path, self.model, 2, min_compaction_age=30))
            write.assert_not_called()
        self.assertEqual(before, self.cache.read_bytes())
        self.assertEqual(self.dirty_count(), 1)
        results = quick_index.QuickSearch(0).search(self.db, [1, 0], self.model, limit=2)
        self.assertEqual([r['file_id'] for r in results], [2, 1])
        self.assertLess(results[1]['similarity'], 0)

    def test_expired_base_compacts(self):
        self.change()
        old = time.time()-60
        os.utime(self.cache, (old, old))
        self.assertTrue(quick_cache.build(self.path, self.model, 2, min_compaction_age=30))
        self.assertEqual(self.dirty_count(), 0)

    def test_half_full_delta_compacts_without_waiting(self):
        self.change()
        with patch.object(quick_cache, 'MAX_DIRTY', 2):
            self.assertTrue(quick_cache.build(self.path, self.model, 2, min_compaction_age=30))
        self.assertEqual(self.dirty_count(), 0)

    def test_default_compacts_immediately(self):
        self.change()
        self.assertTrue(quick_cache.build(self.path, self.model, 2))
        self.assertEqual(self.dirty_count(), 0)

    def test_backward_clock_does_not_defer(self):
        self.change()
        future = time.time()+3600
        os.utime(self.cache, (future, future))
        self.assertTrue(quick_cache.build(self.path, self.model, 2, min_compaction_age=30))
        self.assertEqual(self.dirty_count(), 0)

    def test_cancellation_preserves_base_and_delta(self):
        before = self.cache.read_bytes()
        self.change()
        stopping = threading.Event()
        stopping.set()
        self.assertFalse(quick_cache.build(self.path, self.model, 2, stopping,
                                          min_compaction_age=30))
        self.assertEqual(before, self.cache.read_bytes())
        self.assertEqual(self.dirty_count(), 1)


if __name__ == '__main__':
    unittest.main()
