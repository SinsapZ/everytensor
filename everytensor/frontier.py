"""Persistent, bounded directory frontier. Payloads never live in this queue."""
from __future__ import annotations

import math
import os
from collections import OrderedDict
from pathlib import Path
from . import catalog, storage

KINDS = ('document', 'image', 'video', 'code', 'audio')
WORK_LIMIT = 64


def install(db):
    # No catalog-sized migration on startup. pump() backfills by primary key.
    db.executescript('''
    BEGIN IMMEDIATE;
    CREATE TABLE IF NOT EXISTS exploration_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS exploration_nodes(
      id INTEGER PRIMARY KEY,root TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,
      parent INTEGER REFERENCES exploration_nodes(id),depth INTEGER NOT NULL,
      directory INTEGER,active INTEGER NOT NULL DEFAULT 0,
      round INTEGER NOT NULL DEFAULT 0,quota INTEGER NOT NULL DEFAULT 1,
      served INTEGER NOT NULL DEFAULT 0,credit REAL NOT NULL DEFAULT 4,
      cursor INTEGER NOT NULL DEFAULT 0,completed INTEGER NOT NULL DEFAULT 0,
      UNIQUE(root,kind,path));
    CREATE INDEX IF NOT EXISTS exploration_ready
      ON exploration_nodes(root,kind,active,round,depth,id);
    CREATE INDEX IF NOT EXISTS exploration_children ON exploration_nodes(parent,id);
    CREATE INDEX IF NOT EXISTS exploration_directory ON exploration_nodes(root,kind,directory);
    CREATE INDEX IF NOT EXISTS exploration_paths ON exploration_nodes(root,path,kind) WHERE active=1;
    CREATE TABLE IF NOT EXISTS exploration_dirty(
      root TEXT NOT NULL,directory INTEGER NOT NULL,kind TEXT NOT NULL,
      PRIMARY KEY(root,directory,kind));
    CREATE TABLE IF NOT EXISTS exploration_work(
      file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE);
    DROP TRIGGER IF EXISTS exploration_insert;
    DROP TRIGGER IF EXISTS exploration_update;
    DROP TRIGGER IF EXISTS exploration_delete;
    CREATE TRIGGER exploration_insert AFTER INSERT ON files
    WHEN new.status='pending' AND new.parent_id IS NOT NULL BEGIN
      INSERT INTO exploration_dirty VALUES(new.root,new.parent_id,new.kind) ON CONFLICT DO NOTHING;
    END;
    CREATE TRIGGER exploration_update
    AFTER UPDATE OF status,parent_id,root,kind ON files
    WHEN old.status IS NOT new.status OR old.parent_id IS NOT new.parent_id
      OR old.root IS NOT new.root OR old.kind IS NOT new.kind BEGIN
      INSERT INTO exploration_dirty
        SELECT old.root,old.parent_id,old.kind WHERE old.status='pending' AND old.parent_id IS NOT NULL ON CONFLICT DO NOTHING;
      INSERT INTO exploration_dirty
        SELECT new.root,new.parent_id,new.kind WHERE new.status='pending' AND new.parent_id IS NOT NULL ON CONFLICT DO NOTHING;
      DELETE FROM exploration_work WHERE file_id=new.id AND new.status<>'ready';
      INSERT INTO exploration_work
        SELECT new.id WHERE new.status='ready' AND old.status='pending'
          AND (SELECT count(*) FROM exploration_work)<64 ON CONFLICT DO NOTHING;
    END;
    CREATE TRIGGER exploration_delete AFTER DELETE ON files
    WHEN old.status='pending' AND old.parent_id IS NOT NULL BEGIN
      INSERT INTO exploration_dirty VALUES(old.root,old.parent_id,old.kind) ON CONFLICT DO NOTHING;
    END;
    COMMIT;
    ''')


def state(db, key, default='0'):
    row = db.execute('SELECT value FROM exploration_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def save(db, key, value):
    db.execute('INSERT INTO exploration_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, str(value)))


def cost(row):
    # Bounded estimate, subsequently corrected using extraction wall time.
    size = max(0, row['size'] or 0)
    return min(16, (4 if row['kind'] == 'video' else 1) + math.log2(1+size/(4*1024**2)))


class TreeFrontier:
    def __init__(self):
        self.selected = None
        self.hot_cursors = OrderedDict()
        self.hot_turn = 0

    def _register(self, db, root, directory, kind):
        if kind not in KINDS:
            return
        folder = db.execute('SELECT path FROM directories WHERE id=?', (directory,)).fetchone()
        existing = db.execute('SELECT id FROM exploration_nodes WHERE root=? AND kind=? AND directory=?',
                              (root, kind, directory)).fetchone()
        pending = db.execute("SELECT 1 FROM files WHERE root=? AND parent_id=? AND kind=? AND status='pending' LIMIT 1",
                             (root, directory, kind)).fetchone()
        if not folder or not pending:
            if existing:
                db.execute('UPDATE exploration_nodes SET active=0 WHERE id=?', (existing[0],))
            return
        if not catalog.within(folder[0], root):
            return
        # Materialize only the ancestor chain of this leaf, never all children.
        path, base = Path(folder[0]), Path(root)
        chain = [path]
        while os.path.normcase(str(path)) != os.path.normcase(str(base)):
            if path == path.parent:
                return
            path = path.parent
            chain.append(path)
        parent = None
        epoch = int(state(db, 'epoch:'+root+':'+kind))
        for depth, path in enumerate(reversed(chain)):
            db.execute('''INSERT OR IGNORE INTO exploration_nodes(root,path,kind,parent,depth,round)
                          VALUES(?,?,?,?,?,?)''', (root, str(path), kind, parent, depth, epoch))
            parent = db.execute('SELECT id FROM exploration_nodes WHERE root=? AND kind=? AND path=?',
                                (root, kind, str(path))).fetchone()[0]
        db.execute('''UPDATE exploration_nodes SET directory=?,active=1,
                      round=max(round,?) WHERE id=?''', (directory, epoch, parent))

    def pump(self, db, rows=512, directories=32):
        """A bounded migration slice and dirty leaves; safe to repeat after a crash."""
        with storage.write(db, 256*1024):
            last = int(state(db, 'backfill'))
            page = db.execute('SELECT id,root,parent_id,kind,status FROM files WHERE id>? ORDER BY id LIMIT ?',
                              (last, rows)).fetchall()
            db.executemany('INSERT OR IGNORE INTO exploration_dirty VALUES(?,?,?)',
                           {(r['root'], r['parent_id'], r['kind']) for r in page
                            if r['status'] == 'pending' and r['parent_id'] is not None})
            if page:
                save(db, 'backfill', page[-1]['id'])
            dirty = db.execute('SELECT root,directory,kind FROM exploration_dirty ORDER BY rowid LIMIT ?',
                               (directories,)).fetchall()
            for root, directory, kind in dirty:
                self._register(db, root, directory, kind)
                db.execute('DELETE FROM exploration_dirty WHERE root=? AND directory=? AND kind=?',
                           (root, directory, kind))
            # Prune abandoned leaves bottom-up, using bounded keyset pages.
            gc_page = db.execute('SELECT id FROM exploration_nodes WHERE id>? ORDER BY id LIMIT 32',
                                 (int(state(db, 'gc')),)).fetchall()
            db.executemany('''DELETE FROM exploration_nodes WHERE id=?
                AND NOT EXISTS(SELECT 1 FROM exploration_nodes child WHERE child.parent=exploration_nodes.id)
                AND NOT EXISTS(SELECT 1 FROM files WHERE root=exploration_nodes.root
                    AND parent_id=exploration_nodes.directory AND kind=exploration_nodes.kind)''', gc_page)
            save(db, 'gc', gc_page[-1][0] if len(gc_page) == 32 else 0)

    def take(self, db, node):
        query = "SELECT id,size,kind,path FROM files WHERE root=? AND parent_id=? AND kind=? AND status='pending'"
        args = (node['root'], node['directory'], node['kind'])
        candidates = db.execute(query+' AND id>? ORDER BY id LIMIT 8', (*args, node['cursor'])).fetchall()
        if not candidates:
            candidates = db.execute(query+' ORDER BY id LIMIT 8', args).fetchall()
        if not candidates:
            db.execute('UPDATE exploration_nodes SET active=0 WHERE id=?', (node['id'],))
            return None
        # Sample inexpensive files across types/subdirectories. Cursor wrap keeps
        # every skipped large file eligible in later rounds.
        from .work_costs import estimate as learned_cost
        row = min(candidates, key=lambda r: learned_cost(db, r, cost(r)))
        estimate = learned_cost(db, row, cost(row))
        if node['credit'] < estimate:
            db.execute('UPDATE exploration_nodes SET round=round+1,credit=min(32,credit+4) WHERE id=?', (node['id'],))
            return None
        finished = node['served']+1 >= node['quota']
        db.execute('''UPDATE exploration_nodes SET cursor=?,served=?,round=round+?,
                      quota=?,credit=min(32,credit-?+?) WHERE id=?''',
                   (row['id'], 0 if finished else node['served']+1, int(finished),
                    min(16, node['quota']*2) if finished else node['quota'], estimate,
                    4 if finished else 0, node['id']))
        self.selected = (row['id'], node['id'], estimate)
        return row['id']

    def fair(self, db, roots, kind):
        if not roots:
            return None
        key = 'root-turn:'+kind
        start = int(state(db, key)) % len(roots)
        for step in range(len(roots)):
            position = (start+step) % len(roots)
            root = roots[position]
            for _ in range(32):
                node = db.execute('''SELECT * FROM exploration_nodes
                    WHERE root=? AND kind=? AND active=1 ORDER BY round,depth,id LIMIT 1''', (root, kind)).fetchone()
                if node is None:
                    break
                save(db, 'epoch:'+root+':'+kind, node['round'])
                fid = self.take(db, node)
                if fid is not None:
                    save(db, key, position+1)
                    return fid
        return None

    def neighbors(self, db, roots, seeds, kind):
        # Include descendants with literal path boundaries, not SQL wildcards.
        seeds = tuple(seeds[:8])
        for _ in seeds:
            fid = seeds[self.hot_turn % len(seeds)]
            self.hot_turn += 1
            seed = db.execute('SELECT root,path FROM files WHERE id=?', (fid,)).fetchone()
            if not seed or seed['root'] not in roots:
                continue
            folder = str(Path(seed['path']).parent)
            prefix = folder.rstrip('\\/')+os.sep
            clause, extra = (' AND kind=?', (kind,)) if kind else ('', ())
            # Scan a small path-ordered window, then choose the least visited
            # node inside it. A root-level hit must not sort an entire drive.
            key = seed['root'], folder, kind
            after = self.hot_cursors.get(key, '')
            nodes = db.execute('''SELECT * FROM exploration_nodes INDEXED BY exploration_paths
                WHERE root=? AND active=1 AND path>=? AND path<? AND path>?'''+clause+
                ' ORDER BY path,kind LIMIT 32', (seed['root'], prefix, prefix+'\U0010ffff', after, *extra)).fetchall()
            if not nodes and after:
                nodes = db.execute('''SELECT * FROM exploration_nodes INDEXED BY exploration_paths
                    WHERE root=? AND active=1 AND path>=? AND path<?'''+clause+
                    ' ORDER BY path,kind LIMIT 32', (seed['root'], prefix, prefix+'\U0010ffff', *extra)).fetchall()
            self.hot_cursors[key] = nodes[-1]['path'] if nodes else ''
            self.hot_cursors.move_to_end(key)
            while len(self.hot_cursors) > 8:
                self.hot_cursors.popitem(last=False)
            nodes += db.execute('''SELECT * FROM exploration_nodes WHERE root=? AND active=1 AND path=?'''
                                +clause, (seed['root'], folder, *extra)).fetchall()
            for node in sorted(nodes, key=lambda n: (n['round'], n['depth'], n['id'])):
                chosen = self.take(db, node)
                if chosen is not None:
                    return chosen
        return None

    def completed(self, db, fid, elapsed):
        if self.selected and self.selected[0] == fid:
            _, node, estimate = self.selected
            with storage.transaction(db):
                db.execute('''UPDATE exploration_nodes SET completed=completed+1,
                    credit=max(-16,credit-?) WHERE id=?''', (max(0, min(16, elapsed/0.25)-estimate), node))
        self.selected = None


def settle_work(db, model, dim):
    """Release a producer slot after the first fragment, not a whole long video."""
    if not db.execute('SELECT 1 FROM exploration_work LIMIT 1').fetchone():
        return
    # Check and delete under one writer reservation: a concurrent file update
    # must not have its newly queued generation removed based on an old digest.
    with storage.transaction(db):
        done = []
        for item in db.execute('SELECT file_id FROM exploration_work LIMIT 64').fetchall():
            row = db.execute('''SELECT u.digest FROM units u JOIN files f ON f.id=u.file_id
                                WHERE u.file_id=? AND f.status='ready'
                                  AND coalesce(json_extract(u.location,'$.semantic'),1)<>0
                                ORDER BY u.ordinal LIMIT 1''', item).fetchone()
            if row is None or db.execute('''SELECT 1 FROM vectors WHERE digest=? AND model=? AND dim>=?
                  UNION ALL SELECT 1 FROM embedding_errors WHERE digest=? AND model=? LIMIT 1''',
                  (row[0], model, dim, row[0], model)).fetchone():
                done.append(tuple(item))
        if done:
            db.executemany('DELETE FROM exploration_work WHERE file_id=?', done)
