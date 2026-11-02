from __future__ import annotations

import json
import re
import sqlite3
import heapq
import time
import os
import stat
from contextlib import contextmanager
from collections import OrderedDict
from pathlib import Path
from .cache import ByteCache
from . import catalog, query_evidence

SCHEMA_VERSION = 3


def connect(path, *, initialize=True):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.create_function('search_key', 1, catalog.search_key, deterministic=True)
    db.create_function('search_grams', 1, catalog.grams, deterministic=True)
    db.create_function('parent_path', 1, lambda p: str(Path(p).parent), deterministic=True)
    db.create_function('index_names_now', 0, lambda: 1)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA wal_autocheckpoint=256')
    db.execute('PRAGMA journal_size_limit=4194304')
    db.execute('PRAGMA cache_size=-8192')
    if not initialize:
        return db
    if db.execute('PRAGMA user_version').fetchone()[0] >= SCHEMA_VERSION:
        return db
    db.executescript("""
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS work_costs(phase TEXT,kind TEXT,units REAL,seconds REAL,samples INTEGER,
      PRIMARY KEY(phase,kind));
    CREATE TABLE IF NOT EXISTS files(
      id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL,root TEXT NOT NULL,
      name TEXT NOT NULL,kind TEXT NOT NULL,size INTEGER,mtime INTEGER,
      fingerprint TEXT,status TEXT NOT NULL DEFAULT 'pending',error TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS extraction_jobs(
      file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
      size INTEGER,mtime INTEGER,schema TEXT,fingerprint TEXT,ordinal INTEGER,cursor TEXT DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS units(
      id INTEGER PRIMARY KEY,file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
      ordinal INTEGER NOT NULL,text TEXT NOT NULL DEFAULT '',location TEXT NOT NULL,
      digest TEXT NOT NULL,UNIQUE(file_id,ordinal));
    CREATE TABLE IF NOT EXISTS vectors(
      digest TEXT NOT NULL,model TEXT NOT NULL,dim INTEGER NOT NULL,data BLOB NOT NULL,
      PRIMARY KEY(digest,model));
    CREATE INDEX IF NOT EXISTS units_digest ON units(digest);
    CREATE INDEX IF NOT EXISTS files_root ON files(root);
    CREATE TABLE IF NOT EXISTS embedding_errors(
      digest TEXT NOT NULL,model TEXT NOT NULL,error TEXT NOT NULL,updated REAL NOT NULL,
      PRIMARY KEY(digest,model));
    CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(tokens);
    CREATE TRIGGER IF NOT EXISTS units_delete AFTER DELETE ON units BEGIN
      DELETE FROM content_fts WHERE rowid=old.id;
    END;
    """)
    columns = {r[1] for r in db.execute('PRAGMA table_info(files)')}
    for name, definition in [('name_key', "TEXT NOT NULL DEFAULT ''"),
                             ('path_key', "TEXT NOT NULL DEFAULT ''"),
                             ('parent_id', 'INTEGER'), ('scan_token', "TEXT NOT NULL DEFAULT ''"),
                             ('name_indexed', 'INTEGER NOT NULL DEFAULT 1'),
                             ('identity', "TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:
            db.execute(f'ALTER TABLE files ADD COLUMN {name} {definition}')
    db.commit()
    db.executescript("""
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS directories(id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL);
    CREATE VIRTUAL TABLE IF NOT EXISTS name_fts USING fts5(tokens,detail=none,columnsize=0);
    CREATE VIRTUAL TABLE IF NOT EXISTS directory_fts USING fts5(tokens,detail=none,columnsize=0);
    CREATE VIRTUAL TABLE IF NOT EXISTS name_terms USING fts5vocab(name_fts,'row');
    CREATE VIRTUAL TABLE IF NOT EXISTS directory_terms USING fts5vocab(directory_fts,'row');
    CREATE TABLE IF NOT EXISTS name_frequencies(term TEXT PRIMARY KEY,doc INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS directory_frequencies(term TEXT PRIMARY KEY,doc INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS files_name_key ON files(name_key,id);
    CREATE INDEX IF NOT EXISTS files_kind_name ON files(kind,name_key,id);
    CREATE INDEX IF NOT EXISTS files_parent ON files(parent_id);
    CREATE INDEX IF NOT EXISTS files_path_key ON files(path_key);
    CREATE INDEX IF NOT EXISTS files_identity ON files(identity);
    CREATE INDEX IF NOT EXISTS files_name_pending ON files(name_indexed,id);
    CREATE INDEX IF NOT EXISTS files_pending ON files(status,id);
    CREATE INDEX IF NOT EXISTS files_content_queue ON files(root,parent_id,id) WHERE status='pending';
    CREATE INDEX IF NOT EXISTS files_content_kind_queue ON files(kind,root,parent_id,id) WHERE status='pending';
    CREATE INDEX IF NOT EXISTS units_frontier ON units(ordinal,file_id);
    CREATE INDEX IF NOT EXISTS vectors_model ON vectors(model,dim);
    CREATE TRIGGER IF NOT EXISTS directory_search_insert AFTER INSERT ON directories BEGIN
      INSERT INTO directory_fts(rowid,tokens) VALUES(new.id,search_grams(new.path));
    END;
    CREATE TRIGGER IF NOT EXISTS directory_search_delete AFTER DELETE ON directories BEGIN
      DELETE FROM directory_fts WHERE rowid=old.id;
    END;
    DROP TRIGGER IF EXISTS files_search_insert;
    DROP TRIGGER IF EXISTS files_search_update;
    CREATE TRIGGER files_search_insert AFTER INSERT ON files BEGIN
      INSERT OR IGNORE INTO directories(path) VALUES(parent_path(new.path));
      UPDATE files SET name_key=search_key(new.name),path_key=search_key(new.path),
        parent_id=(SELECT id FROM directories WHERE path=parent_path(new.path)),name_indexed=index_names_now() WHERE id=new.id;
      INSERT INTO name_fts(rowid,tokens) SELECT new.id,search_grams(new.name) WHERE index_names_now();
    END;
    CREATE TRIGGER files_search_update AFTER UPDATE OF path,name ON files
    WHEN old.path<>new.path OR old.name<>new.name BEGIN
      INSERT OR IGNORE INTO directories(path) VALUES(parent_path(new.path));
      UPDATE files SET name_key=search_key(new.name),path_key=search_key(new.path),
        parent_id=(SELECT id FROM directories WHERE path=parent_path(new.path)),name_indexed=index_names_now() WHERE id=new.id;
      DELETE FROM name_fts WHERE rowid=new.id;
      INSERT INTO name_fts(rowid,tokens) SELECT new.id,search_grams(new.name) WHERE index_names_now();
    END;
    CREATE TRIGGER IF NOT EXISTS files_search_delete AFTER DELETE ON files BEGIN
      DELETE FROM name_fts WHERE rowid=old.id;
    END;
    COMMIT;
    """)
    # One-time bounded backfill; old vectors/content remain untouched.
    while rows := db.execute("SELECT id,path,name FROM files WHERE parent_id IS NULL LIMIT 500").fetchall():
        with db:
            for row in rows:
                parent = str(Path(row['path']).parent)
                db.execute('INSERT OR IGNORE INTO directories(path) VALUES(?)', (parent,))
                db.execute('UPDATE files SET name_key=?,path_key=?,parent_id=(SELECT id FROM directories WHERE path=?) WHERE id=?',
                           (catalog.search_key(row['name']), catalog.search_key(row['path']), parent, row['id']))
                db.execute('INSERT INTO name_fts(rowid,tokens) VALUES(?,?)', (row['id'], catalog.grams(row['name'])))
    if 'cursor' not in {r[1] for r in db.execute('PRAGMA table_info(extraction_jobs)')}:
        db.execute("ALTER TABLE extraction_jobs ADD COLUMN cursor TEXT DEFAULT '{}'")
    from . import frontier
    frontier.install(db)
    from . import discovery
    discovery.install(db)
    from . import query_vectors
    query_vectors.install(db)
    db.executescript('''
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS search_revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
    INSERT OR IGNORE INTO search_revision VALUES(1,0);
    CREATE TRIGGER IF NOT EXISTS search_files_insert AFTER INSERT ON files BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_files_delete AFTER DELETE ON files BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_files_update
    AFTER UPDATE OF path,name,kind,size,mtime,status ON files
    WHEN old.path IS NOT new.path OR old.name IS NOT new.name OR old.kind IS NOT new.kind
      OR old.size IS NOT new.size OR old.mtime IS NOT new.mtime OR old.status IS NOT new.status BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_units_insert AFTER INSERT ON units BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_units_delete AFTER DELETE ON units BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_units_update AFTER UPDATE ON units BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_vectors_insert AFTER INSERT ON vectors BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_vectors_delete AFTER DELETE ON vectors BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    CREATE TRIGGER IF NOT EXISTS search_vectors_update AFTER UPDATE ON vectors BEGIN
      UPDATE search_revision SET value=value+1 WHERE id=1;
    END;
    COMMIT;
    ''')
    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
    return db


def search_revision(db):
    """Only source/index changes invalidate results, not coverage/query-cache writes."""
    try:
        return db.execute('SELECT value FROM search_revision WHERE id=1').fetchone()[0]
    except sqlite3.OperationalError:
        # Read-only tooling can still open a catalog created before this migration.
        return (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0])


@contextmanager
def read_snapshot(db):
    own = not db.in_transaction
    if own:
        db.execute('BEGIN')
    try:
        yield
    finally:
        if own:
            db.rollback()


class _LiveFiles:
    def __init__(self, db, scope=None, max_entries=1024):
        self.db = db
        self.scope = catalog.search_scope(db) if scope is None else scope
        def boundaries(paths):
            keys = (catalog.search_key(catalog.canonical(path)) for path in paths)
            return tuple((key, key.rstrip('\\')+'\\') for key in keys)
        self.roots = None if self.scope[0] is None else boundaries(self.scope[0])
        self.exclusions = boundaries(self.scope[1])
        self.stats, self.files = OrderedDict(), OrderedDict()
        self.max_entries = max_entries

    def remember(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        if len(cache) > self.max_entries:
            cache.popitem(last=False)

    def accept(self, row, *, content=None):
        path = row['path']
        key = catalog.search_key(catalog.canonical(path))
        if ((self.roots is not None and not any(key == root or key.startswith(prefix) for root, prefix in self.roots)) or
                any(key == root or key.startswith(prefix) for root, prefix in self.exclusions)):
            return False
        if self.roots is None:
            return True
        if content is None:
            content = (row.get('content_source') == 'live' or
                       row.get('quick_basis') != 'name' and (row.get('unit_id') is not None or 'similarity' in row))
        if content and row.get('status') != 'ready' and row.get('content_source') != 'live':
            return False
        if path not in self.stats:
            try:
                current = os.stat(path)
                self.remember(self.stats, path, current if stat.S_ISREG(current.st_mode) else None)
            except OSError:
                self.remember(self.stats, path, None)
        else:
            self.stats.move_to_end(path)
        current = self.stats[path]
        if current is None:
            return False
        expected = row.get('size'), row.get('mtime')
        if None not in expected and expected != (current.st_size, current.st_mtime_ns):
            if content:
                return False
            row['status'] = 'pending'
        return True

    def content_file(self, fid):
        if fid not in self.files:
            row = self.db.execute('SELECT path,size,mtime,status FROM files WHERE id=?', (fid,)).fetchone()
            self.remember(self.files, fid, bool(row and self.accept(dict(row), content=True)))
        else:
            self.files.move_to_end(fid)
        return self.files[fid]


def live_results(db, rows):
    """Validate bounded candidates against current paths without changing the index."""
    live = _LiveFiles(db)
    return [row for row in rows if live.accept(row)]


def _result_page(db, query, args, limit, offset=0, *, scope=None):
    live = _LiveFiles(db, scope)
    if live.scope[0] is None:
        return [result(row) for row in db.execute(query+' LIMIT ? OFFSET ?', (*args, limit, offset))]
    page_size, position, skipped, found = max(64, min(512, limit+offset)), 0, 0, []
    while len(found) < limit:
        rows = db.execute(query+' LIMIT ? OFFSET ?', (*args, page_size, position)).fetchall()
        for row in rows:
            item = result(row)
            if live.accept(item):
                if skipped < offset:
                    skipped += 1
                else:
                    found.append(item)
                    if len(found) == limit:
                        break
        if len(rows) < page_size:
            break
        position += len(rows)
    return found


def index_names_batch(db, limit=512):
    """Build substring postings after names become visible; never hide pending names."""
    from . import storage
    ids = [r[0] for r in db.execute('SELECT id FROM files WHERE name_indexed=0 LIMIT ?', (limit,))]
    if not ids:
        return 0
    from collections import Counter
    frequencies = Counter()
    with storage.write(db, max(65536, len(ids)*8192), catalog=True):
        for fid in ids:
            row = db.execute('SELECT name FROM files WHERE id=? AND name_indexed=0', (fid,)).fetchone()
            if row:
                db.execute('DELETE FROM name_fts WHERE rowid=?', (fid,))
                grams = catalog.grams(row['name'])
                db.execute('INSERT INTO name_fts(rowid,tokens) VALUES(?,?)', (fid, grams))
                frequencies.update(grams.split())
                db.execute('UPDATE files SET name_indexed=1 WHERE id=?', (fid,))
        db.executemany('''INSERT INTO name_frequencies(term,doc) VALUES(?,?)
            ON CONFLICT(term) DO UPDATE SET doc=doc+excluded.doc''', list(frequencies.items()))
    return len(ids)


def refresh_search_statistics(db):
    """Reconcile planning estimates off the query path. Stale estimates are safe:
    all selected grams are necessary conditions, and literals are always verified.
    """
    from . import storage
    with storage.write(db, 2*1024**2, catalog=True):
        for scope in ('name', 'directory'):
            db.execute(f'DELETE FROM {scope}_frequencies')
            db.execute(f'INSERT INTO {scope}_frequencies SELECT term,doc FROM {scope}_terms')


def reader(path):
    """Search connections never run schema creation or request a writer lock."""
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def tokens(text):
    # Preserve English/code tokens; Chinese characters + adjacent bigrams.
    runs = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.casefold())
    out = []
    for run in runs:
        if '\u3400' <= run[0] <= '\u9fff':
            out.extend(run)
            out.extend(run[i:i+2] for i in range(len(run)-1))
        else:
            out.append(run)
    return out


def setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(db, key, value):
    from . import storage
    with storage.transaction(db):
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))


def coverage_context(db):
    return [setting(db, key) for key in ('scope', 'roots', 'exclusions', 'dimensions', 'active_model')]


def save_coverage(db, stats):
    """One small, advisory snapshot; never used to decide what needs indexing."""
    if stats.get('model_key') != setting(db, 'active_model', ''):
        return
    keys = ('files', 'units', 'vectors', 'covered_units', 'semantic_files', 'content_states',
            'roots', 'name_pending', 'storage', 'dimension')
    set_setting(db, 'coverage_snapshot', {'context': coverage_context(db), 'saved': time.time(),
                'stats': {key: stats[key] for key in keys if key in stats}})


def load_coverage(db):
    saved = setting(db, 'coverage_snapshot', {})
    if saved.get('context') != coverage_context(db):
        return {}
    return dict(saved.get('stats', {}), _cached=True)


def resolve_model(db, default=None):
    """Use a saved valid model, or discover the model shipped beside the app."""
    saved = setting(db, 'model', '')
    if default is None:
        from .paths import frozen, resource_root
        database = db.execute('PRAGMA database_list').fetchone()[2]
        root = resource_root() if frozen() else Path(database).resolve().parent.parent
        default = root/'models/embeddinggemma-2'
    for value in (saved, default):
        if not value:
            continue
        path = Path(value).resolve()
        try:
            config = json.loads((path/'config.json').read_text('utf-8'))
            if config.get('model_type') != 'embedding_gemma2':
                continue
            if not (path/'model.safetensors').is_file() or not (path/'modules.json').is_file():
                continue
        except (OSError, ValueError):
            continue
        if str(path) != saved:
            set_setting(db, 'model', str(path))
        return str(path)
    return ''


def result(row):
    d = dict(row)
    d['location'] = json.loads(d.get('location') or '{}')
    return d


def name_batches(db, query, kind='', limit=100, cancelled=lambda: False):
    with read_snapshot(db):
        yield from _name_batches(db, query, kind, limit, cancelled)


def _name_batches(db, query, kind='', limit=100, cancelled=lambda: False):
    """Exact/prefix hits, indexed substrings, then complete unindexed fallback.

    All lanes verify real names/paths; partial results never mean search finished.
    The B-tree lane also covers newly discovered files before FTS catches up.
    """
    kind = kind or query_intent(query)[0]
    topic = catalog.search_key(query_topic(query))
    scope = catalog.search_scope(db)
    allowed, scope_args = catalog.scope_clause(scope)
    if topic:
        fields = "id AS file_id,path,name,kind,status,error,size,mtime,NULL AS unit_id,'' AS text,'{}' AS location"
        filter_kind, args = (' AND kind=?', [kind]) if kind else ('', [])
        fast = {}
        for key in ('name_key', 'path_key'):
            if cancelled():
                return
            for row in _result_page(db, f'SELECT {fields} FROM files WHERE {key}=?{filter_kind} AND {allowed} ORDER BY id',
                                    (topic, *args, *scope_args), limit, scope=scope):
                fast[row['file_id']] = row
        # Publish exact matches before even the prefix lookup.
        if fast:
            yield list(fast.values())[:limit], False
        if cancelled():
            return
        for row in _result_page(db, f'''SELECT {fields} FROM files
                WHERE name_key>=? AND name_key<?{filter_kind} AND {allowed} ORDER BY name_key,id''',
                                (topic, topic+'\U0010ffff', *args, *scope_args), min(limit, 64), scope=scope):
            fast[row['file_id']] = row
        if fast:
            yield list(fast.values())[:limit], False
    if cancelled():
        return
    indexed = names(db, query, kind, limit, indexed_only=True)
    pending = bool(topic and db.execute(f'SELECT 1 FROM files WHERE name_indexed=0 AND {allowed} LIMIT 1', scope_args).fetchone())
    if not pending:
        yield indexed, True
        return
    # Retain fast hits from files whose substring postings are still pending.
    combined = {r['file_id']: r for r in indexed}
    combined.update(fast)
    yield sorted(combined.values(), key=lambda r: (
        0 if catalog.search_key(r['name']) == topic else 1 if topic in catalog.search_key(r['name']) else 2,
        catalog.search_key(r['name']), r['file_id']))[:limit], False
    if not cancelled():
        yield names(db, query, kind, limit), True


def names(db, query, kind='', limit=100, offset=0, *, indexed_only=False):
    with read_snapshot(db):
        return _names(db, query, kind, limit, offset, indexed_only=indexed_only)


def _names(db, query, kind='', limit=100, offset=0, *, indexed_only=False):
    kind = kind or query_intent(query)[0]
    topic = catalog.search_key(query_topic(query))
    terms = topic.split()
    scope = catalog.search_scope(db)
    allowed, scope_args = catalog.scope_clause(scope)
    clauses, args, source = [allowed], list(scope_args), 'files'
    source_args = []
    if terms:
        candidates = []
        for term in terms[:32]:
            leaf = term.rstrip('\\').rsplit('\\', 1)[-1] or term
            grams = catalog.expression(leaf).replace('"', '').split(' AND ')
            expressions, costs = [], []
            for table in ('name_frequencies', 'directory_frequencies'):
                marks = ','.join('?' for _ in grams)
                counts = dict(db.execute(f'SELECT term,doc FROM {table} WHERE term IN ({marks})', grams))
                def estimate(g):
                    if g in counts:
                        return counts[g]
                    value = bytes.fromhex(g[1:]).decode('utf-8')
                    # Bootstrap before statistics exist: prefer varied digits and
                    # non-ASCII grams to common alphabetic prefixes or separators.
                    return (100 if any(ord(c) > 127 for c in value) else
                            500 if any(c.isdigit() for c in value) and len(set(value)) > 1 else 10000)
                chosen = sorted(grams, key=estimate)[:4]
                expressions.append(' AND '.join('"'+g+'"' for g in chosen))
                costs.append(min(map(estimate, grams), default=0))
            candidates.append((costs[0]+100*costs[1], expressions))
        # Seed with the rarest literal term, then verify ALL terms on that set.
        # This avoids materializing huge postings for common terms like "project".
        _, expressions = min(candidates, key=lambda item: item[0])
        fallback = '' if indexed_only else 'SELECT id AS candidate_id FROM files WHERE name_indexed=0 UNION '
        # Force the rare-posting set to drive rowid lookup. Otherwise a type
        # filter can make SQLite walk every document in name order first.
        source = f'''({fallback}SELECT rowid AS candidate_id FROM name_fts WHERE name_fts MATCH ?
            UNION SELECT f.id AS candidate_id FROM files f WHERE f.parent_id IN
              (SELECT rowid FROM directory_fts WHERE directory_fts MATCH ?)) AS candidates
            CROSS JOIN files ON files.id=candidates.candidate_id'''
        source_args.extend(expressions)
    for term in terms:
        clauses.append('instr(path_key,?)>0')
        args.append(term)
    if kind:
        clauses.append('kind=?')
        args.append(kind)
    order = 'CASE WHEN name_key=? THEN 0 WHEN instr(name_key,?)>0 THEN 1 ELSE 2 END,name_key,id' if terms else 'name_key,id'
    order_args = (topic, topic) if terms else ()
    return _result_page(db, f"""SELECT id AS file_id,path,name,kind,status,error,size,mtime,
        NULL AS unit_id,'' AS text,'{{}}' AS location
        FROM {source} WHERE {' AND '.join(clauses)}
        ORDER BY {order}""", (*source_args, *args, *order_args), limit, offset, scope=scope)


def lexical_terms(text):
    terms = []
    for run in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', text.casefold()):
        if '\u3400' <= run[0] <= '\u9fff' and len(run) > 1:
            terms.extend(run[i:i+2] for i in range(0, len(run)-1, 2))
            if len(run) % 2:
                terms.append(run[-2:])
        else:
            terms.append(run)
    return list(dict.fromkeys(terms))


def keywords(db, query, kind='', limit=300, *, cancelled=lambda: False):
    with read_snapshot(db):
        found = _keywords(db, query, kind, limit)
        if len(found) < limit and not cancelled():
            from .on_demand import search
            seen = {row['file_id'] for row in found}
            found.extend(row for row in search(db, query, kind, limit-len(found), cancelled, exclude=seen)
                         if row['file_id'] not in seen)
        return found


def _keywords(db, query, kind='', limit=300):
    kind = kind or query_intent(query)[0]
    description = query_evidence.clean(query_topic(query))
    ts = lexical_terms(description)
    if not ts:
        return []
    # Partial single-character OR matches are very noisy before the model loads.
    # Require the query's lexical concepts; semantic search supplies paraphrases.
    expression = ' AND '.join('"' + t + '"' for t in ts[:40])
    scope = catalog.search_scope(db)
    allowed, scope_args = catalog.scope_clause(scope, 'f.path_key')
    ready = " AND f.status='ready'" if scope[0] is not None else ''
    rows = db.execute(f"""SELECT f.id AS file_id,f.path,f.name,f.kind,f.status,f.error,f.size,f.mtime,
        u.id AS unit_id,u.text,u.location,content_fts.rank AS rank
        FROM content_fts JOIN units u ON u.id=content_fts.rowid
        JOIN files f ON f.id=u.file_id
        WHERE content_fts MATCH ? AND (?='' OR f.kind=?) AND {allowed}{ready}
        ORDER BY content_fts.rank""", (expression, kind, kind, *scope_args))
    # LIMIT applies to files, not chunks: a long book must not fill the budget.
    found, seen, live = [], set(), _LiveFiles(db, scope)
    for row in rows:
        if len(found) >= limit:
            break
        if row['file_id'] not in seen:
            item = result(row)
            if live.accept(item):
                item['literal_match'] = query_evidence.evidence(description, item['text'])
                seen.add(row['file_id'])
                found.append(item)
    return found


class VectorCache:
    """Cache numeric blocks only; fetch live metadata after ranking.

    A transactional source revision detects both this writer and other writers.
    The cache is owned by one search connection and bounded at 64 MiB.
    """
    def __init__(self, max_bytes=64*1024*1024):
        self.cache = ByteCache(max_bytes, max_items=8)
        self.revision = None

    def blocks(self, db, model, dim, kind):
        import numpy as np
        revision = (db, search_revision(db))
        if revision != self.revision:
            self.cache.clear()
            self.revision = revision
        scope = catalog.search_scope(db)
        allowed, scope_args = catalog.scope_clause(scope, 'f.path_key')
        ready = " AND f.status='ready'" if scope[0] is not None else ''
        key = (model, dim, kind, scope)
        cached = self.cache.get(key)
        if cached is not None:
            yield from cached
            return
        # A scope/type predicate must not make SQLite start at millions of
        # filename-only rows. Only encoded vectors can enter this channel.
        cursor = db.execute(f"""SELECT u.id,u.file_id,v.data,v.dim FROM vectors v INDEXED BY vectors_model
            CROSS JOIN units u ON u.digest=v.digest CROSS JOIN files f ON f.id=u.file_id
            WHERE v.model=? AND (?='' OR f.kind=?) AND {allowed}{ready}""", (model, kind, kind, *scope_args))
        saved, size, batch_size = [], 0, 256
        # Small first read for first paint, then grow to throughput-sized blocks.
        while rows := cursor.fetchmany(batch_size):
            from itertools import groupby
            from .embeddings import truncate
            for stored_dim, group in groupby(rows, key=lambda r: r[3]):
                group = list(group)
                ids = np.array([(r[0], r[1]) for r in group], dtype=np.int64)
                packed = np.frombuffer(b''.join(r[2] for r in group), dtype='<f4').reshape(len(group), stored_dim)
                matrix = truncate(packed, min(dim, stored_dim))
                block = (ids, matrix)
                size += ids.nbytes + matrix.nbytes
                if saved is not None:
                    if size <= self.cache.max_bytes:
                        saved.append(block)
                    else:
                        saved = None
                yield block
            batch_size = min(4096, batch_size*2)
        if saved is not None:
            self.cache.put(key, saved, size)


def semantic(db, vector, model, kind='', limit=300, cache=None, cancelled=lambda: False, progress=None):
    with read_snapshot(db):
        return _semantic(db, vector, model, kind, limit, cache, cancelled, progress)


def _semantic(db, vector, model, kind='', limit=300, cache=None, cancelled=lambda: False, progress=None):
    import numpy as np
    if limit <= 0:
        return []
    q = np.array(vector, dtype=np.float32, copy=True)
    q /= max(float(np.linalg.norm(q)), 1e-12)
    blocks = (cache or VectorCache(max_bytes=0)).blocks(db, model, len(q), kind)
    best, heap, live = {}, [], _LiveFiles(db, max_entries=max(64, 2*limit))
    last_progress = 0.
    def snapshot(count):
        chosen = heapq.nlargest(count, best, key=best.get)
        metadata = {}
        for offset in range(0, len(chosen), 400):
            uids = [best[fid][1] for fid in chosen[offset:offset+400]]
            marks = ','.join('?' for _ in uids)
            for row in db.execute(f"""SELECT f.id AS file_id,f.path,f.name,f.kind,f.status,f.error,f.size,f.mtime,
                u.id AS unit_id,u.text,u.location FROM units u JOIN files f ON f.id=u.file_id
                WHERE u.id IN ({marks})""", uids):
                metadata[row['file_id']] = result(row)
        found = [dict(metadata[fid], similarity=best[fid][0]) for fid in chosen if fid in metadata]
        return found, metadata
    def offer(score, uid, fid):
        candidate = (score, uid)
        if fid in best:
            if candidate <= best[fid]:
                return
        elif len(best) >= limit:
            while heap and best.get(heap[0][2]) != heap[0][:2]:
                heapq.heappop(heap)
            if candidate <= heap[0][:2]:
                return
        # Validate prospective winners, not every vector in the database.
        # Invalid cached/source entries never consume the result budget.
        if live.scope[0] is not None and not live.content_file(fid):
            return
        if fid not in best and len(best) >= limit:
            _, _, removed = heapq.heappop(heap)
            del best[removed]
        best[fid] = candidate
        heapq.heappush(heap, (score, uid, fid))
        if len(heap) > max(64, 2*limit):
            heap[:] = [(s, u, f) for f, (s, u) in best.items()]
            heapq.heapify(heap)
    for ids, matrix in blocks:
        if cancelled():
            return []
        short = q[:matrix.shape[1]]
        short = short/max(float(np.linalg.norm(short)), 1e-12)
        scores = matrix @ short
        # The current file Top-K threshold can only increase. Reject entire
        # low-scoring vector lanes in NumPy before creating Python objects.
        if len(best) >= limit:
            keep = scores >= heap[0][0]
            scores, ids = scores[keep], ids[keep]
        for score, (uid, fid) in zip(scores.tolist(), ids.tolist()):
            offer(score, uid, fid)
        now = time.monotonic()
        if progress is not None and best and now-last_progress >= .15 and not cancelled():
            progress(snapshot(min(limit, 100))[0])
            last_progress = now
    # Exact file Top-K; accumulator memory is O(K), not O(all files).
    if cancelled():
        return []
    found, metadata = snapshot(limit)
    # Only selected videos need additional local peaks. All windows remain eligible.
    videos = [r['file_id'] for r in found if r['kind'] in ('video', 'audio')]
    found = [r for r in found if r['kind'] not in ('video', 'audio')]
    for fid in videos:
        candidates = []
        cursor = db.execute("""SELECT u.id,u.location,v.data,v.dim FROM units u JOIN vectors v ON v.digest=u.digest
            WHERE u.file_id=? AND v.model=?""", (fid, model))
        for row in cursor:
            from .embeddings import truncate
            if cancelled():
                return []
            dim = min(len(q), row['dim'])
            score = float(truncate(np.frombuffer(row['data'], dtype='<f4'), dim) @ truncate(q, dim))
            if score >= best[fid][0]-.040001:
                candidates.append((score, row['id'], row['location']))
        for score, uid, location in sorted(candidates, reverse=True):
            d = dict(metadata[fid], unit_id=uid, location=json.loads(location), similarity=score)
            found.append(d)
    found.sort(key=lambda r: (r['similarity'], r['unit_id']), reverse=True)
    # Avoid turning every window of a short video into one full-length "hit".
    # Keep at most three strong, distinct temporal peaks per file.
    filtered, video_peaks = [], {}
    for hit in found:
        if hit['kind'] not in ('video', 'audio'):
            filtered.append(hit)
            continue
        peaks = video_peaks.setdefault(hit['file_id'], [])
        loc = hit['location']
        if len(peaks) >= 3 or (peaks and hit['similarity'] < peaks[0]['similarity']-.04):
            continue
        def overlap(other):
            area = max(0, min(loc['end'], other['end'])-max(loc['start'], other['start']))
            union = max(loc['end'], other['end'])-min(loc['start'], other['start'])
            return area/max(union, 1e-6)
        if any(overlap(p['location']) > .3 for p in peaks):
            continue
        peaks.append(hit)
        filtered.append(hit)
    return filtered


def prefers_pdf(query):
    return bool(re.search(r'论文|\bpapers?\b|\bpdf\b', query, re.I))


def query_intent(query):
    """Only explicit requested media types constrain a natural-language query."""
    value = query.strip()
    if re.search(r'\.[a-z0-9]{1,10}$', value, re.I):
        return '', value
    labels = {'音频': 'audio', '录音': 'audio', 'audio': 'audio', 'recordings': 'audio',
              '论文': 'document', '文档': 'document', '视频': 'video', '图片': 'image',
              '照片': 'image', '代码': 'code', 'pdf': 'document', 'paper': 'document',
              'papers': 'document', 'videos': 'video', 'video': 'video', 'images': 'image', 'image': 'image'}
    match = re.fullmatch(r'(.*?)(音频|录音|audio|recordings|论文|文档|视频|图片|照片|代码|pdf|papers?|videos?|images?)', value, re.I)
    if match:
        topic, label = match.groups()
        if label.isascii() and topic and topic[-1].isascii() and (topic[-1].isalnum() or topic[-1] in '_-'):
            return '', value
        topic = re.sub(r'^(?:请)?(?:帮我)?(?:搜索|查找|寻找|找一下|找|关于|有关)\s*', '', topic)
        topic = re.sub(r'的\s*$', '', topic).strip()
        return labels[label.lower()], topic
    match = re.fullmatch(r'(audio|recordings|papers?|videos?|images?)\s+(?:about|of|on)\s+(.+)', value, re.I)
    if match:
        return labels[match[1].lower()], match[2].strip()
    return '', value


def query_topic(query):
    return query_intent(query)[1]


def fuse(*lists, query=''):
    # Reciprocal-rank fusion: filename and cosine scores are not comparable.
    scores, entries, video_hits = {}, {}, {}
    exact_query = query.strip().casefold()
    requested, _ = query_intent(query)
    semantic_ranks = {}
    literal_strength = {}
    full_semantics = False
    for source, rows in enumerate(lists):
        seen = set()
        for row in rows:
            key = row['file_id']
            if 'start' in row['location']:
                video_hits.setdefault(key, []).append(row['location'])
            if key in seen:
                continue
            seen.add(key)
            if source == 2 and 'similarity' in row:
                semantic_ranks[key] = len(seen)
                full_semantics = full_semantics or not row.get('quick_basis')
            if source == 1:
                literal_strength[key] = query_evidence.strength(row)
            weight = 1.15 if source == 2 else 1.
            scores[key] = scores.get(key, 0) + weight / (60 + len(seen))
            if (key not in entries or (not entries[key].get('unit_id') and
                                      (row.get('unit_id') or row.get('content_source') == 'live'))
                    or (source == 2 and row.get('unit_id') and row['name'].casefold() != exact_query)):
                sources = entries.get(key, {}).get('sources', [])
                entries[key] = dict(row)
                entries[key]['sources'] = list(sources)
            entries[key].setdefault('sources', []).append(('名称', '内容', '语义')[source])
    for row in entries.values():
        # Keep distinct video hits; coalesce only overlapping time windows.
        hits = video_hits.get(row['file_id'], [])
        merged = []
        for hit in sorted(hits, key=lambda x: x['start']):
            if merged and hit['start'] <= merged[-1]['end']:
                merged[-1]['end'] = max(merged[-1]['end'], hit['end'])
            else:
                merged.append(dict(hit))
        row['hits'] = merged
        row['exact_name'] = bool(exact_query and row['name'].casefold() == exact_query)
        row['requested_type'] = (not requested or row['kind'] == requested)
        if requested == 'document' and prefers_pdf(query):
            row['requested_type'] = Path(row['path']).suffix.lower() == '.pdf'
    # Semantic relevance orders descriptive requests. Lexical evidence remains
    # the fallback while the model is loading; it cannot outvote relevance by
    # matching the same words in several channels. Exact names always win.
    return [entries[k] for k in sorted(scores, key=lambda k:
        (not entries[k]['exact_name'], not entries[k]['requested_type'],
         -literal_strength.get(k, 0) if not full_semantics else 0,
         semantic_ranks.get(k, float('inf')) if semantic_ranks else 0,
         -scores[k], entries[k]['name'], k))]


def stats(db):
    from .storage import usage
    scope = catalog.search_scope(db)
    ready = " AND f.status='ready'" if scope[0] is not None else ''
    # Inventory publishes these aggregates after applying the current scope.
    # Adding path predicates here turns covering-index counts into millions of
    # random table reads on large catalogs; search applies scope independently.
    counts = {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM files GROUP BY status')}
    active = setting(db, 'active_model', '')
    if not active:
        existing = db.execute('SELECT model FROM vectors LIMIT 1').fetchone()
        active = existing[0] if existing else ''
    coverage = db.execute(f'''SELECT count(*),count(DISTINCT u.file_id) FROM units u
        CROSS JOIN files f ON f.id=u.file_id WHERE 1{ready} AND EXISTS
        (SELECT 1 FROM vectors v WHERE v.digest=u.digest AND v.model=?)''', (active,)).fetchone() if active else (0, 0)
    roots = []
    for (root,) in db.execute('SELECT DISTINCT root FROM files'):
        roots.append({'path': root,
            'files': db.execute('SELECT count(*) FROM files WHERE root=?', (root,)).fetchone()[0],
            'ready': db.execute("SELECT count(*) FROM files WHERE status='ready' AND root=?", (root,)).fetchone()[0],
            'pending': db.execute("SELECT count(*) FROM files WHERE status='pending' AND root=?", (root,)).fetchone()[0]})
    return {'files': sum(counts.values()), 'content_states': counts, 'covered_units': coverage[0], 'model_key': active,
            'partial_files': db.execute('SELECT count(*) FROM extraction_jobs').fetchone()[0],
            'semantic_skipped': db.execute("SELECT count(*) FROM units WHERE json_extract(location,'$.semantic')=0").fetchone()[0],
            'semantic_files': coverage[1], 'roots': roots, 'revision': search_revision(db),
            'name_pending': db.execute('SELECT count(*) FROM files WHERE name_indexed=0').fetchone()[0],
            'storage': usage(db), 'dimension': setting(db, 'dimensions', 256),
            'units': db.execute('SELECT count(*) FROM units').fetchone()[0],
            'vectors': db.execute('SELECT count(*) FROM vectors').fetchone()[0],
            'embedding_errors': [dict(r) for r in db.execute(f'''SELECT f.name,e.error FROM embedding_errors e
                JOIN units u ON u.digest=e.digest JOIN files f ON f.id=u.file_id
                WHERE e.model=?{ready} GROUP BY f.id,e.error LIMIT 100''', (active,))],
            'errors': [dict(r) for r in db.execute("SELECT path,error FROM files WHERE status='error' AND error<>'' LIMIT 100")]}
