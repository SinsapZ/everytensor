"""Exercise exact EmbeddingGemma file and temporal ranking on SQLite snapshots."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from everytensor import embeddings, store


class FullVectorSearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='everytensor-full-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'files'
        self.root.mkdir()
        self.catalog = Path(temp.name) / 'index.sqlite3'
        self.db = store.connect(self.catalog)
        self.addCleanup(self.db.close)
        self.model = 'test-full-model'
        self.cache = store.VectorCache()
        self.q = np.array([1., 0., 0., 0.], dtype='<f4')
        store.set_setting(self.db, 'roots', [str(self.root)])

    def file(self, fid, kind='document'):
        path = self.root / str(fid)
        path.write_bytes(b'x')
        info = path.stat()
        self.db.execute("""INSERT INTO files(id,path,name,root,kind,status,size,mtime)
            VALUES(?,?,?,?,?,'ready',?,?)""",
                        (fid, str(path), path.name, str(self.root), kind, info.st_size, info.st_mtime_ns))
        return path

    def unit(self, fid, uid, vector, *, start=0, end=4):
        digest = str(uid)
        array = np.asarray(vector, dtype='<f4')
        self.db.execute('INSERT INTO vectors VALUES(?,?,?,?)',
                        (digest, self.model, len(array), array.tobytes()))
        self.db.execute('INSERT INTO units(id,file_id,ordinal,text,location,digest) VALUES(?,?,?,?,?,?)',
                        (uid, fid, uid, f'unit {uid}', json.dumps(dict(start=start, end=end)), digest))

    def search(self, limit, **kwargs):
        return store.semantic(self.db, self.q, self.model, limit=limit, cache=self.cache, **kwargs)

    @staticmethod
    def signature(rows):
        return [(r['file_id'], r['unit_id']) for r in rows]

    def test_ties_stale_winners_and_live_scope_with_warm_cache(self):
        with self.db:
            paths = [self.file(i, 'code' if i == 4 else 'document') for i in range(1, 6)]
            for uid in range(1, 261):
                self.unit(1, uid, self.q)
            for fid in range(2, 6):
                self.unit(fid, 1000+fid, self.q[:2] if fid == 2 else self.q)
        self.assertEqual(self.signature(self.search(3)), [(5, 1005), (4, 1004), (3, 1003)])
        paths[4].unlink()
        paths[3].write_bytes(b'different size')
        self.assertEqual(self.signature(self.search(3)), [(3, 1003), (2, 1002), (1, 260)])
        store.set_setting(self.db, 'exclusions', [str(paths[2])])
        self.assertEqual(self.signature(self.search(3, kind='document')), [(2, 1002), (1, 260)])
        store.set_setting(self.db, 'roots', [])
        store.set_setting(self.db, 'scope', 'folders')
        self.assertEqual(self.search(3), [])

    def test_audio_video_windows_and_their_excerpts(self):
        with self.db:
            self.file(1, 'audio')
            self.file(2, 'video')
            self.file(3)
            for fid in (1, 2):
                for i, (start, end, vector) in enumerate([
                    (0, 4, [1, .01]), (1, 4, [1, 0]), (7, 12, [1, .035, .02, 0]),
                    (14, 19, [1, .05]), (30, 35, [1, .06]), (40, 45, [0, 1]),
                ]):
                    self.unit(fid, 10*fid+i, vector, start=start, end=end)
            self.unit(3, 35, [1, .08])
        q = embeddings.truncate(self.q, 4)
        expected_ids = [(2, 21), (1, 11), (2, 22), (1, 12), (2, 23), (1, 13), (3, 35)]
        hits = self.search(3)
        self.assertEqual(self.signature(hits), expected_ids)
        for row in hits:
            uid = row['unit_id']
            self.assertEqual(row['text'], f'unit {uid}')
            if row['kind'] in ('audio', 'video'):
                data, dim = self.db.execute('SELECT data,dim FROM vectors WHERE digest=?',
                                            (str(uid),)).fetchone()
                score = embeddings.truncate(np.frombuffer(data, '<f4'), dim) @ embeddings.truncate(q, dim)
                self.assertEqual(row['similarity'], float(score))

    def test_ranking_across_groups_with_duplicate_units_and_stale_files(self):
        rng = np.random.default_rng(623)
        count = 17123
        vectors = rng.normal(size=(count, 4)).astype('<f4')
        file_ids = rng.integers(1, 29, size=count)
        # Exercise tie order across the 16K scoring buffer boundary.
        vectors[[2, count-2, count-1]] = self.q
        file_ids[[2, count-2, count-1]] = [2, 3, 2]
        with self.db:
            for fid in range(1, 29):
                self.file(fid)
            for i, (fid, vector) in enumerate(zip(file_ids.tolist(), vectors)):
                self.unit(fid, i+1, vector)
        (self.root / '3').unlink()
        best = {}
        for i, (fid, vector) in enumerate(zip(file_ids.tolist(), vectors)):
            if fid != 3:
                key = float(embeddings.truncate(vector, 4) @ self.q), i+1
                best[fid] = max(best.get(fid, (-2., 0)), key)
        limit = 12
        expected = [(fid, best[fid][1]) for fid in sorted(best, key=best.get, reverse=True)[:limit]]
        self.assertEqual(self.signature(self.search(limit)), expected)
        self.assertEqual(self.signature(self.search(limit)), expected)

    def test_concurrent_commit_progress_cancellation_and_cache_invalidation(self):
        with self.db:
            self.file(1)
            self.file(2)
            for uid in range(1, 270):
                self.unit(1, uid, [0, 1])
            self.unit(2, 1000, [1, 0])
        writer = store.connect(self.catalog)
        self.addCleanup(writer.close)
        snapshots = []
        def update(rows):
            snapshots.append(rows)
            if len(snapshots) == 1:
                with writer:
                    writer.execute("UPDATE vectors SET data=? WHERE digest='269'",
                                   (np.array([1, 0], '<f4').tobytes(),))
                    writer.execute('DELETE FROM files WHERE id=2')
        self.assertEqual(self.signature(self.search(1, progress=update)), [(2, 1000)])
        self.assertTrue(snapshots)
        self.assertEqual(self.signature(self.search(1)), [(1, 269)])
        cancelled = []
        self.assertEqual(self.search(1, progress=cancelled.append, cancelled=lambda: bool(cancelled)), [])
        self.assertEqual(self.signature(self.search(1)), [(1, 269)])

    def test_rolled_back_vector_cannot_survive_a_reused_revision(self):
        with self.db:
            self.file(1)
            self.unit(1, 1, [0, 1])
            self.file(2)
            self.unit(2, 2, [1, .2])
        self.db.execute("UPDATE vectors SET data=? WHERE digest='1'", (self.q[:2].tobytes(),))
        self.assertEqual(self.signature(self.search(1)), [(1, 1)])
        self.db.rollback()
        with self.db:
            self.db.execute("UPDATE vectors SET data=? WHERE digest='1'",
                            (np.array([0, -1], '<f4').tobytes(),))
        self.assertEqual(self.signature(self.search(1)), [(2, 2)])


if __name__ == '__main__':
    unittest.main()
