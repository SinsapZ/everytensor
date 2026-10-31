"""Bounded retrieval combining literal filename and independently checked text.

Candidate names come from the current catalog, not a resource-word dictionary.
Indexed bodies are reused; small unindexed text files can be read without models.
"""
from pathlib import Path
from collections import deque
import json
import stat
import time

from . import catalog, media, query_evidence

MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_FILES = 32
MAX_CANDIDATES = 128
# This work runs off the UI thread. Allow cold local reads under memory pressure
# while keeping a fixed cancellation/time cap, independent of the query.
TIME_BUDGET = 1.


def candidates(db, description, scope, kind, cancelled):
    allowed, args = catalog.scope_clause(scope, 'path_key')
    columns = 'id AS file_id,path,name,kind,status,error,size,mtime'
    common = f"status IN ('pending','ready','error') AND (?='' OR kind=?) AND {allowed}"
    seen = set()
    for clue in sorted(query_evidence.clues(description), key=lambda item: -len(item[0])):
        if cancelled():
            break
        value = clue[0]
        # Literal names and stems use the B-tree, irrespective of their subject.
        for condition, params in (('name_key=?', (value,)),
                                  ('name_key>=? AND name_key<?', (value+'.', value+'/'))):
            # Without an explicit index SQLite can scan every pending file for
            # a stem range, exhausting the deadline before checking any body.
            rows = db.execute(f'''SELECT {columns} FROM files INDEXED BY files_name_key
                                  WHERE {condition} AND {common} ORDER BY size,id LIMIT ?''',
                              (*params, kind, kind, *args, MAX_CANDIDATES)).fetchall()
            # Repeated source-code basenames must not consume the whole read
            # budget before another extension is considered. Each discovered
            # extension gets a turn; none has a built-in priority.
            groups = {}
            for row in rows:
                groups.setdefault(Path(row['name']).suffix.casefold(), deque()).append(row)
            groups = deque(groups.values())
            while groups and not cancelled():
                group = groups.popleft()
                row = group.popleft()
                if group:
                    groups.append(group)
                if row['file_id'] not in seen:
                    seen.add(row['file_id'])
                    yield dict(row), clue
                if len(seen) >= MAX_CANDIDATES:
                    return


def search(db, query, kind='', limit=300, cancelled=lambda: False, exclude=()):
    from . import store
    description = query_evidence.clean(store.query_topic(query))
    if len(store.lexical_terms(description)) < 2 or limit <= 0:
        return []
    kind = kind or store.query_intent(query)[0]
    scope = catalog.search_scope(db)
    live = store._LiveFiles(db, scope)
    deadline = time.monotonic()+TIME_BUDGET
    stopped = lambda: cancelled() or time.monotonic() >= deadline
    found, used, opened = [], 0, 0
    for row, clue in candidates(db, description, scope, kind, stopped):
        if stopped() or opened >= MAX_FILES or used >= MAX_TOTAL_BYTES:
            break
        if row['file_id'] in exclude:
            continue
        body_query = query_evidence.remainder(description, clue)
        terms = set(store.lexical_terms(body_query))
        if not terms:
            continue
        if row['status'] == 'ready':
            expression = ' AND '.join('"'+term+'"' for term in sorted(terms))
            unit = db.execute('''SELECT u.id,u.text,u.location FROM content_fts
                    JOIN units u ON u.id=content_fts.rowid
                    WHERE content_fts MATCH ? AND u.file_id=? ORDER BY content_fts.rank LIMIT 1''',
                              (expression, row['file_id'])).fetchone()
            if unit is not None:
                item = dict(row, text=unit['text'], location=json.loads(unit['location']), unit_id=unit['id'],
                            literal_match=query_evidence.evidence(body_query, unit['text'], clue[0]))
                if live.accept(item, content=True):
                    found.append(item)
                if len(found) >= limit:
                    break
                continue
        path = Path(row['path'])
        if path.suffix.lower() not in media.TEXT | media.CODE or not 0 < (row['size'] or 0) <= MAX_FILE_BYTES:
            continue
        try:
            before = path.stat(follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or getattr(before, 'st_file_attributes', 0) & 0x400 or
                    not catalog.content_allowed(path, row['kind'], before) or
                    (before.st_size, before.st_mtime_ns) != (row['size'], row['mtime'])):
                continue
            opened += 1
            allowance = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES-used)
            with path.open('rb') as handle:
                raw = handle.read(allowance+1)
            used += len(raw)
            if len(raw) > allowance:
                continue
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                continue
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gb18030', 'replace')
            for chunk, location in media.chunks(text):
                if stopped():
                    break
                if terms.issubset(set(store.tokens(chunk))):
                    item = dict(row, text=chunk, location=location, unit_id=None, content_source='live',
                                literal_match=query_evidence.evidence(body_query, chunk, clue[0]))
                    if live.accept(item, content=True):
                        found.append(item)
                    break
            if len(found) >= limit:
                break
        except (OSError, UnicodeError):
            continue
    return found
