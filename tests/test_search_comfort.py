"""Search presentation and partial-failure recovery against real Qt models."""
import concurrent.futures
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPersistentModelIndex
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
