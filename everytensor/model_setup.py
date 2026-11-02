"""Qt-facing optional model setup state. Network/file work stays off the UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from .model_install import Installer, Cancelled, atomic_json, component_root, installed_configuration, manifest


class ModelSetup(QObject):
    changed = Signal()
    installed = Signal(str)

    def __init__(self, data, *, offer=False, parent=None):
        super().__init__(parent)
        self.data = Path(data)
        self.path = self.data / 'model-setup.json'
        self._assets = manifest()
        self._total = sum(a['bytes'] for a in self._assets['artifacts'])
        try:
            saved = json.loads(self.path.read_text('utf-8'))
        except (OSError, ValueError):
            saved = {}
        ready = installed_configuration(data)
        self._state = dict(phase='ready' if ready else 'paused' if saved.get('phase') in
                           ('downloading', 'installing', 'verifying', 'pausing', 'paused') else 'needed',
                           received=saved.get('received', 0), total=saved.get('total', self._total),
                           detail='', error='', seen=bool(saved.get('seen')),
                           local_model=saved.get('local_model', ''))
        self._offer = bool(offer and not ready and not self._state['seen'])
        self._thread = None
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._last_update = 0.
        self._last_phase = ''
        self._saved_at = 0.
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        os.environ['EVERYTENSOR_COMPONENTS'] = str(component_root(data))

    state = Property('QVariantMap', lambda s: dict(s._state), notify=changed)
    ready = Property(bool, lambda s: s._state['phase'] == 'ready', notify=changed)
    active = Property(bool, lambda s: s._state['phase'] in ('downloading', 'installing', 'verifying', 'pausing'), notify=changed)
    shouldOffer = Property(bool, lambda s: s._offer, notify=changed)
    downloadLabel = Property(str, lambda s: f'{s._total / 10**9:.2f} GB', constant=True)
    location = Property(str, lambda s: str(component_root(s.data)), constant=True)

    def _save(self):
        atomic_json(self.path, self._state)

    @Slot()
    def dismiss(self):
        self._offer = False
        self._state['seen'] = True
        self._save()
        self.changed.emit()

    @Slot()
    def start(self):
        self._start(self._state.get('local_model') or None)

    @Slot(str)
    def useLocalModel(self, path):
        if path:
            self._start(path)

    def _start(self, local_model):
        if self.ready or (self._thread is not None and self._thread.is_alive()):
            return
        self._cancel.clear()
        self._state.update(phase='verifying' if local_model else 'downloading', error='',
                           local_model=str(local_model or ''), detail='正在检查模型与可用空间', seen=True)
        self._offer = False
        self._save()
        self.changed.emit()
        def progress(event):
            now = time.monotonic()
            if now - self._last_update >= .1 or event['phase'] != self._last_phase:
                self._last_update, self._last_phase = now, event['phase']
                self._events.put(('progress', event))
        def work():
            try:
                result = Installer(self.data, progress, self._cancel).run(local_model)
                self._events.put(('done', str(result['model'])))
            except Cancelled:
                self._events.put(('paused', None))
            except Exception as exc:
                self._events.put(('error', str(exc)))
        self._thread = threading.Thread(target=work, name='optional-model-install')
        self._thread.start()

    @Slot()
    def pause(self):
        if self.active:
            self._cancel.set()
            self._state.update(phase='pausing', detail='正在暂停，已下载的文件会保留')
            self.changed.emit()

    @Slot()
    def downloadInstead(self):
        if not self.active:
            self._state['local_model'] = ''
            self._start(None)

    @Slot()
    def poll(self):
        updated = False
        while not self._events.empty():
            kind, value = self._events.get_nowait()
            if kind == 'progress':
                if self._state['phase'] != 'pausing':
                    self._state.update(value)
            elif kind == 'done':
                self._state.update(phase='ready', detail='多模态模型已安装，正在启用内容搜索', error='')
                self.installed.emit(value)
            elif kind == 'paused':
                self._state.update(phase='paused', detail='已暂停，下次可继续下载')
            elif kind == 'error':
                self._state.update(phase='error', error=value, detail='安装未完成；文件名和轻量文字搜索仍可使用')
            updated = True
        if updated:
            if not self.active or time.monotonic() - self._saved_at >= 1:
                self._save()
                self._saved_at = time.monotonic()
            self.changed.emit()

    def close(self):
        self._timer.stop()
        self._cancel.set()
        if self.active:
            self._state['phase'] = 'paused'
            self._save()
