"""Durable directory-level checkpoints for full-volume metadata discovery."""
from __future__ import annotations

from pathlib import Path
import logging
import time
import uuid
from . import storage


def install(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS discovery_runs(
      root TEXT PRIMARY KEY,epoch INTEGER NOT NULL,token TEXT NOT NULL,
      phase TEXT NOT NULL,seen INTEGER NOT NULL DEFAULT 0,
      cleanup_cursor INTEGER NOT NULL DEFAULT 0,completed REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS discovery_directories(
      id INTEGER PRIMARY KEY,root TEXT NOT NULL,path TEXT NOT NULL,
      epoch INTEGER NOT NULL DEFAULT 0,priority INTEGER NOT NULL,depth INTEGER NOT NULL,
      UNIQUE(root,path));
    CREATE INDEX IF NOT EXISTS discovery_frontier
      ON discovery_directories(root,epoch,priority,depth,id);
    ''')


class Session:
    def __init__(self, db, root, priority):
        self.db, self.root = db, root
        with storage.write(db, 65536, catalog=True):
            row = db.execute('SELECT * FROM discovery_runs WHERE root=?', (root,)).fetchone()
            self.resumed = bool(row and row['phase'] != 'done')
            if self.resumed:
                self.epoch, self.token = row['epoch'], row['token']
                self.seen, self.cursor = row['seen'], row['cleanup_cursor']
                self.phase = row['phase']
            else:
                self.epoch = row['epoch']+1 if row else 1
                self.token, self.seen, self.cursor, self.phase = uuid.uuid4().hex, 0, 0, 'walk'
                db.execute('''INSERT INTO discovery_runs(root,epoch,token,phase) VALUES(?,?,?,'walk')
                    ON CONFLICT(root) DO UPDATE SET epoch=excluded.epoch,token=excluded.token,
                    phase='walk',seen=0,cleanup_cursor=0''', (root, self.epoch, self.token))
                existing = []
                for p in priority:
                    try:
                        if Path(p).is_dir():
                            existing.append((p, 0))
                    except OSError:
                        pass
                self._add(existing+[(root, 1)])
        logging.getLogger(__name__).info('Discovery %s root=%s phase=%s checked=%d',
                                         'resumed' if self.resumed else 'started', root, self.phase, self.seen)

    def _add(self, paths):
        self.db.executemany('''INSERT INTO discovery_directories(root,path,priority,depth)
            VALUES(?,?,?,?) ON CONFLICT(root,path) DO NOTHING''',
            [(self.root, p, priority, len(Path(p).parts)-len(Path(self.root).parts)) for p, priority in paths])

    def add(self, children, priority):
        with storage.write(self.db, max(65536, sum(len(p)*16+1024 for p in children)), catalog=True):
            self._add([(p, priority) for p in children])

    def next(self):
        if self.phase != 'walk':
            return None
        return self.db.execute('''SELECT * FROM discovery_directories WHERE root=? AND epoch<?
            ORDER BY epoch,priority,depth,id LIMIT 1''', (self.root, self.epoch)).fetchone()

    def finish_directory(self, row, seen):
        missing = False
        try:
            Path(row['path']).stat(follow_symlinks=False)
        except FileNotFoundError:
            missing = row['path'] != self.root
        except OSError:
            pass
        with storage.transaction(self.db):
            if missing:
                self.db.execute('DELETE FROM discovery_directories WHERE id=?', (row['id'],))
            else:
                self.db.execute('UPDATE discovery_directories SET epoch=? WHERE id=?', (self.epoch, row['id']))
            self.db.execute('UPDATE discovery_runs SET seen=? WHERE root=?', (seen, self.root))
        self.seen = seen

    def walk_done(self):
        if self.phase == 'walk':
            with storage.transaction(self.db):
                self.db.execute("UPDATE discovery_runs SET phase='cleanup' WHERE root=?", (self.root,))
            self.phase = 'cleanup'

    def checkpoint_cleanup(self, cursor):
        with storage.transaction(self.db):
            self.db.execute('UPDATE discovery_runs SET cleanup_cursor=? WHERE root=?', (cursor, self.root))
        self.cursor = cursor

    def finish(self):
        with storage.transaction(self.db):
            self.db.execute("UPDATE discovery_runs SET phase='done',completed=? WHERE root=?", (time.time(), self.root))
        self.phase = 'done'
