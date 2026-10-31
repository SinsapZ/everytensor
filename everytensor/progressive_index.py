"""Version-checked publication of bounded portions of long files."""
import hashlib
import json
from pathlib import Path
import time

from . import content_quality, media, storage, store, work_costs


def advance(db, row, limit, checkpoint):
    from .indexer import SCHEMA
    started = time.monotonic()
    path = Path(row['path'])
    expected = row['size'], row['mtime']
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != expected:
        return 0
    job = db.execute('SELECT * FROM extraction_jobs WHERE file_id=?', (row['id'],)).fetchone()
    if job and (job['size'], job['mtime'], job['schema']) == (*expected, SCHEMA):
        ordinal, fingerprint = job['ordinal'], job['fingerprint']
        resume = json.loads(job['cursor'] or '{}')
    else:
        ordinal, fingerprint = 0, media.digest_file(path, checkpoint)
        resume = {}
        if row['fingerprint'] == SCHEMA+fingerprint:
            checkpoint()
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != expected:
                return 0
            with storage.transaction(db):
                current = db.execute('SELECT size,mtime FROM files WHERE id=?', (row['id'],)).fetchone()
                if not current or tuple(current) != expected:
                    return 0
                db.execute("UPDATE files SET status='ready',error='' WHERE id=?", (row['id'],))
                db.execute('DELETE FROM extraction_jobs WHERE file_id=?', (row['id'],))
            return 0
    generator = media.extract(path, resume=resume) if resume else media.extract(path)
    items = []
    try:
        # A saved ordinal survives process exit; no decoded payload is persisted.
        for index, item in enumerate(generator):
            checkpoint()
            if not resume and index < ordinal:
                continue
            items.append(item)
            if len(items) > limit:
                break
    finally:
        generator.close()
    finished = len(items) <= limit
    prepared = []
    for index, (text, loc) in enumerate(items[:limit], ordinal):
        if text and loc.get('semantic') is not False:
            loc = content_quality.annotate(text, loc, kind=row['kind'])
        location = json.dumps(loc, ensure_ascii=False, sort_keys=True)
        identity = text if text else fingerprint+location
        digest = hashlib.sha256((SCHEMA+row['kind']+identity).encode()).hexdigest()
        prepared.append((row['id'], index, text, location, digest, ' '.join(store.tokens(text)) if text else ''))
    checkpoint()
    stat = path.stat()
    if (stat.st_size, stat.st_mtime_ns) != expected:
        return 0
    budget = max(65536, sum(len(r[2].encode())*12+len(r[3])*4+len(r[5].encode())*2+2048 for r in prepared))
    with storage.write(db, budget):
        current = db.execute('SELECT size,mtime FROM files WHERE id=?', (row['id'],)).fetchone()
        if not current or tuple(current) != expected:
            return 0
        if ordinal == 0:
            db.execute('DELETE FROM units WHERE file_id=?', (row['id'],))
        for fid, index, text, location, digest, tokens in prepared:
            cursor = db.execute('INSERT INTO units(file_id,ordinal,text,location,digest) VALUES(?,?,?,?,?)',
                                (fid, index, text, location, digest))
            if text:
                db.execute('INSERT INTO content_fts(rowid,tokens) VALUES(?,?)', (cursor.lastrowid, tokens))
        db.execute("UPDATE files SET status='ready',error='',fingerprint=? WHERE id=?",
                   (SCHEMA+fingerprint if finished else None, row['id']))
        if finished:
            db.execute('DELETE FROM extraction_jobs WHERE file_id=?', (row['id'],))
        else:
            db.execute('INSERT OR REPLACE INTO extraction_jobs VALUES(?,?,?,?,?,?,?)',
                       (row['id'], *expected, SCHEMA, fingerprint, ordinal+len(prepared),
                        json.dumps(items[limit][1], ensure_ascii=False)))
        # Per-byte observations use complete files only; partial work costs are
        # recorded separately so they cannot imply a whole file has been read.
        work_costs.observe(db, 'extract_chunk', path.suffix.lower(), max(1, len(prepared)), time.monotonic()-started)
        if ordinal == 0 and finished:
            work_costs.observe(db, 'extract', path.suffix.lower(), max(1, row['size']), time.monotonic()-started)
    return int(finished)
