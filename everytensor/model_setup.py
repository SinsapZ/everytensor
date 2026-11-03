"""Qt-facing optional model setup state. Network/file work stays off the UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from .model_install import Installer, Cancelled, atomic_json, component_root, installed_configuration, manifest


class TransferMeter:
    """A short moving window of network bytes, excluding cached/resumed bytes."""
    def __init__(self):
        self.samples = deque()
        self.last_transfer = time.monotonic()
        self.stage = None

    def record(self, count, now, stage='downloading'):
        # Verification/cache hashing may take minutes. Starting a connection
        # afterwards gets its own window instead of inheriting that idle time.
        network_stages = ('connecting', 'downloading')
        if stage in network_stages and self.stage not in network_stages:
            self.samples.clear()
            self.last_transfer = now
        self.stage = stage
        if self.samples and count < self.samples[-1][1]:
            self.samples.clear()
            self.last_transfer = now
        if self.samples and count > self.samples[-1][1]:
            self.last_transfer = now
        self.samples.append((now, count))
        while len(self.samples) > 2 and self.samples[1][0] < now - 8:
            self.samples.popleft()

    def snapshot(self, now, received, total, stage):
        stalled = stage in ('connecting', 'downloading') and now - self.last_transfer >= 8
        speed = 0.
        if stage == 'downloading' and not stalled and len(self.samples) > 1:
            start, first = self.samples[0]
            elapsed = now - start
            if elapsed >= .5:
                speed = max(0, self.samples[-1][1] - first) / elapsed
        remaining = max(0, total - received)
        eta = remaining / speed if speed > 0 and remaining else -1
        return dict(speed_bps=speed, eta_seconds=eta, stalled=stalled)


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
                           local_model=saved.get('local_model', ''), speed_bps=0., eta_seconds=-1,
                           stalled=False, transfer_state='connecting', completed_files=0, file_count=0)
        self._offer = bool(offer and not ready and not self._state['seen'])
        self._thread = None
        self._cancel = threading.Event()
        self._events = queue.Queue()
        self._last_update = 0.
        self._last_phase = ''
        self._last_transfer_state = ''
        self._latest_progress = None
        self._saved_at = 0.
        self._meter = TransferMeter()
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
        self._meter = TransferMeter()
        self._last_update = 0.
        self._last_phase = ''
        self._last_transfer_state = ''
        self._latest_progress = None
        self._state.update(phase='verifying' if local_model else 'downloading', error='',
                           local_model=str(local_model or ''), detail='正在检查模型与可用空间', seen=True,
                           speed_bps=0., eta_seconds=-1, stalled=False, transfer_state='connecting',
                           completed_files=0, file_count=0, active_files=0)
        self._offer = False
        self._save()
        self.changed.emit()
        def progress(event):
            now = time.monotonic()
            self._latest_progress = dict(event, measured_at=now)
            stage = event.get('transfer_state', '')
            if now - self._last_update >= .1 or event['phase'] != self._last_phase or stage != self._last_transfer_state:
                self._last_update, self._last_phase = now, event['phase']
                self._last_transfer_state = stage
                self._events.put(('progress', dict(self._latest_progress)))
        def finish(kind, value):
            # Do not lose the last byte count when throttling rapid callbacks.
            if self._latest_progress:
                self._events.put(('progress', dict(self._latest_progress)))
            self._events.put((kind, value))
        def work():
            try:
                result = Installer(self.data, progress, self._cancel).run(local_model)
                finish('done', str(result['model']))
            except Cancelled:
                finish('paused', None)
            except Exception as exc:
                finish('error', str(exc))
        self._thread = threading.Thread(target=work, name='optional-model-install')
        self._thread.start()

    @Slot()
    def pause(self):
        if self.active:
            self._cancel.set()
            self._state.update(phase='pausing', detail='正在暂停，已下载的文件会保留；连接无响应时需要等待当前请求结束',
                               speed_bps=0., eta_seconds=-1, stalled=False)
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
                    if 'network_received' in value:
                        self._meter.record(value['network_received'], value.pop('measured_at', time.monotonic()),
                                           value.get('transfer_state', 'connecting'))
                    self._state.update(value)
                else:
                    self._state.update({key: value[key] for key in ('received', 'total', 'completed_files', 'file_count') if key in value})
            elif kind == 'done':
                self._state.update(phase='ready', detail='多模态模型已安装，正在启用内容搜索', error='')
                self.installed.emit(value)
            elif kind == 'paused':
                self._state.update(phase='paused', detail='已暂停，下次可继续下载')
            elif kind == 'error':
                self._state.update(phase='error', error=value, detail='安装未完成；文件名和轻量文字搜索仍可使用')
            updated = True
        if self._state['phase'] == 'downloading':
            telemetry = self._meter.snapshot(time.monotonic(), self._state['received'], self._state['total'],
                                             self._state.get('transfer_state', 'connecting'))
            # Update a stalled connection even when the worker emits no bytes.
            if updated or telemetry['stalled'] != self._state.get('stalled'):
                self._state.update(telemetry)
                updated = True
        elif updated:
            self._state.update(speed_bps=0., eta_seconds=-1, stalled=False)
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
