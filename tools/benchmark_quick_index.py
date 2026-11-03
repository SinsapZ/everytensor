"""Reproducible exact-search benchmark after small index updates.

Uses deterministic synthetic vectors by default. --source reads vector BLOBs
from a local SQLite database in read-only mode; paths/text never leave it.
The benchmark mutates only a temporary catalog. Reports contain timings/counts,
not vectors or user content. Reference is the authoritative SQLite search path.

Research: https://arxiv.org/abs/2505.17152v3 (24 Oct 2026),
https://arxiv.org/abs/2105.09613. Stable-base/delta design principles inform
this implementation; this is not a reproduction of their ANN algorithms.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from everytensor import quick_cache, quick_index, store


def run(count=100000, repeats=5, source=None):
    rng = np.random.default_rng(20261102)
    dim, model = 256, 'benchmark'
    source_db = None
    try:
        if source:
            source_db = sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)
            row = source_db.execute("SELECT model,length(data)/2 FROM quick_vectors LIMIT 1").fetchone()
            if row is None:
                raise ValueError('Source has no quick vectors')
            source_model, dim = row
            cursor = source_db.execute('SELECT data FROM quick_vectors WHERE model=? LIMIT ?',
                                       (source_model, count))
        with tempfile.TemporaryDirectory(prefix='everytensor-index-benchmark-') as temporary:
            path = Path(temporary)/'index.sqlite3'
            db = store.connect(path)
            try:
                quick_index.install(db)
                db.create_function('index_names_now', 0, lambda: 0)
                samples, inserted = [], 0
                while inserted < count:
                    size = min(2048, count-inserted)
                    if source:
                        blobs = [r[0] for r in cursor.fetchmany(size)]
                    else:
                        matrix = rng.normal(size=(size, dim)).astype('f4')
                        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
                        blobs = [v.astype('<f2').tobytes() for v in matrix]
                    if not blobs:
                        break
                    samples.append(blobs[len(blobs)//2])
                    with db:
                        quick_index.save(db, 'model', model)
                        for offset, blob in enumerate(blobs, inserted+1):
                            fake = str(Path(temporary)/'documents'/f'{offset}.txt')
                            db.execute('''INSERT INTO files(id,path,root,name,kind,status)
                                VALUES(?,?,? ,?,'document','ready')''',
                                (offset, fake, temporary, f'{offset}.txt'))
                            db.execute('INSERT INTO quick_vectors VALUES(?,?,?,?,?)',
                                       ('name', offset, fake, model, blob))
                    inserted += len(blobs)
                if inserted < 2:
                    raise ValueError('Need at least two vectors')
                started = time.perf_counter()
                if not quick_cache.build(path, model, dim):
                    raise RuntimeError('Could not build benchmark dense cache')
                build_seconds = time.perf_counter()-started
                changed = min(256, inserted)
                with db:
                    for fid in range(1, changed+1):
                        db.execute("UPDATE quick_vectors SET data=? WHERE kind='name' AND source_id=?",
                                   (samples[fid % len(samples)], fid))
                timings = {'sqlite': [], 'base_delta': []}
                # Alternate order, warm both paths, retain all individual timings.
                for trial in range(repeats+1):
                    q = np.frombuffer(samples[trial % len(samples)], '<f2').astype('f4')
                    outputs = {}
                    for method in (('sqlite', 'base_delta') if trial % 2 else ('base_delta', 'sqlite')):
                        context = patch.object(quick_cache, 'blocks', return_value=None) if method == 'sqlite' else nullcontext()
                        with context:
                            search = quick_index.QuickSearch()
                            started = time.perf_counter()
                            rows = search.search(db, q, model, limit=20)
                            elapsed = time.perf_counter()-started
                        outputs[method] = rows
                        if trial:
                            timings[method].append(elapsed)
                    signature = lambda rows: [(r['file_id'], r['unit_id']) for r in rows]
                    assert signature(outputs['sqlite']) == signature(outputs['base_delta']), {
                        method: [(r['file_id'], r['similarity']) for r in rows]
                        for method, rows in outputs.items()}
                    np.testing.assert_allclose([r['similarity'] for r in outputs['sqlite']],
                                               [r['similarity'] for r in outputs['base_delta']], atol=1e-7)
                median = {key: float(np.median(value)) for key, value in timings.items()}
                return dict(vectors=inserted, dimensions=dim, changed_vectors=changed,
                            vector_source='local_read_only' if source else 'synthetic_seed_20261102',
                            queries=repeats+1, top_k=20, exact_matches=True,
                            seconds=timings, median_seconds=median,
                            median_speedup=median['sqlite']/median['base_delta'],
                            cache_build_seconds=build_seconds,
                            cache_bytes=(path.parent/quick_cache.FILENAME).stat().st_size,
                            numpy=np.__version__, python=sys.version.split()[0],
                            limitations='Warm local microbenchmark; excludes extraction, model encoding, live file stat, UI and human relevance.')
            finally:
                db.close()
    finally:
        if source_db is not None:
            source_db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.count < 2 or args.repeats < 1:
        parser.error('count must be >= 2 and repeats >= 1')
    report = run(args.count, args.repeats, args.source)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n', encoding='utf-8')
    print(text)
