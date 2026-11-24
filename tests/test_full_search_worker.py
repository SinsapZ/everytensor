"""Exercise cached full retrieval and its Quick fallback through worker commands."""
from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from everytensor import engine, indexer, query_vectors, store


class FullSearchWorkerTests(unittest.TestCase):
    def test_cached_full_query_prefers_verified_full_rows_and_falls_back_by_kind(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            directory = Path(folder)
            corpus = directory / 'documents'
            corpus.mkdir()
            (corpus / 'report.txt').write_text('apples and pears', encoding='utf-8')
            db_path = directory / 'catalog.sqlite3'
            db = store.connect(db_path)
            store.set_setting(db, 'scope', 'folders')
            store.set_setting(db, 'roots', [str(corpus)])
            indexer.scan(db, str(corpus))
            indexer.index_pending(db)
            vector = np.ones(256, dtype='<f4') / 16
            key = 'test-full'
            db.execute('INSERT INTO vectors SELECT digest,?,256,? FROM units', (key, vector.tobytes()))
            db.commit()
            query_vectors.put(db, key, 'fruit', vector)
            db.close()

            commands = queue.Queue()
            commands.put(dict(action='pause', value=True))
            def request(n, kind='', compact=True):
                commands.put(dict(action='search', id=n, query='fruit', kind=kind,
                                  channels=True, semantic_only=compact, limit=10))
            request(1)
            replies = []
            def receive(event):
                if event['type'] != 'results':
                    return
                replies.append(event)
                if event['id'] == 1:
                    request(2, compact=False)
                elif event['id'] == 2:
                    request(3, kind='image')
                else:
                    commands.put(dict(action='stop'))

            inventory = Mock(scanning=False)
            quick = Mock(coverage={})
            quick.model.encode.return_value = vector
            stack.enter_context(patch('everytensor.inventory.Inventory', return_value=inventory))
            stack.enter_context(patch.object(engine.quick_index, 'resolve_model', return_value='quick'))
            stack.enter_context(patch.object(engine.quick_index, 'QuickService', return_value=quick))
            quick_search = stack.enter_context(patch.object(engine.quick_index.QuickSearch, 'search',
                return_value=[dict(file_id=2, quick_basis='name')]))
            stack.enter_context(patch.object(store, 'resolve_model', return_value=folder))
            stack.enter_context(patch.object(query_vectors, 'model_signature', return_value=key))
            engine.worker(db_path, commands, Mock(put=receive))

            self.assertEqual([reply['semantic_stage'] for reply in replies], ['full', 'full', 'quick'])
            self.assertTrue(all(reply['full_checked'] for reply in replies))
            self.assertEqual(replies[0]['semantic_rows'][0]['name'], 'report.txt')
            self.assertNotIn('rows', replies[0])
            self.assertEqual(replies[1]['rows'][0]['name'], 'report.txt')
            self.assertEqual(replies[2]['semantic_rows'][0]['quick_basis'], 'name')
            quick_search.assert_called_once()
            self.assertEqual(quick_search.call_args.args[3], 'image')


if __name__ == '__main__':
    unittest.main()
