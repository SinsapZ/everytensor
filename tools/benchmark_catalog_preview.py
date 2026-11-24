"""Compare two source trees against the same disposable catalog and JPEG.

Run with the desktop Python, --baseline <original source> --output <report.json>.
Each round runs in a fresh process; order alternates and file caches stay warm.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]


def create_fixture(folder):
    from PIL import Image
    import numpy as np
    library = folder / 'library'
    for i in range(768):
        parent = library / f'section-{i % 12:02d}'
        parent.mkdir(parents=True, exist_ok=True)
        (parent / f'notes-{i:04d}.txt').write_text(f'research record {i}', 'utf-8')
    pixels = np.random.default_rng(617).integers(0, 256, (3000, 4000, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(pixels).save(folder / 'camera.jpg', quality=85, exif=exif)


def run_sample(source, folder):
    sys.path.insert(0, str(source))
    from everytensor import indexer, store
    from everytensor.preview_service import PreviewService, ImageStore, ThumbnailProvider
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSize
    import base64
    import threading

    app = QApplication([])
    library = folder / 'library'
    timings, checks = {}, {}
    def measure(name, action):
        start = time.perf_counter_ns()
        value = action()
        timings[name] = (time.perf_counter_ns() - start) / 1e6
        return value

    with tempfile.TemporaryDirectory(dir=folder) as temp:
        database = Path(temp) / '.everytensor' / 'index.sqlite3'
        db = store.connect(database)
        store.set_setting(db, 'scope', 'folders')
        store.set_setting(db, 'roots', [str(library)])
        store.set_setting(db, 'exclusions', [str(library / f'section-{i:02d}') for i in (7, 8, 9, 10, 11)])
        preview = PreviewService(database, ImageStore())
        try:
            measure('scan_ms', lambda: indexer.scan(db, str(library)))
            checks['scanned_live_files'] = db.execute('SELECT count(*) FROM files').fetchone()[0]
            assert checks['scanned_live_files'] == 448
            # Retain only the filename candidates, as a catalog awaiting reconciliation would.
            section = library / 'section-00'
            with db:
                for i in range(1024):
                    name = f'notes-0000-missing-{i:04d}.txt'
                    db.execute('INSERT INTO files(path,root,name,kind) VALUES (?,?,?,?)',
                               (str(section / name), str(library), name, 'document'))
            result = measure('exact_filename_ms', lambda: list(store.name_batches(db, 'notes-0012.txt')))
            assert result[-1][1] and [row['name'] for row in result[-1][0]] == ['notes-0012.txt']
            selects = []
            db.set_trace_callback(lambda sql: selects.append(sql) if 'AS candidates' in sql else None)
            rows = measure('stale_substring_ms', lambda: store.names(db, 'notes', limit=25, offset=4))
            db.set_trace_callback(None)
            checks['candidate_queries'] = len(selects)
            checks['ranked_filenames'] = [row['name'] for row in rows]
            assert checks['ranked_filenames'] == sorted(p.name for p in library.glob('*/*.txt')
                if int(p.parent.name[-2:]) < 7)[4:29]

            photo = folder / 'camera.jpg'
            stat = photo.stat()
            row = dict(path=str(photo), kind='image', size=stat.st_size, mtime=stat.st_mtime_ns,
                       file_id=-1, location={'page': 1})
            key = base64.urlsafe_b64encode(json.dumps([str(photo), stat.st_size, stat.st_mtime_ns])
                .encode()).decode().rstrip('=')
            provider = ThumbnailProvider(preview.pdf_renderer)
            thumb = measure('jpeg_thumbnail_ms', lambda: provider.requestImage(key, QSize(), QSize()))
            main = measure('jpeg_preview_ms', lambda: preview._render(row, (720, 650), threading.Event()))['image']
            checks['image_sizes'] = [[thumb.width(), thumb.height()], [main.width(), main.height()]]
            assert checks['image_sizes'][0] == [99, 132] and checks['image_sizes'][1] in ([487, 650], [488, 650])
        finally:
            preview.close()
            db.close()
    return dict(timings=timings, checks=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--rounds', type=int, default=9)
    parser.add_argument('--sample', type=Path, nargs=2, metavar=('SOURCE', 'FIXTURE'))
    args = parser.parse_args()
    if args.sample:
        print(json.dumps(run_sample(*args.sample)))
        return
    if not args.baseline or not args.output or args.rounds < 1:
        parser.error('specify --baseline and --output, with at least one round')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', EVERYTENSOR_RESOURCE_ROOT=str(PROJECT))
    runs = {'before': [], 'after': []}
    sources = {'before': args.baseline.resolve(), 'after': PROJECT}
    with tempfile.TemporaryDirectory(prefix='everytensor-experience-') as workspace:
        fixture = Path(workspace)
        create_fixture(fixture)
        digest = hashlib.sha256((fixture / 'camera.jpg').read_bytes()).hexdigest()
        for i in range(args.rounds + 1):
            for name in (('before', 'after') if i % 2 == 0 else ('after', 'before')):
                process = subprocess.run([sys.executable, '-X', 'utf8', __file__, '--sample', sources[name], fixture],
                                         cwd=sources[name], env=env, capture_output=True, text=True)
                if process.returncode:
                    raise RuntimeError(process.stderr)
                if i:
                    runs[name].append(json.loads(process.stdout))
    summary = {name: {metric: statistics.median(run['timings'][metric] for run in samples)
                      for metric in samples[0]['timings']} for name, samples in runs.items()}
    checks = [sample['checks'] for samples in runs.values() for sample in samples]
    for key in ('scanned_live_files', 'ranked_filenames', 'image_sizes'):
        assert all(record[key] == checks[0][key] for record in checks), (key, [record[key] for record in checks])
    output = dict(medians_ms=summary, samples=runs, rounds=args.rounds, jpeg_sha256=digest,
                  python=platform.python_version(), os=platform.platform(), warmup_pairs=1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), 'utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
