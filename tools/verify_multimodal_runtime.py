"""Verify an installed optional runtime on synthetic files, optionally against a bundle.

Use only a disposable --data directory previously populated by model setup.
This opt-in check never downloads anything and does not touch a real user index.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import sys
import time
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from everytensor import store, indexer, media, quick_index
from everytensor.model_install import installed_configuration
from everytensor.backend import Backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    assert data.is_relative_to(ROOT / 'build'), 'Use a disposable directory under build/'
    config = installed_configuration(data)
    assert config, 'Install optional components into --data before this check'
    samples = data.parent / 'multimodal-samples'
    samples.mkdir(exist_ok=True)
    (samples / '文字测试.txt').write_text('A red square. 红色方块。', 'utf-8')
    from PIL import Image
    Image.new('RGB', (64, 64), 'red').save(samples / '图片测试.png')
    with wave.open(str(samples / '录音测试.wav'), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0' * 32000)
    db_path = data / 'smoke.sqlite3'
    resources = args.bundle.resolve() / '_internal' if args.bundle else ROOT
    report = dict(ok=False, bundle=bool(args.bundle), checks=[])
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, EVERYTENSOR_COMPONENTS=str(config['root']),
                                       EVERYTENSOR_RESOURCE_ROOT=str(resources),
                                       PATH=str(Path(os.environ['SystemRoot']) / 'System32')))
        if args.bundle:
            stack.enter_context(patch('everytensor.paths.frozen', return_value=True))
        media.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:r=2',
                   '-t', '1', '-pix_fmt', 'yuv420p', str(samples / '视频测试.mp4')])
        db = store.connect(db_path)
        store.set_setting(db, 'scope', 'folders')
        store.set_setting(db, 'roots', [str(samples)])
        store.set_setting(db, 'model', str(config['model']))
        quick_index.resolve_model(db, resources / 'models/quick-semantic')
        indexer.scan(db, str(samples))
        indexer.index_pending(db)
        db.close()
        events = queue.Queue()
        backend = Backend(db_path, events)
        try:
            assert str(config['python']) == backend.process.args[0]
            deadline = time.monotonic() + 240
            ready = False
            while time.monotonic() < deadline:
                try:
                    event = events.get(timeout=.5)
                except queue.Empty:
                    continue
                assert event['type'] != 'fatal', event
                if event['type'] == 'model':
                    print(event, flush=True)
                    assert event.get('state') not in ('error', 'missing'), event
                    if event.get('state') == 'ready':
                        ready = True
                        break
            assert ready, 'Full multimodal index did not become ready'
            report['checks'].append('isolated_cpu_worker_indexed_four_modalities')
            for request_id, kind in enumerate(('document', 'image', 'audio', 'video'), 1):
                backend.put(dict(action='search', id=request_id, query='a red square', kind=kind,
                                 limit=10, channels=True))
                found = False
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    try:
                        event = events.get(timeout=.5)
                    except queue.Empty:
                        continue
                    assert event['type'] != 'fatal', event
                    if event['type'] == 'results' and event.get('id') == request_id and event.get('full_checked'):
                        assert not event.get('error'), event
                        assert event.get('semantic_stage') == 'full', event
                        assert event.get('semantic_rows'), event
                        found = True
                        break
                assert found, f'No full semantic result for {kind}'
                report['checks'].append(kind + '_semantic_query')
            backend.close(wait=True)
            assert backend.process.returncode == 0
            report['checks'].append('clean_worker_shutdown')
            report['ok'] = True
        finally:
            backend.close(wait=True)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
