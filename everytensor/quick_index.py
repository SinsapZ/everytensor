"""Local, inexpensive semantic coverage independent of the visual model.

File hints and every extracted text fragment have separate learned embeddings.
Hints remain hints: they never claim to understand unprocessed image/video data.
"""
from __future__ import annotations

import heapq
import json
import logging
from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import threading
import time

from . import catalog, storage, store
from .cache import ByteCache

log = logging.getLogger(__name__)
SCHEMA_VERSION = 3
RETRY_SECONDS = 60


def resolve_model(db, default=None):
    if default is None:
        from .paths import frozen, resource_root
        database = Path(db.execute('PRAGMA database_list').fetchone()[2])
        root = resource_root() if frozen() else database.resolve().parent.parent
        default = root / 'models' / 'quick-semantic'
    for value in (store.setting(db, 'quick_model', ''), default):
        if not value:
            continue
        path = Path(value).resolve()
        try:
            config = json.loads((path/'manifest.json').read_text('utf-8'))
            if (config.get('format_version') != 1 or
                    config.get('model_id') != 'sentence-transformers/static-similarity-mrl-multilingual-v1'):
                continue
            if not all((path/name).is_file() for name in ('embeddings.npy', 'tokenizer.json')):
                continue
        except (OSError, ValueError):
            continue
        if store.setting(db, 'quick_model', '') != str(path):
            store.set_setting(db, 'quick_model', str(path))
        return str(path)
    return ''


def install(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='quick_jobs'").fetchone():
        if int(state(db, 'schema_version')) >= SCHEMA_VERSION:
            return
    db.executescript('''
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS quick_vectors(
      kind TEXT NOT NULL,source_id INTEGER NOT NULL,identity TEXT NOT NULL,
      model TEXT NOT NULL,data BLOB NOT NULL,PRIMARY KEY(kind,source_id));
    CREATE TABLE IF NOT EXISTS quick_jobs(kind TEXT NOT NULL,source_id INTEGER NOT NULL,
      PRIMARY KEY(kind,source_id));
    CREATE INDEX IF NOT EXISTS quick_model_kind ON quick_vectors(model,kind);
    CREATE TABLE IF NOT EXISTS quick_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS quick_dirty(
      kind TEXT NOT NULL,source_id INTEGER NOT NULL,PRIMARY KEY(kind,source_id)) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS quick_retry(
      source_id INTEGER PRIMARY KEY REFERENCES units(id) ON DELETE CASCADE,
      digest TEXT NOT NULL,path TEXT NOT NULL,size INTEGER,mtime INTEGER,retry_at REAL NOT NULL);
    INSERT OR IGNORE INTO quick_state VALUES('revision','0');
    CREATE TRIGGER IF NOT EXISTS quick_file_insert AFTER INSERT ON files WHEN new.status<>'metadata' BEGIN
      INSERT OR IGNORE INTO quick_jobs VALUES('name',new.id);
    END;
    DROP TRIGGER IF EXISTS quick_file_update;
    CREATE TRIGGER quick_file_update AFTER UPDATE OF path,name,status,kind ON files
      WHEN old.path<>new.path OR old.name<>new.name OR old.kind<>new.kind OR
           (old.status='metadata')<>(new.status='metadata') BEGIN
      DELETE FROM quick_jobs WHERE kind='name' AND source_id=new.id;
      INSERT OR IGNORE INTO quick_jobs SELECT 'name',new.id WHERE new.status<>'metadata';
      DELETE FROM quick_vectors WHERE kind='name' AND source_id=new.id;
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    CREATE TRIGGER IF NOT EXISTS quick_file_delete AFTER DELETE ON files BEGIN
      DELETE FROM quick_jobs WHERE kind='name' AND source_id=old.id;
      DELETE FROM quick_vectors WHERE kind='name' AND source_id=old.id;
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    DROP TRIGGER IF EXISTS quick_unit_insert;
    CREATE TRIGGER quick_unit_insert AFTER INSERT ON units
      WHEN new.text<>'' AND coalesce(json_extract(new.location,'$.semantic'),1)<>0 BEGIN
      INSERT OR IGNORE INTO quick_jobs VALUES('text',new.id);
    END;
    DROP TRIGGER IF EXISTS quick_unit_update;
    CREATE TRIGGER quick_unit_update AFTER UPDATE OF text,digest ON units
      WHEN old.text<>new.text OR old.digest<>new.digest BEGIN
      DELETE FROM quick_vectors WHERE kind='text' AND source_id=new.id;
      DELETE FROM quick_jobs WHERE kind='text' AND source_id=new.id;
      DELETE FROM quick_retry WHERE source_id=new.id;
      INSERT OR IGNORE INTO quick_jobs SELECT 'text',new.id
        WHERE new.text<>'' AND coalesce(json_extract(new.location,'$.semantic'),1)<>0;
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    DROP TRIGGER IF EXISTS quick_unit_delete;
    CREATE TRIGGER quick_unit_delete AFTER DELETE ON units BEGIN
      DELETE FROM quick_jobs WHERE kind='text' AND source_id=old.id;
      DELETE FROM quick_vectors WHERE kind='text' AND source_id=old.id;
      DELETE FROM quick_retry WHERE source_id=old.id;
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    -- Track changes only once a dense base exists. Repeated edits occupy one
    -- entry, and deletes remain tombstones until the next base is published.
    CREATE TRIGGER IF NOT EXISTS quick_vector_delta_insert AFTER INSERT ON quick_vectors BEGIN
      INSERT OR IGNORE INTO quick_dirty SELECT new.kind,new.source_id
        WHERE EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    CREATE TRIGGER IF NOT EXISTS quick_vector_delta_delete AFTER DELETE ON quick_vectors BEGIN
      INSERT OR IGNORE INTO quick_dirty SELECT old.kind,old.source_id
        WHERE EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    CREATE TRIGGER IF NOT EXISTS quick_vector_delta_update AFTER UPDATE ON quick_vectors BEGIN
      INSERT OR IGNORE INTO quick_dirty SELECT old.kind,old.source_id
        WHERE EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
      INSERT OR IGNORE INTO quick_dirty SELECT new.kind,new.source_id
        WHERE EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    CREATE TRIGGER IF NOT EXISTS quick_file_kind_delta AFTER UPDATE OF kind ON files
      WHEN old.kind<>new.kind BEGIN
      INSERT OR IGNORE INTO quick_dirty SELECT 'text',id FROM units WHERE file_id=new.id
        AND EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
    END;
    CREATE TRIGGER IF NOT EXISTS quick_unit_owner_delta AFTER UPDATE OF file_id ON units
      WHEN old.file_id<>new.file_id BEGIN
      INSERT OR IGNORE INTO quick_dirty SELECT 'text',new.id
        WHERE EXISTS(SELECT 1 FROM quick_state WHERE key='dense_base_revision');
      UPDATE quick_state SET value=CAST(value AS INTEGER)+1 WHERE key='revision';
    END;
    INSERT OR REPLACE INTO quick_state VALUES('schema_version','3');
    COMMIT;
    ''')


def state(db, key, default='0'):
    row = db.execute('SELECT value FROM quick_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def save(db, key, value):
    db.execute('INSERT INTO quick_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, str(value)))


def hint(row):
    # Include a little folder context, not the entire drive's common prefix.
    parent = Path(row['path']).parent
    return row['name'] + ' ' + ' '.join(parent.parts[-2:])


def has_eligible_files(db, kind=''):
    """An empty, reconciled type/scope does not need to wait for embeddings."""
    clause, args = catalog.scope_clause(catalog.search_scope(db), 'path_key')
    if kind:
        clause += ' AND kind=?'
        args = (*args, kind)
    return bool(db.execute("SELECT 1 FROM files WHERE status IN ('pending','ready','error') AND "+
                           clause+' LIMIT 1', args).fetchone())


def pump(db, model, limit=512):
    """Bounded migration; changes after the cursor are also captured by triggers."""
    with storage.write(db, 256*1024):
        if state(db, 'model', '') != model:
            save(db, 'model', model)
            save(db, 'files', 0)
            save(db, 'units', 0)
            save(db, 'dispatch_name', 0)
            save(db, 'dispatch_text', 0)
        # Three bounded seeks avoid both sorting a multi-status backlog and
        # walking a huge metadata-only tail on every idle poll.
        after = int(state(db, 'files'))
        files = sorted(row[0] for status in ('pending', 'ready', 'error')
                       for row in db.execute('''SELECT id FROM files INDEXED BY files_pending
                           WHERE status=? AND id>? ORDER BY id LIMIT ?''', (status, after, limit)))[:limit]
        db.executemany('''INSERT OR IGNORE INTO quick_jobs SELECT 'name',f.id FROM files f
            WHERE f.id=? AND NOT EXISTS(SELECT 1 FROM quick_vectors v WHERE v.kind='name'
                AND v.source_id=f.id AND v.identity=f.path AND v.model=?)''', [(fid, model) for fid in files])
        if files:
            save(db, 'files', files[-1])
        # Advance over inspected units, including visual-only fragments. Their
        # absence of text must not cause an unbounded scan or repeated tail scan.
        units = db.execute("SELECT id,text<>'' AND coalesce(json_extract(location,'$.semantic'),1)<>0 FROM units WHERE id>? ORDER BY id LIMIT ?",
                           (int(state(db, 'units')), limit)).fetchall()
        db.executemany('''INSERT OR IGNORE INTO quick_jobs SELECT 'text',u.id FROM units u
            WHERE u.id=? AND NOT EXISTS(SELECT 1 FROM quick_vectors v WHERE v.kind='text'
                AND v.source_id=u.id AND v.identity=u.digest AND v.model=?)''', [(r[0], model) for r in units if r[1]])
        if units:
            save(db, 'units', units[-1][0])
        return len(files)+len(units)


def pending(db, model, kind, preferred=(), limit=128, *, after=None):
    if kind == 'name':
        query = '''SELECT f.id AS source_id,f.id AS file_id,f.name,f.path,f.path AS identity
          FROM quick_jobs j JOIN files f ON f.id=j.source_id
          WHERE j.kind='name' AND f.status<>'metadata' '''
    else:
        query = '''SELECT u.id AS source_id,f.id AS file_id,f.name,f.root,f.path,f.size,f.mtime,u.digest AS identity,u.text
          FROM quick_jobs j JOIN units u ON u.id=j.source_id JOIN files f ON f.id=u.file_id
          WHERE j.kind='text' AND f.status='ready' AND u.text<>''
            AND coalesce(json_extract(u.location,'$.semantic'),1)<>0 '''
    clause, args = catalog.scope_clause(catalog.search_scope(db), 'f.path_key')
    query += ' AND '+clause
    query += ''' AND NOT EXISTS(SELECT 1 FROM quick_vectors v WHERE v.kind=j.kind
        AND v.source_id=j.source_id AND v.model=? AND v.identity='''+('f.path' if kind == 'name' else 'u.digest')+')'
    args = (*args, model)
    if kind == 'text':
        query += ''' AND NOT EXISTS(SELECT 1 FROM quick_retry r WHERE r.source_id=u.id
            AND r.digest=u.digest AND r.path=f.path AND r.size IS f.size AND r.mtime IS f.mtime
            AND r.retry_at>?)'''
        args = (*args, time.time())
    selected = []
    if preferred:
        ids = tuple(preferred[:64])
        selected = db.execute(query+' AND f.id IN ('+','.join('?' for _ in ids)+') LIMIT ?',
                              (*args, *ids, limit//2)).fetchall()
    if after is None:
        after = int(state(db, 'dispatch_'+kind))
    # Walk jobs in key order, not every ready file followed by a large sort.
    ordinary_query = query.replace(' JOIN ', ' CROSS JOIN ')
    ordinary = db.execute(ordinary_query+' AND j.source_id>? ORDER BY j.source_id LIMIT ?',
                          (*args, after, limit)).fetchall()
    if after and len(ordinary) < limit:
        ordinary += db.execute(ordinary_query+' AND j.source_id<=? ORDER BY j.source_id LIMIT ?',
                               (*args, after, limit-len(ordinary))).fetchall()
    return list({row['source_id']: row for row in selected+ordinary}.values())[:limit]


def persist(db, model, kind, rows, vectors, *, on_stale=None):
    import numpy as np
    accepted, changed = [], set()
    with storage.write(db, max(65536, len(rows)*2048)):
        for row, vector in zip(rows, vectors):
            if kind == 'name':
                live = db.execute("SELECT path FROM files WHERE id=? AND status<>'metadata'", (row['source_id'],)).fetchone()
            else:
                live = db.execute("SELECT u.digest,f.path,f.size,f.mtime FROM units u JOIN files f ON f.id=u.file_id WHERE u.id=? AND f.status='ready'",
                                  (row['source_id'],)).fetchone()
                identity = row['identity'], row['path'], row['size'], row['mtime']
                if live is None or tuple(live) != identity:
                    continue
                try:
                    stat = Path(row['path']).stat()
                    current = (stat.st_size, stat.st_mtime_ns) == (row['size'], row['mtime'])
                except OSError:
                    current = False
                if not current:
                    previous = db.execute('SELECT digest,path,size,mtime FROM quick_retry WHERE source_id=?',
                                          (row['source_id'],)).fetchone()
                    db.execute('INSERT OR REPLACE INTO quick_retry VALUES(?,?,?,?,?,?)',
                               (row['source_id'], *identity, time.time()+RETRY_SECONDS))
                    if previous is None or tuple(previous) != identity:
                        changed.add((row['root'], row['path']))
                    continue
            if live and live[0] == row['identity']:
                accepted.append((kind, row['source_id'], row['identity'], model,
                                 np.asarray(vector, dtype='<f2').tobytes()))
        db.executemany('INSERT OR REPLACE INTO quick_vectors VALUES(?,?,?,?,?)', accepted)
        db.executemany('DELETE FROM quick_jobs WHERE kind=? AND source_id=?', [(r[0], r[1]) for r in accepted])
        if kind == 'text':
            db.executemany('DELETE FROM quick_retry WHERE source_id=?', [(r[1],) for r in accepted])
        if rows:
            # Rejected sources stay queued for recovery, but cannot monopolize
            # the front of the queue while inventory catches up with disk.
            save(db, 'dispatch_'+kind, rows[-1]['source_id'])
        if accepted:
            save(db, 'revision', int(state(db, 'revision'))+1)
    # Filesystem reconciliation can acquire its own locks or write later. Never
    # call it while the quick-vector writer transaction is held.
    if on_stale is not None:
        for root, path in changed:
            on_stale(root, path)
    return len(accepted)


class QuickSearch:
    def __init__(self, max_bytes=256*1024**2):
        self.max_bytes = max_bytes
        self.cache = ByteCache(min(max_bytes, 32*1024**2), max_items=1)

    def search(self, db, vector, model, kind='', limit=500, cancelled=lambda: False):
        with store.read_snapshot(db), ExitStack() as resources:
            return self._search(db, vector, model, kind, limit, cancelled, resources)

    def _search(self, db, vector, model, kind, limit, cancelled, resources):
        import numpy as np
        from .inference import host_available
        if limit <= 0:
            return []
        available = host_available()
        capacity = (min(self.max_bytes, 32*1024**2) if available is None else
                    min(self.max_bytes, max(0, available+self.cache.bytes-640*1024**2)))
        self.cache.max_bytes = capacity
        if self.cache.bytes > capacity:
            self.cache.clear()
        scope = catalog.search_scope(db)
        if scope[0] == ():
            return []
        clause, args = catalog.scope_clause(scope, 'f.path_key')
        kind_clause = ' AND f.kind=?' if kind else ''
        args = (*args, kind) if kind else tuple(args)
        if kind in ('document', 'image', 'video'):
            name_source = '''files f INDEXED BY files_kind_name
                CROSS JOIN quick_vectors v ON v.source_id=f.id'''
            text_source = '''files f INDEXED BY files_kind_name
                CROSS JOIN units u ON u.file_id=f.id
                CROSS JOIN quick_vectors v ON v.source_id=u.id'''
        else:
            name_source = 'quick_vectors v CROSS JOIN files f ON f.id=v.source_id'
            text_source = '''quick_vectors v CROSS JOIN units u ON u.id=v.source_id
                CROSS JOIN files f ON f.id=u.file_id'''
        direct_names = not kind and (scope[0] is None or all(str(Path(root)) == Path(root).anchor for root in scope[0]))
        queries = [
            (0, f'''SELECT f.id AS file_id,v.source_id,v.data FROM {name_source}
                WHERE v.kind='name' AND v.identity=f.path
                AND f.status<>'metadata' AND v.model=? AND '''+clause+kind_clause, (model, *args)),
            (1, f'''SELECT f.id AS file_id,v.source_id,v.data FROM {text_source}
                WHERE v.kind='text' AND v.identity=u.digest AND f.status='ready'
                AND v.model=? AND '''+clause+kind_clause, (model, *args))]
        if direct_names:
            # Whole-drive search can stream the compact name vectors directly.
            # Only prospective winners need a file/path/identity lookup. Narrow
            # folder scopes retain the join to filter their candidates in SQL.
            queries[0] = (0, '''SELECT source_id AS file_id,source_id,data FROM quick_vectors
                               WHERE kind='name' AND model=?''', (model,))
        q = np.asarray(vector, dtype='<f4')
        q = q/max(float(np.linalg.norm(q)), 1e-12)
        from . import quick_cache
        whole_drive = scope[0] is None or all(str(Path(root)) == Path(root).anchor for root in scope[0])
        packed = quick_cache.blocks(db, model, len(q), kind=kind, allow_delta=True) if whole_drive else None
        if packed is not None:
            resources.callback(packed.close)
            # The OS can page this representation; do not also pin a whole
            # corpus in the Python heap when a current mapped cache is ready.
            self.cache.clear()
        database = db.execute('PRAGMA database_list').fetchone()[2]
        revision = (database or id(db), state(db, 'revision'), store.search_revision(db), scope, model, kind)
        blocks = self.cache.get(revision) if packed is None else None
        if blocks is None:
            # Do not hold two whole-corpus arrays while filling another scope or
            # revision. Keep the half-precision storage; widen one block at a time.
            self.cache.clear()
        saved, used = ([] if blocks is None and packed is None else None), 0
        def source():
            if packed is not None:
                yield from packed
                # The base has omitted changed IDs (including tombstones).
                # Read their current values in the very same SQLite snapshot.
                for basis, label in ((0, 'name'), (1, 'text')):
                    changed = [sid for source_kind, sid in packed.dirty if source_kind == label]
                    for offset in range(0, len(changed), 400):
                        if cancelled():
                            return
                        selected = changed[offset:offset+400]
                        marks = ','.join('?' for _ in selected)
                        if basis:
                            sql = '''SELECT f.id AS file_id,v.source_id,v.data FROM quick_vectors v
                                JOIN units u ON u.id=v.source_id JOIN files f ON f.id=u.file_id
                                WHERE v.kind='text' AND v.identity=u.digest AND f.status='ready' '''
                        else:
                            sql = '''SELECT f.id AS file_id,v.source_id,v.data FROM quick_vectors v
                                JOIN files f ON f.id=v.source_id WHERE v.kind='name'
                                AND v.identity=f.path AND f.status<>'metadata' '''
                        rows = db.execute(sql+' AND v.model=? AND '+clause+kind_clause+
                                          ' AND v.source_id IN ('+marks+')', (model, *args, *selected)).fetchall()
                        if rows:
                            ids = np.array([(r['file_id'], r['source_id']) for r in rows], dtype=np.int64)
                            matrix = np.frombuffer(b''.join(r['data'] for r in rows), dtype='<f2').reshape(len(rows), len(q))
                            yield basis, ids, matrix
                return
            if blocks is not None:
                yield from blocks
                return
            nonlocal saved, used
            for basis, sql, query_args in queries:
                cursor = db.execute(sql, query_args)
                while rows := cursor.fetchmany(2048):
                    if cancelled():
                        return
                    ids = np.array([(r['file_id'], r['source_id']) for r in rows], dtype=np.int64)
                    matrix = np.frombuffer(b''.join(r['data'] for r in rows), dtype='<f2').reshape(len(rows), len(q))
                    block = (basis, ids, matrix)
                    used += ids.nbytes+matrix.nbytes
                    if saved is not None:
                        if used <= self.cache.max_bytes:
                            saved.append(block)
                        else:
                            saved = None
                    yield block
        best, heap = {}, []
        live = store._LiveFiles(db, scope)
        validity = OrderedDict()
        def valid(fid, basis, uid):
            key = fid, basis, uid if packed is not None and basis else 0
            if key not in validity:
                if (direct_names or packed is not None) and not basis:
                    row = db.execute('''SELECT f.path,f.size,f.mtime,f.status,f.kind FROM files f
                        JOIN quick_vectors v ON v.kind='name' AND v.source_id=f.id
                        WHERE f.id=? AND v.model=? AND v.identity=f.path AND f.status<>'metadata' ''',
                                     (fid, model)).fetchone()
                elif packed is not None:
                    row = db.execute('''SELECT f.path,f.size,f.mtime,f.status,f.kind FROM units u
                        JOIN files f ON f.id=u.file_id
                        JOIN quick_vectors v ON v.kind='text' AND v.source_id=u.id
                        WHERE u.id=? AND f.id=? AND v.model=? AND v.identity=u.digest
                          AND f.status='ready' ''', (uid, fid, model)).fetchone()
                else:
                    row = db.execute('SELECT path,size,mtime,status,kind FROM files WHERE id=?', (fid,)).fetchone()
                validity[key] = bool(row and (not kind or row['kind'] == kind)
                                     and live.accept(dict(row), content=bool(basis)))
                if len(validity) > max(1024, limit*2):
                    validity.popitem(last=False)
            return validity[key]
        for basis, ids, matrix in source():
            if cancelled():
                return []
            # A fixed row reduction makes scores independent of block length
            # and row position. BLAS GEMV can round identical vectors differently
            # in its tail lanes, changing tie winners after base/delta splitting.
            scores = np.einsum('ij,j->i', matrix.astype('<f4'), q, optimize=False)
            if not basis:
                # Short path hints otherwise systematically outrank stronger
                # evidence from the extracted body in this static model.
                scores *= .75
            if len(best) >= limit:
                take = scores >= heap[0][0]
                scores, ids = scores[take], ids[take]
            for score, (fid, uid) in zip(scores.tolist(), ids.tolist()):
                candidate = (score, basis, uid)
                if fid in best:
                    if candidate <= best[fid]:
                        continue
                elif len(best) >= limit:
                    while heap and best.get(heap[0][3]) != heap[0][:3]:
                        heapq.heappop(heap)
                    if candidate <= heap[0][:3]:
                        continue
                    if not valid(fid, basis, uid):
                        continue
                    del best[heapq.heappop(heap)[3]]
                if not valid(fid, basis, uid):
                    continue
                best[fid] = candidate
                heapq.heappush(heap, (*candidate, fid))
                if len(heap) > max(64, 2*limit):
                    heap[:] = [(*value, fid) for fid, value in best.items()]
                    heapq.heapify(heap)
        if saved is not None and not cancelled():
            self.cache.put(revision, saved, used)
        ranked = sorted(best, key=best.get, reverse=True)
        results = {}
        for offset in range(0, len(ranked), 400):
            ids = ranked[offset:offset+400]
            for row in db.execute('SELECT id AS file_id,path,name,kind,status,error,size,mtime FROM files WHERE id IN ('+
                                  ','.join('?' for _ in ids)+')', ids):
                fid = row['file_id']
                score, basis, uid = best[fid]
                value = dict(row, unit_id=None, text='', location={}, similarity=score,
                             semantic_stage='quick', quick_basis='text' if basis else 'name')
                if basis:
                    unit = db.execute('SELECT text,location FROM units WHERE id=? AND file_id=?', (uid, fid)).fetchone()
                    if not unit:
                        continue
                    value.update(unit_id=uid, text=unit['text'], location=json.loads(unit['location']))
                results[fid] = value
        return [results[fid] for fid in ranked if fid in results]


def coverage(db, model, *, eligible=None, reconciled=False):
    clause, args = ('1=1', ()) if reconciled else catalog.scope_clause(catalog.search_scope(db), 'f.path_key')
    if eligible is None:
        eligible = db.execute("SELECT count(*) FROM files f WHERE f.status<>'metadata' AND "+clause, args).fetchone()[0]
    if reconciled:
        # Source triggers remove renamed/deleted/ineligible file hints. Once the
        # scope prune completes this covering index answers without path scans.
        covered = db.execute("SELECT count(*) FROM quick_vectors WHERE kind='name' AND model=?", (model,)).fetchone()[0]
    else:
        covered = db.execute("""SELECT count(*) FROM quick_vectors v CROSS JOIN files f ON f.id=v.source_id
            WHERE v.kind='name' AND v.model=? AND v.identity=f.path AND f.status<>'metadata' AND """+clause,
                             (model, *args)).fetchone()[0]
    texts = db.execute("""SELECT count(*) FROM quick_vectors v CROSS JOIN units u ON u.id=v.source_id
        CROSS JOIN files f ON f.id=u.file_id WHERE v.kind='text' AND v.model=? AND v.identity=u.digest
        AND f.status='ready' AND """+clause, (model, *args)).fetchone()[0]
    return dict(covered_files=covered, eligible_files=eligible, text_fragments=texts)


def settle_work(db, model):
    """A useful quick representation frees the producer; deep work stays queued.

    The producer bound is about unconsumed extraction, not requiring a large
    model to reside in memory. Visual-only files keep their full-model units.
    """
    with storage.transaction(db):
        done = []
        for (fid,) in db.execute('SELECT file_id FROM exploration_work LIMIT 64').fetchall():
            first = db.execute("""SELECT u.id,u.text,u.digest,f.path FROM units u
                JOIN files f ON f.id=u.file_id WHERE u.file_id=? AND f.status='ready'
                AND coalesce(json_extract(u.location,'$.semantic'),1)<>0
                ORDER BY u.ordinal LIMIT 1""", (fid,)).fetchone()
            if first is None:
                done.append((fid,))
                continue
            kind, sid, identity = ('text', first['id'], first['digest']) if first['text'] else ('name', fid, first['path'])
            if db.execute('SELECT 1 FROM quick_vectors WHERE kind=? AND source_id=? AND identity=? AND model=?',
                          (kind, sid, identity, model)).fetchone():
                done.append((fid,))
        db.executemany('DELETE FROM exploration_work WHERE file_id=?', done)


class QuickService:
    def __init__(self, db_path, path, inventory, events):
        self.model = None
        self.error = ''
        self.coverage = {}
        self.revision = 0
        self.stopping = threading.Event()
        self.inventory, self.events = inventory, events
        self.thread = threading.Thread(target=self._run, args=(db_path, path), name='quick-semantics', daemon=True)
        self.thread.start()

    def _run(self, db_path, path):
        db = None
        try:
            from .quick_embeddings import QuickEmbedder
            self.model = QuickEmbedder(path)
            self.events.put({'type': 'quick_model', 'ready': True})
            db = store.connect(db_path, initialize=False)
            cached_revision, last_cache_attempt = None, 0.
            while not self.stopping.is_set():
                if self.inventory.paused:
                    self.stopping.wait(.1)
                    continue
                try:
                    new = pump(db, self.model.key)
                    done = 0
                    _, preferred = self.inventory.priority()
                    for kind, limit in (('name', 256), ('text', 64)):
                        rows = pending(db, self.model.key, kind, preferred, limit)
                        if rows:
                            values = self.model.encode_texts([hint(r) if kind == 'name' else r['text'] for r in rows])
                            done += persist(db, self.model.key, kind, rows, values,
                                            on_stale=self.inventory.changed)
                    settle_work(db, self.model.key)
                    if done:
                        self.revision += 1
                        self.inventory.revision += 1
                    if not new and not done:
                        revision = state(db, 'revision')
                        if revision != cached_revision and time.monotonic()-last_cache_attempt >= 2:
                            last_cache_attempt = time.monotonic()
                            try:
                                from . import quick_cache
                                dimensions = getattr(self.model, 'dimensions', 0)
                                if dimensions and quick_cache.build(db_path, self.model.key, dimensions, self.stopping,
                                                                    min_compaction_age=30):
                                    cached_revision = revision
                            except Exception:
                                # An optional acceleration failure must never
                                # stop indexing or the authoritative SQL path.
                                log.warning('Quick vector cache unavailable', exc_info=True)
                        self.stopping.wait(.3)
                except (storage.BudgetFull, storage.WriterBusy) as exc:
                    self.error = str(exc)
                    self.stopping.wait(2)
                except Exception as exc:
                    if storage.is_busy(exc):
                        db.rollback()
                        self.stopping.wait(.1)
                    else:
                        raise
        except Exception as exc:
            self.error = str(exc)
            log.exception('Quick semantic service failed')
            self.events.put({'type': 'quick_model', 'ready': False, 'error': str(exc)})
        finally:
            if db is not None:
                db.close()

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=3)
