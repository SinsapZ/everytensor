"""Disposable dense quick vectors plus a bounded, exact SQLite delta.

SQLite remains authoritative. This file only avoids repeatedly decoding its
vector BLOBs; scope, live files, and source identities are checked by the search.
"""
from __future__ import annotations

import json
import logging
import mmap
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import struct
import time
import uuid

from . import storage, store

log = logging.getLogger(__name__)
FORMAT = 2
MAGIC = b'ETQDC02\0'
PREFIX = struct.Struct('<8sII')
HEADER_BYTES = 4096
BLOCK_ROWS = 2048
FILENAME = 'quick-search.bin'
BUILDING = 'quick-search.building'
LOCKFILE = 'quick-search.lock'
IDENTITY_KEY = 'cache_identity'
BASE_REVISION_KEY = 'dense_base_revision'
MAX_DIRTY = 8192
KIND_CODES = {'document': 1, 'image': 2, 'video': 3, 'code': 4, 'other': 5, 'audio': 6}


def _stopped(stopping):
    if stopping is None:
        return False
    return stopping.is_set() if hasattr(stopping, 'is_set') else bool(stopping())


def _stamp(db):
    values = dict(db.execute('SELECT key,value FROM quick_state WHERE key IN (?,?,?)',
                             ('model', 'revision', IDENTITY_KEY)))
    identity = values.get(IDENTITY_KEY, '')
    revision = int(values.get('revision', '-1'))
    if not identity or revision < 0 or not values.get('model'):
        return None
    return values['model'], revision, identity


def _path(db):
    return Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve().parent/FILENAME


def _header(source, expected, dimensions):
    data = source.read(HEADER_BYTES)
    if len(data) != HEADER_BYTES:
        return None
    magic, version, length = PREFIX.unpack_from(data)
    if magic != MAGIC or version != FORMAT or not 0 < length <= HEADER_BYTES-PREFIX.size:
        return None
    header = json.loads(data[PREFIX.size:PREFIX.size+length])
    if not isinstance(header, dict):
        return None
    numeric = ('dimensions', 'names', 'texts', 'count', 'ids_offset', 'kinds_offset',
               'vectors_offset', 'bytes', 'revision')
    if any(type(header.get(key)) is not int or header[key] < 0 for key in numeric):
        return None
    if ((header.get('model'), header['revision'], header.get('identity')) != expected
            or header['dimensions'] != dimensions or dimensions <= 0):
        return None
    count = header['names']+header['texts']
    kinds_offset = HEADER_BYTES+count*16
    vector_offset = (kinds_offset+count+63)//64*64
    size = vector_offset+count*dimensions*2
    if (header['count'] != count or header['ids_offset'] != HEADER_BYTES
            or header['kinds_offset'] != kinds_offset
            or header['vectors_offset'] != vector_offset or header['bytes'] != size
            or os.fstat(source.fileno()).st_size != size):
        return None
    return header


class _Blocks:
    """A closeable iterator; yielded arrays own their storage, never the mmap."""
    def __init__(self, source, header, kind='', dirty=()):
        import numpy as np
        self._source = source
        self._map = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
        self._header = header
        self._position = 0
        self._kind = KIND_CODES[kind] if kind else None
        # This snapshot is also consumed by the caller to fetch replacements
        # and additions from authoritative SQLite rows. Deleted ids contribute
        # no replacement, but must still be suppressed in the mapped base.
        self.dirty = tuple(dirty)
        self._dirty_ids = tuple(np.array(sorted(sid for k, sid in self.dirty if k == name),
                                        dtype=np.int64) for name in ('name', 'text'))

    def __iter__(self):
        return self

    def __next__(self):
        import numpy as np
        header = self._header
        try:
            while self._map is not None and self._position < header['count']:
                start = self._position
                basis = 0 if start < header['names'] else 1
                end = min(start+BLOCK_ROWS, header['names'] if basis == 0 else header['count'])
                count, dim = end-start, header['dimensions']
                self._position = end
                take = None
                if self._kind is not None:
                    take = np.ndarray((count,), dtype='u1', buffer=self._map,
                                      offset=header['kinds_offset']+start) == self._kind
                    if not take.any():
                        continue
                ids = np.ndarray((count, 2), dtype='<i8', buffer=self._map,
                                 offset=header['ids_offset']+start*16)
                dirty_ids = self._dirty_ids[basis]
                if dirty_ids.size:
                    positions = np.searchsorted(dirty_ids, ids[:, 1])
                    clean = (positions == dirty_ids.size) | (
                        dirty_ids[np.minimum(positions, dirty_ids.size-1)] != ids[:, 1])
                    take = clean if take is None else take & clean
                    if not take.any():
                        del ids
                        continue
                ids = ids.copy() if take is None else ids[take]
                matrix = np.ndarray((count, dim), dtype='<f2', buffer=self._map,
                                    offset=header['vectors_offset']+start*dim*2)
                matrix = matrix.copy() if take is None else matrix[take]
                if end == header['count']:
                    self.close()
                return basis, ids, matrix
            self.close()
            raise StopIteration
        except BaseException:
            self.close()
            raise

    def close(self):
        mapping, self._map = getattr(self, '_map', None), None
        source, self._source = getattr(self, '_source', None), None
        if mapping is not None:
            mapping.close()
        if source is not None:
            source.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


def blocks(db, model, dimensions, kind='', *, allow_delta=False):
    """Return None on a cache miss, else a closeable/context-managed iterator.

    Validation uses the caller's current SQLite snapshot. With allow_delta,
    the file must match the committed base marker and at most MAX_DIRTY source
    ids may have changed. The caller MUST append their current SQLite vectors
    using this same snapshot; the returned iterator exposes them as .dirty.
    Close the iterator in a finally block if a query abandons it before exhaustion.
    """
    source = None
    try:
        if kind and kind not in KIND_CODES:
            return None
        with store.read_snapshot(db):
            expected = _stamp(db)
            if expected is None or expected[0] != model:
                return None
            dirty = ()
            if allow_delta:
                base = db.execute('SELECT value FROM quick_state WHERE key=?',
                                  (BASE_REVISION_KEY,)).fetchone()
                if base is None or not 0 <= int(base[0]) <= expected[1]:
                    return None
                dirty = tuple((row[0], row[1]) for row in db.execute(
                    'SELECT kind,source_id FROM quick_dirty LIMIT ?', (MAX_DIRTY+1,)))
                if len(dirty) > MAX_DIRTY:
                    return None
                expected = expected[0], int(base[0]), expected[2]
            source = _path(db).open('rb')
            header = _header(source, expected, dimensions)
            if header is None:
                return None
            result = _Blocks(source, header, kind, dirty)
            source = None  # The returned iterator now owns the open descriptor.
            return result
    except (OSError, ValueError, TypeError, sqlite3.Error, struct.error):
        return None
    finally:
        if source is not None:
            source.close()


def _room(db, additional=0):
    usage = storage.usage(db)  # Includes both the old cache and our temporary file.
    floor = int(store.setting(db, 'free_disk_floor_mib', 256))*1024**2
    return (usage['bytes']+additional <= int(.8*usage['limit'])
            and shutil.disk_usage(_path(db).parent).free >= floor+additional)


@contextmanager
def _build_lock(path):
    """The persistent file is harmless; the OS releases its lock after a crash."""
    source = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), 'r+b')
    acquired = False
    try:
        if os.fstat(source.fileno()).st_size == 0:
            source.write(b'\0')
            source.flush()
        source.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(source.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(source.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        try:
            if acquired:
                source.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(source.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(source.fileno(), fcntl.LOCK_UN)
        finally:
            source.close()


def _write_snapshot(db, target, expected, dimensions, stopping):
    queries = (
        """SELECT v.source_id AS file_id,v.source_id,v.data,f.kind AS file_kind FROM quick_vectors v
            JOIN files f ON f.id=v.source_id WHERE v.model=? AND v.kind='name'""",
        """SELECT u.file_id,v.source_id,v.data,f.kind AS file_kind FROM quick_vectors v
            JOIN units u ON u.id=v.source_id JOIN files f ON f.id=u.file_id
            WHERE v.model=? AND v.kind='text'""",
    )
    counts = [db.execute('SELECT count(*) FROM ('+query+')', (expected[0],)).fetchone()[0]
              for query in queries]
    count = sum(counts)
    kinds_offset = HEADER_BYTES+count*16
    vector_offset = (kinds_offset+count+63)//64*64
    header = dict(model=expected[0], revision=expected[1], identity=expected[2], dimensions=dimensions,
                  names=counts[0], texts=counts[1], count=count, ids_offset=HEADER_BYTES,
                  kinds_offset=kinds_offset, vectors_offset=vector_offset,
                  bytes=vector_offset+count*dimensions*2)
    payload = json.dumps(header, separators=(',', ':')).encode('utf-8')
    if len(payload) > HEADER_BYTES-PREFIX.size or not _room(db, header['bytes']):
        return False
    # Reserve the logical size before streaming; usage() can then account for
    # this entire build while other index producers continue writing.
    target.truncate(header['bytes'])
    position = 0
    for query in queries:
        cursor = db.execute(query, (expected[0],))
        while rows := cursor.fetchmany(BLOCK_ROWS):
            if _stopped(stopping):
                return False
            if any(len(row['data']) != dimensions*2 for row in rows):
                raise ValueError('Quick vector dimensions do not match the dense cache')
            ids = b''.join(struct.pack('<qq', row['file_id'], row['source_id']) for row in rows)
            kinds = bytes(KIND_CODES.get(row['file_kind'], 0) for row in rows)
            data = b''.join(row['data'] for row in rows)
            target.seek(header['ids_offset']+position*16)
            target.write(ids)
            target.seek(header['kinds_offset']+position)
            target.write(kinds)
            target.seek(header['vectors_offset']+position*dimensions*2)
            target.write(data)
            position += len(rows)
    if position != count or _stopped(stopping):
        return False
    target.seek(0)
    target.write(PREFIX.pack(MAGIC, FORMAT, len(payload))+payload)
    target.flush()
    os.fsync(target.fileno())
    return True


def build(db_path, model, dimensions, stopping=None, *, min_compaction_age=0):
    """Return True when a matching dense cache is ready, else False.

    The optional cache never holds a SQLite writer while copying BLOBs. Failures,
    cancellation, and concurrent source changes leave the previous cache intact.
    An OS lock protects the build file, including recovery after a crashed writer.
    A recent searchable base with a small delta may defer compaction and return
    False, allowing the caller to retry later without rewriting after every edit.
    """
    db = None
    path = Path(db_path).resolve()
    temporary, destination = path.parent/BUILDING, path.parent/FILENAME
    try:
        if not path.is_file() or type(dimensions) is not int or dimensions <= 0 or _stopped(stopping):
            return False
        with _build_lock(path.parent/LOCKFILE) as acquired:
            if not acquired:
                return False
            # No current builder can own this file while we hold the lock.
            # Remove abandoned bytes before either budget checks or a cache hit.
            temporary.unlink(missing_ok=True)
            owned = False
            try:
                db = store.connect(path, initialize=False)
                cached = blocks(db, model, dimensions)
                if cached is not None:
                    revision = cached._header['revision']
                    cached.close()
                    base = db.execute('SELECT value FROM quick_state WHERE key=?',
                                      (BASE_REVISION_KEY,)).fetchone()
                    if base is not None and int(base[0]) == revision:
                        return True
                if min_compaction_age > 0:
                    searchable = blocks(db, model, dimensions, allow_delta=True)
                    if searchable is not None:
                        try:
                            age = time.time()-destination.stat().st_mtime
                            # A backwards clock jump must not defer forever.
                            if (len(searchable.dirty) < MAX_DIRTY//2
                                    and 0 <= age < min_compaction_age):
                                return False
                        finally:
                            searchable.close()
                # A replacement/reset quick_state gets a new identity, even if
                # the model and numerical revision happen to match an old file.
                if not db.execute('SELECT 1 FROM quick_state WHERE key=?', (IDENTITY_KEY,)).fetchone():
                    with storage.transaction(db, background=True):
                        db.execute('INSERT OR IGNORE INTO quick_state VALUES(?,?)', (IDENTITY_KEY, uuid.uuid4().hex))
                with store.read_snapshot(db):
                    expected = _stamp(db)
                    if expected is None or expected[0] != model or _stopped(stopping):
                        return False
                    with temporary.open('wb') as target:
                        owned = True
                        if not _write_snapshot(db, target, expected, dimensions, stopping):
                            return False
                # Freeze only final validation/rename against SQLite writers.
                with storage.transaction(db, background=True):
                    if _stopped(stopping) or _stamp(db) != expected or not _room(db):
                        return False
                    os.replace(temporary, destination)
                    owned = False
                    # The marker and dirty reset commit together. Readers with
                    # an older SQLite snapshot reject the new file's revision;
                    # a crash between rename and commit likewise causes a miss.
                    db.execute('DELETE FROM quick_dirty')
                    db.execute('INSERT INTO quick_state VALUES(?,?) ON CONFLICT(key) '
                               'DO UPDATE SET value=excluded.value',
                               (BASE_REVISION_KEY, str(expected[1])))
                return True
            finally:
                # Cleanup remains inside the lock, so it can never unlink a
                # subsequent builder's temporary file after releasing ownership.
                if owned:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        log.debug('Could not remove abandoned quick cache build', exc_info=True)
    except Exception:
        log.debug('Dense quick cache build skipped', exc_info=True)
        return False
    finally:
        if db is not None:
            db.close()
