"""Bounded video ranges and asynchronous filmstrip regression checks."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImage
from everytensor.preview_service import ImageStore, PreviewService


class VideoPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.mp4'
        self.path.write_bytes(b'video fixture')
        stat = self.path.stat()
        self.version = (stat.st_size, stat.st_mtime_ns)
        self.preview = PreviewService(Path(self.temp.name) / 'index.db', ImageStore())
        self.row = dict(file_id=1, path=str(self.path), kind='video', size=stat.st_size,
                        mtime=stat.st_mtime_ns, location=dict(start=600., duration=3600.))
        self.preview._row = dict(self.row)

    def tearDown(self):
        self.preview.close()
        self.preview._pool.shutdown(wait=True, cancel_futures=True)
        self.preview._frame_pool.shutdown(wait=True, cancel_futures=True)
        self.temp.cleanup()

    def complete(self, duration=3600., position=600.):
        self.preview._complete(self.preview._token, dict(path=str(self.path), version=self.version,
                                                        location=dict(start=position, duration=duration)))

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_ranges_center_clamp_and_reach_end(self):
        with patch.object(self.preview, '_make_frames'):
            self.complete()
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (585., 615.))
            self.preview.setFrameSpan(10)
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (595., 605.))
            self.preview.moveFrameRange(1)
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (605., 615.))
            self.preview.focusFrameRange()
            self.assertEqual(self.preview.frameStart, 595.)
            self.complete(position=3599.)
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (3590., 3600.))
            self.preview.moveFrameRange(1)
            self.assertEqual(self.preview.frameStart, 3590.)
            self.assertLess(self.preview.frames[-1]['second'], 3600.)
            self.preview.setFrameSpan(0)
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (0., 3600.))
            self.preview.setFrameSpan(float('nan'))
            self.assertEqual(self.preview.frameSpan, 0.)

    def test_short_and_unknown_videos_have_safe_bounds(self):
        with patch.object(self.preview, '_make_frames') as make_frames:
            self.complete(duration=0, position=0)
            make_frames.assert_not_called()
            self.complete(duration=.02, position=0)
            self.assertEqual((self.preview.frameStart, self.preview.frameEnd), (0., .02))
            self.assertTrue(all(0 <= f['second'] < .02 for f in self.preview.frames))

    def test_seek_rejects_nonfinite_and_clamps_end(self):
        changes = []
        self.preview.locationChanged.connect(changes.append)
        self.preview.seek(10)
        self.assertEqual(changes, [])
        self.preview._duration = 10
        self.preview.seek(float('nan'))
        self.preview.seek(float('inf'))
        self.assertEqual(changes, [])
        self.preview.seek(-5)
        self.preview.seek(20)
        self.assertEqual(changes[0]['start'], 0.)
        self.assertEqual(changes[1]['start'], 9.95)
        self.assertEqual(changes[1]['end'], 10.)

    def test_stable_slots_reject_stale_range(self):
        with patch.object(self.preview, '_make_frames'):
            self.complete()
            first_token = self.preview._frame_token
            self.preview.moveFrameRange(1)
            expected = [f['second'] for f in self.preview.frames]
            image = QImage(4, 4, QImage.Format.Format_RGB32)
            self.preview._frame_complete(first_token, (0, 585., image))
            self.assertEqual([f['second'] for f in self.preview.frames], expected)
            self.assertFalse(any(f['source'] for f in self.preview.frames))
            self.preview._frame_complete(self.preview._frame_token, (7, expected[7], image))
            self.assertEqual([f['index'] for f in self.preview.frames], list(range(8)))
            self.assertTrue(self.preview.frames[7]['source'])
            self.preview._frame_complete(self.preview._frame_token, (0, expected[0], None))
            self.assertTrue(self.preview.frames[0]['failed'])

    def test_scrubbing_preserves_inflight_filmstrip(self):
        release = threading.Event()
        calls = []
        def frame(path, location, bounds):
            calls.append(location['start'])
            release.wait(3)
            return Image.new('RGB', (16, 10))
        def render(row, bounds, cancel):
            return dict(path=row['path'], version=self.version, location=row['location'])
        with patch('everytensor.preview_service.media.preview_image', side_effect=frame), \
             patch.object(self.preview, '_render', side_effect=render):
            try:
                self.complete()
                token, cancel = self.preview._frame_token, self.preview._frame_cancel
                self.preview.setRow(dict(self.row, location=dict(start=602., duration=3600.)))
                self.wait_for(lambda: not self.preview.loading)
                self.assertEqual(self.preview._frame_token, token)
                self.assertFalse(cancel.is_set())
            finally:
                release.set()
            self.wait_for(lambda: not self.preview.framesLoading)
            self.assertEqual(len(calls), 8)
            self.assertTrue(all(f['source'] for f in self.preview.frames))

    def test_frame_failure_finishes_without_shifting_slots(self):
        with patch('everytensor.preview_service.media.preview_image', side_effect=ValueError('bad frame')):
            self.complete()
            self.wait_for(lambda: not self.preview.framesLoading)
            self.assertEqual(len(self.preview.frames), 8)
            self.assertTrue(all(f['failed'] for f in self.preview.frames))

    def test_end_seek_falls_back_and_reports_resolved_position(self):
        calls = []
        def frame(path, location, bounds):
            calls.append(location['start'])
            if location['start'] > 124.5:
                raise ValueError('No frame remains after final presentation timestamp')
            return Image.new('RGB', (16, 10))
        row = dict(self.row, location=dict(start=125., end=125., duration=125.))
        with patch('everytensor.preview_service.media.preview_image', side_effect=frame):
            result = self.preview._render(row, (160, 90), threading.Event())
        self.assertEqual(calls, [124.95, 124.5])
        self.assertEqual(result['location']['start'], 124.5)
        self.assertIn('image', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
