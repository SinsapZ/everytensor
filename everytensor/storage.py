"""Bound indexing writes, including SQLite WAL and migration working space."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import threading

GiB = 1024**3
_lock = threading.RLock()


class BudgetFull(Exception):
    pass


class WriterBusy(sqlite3.OperationalError):
    pass


def is_busy(exc):
    return isinstance(exc, WriterBusy) or (isinstance(exc, sqlite3.OperationalError) and
        (getattr(exc, 'sqlite_errorcode', 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))


@contextmanager
def transaction(db, *, background=False):
    """Reserve the writer before reading data that will be updated.

    Maintenance never waits behind foreground/index writers, including writers
    in another process. Callers retry a whole transaction, never half a statement.
    """
    if not _lock.acquire(blocking=not background):
        raise WriterBusy('writer in use')
    old = None
    try:
        if background:
            old = db.execute('PRAGMA busy_timeout').fetchone()[0]
            db.execute('PRAGMA busy_timeout=0')
        with db:
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            yield
    finally:
        try:
            if old is not None:
                db.execute(f'PRAGMA busy_timeout={old}')
        finally:
            _lock.release()


def usage(db):
    from .store import setting
    path = Path(db.execute('PRAGMA database_list').fetchone()[2])
    sizes = {}
    for suffix in ('', '-wal', '-shm'):
        file = Path(str(path)+suffix)
        try:
            sizes[suffix or 'database'] = file.stat().st_size
        except FileNotFoundError:
            sizes[suffix or 'database'] = 0
    # The optional dense vector cache is disposable, but still consumes the
    # user's index budget, including its temporary atomic replacement.
    for key, name in (('quick_cache', 'quick-search.bin'),
                      ('quick_cache_building', 'quick-search.building'),
                      ('quick_cache_lock', 'quick-search.lock')):
        try:
            sizes[key] = (path.parent/name).stat().st_size
        except FileNotFoundError:
            sizes[key] = 0
    limit = int(float(setting(db, 'budget_gib', 8))*GiB)
    return dict(bytes=sum(sizes.values()), limit=limit, **sizes)


@contextmanager
def write(db, estimate=256*1024, *, catalog=False):
    """Short, serialized writer reservations. Foreground readers do not take this lock.

    Leave 20% for WAL/maintenance and another 10% for new filenames. A single
    oversized document rolls back, retaining its previously committed generation.
    """
    from .store import setting
    with _lock:
        state = usage(db)
        limit = state['limit']
        ceiling = int(limit*(.9 if catalog else .8))
        if state['bytes'] + estimate > ceiling:
            db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            state = usage(db)
            if state['bytes'] + estimate > ceiling:
                # This is nonblocking: a long reader may prevent truncation.
                old = db.execute('PRAGMA busy_timeout').fetchone()[0]
                db.execute('PRAGMA busy_timeout=0')
                try:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                finally:
                    db.execute(f'PRAGMA busy_timeout={old}')
                state = usage(db)
        reserve = int(setting(db, 'free_disk_floor_mib', 256))*1024**2
        directory = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
        if state['bytes'] + estimate > ceiling or shutil.disk_usage(directory).free < reserve+estimate:
            raise BudgetFull('索引空间已达预算，已保留现有结果；可在设置中提高上限')
        pagesize = db.execute('PRAGMA page_size').fetchone()[0]
        # SQLite enforces the main-database cap even if an estimate is too small.
        db.execute(f'PRAGMA max_page_count={max(1, int(limit*(.85 if catalog else .75))//pagesize)}')
        with transaction(db):
            yield


def compact(db):
    """Reuse freed pages and checkpoint; never do a space-doubling full VACUUM."""
    if not _lock.acquire(blocking=False):
        return
    try:
        db.execute('PRAGMA wal_checkpoint(PASSIVE)')
    except sqlite3.OperationalError as exc:
        if not is_busy(exc):
            raise
    finally:
        _lock.release()


class Maintenance:
    """Bound visited rows, not just deleted rows, even in million-file catalogs."""
    def __init__(self):
        self.positions = dict(vectors=0, directories=0, embedding_errors=0)

    def step(self, db, limit=128):
        positions = dict(self.positions)
        visited = 0
        try:
            with transaction(db, background=True):
                for table, last in positions.items():
                    rows = db.execute(f'SELECT rowid FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?',
                                      (last, limit)).fetchall()
                    visited += len(rows)
                    relation = ('SELECT 1 FROM files WHERE parent_id=directories.id' if table == 'directories'
                                else f'SELECT 1 FROM units WHERE digest={table}.digest')
                    db.executemany(f'DELETE FROM {table} WHERE rowid=? AND NOT EXISTS({relation})', rows)
                    positions[table] = rows[-1][0] if len(rows) == limit else 0
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            return 1  # Retry later; preserve the cursor after a rollback.
        self.positions = positions
        return 1 if visited and any(positions.values()) else 60
