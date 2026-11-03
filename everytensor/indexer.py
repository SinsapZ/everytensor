from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat as stat_mode
import uuid
from pathlib import Path
from . import media, store, catalog, storage, discovery

SCHEMA = 'extract-v3-quality-office-audio'


def upsert_metadata(db, batch):
    """Shared by bulk discovery and live notifications; caller owns the writer."""
    for row in batch:
        previous = db.execute('SELECT id,path FROM files WHERE identity=? AND path<>? LIMIT 2',
                              (row[7], row[0])).fetchall() if row[7] else []
        if len(previous) == 1 and (not Path(previous[0]['path']).exists() or
                                  os.path.normcase(previous[0]['path']) == os.path.normcase(row[0])):
            db.execute('UPDATE files SET path=?,name=?,root=? WHERE id=?',
                       (row[0], row[2], row[1], previous[0]['id']))
    db.executemany('''INSERT INTO files(path,root,name,kind,size,mtime,status,identity,scan_token)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
        root=excluded.root,kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,identity=excluded.identity,
        scan_token=CASE WHEN excluded.scan_token='' THEN files.scan_token ELSE excluded.scan_token END,
        status=CASE WHEN excluded.status='metadata' THEN 'metadata'
            WHEN files.size<>excluded.size OR files.mtime<>excluded.mtime OR files.status='metadata'
            THEN 'pending' ELSE files.status END,
        error=CASE WHEN files.size<>excluded.size OR files.mtime<>excluded.mtime THEN '' ELSE files.error END''', batch)


def refresh_file(db, root, path):
    """Publish a stable live file without waiting for a volume traversal."""
    root, path = catalog.canonical(root), Path(catalog.canonical(path))
    if not catalog.within(path, root) or any(catalog.within(path, p) for p in catalog.excluded_paths(db)):
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None  # Directory moves/deletions are reconciled by discovery.
        stat = path.stat()
    except OSError:
        return None
    kind = media.kind(path)
    eligible = catalog.content_allowed(path, kind, stat)
    identity = f'{Path(root).anchor.casefold()}:{stat.st_ino}' if eligible and stat.st_ino else ''
    row = (str(path), root, path.name, kind, stat.st_size, stat.st_mtime_ns,
           'pending' if eligible else 'metadata', identity, '')
    with storage.write(db, max(65536, len(str(path))*80+4096), catalog=True):
        upsert_metadata(db, [row])
        found = db.execute('SELECT id FROM files WHERE path=?', (str(path),)).fetchone()
    return found[0] if eligible else None


def prune_scope(db, roots, exclusions, checkpoint=lambda: None):
    """Filter scope in SQLite instead of canonicalizing every stored path in Python."""
    def ranges(paths):
        clauses, values = [], []
        for path in paths:
            key = catalog.search_key(catalog.canonical(path))
            prefix = key.rstrip('\\')+'\\'
            clauses.append('(path_key=? OR (path_key>=? AND path_key<?))')
            values.extend((key, prefix, prefix+'\U0010ffff'))
        return '('+' OR '.join(clauses)+')' if clauses else '0', values
    allowed, allow_args = ranges(roots)
    excluded, exclude_args = ranges(exclusions)
    last, removed = 0, 0
    while True:
        checkpoint()
        rows = db.execute(f'''SELECT id FROM files WHERE id>? AND (NOT {allowed} OR {excluded})
            ORDER BY id LIMIT 500''', (last, *allow_args, *exclude_args)).fetchall()
        if not rows:
            return removed
        with storage.transaction(db):
            db.executemany('DELETE FROM files WHERE id=?', rows)
        removed += len(rows)
        last = rows[-1][0]


def scan(db, root, checkpoint=lambda: None, *, subtree=None, progress=lambda _: None, resume=False):
    """Stream metadata in batches; reconcile deletions only after a complete scan."""
    root = catalog.canonical(root)
    scope = catalog.canonical(subtree or root)
    if not catalog.within(scope, root):
        raise ValueError('扫描范围不属于索引根目录')
    if not Path(root).is_dir():
        return {'offline': root}
    if store.setting(db, 'extractor_version') != SCHEMA:
        with storage.transaction(db):
            db.execute("UPDATE files SET status='pending' WHERE status<>'metadata'")
        store.set_setting(db, 'extractor_version', SCHEMA)
    exclusions = catalog.excluded_paths(db)
    home = Path.home()
    priority = store.setting(db, 'roots', []) + [str(home/p) for p in
        ('Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos', 'Music')] + [str(home)]
    priority = [catalog.canonical(p) for p in priority if catalog.within(p, scope) and catalog.canonical(p) != scope]
    session = discovery.Session(db, root, priority) if resume and subtree is None else None
    token, seen, error_count, errors, batch = uuid.uuid4().hex, 0, 0, [], []
    if session:
        token, seen = session.token, session.seen
        progress(f'继续核对文件 · 已检查 {seen:,}' if session.resumed else '正在核对文件变化')
    completed = []

    def error(exc):
        nonlocal error_count
        error_count += 1
        if len(errors) < 20:
            errors.append(str(exc))

    def flush():
        if not batch:
            return
        with storage.write(db, max(65536, sum(len(r[0])*80+4096 for r in batch)), catalog=True):
            upsert_metadata(db, batch)
        batch.clear()
        progress(f'核对文件 · {seen:,}')

    def walk(folder):
        nonlocal seen
        checkpoint()
        if (Path(folder).name.casefold() == '.everytensor' or
                any(catalog.within(folder, p) for p in exclusions+completed)):
            return
        try:
            # A remembered directory can become a junction/symlink while the
            # app is closed. Do not follow that stale frontier outside scope.
            mode = os.stat(folder, follow_symlinks=False)
            if not stat_mode.S_ISDIR(mode.st_mode) or getattr(mode, 'st_file_attributes', 0) & 0x400:
                return
            with os.scandir(folder) as entries:
                for entry in entries:
                    checkpoint()
                    try:
                        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        if entry.is_dir(follow_symlinks=False):
                            if not getattr(stat, 'st_file_attributes', 0) & 0x400:
                                yield entry.path
                            continue
                        if any(catalog.within(entry.path, p) for p in exclusions):
                            continue
                        kind = media.kind(entry.path)
                        eligible = catalog.content_allowed(entry.path, kind, stat)
                        # Windows DirEntry.stat() leaves st_ino zero. inode() gets
                        # the real file ID, needed only for content-bearing files.
                        inode = stat.st_ino or (entry.inode() if eligible else 0)
                        identity = f'{Path(root).anchor.casefold()}:{inode}' if inode else ''
                        batch.append((entry.path, root, entry.name, kind, stat.st_size, stat.st_mtime_ns,
                                      'pending' if eligible else 'metadata', identity, token))
                        seen += 1
                        if len(batch) >= 256:
                            flush()
                    except OSError as exc:
                        error(exc)
        except FileNotFoundError:
            return
        except OSError as exc:
            error(exc)

    if session:
        # Completed directories survive restarts. Only the interrupted directory
        # may be enumerated again; inserts and metadata writes are idempotent.
        while job := session.next():
            checkpoint()
            children = []
            for child in walk(job['path']):
                children.append(child)
                if len(children) >= 128:
                    session.add(children, job['priority'])
                    children.clear()
            if children:
                session.add(children, job['priority'])
            flush()
            session.finish_directory(job, seen)
        session.walk_done()
    else:
        # Short subtree reconciliations retain a bounded in-memory iterator stack.
        for start in priority+[scope]:
            try:
                os.stat(start)
                stack = [iter(walk(start))]
            except FileNotFoundError:
                stack = []
            except OSError as exc:
                error(exc)
                stack = []
            while stack:
                checkpoint()
                try:
                    stack.append(iter(walk(next(stack[-1]))))
                except StopIteration:
                    stack.pop()
            completed.append(start)
    flush()
    # Live notifications may publish a new file after this traversal passed its
    # folder. A missing scan token alone is therefore not evidence of deletion.
    last = session.cursor if session else 0
    prefix = catalog.search_key(scope).rstrip('\\')+'\\'
    while rows := db.execute('''SELECT id,path FROM files WHERE root=? AND scan_token<>?
            AND path_key>=? AND path_key<? AND id>? ORDER BY id LIMIT 256''',
            (root, token, prefix, prefix+'\U0010ffff', last)).fetchall():
        removed = []
        for row in rows:
            checkpoint()
            try:
                os.stat(row['path'])
            except FileNotFoundError:
                removed.append((row['id'],))
            except OSError:
                pass
        if removed and Path(root).is_dir():
            with storage.write(db, 65536, catalog=True):
                db.executemany('DELETE FROM files WHERE id=?', removed)
        last = rows[-1]['id']
        if session:
            session.checkpoint_cleanup(last)
    if session:
        session.finish()
    return {'seen': seen, 'errors': errors, 'error_count': error_count,
            'resumed': bool(session and session.resumed)}


def index_pending(db, checkpoint=lambda: None, progress=lambda _: None, limit=None, *, file_id=None, max_units=None):
    checkpoint_error = None

    def checked_checkpoint():
        nonlocal checkpoint_error
        try:
            checkpoint()
        except Exception as exc:
            # Callers use exceptions to stop/reconfigure indexing. Preserve
            # that control flow without coupling extraction to the scheduler's
            # exception type or treating an interruption as a damaged file.
            checkpoint_error = exc
            raise

    def pending():
        # Do not hold a live read cursor across extraction and concurrent writes:
        # that old WAL snapshot cannot subsequently be upgraded to a writer.
        last, remaining = 0, limit
        while remaining is None or remaining > 0:
            rows = db.execute("SELECT * FROM files WHERE status='pending' AND id>? ORDER BY id LIMIT ?",
                              (last, min(128, remaining) if remaining is not None else 128)).fetchall()
            if not rows:
                return
            yield from rows
            last = rows[-1]['id']
            if remaining is not None:
                remaining -= len(rows)
    rows = (db.execute("""SELECT * FROM files WHERE id=? AND (status='pending' OR
                       EXISTS(SELECT 1 FROM extraction_jobs j WHERE j.file_id=files.id))""", (file_id,)).fetchall()
            if file_id is not None else pending())
    count = 0
    for row in rows:
        checkpoint()
        path = Path(row['path'])
        progress(f'分析内容 · {path.name}')
        try:
            if max_units is not None:
                from .progressive_index import advance
                count += advance(db, row, max(1, int(max_units)), checked_checkpoint)
                continue
            fingerprint = media.digest_file(path, checked_checkpoint)
            if row['fingerprint'] == SCHEMA+fingerprint:
                with storage.write(db, 16384):
                    db.execute("UPDATE files SET status='ready',error='' WHERE id=?", (row['id'],))
                continue
            # Staging is part of the budget. Interrupted extraction keeps the old generation.
            with storage.transaction(db):
                db.execute('CREATE TABLE IF NOT EXISTS staged(ordinal INTEGER,text TEXT,location TEXT,digest TEXT,tokens TEXT)')
                if 'tokens' not in {r[1] for r in db.execute('PRAGMA table_info(staged)')}:
                    db.execute('ALTER TABLE staged ADD COLUMN tokens TEXT')
                db.execute('DELETE FROM staged')
            staged = []
            def flush():
                if staged:
                    estimate = sum(len(x[1].encode('utf-8'))*12+len(x[2])*4+len(x[4].encode('utf-8'))*2+1024 for x in staged)
                    with storage.write(db, max(65536, estimate)):
                        db.executemany('INSERT INTO staged(ordinal,text,location,digest,tokens) VALUES(?,?,?,?,?)', staged)
                    staged.clear()
            for ordinal, (text, loc) in enumerate(media.extract(path)):
                checked_checkpoint()
                if text and loc.get('semantic') is not False:
                    from .content_quality import annotate
                    loc = annotate(text, loc, kind=row['kind'])
                location = json.dumps(loc, ensure_ascii=False, sort_keys=True)
                identity = text if text else fingerprint+location
                digest = hashlib.sha256((SCHEMA+row['kind']+identity).encode()).hexdigest()
                # Tokenization is CPU work: keep it outside the shared writer lock,
                # so directory discovery and vector commits continue concurrently.
                staged.append((ordinal, text, location, digest, ' '.join(store.tokens(text)) if text else ''))
                if len(staged) >= 64:
                    flush()
            flush()
            stat = path.stat()
            if stat.st_size != row['size'] or stat.st_mtime_ns != row['mtime']:
                continue
            estimate = db.execute('SELECT coalesce(sum(length(CAST(text AS BLOB))*12+2048),0) FROM staged').fetchone()[0]
            with storage.write(db, max(65536, estimate)):
                live = db.execute('SELECT size,mtime FROM files WHERE id=?', (row['id'],)).fetchone()
                if not live or tuple(live) != (stat.st_size, stat.st_mtime_ns):
                    continue
                db.execute('DELETE FROM units WHERE file_id=?', (row['id'],))
                db.execute('''INSERT INTO units(file_id,ordinal,text,location,digest)
                    SELECT ?,ordinal,text,location,digest FROM staged''', (row['id'],))
                db.execute('''INSERT INTO content_fts(rowid,tokens)
                    SELECT u.id,s.tokens FROM staged s JOIN units u ON u.ordinal=s.ordinal
                    WHERE u.file_id=? AND s.text<>'' ''', (row['id'],))
                db.execute("UPDATE files SET fingerprint=?,status='ready',error='' WHERE id=?", (SCHEMA+fingerprint, row['id']))
                db.execute('DELETE FROM staged')
            count += 1
        except storage.BudgetFull:
            db.rollback()
            raise
        except sqlite3.OperationalError as exc:
            db.rollback()
            if 'full' in str(exc).lower():
                raise storage.BudgetFull('索引空间已达预算') from exc
            raise
        except Exception as exc:
            db.rollback()
            if exc is checkpoint_error:
                raise
            with storage.transaction(db):
                db.execute("UPDATE files SET status='error',error=? WHERE id=?", (str(exc)[:600], row['id']))
                db.execute('DELETE FROM extraction_jobs WHERE file_id=?', (row['id'],))
    if limit is None:
        with storage.transaction(db):
            db.execute('DELETE FROM vectors WHERE NOT EXISTS(SELECT 1 FROM units WHERE units.digest=vectors.digest)')
    return count
