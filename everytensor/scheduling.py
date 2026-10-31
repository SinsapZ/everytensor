"""Persistent breadth-first coverage with bounded search preference."""
from __future__ import annotations

from . import frontier, storage


class ContentQueue:
    def __init__(self):
        self.tree = frontier.TreeFrontier()

    def _coverage(self, db, roots):
        turn = int(frontier.state(db, 'kind-turn'))
        for _ in frontier.KINDS:
            kind = frontier.KINDS[turn % len(frontier.KINDS)]
            turn += 1
            chosen = self.tree.fair(db, roots, kind)
            if chosen is not None:
                frontier.save(db, 'kind-turn', turn)
                return chosen
        return None

    def _continuation(self, db, roots):
        if not roots:
            return None
        last = int(frontier.state(db, 'continuation'))
        marks = ','.join('?' for _ in roots)
        row = db.execute(f'''SELECT j.file_id FROM extraction_jobs j JOIN files f ON f.id=j.file_id
            WHERE f.root IN ({marks}) AND f.status='ready'
            ORDER BY j.file_id<=?,j.file_id LIMIT 1''', (*roots, last)).fetchone()
        if row:
            frontier.save(db, 'continuation', row[0])
            return row[0]

    def next(self, db, roots, *, kind='', preferred=(), recent=(), seeds=()):
        self.tree.pump(db)
        self.tree.selected = None
        with storage.transaction(db):
            turn = int(frontier.state(db, 'turn'))+1
            frontier.save(db, 'turn', turn)
            if turn % 4 == 2:
                continuation = self._continuation(db, roots)
                if continuation is not None:
                    return continuation
            # At least one quarter of selections advance global coverage.
            if turn % 4:
                groups = (recent, preferred) if turn % 2 == 0 else (preferred, recent)
                for group in groups:
                    for fid in group[:64]:
                        row = db.execute("SELECT root FROM files WHERE id=? AND status='pending'", (fid,)).fetchone()
                        if row and row[0] in roots:
                            return fid
                chosen = self.tree.neighbors(db, roots, seeds, kind)
                if chosen is not None:
                    return chosen
                if kind in frontier.KINDS:
                    chosen = self.tree.fair(db, roots, kind)
                    if chosen is not None:
                        return chosen
            selected = self._coverage(db, roots)
            return selected if selected is not None else self._continuation(db, roots)

    def completed(self, db, fid, elapsed):
        self.tree.completed(db, fid, elapsed)
