"""Bounded, decaying per-kind cost observations, stored with the local index."""
import math
from pathlib import Path


def observe(db, phase, kind, units, seconds):
    if not units or seconds <= 0 or not math.isfinite(seconds):
        return
    db.execute('''INSERT INTO work_costs VALUES(?,?,?,?,1) ON CONFLICT(phase,kind)
        DO UPDATE SET units=work_costs.units*0.9+excluded.units,
        seconds=work_costs.seconds*0.9+excluded.seconds, samples=work_costs.samples+1''',
               (phase, kind, units, seconds))


def estimate(db, row, fallback):
    key = Path(row['path']).suffix.lower() if 'path' in row.keys() else row['kind']
    learned = db.execute("SELECT units,seconds,samples FROM work_costs WHERE phase='extract' AND kind=?", (key,)).fetchone()
    seconds = fallback*.25
    if learned is not None and learned['samples'] >= 3 and learned['units'] > 0:
        seconds = learned['seconds']/learned['units'] * max(1, row['size'] or 0)
    from .store import setting
    model = setting(db, 'active_model', '')
    encoding = db.execute('SELECT units,seconds,samples FROM work_costs WHERE phase=? AND kind=?',
                          ('encode:'+model, row['kind'])).fetchone()
    if encoding and encoding['samples'] >= 3 and encoding['units'] > 0:
        seconds += encoding['seconds']/encoding['units']
    # Keep old fairness credit bounds; a slow task remains schedulable.
    return max(.25, min(16., seconds/.25))
