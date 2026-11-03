"""Toolkit-independent search behavior exposed to the Qt Quick desktop.

The model worker and the SQLite schema remain the source of truth. Qt owns only
presentation state; slow readers run in their own connections and every reply is
versioned, including previews and settings acknowledgements.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from . import catalog, media, store, locations
from .backend import Backend, NullBackend
from .client_models import ResultModel, location_label


class Controller(QObject):
    stateChanged = Signal()
    selectedChanged = Signal()
    previewRequested = Signal(object)
    settingsApplied = Signal()
    errorOccurred = Signal(str)
    notification = Signal(str)
    rowsAboutToChange = Signal()
    rowsChanged = Signal()
    _modelActivated = Signal(str)

    def __init__(self, data: Path, start_backend=True, parent=None):
        super().__init__(parent)
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data/'index.sqlite3'
        self.db = store.connect(self.db_path)
        self._modelActivated.connect(self._finish_model_activation)
        self._activating_model = False
        catalog.initialize_scope(self.db, self.data)
        if start_backend:
            # A custom --data directory still uses the bundled local models;
            # this only inspects manifests and never imports the inference stack.
            from . import quick_index
            bundled = Path(__file__).resolve().parent.parent/'models'
            from .model_install import installed_configuration
            installed = installed_configuration(self.data)
            store.resolve_model(self.db, installed['model'] if installed else bundled/'embeddinggemma-2')
            quick_index.resolve_model(self.db, bundled/'quick-semantic')
        self.log = logging.getLogger(f'everytensor.client.{id(self)}')
        self._log_handler = RotatingFileHandler(self.data/'ui.log', maxBytes=2*1024**2,
                                                backupCount=1, encoding='utf-8')
        self._log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.log.addHandler(self._log_handler)
        self.log.setLevel(logging.INFO)
        self._query = self._kind = ''
        self._generation = self._preview_token = 0
        self._manual_generation = -1
        self._position_choice = None
        self._selected = {}
        self._selection_origin = None
        self._rows = []
        self._model = ResultModel(self)
        self._visible, self._limit = 100, 500
        self._channels = {name: [] for name in ('names', 'keywords', 'semantic')}
        self._more = dict.fromkeys(self._channels, False)
        self._pending = set()
        self._inflight = False
        self._refreshing = self._dirty = False
        self._last_refresh = 0.
        self._semantic_stage = ''
        self._semantic_coverage = {}
        self._search_error = self._warning = ''
        self._channel_errors = {}
        self._scope_pending = True
        self._paused = False
        self._closing = False
        self._settings_applying = False
        self._settings_error = ''
        self._settings_request = None
        self._settings = self._read_settings()
        self._stats = store.load_coverage(self.db)
        self._model_state = 'preparing' if start_backend else 'missing'
        self._model_status = '正在准备本地搜索' if start_backend else '本地文件搜索'
        self._last_status = '已恢复上次进度' if self._stats else '正在读取索引进度'
        self._cancel = threading.Event()
        self._pools = {name: concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='client-'+name)
                       for name in ('names', 'keywords')}
        self._jobs = {}
        self.futures = queue.Queue()
        self.events = queue.Queue()
        self.commands = Backend(self.db_path, self.events) if start_backend else NullBackend()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._issue_semantic)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self.poll)
        self._poll_timer.start()

    def _read_settings(self):
        return dict(scope=store.setting(self.db, 'scope', 'computer'),
                    roots=store.setting(self.db, 'roots', []),
                    exclusions=store.setting(self.db, 'exclusions', []),
                    dimensions=store.setting(self.db, 'dimensions', 256),
                    budget_gib=store.setting(self.db, 'budget_gib', 8),
                    model=store.setting(self.db, 'model', ''),
                    localDrives=catalog.local_drives())

    @Property(str, notify=stateChanged)
    def query(self): return self._query

    @Property(str, notify=stateChanged)
    def kind(self): return self._kind

    @Property(QObject, constant=True)
    def results(self): return self._model

    @Property(int, notify=selectedChanged)
    def selectedId(self): return self._selected.get('file_id', -1)

    @Property('QVariantMap', notify=selectedChanged)
    def selected(self):
        if not self._selected:
            return {}
        value = dict(self._selected)
        value.update(ResultModel.presentation(self._selected, self._query))
        return value

    @property
    def selectedRaw(self): return copy.deepcopy(self._selected)

    @property
    def generation(self): return self._generation

    @property
    def rows(self): return self._rows

    @Property(bool, notify=stateChanged)
    def pending(self): return bool(self._pending or self._inflight)

    @Property(bool, notify=stateChanged)
    def manualSelection(self): return self._manual_generation == self._generation

    @Property(str, notify=stateChanged)
    def semanticStage(self): return self._semantic_stage

    @Property(str, notify=stateChanged)
    def resultLabel(self):
        if not self._rows and self.pending:
            return '正在查找…'
        total = f'{len(self._rows):,}' + ('+' if any(self._more.values()) else '')
        count = (f'已显示 {self._model.count:,} / {total}'
                 if self._model.count < len(self._rows) else total)
        parts = [count + ' 个结果']
        if self._refreshing:
            parts.append('正在更新')
        elif 'semantic' in self._pending:
            parts.append('正在检索语义')
        elif self.pending:
            parts.append('正在查找')
        elif self._search_error:
            parts.append('部分搜索未完成')
        elif self._semantic_stage == 'quick':
            parts.append('快速语义（文字）')
        elif self._content_incomplete() and self._rows:
            parts.append('内容索引中')
        return ' · '.join(parts)

    @Property(str, notify=stateChanged)
    def searchError(self): return self._search_error

    @Property(bool, notify=stateChanged)
    def refreshing(self): return self._refreshing

    def _set_search_error(self, channel, error=''):
        if error:
            self._channel_errors[channel] = error
        else:
            self._channel_errors.pop(channel, None)
        self._search_error = ' · '.join(self._channel_errors.values())

    @Property(str, notify=stateChanged)
    def emptyMessage(self):
        if self._search_error:
            return self._search_error
        if self.pending:
            return '正在准备语义搜索' if self._model_state in ('preparing', 'loading') else '正在查找'
        if self._scope_pending or self._content_incomplete():
            return '暂未找到，内容索引仍在继续'
        return '没有匹配的文件'

    @Property(bool, notify=stateChanged)
    def canLoadMore(self): return len(self._rows) > self._visible or any(self._more.values())

    @Property(str, notify=stateChanged)
    def modelState(self): return self._model_state

    @Property(str, notify=stateChanged)
    def modelStatus(self): return self._model_status

    @Property('QVariantMap', notify=stateChanged)
    def stats(self): return self._stats

    @Property('QVariantMap', notify=stateChanged)
    def settings(self): return self._settings

    @Property(bool, notify=stateChanged)
    def settingsApplying(self): return self._settings_applying

    @Property(str, notify=stateChanged)
    def settingsError(self): return self._settings_error

    @Property(bool, notify=stateChanged)
    def paused(self): return self._paused

    @Property(str, notify=stateChanged)
    def warning(self): return self._warning

    @Property(str, notify=stateChanged)
    def indexStatus(self): return self._last_status

    def _content_incomplete(self):
        states = self._stats.get('content_states', {})
        return bool(self._stats.get('scanning') or self._stats.get('blocked') or
                    states.get('pending', 0) or states.get('error', 0) or
                    self._stats.get('covered_units', 0) < self._stats.get('units', 0))

    @Slot(str)
    def setQuery(self, value):
        if self._closing or value == self._query:
            return
        self._query = value
        requested = store.query_intent(value)[0]
        if not requested and Path(value.strip()).suffix:
            guessed = media.kind(value.strip())
            requested = guessed if guessed != 'other' else ''
        if requested:
            self._kind = requested
        self._changed()

    @Slot(str)
    def setKind(self, value):
        if self._closing or value == self._kind or value not in ('', 'document', 'image', 'video', 'code', 'audio'):
            return
        self._kind = value
        self._changed()

    def _cancel_readers(self):
        self._cancel.set()
        self._cancel = threading.Event()
        for job in self._jobs.values():
            job.cancel()
        self.commands.put({'action': 'cancel_search', 'id': self._generation})

    def _changed(self):
        self._generation += 1
        self._manual_generation = -1
        self._position_choice = None
        self._visible, self._limit = 100, 500
        self._debounce.stop()
        self._cancel_readers()
        self._channels = dict(names=[], keywords=[], semantic=[])
        self._more = dict.fromkeys(self._channels, False)
        self._pending.clear()
        self._inflight = self._refreshing = self._dirty = False
        self._semantic_stage, self._semantic_coverage = '', {}
        self._search_error = ''
        self._channel_errors.clear()
        self._publish([])
        if self._query.strip() or self._kind:
            self._issue_lexical()
            if self._query.strip():
                self._debounce.start()
        self.stateChanged.emit()

    def _issue_lexical(self):
        generation, query, kind, limit = self._generation, self._query, self._kind, self._limit+1
        cancel, path, output = self._cancel, self.db_path, self.futures
        self._pending.update(('names', 'keywords'))
        if query.strip():
            self._pending.add('semantic')
        for channel in ('names', 'keywords'):
            old = self._jobs.get(channel)
            if old:
                old.cancel()
            def task(name=channel):
                if cancel.is_set():
                    return []
                db = store.reader(path)
                db.set_progress_handler(lambda: int(cancel.is_set()), 1000)
                try:
                    if name == 'names':
                        for rows, complete in store.name_batches(db, query, kind, limit, cancel.is_set):
                            if cancel.is_set():
                                return []
                            if complete:
                                return rows
                            partial = concurrent.futures.Future()
                            partial.set_result(rows)
                            output.put(('names_partial', generation, partial))
                        return []
                    return store.keywords(db, query, kind, limit, cancelled=cancel.is_set)
                finally:
                    db.close()
            future = self._pools[channel].submit(task)
            self._jobs[channel] = future
            future.add_done_callback(lambda f, c=channel, g=generation: output.put((c, g, f)))

    @Slot()
    def _issue_semantic(self):
        self._debounce.stop()
        if not self._query.strip() or self._closing:
            return
        self._pending.add('semantic')
        if self._inflight:
            self._dirty = True
            return
        self._inflight = True
        self.commands.put(dict(action='search', id=self._generation, query=self._query,
                               kind=self._kind, limit=self._limit+1, channels=True))
        self.stateChanged.emit()

    def _set_channel(self, channel, rows, complete=True):
        accepted, seen, more = [], set(), False
        for row in rows:
            fid = row['file_id']
            if fid not in seen:
                if len(seen) >= self._limit:
                    more = True
                    continue
                seen.add(fid)
            accepted.append(row)
        self._channels[channel] = accepted
        if complete:
            self._more[channel] = more

    def _waiting_refresh(self):
        return bool(self._pending.intersection(('names', 'keywords')) or self._inflight)

    def _merge(self):
        if self._refreshing and self._waiting_refresh():
            self.stateChanged.emit()
            return
        self._refreshing = False
        rows = store.fuse(self._channels['names'], self._channels['keywords'],
                          self._channels['semantic'], query=self._query)
        self._publish(rows)

    def _publish(self, rows):
        manual = self._manual_generation == self._generation
        selected_id = self.selectedId if manual else -1
        self._rows = rows
        selected = next((row for row in rows if row['file_id'] == selected_id), None)
        if selected is not None:
            index = next(i for i, row in enumerate(rows) if row['file_id'] == selected_id)
            if index >= self._visible:
                self._visible = (index//100+1)*100
        self.rowsAboutToChange.emit()
        self._model.replace(rows[:self._visible], self._query)
        self.rowsChanged.emit()
        self._select(selected or (rows[0] if rows else {}))
        self.stateChanged.emit()

    def _select(self, row):
        origin = ((row.get('file_id'), row.get('unit_id'), row.get('path'), row.get('size'), row.get('mtime'),
                   json.dumps(row.get('location', {}), sort_keys=True), row.get('text'), row.get('error'))
                  if row else None)
        if origin == self._selection_origin and row:
            hits_changed = self._selected.get('hits') != row.get('hits')
            location = self._selected.get('location', {})
            self._selected = copy.deepcopy(row)
            self._selected['location'] = location
            self.selectedChanged.emit()
            if hits_changed:
                self._preview_token += 1
                self.previewRequested.emit(dict(row=self.selectedRaw, token=self._preview_token))
            return
        if not row and not self._selected:
            return
        chosen_location = None
        identity = tuple(row.get(key) for key in ('file_id', 'path', 'size', 'mtime', 'kind'))
        if row and identity == self._position_choice:
            chosen_location = self._selected.get('location', {})
        else:
            self._position_choice = None
        self._selection_origin = origin
        self._selected = copy.deepcopy(row)
        if chosen_location is not None:
            self._selected['location'] = chosen_location
        self._preview_token += 1
        self.selectedChanged.emit()
        self.previewRequested.emit(dict(row=self.selectedRaw, token=self._preview_token))

    @Slot(int, bool)
    def selectFile(self, file_id, user=True):
        if user:
            self.markInteraction()
        row = next((row for row in self._rows if row['file_id'] == file_id), None)
        if row:
            self._select(row)

    @Slot()
    def markInteraction(self):
        if self._manual_generation != self._generation:
            self._manual_generation = self._generation
            self.stateChanged.emit()

    @Slot('QVariantMap')
    def setSelectedLocation(self, location):
        if not self._selected:
            return
        self.markInteraction()
        if self._selected.get('kind') in ('audio', 'video'):
            self._position_choice = tuple(self._selected.get(key)
                                          for key in ('file_id', 'path', 'size', 'mtime', 'kind'))
        self._selected['location'] = dict(location)
        self._preview_token += 1
        self.selectedChanged.emit()
        self.previewRequested.emit(dict(row=self.selectedRaw, token=self._preview_token))

    @Slot('QVariantMap')
    def resolveSelectedLocation(self, location):
        """Carry preview-discovered anchors to open/copy without user selection."""
        if not self._selected:
            return
        resolved = dict(location, **self._selected.get('location', {}))
        if self._selected.get('kind') in ('video', 'audio'):
            # The preview validates media bounds and may seek back from an
            # undecodable EOF. Open/copy must use the frame actually displayed.
            resolved.update({key: location[key] for key in ('start', 'end', 'duration') if key in location})
        if resolved != self._selected.get('location', {}):
            self._selected['location'] = resolved
            self.selectedChanged.emit()

    @Slot()
    def refreshSelected(self):
        self._preview_token += 1
        self.previewRequested.emit(dict(row=self.selectedRaw, token=self._preview_token))

    @Slot()
    def loadMore(self):
        if not self.canLoadMore:
            return
        self.markInteraction()
        self._visible += 100
        if self._visible > self._limit and any(self._more.values()):
            self._limit += 500
            self._refresh()
        self._publish(self._rows)

    @Slot()
    def retrySearch(self):
        if not self._closing and not self.pending and (self._query.strip() or self._kind):
            self._refresh()

    def _refresh(self):
        manual = self._manual_generation == self._generation
        self._generation += 1
        if manual:
            self._manual_generation = self._generation
        self._cancel_readers()
        self._channels = dict(names=[], keywords=[], semantic=[])
        self._more = dict.fromkeys(self._channels, False)
        self._semantic_stage, self._semantic_coverage = '', {}
        self._pending.clear()
        self._inflight = False
        self._refreshing, self._dirty = True, False
        self._last_refresh = time.monotonic()
        self._search_error = ''
        self._channel_errors.clear()
        self._issue_lexical()
        self._issue_semantic()
        self.stateChanged.emit()

    @Slot('QVariantMap')
    def applySettings(self, values):
        if self._settings_applying or self._closing:
            return
        try:
            current = dict(self._settings)
            current.update(dict(values))
            scope = current['scope']
            if scope not in ('computer', 'folders'):
                raise ValueError('请选择有效的搜索范围。')
            roots = current.get('roots', [])
            exclusions = current.get('exclusions', [])
            if not isinstance(roots, list) or not isinstance(exclusions, list) or not all(isinstance(p, str) for p in roots+exclusions):
                raise ValueError('请使用有效的文件夹路径。')
            if scope == 'folders' and not roots:
                raise ValueError('请至少添加一个搜索文件夹。')
            dimensions = int(current['dimensions'])
            if dimensions not in (256, 512, 768):
                raise ValueError('请选择 256、512 或 768 维。')
            budget = float(current['budget_gib'])
            if not .125 <= budget <= 1024:
                raise ValueError('索引预算应在 0.125–1024 GiB 之间。')
            model_path = None
            if 'model' in values and current.get('model') != self._settings.get('model', ''):
                requested_model = current.get('model')
                if not isinstance(requested_model, str) or not requested_model.strip():
                    raise ValueError('请选择完整的本地 EmbeddingGemma 2 模型文件夹。')
                model_folder = Path(requested_model).expanduser().resolve()
                try:
                    config = json.loads((model_folder/'config.json').read_text(encoding='utf-8'))
                except (OSError, ValueError) as exc:
                    raise ValueError('所选文件夹没有可读取的模型 config.json。') from exc
                if not isinstance(config, dict) or config.get('model_type') != 'embedding_gemma2':
                    raise ValueError('所选文件夹不是 EmbeddingGemma 2 模型。')
                model_path = str(model_folder)
            settings = dict(scope=scope, roots=catalog.compact_roots(roots),
                            exclusions=catalog.compact_roots(exclusions), dimensions=dimensions, budget_gib=budget)
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
            self._settings_error = str(exc)
            self.stateChanged.emit()
            return
        self._settings_error = ''
        self._settings_applying = True
        self._settings_request = settings
        if model_path is not None:
            self.commands.put({'action': 'model', 'path': model_path})
            self._model_state, self._model_status = 'preparing', '正在切换本地模型'
        self.commands.put({'action': 'configure', 'settings': settings})
        self.stateChanged.emit()

    @Slot(bool)
    def pauseIndexing(self, value):
        self._paused = bool(value)
        self.commands.put({'action': 'pause', 'value': self._paused})
        self.stateChanged.emit()

    @Slot()
    def rescan(self):
        self._scope_pending = True
        self._last_status = '正在检查文件变化'
        self.commands.put({'action': 'scan'})
        self.stateChanged.emit()

    @Slot(str)
    def setModelPath(self, path):
        if not path:
            return
        self.commands.put({'action': 'model', 'path': catalog.canonical(path)})
        self._model_state, self._model_status = 'preparing', '正在切换本地模型'
        self._settings['model'] = path
        self.stateChanged.emit()

    def _error(self, text):
        self._warning = text
        self.log.error(text)
        self.errorOccurred.emit(text)
        self.stateChanged.emit()

    @Slot()
    def openSelected(self):
        if not self._selected:
            return
        row = copy.deepcopy(self._selected)
        if 'open' not in self._pools:
            self._pools['open'] = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='open-file')
        def open_file():
            try:
                note = locations.open_hit(row)
            except Exception as exc:
                note = '无法打开文件：'+str(exc)
            if note and not self._closing:
                self.events.put({'type': 'warning', 'text': note})
        self._pools['open'].submit(open_file)

    @Slot()
    def copyLocation(self):
        if self._selected:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(locations.reference(self._selected))
            self.notification.emit('已复制文件位置')

    @Slot()
    def revealSelected(self):
        if not self._selected:
            return
        try:
            path = self._selected['path']
            if os.name == 'nt':
                subprocess.Popen(['explorer', '/select,', path])
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(Path(path).parent)])
        except Exception as exc:
            self._error('无法显示文件位置：'+str(exc))

    def _handle_event(self, event):
        typ = event.get('type')
        if typ == 'results' and event.get('id') == self._generation:
            if (self._semantic_stage == 'full' and event.get('semantic_stage') == 'quick'
                    and not event.get('full_checked')):
                return
            pending, partial = event.get('semantic_pending', False), event.get('partial', False)
            if not partial:
                self._inflight = False
                if not pending:
                    self._pending.discard('semantic')
            self._set_search_error('semantic', ('语义查询失败：'+event['error'][:120]) if event.get('error') else '')
            if self._search_error:
                self.log.error(self._search_error)
            if event.get('channels'):
                if not pending:
                    self._set_channel('semantic', event.get('semantic_rows', []), complete=not partial)
                    self._semantic_stage = event.get('semantic_stage', 'full')
                    self._semantic_coverage = event.get('semantic_coverage', {})
                self._merge()
            else:
                self._publish(event.get('rows', []))
        elif typ == 'model':
            self._model_state = event.get('state', 'ready' if event.get('ready') else 'error')
            self._model_status = event.get('text', '')
            if self._model_state in ('indexing', 'ready') and self._query.strip():
                self._issue_semantic()
        elif typ == 'quick_model':
            if event.get('state') == 'ready' or event.get('ready'):
                if self._model_state == 'missing':
                    self._model_state, self._model_status = 'partial', '快速语义已就绪'
                if self._query.strip():
                    self._issue_semantic()
            elif event.get('state') == 'error' or event.get('ready') is False:
                self._warning = event.get('error') or event.get('text') or '快速语义暂不可用'
        elif typ == 'indexed':
            self._stats = event['stats']
            self._scope_pending = False
            self._dirty = True
        elif typ == 'index_changed':
            self._dirty = True
        elif typ == 'configured':
            self._settings_applying = False
            self._settings_error = ''
            # Read only after the worker has committed the authoritative values.
            self._settings = self._read_settings()
            self._settings_request = None
            self._stats = {}
            self._scope_pending = True
            if self._query.strip() or self._kind:
                self._changed()
            self.settingsApplied.emit()
        elif typ == 'status':
            self._last_status = event.get('text', '')
        elif typ in ('fatal', 'warning'):
            self._warning = event.get('text', '')
            self.log.error('%s: %s', typ, self._warning)
            if self._settings_applying:
                self._settings_applying = False
                self._settings_error = self._warning
                self._settings_request = None
            if typ == 'fatal':
                self._model_state, self._model_status = 'error', self._warning
                self._inflight = False
                self._pending.discard('semantic')
                self._set_search_error('semantic', self._warning)
                self._merge()
            self.errorOccurred.emit(self._warning)
        self.stateChanged.emit()

    @Slot()
    def poll(self):
        if self._closing:
            return
        deadline = time.monotonic()+.006
        while time.monotonic() < deadline:
            try:
                typ, generation, future = self.futures.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation or future.cancelled():
                continue
            try:
                rows = future.result()
                channel = 'names' if typ == 'names_partial' else typ
                if typ != 'names_partial':
                    self._pending.discard(channel)
                    self._set_search_error(channel)
                self._set_channel(channel, rows, complete=typ != 'names_partial')
                if channel in ('names', 'keywords'):
                    candidates = [row for row in self._channels['keywords'] if row.get('content_source') == 'live']
                    candidates.extend(self._channels['names'])
                    self.commands.put(dict(action='prioritize', id=generation, kind=self._kind,
                                           files=list(dict.fromkeys(row['file_id'] for row in candidates))[:64]))
                self._merge()
            except concurrent.futures.CancelledError:
                pass
            except Exception:
                self.log.exception('%s query failed', typ)
                self._pending.discard('names' if typ == 'names_partial' else typ)
                self._set_search_error('names' if typ == 'names_partial' else typ,
                                       '文件名搜索遇到问题，请重试' if typ.startswith('names') else '正文搜索遇到问题，请重试')
                self._merge()
        deadline = time.monotonic()+.006
        while time.monotonic() < deadline:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception:
                self.log.exception('Worker event failed: %s', event.get('type'))
        if (self._dirty and (self._query.strip() or self._kind) and not self._debounce.isActive()
                and not self._waiting_refresh() and time.monotonic()-self._last_refresh >= 1.):
            self._refresh()

    @Slot(str)
    def _finish_model_activation(self, path):
        if self._closing:
            return
        self._activating_model = False
        try:
            pending = list(self.commands.messages) if isinstance(self.commands, NullBackend) else []
            store.set_setting(self.db, 'model', path)
            self._settings = self._read_settings()
            self.events = queue.Queue()
            self.commands = Backend(self.db_path, self.events)
            for command in pending:
                self.commands.put(command)
            if self._settings_request and not any(c.get('action') == 'configure' for c in pending):
                self.commands.put({'action': 'configure', 'settings': self._settings_request})
            self._model_state, self._model_status = 'preparing', '正在启用多模态搜索'
            self._inflight = False
            self._dirty = True
            if self._paused:
                self.commands.put({'action': 'pause', 'value': True})
            self.notification.emit('多模态模型已安装，正在准备图片和音视频搜索')
        except Exception as exc:
            self._warning = '启动多模态搜索失败，请重新打开应用：' + str(exc)
            self._settings_applying = False
            self._settings_error = self._warning
        self.stateChanged.emit()

    @Slot(str)
    def activateInstalledModel(self, path):
        if self._closing or self._activating_model:
            return
        self._activating_model = True
        previous = self.commands
        self.commands = NullBackend()
        self._inflight = False
        self._model_status = '正在启用多模态搜索；文件名和正文搜索可用'
        self.stateChanged.emit()
        def replace():
            previous.close(wait=True)
            self._modelActivated.emit(path)
        threading.Thread(target=replace, name='activate-model-backend').start()

    @Slot()
    def close(self):
        if self._closing:
            return
        self._closing = True
        self._debounce.stop()
        self._poll_timer.stop()
        self._cancel.set()
        self.commands.close()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        self.db.close()
        self.log.removeHandler(self._log_handler)
        self._log_handler.close()
