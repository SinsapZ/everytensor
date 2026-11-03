"""Compare mutable dense-cache search with SQLite and an independent full sort."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from everytensor import quick_cache, quick_index, store


class QuickDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='everytensor-delta-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'index.sqlite3'
        self.db = store.connect(self.path)
        self.addCleanup(self.db.close)
        quick_index.install(self.db)
        self.rng = np.random.default_rng(911)
        self.dim, self.model = 16, 'test-model'
        self.q = self.vector().astype('f4')
        with self.db:
            quick_index.save(self.db, 'model', self.model)
            for fid in range(1, 25):
                self.db.execute('''INSERT INTO files(id,path,root,name,kind,status,size,mtime)
                    VALUES(?,?,?,?,?,'ready',0,0)''',
                    (fid, str(Path(self.temp.name)/f'{fid}.txt'), self.temp.name, f'{fid}.txt',
                     'document' if fid % 2 else 'code'))
                self.put('name', fid, str(Path(self.temp.name)/f'{fid}.txt'))
                for ordinal in range(3):
                    uid = fid*10+ordinal
                    self.db.execute('''INSERT INTO units(id,file_id,ordinal,text,location,digest)
                        VALUES(?,?,?,'searchable content','{}',?)''', (uid, fid, ordinal, str(uid)))
                    self.put('text', uid, str(uid))
        self.assertTrue(quick_cache.build(self.path, self.model, self.dim))

    def vector(self):
        v = self.rng.normal(size=self.dim if hasattr(self, 'dim') else 16).astype('f4')
        return (v / np.linalg.norm(v)).astype('<f2')

    def put(self, kind, sid, identity, vector=None):
        self.db.execute('INSERT OR REPLACE INTO quick_vectors VALUES(?,?,?,?,?)',
                        (kind, sid, identity, self.model,
                         (self.vector() if vector is None else vector.astype('<f2')).tobytes()))

    def signature(self, rows):
        return [(r['file_id'], r['quick_basis'], r['unit_id']) for r in rows]

    def oracle(self, kind='', limit=7):
        q = self.q / np.linalg.norm(self.q)
        best = {}
        for basis, sql in ((0, '''SELECT f.id AS fid,v.source_id,v.data FROM quick_vectors v
            JOIN files f ON f.id=v.source_id WHERE v.kind='name' AND v.identity=f.path
            AND f.status<>'metadata' AND v.model=? AND (?='' OR f.kind=?)'''),
                           (1, '''SELECT f.id AS fid,v.source_id,v.data FROM quick_vectors v
            JOIN units u ON u.id=v.source_id JOIN files f ON f.id=u.file_id
            WHERE v.kind='text' AND v.identity=u.digest AND f.status='ready'
            AND v.model=? AND (?='' OR f.kind=?)''')):
            for row in self.db.execute(sql, (self.model, kind, kind)):
                score = np.dot(np.frombuffer(row['data'], '<f2').astype('f4'), q)
                if not basis:
                    score = np.float32(score * np.float32(.75))
                value = float(score), basis, row['source_id']
                if row['fid'] not in best or value > best[row['fid']]:
                    best[row['fid']] = value
        ranked = sorted(best, key=best.get, reverse=True)[:limit]
        return [(fid, 'text' if best[fid][1] else 'name',
                 best[fid][2] if best[fid][1] else None) for fid in ranked]

    def assert_equivalent(self, kind='', delta=True):
        packed = quick_cache.blocks(self.db, self.model, self.dim, kind, allow_delta=True)
        self.assertIsNotNone(packed)
        if delta:
            self.assertTrue(packed.dirty)
        packed.close()
        actual = quick_index.QuickSearch(max_bytes=0).search(self.db, self.q, self.model, kind, 7)
        with patch.object(quick_cache, 'blocks', return_value=None):
            expected = quick_index.QuickSearch(max_bytes=0).search(self.db, self.q, self.model, kind, 7)
        self.assertEqual(self.signature(actual), self.signature(expected))
        self.assertEqual(self.signature(actual), self.oracle(kind))
        np.testing.assert_allclose([r['similarity'] for r in actual],
                                   [r['similarity'] for r in expected], atol=1e-7)

    def test_update_delete_insert_and_file_kind(self):
        with self.db:
            self.put('text', 11, '11', self.q)
            self.db.execute("DELETE FROM quick_vectors WHERE kind='text' AND source_id=22")
            self.db.execute('DELETE FROM files WHERE id=3')
            self.db.execute("UPDATE files SET kind='document' WHERE id=4")
            self.db.execute("UPDATE files SET path=path||'.renamed',name=name||'.renamed' WHERE id=5")
            self.db.execute("UPDATE units SET file_id=7,ordinal=9 WHERE id=61")
            self.db.execute("INSERT INTO units VALUES(999,8,9,'new content','{}','999')")
            self.put('text', 999, '999', self.q)
        for kind in ('', 'document', 'code', 'image'):
            self.assert_equivalent(kind)

    def test_status_and_rebuild(self):
        with self.db:
            self.db.execute("UPDATE files SET status='pending' WHERE id=1")
            self.db.execute("UPDATE files SET status='metadata' WHERE id=2")
            self.put('text', 31, '31', self.q)
        self.assert_equivalent()
        self.assertTrue(quick_cache.build(self.path, self.model, self.dim))
        self.assertEqual(self.db.execute('SELECT count(*) FROM quick_dirty').fetchone()[0], 0)
        self.assert_equivalent(delta=False)

    def test_snapshot_consistency_across_writer_and_rebuild(self):
        writer = store.connect(self.path, initialize=False)
        self.addCleanup(writer.close)
        with store.read_snapshot(self.db):
            expected = self.oracle()
            with writer:
                writer.execute("DELETE FROM quick_vectors WHERE kind='text' AND source_id=11")
            actual = quick_index.QuickSearch(0).search(self.db, self.q, self.model, limit=7)
            self.assertEqual(self.signature(actual), expected)
            self.assertTrue(quick_cache.build(self.path, self.model, self.dim))
            # An old SQLite snapshot cannot pair with the newly published base.
            self.assertIsNone(quick_cache.blocks(self.db, self.model, self.dim, allow_delta=True))
            actual = quick_index.QuickSearch(0).search(self.db, self.q, self.model, limit=7)
            self.assertEqual(self.signature(actual), expected)
        self.assert_equivalent(delta=False)

    def test_missing_marker_and_bad_identity_fall_back(self):
        with self.db:
            self.db.execute("DELETE FROM quick_state WHERE key='dense_base_revision'")
        self.assertIsNone(quick_cache.blocks(self.db, self.model, self.dim, allow_delta=True))
        self.assertTrue(quick_cache.build(self.path, self.model, self.dim))
        with self.db:
            quick_index.save(self.db, 'cache_identity', 'different-database')
        self.assertIsNone(quick_cache.blocks(self.db, self.model, self.dim, allow_delta=True))

    def test_scope_filter_and_cancellation(self):
        with self.db:
            self.put('text', 11, '11', self.q)
        actual = quick_index.QuickSearch(0).search(self.db, self.q, self.model,
                                                 cancelled=lambda: True)
        self.assertEqual(actual, [])
        with self.db:
            store.set_setting(self.db, 'scope', 'folders')
            store.set_setting(self.db, 'roots', [])
        self.assertEqual(quick_index.QuickSearch(0).search(self.db, self.q, self.model), [])

    def test_overflow_rollback_and_failed_build(self):
        with self.db:
            self.put('text', 11, '11', self.q)
            self.put('text', 12, '12', self.q)
        with patch.object(quick_cache, 'MAX_DIRTY', 1):
            self.assertIsNone(quick_cache.blocks(self.db, self.model, self.dim, allow_delta=True))
            rows = quick_index.QuickSearch(0).search(self.db, self.q, self.model, limit=7)
            self.assertEqual(self.signature(rows), self.oracle())
        before = self.oracle()
        self.db.execute("DELETE FROM quick_vectors WHERE kind='text' AND source_id=11")
        self.db.rollback()
        self.assertEqual(self.oracle(), before)
        with patch.object(quick_cache.os, 'replace', side_effect=PermissionError('in use')):
            self.assertFalse(quick_cache.build(self.path, self.model, self.dim))
        self.assert_equivalent()

    def test_model_switch_and_truncated_cache_fall_back(self):
        with self.db:
            quick_index.save(self.db, 'model', 'new-model')
        self.assertIsNone(quick_cache.blocks(self.db, 'new-model', self.dim, allow_delta=True))
        with self.db:
            quick_index.save(self.db, 'model', self.model)
        cache_path = self.path.parent/quick_cache.FILENAME
        cache_path.write_bytes(cache_path.read_bytes()[:50])
        self.assertIsNone(quick_cache.blocks(self.db, self.model, self.dim, allow_delta=True))
        self.assertTrue(quick_cache.build(self.path, self.model, self.dim))
        self.assert_equivalent(delta=False)

    def test_identical_vectors_have_stable_ties_across_block_layouts(self):
        self.dim = 256
        self.q = self.vector().astype('f4')
        blob = self.q.astype('<f2').tobytes()
        with self.db:
            self.db.execute("DELETE FROM quick_vectors WHERE kind='text'")
            self.db.execute("UPDATE quick_vectors SET data=?", (blob,))
        self.assertTrue(quick_cache.build(self.path, self.model, self.dim))
        with self.db:
            self.db.execute("UPDATE quick_vectors SET data=? WHERE source_id%3=0", (blob,))
        for block_size in (3, 7, 16, 2048):
            with patch.object(quick_cache, 'BLOCK_ROWS', block_size):
                self.assert_equivalent()


if __name__ == '__main__':
    unittest.main()
