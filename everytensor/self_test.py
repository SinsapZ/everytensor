"""Exercise installed resources and real child processes using disposable data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import tempfile
import time
import traceback
from contextlib import nullcontext
from unittest.mock import patch


def run(report_path):
    from . import RELEASE_VERSION
    from .paths import default_data, frozen, resource_root
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(version=RELEASE_VERSION, frozen=frozen(), checks=[], ok=False,
                  default_data=str(default_data()))
    runtime = backend = renderer = None
    started = time.monotonic()
    try:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        os.environ.setdefault('QT_QUICK_BACKEND', 'software')
        from . import store, indexer, quick_index
        from .backend import Backend
        from .pdf_render import PdfRenderer
        from .quick_embeddings import QuickEmbedder
        from .desktop import create_desktop
        import fitz
        import numpy as np
        from PIL import Image
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFontDatabase

        with tempfile.TemporaryDirectory(prefix='EveryTensor-selftest-', ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            samples = root / '中文路径 with spaces'
            samples.mkdir()
            sample = samples / '安装验证.txt'
            sample.write_text('海豚生活在海洋中。\nEveryTensor installer search verification.\n', 'utf-8')
            pdf = samples / '预览测试.pdf'
            with fitz.open() as document:
                document.new_page().insert_text((40, 60), 'EveryTensor PDF preview')
                document.save(pdf)
            Image.new('RGB', (48, 32), '#a43d35').save(samples / '图片.png')
            db_path = root / '.everytensor' / 'index.sqlite3'
            db = store.connect(db_path)
            store.set_setting(db, 'scope', 'folders')
            store.set_setting(db, 'roots', [str(samples)])
            quick_index.resolve_model(db, resource_root() / 'models' / 'quick-semantic')
            indexer.scan(db, str(samples))
            indexer.index_pending(db)
            assert store.names(db, '安装验证'), 'Unicode filename search returned nothing'
            assert store.keywords(db, 'installer'), 'Content search returned nothing'
            db.close()
            report['checks'].append('unicode_filename_and_content_search')

            model = QuickEmbedder()
            vectors = model.encode_texts(['海豚生活在海洋中', '海里的动物', '安装软件'])
            assert vectors.shape == (3, 256) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
            del model, vectors
            report['checks'].append('bundled_model_checksums_and_inference')

            events = queue.Queue()
            backend = Backend(db_path, events)
            deadline = time.monotonic() + 60
            ready = False
            result = None
            while time.monotonic() < deadline:
                if ready:
                    backend.put(dict(action='search', id=7, query='海豚', kind='', limit=10, channels=True))
                try:
                    event = events.get(timeout=.5)
                except queue.Empty:
                    continue
                assert event.get('type') != 'fatal', event
                if event.get('type') == 'quick_model':
                    assert event.get('ready'), event
                    ready = True
                if event.get('type') == 'results' and event.get('id') == 7:
                    assert not event.get('error'), event
                    if event.get('semantic_rows') and event.get('semantic_stage') == 'quick':
                        result = event
                        break
            assert ready and result, 'Installed worker did not return semantic search results'
            backend.close(wait=True)
            assert backend.process.poll() == 0, 'Search worker did not shut down cleanly'
            backend = None
            report['checks'].append('search_worker_semantic_query_and_shutdown')

            renderer = PdfRenderer(timeout=20)
            width, height, stride, pixels = renderer.render(pdf, 1, (320, 240))
            assert width > 0 and height > 0 and len(pixels) == stride * height
            renderer.close()
            renderer = None
            report['checks'].append('pdf_worker_render')

            # Load QML and start the actual desktop transport on the same isolated
            # small library; never inherit or scan the real user's search roots.
            # A developer checkout can have an unrelated full model beside it.
            # Source tests isolate that optional tier; frozen tests discover the
            # actual shipped model resources without overriding discovery.
            isolated = patch('everytensor.store.resolve_model', return_value='') if not frozen() else nullcontext()
            app = QApplication.instance() or QApplication([])
            # Qt's offscreen Windows platform does not discover installed fonts.
            # Production uses the windows platform and needs no font override.
            if os.environ.get('QT_QPA_PLATFORM') == 'offscreen':
                for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
                    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
                    if font.is_file():
                        QFontDatabase.addApplicationFont(str(font))
            with isolated:
                runtime = create_desktop(db_path.parent, restore_geometry=False, offer_setup=False)
            deadline = time.monotonic() + 30
            while runtime.client._model_state == 'preparing' and time.monotonic() < deadline:
                QTest.qWait(50)
            assert runtime.window and runtime.window.width() > 0
            assert runtime.client.commands.process.poll() is None
            assert runtime.client._model_state != 'error', runtime.client.modelStatus
            QTest.qWait(300)
            assert not runtime.warnings, runtime.warnings
            from PySide6.QtCore import QObject
            from PySide6.QtQml import QQmlExpression, QQmlEngine
            expression = QQmlExpression(QQmlEngine.contextForObject(runtime.window), runtime.window, 'window.openModelSetup()')
            expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            QTest.qWait(200)
            assert runtime.window.findChild(QObject, 'modelSetupDialog').property('opened')
            assert runtime.model_setup.state['phase'] == 'needed'
            report['checks'].append('optional_model_setup_dialog_without_network')
            runtime.window.grabWindow().save(str(report_path.with_suffix('.png')))
            runtime.client.commands.close(wait=True)
            runtime.close()
            runtime = None
            report['checks'].append('qml_desktop_and_backend_startup')
            assert (resource_root() / 'everytensor' / 'office_open.ps1').is_file()
            report['checks'].append('office_helper_resource')
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if backend is not None:
            backend.close(wait=True)
        if renderer is not None:
            renderer.close()
        if runtime is not None:
            runtime.client.commands.close(wait=True)
            runtime.close()
        report['seconds'] = round(time.monotonic() - started, 2)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    if not report['ok']:
        raise RuntimeError('Self-test failed; see ' + str(report_path))
    return 0
