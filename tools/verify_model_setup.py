"""Offline regression checks for optional component installation."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from everytensor import model_install as setup

PAYLOAD = bytes(range(256)) * 4096


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        with self.server.activity_lock:
            self.server.active += 1
            self.server.max_active = max(self.server.max_active, self.server.active)
        try:
            if self.path == '/latency':
                time.sleep(.15)
            self.serve_payload()
        finally:
            with self.server.activity_lock:
                self.server.active -= 1

    def serve_payload(self):
        range_header = self.headers.get('Range')
        self.server.ranges.append(range_header)
        start = int(range_header.split('=')[1].split('-')[0]) if range_header else 0
        if self.path == '/ignore-range':
            start = 0
        partial = start > 0
        self.send_response(206 if partial else 200)
        if partial:
            position = start + 1 if self.path == '/bad-range' else start
            self.send_header('Content-Range', f'bytes {position}-{len(PAYLOAD)-1}/{len(PAYLOAD)}')
        self.send_header('Content-Length', str(len(PAYLOAD)-start))
        self.end_headers()
        data = PAYLOAD[start:]
        if self.path == '/interrupt' and not self.server.interrupted:
            self.server.interrupted = True
            data = data[:300000]
        try:
            self.wfile.write(data)
        except (ConnectionResetError, BrokenPipeError):
            pass
        self.close_connection = True


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.ranges = []
        cls.server.interrupted = False
        cls.server.activity_lock = threading.Lock()
        cls.server.active = cls.server.max_active = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='everytensor-model-test-')
        self.root = Path(self.temp.name)
        self.target = self.root / 'payload.bin'
        self.partial = self.root / 'payload.bin.part'
        self.cancel = threading.Event()
        self.server.ranges.clear()
        self.server.interrupted = False

    def tearDown(self):
        self.temp.cleanup()

    def download(self, route='/', digest=None, progress=lambda value: None):
        return setup.download(dict(url=self.base+route, bytes=len(PAYLOAD),
                                   sha256=digest or hashlib.sha256(PAYLOAD).hexdigest()),
                              self.target, self.cancel, progress, opener=self.opener)

    def test_resume_and_reuse_without_network(self):
        self.partial.write_bytes(PAYLOAD[:34567])
        self.download()
        self.assertEqual(self.server.ranges, ['bytes=34567-'])
        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertFalse(self.partial.exists())
        self.download()
        self.assertEqual(len(self.server.ranges), 1)

    def test_bounded_parallel_download_and_cached_byte_accounting(self):
        item = dict(url=self.base+'/latency', bytes=len(PAYLOAD), sha256=hashlib.sha256(PAYLOAD).hexdigest())
        jobs = [(item, self.root / f'parallel-{i}.bin') for i in range(6)]
        start = time.monotonic()
        for i in range(6):
            setup.download(item, self.root / f'serial-{i}.bin', self.cancel, lambda value: None, opener=self.opener)
        serial = time.monotonic() - start
        events = []
        installer = setup.Installer(self.root, events.append, opener=self.opener)
        self.server.max_active = 0
        start = time.monotonic()
        installer.fetch(jobs, len(PAYLOAD)*6)
        parallel = time.monotonic() - start
        self.assertEqual(self.server.max_active, 3)
        self.assertLess(parallel, serial * .8)
        self.assertEqual(events[-1]['received'], len(PAYLOAD)*6)
        self.assertEqual(events[-1]['network_received'], len(PAYLOAD)*6)
        self.assertEqual(events[-1]['completed_files'], 6)
        self.assertTrue(all(0 <= event['received'] <= event['total'] for event in events))
        for _, target in jobs:
            self.assertEqual(target.read_bytes(), PAYLOAD)
        requests = len(self.server.ranges)
        installer.fetch(jobs, len(PAYLOAD)*6)
        self.assertEqual(len(self.server.ranges), requests)
        self.assertEqual(events[-1]['network_received'], 0)
        print(f'\nControlled local HTTP latency: serial={serial:.3f}s, parallel={parallel:.3f}s, ratio={serial/parallel:.2f}x')

    def test_parallel_failure_keeps_ready_unpublished(self):
        item = dict(url=self.base, bytes=len(PAYLOAD), sha256='0'*64)
        installer = setup.Installer(self.root, lambda event: None, opener=self.opener)
        with self.assertRaises(ValueError):
            installer.fetch([(item, self.root / f'bad-{i}.bin') for i in range(6)], len(PAYLOAD)*6)
        self.assertTrue(installer.cancel.is_set())
        self.assertFalse((installer.root / 'ready.json').exists())
        self.assertFalse(any((self.root / f'bad-{i}.bin').exists() for i in range(6)))

    def test_transfer_meter_excludes_cache_and_expires_stale_speed(self):
        from everytensor.model_setup import TransferMeter
        meter = TransferMeter()
        meter.record(0, 10.)
        # 900 cached bytes do not count as transferred network bytes.
        meter.record(100, 12.)
        sample = meter.snapshot(12., 1000, 2000, 'downloading')
        self.assertEqual(sample['speed_bps'], 50.)
        self.assertEqual(sample['eta_seconds'], 20.)
        sample = meter.snapshot(20., 1000, 2000, 'downloading')
        self.assertTrue(sample['stalled'])
        self.assertEqual(sample['speed_bps'], 0.)
        self.assertEqual(sample['eta_seconds'], -1)
        self.assertEqual(meter.snapshot(13., 1000, 2000, 'checking')['speed_bps'], 0.)
        meter.record(200, 21.)
        self.assertFalse(meter.snapshot(21., 1100, 2000, 'downloading')['stalled'])

    def test_connection_after_long_verification_gets_fresh_measurement_window(self):
        from everytensor.model_setup import TransferMeter
        meter = TransferMeter()
        meter.record(0, 0., 'checking')
        self.assertFalse(meter.snapshot(20., 900, 2000, 'checking')['stalled'])
        meter.record(0, 20., 'connecting')
        self.assertFalse(meter.snapshot(20., 900, 2000, 'connecting')['stalled'])
        meter.record(100, 22., 'downloading')
        self.assertEqual(meter.snapshot(22., 1000, 2000, 'downloading')['speed_bps'], 50.)
        meter.record(100, 22.01, 'checking')
        meter.record(100, 60., 'connecting')
        self.assertFalse(meter.snapshot(60., 1000, 2000, 'connecting')['stalled'])
        self.assertTrue(meter.snapshot(68., 1000, 2000, 'connecting')['stalled'])

    def test_retry_status_is_visible(self):
        statuses = []
        item = dict(url=self.base+'/interrupt', bytes=len(PAYLOAD), sha256=hashlib.sha256(PAYLOAD).hexdigest())
        setup.download(item, self.target, self.cancel, lambda value: None, opener=self.opener, status=statuses.append)
        self.assertIn('retrying', statuses)
        self.assertEqual(statuses[-1], 'checking')

    def test_stalled_ui_updates_without_a_worker_event(self):
        from PySide6.QtCore import QCoreApplication
        from everytensor.model_setup import ModelSetup
        app = QCoreApplication.instance() or QCoreApplication([])
        with patch('everytensor.model_setup.time.monotonic', return_value=100.):
            model = ModelSetup(self.root)
        model._timer.stop()
        try:
            model._state.update(phase='downloading', received=0, total=1000)
            model._events.put(('progress', dict(phase='downloading', received=0, total=1000,
                                               transfer_state='downloading', network_received=0, measured_at=100.)))
            with patch('everytensor.model_setup.time.monotonic', return_value=100.):
                model.poll()
            model._events.put(('progress', dict(phase='downloading', received=100, total=1000,
                                               transfer_state='downloading', network_received=100, measured_at=102.)))
            with patch('everytensor.model_setup.time.monotonic', return_value=102.):
                model.poll()
            self.assertEqual(model.state['speed_bps'], 50.)
            with patch('everytensor.model_setup.time.monotonic', return_value=110.):
                model.poll()
            self.assertTrue(model.state['stalled'])
            self.assertEqual(model.state['speed_bps'], 0.)
            self.assertEqual(model.state['eta_seconds'], -1)
        finally:
            model.close()

    def test_pause_flushes_throttled_byte_count(self):
        from PySide6.QtCore import QCoreApplication
        from everytensor import model_setup
        app = QCoreApplication.instance() or QCoreApplication([])
        model = model_setup.ModelSetup(self.root)
        model._timer.stop()
        class PausingInstaller:
            def __init__(self, data, progress, cancel):
                self.progress = progress
            def run(self, local_model):
                for count in (111, 222):
                    self.progress(dict(phase='downloading', received=count, total=1000,
                                       network_received=count, transfer_state='downloading'))
                raise setup.Cancelled()
        try:
            with patch.object(model_setup, 'Installer', PausingInstaller), patch.object(model_setup.time, 'monotonic', return_value=100.):
                model.start()
                model._thread.join(timeout=2)
                self.assertFalse(model._thread.is_alive())
                model.poll()
            self.assertEqual(model.state['phase'], 'paused')
            self.assertEqual(model.state['received'], 222)
            self.assertEqual(model.state['speed_bps'], 0.)
        finally:
            model.close()

    def test_server_ignoring_range_restarts(self):
        self.partial.write_bytes(PAYLOAD[:123])
        self.download('/ignore-range')
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_invalid_range_never_published(self):
        self.partial.write_bytes(PAYLOAD[:123])
        with self.assertRaises(ValueError):
            self.download('/bad-range')
        self.assertFalse(self.target.exists())

    def test_checksum_failure_never_published(self):
        with self.assertRaises(ValueError):
            self.download(digest='0'*64)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.partial.exists())

    def test_disconnect_resumes(self):
        self.download('/interrupt')
        self.assertEqual(self.target.read_bytes(), PAYLOAD)
        self.assertEqual(self.server.ranges, [None, 'bytes=300000-'])

    def test_pause_preserves_partial_for_resume(self):
        def progress(value):
            if value >= 262144:
                self.cancel.set()
        with self.assertRaises(setup.Cancelled):
            self.download(progress=progress)
        self.assertFalse(self.target.exists())
        self.assertGreaterEqual(self.partial.stat().st_size, 262144)
        self.assertLess(self.partial.stat().st_size, len(PAYLOAD))
        self.cancel.clear()
        self.download()
        self.assertEqual(self.target.read_bytes(), PAYLOAD)

    def test_oversized_partial_is_restarted(self):
        self.partial.write_bytes(PAYLOAD+b'extra')
        self.download()
        self.assertEqual(self.server.ranges, [None])

    def test_archives_reject_traversal_and_symlinks(self):
        for name in ('../escaped', '/escaped', 'C:/escaped', '..\\escaped'):
            with self.subTest(name=name):
                archive = self.root / 'unsafe.zip'
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr(name, b'x')
                with self.assertRaises(ValueError):
                    setup.extract(archive, self.root / 'runtime', self.cancel)
        with zipfile.ZipFile(archive, 'w') as output:
            info = zipfile.ZipInfo('symlink')
            info.external_attr = 0o120777 << 16
            output.writestr(info, '../escaped')
        with self.assertRaises(ValueError):
            setup.extract(archive, self.root / 'runtime', self.cancel)
        self.assertFalse((self.root / 'escaped').exists())

    def test_wheel_layout(self):
        archive = self.root / 'package.whl'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('pkg/__init__.py', 'example')
            output.writestr('pkg.data/platlib/pkg/extension.pyd', 'dll')
            output.writestr('pkg.dist-info/LICENSE', 'license')
            output.writestr('pkg.data/scripts/unneeded.exe', 'script')
        target = self.root / 'runtime'
        setup.extract(archive, target, self.cancel, wheel=True)
        self.assertTrue((target / 'pkg/extension.pyd').exists())
        self.assertTrue((target / 'pkg.dist-info/LICENSE').exists())
        self.assertFalse((target / 'unneeded.exe').exists())

    def test_only_one_installer_owns_directory(self):
        with setup.installation_lock(self.root):
            with self.assertRaises(RuntimeError):
                with setup.installation_lock(self.root):
                    pass
        with setup.installation_lock(self.root):
            pass

    def test_failed_validation_does_not_publish_ready(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('python.exe', 'fixture')
        payload = archive.getvalue()
        assets = dict(id='fixture', working_bytes=0, artifacts=[dict(kind='python', name='python.zip',
                      bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), url='unused')])
        installer = setup.Installer(self.root, lambda event: None, assets=assets)
        destination = installer.root / 'downloads/python.zip'
        destination.parent.mkdir(parents=True)
        destination.write_bytes(payload)
        with patch.object(installer, 'validate', side_effect=RuntimeError('validation failed')):
            with self.assertRaisesRegex(RuntimeError, 'validation failed'):
                installer.run()
        self.assertFalse((installer.root / 'ready.json').exists())
        with patch.object(installer, 'validate'):
            installer.run()
        self.assertEqual(json.loads((installer.root / 'ready.json').read_text())['id'], 'fixture')

    def test_invalid_local_model_fails_before_network(self):
        installer = setup.Installer(self.root, lambda event: None)
        with patch.object(setup, 'download', side_effect=AssertionError('network must not start')):
            with self.assertRaisesRegex(ValueError, '已有模型'):
                installer.run(self.root / 'missing-model')


if __name__ == '__main__':
    unittest.main(verbosity=2)
