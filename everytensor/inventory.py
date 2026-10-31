"""Independent discovery and content producers; GPU inference remains single-owner."""
from __future__ import annotations

from pathlib import Path
from collections import OrderedDict
import threading
import time
import logging
import concurrent.futures
from . import catalog, indexer, storage, store, frontier, inference
from .watcher import DirectoryWatcher
from .scheduling import ContentQueue

log = logging.getLogger(__name__)


class Restart(Exception):
    pass


class Inventory:
    def __init__(self, path, roots, status):
        self.path, self.roots, self.status = path, list(roots), status
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.paused = False
        self.generation = 0
        self.pruned_generation = -1
        self.revision = 0
        self.full = True
        self.restart_discovery = False
        self.changes = set()
        self.live_changes = OrderedDict()
        self.recent_files = OrderedDict()
        self.seed_files = ()
        self.exclusions = []
        self.discovery = '正在恢复文件核对进度'
        self.content = '正在恢复内容分析进度'
        self.blocked = {}
        self.scanning = True
        self.extracting = False
        self.watchers = []
        self.query_id, self.query_kind, self.preferred = -1, '', ()
        self.priority_until = 0.
        self.backpressured = False
        self.embedding_expected = None
        self.threads = [threading.Thread(target=self._discover, daemon=True, name='file-discovery'),
                        threading.Thread(target=self._names, daemon=True, name='filename-index'),
                        threading.Thread(target=self._extract, daemon=True, name='content-extraction')]
        for thread in self.threads:
            thread.start()

    def configure(self, roots):
        with self.lock:
            self.roots = list(roots)
            self.generation += 1
            self.full = True
            self.restart_discovery = True
            self.scanning = True
            self.changes.clear()
            self.live_changes.clear()
            self.recent_files.clear()
            self.seed_files = ()
            self.blocked.clear()
            self.preferred = ()
        self.wake.set()

    def rescan(self):
        with self.lock:
            self.full = True
            self.restart_discovery = True
            self.scanning = True
            self.blocked.clear()
        self.wake.set()

    def resume_capacity(self):
        """Budget/dimension changes do not invalidate discovery or extraction."""
        with self.lock:
            self.blocked.clear()
        self.wake.set()

    def prioritize(self, token, kind='', files=None):
        with self.lock:
            if token < self.query_id:
                return
            if token != self.query_id:
                self.preferred = ()
                self.seed_files = ()
            self.query_id, self.query_kind = token, kind
            self.priority_until = time.monotonic()+30
            if files is not None:
                self.preferred = tuple(dict.fromkeys(files))[:64]

    def priority(self):
        with self.lock:
            recent = tuple(reversed(getattr(self, 'recent_files', {})))[:32]
            active = time.monotonic() < self.priority_until
            return (self.query_kind if active else '',
                    tuple(dict.fromkeys((self.preferred[:32] if active else ())+recent))[:64])

    def expand(self, token, files):
        """Verified semantic hits guide exploration; their neighbors are not hits."""
        with self.lock:
            if token == self.query_id:
                self.seed_files = tuple(dict.fromkeys(files))[:8]

    def content_pressure(self, db):
        available = inference.host_available()
        if available is not None and available < 512*1024**2:
            return '内存紧张，内容分析暂缓'
        expected = self.embedding_expected
        if expected is None:
            expected = bool(store.setting(db, 'model', ''))
        count = db.execute('SELECT count(*) FROM exploration_work').fetchone()[0]
        if not expected:
            self.backpressured = False
        elif count >= frontier.WORK_LIMIT:
            self.backpressured = True
        elif count <= frontier.WORK_LIMIT//2:
            self.backpressured = False
        return '等待语义编码，已保留展开进度' if self.backpressured else ''

    def _refresh_live(self, db, generation):
        now = time.monotonic()
        with self.lock:
            due = [(key, value) for key, value in self.live_changes.items() if value <= now][:8]
        for (root, path), deadline in due:
            self.checkpoint(generation)
            with self.lock:
                if self.live_changes.get((root, path)) != deadline:
                    continue
                del self.live_changes[root, path]
            try:
                fid = indexer.refresh_file(db, root, path)
            except Exception:
                with self.lock:
                    if generation == self.generation and len(self.live_changes) < 2048:
                        self.live_changes.setdefault((root, path), time.monotonic()+1)
                raise
            if fid is not None:
                with self.lock:
                    if generation == self.generation:
                        self.recent_files[fid] = None
                        self.recent_files.move_to_end(fid)
                        while len(self.recent_files) > 128:
                            self.recent_files.popitem(last=False)
            self.revision += 1

    def close(self):
        self.stop.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=3)

    def checkpoint(self, generation):
        if self.stop.is_set() or self.generation != generation:
            raise Restart()
        while self.paused:
            if self.stop.wait(.1) or self.generation != generation:
                raise Restart()

    def changed(self, root, path):
        # Native buffers and this queue are both bounded; overflow means reconcile.
        with self.lock:
            if path and '.everytensor' in {p.casefold() for p in Path(path).parts}:
                return
            if path and any(catalog.within(path, p) for p in self.exclusions):
                return
            if path:
                key = root, path
                if key in self.live_changes or len(self.live_changes) < 2048:
                    # Coalesce editor saves/download writes before content work.
                    self.live_changes[key] = time.monotonic()+.75
                    self.live_changes.move_to_end(key)
                else:
                    self.full = True
            if path is None or len(self.changes) >= 2048:
                self.full = True
                self.changes.clear()
            else:
                self.changes.add((root, str(Path(path).parent)))
        self.wake.set()

    def _discover(self):
        db = store.connect(self.path, initialize=False)
        db.create_function('index_names_now', 0, lambda: 0)
        db.execute('PRAGMA cache_size=-65536')
        db.execute('PRAGMA wal_autocheckpoint=4096')
        version, next_scan = -1, 0.
        try:
            while not self.stop.is_set():
                generation = self.generation
                try:
                    self.checkpoint(generation)
                    if version != generation:
                        for watcher in self.watchers:
                            watcher.close()
                        self.exclusions = catalog.excluded_paths(db)
                        self.watchers = [DirectoryWatcher(root, self.changed) for root in self.roots]
                        version = generation
                        # Scope changes remove only index entries, never source files.
                        indexer.prune_scope(db, self.roots, self.exclusions, lambda: self.checkpoint(generation))
                        self.pruned_generation = generation
                    with self.lock:
                        full = self.full or time.monotonic() >= next_scan
                        restart = self.restart_discovery
                        self.restart_discovery = False
                        changes = list(self.changes)
                        if full or changes:
                            self.scanning = True
                        self.full = False
                        self.changes.clear()
                    if full or changes:
                        self.scanning = True
                        if restart:
                            with storage.transaction(db):
                                db.executemany("UPDATE discovery_runs SET phase='done' WHERE root=?",
                                               [(r,) for r in self.roots])
                        tasks = [(r, None) for r in self.roots] if full else [
                            (root, folder) for root in self.roots for folder in catalog.compact_roots(
                                [p for r, p in changes if r == root])]
                        def progress(text):
                            self.discovery = text
                            self.revision += 1
                        def scan_task(task):
                            root, folder = task
                            connection = store.connect(self.path, initialize=False)
                            connection.create_function('index_names_now', 0, lambda: 0)
                            connection.execute('PRAGMA cache_size=-32768')
                            connection.execute('PRAGMA wal_autocheckpoint=4096')
                            try:
                                return indexer.scan(connection, root, lambda: self.checkpoint(generation),
                                                    subtree=folder, progress=progress, resume=folder is None)
                            finally:
                                connection.close()
                        if len(tasks) > 1:
                            # At most two volumes; I/O, extraction and GPU work overlap
                            # without creating one unbounded task per file.
                            with concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix='volume-scan') as pool:
                                reports = list(pool.map(scan_task, tasks))
                        else:
                            reports = [indexer.scan(db, root, lambda: self.checkpoint(generation),
                                                   subtree=folder, progress=progress, resume=folder is None) for root, folder in tasks]
                        for report in reports:
                            if report.get('errors') or report.get('offline'):
                                self.status({'type': 'warning', 'text': str(report)})
                        self.discovery = '文件目录已更新'
                        self.blocked.pop('catalog', None)
                        self.revision += 1
                        # Resume first; then reconcile directories completed before
                        # shutdown, which may have changed while no watcher ran.
                        delay = 5 if any(r.get('resumed') for r in reports) else (
                            1800 if self.watchers and all(w.active for w in self.watchers) else 60)
                        next_scan = time.monotonic()+delay
                    with self.lock:
                        if generation == self.generation and not self.full:
                            self.scanning = False
                except Restart:
                    continue
                except Exception as exc:
                    db.rollback()
                    if storage.is_busy(exc):
                        self.full = True
                        self.stop.wait(.2)
                        continue
                    message = '索引空间已达预算，文件目录暂停扩建' if 'database or disk is full' in str(exc) else str(exc)
                    self.blocked['catalog'] = message
                    self.discovery = message
                    self.scanning = False
                    next_scan = time.monotonic()+60
                    log.exception('File discovery paused')
                self.wake.wait(.5)
                self.wake.clear()
        finally:
            for watcher in self.watchers:
                watcher.close()
            db.close()

    def _names(self):
        db = store.connect(self.path, initialize=False)
        next_statistics = 0.
        statistics_dirty = True
        try:
            while not self.stop.is_set():
                generation = self.generation
                try:
                    self.checkpoint(generation)
                    self._refresh_live(db, generation)
                    if 'names' in self.blocked:
                        self.stop.wait(.5)
                        continue
                    indexed_names = store.index_names_batch(db)
                    if indexed_names:
                        self.revision += 1
                        statistics_dirty = True
                    elif statistics_dirty and not self.scanning and time.monotonic() >= next_statistics:
                        store.refresh_search_statistics(db)
                        next_statistics = time.monotonic()+120
                        statistics_dirty = False
                    if indexed_names:
                        continue
                except Restart:
                    db.rollback()
                except Exception as exc:
                    db.rollback()
                    if storage.is_busy(exc):
                        self.stop.wait(.2)
                        continue
                    self.blocked['names'] = str(exc)
                    log.exception('Filename indexing paused')
                self.stop.wait(.2)
        finally:
            db.close()

    def _extract(self):
        db = store.connect(self.path, initialize=False)
        scheduler = ContentQueue()
        version = -1
        try:
            while not self.stop.is_set():
                generation = self.generation
                try:
                    self.checkpoint(generation)
                    if version != generation:
                        scheduler, version = ContentQueue(), generation
                    if 'content' in self.blocked:
                        self.stop.wait(.5)
                        continue
                    pressure = self.content_pressure(db)
                    if pressure:
                        self.content = pressure
                        self.stop.wait(.2)
                        continue
                    with self.lock:
                        active = time.monotonic() < self.priority_until
                        kind, preferred = (self.query_kind, self.preferred) if active else ('', ())
                        recent = tuple(reversed(self.recent_files))[:32]
                        seeds = (self.seed_files or preferred) if active else ()
                    # Include saved library labels until discovery has reassigned
                    # their existing rows to the enclosing whole-drive root.
                    roots = list(dict.fromkeys(list(self.roots)+store.setting(db, 'roots', [])))
                    file_id = scheduler.next(db, roots, kind=kind, preferred=preferred, recent=recent, seeds=seeds)
                    if file_id is not None:
                        self.extracting = True
                        def progress(text):
                            self.content = text
                        started = time.monotonic()
                        indexer.index_pending(db, lambda: self.checkpoint(generation), progress,
                                              limit=1, file_id=file_id, max_units=32)
                        scheduler.completed(db, file_id, time.monotonic()-started)
                        self.revision += 1
                        self.extracting = False
                        continue
                    self.content = '内容提取已更新'
                except Restart:
                    db.rollback()
                except Exception as exc:
                    db.rollback()
                    if storage.is_busy(exc):
                        self.extracting = False
                        self.stop.wait(.2)
                        continue
                    message = '索引空间已达预算，内容分析已暂停' if 'database or disk is full' in str(exc) else str(exc)
                    self.blocked['content'] = message
                    self.content = message
                    log.exception('Content extraction paused')
                self.extracting = False
                self.stop.wait(.2)
        finally:
            db.close()
