"""Search presentation and partial-failure recovery against real Qt models."""
import concurrent.futures
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEventLoop, QPersistentModelIndex, QTimer
from PySide6.QtWidgets import QApplication

from everytensor.client import Controller
from everytensor.client_models import ResultModel, match_excerpt
from everytensor import locations


def row(fid=1, **values):
    return dict(dict(file_id=fid, name=f'note-{fid}.txt', path=f'C:/notes/note-{fid}.txt',
                     kind='document', location={}, text=''), **values)


class SearchPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_excerpt_shows_distant_matching_text_and_is_bounded(self):
        text = 'Introduction. ' * 100 + '\n世界模型\t关键实验结果。' + 'More detail. ' * 100
        excerpt = match_excerpt(row(text=text), '关于世界模型')
        self.assertIn('世界模型', excerpt)
        self.assertTrue(excerpt.startswith('…'))
        self.assertTrue(excerpt.endswith('…'))
        self.assertNotIn('\n', excerpt)
        self.assertLessEqual(len(excerpt), 122)

    def test_semantic_excerpt_does_not_fabricate_literal_match(self):
        view = ResultModel.presentation(row(text='Different words conveying the same idea.', sources=['语义']), 'other words')
        self.assertEqual(view['matchLabel'], '语义相关')
        self.assertEqual(view['excerpt'], 'Different words conveying the same idea.')
        self.assertEqual(ResultModel.presentation(row())['matchLabel'], '')

    def test_filename_semantics_never_pretends_to_be_content_evidence(self):
        view = ResultModel.presentation(row(text='Metadata', sources=['语义'], quick_basis='name'))
        self.assertEqual(view['matchLabel'], '名称语义')
        self.assertEqual(view['excerpt'], '')

    def test_new_query_updates_excerpt_without_resetting_file_identity(self):
        model = ResultModel()
        rows = [row(1, text='Alpha ' + 'context ' * 60 + 'Beta ending'), row(2)]
        model.replace(rows, 'Alpha')
        index = QPersistentModelIndex(model.index(0))
        self.assertTrue(model.get(0)['excerpt'].startswith('Alpha'))
        model.replace(list(reversed(rows)), 'Beta')
        self.assertTrue(index.isValid())
        self.assertEqual(index.row(), 1)
        self.assertIn('Beta', model.get(1)['excerpt'])
        self.assertTrue(model.get(1)['excerpt'].startswith('…'))

    def test_mixed_removal_reorder_and_insert_preserve_surviving_indexes(self):
        model = ResultModel()
        model.replace([row(i) for i in (1, 2, 3, 4, 5)])
        indexes = {i: QPersistentModelIndex(model.index(i-1)) for i in range(1, 6)}
        model.replace([row(i) for i in (6, 4, 2, 7, 1)])
        self.assertEqual([model.get(i)['fileId'] for i in range(5)], [6, 4, 2, 7, 1])
        for fid, index in indexes.items():
            self.assertEqual(index.isValid(), fid in (4, 2, 1))
            if index.isValid():
                self.assertEqual(model.get(index.row())['fileId'], fid)

    def test_offscreen_presentations_are_lazy_and_invalidated_on_change(self):
        model = ResultModel()
        rows = [row(i, text='Alpha ' + 'context ' * 50 + 'Beta') for i in range(100)]
        with patch.object(model, 'presentation', wraps=model.presentation) as present:
            model.replace(rows, 'Alpha')
            self.assertEqual(present.call_count, 0)
            self.assertIn('Alpha', model.get(12)['excerpt'])
            model.get(12)
            self.assertEqual(present.call_count, 1)
            model.replace(rows, 'Beta')
            self.assertEqual(model.get(12)['excerpt'][-4:], 'Beta')
            self.assertEqual(present.call_count, 2)
            rows[12] = dict(rows[12], text='Different evidence')
            model.replace(rows, 'Beta')
            self.assertEqual(model.get(12)['excerpt'], 'Different evidence')

    def test_thumbnail_override_survives_rerank_until_file_version_changes(self):
        model = ResultModel()
        rows = [row(1, path='C:/notes/page.pdf', size=10, mtime=2), row(2)]
        role = next(key for key, value in model.roleNames().items() if value == b'thumbnailSource')
        model.replace(rows)
        model.set_thumbnail(1, 'image://preview/cached')
        model.replace(rows[::-1], 'new query')
        self.assertEqual(model.data(model.index(1), role), 'image://preview/cached')
        model.replace([rows[1], dict(rows[0], size=12)])
        self.assertTrue(model.data(model.index(1), role).startswith('image://thumb/'))


class SearchRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='everytensor-search-comfort-')
        self.addCleanup(self.temp.cleanup)
        self.client = Controller(Path(self.temp.name), start_backend=False)
        self.addCleanup(self.client.close)
        self.client._poll_timer.stop()
        self.client._query = 'notes'
        self.client._scope_pending = False
        self.client._stats = {}

    def test_result_count_distinguishes_visible_from_available(self):
        self.client._publish([row(i) for i in range(130)])
        self.assertEqual(self.client.resultLabel, '已显示 100 / 130 个结果')
        self.client.loadMore()
        self.assertEqual(self.client.resultLabel, '130 个结果')
        self.assertFalse(self.client.canLoadMore)

    def test_pending_empty_does_not_report_zero_final_results(self):
        self.client._pending.add('semantic')
        self.assertEqual(self.client.resultLabel, '正在查找…')
        self.assertIn('正在', self.client.emptyMessage)
        self.client._pending.clear()
        self.assertEqual(self.client.resultLabel, '0 个结果')
        self.assertEqual(self.client.emptyMessage, '没有匹配的文件')

    def test_successful_semantics_does_not_hide_filename_failure(self):
        self.client._set_search_error('names', '文件名搜索失败')
        self.client._handle_event(dict(type='results', id=self.client.generation,
                                       channels=True, semantic_rows=[row()], semantic_stage='quick'))
        self.assertEqual(self.client.searchError, '文件名搜索失败')
        self.assertEqual(self.client.results.count, 1)
        self.assertIn('部分搜索未完成', self.client.resultLabel)

    def test_successful_filename_query_does_not_hide_semantic_failure(self):
        self.client._set_search_error('semantic', '语义查询失败')
        future = concurrent.futures.Future()
        future.set_result([row()])
        self.client.futures.put(('names', self.client.generation, future))
        self.client.poll()
        self.assertEqual(self.client.searchError, '语义查询失败')
        self.assertEqual(self.client.results.count, 1)

    def test_failed_final_keeps_partial_rows_and_only_publishes_once(self):
        publications = []
        self.client.rowsChanged.connect(lambda: publications.append(self.client.results.count))
        for kind, value in [('names_partial', [row(2)]), ('names', OSError('unavailable'))]:
            future = concurrent.futures.Future()
            if isinstance(value, Exception):
                future.set_exception(value)
            else:
                future.set_result(value)
            self.client.futures.put((kind, self.client.generation, future))
        self.client.poll()
        self.assertEqual(publications, [1])
        self.assertEqual(self.client.selectedId, 2)
        self.assertTrue(self.client.searchError)

    def test_worker_event_wakes_idle_client_and_honors_refresh_deadline(self):
        calls = []
        loop = QEventLoop()
        def refresh():
            calls.append(time.monotonic())
            loop.quit()
        self.client._last_refresh = time.monotonic() - .8
        self.client._refresh = refresh
        producer = threading.Thread(target=lambda: self.client.events.put(dict(type='index_changed')))
        producer.start()
        producer.join()
        QTimer.singleShot(2000, loop, loop.quit)
        loop.exec()
        self.assertTrue(calls)
        self.assertGreaterEqual(calls[0], self.client._last_refresh + 1.)
        self.assertFalse(self.client._poll_timer.isActive())

    def test_retry_keeps_current_results_and_manual_selection(self):
        self.client._publish([row(1), row(2)])
        self.client.selectFile(2)
        self.client._set_search_error('semantic', '语义查询失败')
        calls = []
        self.client._issue_lexical = lambda: calls.append('lexical')
        self.client._issue_semantic = lambda: calls.append('semantic')
        previous = self.client.generation
        self.client.retrySearch()
        self.assertEqual(calls, ['lexical', 'semantic'])
        self.assertGreater(self.client.generation, previous)
        self.assertEqual(self.client.selectedId, 2)
        self.assertEqual(self.client.results.count, 2)
        self.assertEqual(self.client.searchError, '')
        self.assertTrue(self.client.manualSelection)
        self.assertTrue(self.client.refreshing)

    def test_old_query_failure_cannot_replace_current_search_state(self):
        self.client._publish([row(2)])
        self.client._handle_event(dict(type='results', id=self.client.generation - 1,
                                       error='old query failed', rows=[]))
        self.assertEqual(self.client.selectedId, 2)
        self.assertEqual(self.client.searchError, '')

    def test_resolved_media_position_matches_copy_and_external_playback(self):
        requested = []
        self.client.previewRequested.connect(requested.append)
        for kind in ('video', 'audio'):
            with self.subTest(kind=kind):
                self.client._publish([row(kind=kind, location=dict(start=900., end=950., duration=1000., label='hit'))])
                requested.clear()
                self.client.resolveSelectedLocation(dict(start=59.5, end=60., duration=60., label='preview'))
                self.assertEqual(self.client.selectedRaw['location'],
                                 dict(start=59.5, end=60., duration=60., label='hit'))
                self.client.copyLocation()
                self.assertEqual(self.app.clipboard().text(), locations.reference(self.client.selectedRaw))
                self.assertNotIn('15:00', self.app.clipboard().text())
                with patch('everytensor.locations.find_app', return_value='mpv'):
                    self.assertIn('--start=59.5', locations.plan(self.client.selectedRaw).command)
                self.assertFalse(requested, 'resolving media bounds must not create another preview request')
                self.assertFalse(self.client.manualSelection)

    def test_resolved_document_defaults_keep_existing_user_anchor(self):
        self.client._publish([row(location=dict(page=12))])
        self.client.resolveSelectedLocation(dict(page=1, pages=20))
        self.assertEqual(self.client.selectedRaw['location'], dict(page=12, pages=20))


if __name__ == '__main__':
    unittest.main()
