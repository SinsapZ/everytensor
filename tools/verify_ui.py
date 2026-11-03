"""Exercise the real QML desktop with isolated sample data (no model or scan).

Run: .venv/Scripts/python.exe tools/verify_ui.py
Screenshots: add --capture-dir <folder>. Uses Qt's offscreen software renderer.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from everytensor.desktop import create_desktop
from everytensor import locations, media

CAPTURE_DIR = None


class DesktopInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The offscreen Windows plugin does not discover system fonts itself.
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
            font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        cls.temp = tempfile.TemporaryDirectory(prefix='everytensor-ui-')
        cls.root = Path(cls.temp.name)
        cls.runtime = create_desktop(cls.root / 'state', start_backend=False, restore_geometry=False)
        cls.window = cls.runtime.window
        cls.client = cls.runtime.client
        cls.client._poll_timer.stop()
        cls.client._issue_lexical = lambda: None
        cls.client._issue_semantic = lambda: None
        cls.rows = []
        for i in range(130):
            path = cls.root / f'研究笔记 {i + 1:03} — 世界模型与空间理解.txt'
            path.write_text(('世界模型与空间理解\n\n从观察到预测：整理实验记录、方法与结果。\n' * 70), encoding='utf-8')
            cls.rows.append(dict(file_id=i + 1, path=str(path), name=path.name, kind='document',
                                 size=path.stat().st_size, mtime=path.stat().st_mtime_ns,
                                 text=path.read_text('utf-8'), location={'line': 1}))
        cls.window.show()
        QTest.qWait(200)

    @classmethod
    def tearDownClass(cls):
        cls.runtime.close()
        cls.temp.cleanup()

    def setUp(self):
        self.js('resultMenu.close()')
        self.js('modelSetupDialog.close()')
        self.js('settingsDialog.close()')
        QTest.qWait(140)
        self.window.setWidth(1280)
        self.window.setHeight(820)
        self.client._query = '世界模型'
        self.client._kind = ''
        self.client._manual_generation = -1
        self.client._pending.clear()
        self.client._inflight = False
        self.client._refreshing = False
        self.client._channel_errors.clear()
        self.client._search_error = ''
        self.client._visible = 100
        self.window.setProperty('feedbackMessage', '')
        self.client._publish(self.rows)
        self.js('window.focusSearch()')
        QTest.qWait(200)

    def js(self, source):
        expr = QQmlExpression(QQmlEngine.contextForObject(self.window), self.window, source)
        result = expr.evaluate()
        self.assertFalse(expr.hasError(), expr.error().toString())
        return result[0] if isinstance(result, tuple) else result

    def item(self, name):
        item = self.window.findChild(QObject, name)
        if item is None:
            pending = [self.window.contentItem()]
            while pending:
                candidate = pending.pop()
                if candidate.objectName() == name:
                    item = candidate
                    break
                pending.extend(candidate.childItems())
        self.assertIsNotNone(item, name)
        return item

    def click(self, name, button=Qt.MouseButton.LeftButton):
        self.click_item(self.item(name), button)

    def click_item(self, item, button=Qt.MouseButton.LeftButton):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.window, button, Qt.KeyboardModifier.NoModifier, point)
        QTest.qWait(40)

    def key(self, key, modifier=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.window, key, modifier)
        QTest.qWait(25)

    def capture(self, name):
        if CAPTURE_DIR:
            QTest.mouseMove(self.window, QPointF(8, self.window.height() - 8).toPoint())
            QTest.qWait(150)
            target = CAPTURE_DIR / (name + '.png')
            self.assertTrue(self.window.grabWindow().save(str(target)), str(target))

    def wait_until(self, predicate, seconds=3):
        end = time.monotonic() + seconds
        while not predicate() and time.monotonic() < end:
            QTest.qWait(20)
            # Qt's Windows test wait can retain the GIL; let Python preview
            # workers run as they do in the application's normal event loop.
            time.sleep(.005)
        self.assertTrue(predicate(), 'Timed out waiting for UI state')

    def video_row(self, file_id=2000):
        """A real, small video exercises FFmpeg and asynchronous Qt images."""
        if not shutil.which(media.executable('ffmpeg')):
            self.skipTest('FFmpeg is needed for the real video interaction check')
        path = self.root / f'视频范围验证 {file_id}.mp4'
        if not path.exists():
            media.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                       'testsrc2=size=160x90:rate=2:duration=125',
                       '-c:v', 'mpeg4', '-q:v', '8', '-threads', '1', str(path)])
        return dict(file_id=file_id, path=str(path), name=path.name, kind='video',
                    size=path.stat().st_size, mtime=path.stat().st_mtime_ns,
                    location={'start': 62., 'end': 70., 'duration': 125.},
                    hits=[{'start': 62., 'end': 70.}, {'start': 100., 'end': 108.}])

    def test_01_six_categories_cycle(self):
        self.click('tab_all')
        for kind in ('document', 'image', 'video', 'audio', 'code', ''):
            self.key(Qt.Key.Key_Right)
            self.assertEqual(self.client.kind, kind)
        self.key(Qt.Key.Key_Left)
        self.assertEqual(self.client.kind, 'code')

    def test_02_keyboard_selection_and_end(self):
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.client.selectedId, 1)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.client.selectedId, 2)
        self.key(Qt.Key.Key_End)
        self.assertEqual(self.client.selectedId, 100)
        self.key(Qt.Key.Key_Home)
        self.assertEqual(self.client.selectedId, 1)

    def test_03_settings_returns_search_focus(self):
        self.key(Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(220)
        self.assertTrue(self.item('settingsDialog').property('opened'))
        self.key(Qt.Key.Key_Escape)
        QTest.qWait(220)
        self.assertTrue(self.item('searchField').hasActiveFocus())

    def test_04_up_returns_to_search_without_selecting_text(self):
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Up)
        self.assertTrue(self.item('searchField').hasActiveFocus())
        self.assertEqual(self.client.selectedId, 1)

    def test_05_copy_feedback_and_text_selection(self):
        self.click('copyLocationButton')
        self.assertEqual(self.app.clipboard().text(), locations.reference(self.rows[0]))
        self.assertTrue(self.item('feedbackToast').property('visible'))
        self.capture('copy-feedback')
        self.key(Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        self.key(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), '世界模型')
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), locations.reference(self.rows[1]))
        self.wait_until(lambda: not self.item('feedbackToast').property('visible'))

    def test_06_context_menu_mouse_keyboard_and_escape(self):
        self.click('resultRow_2', Qt.MouseButton.RightButton)
        self.assertEqual(self.client.selectedId, 2)
        self.assertTrue(self.item('resultMenu').property('visible'))
        self.capture('context-menu')
        self.key(Qt.Key.Key_Escape)
        QTest.qWait(150)
        self.assertEqual(self.client.query, '世界模型')
        self.assertFalse(self.item('resultMenu').property('visible'))
        self.assertTrue(self.item('resultsList').hasActiveFocus())
        self.key(Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
        self.assertTrue(self.item('resultMenu').property('visible'))
        self.click_item(self.js('resultMenu.itemAt(2)'))
        self.assertEqual(self.app.clipboard().text(), locations.reference(self.rows[1]))

    def test_07_immediate_navigation_opens_current_file(self):
        opened = []
        with patch('everytensor.locations.open_hit', side_effect=lambda row: opened.append(row['file_id'])):
            self.key(Qt.Key.Key_Down)
            for _ in range(12):
                QTest.keyClick(self.window, Qt.Key.Key_Down)
            QTest.keyClick(self.window, Qt.Key.Key_Return)
            self.wait_until(lambda: bool(opened))
        self.assertEqual(opened, [13])

    def test_08_reranking_keeps_selected_file_and_keyboard_position(self):
        self.key(Qt.Key.Key_Down)
        for _ in range(4):
            self.key(Qt.Key.Key_Down)
        self.assertEqual(self.client.selectedId, 5)
        reordered = self.rows[4:20] + self.rows[:4] + self.rows[20:]
        self.client._publish(reordered)
        QTest.qWait(60)
        self.assertEqual(self.client.selectedId, 5)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.client.selectedId, 6)

    def test_09_new_file_resets_preview_scroll(self):
        self.wait_until(lambda: not self.runtime.preview.loading)
        text_scroll = self.item('previewTextScroll')
        flickable = text_scroll.property('contentItem')
        flickable.setProperty('contentY', 240)
        self.assertGreater(flickable.property('contentY'), 0)
        self.click('resultRow_2')
        self.wait_until(lambda: not self.runtime.preview.loading)
        self.assertEqual(flickable.property('contentY'), 0)

    def test_10_header_fits_at_supported_sizes(self):
        # PDF controls add one more button than the text preview.
        import fitz
        path = self.root / '世界模型 — 方法与实验.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), 'EveryTensor UI preview fixture')
            pdf.save(str(path))
        row = dict(file_id=1000, path=str(path), name=path.name, kind='document',
                   size=path.stat().st_size, mtime=path.stat().st_mtime_ns, location={'page': 1})
        self.client._publish([row])
        for width, height in ((920, 620), (1040, 700), (1280, 820), (1600, 900)):
            with self.subTest(size=(width, height)):
                self.window.setWidth(width)
                self.window.setHeight(height)
                QTest.qWait(200)
                title, actions = self.item('previewTitle'), self.item('previewActions')
                header = self.item('previewHeader')
                title_end = title.mapToScene(QPointF(title.width(), title.height()))
                actions_start = actions.mapToScene(QPointF(0, 0))
                self.assertTrue(title_end.y() <= actions_start.y() or title_end.x() <= actions_start.x())
                self.assertGreater(title.width(), 200)
                for name in ('copyLocationButton', 'revealButton', 'fitButton', 'openButton'):
                    button = self.item(name)
                    start = button.mapToItem(header, QPointF(0, 0))
                    self.assertGreaterEqual(start.x(), 0)
                    self.assertLessEqual(start.x() + button.width(), header.width())
                    self.assertLessEqual(start.y() + button.height(), header.height())
        self.window.setWidth(920)
        self.window.setHeight(620)
        self.wait_until(lambda: not self.runtime.preview.loading)
        QTest.qWait(250)
        self.capture('pdf-compact')

    def test_11_empty_results_recovery(self):
        self.client._kind = 'image'
        self.client._publish([])
        QTest.qWait(50)
        self.assertTrue(self.item('clearFilterButton').property('visible'))
        self.capture('empty-results')
        self.click('clearFilterButton')
        self.assertEqual(self.client.kind, '')
        self.assertEqual(self.client.query, '世界模型')
        self.click('searchScopeButton')
        QTest.qWait(220)
        self.assertTrue(self.item('settingsDialog').property('opened'))

    def test_12_pagination_preserves_selection(self):
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_End)
        self.assertEqual(self.client.selectedId, 100)
        self.client.loadMore()
        QTest.qWait(50)
        self.assertEqual(self.client.selectedId, 100)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.client.selectedId, 101)

    def test_13_settings_validation_and_layout(self):
        self.window.setWidth(920)
        self.window.setHeight(620)
        self.key(Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(220)
        self.item('budgetField').setProperty('text', '0')
        self.click('saveSettingsButton')
        self.assertTrue(self.item('settingsDialog').property('opened'))
        self.assertTrue(self.item('settingsDialog').property('localError'))
        self.assertFalse(self.client.settingsApplying)
        self.capture('settings-validation')

    def test_14_escape_clears_query_then_filter(self):
        self.client._kind = 'document'
        self.client.stateChanged.emit()
        self.key(Qt.Key.Key_Escape)
        self.assertEqual(self.client.query, '')
        self.assertEqual(self.client.kind, 'document')
        self.key(Qt.Key.Key_Escape)
        self.assertEqual(self.client.kind, '')
        self.assertTrue(self.item('searchField').hasActiveFocus())

    def test_15_reveal_shortcut_and_double_click(self):
        self.key(Qt.Key.Key_Down)
        with patch('everytensor.client.subprocess.Popen') as launch:
            self.key(Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            launch.assert_called_once()
            self.assertIn(self.rows[0]['path'], launch.call_args.args[0])
        opened = []
        with patch('everytensor.locations.open_hit', side_effect=lambda row: opened.append(row['file_id'])):
            row = self.item('resultRow_2')
            point = row.mapToScene(QPointF(row.width() / 2, row.height() / 2)).toPoint()
            QTest.mouseDClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
            self.wait_until(lambda: bool(opened))
        self.assertEqual(opened, [2])

    def test_16_model_setup_flow_and_compact_layout(self):
        manager = self.runtime.model_setup
        manager._state.update(phase='needed', detail='', error='', received=0)
        manager.changed.emit()
        self.window.setWidth(920)
        self.window.setHeight(620)
        self.js('window.openSettings()')
        QTest.qWait(220)
        flick = self.item('settingsScroll').property('contentItem')
        flick.setProperty('contentY', max(0, flick.property('contentHeight') - flick.height()))
        QTest.qWait(80)
        self.click('modelSetupButton')
        QTest.qWait(120)
        self.assertTrue(self.item('modelSetupDialog').property('opened'))
        button = self.item('startModelButton')
        point = button.mapToScene(QPointF(button.width(), button.height()))
        self.assertLessEqual(point.x(), self.window.width())
        self.assertLessEqual(point.y(), self.window.height())
        self.capture('model-setup-compact')
        with patch.object(manager, '_start') as start:
            self.click('startModelButton')
            start.assert_called_once_with(None)
        manager._events.put(('progress', dict(phase='downloading', received=12345678, total=23456789,
                                             detail='正在下载模型')))
        manager.poll()
        QTest.qWait(60)
        self.assertFalse(button.property('visible'))
        self.click('pauseModelButton')
        self.assertTrue(manager._cancel.is_set())
        manager._events.put(('paused', None))
        manager.poll()
        QTest.qWait(60)
        self.assertEqual(button.property('text'), '继续安装')
        self.capture('model-setup-paused')
        manager._events.put(('error', '测试网络断开，已保存进度'))
        manager.poll()
        QTest.qWait(60)
        self.assertEqual(button.property('text'), '重试')
        self.assertTrue(self.item('modelInstallError').property('visible'))
        self.click('deferModelButton')
        QTest.qWait(100)
        self.assertFalse(self.item('modelSetupDialog').property('visible'))
        self.assertTrue(manager.state['seen'])
        self.assertFalse(manager.shouldOffer)

    def test_17_first_launch_prompt_can_be_skipped(self):
        runtime = create_desktop(self.root / 'first-launch', start_backend=False,
                                 restore_geometry=False, offer_setup=True)
        try:
            QTest.qWait(180)
            dialog = runtime.window.findChild(QObject, 'modelSetupDialog')
            self.assertTrue(dialog.property('opened'))
            runtime.model_setup.dismiss()
        finally:
            runtime.close()
        runtime = create_desktop(self.root / 'first-launch', start_backend=False,
                                 restore_geometry=False, offer_setup=True)
        try:
            QTest.qWait(100)
            self.assertFalse(runtime.model_setup.shouldOffer)
            self.assertFalse(runtime.window.findChild(QObject, 'modelSetupDialog').property('visible'))
        finally:
            runtime.close()

    def test_18_activation_preserves_pending_settings(self):
        from everytensor.client import Controller
        from everytensor.backend import NullBackend
        from everytensor import store
        client = Controller(self.root / 'activation', start_backend=False)
        transport = NullBackend()
        try:
            with patch('everytensor.client.Backend', return_value=transport):
                client.activateInstalledModel(str(self.root / 'installed-model'))
                client.applySettings(dict(scope='folders', roots=[str(self.root)], budget_gib=4))
                client.pauseIndexing(True)
                self.wait_until(lambda: client.commands is transport)
                self.assertTrue(any(c['action'] == 'configure' for c in transport.messages))
                self.assertTrue(any(c['action'] == 'pause' and c['value'] for c in transport.messages))
                self.assertEqual(store.setting(client.db, 'model'), str(self.root / 'installed-model'))
        finally:
            client.close()

    def test_19_video_range_controls_and_real_frames(self):
        preview = self.runtime.preview
        self.client._kind = 'video'
        self.client._publish([self.video_row()])
        self.wait_until(lambda: not preview.loading and len(preview.frames) == 8, seconds=10)
        self.wait_until(lambda: not preview.framesLoading, seconds=10)
        self.assertTrue(all(frame['source'] for frame in preview.frames))
        self.assertEqual(preview.frameSpan, 30)
        self.assertEqual((preview.frameStart, preview.frameEnd), (47, 77))
        self.assertFalse(self.item('videoRangeCurrent').property('visible'))
        self.click('videoRangeNext')
        self.assertEqual((preview.frameStart, preview.frameEnd), (77, 107))
        self.assertTrue(self.item('videoRangeCurrent').property('visible'))
        self.click('videoRangeCurrent')
        self.assertEqual((preview.frameStart, preview.frameEnd), (47, 77))
        self.assertFalse(self.item('videoRangeCurrent').property('visible'))
        choice = self.item('videoFrameRange')
        choice.forceActiveFocus()
        self.key(Qt.Key.Key_End)
        self.wait_until(lambda: preview.frameSpan == 0)
        self.assertEqual((preview.frameStart, preview.frameEnd), (0, 125))
        self.assertFalse(self.item('videoRangeNext').property('enabled'))
        self.assertFalse(self.item('videoRangePrevious').property('enabled'))
        self.wait_until(lambda: not preview.framesLoading, seconds=10)
        self.assertTrue(all(frame['source'] for frame in preview.frames))
        choice.forceActiveFocus()
        self.key(Qt.Key.Key_Home)
        self.assertEqual(preview.frameSpan, 10)
        self.wait_until(lambda: not preview.framesLoading, seconds=10)
        frame_list = self.item('videoFrames')
        first = frame_list.property('contentItem').childItems()[0]
        self.click_item(first)
        self.wait_until(lambda: not preview.loading)
        self.assertAlmostEqual(self.client.selectedRaw['location']['start'], preview.frames[0]['second'])
        for width, height in ((920, 620), (1280, 820)):
            self.window.setWidth(width)
            self.window.setHeight(height)
            QTest.qWait(250)
            self.wait_until(lambda: not preview.loading, seconds=10)
            pane = self.item('previewPane')
            for name in ('videoFrameRange', 'videoRangeLabel', 'videoRangeCurrent', 'videoTimeline'):
                item = self.item(name)
                if not item.property('visible'):
                    continue
                point = item.mapToItem(pane, QPointF(0, 0))
                self.assertGreaterEqual(point.x(), 0)
                self.assertLessEqual(point.x() + item.width(), pane.width())
                self.assertLessEqual(point.y() + item.height(), pane.height())
            self.capture('video-range-' + str(width))

    def test_20_video_pending_seek_does_not_cross_files(self):
        preview = self.runtime.preview
        rows = [self.video_row(), self.video_row(2001)]
        self.client._publish(rows)
        self.wait_until(lambda: not preview.loading and preview.duration > 0, seconds=10)
        slider = self.item('videoTimeline')
        slider.forceActiveFocus()
        # Queue a debounced seek and change files before its 90 ms timer fires.
        QTest.keyClick(self.window, Qt.Key.Key_Right)
        self.client.selectFile(2001, True)
        QTest.qWait(180)
        self.wait_until(lambda: not preview.loading, seconds=10)
        self.assertEqual(self.client.selectedId, 2001)
        self.assertEqual(self.client.selectedRaw['location']['start'], 62.)
        self.assertEqual(preview.position, 62.)

    def test_21_download_feedback_and_background(self):
        manager = self.runtime.model_setup
        manager._timer.stop()
        try:
            manager._state.update(phase='downloading', detail='正在下载模型和运行组件',
                                  error='', received=600000000, total=1800000000,
                                  speed_bps=2500000, eta_seconds=480, stalled=False,
                                  transfer_state='downloading', completed_files=3, file_count=40)
            manager.changed.emit()
            self.js('window.openModelSetup()')
            QTest.qWait(150)
            speed = self.item('modelInstallSpeed')
            self.assertIn('/s', speed.property('text'))
            self.assertIn('8', speed.property('text'))
            self.assertFalse(self.item('modelInstallExamples').property('visible'))
            self.capture('model-download-speed')
            manager._state.update(stalled=True, speed_bps=0, eta_seconds=-1)
            manager.changed.emit()
            QTest.qWait(30)
            self.assertNotIn('/s', speed.property('text'))
            self.click('deferModelButton')
            self.assertTrue(manager.active)
            self.assertFalse(self.item('modelSetupDialog').property('visible'))
        finally:
            manager._state.update(phase='paused', speed_bps=0, eta_seconds=-1, stalled=False)
            manager.changed.emit()
            manager._timer.start()

    def test_22_search_excerpt_partial_failure_and_retry(self):
        row = dict(self.rows[0], text='<b>世界模型</b>：预测实验的误差记录。', sources=['内容'])
        self.client._publish([row])
        self.client._set_search_error('semantic', '语义查询暂时失败')
        self.client.stateChanged.emit()
        QTest.qWait(80)
        self.assertTrue(self.item('searchErrorNotice').property('visible'))
        excerpt = self.item('resultExcerpt_1')
        self.assertIn('<b>世界模型</b>', excerpt.property('text'))
        self.client.selectFile(1, True)
        self.window.setWidth(920)
        self.window.setHeight(620)
        QTest.qWait(120)
        self.capture('search-partial-error')
        self.click('retrySearchButton')
        self.assertEqual(self.client.searchError, '')
        self.assertEqual(self.client.selectedId, 1)
        self.assertEqual(self.client.results.count, 1)

    def test_23_result_summary_fits_beside_categories(self):
        self.window.setWidth(920)
        self.window.setHeight(620)
        self.client._pending.add('semantic')
        self.client._more['semantic'] = True
        self.client.stateChanged.emit()
        QTest.qWait(80)
        tab = self.item('tab_code')
        summary = self.item('resultStatus')
        tab_end = tab.mapToScene(QPointF(tab.width(), 0)).x()
        summary_start = summary.mapToScene(QPointF(0, 0)).x()
        self.assertGreater(summary_start, tab_end)
        self.assertIn('100 / 130+', self.client.resultLabel)
        self.assertIn('正在检索语义', self.client.resultLabel)
        self.capture('search-progress-compact')
        self.client._pending.clear()
        self.client._more['semantic'] = False

    def test_24_video_controls_fit_beside_results(self):
        preview = self.runtime.preview
        self.window.setWidth(920)
        self.window.setHeight(620)
        self.client._publish([self.video_row(), self.video_row(2001)])
        self.wait_until(lambda: not preview.loading and len(preview.frames) == 8, seconds=10)
        self.wait_until(lambda: not preview.framesLoading, seconds=10)
        pane = self.item('previewPane')
        self.assertTrue(self.item('resultsList').property('visible'))
        # Keep a usable picture beside the results, not just controls and a sliver.
        self.assertGreaterEqual(self.item('previewImageScroll').height(), 110)
        for name in ('videoFrameRange', 'videoRangePrevious', 'videoRangeLabel', 'videoRangeNext',
                     'videoRangeCurrent', 'videoFrames', 'videoTimeline'):
            item = self.item(name)
            if not item.property('visible'):
                continue
            point = item.mapToItem(pane, QPointF(0, 0))
            self.assertGreaterEqual(point.x(), 0)
            self.assertGreater(item.height(), 0)
            self.assertLessEqual(point.x() + item.width(), pane.width())
            self.assertLessEqual(point.y() + item.height(), pane.height())
        self.capture('video-results-compact')

    def test_90_capture(self):
        self.capture('results-wide')
        self.window.setWidth(920)
        self.window.setHeight(620)
        QTest.qWait(200)
        self.capture('results-compact')
        self.client.setQuery('')
        QTest.qWait(100)
        self.capture('home-compact')

    def test_99_no_qml_warnings(self):
        self.assertEqual(self.runtime.warnings, [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-dir', type=Path)
    args, remaining = parser.parse_known_args()
    if args.capture_dir:
        CAPTURE_DIR = args.capture_dir.resolve()
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
