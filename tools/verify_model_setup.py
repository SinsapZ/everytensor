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
